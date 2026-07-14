from __future__ import annotations

import os
from typing import Any


def get_connection_url() -> str:
    """Build a SQLAlchemy-compatible connection URL from environment variables."""
    host = os.getenv("POSTGRES_HOST", "localhost")
    port = os.getenv("POSTGRES_PORT", "5432")
    user = os.getenv("POSTGRES_USER", "postgres")
    password = os.getenv("POSTGRES_PASSWORD", "postgres")
    database = os.getenv("POSTGRES_DB", "research")
    return f"postgresql+psycopg://{user}:{password}@{host}:{port}/{database}"


def build_inspection_report(
    database: str,
    table_rows: list[tuple[str, str, int]],
    connection_url: str,
) -> dict[str, Any]:
    """Create a structured inspection report for the database.

    The report includes a summary of available tables and the connection URL.
    """
    tables = [
        {
            "schema": schema,
            "name": name,
            "row_count": row_count,
        }
        for schema, name, row_count in table_rows
    ]
    return {
        "database": database,
        "connection_url": connection_url,
        "table_count": len(tables),
        "tables": tables,
    }
