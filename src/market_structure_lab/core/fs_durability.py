"""Cross-platform durable, no-replace filesystem publication primitives."""

from __future__ import annotations

import ctypes
import errno
import os
from pathlib import Path
import stat

from market_structure_lab.core.secure_windows import WindowsHandleFilesystem

_AT_FDCWD = -100
_RENAME_NOREPLACE = 1


def fsync_directory_posix(path: Path) -> None:
    """Durably commit one POSIX directory without following its final component."""

    if _is_windows_platform():
        raise RuntimeError("POSIX directory fsync is unavailable on Windows")
    no_follow = _required_posix_flag("O_NOFOLLOW")
    directory = _required_posix_flag("O_DIRECTORY")
    if not no_follow or not directory:
        raise RuntimeError("durable publication requires no-follow directory handles")
    descriptor = os.open(Path(path), os.O_RDONLY | no_follow | directory)
    try:
        if not stat.S_ISDIR(os.fstat(descriptor).st_mode):
            raise RuntimeError("durability target must remain a directory")
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def durable_move_no_replace(source: Path, destination: Path) -> None:
    """Move a staged file or directory without replacement and commit its metadata."""

    source = Path(source)
    destination = Path(destination)
    if _is_windows_platform():
        WindowsHandleFilesystem().move_no_replace_write_through(source, destination)
        return
    _rename_no_replace_posix(source, destination)
    fsync_directory_posix(destination.parent)


def _rename_no_replace_posix(source: Path, destination: Path) -> None:
    libc = ctypes.CDLL(None, use_errno=True)
    renameat2 = getattr(libc, "renameat2", None)
    if renameat2 is None:
        raise RuntimeError("atomic no-replace publication is unsupported")
    renameat2.argtypes = [
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_uint,
    ]
    renameat2.restype = ctypes.c_int
    result = renameat2(
        _AT_FDCWD,
        os.fsencode(source),
        _AT_FDCWD,
        os.fsencode(destination),
        _RENAME_NOREPLACE,
    )
    if result == 0:
        return
    error_number = ctypes.get_errno()
    if error_number == errno.EEXIST:
        raise FileExistsError(error_number, "refusing existing publication", destination)
    raise OSError(error_number, os.strerror(error_number), destination)


def _required_posix_flag(name: str) -> int:
    return int(getattr(os, name, 0))


def _is_windows_platform() -> bool:
    return os.name == "nt"


__all__ = ["durable_move_no_replace", "fsync_directory_posix"]
