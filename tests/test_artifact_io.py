"""Cross-platform tests for bounded immutable-artifact reads."""

from __future__ import annotations

from contextlib import contextmanager
import hashlib
import io
from pathlib import Path

import pytest

from market_structure_lab.core import artifact_io


class _AllocationGuard(io.BytesIO):
    """Reject a single read larger than the repository's streaming chunk."""

    def read(self, size: int = -1) -> bytes:
        if size > 1024 * 1024:
            raise MemoryError("single bounded read exceeded the streaming allocation cap")
        return super().read(size)


def test_read_bounded_regular_streams_without_one_maximum_sized_allocation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = b"x" * (1024 * 1024 + 7)

    @contextmanager
    def guarded_open(_path: Path):
        with _AllocationGuard(payload) as handle:
            yield handle

    monkeypatch.setattr(artifact_io, "_open_regular", guarded_open)

    assert artifact_io.read_bounded_regular(Path("artifact.json"), len(payload)) == payload


def test_read_bounded_regular_rejects_a_file_larger_than_the_limit(tmp_path: Path) -> None:
    artifact = tmp_path / "artifact.json"
    artifact.write_bytes(b"12345")

    with pytest.raises(RuntimeError, match="exceeds the bounded size limit"):
        artifact_io.read_bounded_regular(artifact, 4)


def test_read_bounded_regular_accepts_an_empty_file_at_zero_limit(tmp_path: Path) -> None:
    artifact = tmp_path / "artifact.json"
    artifact.write_bytes(b"")

    assert artifact_io.read_bounded_regular(artifact, 0) == b""


def test_read_bounded_regular_rejects_a_negative_limit(tmp_path: Path) -> None:
    artifact = tmp_path / "artifact.json"
    artifact.write_bytes(b"")

    with pytest.raises(RuntimeError, match="exceeds the bounded size limit"):
        artifact_io.read_bounded_regular(artifact, -1)


def test_iter_verified_regular_lines_yields_only_from_fully_verified_snapshot(
    tmp_path: Path,
) -> None:
    artifact = tmp_path / "rows.jsonl"
    payload = b'{"row":1}\n{"row":2}\n'
    artifact.write_bytes(payload)

    assert tuple(
        artifact_io.iter_verified_regular_lines(
            artifact,
            expected_sha256=hashlib.sha256(payload).hexdigest(),
            expected_byte_count=len(payload),
            expected_line_count=2,
            maximum_line_bytes=32,
        )
    ) == (b'{"row":1}', b'{"row":2}')

    observed: list[bytes] = []
    with pytest.raises(RuntimeError, match="checksum"):
        for line in artifact_io.iter_verified_regular_lines(
            artifact,
            expected_sha256="0" * 64,
            expected_byte_count=len(payload),
            expected_line_count=2,
            maximum_line_bytes=32,
        ):
            observed.append(line)
    assert observed == []
