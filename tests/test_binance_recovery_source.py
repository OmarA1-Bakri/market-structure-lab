from __future__ import annotations

import hashlib
import io
import json
import zipfile
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from market_structure_lab.data.sources.base import (
    FetchRequest,
    SourceIntegrityError,
    SourceKline,
    SourceRateLimited,
)
from market_structure_lab.data.sources.binance import (
    BinanceSpotSource,
    RetryPolicy,
    iter_archive_klines,
    normalize_spot_timestamp_ms,
    parse_checksum,
    parse_kline_row,
    plan_fetch_segments,
)


def _row(timestamp: int | str, close: str = "101") -> list[object]:
    return [timestamp, "100", "102", "99", close, "2.5", 0, "250", 12, "0", "0", "0"]


def _zip_bytes(rows: list[list[object]]) -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("BTCUSDT-1m.csv", "\n".join(",".join(map(str, row)) for row in rows))
    return output.getvalue()


class FakeTransport:
    def __init__(self, responses: dict[str, bytes], *, stream_status: int = 200) -> None:
        self.responses = responses
        self.stream_status = stream_status
        self.headers: list[dict[str, str]] = []
        self.failures = 0

    def get_bytes(self, url: str, *, headers: dict[str, str], limit: int) -> bytes:
        if self.failures:
            self.failures -= 1
            raise OSError("temporary")
        payload = self.responses[url]
        assert len(payload) <= limit
        return payload

    def stream(self, url: str, *, headers: dict[str, str], chunk_size: int):
        self.headers.append(headers)
        payload = self.responses[url]
        start = int(headers.get("Range", "bytes=0-").split("=")[1].split("-")[0])
        return self.stream_status, (
            payload[index : index + chunk_size] for index in range(start, len(payload), chunk_size)
        )


def test_archive_timestamp_normalization_covers_both_contract_eras() -> None:
    assert normalize_spot_timestamp_ms(1_735_689_600_000) == 1_735_689_600_000
    assert normalize_spot_timestamp_ms(1_735_689_600_000_000) == 1_735_689_600_000
    with pytest.raises(SourceIntegrityError, match="not millisecond aligned"):
        normalize_spot_timestamp_ms(1_735_689_600_000_001)


def test_parse_kline_preserves_exact_decimals_and_optional_fields() -> None:
    parsed = parse_kline_row(_row(1_735_689_600_000_000), symbol="BTCUSDT", timeframe="1m")
    assert parsed.open_time_ms == 1_735_689_600_000
    assert parsed.close == Decimal("101")
    assert parsed.quote_volume == Decimal("250")
    assert parsed.trades == 12


@pytest.mark.parametrize(
    "row, message",
    [
        (_row(1_735_689_600_000, close="103"), "OHLC"),
        ([1_735_689_600_000, "100"], "at least 9"),
        ([*_row(1_735_689_600_000)[:5], "-1", 0, "250", 12], "negative volume"),
    ],
)
def test_parse_kline_fails_closed_on_invalid_rows(row, message) -> None:
    with pytest.raises(SourceIntegrityError, match=message):
        parse_kline_row(row, symbol="BTCUSDT", timeframe="1m")


def test_source_kline_rejects_off_grid_and_non_finite_values() -> None:
    values = dict(
        symbol="BTCUSDT",
        timeframe="1m",
        open_time_ms=1_735_689_600_001,
        open=Decimal("1"),
        high=Decimal("1"),
        low=Decimal("1"),
        close=Decimal("1"),
        volume=Decimal("1"),
    )
    with pytest.raises(ValueError, match="minute grid"):
        SourceKline(**values)
    values["open_time_ms"] = 1_735_689_600_000
    values["volume"] = Decimal("NaN")
    with pytest.raises(ValueError, match="non-finite"):
        SourceKline(**values)


def test_checksum_requires_adjacent_filename_and_sha256() -> None:
    digest = "a" * 64
    assert parse_checksum(f"{digest}  BTCUSDT.zip\n".encode(), "BTCUSDT.zip") == digest
    with pytest.raises(SourceIntegrityError, match="adjacent ZIP"):
        parse_checksum(f"{digest} other.zip".encode(), "BTCUSDT.zip")


