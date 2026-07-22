"""Win32 handle primitives for reparse-safe immutable artifact publication."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
import ctypes
from ctypes import wintypes
import ntpath
import os
from pathlib import Path
from typing import Any, Final, Protocol, cast

GENERIC_READ: Final = 0x80000000
GENERIC_WRITE: Final = 0x40000000
FILE_SHARE_READ: Final = 0x00000001
CREATE_NEW: Final = 1
OPEN_EXISTING: Final = 3
FILE_ATTRIBUTE_DIRECTORY: Final = 0x00000010
FILE_ATTRIBUTE_REPARSE_POINT: Final = 0x00000400
FILE_FLAG_OPEN_REPARSE_POINT: Final = 0x00200000
FILE_FLAG_BACKUP_SEMANTICS: Final = 0x02000000
_FILE_ATTRIBUTE_TAG_INFO: Final = 9
_ERROR_ALREADY_EXISTS: Final = 183


class WindowsApi(Protocol):
    """Minimal injectable Win32 API surface used by the secure filesystem."""

    def open_path(
        self,
        path: str,
        *,
        access: int,
        share: int,
        creation: int,
        flags: int,
    ) -> int: ...

    def attributes(self, handle: int) -> int: ...

    def close(self, handle: int) -> None: ...

    def create_directory(self, path: str) -> None: ...

    def fd_from_handle(self, handle: int, flags: int) -> int: ...


class CtypesWindowsApi:
    """Actual Win32 implementation, loaded only on native Windows."""

    def __init__(self) -> None:
        if os.name != "nt":
            raise RuntimeError("Win32 secure filesystem is available only on Windows")
        kernel32 = cast(Any, getattr(ctypes, "WinDLL"))("kernel32", use_last_error=True)
        self._create_file = kernel32.CreateFileW
        self._create_file.argtypes = (
            wintypes.LPCWSTR,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.LPVOID,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.HANDLE,
        )
        self._create_file.restype = wintypes.HANDLE
        self._create_directory = kernel32.CreateDirectoryW
        self._create_directory.argtypes = (wintypes.LPCWSTR, wintypes.LPVOID)
        self._create_directory.restype = wintypes.BOOL
        self._get_information = kernel32.GetFileInformationByHandleEx
        self._get_information.argtypes = (
            wintypes.HANDLE,
            ctypes.c_int,
            wintypes.LPVOID,
            wintypes.DWORD,
        )
        self._get_information.restype = wintypes.BOOL
        self._close_handle = kernel32.CloseHandle
        self._close_handle.argtypes = (wintypes.HANDLE,)
        self._close_handle.restype = wintypes.BOOL

    def open_path(
        self,
        path: str,
        *,
        access: int,
        share: int,
        creation: int,
        flags: int,
    ) -> int:
        handle = self._create_file(path, access, share, None, creation, flags, None)
        invalid = ctypes.c_void_p(-1).value
        if handle == invalid:
            _raise_windows_error(path)
        return int(handle)

    def attributes(self, handle: int) -> int:
        information = _FileAttributeTagInfo()
        if not self._get_information(
            handle,
            _FILE_ATTRIBUTE_TAG_INFO,
            ctypes.byref(information),
            ctypes.sizeof(information),
        ):
            _raise_windows_error("open artifact handle")
        return int(information.file_attributes)

    def close(self, handle: int) -> None:
        if not self._close_handle(handle):
            _raise_windows_error("open artifact handle")

    def create_directory(self, path: str) -> None:
        if not self._create_directory(path, None):
            _raise_windows_error(path)

    def fd_from_handle(self, handle: int, flags: int) -> int:
        import msvcrt

        try:
            return cast(Any, getattr(msvcrt, "open_osfhandle"))(handle, flags)
        except Exception:
            self.close(handle)
            raise


class WindowsHandleFilesystem:
    """Pin path components and create entries without following reparse points."""

    def __init__(self, api: WindowsApi | None = None) -> None:
        self._api = api or CtypesWindowsApi()

    @contextmanager
    def pin_directory_chain(self, path: str | Path) -> Iterator[tuple[int, ...]]:
        handles: list[int] = []
        try:
            for component in _absolute_directory_chain(os.fspath(path)):
                handle = self._api.open_path(
                    component,
                    access=0,
                    share=FILE_SHARE_READ,
                    creation=OPEN_EXISTING,
                    flags=FILE_FLAG_OPEN_REPARSE_POINT | FILE_FLAG_BACKUP_SEMANTICS,
                )
                handles.append(handle)
                attributes = self._api.attributes(handle)
                if attributes & FILE_ATTRIBUTE_REPARSE_POINT:
                    raise RuntimeError("secure artifact path contains a reparse component")
                if not attributes & FILE_ATTRIBUTE_DIRECTORY:
                    raise RuntimeError("secure artifact directory component is not a directory")
            yield tuple(handles)
        finally:
            for handle in reversed(handles):
                self._api.close(handle)

    @contextmanager
    def create_regular_exclusive(
        self,
        trusted_root: str | Path,
        relative_path: str | Path,
    ) -> Iterator[int]:
        descriptor = self.create_regular_descriptor(trusted_root, relative_path)
        try:
            yield descriptor
        finally:
            os.close(descriptor)

    def create_regular_descriptor(
        self,
        trusted_root: str | Path,
        relative_path: str | Path,
    ) -> int:
        """Create one pinned, non-reparse regular file and return its owned descriptor."""

        destination = self._pinned_destination(trusted_root, relative_path)
        with self.pin_directory_chain(destination.parent):
            handle = self._api.open_path(
                os.fspath(destination),
                access=GENERIC_READ | GENERIC_WRITE,
                share=0,
                creation=CREATE_NEW,
                flags=FILE_FLAG_OPEN_REPARSE_POINT,
            )
            try:
                _require_regular_attributes(self._api.attributes(handle))
                descriptor = self._api.fd_from_handle(
                    handle,
                    os.O_RDWR | getattr(os, "O_BINARY", 0),
                )
                handle = -1
                return descriptor
            finally:
                if handle != -1:
                    self._api.close(handle)

    @contextmanager
    def open_regular_read(
        self,
        trusted_root: str | Path,
        relative_path: str | Path,
    ) -> Iterator[int]:
        descriptor = self.open_regular_descriptor(trusted_root, relative_path)
        try:
            yield descriptor
        finally:
            os.close(descriptor)

    def open_regular_descriptor(
        self,
        trusted_root: str | Path,
        relative_path: str | Path,
    ) -> int:
        """Open one pinned, non-reparse regular file and deny writes and deletion."""

        source = self._pinned_destination(trusted_root, relative_path)
        with self.pin_directory_chain(source.parent):
            handle = self._api.open_path(
                os.fspath(source),
                access=GENERIC_READ,
                share=FILE_SHARE_READ,
                creation=OPEN_EXISTING,
                flags=FILE_FLAG_OPEN_REPARSE_POINT,
            )
            try:
                _require_regular_attributes(self._api.attributes(handle))
                descriptor = self._api.fd_from_handle(
                    handle,
                    os.O_RDONLY | getattr(os, "O_BINARY", 0),
                )
                handle = -1
                return descriptor
            finally:
                if handle != -1:
                    self._api.close(handle)

    def _pinned_destination(
        self,
        trusted_root: str | Path,
        relative_path: str | Path,
    ) -> Path:
        relative_parts = validated_relative_parts(relative_path)
        return Path(trusted_root).joinpath(*relative_parts)

    @contextmanager
    def claim_exclusive_directory(
        self,
        trusted_root: str | Path,
        ancestor_components: tuple[str, ...],
        final_component: str,
    ) -> Iterator[WindowsDirectoryClaim]:
        """Pin safe ancestors and exclusively claim the final publication directory."""

        for component in (*ancestor_components, final_component):
            _require_safe_component(component)
        held: list[int] = []
        root = Path(trusted_root)
        with self.pin_directory_chain(root):
            current = root
            try:
                for component in ancestor_components:
                    current /= component
                    try:
                        self._api.create_directory(os.fspath(current))
                    except FileExistsError:
                        pass
                    held.append(self._open_pinned_directory(current))
                final = current / final_component
                self._api.create_directory(os.fspath(final))
                held.append(self._open_pinned_directory(final))
                yield WindowsDirectoryClaim(self._api, final, held)
            finally:
                for handle in reversed(held):
                    self._api.close(handle)

    def _open_pinned_directory(self, path: Path) -> int:
        handle = self._api.open_path(
            os.fspath(path),
            access=0,
            share=FILE_SHARE_READ,
            creation=OPEN_EXISTING,
            flags=FILE_FLAG_OPEN_REPARSE_POINT | FILE_FLAG_BACKUP_SEMANTICS,
        )
        try:
            attributes = self._api.attributes(handle)
            if attributes & FILE_ATTRIBUTE_REPARSE_POINT:
                raise RuntimeError("secure artifact path contains a reparse component")
            if not attributes & FILE_ATTRIBUTE_DIRECTORY:
                raise RuntimeError("secure artifact directory component is not a directory")
            return handle
        except Exception:
            self._api.close(handle)
            raise


class WindowsDirectoryClaim:
    """One pinned exclusive directory claim used for no-clobber file creation."""

    def __init__(self, api: WindowsApi, path: Path, held: list[int]) -> None:
        self._api = api
        self._path = path
        self._held = held
        self._directories: dict[tuple[str, ...], Path] = {(): path}

    def create_regular_descriptor(self, relative_path: str | Path) -> int:
        parts = validated_relative_parts(relative_path)
        current_parts: tuple[str, ...] = ()
        current = self._path
        for component in parts[:-1]:
            current_parts = (*current_parts, component)
            current /= component
            if current_parts not in self._directories:
                self._api.create_directory(os.fspath(current))
                handle = self._api.open_path(
                    os.fspath(current),
                    access=0,
                    share=FILE_SHARE_READ,
                    creation=OPEN_EXISTING,
                    flags=FILE_FLAG_OPEN_REPARSE_POINT | FILE_FLAG_BACKUP_SEMANTICS,
                )
                try:
                    attributes = self._api.attributes(handle)
                    if attributes & FILE_ATTRIBUTE_REPARSE_POINT or not (
                        attributes & FILE_ATTRIBUTE_DIRECTORY
                    ):
                        raise RuntimeError("secure publication child directory is invalid")
                except Exception:
                    self._api.close(handle)
                    raise
                self._held.append(handle)
                self._directories[current_parts] = current
        destination = current / parts[-1]
        handle = self._api.open_path(
            os.fspath(destination),
            access=GENERIC_READ | GENERIC_WRITE,
            share=0,
            creation=CREATE_NEW,
            flags=FILE_FLAG_OPEN_REPARSE_POINT,
        )
        try:
            _require_regular_attributes(self._api.attributes(handle))
            descriptor = self._api.fd_from_handle(
                handle,
                os.O_RDWR | getattr(os, "O_BINARY", 0),
            )
            handle = -1
            return descriptor
        finally:
            if handle != -1:
                self._api.close(handle)

    @property
    def created_directories(self) -> tuple[str, ...]:
        return tuple(
            sorted(
                Path(*parts).as_posix()
                for parts in self._directories
                if parts
            )
        )

    def unlink_regular(self, relative_path: str | Path) -> None:
        parts = validated_relative_parts(relative_path)
        destination = self._path.joinpath(*parts)
        os.unlink(destination)


def validated_relative_parts(path: str | Path) -> tuple[str, ...]:
    """Reject absolute, parent, empty, and alternate-stream relative paths."""

    value = os.fspath(path)
    drive, _ = ntpath.splitdrive(value)
    normalized = value.replace("\\", "/")
    parts = tuple(part for part in normalized.split("/") if part not in ("", "."))
    if drive or normalized.startswith("/") or not parts:
        raise ValueError("secure artifact path must be non-empty and relative")
    if any(part == ".." or ":" in part for part in parts):
        raise ValueError("secure artifact path contains a parent or alternate stream")
    return parts


def _absolute_directory_chain(path: str) -> tuple[str, ...]:
    raw_drive, raw_tail = ntpath.splitdrive(path)
    if not raw_drive or not raw_tail.startswith(("\\", "/")):
        raise ValueError("secure Windows directory path must be absolute")
    raw_parts = tuple(part for part in raw_tail.replace("/", "\\").split("\\") if part)
    if any(part in (".", "..") for part in raw_parts):
        raise ValueError("secure Windows directory path contains ambiguous components")
    anchor = f"{raw_drive}\\"
    chain = [anchor]
    current = anchor
    for part in raw_parts:
        current = ntpath.join(current, part)
        chain.append(current)
    return tuple(chain)


def _require_regular_attributes(attributes: int) -> None:
    if attributes & FILE_ATTRIBUTE_REPARSE_POINT:
        raise RuntimeError("secure artifact file is a reparse point")
    if attributes & FILE_ATTRIBUTE_DIRECTORY:
        raise RuntimeError("secure artifact file is a directory")


def _require_safe_component(component: str) -> None:
    if (
        not component
        or component in (".", "..")
        or any(character in component for character in ("/", "\\", ":"))
    ):
        raise ValueError("secure publication path component is invalid")


def _raise_windows_error(path: str) -> None:
    error = cast(Any, getattr(ctypes, "get_last_error"))()
    if error == _ERROR_ALREADY_EXISTS:
        raise FileExistsError(error, "secure artifact entry already exists", path)
    raise OSError(error, f"Win32 secure artifact operation failed: {path}")


class _FileAttributeTagInfo(ctypes.Structure):
    _fields_ = (
        ("file_attributes", wintypes.DWORD),
        ("reparse_tag", wintypes.DWORD),
    )


__all__ = [
    "CREATE_NEW",
    "FILE_FLAG_BACKUP_SEMANTICS",
    "FILE_FLAG_OPEN_REPARSE_POINT",
    "FILE_SHARE_READ",
    "OPEN_EXISTING",
    "WindowsDirectoryClaim",
    "WindowsHandleFilesystem",
    "validated_relative_parts",
]
