#!/usr/bin/env python3
"""Inspect the local PostgreSQL research database."""

from __future__ import annotations

import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.utils.db_inspection import build_inspection_report, get_connection_url


def main() -> None:
    database = os.getenv("POSTGRES_DB", "research")
    connection_url = get_connection_url()
    dump_path = Path("data/dumps/callscore.dump")

    print("Database inspection")
    print(f"Database: {database}")
    print(f"Connection URL: {connection_url}")
    print(f"Expected dump: {dump_path}")

    print("\nInspection summary")
    report = build_inspection_report(
        database=database,
        table_rows=[],
        connection_url=connection_url,
    )
    print(f"Table count: {report['table_count']}")
    print(f"Dump exists: {dump_path.exists()}")


if __name__ == "__main__":
    main()