def test_fetch_plan_prefers_monthly_daily_then_api_residuals() -> None:
    start = int(datetime(2025, 1, 1, 0, 1, tzinfo=UTC).timestamp() * 1_000)
    end = int(datetime(2025, 3, 1, 0, 1, tzinfo=UTC).timestamp() * 1_000)
    request = FetchRequest("BTCUSDT", "1m", start, end)
    segments = plan_fetch_segments(request)
    assert segments[0].method == "api"
    assert any(segment.method == "monthly" for segment in segments)
    assert segments[-1].method == "api"


def test_offline_archive_is_checksum_pinned_cached_and_microsecond_normalized(tmp_path) -> None:
    start = 1_735_689_600_000
    archive = _zip_bytes([_row(start * 1_000), _row((start + 60_000) * 1_000)])
    digest = hashlib.sha256(archive).hexdigest()
    filename = "BTCUSDT-1m-2025-01-01.zip"
    base = f"https://data.binance.vision/data/spot/daily/klines/BTCUSDT/1m/{filename}"
    transport = FakeTransport({base: archive, f"{base}.CHECKSUM": f"{digest}  {filename}".encode()})
    source = BinanceSpotSource(
        tmp_path,
        transport=transport,
        now=lambda: datetime(2026, 7, 16, tzinfo=UTC),
        sleep=lambda _: None,
    )
    batches = list(source.fetch(FetchRequest("BTCUSDT", "1m", start, start + 86_400_000)))
    assert [row.open_time_ms for batch in batches for row in batch.rows] == [start, start + 60_000]
    assert batches[0].provenance.published_checksum == digest
    assert list(source.fetch(FetchRequest("BTCUSDT", "1m", start, start + 86_400_000)))
    assert len(transport.headers) == 1  # second read uses the verified content-addressed cache


def test_archive_batches_have_distinct_resume_ranges(tmp_path) -> None:
    start = 1_735_689_600_000
    rows = [_row((start + index * 60_000) * 1_000) for index in range(1_001)]
    archive = _zip_bytes(rows)
    digest = hashlib.sha256(archive).hexdigest()
    filename = "BTCUSDT-1m-2025-01-01.zip"
    base = f"https://data.binance.vision/data/spot/daily/klines/BTCUSDT/1m/{filename}"
    transport = FakeTransport({base: archive, f"{base}.CHECKSUM": f"{digest}  {filename}".encode()})
    source = BinanceSpotSource(tmp_path, transport=transport, sleep=lambda _: None)

    batches = list(source.fetch(FetchRequest("BTCUSDT", "1m", start, start + 86_400_000)))

    assert [len(batch.rows) for batch in batches] == [1_000, 1]
    assert [(batch.request.start_ms, batch.request.end_ms) for batch in batches] == [
        (start, start + 1_000 * 60_000),
        (start + 1_000 * 60_000, start + 1_001 * 60_000),
    ]


def test_archive_iterator_rejects_duplicate_keys(tmp_path) -> None:
    start = 1_735_689_600_000
    archive_path = tmp_path / "duplicate.zip"
    archive_path.write_bytes(_zip_bytes([_row(start * 1_000), _row(start * 1_000)]))
    with pytest.raises(SourceIntegrityError, match="duplicate"):
        list(
            iter_archive_klines(
                archive_path,
                symbol="BTCUSDT",
                timeframe="1m",
                start_ms=start,
                end_ms=start + 60_000,
            )
        )


