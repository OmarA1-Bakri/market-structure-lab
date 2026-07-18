"""Bounded, no-follow filesystem primitives for immutable artifact verification."""

from __future__ import annotations

import hashlib
import os
import stat
from pathlib import Path
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

    with _open_regular(path) as handle:
        payload = handle.read(maximum + 1)
    if len(payload) > maximum:
        raise RuntimeError("artifact file exceeds the bounded size limit")
    return payload


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
        raise RuntimeError(
            "artifact entry must be a regular file, not a symlink or reparse point"
        )
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
        raise RuntimeError(
            "artifact path contains a symlink or reparse ancestor or entry"
        )
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
            raise RuntimeError(
                "artifact path contains a symlink or reparse ancestor"
            )


def _is_link_or_reparse(metadata: os.stat_result) -> bool:
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    file_attributes = getattr(metadata, "st_file_attributes", 0)
    return stat.S_ISLNK(metadata.st_mode) or bool(file_attributes & reparse_flag)


__all__ = [
    "bounded_regular_files",
    "bounded_subdirectories",
    "path_exists_no_follow",
    "read_bounded_regular",
    "regular_file_matches",
    "require_regular_directory",
    "sha256_regular",
]
