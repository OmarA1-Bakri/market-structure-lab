from __future__ import annotations

import json
from dataclasses import FrozenInstanceError, replace
from datetime import UTC, datetime, timedelta
from types import MappingProxyType

import pytest

from market_structure_lab.events.fixed_windows import (
    UTCSessionUnit,
    segment_fixed_windows,
    segment_rolling_windows,
    segment_utc_sessions,
)
from market_structure_lab.events.models import EventKind, make_event
from market_structure_lab.features.models import FeatureRow
from market_structure_lab.features.registry import (
    FeatureDefinition,
    FeatureFamily,
    FeatureRegistry,
    FeatureValueKind,
    LeakageClass,
    MissingPolicy,
)

REGISTRY = FeatureRegistry(
    "FS-000001",
    (
        FeatureDefinition(
            name="auction_location",
            definition="Fixture category",
            family=FeatureFamily.AUCTION,
            value_kind=FeatureValueKind.CATEGORY,
            units="category",
            required_prior_observations=0,
            missing_policy=MissingPolicy.NULL,
            version="v1",
            leakage_class=LeakageClass.AT_CUTOFF,
            allowed_categories=("inside_value",),
        ),
        FeatureDefinition(
            name="poc_distance",
            definition="Fixture distance",
            family=FeatureFamily.AUCTION,
            value_kind=FeatureValueKind.FLOAT,
            units="ratio",
            required_prior_observations=0,
            missing_policy=MissingPolicy.NULL,
            version="v1",
            leakage_class=LeakageClass.AT_CUTOFF,
        ),
    ),
)


def row_at(
    timestamp: datetime,
    *,
    segment_id: int = 0,
    symbol: str = "BTCUSDT",
    timeframe: str = "1m",
    value: float = 0.0,
) -> FeatureRow:
    duration = timedelta(minutes=1) if timeframe == "1m" else timedelta(hours=1)
    return FeatureRow(
        timestamp=timestamp,
        information_cutoff=timestamp + duration,
        symbol=symbol,
        timeframe=timeframe,
        segment_id=segment_id,
        dataset_version="DS-000001",
        config_version="config-v1",
        profile_version="profile-v1",
        window_policy_id="rolling-bars-v1:max-bars=60",
        feature_set_id="FS-000001",
        registry_id=REGISTRY.registry_id,
        values={"auction_location": "inside_value", "poc_distance": value},
    )


def minute_rows(count: int, *, start: datetime | None = None) -> list[FeatureRow]:
    origin = start or datetime(2025, 1, 1, tzinfo=UTC)
    return [row_at(origin + timedelta(minutes=index), value=float(index)) for index in range(count)]


def test_make_event_freezes_cutoff_vector_metadata_and_stable_identity() -> None:
    rows = minute_rows(2)
    metadata: dict[str, str | int] = {"window_bars": 2, "method": "fixed"}
    event = make_event(
        EventKind.FIXED_WINDOW,
        rows[0].timestamp,
        rows[-1].information_cutoff,
        rows[-1],
        "fixed-window-v1",
        registry=REGISTRY,
        metadata=metadata,
    )
    replay = make_event(
        EventKind.FIXED_WINDOW,
        rows[0].timestamp,
        rows[-1].information_cutoff,
        rows[-1],
        "fixed-window-v1",
        registry=REGISTRY,
        metadata={"method": "fixed", "window_bars": 2},
    )
    metadata["window_bars"] = 999

    assert event.event_id == replay.event_id
    assert event.event_id.startswith("EV-")
    assert len(event.event_id) == 67
    assert event.start == rows[0].timestamp
    assert event.end == rows[-1].information_cutoff
    assert event.information_cutoff == event.end
    assert event.feature_values == rows[-1].values
    assert event.metadata == {"method": "fixed", "window_bars": 2}
    assert isinstance(event.feature_values, MappingProxyType)
    assert isinstance(event.metadata, MappingProxyType)
    assert json.loads(event.canonical_json())["information_cutoff"].endswith("Z")
    with pytest.raises(TypeError):
        event.metadata["window_bars"] = 3  # type: ignore[index]
    with pytest.raises(FrozenInstanceError):
        event.end = rows[0].information_cutoff  # type: ignore[misc]


