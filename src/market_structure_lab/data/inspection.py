"""Read-only PostgreSQL schema and candle-quality inspection."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from typing import Any

from sqlalchemy import Connection, Engine, create_engine, inspect, text
from sqlalchemy.engine import RowMapping
from sqlalchemy.exc import SQLAlchemyError

from market_structure_lab.core.config import CandleSourceMapping, MarketDataSettings, TimestampUnit

_SYSTEM_SCHEMAS = {"information_schema"}
_REQUIRED_CANDLE_COLUMNS = {"open", "high", "low", "close", "volume"}
_TIMESTAMP_COLUMNS = ("open_time", "timestamp", "time", "datetime")
_TIMEFRAME_COLUMNS = ("interval", "timeframe")
_SYMBOL_COLUMNS = ("symbol", "ticker", "instrument")


class InspectionMode(str, Enum):
    QUICK = "quick"
    FULL = "full"


@dataclass(frozen=True, slots=True)
class ColumnInspection:
    name: str
    data_type: str
    nullable: bool


@dataclass(frozen=True, slots=True)
class CandidateMapping:
    timestamp: str
    symbol: str
    timeframe: str
    open: str
    high: str
    low: str
    close: str
    volume: str


@dataclass(frozen=True, slots=True)
class CandleQuality:
    row_count: int
    symbols: tuple[str, ...]
    timeframes: tuple[str, ...]
    minimum_timestamp: str | None
    maximum_timestamp: str | None
    duplicate_key_count: int
    invalid_ohlc_count: int
    null_counts: dict[str, int]
    gap_run_count: int
    missing_minute_count: int
    largest_gap_minutes: int
    large_gaps: tuple[dict[str, Any], ...]
    sample_rows: tuple[dict[str, Any], ...]


@dataclass(frozen=True, slots=True)
class TableInspection:
    schema: str
    name: str
    columns: tuple[ColumnInspection, ...]
    estimated_row_count: int | None
    exact_row_count: int | None
    candidate_mapping: CandidateMapping | None
    quality: CandleQuality | None


@dataclass(frozen=True, slots=True)
class InspectionReport:
    database: str
    connection_url: str
    mode: InspectionMode
    schemas: tuple[str, ...]
    tables: tuple[TableInspection, ...]

    @property
    def table_count(self) -> int:
        return len(self.tables)

    @property
    def candidate_count(self) -> int:
        return sum(table.candidate_mapping is not None for table in self.tables)


class DatabaseInspectionError(RuntimeError):
    """A secret-safe failure raised by configured database inspection."""


def inspect_configured_database(
    settings: MarketDataSettings,
    *,
    mode: InspectionMode = InspectionMode.QUICK,
    sample_limit: int = 5,
    large_gap_minutes: int = 60,
) -> InspectionReport:
    """Inspect the configured database and dispose the temporary engine."""
    engine: Engine | None = None
    try:
        engine = create_engine(settings.database.url)
        with engine.connect() as connection:
            return inspect_database(
                connection,
                settings=settings,
                mode=mode,
                sample_limit=sample_limit,
                large_gap_minutes=large_gap_minutes,
            )
    except SQLAlchemyError:
        raise DatabaseInspectionError(
            f"database inspection failed for {settings.database.redacted_url}"
        ) from None
    finally:
        if engine is not None:
            engine.dispose()


def inspect_database(
    bind: Engine | Connection,
    *,
    settings: MarketDataSettings,
    mode: InspectionMode = InspectionMode.QUICK,
    sample_limit: int = 5,
    large_gap_minutes: int = 60,
) -> InspectionReport:
    """Inspect real schemas/tables and optionally run exact quality queries."""
    if sample_limit < 0:
        raise ValueError("sample_limit must be greater than or equal to 0")
    if large_gap_minutes < 1:
        raise ValueError("large_gap_minutes must be positive")
    if not isinstance(mode, InspectionMode):
        mode = InspectionMode(mode)

    if isinstance(bind, Engine):
        with bind.connect() as connection:
            return inspect_database(
                connection,
                settings=settings,
                mode=mode,
                sample_limit=sample_limit,
                large_gap_minutes=large_gap_minutes,
            )

    inspector = inspect(bind)
    schemas = tuple(
        sorted(
            schema
            for schema in inspector.get_schema_names()
            if schema not in _SYSTEM_SCHEMAS and not schema.startswith("pg_")
        )
    )
    tables: list[TableInspection] = []
    for schema in schemas:
        for table_name in sorted(inspector.get_table_names(schema=schema)):
            raw_columns = inspector.get_columns(table_name, schema=schema)
            columns = tuple(
                ColumnInspection(
                    name=str(column["name"]),
                    data_type=str(column["type"]),
                    nullable=bool(column.get("nullable", True)),
                )
                for column in sorted(raw_columns, key=lambda item: str(item["name"]))
            )
            mapping = detect_candle_mapping([column.name for column in columns])
            estimated = _estimated_row_count(bind, schema=schema, table=table_name)
            exact: int | None = None
            quality: CandleQuality | None = None
            if mode is InspectionMode.FULL:
                exact = _exact_row_count(bind, schema=schema, table=table_name)
                if mapping is not None:
                    timestamp_unit = _timestamp_unit(settings.candles, schema, table_name, mapping)
                    quality = _inspect_candle_quality(
                        bind,
                        schema=schema,
                        table=table_name,
                        mapping=mapping,
                        column_names=tuple(column.name for column in columns),
                        timestamp_unit=timestamp_unit,
                        sample_limit=sample_limit,
                        large_gap_minutes=large_gap_minutes,
                    )
            tables.append(
                TableInspection(
                    schema=schema,
                    name=table_name,
                    columns=columns,
                    estimated_row_count=estimated,
                    exact_row_count=exact,
                    candidate_mapping=mapping,
                    quality=quality,
                )
            )

    return InspectionReport(
        database=settings.database.database,
        connection_url=settings.database.redacted_url,
        mode=mode,
        schemas=schemas,
        tables=tuple(tables),
    )


def detect_candle_mapping(column_names: Sequence[str]) -> CandidateMapping | None:
    """Detect a plausible OHLCV mapping by names without assuming a table name."""
    lookup = {name.lower(): name for name in column_names}
    if not _REQUIRED_CANDLE_COLUMNS.issubset(lookup):
        return None
    timestamp = _first_present(lookup, _TIMESTAMP_COLUMNS)
    symbol = _first_present(lookup, _SYMBOL_COLUMNS)
    timeframe = _first_present(lookup, _TIMEFRAME_COLUMNS)
    if timestamp is None or symbol is None or timeframe is None:
        return None
    return CandidateMapping(
        timestamp=timestamp,
        symbol=symbol,
        timeframe=timeframe,
        open=lookup["open"],
        high=lookup["high"],
        low=lookup["low"],
        close=lookup["close"],
        volume=lookup["volume"],
    )


def report_to_dict(report: InspectionReport) -> dict[str, Any]:
    """Convert a report to a deterministic, JSON-compatible mapping."""
    return {
        "candidate_count": report.candidate_count,
        "connection_url": report.connection_url,
        "database": report.database,
        "mode": report.mode.value,
        "schemas": list(report.schemas),
        "table_count": report.table_count,
        "tables": [_table_to_dict(table) for table in report.tables],
    }


def render_json(report: InspectionReport) -> str:
    return json.dumps(report_to_dict(report), indent=2, sort_keys=True) + "\n"


def render_human(report: InspectionReport) -> str:
    """Render a stable report containing no unredacted credentials."""
    lines = [
        "Database inspection",
        f"Database: {report.database}",
        f"Connection: {report.connection_url}",
        f"Mode: {report.mode.value}",
        f"Schemas: {', '.join(report.schemas) if report.schemas else '(none)'}",
        f"Tables: {report.table_count}",
        f"OHLCV candidates: {report.candidate_count}",
    ]
    for table in report.tables:
        count = table.exact_row_count
        count_label = "exact" if count is not None else "estimated"
        if count is None:
            count = table.estimated_row_count
        lines.append(f"- {table.schema}.{table.name}: {count_label}_rows={count}")
        lines.append(
            "  columns: "
            + ", ".join(f"{column.name} ({column.data_type})" for column in table.columns)
        )
        if table.candidate_mapping is not None:
            lines.append("  candidate: OHLCV")
        if table.quality is not None:
            quality = table.quality
            lines.extend(
                [
                    f"  symbols: {', '.join(quality.symbols) or '(none)'}",
                    f"  timeframes: {', '.join(quality.timeframes) or '(none)'}",
                    f"  timestamp_range: {quality.minimum_timestamp} to {quality.maximum_timestamp}",
                    f"  duplicate_keys: {quality.duplicate_key_count}",
                    f"  invalid_ohlc: {quality.invalid_ohlc_count}",
                    "  null_counts: "
                    + ", ".join(
                        f"{name}={count}" for name, count in sorted(quality.null_counts.items())
                    ),
                    f"  gap_runs: {quality.gap_run_count}",
                    f"  missing_minutes: {quality.missing_minute_count}",
                    f"  largest_gap_minutes: {quality.largest_gap_minutes}",
                    f"  large_gaps: {len(quality.large_gaps)}",
                ]
            )
            for sample in quality.sample_rows:
                lines.append(f"  sample: {json.dumps(sample, sort_keys=True)}")
    return "\n".join(lines) + "\n"


def write_json_report(report: InspectionReport, path: str | Path) -> None:
    Path(path).write_text(render_json(report), encoding="utf-8")


def _estimated_row_count(connection: Connection, *, schema: str, table: str) -> int | None:
    if connection.dialect.name != "postgresql":
        return None
    result = connection.execute(
        text(
            """
            select greatest(c.reltuples::bigint, 0)
            from pg_catalog.pg_class as c
            join pg_catalog.pg_namespace as n on n.oid = c.relnamespace
            where n.nspname = :schema and c.relname = :table
            """
        ),
        {"schema": schema, "table": table},
    ).scalar_one_or_none()
    return int(result) if result is not None else None


def _exact_row_count(connection: Connection, *, schema: str, table: str) -> int:
    qualified = _qualified_name(connection, schema, table)
    return int(connection.execute(text(f"select count(*) from {qualified}")).scalar_one())


def _inspect_candle_quality(
    connection: Connection,
    *,
    schema: str,
    table: str,
    mapping: CandidateMapping,
    column_names: tuple[str, ...],
    timestamp_unit: TimestampUnit,
    sample_limit: int,
    large_gap_minutes: int,
) -> CandleQuality:
    qualified = _qualified_name(connection, schema, table)
    quoted = _quoted_mapping(connection, mapping)
    invalid_condition = _invalid_ohlcv_condition(quoted)
    selected_columns = [
        mapping.timestamp,
        mapping.symbol,
        mapping.timeframe,
        mapping.open,
        mapping.high,
        mapping.low,
        mapping.close,
        mapping.volume,
    ]
    null_parts = [
        f"coalesce(sum(case when {_quote(connection, name)} is null then 1 else 0 end), 0) "
        f"as {_quote(connection, name)}"
        for name in column_names
    ]
    summary = (
        connection.execute(
            text(
                f"""
            select count(*) as row_count,
                   min({quoted["timestamp"]}) as minimum_timestamp,
                   max({quoted["timestamp"]}) as maximum_timestamp,
                   count(*) filter (
                       where {invalid_condition}
                   ) as invalid_ohlc_count,
                   {", ".join(null_parts)}
            from {qualified}
            """
            )
        )
        .mappings()
        .one()
    )
    symbols = _distinct_values(connection, qualified, quoted["symbol"])
    timeframes = _distinct_values(connection, qualified, quoted["timeframe"])
    duplicates = int(
        connection.execute(
            text(
                f"""
                select coalesce(sum(duplicate_count - 1), 0)
                from (
                    select count(*) as duplicate_count
                    from {qualified}
                    group by {quoted["symbol"]}, {quoted["timeframe"]}, {quoted["timestamp"]}
                    having count(*) > 1
                ) as duplicate_keys
                """
            )
        ).scalar_one()
    )
    gap_rows = _gap_rows(
        connection,
        qualified=qualified,
        quoted=quoted,
        timestamp_unit=timestamp_unit,
    )
    sample_rows = tuple(
        _normalize_row(row, timestamp_column=mapping.timestamp, timestamp_unit=timestamp_unit)
        for row in connection.execute(
            text(
                f"""
                select {", ".join(_quote(connection, name) for name in selected_columns)}
                from {qualified}
                order by {quoted["symbol"]}, {quoted["timeframe"]}, {quoted["timestamp"]}
                limit :sample_limit
                """
            ),
            {"sample_limit": sample_limit},
        ).mappings()
    )
    null_counts = {name: int(summary[name]) for name in sorted(column_names)}
    minimum = _utc_timestamp(summary["minimum_timestamp"], timestamp_unit)
    maximum = _utc_timestamp(summary["maximum_timestamp"], timestamp_unit)
    return CandleQuality(
        row_count=int(summary["row_count"]),
        symbols=symbols,
        timeframes=timeframes,
        minimum_timestamp=minimum,
        maximum_timestamp=maximum,
        duplicate_key_count=duplicates,
        invalid_ohlc_count=int(summary["invalid_ohlc_count"]),
        null_counts=null_counts,
        gap_run_count=len(gap_rows),
        missing_minute_count=sum(int(row["missing_minutes"]) for row in gap_rows),
        largest_gap_minutes=max((int(row["missing_minutes"]) for row in gap_rows), default=0),
        large_gaps=tuple(
            _normalize_gap(row, timestamp_unit=timestamp_unit)
            for row in gap_rows
            if int(row["missing_minutes"]) >= large_gap_minutes
        ),
        sample_rows=sample_rows,
    )


def _gap_rows(
    connection: Connection,
    *,
    qualified: str,
    quoted: Mapping[str, str],
    timestamp_unit: TimestampUnit,
) -> list[RowMapping]:
    divisor = 1_000 if timestamp_unit is TimestampUnit.MILLISECONDS else 1_000_000
    minute_step = 60 * divisor
    return list(
        connection.execute(
            text(
                f"""
                with ordered as (
                    select {quoted["symbol"]} as symbol,
                           {quoted["timeframe"]} as timeframe,
                           {quoted["timestamp"]} as timestamp_value,
                           lead({quoted["timestamp"]}) over (
                               partition by {quoted["symbol"]}, {quoted["timeframe"]}
                               order by {quoted["timestamp"]}
                           ) as next_timestamp
                    from {qualified}
                )
                select symbol, timeframe, timestamp_value, next_timestamp,
                       ((next_timestamp - timestamp_value) / :minute_step) - 1 as missing_minutes
                from ordered
                where next_timestamp - timestamp_value > :minute_step
                order by symbol, timeframe, timestamp_value
                """
            ),
            {"minute_step": minute_step},
        ).mappings()
    )


def _distinct_values(connection: Connection, qualified: str, column: str) -> tuple[str, ...]:
    rows = connection.execute(
        text(
            f"select distinct {column} from {qualified} where {column} is not null order by {column}"
        )
    )
    return tuple(str(row[0]) for row in rows)


def _timestamp_unit(
    configured: CandleSourceMapping,
    schema: str,
    table: str,
    mapping: CandidateMapping,
) -> TimestampUnit:
    if (
        schema == configured.schema
        and table == configured.table
        and mapping.timestamp == configured.timestamp_column
    ):
        return configured.timestamp_unit
    return TimestampUnit.MILLISECONDS


def _qualified_name(connection: Connection, schema: str, table: str) -> str:
    return f"{_quote(connection, schema)}.{_quote(connection, table)}"


def _quote(connection: Connection, identifier: str) -> str:
    return connection.dialect.identifier_preparer.quote(identifier)


def _quoted_mapping(connection: Connection, mapping: CandidateMapping) -> dict[str, str]:
    return {
        name: _quote(connection, getattr(mapping, name))
        for name in ("timestamp", "symbol", "timeframe", "open", "high", "low", "close", "volume")
    }


def _invalid_ohlcv_condition(quoted: Mapping[str, str]) -> str:
    numeric = [quoted[name] for name in ("open", "high", "low", "close", "volume")]
    non_finite = [
        f"lower(cast({column} as text)) in ('nan', 'inf', '-inf', 'infinity', '-infinity')"
        for column in numeric
    ]
    invalid = [
        f"{quoted['high']} < {quoted['low']}",
        f"{quoted['open']} < {quoted['low']}",
        f"{quoted['open']} > {quoted['high']}",
        f"{quoted['close']} < {quoted['low']}",
        f"{quoted['close']} > {quoted['high']}",
        *(f"{column} < 0" for column in numeric),
        *non_finite,
    ]
    return " or ".join(invalid)


def _first_present(lookup: Mapping[str, str], candidates: Sequence[str]) -> str | None:
    return next((lookup[name] for name in candidates if name in lookup), None)


def _utc_timestamp(value: Any, unit: TimestampUnit) -> str | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        divisor = 1_000 if unit is TimestampUnit.MILLISECONDS else 1_000_000
        parsed = datetime.fromtimestamp(value / divisor, tz=UTC)
    elif isinstance(value, datetime):
        parsed = value.astimezone(UTC) if value.tzinfo else value.replace(tzinfo=UTC)
    else:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        parsed = parsed.astimezone(UTC) if parsed.tzinfo else parsed.replace(tzinfo=UTC)
    return parsed.isoformat().replace("+00:00", "Z")


def _normalize_gap(
    row: RowMapping,
    *,
    timestamp_unit: TimestampUnit,
) -> dict[str, Any]:
    return {
        "symbol": str(row["symbol"]),
        "timeframe": str(row["timeframe"]),
        "previous_timestamp": _utc_timestamp(row["timestamp_value"], timestamp_unit),
        "next_timestamp": _utc_timestamp(row["next_timestamp"], timestamp_unit),
        "missing_minutes": int(row["missing_minutes"]),
    }


def _normalize_row(
    row: RowMapping,
    *,
    timestamp_column: str,
    timestamp_unit: TimestampUnit,
) -> dict[str, Any]:
    normalized = {str(key): _json_value(value) for key, value in row.items()}
    normalized[timestamp_column] = _utc_timestamp(row[timestamp_column], timestamp_unit)
    return dict(sorted(normalized.items()))


def _json_value(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, datetime):
        parsed = value.astimezone(UTC) if value.tzinfo else value.replace(tzinfo=UTC)
        return parsed.isoformat().replace("+00:00", "Z")
    return str(value)


def _table_to_dict(table: TableInspection) -> dict[str, Any]:
    mapping = table.candidate_mapping
    quality = table.quality
    return {
        "candidate_mapping": (
            {
                name: getattr(mapping, name)
                for name in (
                    "timestamp",
                    "symbol",
                    "timeframe",
                    "open",
                    "high",
                    "low",
                    "close",
                    "volume",
                )
            }
            if mapping is not None
            else None
        ),
        "columns": [
            {"data_type": column.data_type, "name": column.name, "nullable": column.nullable}
            for column in table.columns
        ],
        "estimated_row_count": table.estimated_row_count,
        "exact_row_count": table.exact_row_count,
        "name": table.name,
        "quality": _quality_to_dict(quality) if quality is not None else None,
        "schema": table.schema,
    }


def _quality_to_dict(quality: CandleQuality) -> dict[str, Any]:
    return {
        "duplicate_key_count": quality.duplicate_key_count,
        "gap_run_count": quality.gap_run_count,
        "invalid_ohlc_count": quality.invalid_ohlc_count,
        "large_gaps": list(quality.large_gaps),
        "largest_gap_minutes": quality.largest_gap_minutes,
        "maximum_timestamp": quality.maximum_timestamp,
        "minimum_timestamp": quality.minimum_timestamp,
        "missing_minute_count": quality.missing_minute_count,
        "null_counts": dict(sorted(quality.null_counts.items())),
        "row_count": quality.row_count,
        "sample_rows": list(quality.sample_rows),
        "symbols": list(quality.symbols),
        "timeframes": list(quality.timeframes),
    }
