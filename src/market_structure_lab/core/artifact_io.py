"""Bounded, no-follow filesystem primitives for immutable artifact verification."""

from __future__ import annotations

from collections.abc import Iterator
import hashlib
import os
from pathlib import Path
import stat
import tempfile
from typing import BinaryIO

_CHUNK_SIZE = 1024 * 1024


def path_exists_no_follow(path: Path) -> bool:
    """Return whether a directory entry exists without following a link."""

    try:
        absolute = _unambiguous_absolute_path(path)
        _require_no_link_ancestors(absolute)
        absolute.lstat()
    except FileNotFoundError:
        return False
    return True


def require_regular_directory(path: Path) -> None:
    """Require a real directory rather than a symlink or special entry."""

    _validated_regular_directory(path)


def _validated_regular_directory(path: Path) -> Path:
    try:
        validated, metadata = _validated_no_link_path(path)
    except OSError as error:
        raise RuntimeError("artifact directory is missing or inaccessible") from error
    if not stat.S_ISDIR(metadata.st_mode):
        raise RuntimeError(
            "artifact directory must be a regular directory, not a symlink or reparse point"
        )
    return validated


def bounded_subdirectories(root: Path, *, maximum: int) -> tuple[Path, ...]:
    """List direct child directories with a bound applied before accumulation."""

    root = _validated_regular_directory(root)
    children: list[Path] = []
    count = 0
    try:
        with os.scandir(root) as entries:
            for entry in entries:
                count += 1
                if count > maximum:
                    raise RuntimeError("artifact directory scan exceeds the bounded limit")
                metadata = entry.stat(follow_symlinks=False)
                if _is_link_or_reparse(metadata) or not stat.S_ISDIR(metadata.st_mode):
                    raise RuntimeError(
                        "artifact directory contains a symlink, reparse point, or non-directory entry"
                    )
                children.append(Path(entry.path))
    except OSError as error:
        raise RuntimeError("artifact directory scan failed") from error
    return tuple(sorted(children, key=lambda item: item.name))


def bounded_regular_files(root: Path, *, maximum: int) -> tuple[str, ...]:
    """Walk a tree without following links and return bounded relative file names."""

    root = _validated_regular_directory(root)
    files: list[str] = []
    stack: list[tuple[Path, tuple[str, ...]]] = [(root, ())]
    count = 0
    while stack:
        directory, prefix = stack.pop()
        try:
            with os.scandir(directory) as entries:
                for entry in entries:
                    count += 1
                    if count > maximum:
                        raise RuntimeError("artifact tree scan exceeds the bounded limit")
                    metadata = entry.stat(follow_symlinks=False)
                    relative = (*prefix, entry.name)
                    if _is_link_or_reparse(metadata):
                        raise RuntimeError("artifact tree contains a symlink or reparse entry")
                    if stat.S_ISDIR(metadata.st_mode):
                        stack.append((Path(entry.path), relative))
                    elif stat.S_ISREG(metadata.st_mode):
                        files.append(Path(*relative).as_posix())
                    else:
                        raise RuntimeError("artifact tree contains a non-regular entry")
        except OSError as error:
            raise RuntimeError("artifact tree scan failed") from error
    return tuple(sorted(files))


def read_bounded_regular(path: Path, maximum: int) -> bytes:
    """Read at most ``maximum`` bytes from a non-symlink regular file."""

    if maximum < 0:
        raise RuntimeError("artifact file exceeds the bounded size limit")
    chunks: list[bytes] = []
    total = 0
    with _open_regular(path) as handle:
        while chunk := handle.read(min(_CHUNK_SIZE, maximum - total + 1)):
            total += len(chunk)
            if total > maximum:
                raise RuntimeError("artifact file exceeds the bounded size limit")
            chunks.append(chunk)
    return b"".join(chunks)


def iter_bounded_regular_lines(
    path: Path,
    *,
    maximum_lines: int,
    maximum_line_bytes: int,
) -> Iterator[bytes]:
    """Yield newline-terminated regular-file records without unbounded reads."""

    if maximum_lines < 0 or maximum_line_bytes < 1:
        raise ValueError("line and byte limits must be positive bounded values")
    with _open_regular(path) as handle:
        count = 0
        while line := handle.readline(maximum_line_bytes + 2):
            count += 1
            if count > maximum_lines:
                raise RuntimeError("artifact file exceeds the bounded line limit")
            if len(line) > maximum_line_bytes + 1 or not line.endswith(b"\n"):
                raise RuntimeError("artifact record exceeds its bound or lacks a newline")
            yield line[:-1]