def test_event_id_changes_with_identity_metadata_and_rejects_forgery() -> None:
    current = minute_rows(1)[0]
    base = make_event(
        EventKind.CHANGE_POINT,
        current.timestamp,
        current.information_cutoff,
        current,
        "change-point-v1",
        registry=REGISTRY,
        metadata={"direction": "higher"},
    )
    changed = make_event(
        EventKind.CHANGE_POINT,
        current.timestamp,
        current.information_cutoff,
        current,
        "change-point-v2",
        registry=REGISTRY,
        metadata={"direction": "higher"},
    )
    changed_metadata = make_event(
        EventKind.CHANGE_POINT,
        current.timestamp,
        current.information_cutoff,
        current,
        "change-point-v1",
        registry=REGISTRY,
        metadata={"direction": "lower"},
    )

    assert len({base.event_id, changed.event_id, changed_metadata.event_id}) == 3
    with pytest.raises(ValueError, match="event_id does not match"):
        replace(base, trigger_version="forged")


def test_event_identity_includes_feature_and_auction_configuration() -> None:
    current = minute_rows(1)[0]
    base = make_event(
        EventKind.CHANGE_POINT,
        current.timestamp,
        current.information_cutoff,
        current,
        "change-point-v1",
        registry=REGISTRY,
    )
    changed = replace(
        current,
        config_version="config-v2",
        profile_version="profile-v2",
        window_policy_id="rolling-120-v2",
    )
    changed_event = make_event(
        EventKind.CHANGE_POINT,
        changed.timestamp,
        changed.information_cutoff,
        changed,
        "change-point-v1",
        registry=REGISTRY,
    )

    assert changed_event.event_id != base.event_id
    with pytest.raises(ValueError, match="event_id does not match"):
        replace(base, feature_values={**base.feature_values, "poc_distance": 99.0})


def test_make_event_rejects_unregistered_or_outcome_feature_vectors() -> None:
    current = minute_rows(1)[0]
    for forged in (
        replace(current, registry_id="FR-FORGED"),
        replace(current, values={"future_return_1": 0.5, "profit_label": "win"}),
    ):
        with pytest.raises(ValueError):
            make_event(
                EventKind.CHANGE_POINT,
                forged.timestamp,
                forged.information_cutoff,
                forged,
                "change-point-v1",
                registry=REGISTRY,
            )


def test_make_event_rejects_nonmatching_cutoff_and_outcome_metadata() -> None:
    current = minute_rows(1)[0]
    with pytest.raises(ValueError, match="information_cutoff"):
        make_event(
            EventKind.EXPANSION,
            current.timestamp,
            current.timestamp + timedelta(minutes=2),
            current,
            "expansion-v1",
            registry=REGISTRY,
        )
    for metadata, message in (
        ({"future_label": "up"}, "prohibited"),
        ({"future-return": 1.0}, "safe lower_snake_case"),
        ({"futureReturn": 1.0}, "safe lower_snake_case"),
    ):
        with pytest.raises(ValueError, match=message):
            make_event(
                EventKind.EXPANSION,
                current.timestamp,
                current.information_cutoff,
                current,
                "expansion-v1",
                registry=REGISTRY,
                metadata=metadata,
            )


def test_fixed_windows_are_full_non_overlapping_and_use_last_row_vector() -> None:
    rows = minute_rows(5)
    events = list(
        segment_fixed_windows(
            rows, width=2, trigger_version="fixed-window-v1", registry=REGISTRY
        )
    )

    assert len(events) == 2
    assert [(event.start, event.end) for event in events] == [
        (rows[0].timestamp, rows[1].information_cutoff),
        (rows[2].timestamp, rows[3].information_cutoff),
    ]
    assert all(event.kind is EventKind.FIXED_WINDOW for event in events)
    assert all(event.exploratory is False for event in events)
    assert events[0].feature_values == rows[1].values
    assert events[1].feature_values == rows[3].values


def test_fixed_windows_discard_partial_tail_at_each_segment_boundary() -> None:
    origin = datetime(2025, 1, 1, tzinfo=UTC)
    rows = [
        row_at(origin, segment_id=0),
        row_at(origin + timedelta(minutes=1), segment_id=0),
        row_at(origin + timedelta(minutes=5), segment_id=1),
        row_at(origin + timedelta(minutes=6), segment_id=1),
        row_at(origin + timedelta(minutes=7), segment_id=1),
    ]

    events = list(
        segment_fixed_windows(
            rows, width=3, trigger_version="fixed-window-v1", registry=REGISTRY
        )
    )

    assert len(events) == 1
    assert events[0].segment_id == 1
    assert events[0].start == rows[2].timestamp
    assert events[0].end == rows[4].information_cutoff