def test_archive_quarantines_off_grid_rows_with_durable_provenance(tmp_path) -> None:
    start = 1_735_689_600_000
    archive = _zip_bytes(
        [
            _row(start * 1_000),
            _row(start + 60_123),
            _row((start + 120_000) * 1_000),
        ]
    )
    digest = hashlib.sha256(archive).hexdigest()
    filename = "BTCUSDT-1m-2025-01-01.zip"
    base = f"https://data.binance.vision/data/spot/daily/klines/BTCUSDT/1m/{filename}"
    transport = FakeTransport({base: archive, f"{base}.CHECKSUM": f"{digest}  {filename}".encode()})
    source = BinanceSpotSource(tmp_path, transport=transport, sleep=lambda _: None)

    batches = list(source.fetch(FetchRequest("BTCUSDT", "1m", start, start + 86_400_000)))

    assert [row.open_time_ms for batch in batches for row in batch.rows] == [
        start,
        start + 120_000,
    ]
    assert sum(batch.provenance.excluded_row_count for batch in batches) == 1
    notes = tuple(note for batch in batches for note in batch.provenance.integrity_notes)
    assert notes == (f"archive_off_minute_grid:first={start + 60_123}:last={start + 60_123}",)


def test_api_is_bounded_hashed_retried_and_empty_is_explicit(tmp_path) -> None:
    start = 1_735_689_660_000
    request = FetchRequest("BTCUSDT", "1m", start, start + 60_000)
    expected_query = (
        "https://data-api.binance.vision/api/v3/klines?"
        f"symbol=BTCUSDT&interval=1m&startTime={start}&endTime={start + 59_999}&limit=1000"
    )
    payload = json.dumps([_row(start)]).encode()
    transport = FakeTransport({expected_query: payload})
    transport.failures = 1
    source = BinanceSpotSource(
        tmp_path,
        transport=transport,
        retry_policy=RetryPolicy(attempts=2, initial_delay_seconds=0),
        sleep=lambda _: None,
    )
    batch = next(source.fetch(request))
    assert len(batch.rows) == 1
    assert batch.provenance.payload_checksum == hashlib.sha256(payload).hexdigest()

    transport.responses[expected_query] = b"[]"
    empty = next(source.fetch(request))
    assert empty.authoritative_empty and not empty.rows


def test_api_rejects_duplicate_and_out_of_requested_range(tmp_path) -> None:
    start = 1_735_689_660_000
    request = FetchRequest("BTCUSDT", "1m", start, start + 60_000)
    prefix = "https://data-api.binance.vision/api/v3/klines?"
    transport = FakeTransport({})
    source = BinanceSpotSource(tmp_path, transport=transport, sleep=lambda _: None)
    url = next(iter(_api_urls_for_request(request)))
    transport.responses[url] = json.dumps([_row(start), _row(start)]).encode()
    with pytest.raises(SourceIntegrityError, match="duplicate"):
        next(source.fetch(request))
    transport.responses[url] = json.dumps([_row(start + 60_000)]).encode()
    with pytest.raises(SourceIntegrityError, match="out-of-range"):
        next(source.fetch(request))
    assert url.startswith(prefix)


def test_api_honours_retry_after_when_rate_limited(tmp_path) -> None:
    start = 1_735_689_660_000
    request = FetchRequest("BTCUSDT", "1m", start, start + 60_000)
    url = next(iter(_api_urls_for_request(request)))

    class RateLimitedTransport(FakeTransport):
        calls = 0

        def get_bytes(self, url: str, *, headers: dict[str, str], limit: int) -> bytes:
            self.calls += 1
            if self.calls == 1:
                raise SourceRateLimited(3.0)
            return super().get_bytes(url, headers=headers, limit=limit)

    sleeps: list[float] = []
    transport = RateLimitedTransport({url: json.dumps([_row(start)]).encode()})
    source = BinanceSpotSource(
        tmp_path,
        transport=transport,
        retry_policy=RetryPolicy(attempts=2, initial_delay_seconds=0.5),
        request_interval_seconds=0,
        sleep=sleeps.append,
    )
    assert len(next(source.fetch(request)).rows) == 1
    assert 3.0 in sleeps


def _api_urls_for_request(request: FetchRequest):
    end = request.end_ms - 1
    yield (
        "https://data-api.binance.vision/api/v3/klines?"
        f"symbol={request.symbol}&interval={request.timeframe}&startTime={request.start_ms}"
        f"&endTime={end}&limit=1000"
    )
