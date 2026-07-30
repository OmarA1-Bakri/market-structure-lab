from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from market_structure_lab.cli.freeze_validation_boundary_v2 import (
    BoundaryFreezeAdaptersV2,
    freeze_boundary_publications_v2,
    main,
)
from market_structure_lab.core.identity import hash_json
from market_structure_lab.research.validation_v2_models import (
    SourceCoveragePublicationV2,
)
from market_structure_lab.research.validation_v2_splits import (
    DevelopmentReadBoundaryV2,
    DevelopmentSplitPublicationV2,
)


class ExplodingAdapter:
    def __call__(self, *_args: object, **_kwargs: object):
        raise AssertionError("row/process/network adapter was called")


def _metadata() -> dict[str, object]:
    payload: dict[str, object] = {
        "schema_version": "phase5-validation-boundary-freeze-input-v2",
        "scope": "development_metadata_only",
        "component": "development",
        "final_holdout_access_count": 0,
        "raw_dump": {
            "dump_sha256": "a" * 64,
            "byte_count": 123,
            "pg_restore_list_sha256": "b" * 64,
            "candle_table_toc_identity": "public.candles:table-data:42",
            "source_mapping_version": "callscore-candles-v1",
        },
        "reconciliation": {
            "promotion_receipt_sha256": "c" * 64,
            "work_unit_manifest_sha256": ["d" * 64],
            "comparison_part_sha256": ["e" * 64],
            "replacement_source_policy": "rr-000008-promoted-only",
        },
        "compatibility": {
            "metadata_sha256": "f" * 64,
            "entries": [
                {
                    "symbol": symbol,
                    "complete_start": "2024-01-01T00:00:00Z",
                    "complete_end": "2025-01-01T00:00:00Z",
                    "timeframes": ["1m", "1h", "4h"],
                    "source_conflict": False,
                    "mapping_compatible": True,
                    "metadata_sha256": str(index) * 64,
                }
                for index, symbol in enumerate(
                    ("ADAUSDT", "BNBUSDT", "DOGEUSDT", "SOLUSDT", "XRPUSDT"),
                    start=1,
                )
            ],
        },
        "split_policy": {
            "block_count": 6,
            "minimum_complete_days": 180,
            "asset_holdout_fraction_numerator": 1,
            "asset_holdout_fraction_denominator": 5,
            "asset_holdout_salt": "market-structure-lab-phase5-v2-public-salt",
            "purge_hours": 24,
            "embargo_hours": 24,
            "timeframes": ["1m", "1h", "4h"],
        },
    }
    payload["metadata_sha256"] = hash_json(
        "phase5-validation-boundary-freeze-input-v2",
        payload,
    )
    return payload


def _write_metadata(path: Path, payload: dict[str, object] | None = None) -> None:
    path.write_text(
        json.dumps(payload or _metadata(), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _tree_bytes(root: Path) -> dict[str, bytes]:
    return {path.name: path.read_bytes() for path in sorted(root.iterdir())}


def test_freezer_uses_metadata_only_and_emits_verified_publications(tmp_path: Path) -> None:
    metadata = tmp_path / "metadata.json"
    output = tmp_path / "published"
    _write_metadata(metadata)
    exploding = ExplodingAdapter()

    result = freeze_boundary_publications_v2(
        metadata_path=metadata,
        output_dir=output,
        adapters=BoundaryFreezeAdaptersV2(
            row_reader=exploding,
            process_runner=exploding,
            network_reader=exploding,
        ),
    )

    assert result.coverage_path == output / "source-coverage-v2.json"
    assert result.split_path == output / "development-split-v2.json"
    assert result.boundary_path == output / "development-read-boundary-v2.json"
    coverage = SourceCoveragePublicationV2.from_dict(
        json.loads(result.coverage_path.read_text(encoding="utf-8"))
    )
    split = DevelopmentSplitPublicationV2.from_dict(
        json.loads(result.split_path.read_text(encoding="utf-8")),
        coverage,
    )
    boundary = DevelopmentReadBoundaryV2.from_publication_dict(
        json.loads(result.boundary_path.read_text(encoding="utf-8")),
        coverage,
        split,
    )
    assert boundary.coverage_identity == coverage.coverage_identity
    assert boundary.split_identity == split.split_identity


def test_freezer_is_byte_deterministic_and_no_clobber(tmp_path: Path) -> None:
    metadata = tmp_path / "metadata.json"
    _write_metadata(metadata)
    first = tmp_path / "first"
    second = tmp_path / "second"

    freeze_boundary_publications_v2(metadata_path=metadata, output_dir=first)
    freeze_boundary_publications_v2(metadata_path=metadata, output_dir=second)

    assert _tree_bytes(first) == _tree_bytes(second)
    assert {
        name: hashlib.sha256(data).hexdigest() for name, data in _tree_bytes(first).items()
    } == {
        name: hashlib.sha256(data).hexdigest() for name, data in _tree_bytes(second).items()
    }
    with pytest.raises(FileExistsError):
        freeze_boundary_publications_v2(metadata_path=metadata, output_dir=first)
    assert _tree_bytes(first) == _tree_bytes(second)


@pytest.mark.parametrize(
    ("field", "value", "match"),
    (
        ("schema_version", "phase5-validation-source-preflight-v1", "schema"),
        ("scope", "whole_source", "scope"),
        ("component", "final", "component"),
        ("final_holdout_access_count", 1, "final"),
    ),
)
def test_freezer_rejects_unexpected_metadata_identity_schema_or_scope(
    tmp_path: Path,
    field: str,
    value: object,
    match: str,
) -> None:
    payload = _metadata()
    payload[field] = value
    payload["metadata_sha256"] = hash_json(
        "phase5-validation-boundary-freeze-input-v2",
        {key: item for key, item in payload.items() if key != "metadata_sha256"},
    )
    metadata = tmp_path / "metadata.json"
    _write_metadata(metadata, payload)

    with pytest.raises(ValueError, match=match):
        freeze_boundary_publications_v2(
            metadata_path=metadata,
            output_dir=tmp_path / "output",
        )
    assert not (tmp_path / "output").exists()


def test_freezer_rejects_metadata_digest_mismatch(tmp_path: Path) -> None:
    payload = _metadata()
    payload["metadata_sha256"] = "0" * 64
    metadata = tmp_path / "metadata.json"
    _write_metadata(metadata, payload)

    with pytest.raises(ValueError, match="metadata_sha256"):
        freeze_boundary_publications_v2(metadata_path=metadata, output_dir=tmp_path / "out")


def test_cli_main_publishes_without_registered_entrypoint(tmp_path: Path) -> None:
    metadata = tmp_path / "metadata.json"
    output = tmp_path / "published"
    _write_metadata(metadata)

    assert main(["--metadata", str(metadata), "--output-dir", str(output)]) == 0
    assert (output / "development-read-boundary-v2.json").is_file()