def iter_verified_regular_lines(
    path: Path,
    *,
    expected_sha256: str,
    expected_byte_count: int,
    expected_line_count: int,
    maximum_line_bytes: int,
) -> Iterator[bytes]:
    """Yield records only after one opened file is copied and fully verified."""

    if expected_byte_count < 0 or expected_line_count < 0 or maximum_line_bytes < 1:
        raise ValueError("expected byte, line, and record bounds must be non-negative")
    digest = hashlib.sha256()
    byte_count = 0
    line_count = 0
    with tempfile.SpooledTemporaryFile(max_size=_CHUNK_SIZE, mode="w+b") as snapshot:
        with _open_regular(path) as handle:
            while line := handle.readline(maximum_line_bytes + 2):
                line_count += 1
                byte_count += len(line)
                if line_count > expected_line_count:
                    raise RuntimeError("artifact file bytes or line count exceed expected bounds")
                if byte_count > expected_byte_count:
                    raise RuntimeError("artifact file bytes or line count exceed expected bounds")
                if len(line) > maximum_line_bytes + 1 or not line.endswith(b"\n"):
                    raise RuntimeError("artifact record exceeds its bound or lacks a newline")
                digest.update(line)
                snapshot.write(line)
        if byte_count != expected_byte_count:
            raise RuntimeError("artifact file differs from the expected byte count")
        if line_count != expected_line_count:
            raise RuntimeError("artifact file bytes or line count differ from expected bounds")
        if digest.hexdigest() != expected_sha256:
            raise RuntimeError("artifact file checksum differs from the expected checksum")
        snapshot.seek(0)
        for _ in range(line_count):
            line = snapshot.readline(maximum_line_bytes + 2)
            if len(line) > maximum_line_bytes + 1 or not line.endswith(b"\n"):
                raise RuntimeError("verified artifact snapshot is internally inconsistent")
            yield line[:-1]
        if snapshot.read(1):
            raise RuntimeError("verified artifact snapshot contains unexpected trailing bytes")


def sha256_regular(path: Path) -> str:
    """Stream a SHA-256 digest from a non-symlink regular file."""

    digest = hashlib.sha256()
    with _open_regular(path) as handle:
        while chunk := handle.read(_CHUNK_SIZE):
            digest.update(chunk)
    return digest.hexdigest()


def regular_file_matches(path: Path, expected: bytes) -> bool:
    """Compare a regular file to expected bytes without materialising the file."""

    offset = 0
    with _open_regular(path) as handle:
        while chunk := handle.read(_CHUNK_SIZE):
            if chunk != expected[offset : offset + len(chunk)]:
                return False
            offset += len(chunk)
    return offset == len(expected)


def _open_regular(path: Path) -> BinaryIO:
    validated, metadata = _validated_no_link_path(path)
    if not stat.S_ISREG(metadata.st_mode):
        raise RuntimeError("artifact entry must be a regular file, not a symlink or reparse point")
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(validated, flags)
    try:
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode):
            raise RuntimeError("artifact entry must remain a regular file")
        return os.fdopen(descriptor, "rb")
    except Exception:
        os.close(descriptor)
        raise


def _validated_no_link_path(path: Path) -> tuple[Path, os.stat_result]:
    """Return one unambiguous absolute path after checking every component."""

    absolute = _unambiguous_absolute_path(path)
    _require_no_link_ancestors(absolute)
    metadata = absolute.lstat()
    if _is_link_or_reparse(metadata):
        raise RuntimeError("artifact path contains a symlink or reparse ancestor or entry")
    return absolute, metadata


def _unambiguous_absolute_path(path: Path) -> Path:
    if ".." in path.parts:
        raise RuntimeError("artifact path cannot contain parent traversal components")
    if path.anchor and not path.is_absolute():
        raise RuntimeError("artifact path cannot use ambiguous drive-relative traversal")
    return path if path.is_absolute() else Path.cwd() / path


def _require_no_link_ancestors(absolute: Path) -> None:
    for component in reversed(absolute.parents):
        metadata = component.lstat()
        if _is_link_or_reparse(metadata):
            raise RuntimeError("artifact path contains a symlink or reparse ancestor")


def _is_link_or_reparse(metadata: os.stat_result) -> bool:
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    file_attributes = getattr(metadata, "st_file_attributes", 0)
    return stat.S_ISLNK(metadata.st_mode) or bool(file_attributes & reparse_flag)


__all__ = [
    "bounded_regular_files",
    "bounded_subdirectories",
    "iter_bounded_regular_lines",
    "iter_verified_regular_lines",
    "path_exists_no_follow",
    "read_bounded_regular",
    "regular_file_matches",
    "require_regular_directory",
    "sha256_regular",
]
