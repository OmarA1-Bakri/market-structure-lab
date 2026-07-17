export const TRIAL_MODES = [
  "discovery",
  "hypothesis",
  "validation",
  "strategy",
] as const;

export const TERMINAL_STATUSES = [
  "failed",
  "inconclusive",
  "abandoned",
  "rejected",
  "completed",
] as const;

type TrialMode = (typeof TRIAL_MODES)[number];
type TerminalStatus = (typeof TERMINAL_STATUSES)[number];

export interface TrialCounts {
  by_mode: Record<TrialMode, number>;
  by_status: Record<TerminalStatus, number>;
  by_mode_and_status: Record<TrialMode, Record<TerminalStatus, number>>;
  total: number;
}

function isObject(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

function isNonNegativeInteger(value: unknown): value is number {
  return typeof value === "number" && Number.isInteger(value) && value >= 0;
}

function hasExactKeys(value: Record<string, unknown>, expected: readonly string[]): boolean {
  const keys = Object.keys(value);
  return keys.length === expected.length && expected.every((key) => key in value);
}

function isExactCountMap(
  value: unknown,
  expected: readonly string[],
): value is Record<string, number> {
  return (
    isObject(value) &&
    hasExactKeys(value, expected) &&
    Object.values(value).every(isNonNegativeInteger)
  );
}

export function isTrialCounts(value: unknown): value is TrialCounts {
  if (
    !isObject(value) ||
    !hasExactKeys(value, ["by_mode", "by_status", "by_mode_and_status", "total"]) ||
    !isNonNegativeInteger(value.total)
  )
    return false;
  const byMode = value.by_mode;
  const byStatus = value.by_status;
  const matrix = value.by_mode_and_status;
  if (
    !isExactCountMap(byMode, TRIAL_MODES) ||
    !isExactCountMap(byStatus, TERMINAL_STATUSES) ||
    !isObject(matrix) ||
    !hasExactKeys(matrix, TRIAL_MODES)
  )
    return false;
  if (
    TRIAL_MODES.some((mode) => {
      const row = matrix[mode];
      return (
        !isExactCountMap(row, TERMINAL_STATUSES) ||
        TERMINAL_STATUSES.reduce((sum, status) => sum + row[status], 0) !== byMode[mode]
      );
    })
  )
    return false;
  return (
    TRIAL_MODES.reduce((sum, mode) => sum + byMode[mode], 0) === value.total &&
    TERMINAL_STATUSES.reduce((sum, status) => sum + byStatus[status], 0) === value.total &&
    TERMINAL_STATUSES.every(
      (status) =>
        TRIAL_MODES.reduce(
          (sum, mode) =>
            sum + (matrix[mode] as Record<TerminalStatus, number>)[status],
          0,
        ) === byStatus[status],
    )
  );
}
