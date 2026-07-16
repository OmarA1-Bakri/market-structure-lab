[CmdletBinding()]
param(
    [string]$ProjectRoot = (Split-Path -Parent $PSScriptRoot),
    [string]$CompatibilityPath = "data/exports/manifests/recovery-callscore-20260714-validated.json",
    [string]$CompatibilitySha256 = "482f3a09a4e24a62b0ad92f5bb90028eead67fe961eb1d3320dd38d13fd105d2",
    [string]$DumpPath = "data/dumps/callscore.dump",
    [string]$CacheDir = "data/cache/binance",
    [string]$OutputDir = "data/exports/freshness",
    [string]$StateDir = "data/exports/freshness/automation",
    [string]$PostgresDataDir = "data/postgres",
    [string]$DockerExecutable = "docker",
    [string]$SyncExecutable = "",
    [string]$ContainerName = "market-structure-postgres",
    [ValidateRange(1, 3600)]
    [int]$ContainerHealthTimeoutSeconds = 180,
    [ValidateRange(0, 60)]
    [int]$PollIntervalSeconds = 2
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Resolve-WorkspacePath {
    param([Parameter(Mandatory)][string]$Path)

    if ([System.IO.Path]::IsPathRooted($Path)) {
        return [System.IO.Path]::GetFullPath($Path)
    }
    return [System.IO.Path]::GetFullPath((Join-Path $script:ResolvedProjectRoot $Path))
}

function Write-AtomicJson {
    param(
        [Parameter(Mandatory)][string]$Path,
        [Parameter(Mandatory)][object]$Value
    )

    $temporary = "$Path.tmp"
    $Value | ConvertTo-Json -Depth 20 -Compress | Set-Content -LiteralPath $temporary -Encoding utf8NoBOM
    Move-Item -LiteralPath $temporary -Destination $Path -Force
}

function Read-JsonObject {
    param(
        [Parameter(Mandatory)][string]$Text,
        [Parameter(Mandatory)][string]$Stage
    )

    $trimmed = $Text.Trim()
    if (-not $trimmed) {
        throw "$Stage emitted no JSON output"
    }
    try {
        return $trimmed | ConvertFrom-Json -Depth 30
    }
    catch {
        throw "$Stage emitted invalid JSON"
    }
}

function Invoke-CommandCapture {
    param(
        [Parameter(Mandatory)][string]$Executable,
        [Parameter(Mandatory)][string[]]$Arguments,
        [Parameter(Mandatory)][string]$Stage
    )

    $token = [guid]::NewGuid().ToString("N")
    $stdoutPath = Join-Path $script:ResolvedStateDir "$token.stdout"
    $stderrPath = Join-Path $script:ResolvedStateDir "$token.stderr"
    try {
        try {
            & $Executable @Arguments 1> $stdoutPath 2> $stderrPath
            $exitCode = $LASTEXITCODE
        }
        catch {
            throw "$Stage could not start: $($_.Exception.Message)"
        }
        $stdout = ""
        if (Test-Path -LiteralPath $stdoutPath) {
            $stdout = [System.IO.File]::ReadAllText($stdoutPath)
        }
        $stderr = ""
        if (Test-Path -LiteralPath $stderrPath) {
            $stderr = [System.IO.File]::ReadAllText($stderrPath)
        }
        return [pscustomobject]@{
            ExitCode = [int]$exitCode
            Stdout = $stdout.Trim()
            Stderr = $stderr.Trim()
        }
    }
    finally {
        Remove-Item -LiteralPath $stdoutPath, $stderrPath -Force -ErrorAction SilentlyContinue
    }
}

function Assert-AllowedExit {
    param(
        [Parameter(Mandatory)][object]$Result,
        [Parameter(Mandatory)][string]$Stage,
        [Parameter(Mandatory)][int[]]$Allowed
    )

    if ($Result.ExitCode -notin $Allowed) {
        $detail = if ($Result.Stderr) { $Result.Stderr } else { $Result.Stdout }
        if (-not $detail) {
            $detail = "no diagnostic output"
        }
        throw "$Stage failed with exit $($Result.ExitCode): $detail"
    }
}

function Get-ContainerState {
    $result = Invoke-CommandCapture -Executable $DockerExecutable -Stage "docker inspect" -Arguments @(
        "inspect",
        $ContainerName,
        "--format",
        "{{.State.Status}}|{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}"
    )
    if ($result.ExitCode -ne 0) {
        if ($result.Stderr -match "(?i)no such (object|container)") {
            return $null
        }
        throw "docker inspect failed: $($result.Stderr)"
    }
    $parts = $result.Stdout.Trim().Split("|", 2)
    if ($parts.Count -ne 2) {
        throw "docker inspect returned an invalid container state"
    }
    return [pscustomobject]@{
        Status = $parts[0]
        Health = $parts[1]
    }
}

function Ensure-DurablePostgres {
    $versionPath = Join-Path $script:ResolvedPostgresDataDir "PG_VERSION"
    if (-not (Test-Path -LiteralPath $versionPath -PathType Leaf)) {
        throw "refusing to start PostgreSQL because durable data/postgres/PG_VERSION is absent"
    }

    $dockerInfo = Invoke-CommandCapture -Executable $DockerExecutable -Stage "docker info" -Arguments @(
        "info",
        "--format",
        "{{.ServerVersion}}"
    )
    Assert-AllowedExit -Result $dockerInfo -Stage "docker info" -Allowed @(0)

    $state = Get-ContainerState
    if ($null -eq $state) {
        $start = Invoke-CommandCapture -Executable $DockerExecutable -Stage "docker compose up" -Arguments @(
            "compose",
            "up",
            "-d",
            "--no-deps",
            "postgres"
        )
        Assert-AllowedExit -Result $start -Stage "docker compose up" -Allowed @(0)
    }
    elseif ($state.Status -in @("created", "exited")) {
        $start = Invoke-CommandCapture -Executable $DockerExecutable -Stage "docker compose start" -Arguments @(
            "compose",
            "start",
            "postgres"
        )
        Assert-AllowedExit -Result $start -Stage "docker compose start" -Allowed @(0)
    }
    elseif ($state.Status -ne "running") {
        throw "PostgreSQL container is in unsupported state '$($state.Status)'"
    }

    $deadline = [DateTimeOffset]::UtcNow.AddSeconds($ContainerHealthTimeoutSeconds)
    while ($true) {
        $state = Get-ContainerState
        if ($null -eq $state) {
            throw "PostgreSQL container disappeared while waiting for health"
        }
        if ($state.Status -eq "running" -and $state.Health -eq "healthy") {
            return
        }
        if ($state.Status -ne "running" -or $state.Health -eq "unhealthy") {
            throw "PostgreSQL container is '$($state.Status)' with health '$($state.Health)'"
        }
        if ([DateTimeOffset]::UtcNow -ge $deadline) {
            throw "PostgreSQL container did not become healthy within $ContainerHealthTimeoutSeconds seconds"
        }
        if ($PollIntervalSeconds -gt 0) {
            Start-Sleep -Seconds $PollIntervalSeconds
        }
    }
}

function Get-ExpectedConflictSymbols {
    if (-not (Test-Path -LiteralPath $script:ResolvedCompatibilityPath -PathType Leaf)) {
        throw "reviewed compatibility artifact is missing"
    }
    $compatibility = Get-Content -Raw -LiteralPath $script:ResolvedCompatibilityPath |
        ConvertFrom-Json -Depth 30
    if ($null -eq $compatibility.provenance_validation) {
        throw "reviewed compatibility artifact has no provenance_validation"
    }
    return @(
        $compatibility.provenance_validation.PSObject.Properties |
            Where-Object { $_.Value -eq "source_conflict" } |
            ForEach-Object { $_.Name } |
            Sort-Object
    )
}

function Get-PendingManifest {
    if (-not (Test-Path -LiteralPath $script:PendingPath -PathType Leaf)) {
        return $null
    }
    $pending = Get-Content -Raw -LiteralPath $script:PendingPath | ConvertFrom-Json -Depth 10
    if ($pending.compatibility_sha256 -ne $CompatibilitySha256.ToLowerInvariant()) {
        throw "pending freshness state identifies a different compatibility artifact"
    }
    if (-not (Test-Path -LiteralPath $pending.manifest -PathType Leaf)) {
        throw "pending freshness manifest is missing"
    }
    $envelope = Get-Content -Raw -LiteralPath $pending.manifest | ConvertFrom-Json -Depth 30
    if ($envelope.sha256 -ne $pending.manifest_sha256) {
        throw "pending freshness manifest checksum does not match pending state"
    }
    return $pending
}

function New-PendingManifest {
    $plan = Invoke-CommandCapture -Executable $script:ResolvedSyncExecutable -Stage "freshness plan" -Arguments @(
        "plan",
        "--compatibility",
        $script:ResolvedCompatibilityPath,
        "--compatibility-sha256",
        $CompatibilitySha256,
        "--output-dir",
        $script:ResolvedOutputDir
    )
    Assert-AllowedExit -Result $plan -Stage "freshness plan" -Allowed @(0, 2)
    $payload = Read-JsonObject -Text $plan.Stdout -Stage "freshness plan"
    if (-not $payload.manifest -or -not $payload.manifest_sha256) {
        throw "freshness plan did not identify its manifest"
    }
    $manifestPath = Resolve-WorkspacePath ([string]$payload.manifest)
    $pending = [ordered]@{
        compatibility_sha256 = $CompatibilitySha256.ToLowerInvariant()
        created_at = [DateTimeOffset]::UtcNow.ToString("O")
        manifest = $manifestPath
        manifest_sha256 = [string]$payload.manifest_sha256
        plan_exit_code = $plan.ExitCode
    }
    Write-AtomicJson -Path $script:PendingPath -Value $pending
    return [pscustomobject]$pending
}

function Invoke-FreshnessRun {
    param(
        [Parameter(Mandatory)][object]$Pending,
        [switch]$Apply
    )

    $arguments = @(
        "run",
        "--manifest",
        [string]$Pending.manifest,
        "--compatibility",
        $script:ResolvedCompatibilityPath,
        "--compatibility-sha256",
        $CompatibilitySha256
    )
    if ($Apply) {
        $arguments += @(
            "--dump-path",
            $script:ResolvedDumpPath,
            "--cache-dir",
            $script:ResolvedCacheDir,
            "--output-dir",
            $script:ResolvedOutputDir,
            "--apply"
        )
    }
    $stage = if ($Apply) { "freshness apply" } else { "freshness dry-run" }
    $result = Invoke-CommandCapture -Executable $script:ResolvedSyncExecutable -Stage $stage -Arguments $arguments
    Assert-AllowedExit -Result $result -Stage $stage -Allowed @(0, 2)
    return [pscustomobject]@{
        Result = $result
        Payload = Read-JsonObject -Text $result.Stdout -Stage $stage
    }
}

function Get-TerminalClassification {
    param(
        [Parameter(Mandatory)][object]$ApplyPayload,
        [Parameter(Mandatory)][object]$HealthPayload,
        [Parameter(Mandatory)][int]$HealthExitCode,
        [Parameter(Mandatory)]
        [AllowEmptyCollection()]
        [string[]]$ExpectedConflicts
    )

    if (-not $ApplyPayload.coverage_conserved) {
        throw "freshness apply did not conserve planned coverage"
    }
    if (-not $ApplyPayload.report_sha256 -or
        $ApplyPayload.report_sha256 -ne $HealthPayload.report_sha256) {
        throw "freshness apply and health report checksums differ"
    }
    $expected = [System.Collections.Generic.HashSet[string]]::new(
        [string[]]$ExpectedConflicts,
        [System.StringComparer]::Ordinal
    )
    $observedConflicts = [System.Collections.Generic.HashSet[string]]::new(
        [System.StringComparer]::Ordinal
    )
    $terminalLimits = [System.Collections.Generic.List[string]]::new()
    foreach ($symbol in @($ApplyPayload.symbols)) {
        $name = [string]$symbol.symbol
        if ($expected.Contains($name)) {
            if ($symbol.compatibility_state -ne "source_conflict" -or
                $symbol.status -ne "source_conflict" -or
                [int64]$symbol.inserted_rows -ne 0) {
                throw "expected provenance conflict $name has an invalid terminal report"
            }
            [void]$observedConflicts.Add($name)
            continue
        }
        if ($symbol.status -in @("up_to_date", "recovered")) {
            if (-not [bool]$symbol.current_through_cutoff -or
                [int64]$symbol.after_missing_minutes -ne 0) {
                throw "compatible symbol $name reports current status with missing minutes"
            }
            continue
        }
        if ($symbol.status -in @("provider_absent", "non_trading", "partially_recovered")) {
            if ([bool]$symbol.current_through_cutoff -or
                [int64]$symbol.after_missing_minutes -le 0) {
                throw "compatible symbol $name has a terminal limit without missing minutes"
            }
            $terminalLimits.Add("$name`:$($symbol.status)")
            continue
        }
        throw "compatible symbol $name has unexpected terminal status '$($symbol.status)'"
    }
    if (-not $observedConflicts.SetEquals($expected)) {
        throw "terminal report does not contain exactly the reviewed source conflicts"
    }
    if ($ExpectedConflicts.Count -eq 0 -and $terminalLimits.Count -eq 0) {
        if ($HealthExitCode -ne 0 -or -not [bool]$HealthPayload.current) {
            throw "health is non-current despite a fully healthy reviewed universe"
        }
        return [pscustomobject]@{
            ExitCode = 0
            Classification = "current"
            TerminalLimits = @()
        }
    }
    if ($HealthExitCode -ne 2 -or [bool]$HealthPayload.current) {
        throw "health did not alert for terminal candle coverage limits"
    }
    $classification = if ($terminalLimits.Count) {
        "terminal_data_limits"
    }
    else {
        "expected_provenance_conflicts"
    }
    return [pscustomobject]@{
        ExitCode = 2
        Classification = $classification
        TerminalLimits = @($terminalLimits)
    }
}

$script:ResolvedProjectRoot = [System.IO.Path]::GetFullPath($ProjectRoot)
if (-not (Test-Path -LiteralPath $script:ResolvedProjectRoot -PathType Container)) {
    Write-Error "project root does not exist: $script:ResolvedProjectRoot"
    exit 1
}
Set-Location -LiteralPath $script:ResolvedProjectRoot
$script:ResolvedCompatibilityPath = Resolve-WorkspacePath $CompatibilityPath
$script:ResolvedDumpPath = Resolve-WorkspacePath $DumpPath
$script:ResolvedCacheDir = Resolve-WorkspacePath $CacheDir
$script:ResolvedOutputDir = Resolve-WorkspacePath $OutputDir
$script:ResolvedStateDir = Resolve-WorkspacePath $StateDir
$script:ResolvedPostgresDataDir = Resolve-WorkspacePath $PostgresDataDir
$script:ResolvedSyncExecutable = if ($SyncExecutable) {
    Resolve-WorkspacePath $SyncExecutable
}
else {
    Resolve-WorkspacePath ".venv/Scripts/msl-sync-candles.exe"
}
$script:PendingPath = Join-Path $script:ResolvedStateDir "pending.json"
$lockPath = Join-Path $script:ResolvedStateDir "daily-refresh.lock"
$lock = $null
$status = [ordered]@{
    classification = "operational_failure"
    completed_at = $null
    error = $null
    expected_conflicts = @()
    manifest = $null
    manifest_sha256 = $null
    report_sha256 = $null
    started_at = [DateTimeOffset]::UtcNow.ToString("O")
    terminal_limits = @()
}

try {
    New-Item -ItemType Directory -Path $script:ResolvedStateDir -Force | Out-Null
    New-Item -ItemType Directory -Path $script:ResolvedOutputDir -Force | Out-Null
    $lock = [System.IO.File]::Open(
        $lockPath,
        [System.IO.FileMode]::OpenOrCreate,
        [System.IO.FileAccess]::ReadWrite,
        [System.IO.FileShare]::None
    )
    if (-not (Test-Path -LiteralPath $script:ResolvedSyncExecutable -PathType Leaf)) {
        throw "installed freshness executable is missing: $script:ResolvedSyncExecutable"
    }
    if ($CompatibilitySha256 -notmatch "^[0-9A-Fa-f]{64}$") {
        throw "compatibility SHA-256 is invalid"
    }
    $expectedConflicts = @(Get-ExpectedConflictSymbols)
    $status.expected_conflicts = $expectedConflicts
    Ensure-DurablePostgres
    $pending = Get-PendingManifest
    if ($null -eq $pending) {
        $pending = New-PendingManifest
    }
    $status.manifest = [string]$pending.manifest
    $status.manifest_sha256 = [string]$pending.manifest_sha256

    $dryRun = Invoke-FreshnessRun -Pending $pending
    if ($dryRun.Payload.manifest_sha256 -ne $pending.manifest_sha256) {
        throw "dry-run did not use the pending freshness manifest"
    }
    $apply = Invoke-FreshnessRun -Pending $pending -Apply
    if ($apply.Payload.manifest_sha256 -ne $pending.manifest_sha256) {
        throw "apply did not use the pending freshness manifest"
    }
    $health = Invoke-CommandCapture -Executable $script:ResolvedSyncExecutable -Stage "freshness health" -Arguments @(
        "health",
        "--output-dir",
        $script:ResolvedOutputDir
    )
    Assert-AllowedExit -Result $health -Stage "freshness health" -Allowed @(0, 2)
    $healthPayload = Read-JsonObject -Text $health.Stdout -Stage "freshness health"
    $classification = Get-TerminalClassification `
        -ApplyPayload $apply.Payload `
        -HealthPayload $healthPayload `
        -HealthExitCode $health.ExitCode `
        -ExpectedConflicts $expectedConflicts

    $status.classification = $classification.Classification
    $status.report_sha256 = [string]$apply.Payload.report_sha256
    $status.terminal_limits = @($classification.TerminalLimits)
    $status.completed_at = [DateTimeOffset]::UtcNow.ToString("O")
    Remove-Item -LiteralPath $script:PendingPath -Force
    Write-AtomicJson -Path (Join-Path $script:ResolvedStateDir "latest.json") -Value $status
    $status | ConvertTo-Json -Depth 10 -Compress
    exit $classification.ExitCode
}
catch {
    $status.error = "$($_.Exception.Message) ($($_.ScriptStackTrace))"
    $status.completed_at = [DateTimeOffset]::UtcNow.ToString("O")
    try {
        if ($null -ne $lock -and
            (Test-Path -LiteralPath $script:ResolvedStateDir -PathType Container)) {
            Write-AtomicJson -Path (Join-Path $script:ResolvedStateDir "latest.json") -Value $status
        }
    }
    catch {
        # Preserve the original operational failure.
    }
    Write-Error $status.error
    exit 1
}
finally {
    if ($null -ne $lock) {
        $lock.Dispose()
    }
}
