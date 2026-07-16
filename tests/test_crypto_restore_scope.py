from __future__ import annotations

import hashlib
import shutil
import subprocess
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
RESTORE_LIST = REPO_ROOT / "docker" / "postgres" / "init" / "crypto_only_restore.list"
RESTORE_SCRIPT = REPO_ROOT / "docker" / "postgres" / "init" / "restore_dump.sh"
COMPOSE_FILE = REPO_ROOT / "docker-compose.yml"
DUMP_PATH = REPO_ROOT / "data" / "dumps" / "callscore.dump"


def _manifest_value(key: str) -> str:
    prefix = f"; {key}="
    for line in RESTORE_LIST.read_text().splitlines():
        if line.startswith(prefix):
            return line.removeprefix(prefix)
    raise AssertionError(f"Missing {key} in crypto restore manifest")


def _restore_entries() -> list[str]:
    return [
        line for line in RESTORE_LIST.read_text().splitlines() if line and not line.startswith(";")
    ]


def test_crypto_restore_manifest_allows_only_raw_market_tables() -> None:
    entries = _restore_entries()

    assert len(entries) == 17
    assert any("TABLE DATA public candles " in line for line in entries)
    assert any("TABLE DATA public ticks " in line for line in entries)
    assert any("candles_symbol_interval_open_time_key" in line for line in entries)
    assert any("idx_ticks_lookup" in line for line in entries)
    assert not any("candles_1h" in line or "candles_4h" in line for line in entries)
    assert not any("idx_candles_regime" in line for line in entries)
    assert not any("idx_candles_lookup" in line for line in entries)
    assert not any("idx_candles_symbol_time" in line for line in entries)
    assert all(
        " public candles" in line
        or " public candles_id_seq" in line
        or " public idx_candles_" in line
        or " public ticks" in line
        or " public ticks_id_seq" in line
        or " public idx_ticks_" in line
        for line in entries
    )


def test_restore_manifest_matches_the_local_dump_catalogue() -> None:
    pg_restore = shutil.which("pg_restore")
    if not DUMP_PATH.exists() or pg_restore is None:
        pytest.skip("Local dump and pg_restore are optional outside data-validation runs")

    listing = subprocess.run(
        [pg_restore, "--list", str(DUMP_PATH)],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.splitlines()

    assert set(_restore_entries()).issubset(set(listing))
    assert DUMP_PATH.stat().st_size == int(_manifest_value("dump_size_bytes"))
    with DUMP_PATH.open("rb") as dump_file:
        assert hashlib.file_digest(dump_file, "sha256").hexdigest() == _manifest_value(
            "dump_sha256"
        )


def test_restore_script_is_fail_closed_and_non_destructive() -> None:
    script = RESTORE_SCRIPT.read_text()
    sql_block = script.split("<<'SQL'", maxsplit=1)[1].split("\nSQL", maxsplit=1)[0]

    assert 'DUMP_PATH="/docker-entrypoint-initdb.d/callscore.dump"' in script
    assert "sha256sum" in script
    assert "--use-list" in script
    assert "--exit-on-error" in script
    assert "--no-owner" in script
    assert "--no-acl" in script
    assert "--clean" not in script
    assert "unexpected relation" in script.lower()
    assert "DROP COLUMN regime" in script
    assert "ALTER TABLE public.candles SET SCHEMA market_data" in script
    assert "ALTER SEQUENCE public.candles_id_seq SET SCHEMA" not in script
    assert "ALTER SEQUENCE public.ticks_id_seq SET SCHEMA" not in script
    assert not any(line.startswith("#") for line in sql_block.splitlines())


def test_compose_mounts_crypto_restore_inputs_at_explicit_paths() -> None:
    compose = COMPOSE_FILE.read_text()

    assert "./data/dumps/callscore.dump:/docker-entrypoint-initdb.d/callscore.dump:ro" in compose
    assert (
        "./docker/postgres/init/restore_dump.sh:"
        "/docker-entrypoint-initdb.d/10_restore_dump.sh:ro" in compose
    )
    assert (
        "./docker/postgres/init/crypto_only_restore.list:"
        "/usr/local/share/market-structure-lab/crypto_only_restore.list:ro" in compose
    )
    assert "pg_isready -U $${POSTGRES_USER} -d $${POSTGRES_DB}" in compose
    assert "/docker-entrypoint-initdb.d/dumps" not in compose
    assert "/docker-entrypoint-initdb.d/init" not in compose
