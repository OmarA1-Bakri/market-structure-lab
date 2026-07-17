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
        path.lstat()
    except FileNotFoundError:
        return False
    return True


def require_regular_directory(path: Path) -> None:
    """Require a real directory rather than a symlink or special entry."""

    try:
        metadata = path.lstat()
    except OSError as error:
        raise RuntimeError("artifact directory is missing or inaccessible") from error
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
        raise RuntimeError("artifact directory must be a regular directory, not a symlink")


def bounded_subdirectories(root: Path, *, maximum: int) -> tuple[Path, ...]:
    """List direct child directories with a bound applied before accumulation."""

    require_regular_directory(root)
    children: list[Path] = []
    count = 0
    try:
        with os.scandir(root) as entries:
            for entry in entries:
                count += 1
                if count > maximum:
                    raise RuntimeError("artifact directory scan exceeds the bounded limit")
                metadata = entry.stat(follow_symlinks=False)
                if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
                    raise RuntimeError(
                        "artifact directory contains a symlink or non-directory entry"
                    )
                children.append(Path(entry.path))
    except OSError as error:
        raise RuntimeError("artifact directory scan failed") from error
    return tuple(sorted(children, key=lambda item: item.name))


def bounded_regular_files(root: Path, *, maximum: int) -> tuple[str, ...]:
    """Walk a tree without following links and return bounded relative file names."""

    require_regular_directory(root)
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
                    if stat.S_ISLNK(metadata.st_mode):
                        raise RuntimeError("artifact tree contains a symlink entry")
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
    metadata = path.lstat()
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        raise RuntimeError("artifact entry must be a regular file, not a symlink")
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    try:
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode):
            raise RuntimeError("artifact entry must remain a regular file")
        return os.fdopen(descriptor, "rb")
    except Exception:
        os.close(descriptor)
        raise


__all__ = [
    "bounded_regular_files",
    "bounded_subdirectories",
    "path_exists_no_follow",
    "read_bounded_regular",
    "regular_file_matches",
    "require_regular_directory",
    "sha256_regular",
]
