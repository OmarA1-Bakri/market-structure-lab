import assert from "node:assert/strict";
import { readFile, unlink, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { pathToFileURL } from "node:url";
import ts from "typescript";

const source = await readFile(
  new URL("../src/data/trial-counts.ts", import.meta.url),
  "utf8",
);
const compiled = ts.transpileModule(source, {
  compilerOptions: {
    module: ts.ModuleKind.ESNext,
    target: ts.ScriptTarget.ES2022,
  },
}).outputText;
const temporary = join(
  tmpdir(),
  `market-structure-lab-trial-counts-${process.pid}-${Date.now()}.mjs`,
);
await writeFile(temporary, compiled, "utf8");

try {
  const { isTrialCounts } = await import(`${pathToFileURL(temporary).href}?v=${Date.now()}`);
  const emptyRow = {
    failed: 0,
    inconclusive: 0,
    abandoned: 0,
    rejected: 0,
    completed: 0,
  };
  const canonical = {
    by_mode: { discovery: 0, hypothesis: 0, validation: 0, strategy: 0 },
    by_status: structuredClone(emptyRow),
    by_mode_and_status: {
      discovery: structuredClone(emptyRow),
      hypothesis: structuredClone(emptyRow),
      validation: structuredClone(emptyRow),
      strategy: structuredClone(emptyRow),
    },
    total: 0,
  };
  assert.equal(isTrialCounts(canonical), true);

  const mutations = [
    (value) => Object.assign(value, { extra: 0 }),
    (value) => Object.assign(value.by_mode, { extra: 0 }),
    (value) => Object.assign(value.by_status, { extra: 0 }),
    (value) => Object.assign(value.by_mode_and_status, { extra: structuredClone(emptyRow) }),
    (value) => Object.assign(value.by_mode_and_status.discovery, { extra: 0 }),
  ];
  for (const mutate of mutations) {
    const value = structuredClone(canonical);
    mutate(value);
    assert.equal(isTrialCounts(value), false);
  }
} finally {
  await unlink(temporary).catch(() => undefined);
}
