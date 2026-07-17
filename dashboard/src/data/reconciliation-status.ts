import { useEffect, useState } from "react"

export interface ReconciliationStatus {
  schema_version: 1
  generated_at: string
  connection_mode: "deployment_snapshot"
  run_id: string
  status: "promoted"
  promoted_at: string
  cutoff: string
  candidate_venue: string
  market_type: string
  algorithm_version: string
  mapping_version: string
  code_commit: string
  manifest_sha256: string
  canonical_logical_sha256: string
  replacement_logical_sha256: string
  dump_sha256: string
  work_units: number
  verified_symbols: number
  unavailable_symbols: number
  row_count: number
  replacement_rows: number
  classification_counts: {
    exact_match: number
    binance_correction: number
    binance_fill: number
    source_unavailable: number
  }
  differing_field_counts: Record<string, number>
}

type StatusState =
  | { state: "loading" }
  | { state: "ready"; data: ReconciliationStatus }
  | { state: "error"; message: string }

function isSha256(value: unknown): value is string {
  return typeof value === "string" && /^[0-9a-f]{64}$/.test(value)
}

function isReconciliationStatus(value: unknown): value is ReconciliationStatus {
  if (!value || typeof value !== "object") return false
  const status = value as Partial<ReconciliationStatus>
  return (
    status.schema_version === 1 &&
    status.connection_mode === "deployment_snapshot" &&
    status.status === "promoted" &&
    typeof status.run_id === "string" &&
    typeof status.cutoff === "string" &&
    typeof status.row_count === "number" &&
    typeof status.verified_symbols === "number" &&
    typeof status.unavailable_symbols === "number" &&
    isSha256(status.manifest_sha256) &&
    isSha256(status.canonical_logical_sha256) &&
    isSha256(status.replacement_logical_sha256)
  )
}

export function useReconciliationStatus(): StatusState {
  const [status, setStatus] = useState<StatusState>({ state: "loading" })

  useEffect(() => {
    const controller = new AbortController()

    async function load() {
      try {
        const response = await fetch("/data/reconciliation-status.json", {
          cache: "no-store",
          signal: controller.signal,
        })
        if (!response.ok) throw new Error(`HTTP ${response.status}`)
        const payload: unknown = await response.json()
        if (!isReconciliationStatus(payload)) {
          throw new Error("invalid reconciliation evidence contract")
        }
        setStatus({ state: "ready", data: payload })
      } catch (error) {
        if (controller.signal.aborted) return
        setStatus({
          state: "error",
          message: error instanceof Error ? error.message : "unknown data error",
        })
      }
    }

    void load()
    return () => controller.abort()
  }, [])

  return status
}