def test_rolling_windows_obey_width_step_and_are_exploratory() -> None:
    rows = minute_rows(7)
    events = list(
        segment_rolling_windows(
            rows,
            width=3,
            step=2,
            trigger_version="rolling-window-v1",
            registry=REGISTRY,
        )
    )

    assert [(event.start, event.end) for event in events] == [
        (rows[0].timestamp, rows[2].information_cutoff),
        (rows[2].timestamp, rows[4].information_cutoff),
        (rows[4].timestamp, rows[6].information_cutoff),
    ]
    assert all(event.kind is EventKind.ROLLING_WINDOW for event in events)
    assert all(event.exploratory is True for event in events)
    assert all(event.metadata == {"step_bars": 2, "window_bars": 3} for event in events)


@pytest.mark.parametrize(
    ("unit", "before", "after", "label"),
    [
        (
            UTCSessionUnit.DAY,
            datetime(2025, 1, 1, 23, 59, tzinfo=UTC),
            datetime(2025, 1, 2, tzinfo=UTC),
            ("2025-01-01", "2025-01-02"),
        ),
        (
            UTCSessionUnit.WEEK,
            datetime(2025, 1, 5, 23, 59, tzinfo=UTC),
            datetime(2025, 1, 6, tzinfo=UTC),
            ("2024-12-30", "2025-01-06"),
        ),
        (
            UTCSessionUnit.MONTH,
            datetime(2025, 1, 31, 23, 59, tzinfo=UTC),
            datetime(2025, 2, 1, tzinfo=UTC),
            ("2025-01", "2025-02"),
        ),
    ],
)
def test_utc_sessions_reset_at_exact_calendar_boundaries(
    unit: UTCSessionUnit,
    before: datetime,
    after: datetime,
    label: tuple[str, str],
) -> None:
    rows = [row_at(before), row_at(after)]
    events = list(
        segment_utc_sessions(
            rows, unit=unit, trigger_version="utc-session-v1", registry=REGISTRY
        )
    )

    assert len(events) == 2
    assert [event.metadata["session"] for event in events] == list(label)
    assert all(event.kind is EventKind.UTC_SESSION for event in events)
    assert [event.end for event in events] == [before + timedelta(minutes=1), after + timedelta(minutes=1)]


def test_utc_sessions_reset_at_segment_even_within_one_calendar_session() -> None:
    origin = datetime(2025, 1, 1, tzinfo=UTC)
    rows = [
        row_at(origin, segment_id=0),
        row_at(origin + timedelta(minutes=1), segment_id=0),
        row_at(origin + timedelta(minutes=5), segment_id=1),
    ]
    events = list(
        segment_utc_sessions(
            rows,
            unit=UTCSessionUnit.DAY,
            trigger_version="utc-session-v1",
            registry=REGISTRY,
        )
    )

    assert len(events) == 2
    assert [event.segment_id for event in events] == [0, 1]
    assert [event.metadata["observed_bars"] for event in events] == [2, 1]


@pytest.mark.parametrize(
    ("rows", "message"),
    [
        (
            [*minute_rows(2), replace(minute_rows(3)[2], symbol="ETHUSDT")],
            "identity changed",
        ),
        ([minute_rows(2)[1], minute_rows(2)[0]], "increasing timestamp"),
        (
            [minute_rows(1)[0], row_at(datetime(2025, 1, 1, 0, 2, tzinfo=UTC))],
            "unexplained gap",
        ),
        (
            [replace(minute_rows(1)[0], segment_id=1), replace(minute_rows(2)[1], segment_id=0)],
            "segment identifiers",
        ),
    ],
)
def test_segmenters_fail_closed_on_identity_order_gap_and_segment_errors(
    rows: list[FeatureRow], message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        list(
            segment_fixed_windows(
                rows, width=2, trigger_version="fixed-window-v1", registry=REGISTRY
            )
        )


@pytest.mark.parametrize(("width", "step"), [(0, 1), (1, 0), (True, 1)])
def test_segmenters_reject_invalid_width_or_step(width: int, step: int) -> None:
    with pytest.raises((TypeError, ValueError)):
        list(
            segment_rolling_windows(
                minute_rows(2),
                width=width,
                step=step,
                trigger_version="rolling-window-v1",
                registry=REGISTRY,
            )
        )
