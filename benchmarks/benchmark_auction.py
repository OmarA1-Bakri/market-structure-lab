"""Reproducible Phase 2 benchmark for the bounded auction engine.

The benchmark deliberately uses only the standard library and the installed
project. Input generation and snapshot hashing are outside the measured engine
update intervals. Peak memory is measured in a separate pass so ``tracemalloc``
does not distort the throughput samples.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import platform
import random
import statistics
import sys
import tracemalloc
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from time import perf_counter_ns
from typing import Any

from market_structure_lab.auction.engine import AuctionEngine, AuctionSnapshot
from market_structure_lab.auction.models import AuctionCandle
from market_structure_lab.auction.replay import canonical_snapshot_json
from market_structure_lab.auction.windows import RollingBars
from market_structure_lab.profiles.allocation import UniformAllocation
from market_structure_lab.profiles.binning import FixedStepBins
from market_structure_lab.profiles.models import Candle as ProfileCandle
from market_structure_lab.profiles.models import ProfileSnapshot
from market_structure_lab.profiles.volume import calculate_profile


SCHEMA_VERSION = "phase2-auction-benchmark-v1"
DEFAULT_COUNT = 100_000
DEFAULT_REPETITIONS = 3
DEFAULT_SEED = 2_026_071_6
DEFAULT_WINDOW_BARS = 1_440
DEFAULT_CHUNK_SIZE = 1_000
DEFAULT_EQUIVALENCE_COUNT = 512
DEFAULT_OUTPUT = Path("docs/benchmarks/phase2-auction-baseline.json")


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--count", type=_positive_int, default=DEFAULT_COUNT)
    parser.add_argument("--repetitions", type=_positive_int, default=DEFAULT_REPETITIONS)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--window-bars", type=_positive_int, default=DEFAULT_WINDOW_BARS)
    parser.add_argument("--chunk-size", type=_positive_int, default=DEFAULT_CHUNK_SIZE)
    parser.add_argument(
        "--equivalence-count", type=_positive_int, default=DEFAULT_EQUIVALENCE_COUNT
    )
    return parser.parse_args(argv)


def _positive_int(value: str) -> int:
    parsed = int(value)
    if parsed < 1:
        raise argparse.ArgumentTypeError("value must be a positive integer")
    return parsed


def generate_candles(*, count: int, seed: int) -> tuple[tuple[AuctionCandle, ...], str]:
    """Generate deterministic, valid minute candles and hash their observations."""
    generator = random.Random(seed)
    timestamp = datetime(2025, 1, 1, tzinfo=UTC)
    previous_close_ticks = 3_000_000
    candles: list[AuctionCandle] = []
    digest = hashlib.sha256()
    for minute in range(count):
        open_ticks = previous_close_ticks
        close_ticks = max(10, open_ticks + generator.randint(-18, 18))
        low_ticks = min(open_ticks, close_ticks) - generator.randint(0, 8)
        high_ticks = max(open_ticks, close_ticks) + generator.randint(0, 8)
        volume = float(1_000 + generator.randrange(90_000)) / 100.0
        candle = AuctionCandle(
            timestamp=timestamp + timedelta(minutes=minute),
            symbol="BTCUSDT",
            timeframe="1m",
            open=open_ticks / 100.0,
            high=high_ticks / 100.0,
            low=low_ticks / 100.0,
            close=close_ticks / 100.0,
            volume=volume,
            segment_id=0,
        )
        candles.append(candle)
        digest.update(_canonical_json(_input_material(candle)))
        digest.update(b"\n")
        previous_close_ticks = close_ticks
    return tuple(candles), digest.hexdigest()


def _engine(*, window_bars: int) -> AuctionEngine:
    return AuctionEngine(
        binning=FixedStepBins(step=0.10, origin=0.0, version="benchmark-fixed-step-v1"),
        allocation=UniformAllocation(model_id="benchmark-uniform-touched-v1"),
        window_policy=RollingBars(max_bars=window_bars),
        dataset_version="phase2-benchmark-generated-v1",
        config_version="phase2-benchmark-config-v1",
    )


def timed_repetition(
    candles: Sequence[AuctionCandle], *, window_bars: int, chunk_size: int
) -> dict[str, Any]:
    """Measure only ``AuctionEngine.update`` calls and hash results between calls."""
    engine = _engine(window_bars=window_bars)
    stream_digest = hashlib.sha256()
    chunk_update_ns: list[int] = []
    current_chunk_ns = 0
    current_chunk_count = 0
    active_window_max = 0

    for candle in candles:
        started = perf_counter_ns()
        snapshot = engine.update(candle)
        current_chunk_ns += perf_counter_ns() - started
        current_chunk_count += 1
        if snapshot is None:  # pragma: no cover - rolling windows include every candle
            raise RuntimeError("rolling benchmark unexpectedly excluded a candle")
        stream_digest.update(canonical_snapshot_json(snapshot).encode("utf-8"))
        stream_digest.update(b"\n")
        active_window_max = max(active_window_max, engine.active_count)
        if current_chunk_count == chunk_size:
            chunk_update_ns.append(current_chunk_ns)
            current_chunk_ns = 0
            current_chunk_count = 0

    if current_chunk_count:
        normalized = round(current_chunk_ns * chunk_size / current_chunk_count)
        chunk_update_ns.append(normalized)

    elapsed_ns = sum(chunk_update_ns[:-1]) if current_chunk_count else sum(chunk_update_ns)
    if current_chunk_count:
        elapsed_ns += round(chunk_update_ns[-1] * current_chunk_count / chunk_size)
    chunk_ms_per_1k = [value / 1_000_000 * 1_000 / chunk_size for value in chunk_update_ns]
    elapsed_seconds = elapsed_ns / 1_000_000_000
    return {
        "elapsed_engine_seconds": elapsed_seconds,
        "throughput_candles_per_second": len(candles) / elapsed_seconds,
        "chunk_latency_ms_per_1000": {
            "minimum": min(chunk_ms_per_1k),
            "median": statistics.median(chunk_ms_per_1k),
            "p95": _percentile(chunk_ms_per_1k, 0.95),
            "maximum": max(chunk_ms_per_1k),
            "sample_count": len(chunk_ms_per_1k),
        },
        "active_window_max": active_window_max,
        "snapshot_stream_sha256": stream_digest.hexdigest(),
    }


def measure_peak_memory(candles: Sequence[AuctionCandle], *, window_bars: int) -> int:
    """Measure engine-state peak allocations, excluding pre-generated input candles."""
    engine = _engine(window_bars=window_bars)
    tracemalloc.start()
    try:
        for candle in candles:
            engine.update(candle)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    return peak


def verify_incremental_equivalence(
    candles: Sequence[AuctionCandle], *, window_bars: int
) -> dict[str, Any]:
    """Compare every incremental snapshot with bounded full recomputation."""
    binning = FixedStepBins(step=0.10, origin=0.0, version="benchmark-fixed-step-v1")
    allocation = UniformAllocation(model_id="benchmark-uniform-touched-v1")
    engine = _engine(window_bars=window_bars)
    active: list[AuctionCandle] = []
    for position, candle in enumerate(candles):
        active.append(candle)
        del active[:-window_bars]
        snapshot = engine.update(candle)
        if snapshot is None:  # pragma: no cover - rolling windows include every candle
            raise RuntimeError("rolling equivalence check unexpectedly excluded a candle")
        contributions = [
            allocation.allocate(
                ProfileCandle(
                    open=item.open,
                    high=item.high,
                    low=item.low,
                    close=item.close,
                    volume=item.volume,
                ),
                binning,
            )
            for item in active
        ]
        expected = calculate_profile(
            contributions,
            binning=binning,
            allocation_id=allocation.model_id,
        )
        _assert_profile_equivalent(
            position,
            snapshot,
            expected,
            expected_count=min(position + 1, window_bars),
        )
    return {
        "checked_snapshots": len(candles),
        "window_bars": window_bars,
        "status": "passed",
        "absolute_float_tolerance": 1e-12,
    }


def _assert_profile_equivalent(
    position: int,
    snapshot: AuctionSnapshot,
    expected: ProfileSnapshot,
    *,
    expected_count: int,
) -> None:
    actual_profile = snapshot.profile
    if set(actual_profile.bin_volumes) != set(expected.bin_volumes):
        raise AssertionError(f"bin set differs at snapshot {position}")
    for index, actual_volume in actual_profile.bin_volumes.items():
        if not math.isclose(
            actual_volume, expected.bin_volumes[index], rel_tol=1e-12, abs_tol=1e-12
        ):
            raise AssertionError(f"bin {index} volume differs at snapshot {position}")
    exact_fields = (
        (actual_profile.poc_index, expected.poc_index),
        (actual_profile.value_area_low_index, expected.value_area_low_index),
        (actual_profile.value_area_high_index, expected.value_area_high_index),
        (snapshot.candle_count, expected_count),
    )
    if any(actual != wanted for actual, wanted in exact_fields):
        raise AssertionError(f"profile index/count differs at snapshot {position}")
    for actual, wanted in (
        (actual_profile.total_volume, expected.total_volume),
        (actual_profile.vwap, expected.vwap),
    ):
        if actual is None or wanted is None:
            if actual is not wanted:
                raise AssertionError(f"optional profile value differs at snapshot {position}")
        elif not math.isclose(actual, wanted, rel_tol=1e-12, abs_tol=1e-12):
            raise AssertionError(f"profile float differs at snapshot {position}")


def build_report(args: argparse.Namespace) -> dict[str, Any]:
    candles, input_hash = generate_candles(count=args.count, seed=args.seed)
    equivalence_count = min(args.count, args.equivalence_count)
    equivalence = verify_incremental_equivalence(
        candles[:equivalence_count], window_bars=args.window_bars
    )
    repetitions = [
        timed_repetition(
            candles,
            window_bars=args.window_bars,
            chunk_size=args.chunk_size,
        )
        for _ in range(args.repetitions)
    ]
    stream_hashes = {item["snapshot_stream_sha256"] for item in repetitions}
    if len(stream_hashes) != 1:
        raise AssertionError("snapshot stream hash changed between repetitions")
    memory_count = min(len(candles), max(args.window_bars * 2, 5_000))
    peak_memory = measure_peak_memory(candles[:memory_count], window_bars=args.window_bars)
    throughputs = [item["throughput_candles_per_second"] for item in repetitions]
    p95_values = [item["chunk_latency_ms_per_1000"]["p95"] for item in repetitions]
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "execution": {
            "environment": {"PYTHONPATH": "src"},
            "argv": [
                "python",
                "benchmarks/benchmark_auction.py",
                "--count",
                str(args.count),
                "--repetitions",
                str(args.repetitions),
                "--equivalence-count",
                str(args.equivalence_count),
                "--window-bars",
                str(args.window_bars),
                "--output",
                str(args.output).replace("\\", "/"),
            ],
        },
        "environment": {
            "python": platform.python_version(),
            "python_implementation": platform.python_implementation(),
            "platform": platform.platform(),
            "processor": platform.processor() or "unreported",
            "logical_cpu_count": os.cpu_count(),
        },
        "workload": {
            "count": args.count,
            "repetitions": args.repetitions,
            "seed": args.seed,
            "symbol": "BTCUSDT",
            "timeframe": "1m",
            "window_bars": args.window_bars,
            "chunk_size": args.chunk_size,
            "binning": "benchmark-fixed-step-v1:step=0.1:origin=0",
            "allocation": "benchmark-uniform-touched-v1",
            "input_generation_timed": False,
            "snapshot_hashing_timed": False,
            "memory_pass_separate_from_throughput": True,
            "memory_measurement_count": memory_count,
        },
        "correctness": {
            "input_sha256": input_hash,
            "snapshot_stream_sha256": next(iter(stream_hashes)),
            "repetition_hashes_identical": True,
            "incremental_vs_full_recomputation": equivalence,
        },
        "results": {
            "repetitions": repetitions,
            "summary": {
                "throughput_candles_per_second_median": statistics.median(throughputs),
                "throughput_candles_per_second_minimum": min(throughputs),
                "chunk_p95_ms_per_1000_median": statistics.median(p95_values),
                "peak_tracemalloc_bytes": peak_memory,
                "peak_tracemalloc_mebibytes": peak_memory / (1024 * 1024),
                "memory_measurement_candles": memory_count,
                "active_window_max": max(item["active_window_max"] for item in repetitions),
            },
        },
    }


def _input_material(candle: AuctionCandle) -> list[Any]:
    return [
        candle.timestamp.isoformat(),
        candle.symbol,
        candle.timeframe,
        candle.open,
        candle.high,
        candle.low,
        candle.close,
        candle.volume,
        candle.segment_id,
    ]


def _canonical_json(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=True,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _percentile(values: Sequence[float], fraction: float) -> float:
    ordered = sorted(values)
    return ordered[max(0, math.ceil(len(ordered) * fraction) - 1)]


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    report = build_report(args)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(report["results"]["summary"], indent=2, sort_keys=True))
    print(f"wrote {args.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
