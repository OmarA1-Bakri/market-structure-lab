"""Generate the browser-safe dashboard evidence snapshot."""

from __future__ import annotations

import argparse
from pathlib import Path

from market_structure_lab.data.dashboard_evidence import write_lab_evidence


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository-root", type=Path, default=Path.cwd())
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("dashboard/public/data/lab-evidence-v1.json"),
    )
    parser.add_argument("--generated-at", help="Explicit UTC publication timestamp")
    arguments = parser.parse_args()
    root = arguments.repository_root.resolve()
    output = arguments.output
    if not output.is_absolute():
        output = root / output
    evidence = write_lab_evidence(root, output, generated_at=arguments.generated_at)
    history = evidence["reconciliation"]["full_history"]
    print(
        f"wrote {output} from verified evidence; "
        f"RR-000008 {history['verified_work_units']}/{history['expected_work_units']} units"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
