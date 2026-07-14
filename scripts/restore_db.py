#!/usr/bin/env python3
"""Placeholder restore script for database dumps."""

from pathlib import Path


def main() -> None:
    dump_path = Path("data/dumps/callscore.dump")
    if not dump_path.exists():
        raise FileNotFoundError(f"Expected dump file at {dump_path}")
    print(f"Dump file ready: {dump_path}")


if __name__ == "__main__":
    main()
