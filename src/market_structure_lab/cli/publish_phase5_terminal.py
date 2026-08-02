"""Publish and verify the Phase 5 empty-eligible-batch terminal decision."""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from pathlib import Path

from market_structure_lab.research.phase5_terminal import (
    publish_phase5_terminal_closure,
    verify_phase5_terminal_closure,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--programme-root", type=Path, required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    return parser


def run(args: argparse.Namespace) -> int:
    closure = publish_phase5_terminal_closure(
        args.programme_root,
        args.config,
        args.output_root,
    )
    verify_phase5_terminal_closure(closure)
    payload = closure.to_dict()
    print(f"{payload['final_holdout_marker']} {closure.programme_id} {closure.closure_sha256}")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    return run(build_parser().parse_args(argv))


if __name__ == "__main__":
    raise SystemExit(main())
