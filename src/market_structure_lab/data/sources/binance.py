"""Official Binance Spot archive and public REST source adapter.

Spot public-data archives switched timestamp fields to microseconds on
2025-01-01. The public REST adapter explicitly requests the default millisecond
representation and still normalizes by magnitude before validating bounds.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from threading import Lock
from typing import Callable, Protocol, TypeVar

from market_structure_lab.data.sources.base import (
    FetchBatch,
    FetchRequest,
    SourceIntegrityError,
    SourceKline,
    SourceProvenance,
    SourceRateLimited,
    SourceUnavailable,
)

ARCHIVE_BASE_URL = "https://data.binance.vision/data/spot"
REST_BASE_URL = "https://data-api.binance.vision"
REST_KLINE_LIMIT = 1_000
MINUTE_MS = 60_000
_MICROSECOND_MAGNITUDE = 100_000_000_000_000
_MAX_CHECKSUM_BYTES = 8_192
_T = TypeVar("_T")


class HttpTransport(Protocol):
    def get_bytes(self, url: str, *, headers: dict[str, str], limit: int) -> bytes: ...

    def stream(
        self, url: str, *, headers: dict[str, str], chunk_size: int
    ) -> tuple[int, Iterable[bytes]]: ...


class UrllibTransport:
    """Small standard-library transport with bounded reads."""

    def __init__(self, timeout_seconds: float = 30.0) -> None:
        self.timeout_seconds = timeout_seconds

    def get_bytes(self, url: str, *, headers: dict[str, str], limit: int) -> bytes:
        request = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                payload = response.read(limit + 1)
        except urllib.error.HTTPError as error:
            if error.code == 404:
                raise SourceUnavailable(f"source payload unavailable: {url}") from error
            if error.code == 429:
                raise SourceRateLimited(_retry_after(error)) from error
            raise
        if len(payload) > limit:
            raise SourceIntegrityError(f"source response exceeded the {limit}-byte limit")
        return payload

    def stream(
        self, url: str, *, headers: dict[str, str], chunk_size: int
    ) -> tuple[int, Iterable[bytes]]:
        request = urllib.request.Request(url, headers=headers)
        try:
            response = urllib.request.urlopen(request, timeout=self.timeout_seconds)
        except urllib.error.HTTPError as error:
            if error.code == 404:
                raise SourceUnavailable(f"source payload unavailable: {url}") from error
            if error.code == 429:
                raise SourceRateLimited(_retry_after(error)) from error
            raise
        status = int(getattr(response, "status", 200))

        def chunks() -> Iterator[bytes]:
            with response:
                while chunk := response.read(chunk_size):
                    yield chunk

        return status, chunks()


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    attempts: int = 4
    initial_delay_seconds: float = 0.5
    maximum_delay_seconds: float = 8.0

    def __post_init__(self) -> None:
        if self.attempts < 1 or self.initial_delay_seconds < 0:
            raise ValueError("retry policy requires positive attempts and non-negative delay")


@dataclass(frozen=True, slots=True)
class DownloadArtifact:
    path: Path
    url: str
    checksum_sha256: str
    published_checksum: str
    retrieved_at: str


@dataclass(frozen=True, slots=True)
class FetchSegment:
    method: str
    start_ms: int
    end_ms: int
    archive_period: str | None = None


def normalize_spot_timestamp_ms(raw: object) -> int:
    """Normalize Binance archive/API millisecond or microsecond timestamps."""
    try:
        value = int(str(raw))
    except (TypeError, ValueError) as error:
        raise SourceIntegrityError(f"invalid Binance timestamp: {raw!r}") from error
    if value >= _MICROSECOND_MAGNITUDE:
        if value % 1_000:
            raise SourceIntegrityError("microsecond kline open time is not millisecond aligned")
        value //= 1_000
    if value < 946_684_800_000 or value >= 4_102_444_800_000:
        raise SourceIntegrityError(f"Binance timestamp is outside the supported epoch: {value}")
    return value


def parse_kline_row(row: Sequence[object], *, symbol: str, timeframe: str) -> SourceKline:
    if len(row) < 9:
        raise SourceIntegrityError("Binance kline row must contain at least 9 fields")
    try:
        return SourceKline(
            symbol=symbol,
            timeframe=timeframe,
            open_time_ms=normalize_spot_timestamp_ms(row[0]),
            open=Decimal(str(row[1])),
            high=Decimal(str(row[2])),
            low=Decimal(str(row[3])),
            close=Decimal(str(row[4])),
            volume=Decimal(str(row[5])),
            quote_volume=Decimal(str(row[7])),
            trades=int(str(row[8])),
        )
    except (InvalidOperation, TypeError, ValueError) as error:
        raise SourceIntegrityError(f"invalid Binance kline: {error}") from error


def parse_checksum(payload: bytes, expected_filename: str) -> str:
    try:
        parts = payload.decode("ascii").strip().split()
    except UnicodeDecodeError as error:
        raise SourceIntegrityError("archive checksum is not ASCII") from error
    if len(parts) < 2 or parts[-1].lstrip("*") != expected_filename:
        raise SourceIntegrityError("archive checksum does not name the adjacent ZIP")
    digest = parts[0].lower()
    if len(digest) != 64 or any(character not in "0123456789abcdef" for character in digest):
        raise SourceIntegrityError("archive checksum is not a SHA-256 digest")
    return digest


def plan_fetch_segments(request: FetchRequest) -> tuple[FetchSegment, ...]:
    """Prefer complete monthly/daily archives and use REST only for residual ranges."""
    segments: list[FetchSegment] = []
    cursor = _from_ms(request.start_ms)
    end = _from_ms(request.end_ms)
    while cursor < end:
        next_month = _next_month(cursor)
        if _is_month_start(cursor) and next_month <= end:
            segments.append(
                FetchSegment(
                    "monthly", _to_ms(cursor), _to_ms(next_month), cursor.strftime("%Y-%m")
                )
            )
            cursor = next_month
            continue
        next_day = cursor.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
        if _is_day_start(cursor) and next_day <= end:
            segments.append(
                FetchSegment("daily", _to_ms(cursor), _to_ms(next_day), cursor.strftime("%Y-%m-%d"))
            )
            cursor = next_day
            continue
        residual_end = min(next_day, end)
        segments.append(FetchSegment("api", _to_ms(cursor), _to_ms(residual_end)))
        cursor = residual_end
    return tuple(segments)


class BinanceSpotSource:
    name = "binance_spot"

    def __init__(
        self,
        cache_dir: Path,
        *,
        transport: HttpTransport | None = None,
        retry_policy: RetryPolicy = RetryPolicy(),
        now: Callable[[], datetime] | None = None,
        sleep: Callable[[float], None] | None = None,
        request_interval_seconds: float = 0.05,
    ) -> None:
        self.cache_dir = cache_dir
        self.transport = transport or UrllibTransport()
        self.retry_policy = retry_policy
        self._now = now or (lambda: datetime.now(UTC))
        self._sleep = sleep or time.sleep
        if request_interval_seconds < 0:
            raise ValueError("request_interval_seconds cannot be negative")
        self.request_interval_seconds = request_interval_seconds
        self._download_locks_guard = Lock()
        self._download_locks: dict[str, Lock] = {}

    def fetch(self, request: FetchRequest) -> Iterator[FetchBatch]:
        for segment in plan_fetch_segments(request):
            bounded_request = FetchRequest(
                request.symbol, request.timeframe, segment.start_ms, segment.end_ms
            )
            if segment.method == "api":
                yield from self._fetch_api(bounded_request)
                continue
            try:
                yield from self._fetch_archive(bounded_request, segment)
            except SourceUnavailable:
                # A missing or not-yet-published archive is a residual range, not permission
                # to synthesize candles. The public kline endpoint remains authoritative.
                yield from self._fetch_api(bounded_request)

    def _fetch_archive(self, request: FetchRequest, segment: FetchSegment) -> Iterator[FetchBatch]:
        assert segment.archive_period is not None
        filename = f"{request.symbol}-{request.timeframe}-{segment.archive_period}.zip"
        url = (
            f"{ARCHIVE_BASE_URL}/{segment.method}/klines/"
            f"{request.symbol}/{request.timeframe}/{filename}"
        )
        artifact = self._download_verified_archive(url)
        rows: list[SourceKline] = []
        emitted = False
        for row in iter_archive_klines(
            artifact.path,
            symbol=request.symbol,
            timeframe=request.timeframe,
            start_ms=request.start_ms,
            end_ms=request.end_ms,
        ):
            rows.append(row)
            if len(rows) == REST_KLINE_LIMIT:
                emitted = True
                yield self._archive_batch(request, tuple(rows), artifact)
                rows.clear()
        if rows:
            emitted = True
            yield self._archive_batch(request, tuple(rows), artifact)
        if not emitted:
            yield self._archive_batch(request, (), artifact)

    def _archive_batch(
        self,
        request: FetchRequest,
        rows: tuple[SourceKline, ...],
        artifact: DownloadArtifact,
    ) -> FetchBatch:
        batch_request = request
        if rows:
            batch_request = FetchRequest(
                request.symbol,
                request.timeframe,
                rows[0].open_time_ms,
                rows[-1].open_time_ms + MINUTE_MS,
            )
        return FetchBatch(
            request=batch_request,
            rows=rows,
            provenance=SourceProvenance(
                source_name=self.name,
                source_revision="binance-public-data-v1",
                location=artifact.url,
                payload_checksum=artifact.checksum_sha256,
                published_checksum=artifact.published_checksum,
                retrieved_at=artifact.retrieved_at,
            ),
            authoritative_empty=not rows,
        )

    def _fetch_api(self, request: FetchRequest) -> Iterator[FetchBatch]:
        cursor = request.start_ms
        while cursor < request.end_ms:
            page_end = min(request.end_ms, cursor + REST_KLINE_LIMIT * MINUTE_MS)
            query = urllib.parse.urlencode(
                {
                    "symbol": request.symbol,
                    "interval": request.timeframe,
                    "startTime": cursor,
                    "endTime": page_end - 1,
                    "limit": REST_KLINE_LIMIT,
                }
            )
            url = f"{REST_BASE_URL}/api/v3/klines?{query}"
            self._sleep(self.request_interval_seconds)
            payload = self._retry(
                lambda: self.transport.get_bytes(
                    url,
                    headers={
                        "Accept": "application/json",
                        "User-Agent": "market-structure-lab/0.1",
                    },
                    limit=4_000_000,
                )
            )
            checksum = hashlib.sha256(payload).hexdigest()
            try:
                decoded = json.loads(payload)
            except json.JSONDecodeError as error:
                raise SourceIntegrityError("Binance kline API returned invalid JSON") from error
            if not isinstance(decoded, list):
                raise SourceIntegrityError("Binance kline API returned a non-list payload")
            rows = tuple(
                parse_kline_row(item, symbol=request.symbol, timeframe=request.timeframe)
                for item in decoded
            )
            page_request = FetchRequest(request.symbol, request.timeframe, cursor, page_end)
            _validate_source_page(rows, page_request)
            retrieved_at = self._timestamp()
            yield FetchBatch(
                request=page_request,
                rows=rows,
                provenance=SourceProvenance(
                    source_name=self.name,
                    source_revision="spot-rest-api-v3-klines",
                    location=url,
                    payload_checksum=checksum,
                    retrieved_at=retrieved_at,
                ),
                authoritative_empty=not rows,
            )
            if not rows:
                cursor = page_end
            else:
                cursor = max(page_end, rows[-1].open_time_ms + MINUTE_MS)

    def _download_verified_archive(self, url: str) -> DownloadArtifact:
        with self._download_locks_guard:
            download_lock = self._download_locks.setdefault(url, Lock())
        with download_lock:
            return self._download_verified_archive_locked(url)

    def _download_verified_archive_locked(self, url: str) -> DownloadArtifact:
        filename = Path(urllib.parse.urlparse(url).path).name
        checksum_payload = self._retry(
            lambda: self.transport.get_bytes(
                f"{url}.CHECKSUM",
                headers={"User-Agent": "market-structure-lab/0.1"},
                limit=_MAX_CHECKSUM_BYTES,
            )
        )
        published = parse_checksum(checksum_payload, filename)
        destination = self.cache_dir / "sha256" / published[:2] / published / filename
        metadata = destination.with_suffix(destination.suffix + ".json")
        if destination.exists():
            if _sha256_file(destination) != published:
                raise SourceIntegrityError("content-addressed cache entry failed its SHA-256")
            retrieved_at = _read_retrieved_at(metadata) or self._timestamp()
            return DownloadArtifact(destination, url, published, published, retrieved_at)

        partial = self.cache_dir / "partial" / f"{hashlib.sha256(url.encode()).hexdigest()}.part"
        partial.parent.mkdir(parents=True, exist_ok=True)
        offset = partial.stat().st_size if partial.exists() else 0
        headers = {"User-Agent": "market-structure-lab/0.1"}
        if offset:
            headers["Range"] = f"bytes={offset}-"
        status, chunks = self._retry(
            lambda: self.transport.stream(url, headers=headers, chunk_size=1024 * 1024)
        )
        if offset and status != 206:
            partial.unlink()
            offset = 0
            status, chunks = self._retry(
                lambda: self.transport.stream(
                    url,
                    headers={"User-Agent": "market-structure-lab/0.1"},
                    chunk_size=1024 * 1024,
                )
            )
        if status not in (200, 206):
            raise SourceUnavailable(f"unexpected archive HTTP status {status}")
        with partial.open("ab" if offset else "wb") as handle:
            for chunk in chunks:
                handle.write(chunk)
        actual = _sha256_file(partial)
        if actual != published:
            quarantine = self.cache_dir / "quarantine" / actual / filename
            quarantine.parent.mkdir(parents=True, exist_ok=True)
            partial.replace(quarantine)
            raise SourceIntegrityError(
                f"archive SHA-256 mismatch: published {published}, received {actual}"
            )
        destination.parent.mkdir(parents=True, exist_ok=True)
        partial.replace(destination)
        retrieved_at = self._timestamp()
        _write_json_atomic(
            metadata,
            {
                "url": url,
                "published_checksum": published,
                "payload_checksum": actual,
                "retrieved_at": retrieved_at,
                "source_revision": "binance-public-data-v1",
            },
        )
        return DownloadArtifact(destination, url, actual, published, retrieved_at)

    def _retry(self, operation: Callable[[], _T]) -> _T:
        delay = self.retry_policy.initial_delay_seconds
        for attempt in range(1, self.retry_policy.attempts + 1):
            try:
                return operation()
            except SourceUnavailable:
                raise
            except SourceRateLimited as error:
                if attempt == self.retry_policy.attempts:
                    raise SourceUnavailable(
                        "Binance rate limit persisted through retries"
                    ) from error
                self._sleep(max(delay, error.retry_after_seconds))
                delay = min(delay * 2, self.retry_policy.maximum_delay_seconds)
            except (OSError, urllib.error.URLError) as error:
                if attempt == self.retry_policy.attempts:
                    raise SourceUnavailable(
                        "Binance retrieval exhausted its retry policy"
                    ) from error
                self._sleep(delay)
                delay = min(delay * 2, self.retry_policy.maximum_delay_seconds)
        raise AssertionError("retry loop did not return or raise")

    def _timestamp(self) -> str:
        value = self._now()
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("clock must return a timezone-aware datetime")
        return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def iter_archive_klines(
    archive_path: Path,
    *,
    symbol: str,
    timeframe: str,
    start_ms: int,
    end_ms: int,
) -> Iterator[SourceKline]:
    seen: set[int] = set()
    try:
        with zipfile.ZipFile(archive_path) as archive:
            members = sorted(name for name in archive.namelist() if name.lower().endswith(".csv"))
            if not members:
                raise SourceIntegrityError("Binance archive contains no CSV member")
            for member in members:
                with archive.open(member) as binary:
                    text_stream = io.TextIOWrapper(binary, encoding="utf-8", newline="")
                    for raw in csv.reader(text_stream):
                        if not raw or raw[0].strip().lower() in {"open_time", "open time"}:
                            continue
                        row = parse_kline_row(raw, symbol=symbol, timeframe=timeframe)
                        if start_ms <= row.open_time_ms < end_ms:
                            if row.open_time_ms in seen:
                                raise SourceIntegrityError(
                                    "Binance archive contains a duplicate candle key"
                                )
                            seen.add(row.open_time_ms)
                            yield row
    except (OSError, zipfile.BadZipFile) as error:
        raise SourceIntegrityError("Binance archive is not a readable ZIP") from error


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _validate_source_page(rows: Sequence[SourceKline], request: FetchRequest) -> None:
    seen: set[int] = set()
    for row in rows:
        if row.symbol != request.symbol or row.timeframe != request.timeframe:
            raise SourceIntegrityError("Binance response crossed a market boundary")
        if not request.start_ms <= row.open_time_ms < request.end_ms:
            raise SourceIntegrityError("Binance response contained an out-of-range candle")
        if row.open_time_ms in seen:
            raise SourceIntegrityError("Binance response contained a duplicate candle key")
        seen.add(row.open_time_ms)


def _write_json_atomic(path: Path, value: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8"
    )
    os.replace(temporary, path)


def _read_retrieved_at(path: Path) -> str | None:
    if not path.exists():
        return None
    value = json.loads(path.read_text(encoding="utf-8"))
    retrieved_at = value.get("retrieved_at")
    return str(retrieved_at) if retrieved_at else None


def _from_ms(value: int) -> datetime:
    return datetime.fromtimestamp(value / 1_000, tz=UTC)


def _to_ms(value: datetime) -> int:
    return int(value.timestamp() * 1_000)


def _is_day_start(value: datetime) -> bool:
    return value.hour == value.minute == value.second == value.microsecond == 0


def _is_month_start(value: datetime) -> bool:
    return value.day == 1 and _is_day_start(value)


def _next_month(value: datetime) -> datetime:
    if value.month == 12:
        return value.replace(year=value.year + 1, month=1, day=1, hour=0, minute=0)
    return value.replace(month=value.month + 1, day=1, hour=0, minute=0)


def _retry_after(error: urllib.error.HTTPError) -> float:
    value = error.headers.get("Retry-After") if error.headers is not None else None
    try:
        return float(value) if value is not None else 1.0
    except ValueError:
        return 1.0
