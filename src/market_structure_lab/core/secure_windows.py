"""Win32 handle primitives for reparse-safe immutable artifact publication."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
import ctypes
from ctypes import wintypes
import hashlib
import ntpath
import os
from pathlib import Path
import stat
from typing import Any, Final, Protocol, cast

GENERIC_READ: Final = 0x80000000
GENERIC_WRITE: Final = 0x40000000
FILE_SHARE_READ: Final = 0x00000001
FILE_SHARE_WRITE: Final = 0x00000002
FILE_SHARE_DELETE: Final = 0x00000004
DELETE: Final = 0x00010000
CREATE_NEW: Final = 1
OPEN_EXISTING: Final = 3
FILE_ATTRIBUTE_DIRECTORY: Final = 0x00000010
FILE_ATTRIBUTE_REPARSE_POINT: Final = 0x00000400
FILE_FLAG_OPEN_REPARSE_POINT: Final = 0x00200000
FILE_FLAG_BACKUP_SEMANTICS: Final = 0x02000000
FILE_FLAG_WRITE_THROUGH: Final = 0x80000000
MOVEFILE_REPLACE_EXISTING: Final = 0x00000001
MOVEFILE_WRITE_THROUGH: Final = 0x00000008
_FILE_ATTRIBUTE_TAG_INFO: Final = 9
_FILE_RENAME_INFORMATION_EX: Final = 65
_FILE_DISPOSITION_INFO_EX: Final = 21
_FILE_DISPOSITION_FLAG_DELETE: Final = 0x00000001
_FILE_DISPOSITION_FLAG_POSIX_SEMANTICS: Final = 0x00000002
_FILE_DISPOSITION_FLAG_IGNORE_READONLY_ATTRIBUTE: Final = 0x00000010
_FILE_RENAME_FLAG_POSIX_SEMANTICS: Final = 0x00000002
_ERROR_FILE_EXISTS: Final = 80
_ERROR_ALREADY_EXISTS: Final = 183
_FILE_CREATE: Final = 2
_FILE_DIRECTORY_FILE: Final = 0x00000001
_FILE_SYNCHRONOUS_IO_NONALERT: Final = 0x00000020
_OBJ_CASE_INSENSITIVE: Final = 0x00000040
_DIRECTORY_CLAIM_ACCESS: Final = 0x00110081


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

    def identity(self, handle: int) -> tuple[int, int]: ...

    def close(self, handle: int) -> None: ...

    def create_directory(self, path: str) -> None: ...

    def create_directory_relative(self, parent_handle: int, name: str) -> int: ...

    def fd_from_handle(self, handle: int, flags: int) -> int: ...

    def move_path(self, source: str, destination: str, flags: int) -> None: ...

    def rename_handle(
        self,
        handle: int,
        destination_directory: int,
        destination_name: str,
    ) -> None: ...

    def delete_handle(self, handle: int) -> None: ...

    def final_path(self, handle: int) -> str: ...

    def duplicate(self, handle: int) -> int: ...

    def flush(self, handle: int) -> None: ...


class CtypesWindowsApi:
    """Actual Win32 implementation, loaded only on native Windows."""

    _create_file: Any
    _create_directory: Any
    _get_information: Any
    _get_file_information: Any
    _close_handle: Any
    _move_file: Any
    _set_information: Any
    _get_final_path: Any
    _duplicate_handle: Any
    _current_process: Any
    _flush_file: Any
    _nt_set_information: Any
    _rtl_ntstatus_to_dos_error: Any
    _nt_create_file: Any

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
        self._get_file_information = kernel32.GetFileInformationByHandle
        self._get_file_information.argtypes = (
            wintypes.HANDLE,
            ctypes.POINTER(_ByHandleFileInformation),
        )
        self._get_file_information.restype = wintypes.BOOL
        self._close_handle = kernel32.CloseHandle
        self._close_handle.argtypes = (wintypes.HANDLE,)
        self._close_handle.restype = wintypes.BOOL
        self._move_file = kernel32.MoveFileExW
        self._move_file.argtypes = (wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD)
        self._move_file.restype = wintypes.BOOL
        self._set_information = kernel32.SetFileInformationByHandle
        self._set_information.argtypes = (
            wintypes.HANDLE,
            ctypes.c_int,
            wintypes.LPVOID,
            wintypes.DWORD,
        )
        self._set_information.restype = wintypes.BOOL
        self._get_final_path = kernel32.GetFinalPathNameByHandleW
        self._get_final_path.argtypes = (
            wintypes.HANDLE,
            wintypes.LPWSTR,
            wintypes.DWORD,
            wintypes.DWORD,
        )
        self._get_final_path.restype = wintypes.DWORD
        self._duplicate_handle = kernel32.DuplicateHandle
        self._duplicate_handle.argtypes = (
            wintypes.HANDLE,
            wintypes.HANDLE,
            wintypes.HANDLE,
            ctypes.POINTER(wintypes.HANDLE),
            wintypes.DWORD,
            wintypes.BOOL,
            wintypes.DWORD,
        )
        self._duplicate_handle.restype = wintypes.BOOL
        self._current_process = kernel32.GetCurrentProcess
        self._current_process.argtypes = ()
        self._current_process.restype = wintypes.HANDLE
        self._flush_file = kernel32.FlushFileBuffers
        self._flush_file.argtypes = (wintypes.HANDLE,)
        self._flush_file.restype = wintypes.BOOL
        ntdll = cast(Any, getattr(ctypes, "WinDLL"))("ntdll", use_last_error=True)
        self._nt_set_information = ntdll.NtSetInformationFile
        self._nt_set_information.argtypes = (
            wintypes.HANDLE,
            ctypes.POINTER(_IoStatusBlock),
            wintypes.LPVOID,
            wintypes.ULONG,
            ctypes.c_int,
        )
        self._nt_set_information.restype = wintypes.LONG
        self._rtl_ntstatus_to_dos_error = ntdll.RtlNtStatusToDosError
        self._rtl_ntstatus_to_dos_error.argtypes = (wintypes.LONG,)
        self._rtl_ntstatus_to_dos_error.restype = wintypes.ULONG
        self._nt_create_file = ntdll.NtCreateFile
        self._nt_create_file.argtypes = (
            ctypes.POINTER(wintypes.HANDLE),
            wintypes.DWORD,
            ctypes.POINTER(_ObjectAttributes),
            ctypes.POINTER(_IoStatusBlock),
            wintypes.LPVOID,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.LPVOID,
            wintypes.DWORD,
        )
        self._nt_create_file.restype = wintypes.LONG

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

    def identity(self, handle: int) -> tuple[int, int]:
        information = _ByHandleFileInformation()
        if not self._get_file_information(handle, ctypes.byref(information)):
            _raise_windows_error("owned publication handle")
        file_index = (int(information.file_index_high) << 32) | int(information.file_index_low)
        return int(information.volume_serial_number), file_index

    def close(self, handle: int) -> None:
        if not self._close_handle(handle):
            _raise_windows_error("open artifact handle")

    def create_directory(self, path: str) -> None:
        if not self._create_directory(path, None):
            _raise_windows_error(path)

    def create_directory_relative(self, parent_handle: int, name: str) -> int:
        encoded = name.encode("utf-16-le")
        name_buffer = ctypes.create_unicode_buffer(name)
        unicode_name = _UnicodeString(
            len(encoded),
            len(encoded) + ctypes.sizeof(wintypes.WCHAR),
            ctypes.cast(name_buffer, wintypes.LPWSTR),
        )
        attributes = _ObjectAttributes(
            ctypes.sizeof(_ObjectAttributes),
            parent_handle,
            ctypes.pointer(unicode_name),
            _OBJ_CASE_INSENSITIVE,
            None,
            None,
        )
        handle = wintypes.HANDLE()
        io_status = _IoStatusBlock()
        status = int(
            self._nt_create_file(
                ctypes.byref(handle),
                _DIRECTORY_CLAIM_ACCESS,
                ctypes.byref(attributes),
                ctypes.byref(io_status),
                None,
                0,
                FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE,
                _FILE_CREATE,
                (_FILE_DIRECTORY_FILE | _FILE_SYNCHRONOUS_IO_NONALERT),
                None,
                0,
            )
        )
        if status < 0:
            error = int(self._rtl_ntstatus_to_dos_error(status))
            _raise_windows_error_code(error, name)
        if handle.value is None:
            raise RuntimeError("Win32 created an invalid directory handle")
        return int(handle.value)

    def fd_from_handle(self, handle: int, flags: int) -> int:
        import msvcrt

        try:
            return cast(Any, getattr(msvcrt, "open_osfhandle"))(handle, flags)
        except Exception:
            self.close(handle)
            raise

    def move_path(self, source: str, destination: str, flags: int) -> None:
        if not self._move_file(source, destination, flags):
            _raise_windows_error(destination)

    def rename_handle(
        self,
        handle: int,
        destination_directory: int,
        destination_name: str,
    ) -> None:
        encoded = destination_name.encode("utf-16-le")
        name_offset = _FileRenameInformationEx.file_name.offset
        size = ctypes.sizeof(_FileRenameInformationEx) + len(encoded)
        buffer = ctypes.create_string_buffer(size)
        information = cast(
            _FileRenameInformationEx,
            _FileRenameInformationEx.from_buffer(buffer),
        )
        information.flags = _FILE_RENAME_FLAG_POSIX_SEMANTICS
        information.root_directory = destination_directory
        information.file_name_length = len(encoded)
        ctypes.memmove(ctypes.addressof(buffer) + name_offset, encoded, len(encoded))
        io_status = _IoStatusBlock()
        status = int(
            self._nt_set_information(
                handle,
                ctypes.byref(io_status),
                buffer,
                size,
                _FILE_RENAME_INFORMATION_EX,
            )
        )
        if status < 0:
            error = int(self._rtl_ntstatus_to_dos_error(status))
            _raise_windows_error_code(error, destination_name)

    def delete_handle(self, handle: int) -> None:
        information = _FileDispositionInfoEx(
            _FILE_DISPOSITION_FLAG_DELETE
            | _FILE_DISPOSITION_FLAG_POSIX_SEMANTICS
            | _FILE_DISPOSITION_FLAG_IGNORE_READONLY_ATTRIBUTE
        )
        if not self._set_information(
            handle,
            _FILE_DISPOSITION_INFO_EX,
            ctypes.byref(information),
            ctypes.sizeof(information),
        ):
            _raise_windows_error("owned publication handle")

    def final_path(self, handle: int) -> str:
        required = self._get_final_path(handle, None, 0, 0)
        if not required:
            _raise_windows_error("owned publication handle")
        buffer = ctypes.create_unicode_buffer(required + 1)
        written = self._get_final_path(handle, buffer, len(buffer), 0)
        if not written or written >= len(buffer):
            _raise_windows_error("owned publication handle")
        return _normalize_final_windows_path(buffer.value)

    def duplicate(self, handle: int) -> int:
        process = self._current_process()
        duplicate = wintypes.HANDLE()
        if not self._duplicate_handle(
            process,
            handle,
            process,
            ctypes.byref(duplicate),
            0,
            False,
            2,  # DUPLICATE_SAME_ACCESS
        ):
            _raise_windows_error("owned publication handle")
        if duplicate.value is None:
            raise RuntimeError("Win32 duplicated an invalid publication handle")
        return int(duplicate.value)

    def flush(self, handle: int) -> None:
        if not self._flush_file(handle):
            _raise_windows_error("owned publication handle")


class WindowsHandleFilesystem:
    """Pin path components and create entries without following reparse points."""

    def __init__(self, api: WindowsApi | None = None) -> None:
        self._api = api or CtypesWindowsApi()

    def move_no_replace_write_through(
        self,
        source: str | Path,
        destination: str | Path,
    ) -> None:
        """Move one staged entry durably without permitting replacement."""

        source_value = os.fspath(source)
        destination_value = os.fspath(destination)
        if not ntpath.isabs(source_value):
            source_value = os.path.abspath(source_value)
        if not ntpath.isabs(destination_value):
            destination_value = os.path.abspath(destination_value)
        source_parent = ntpath.dirname(source_value)
        destination_parent = ntpath.dirname(destination_value)
        if not source_parent or not destination_parent:
            raise ValueError("secure Windows move requires absolute source and destination paths")
        with self.pin_directory_chain(source_parent):
            if ntpath.normcase(source_parent) == ntpath.normcase(destination_parent):
                self._api.move_path(
                    source_value,
                    destination_value,
                    MOVEFILE_WRITE_THROUGH,
                )
                return
            with self.pin_directory_chain(destination_parent):
                self._api.move_path(
                    source_value,
                    destination_value,
                    MOVEFILE_WRITE_THROUGH,
                )

    def claim_owned_tree(
        self,
        root: str | Path,
        *,
        maximum_entries: int = 100_000,
        maximum_file_bytes: int = 256 * 1024 * 1024,
    ) -> WindowsOwnedTreeClaim:
        """Authenticate and pin a staged tree for exact-handle move or deletion."""

        return WindowsOwnedTreeClaim.acquire(
            self._api,
            Path(root),
            maximum_entries=maximum_entries,
            maximum_file_bytes=maximum_file_bytes,
        )

    @contextmanager
    def pin_directory_chain(self, path: str | Path) -> Iterator[tuple[int, ...]]:
        with _pin_directory_chain(self._api, path) as handles:
            yield handles

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


@contextmanager
def _pin_directory_chain(
    api: WindowsApi,
    path: str | Path,
) -> Iterator[tuple[int, ...]]:
    handles: list[int] = []
    try:
        for component in _absolute_directory_chain(os.fspath(path)):
            handle = api.open_path(
                component,
                access=0,
                share=FILE_SHARE_READ,
                creation=OPEN_EXISTING,
                flags=FILE_FLAG_OPEN_REPARSE_POINT | FILE_FLAG_BACKUP_SEMANTICS,
            )
            handles.append(handle)
            attributes = api.attributes(handle)
            if attributes & FILE_ATTRIBUTE_REPARSE_POINT:
                raise RuntimeError("secure artifact path contains a reparse component")
            if not attributes & FILE_ATTRIBUTE_DIRECTORY:
                raise RuntimeError("secure artifact directory component is not a directory")
        yield tuple(handles)
    finally:
        for handle in reversed(handles):
            api.close(handle)


@contextmanager
def _pin_rename_directory_chain(
    api: WindowsApi,
    path: str | Path,
) -> Iterator[tuple[int, ...]]:
    handles: list[int] = []
    try:
        for component in _absolute_directory_chain(os.fspath(path)):
            handle = api.open_path(
                component,
                access=GENERIC_READ,
                share=FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE,
                creation=OPEN_EXISTING,
                flags=FILE_FLAG_OPEN_REPARSE_POINT | FILE_FLAG_BACKUP_SEMANTICS,
            )
            handles.append(handle)
            attributes = api.attributes(handle)
            if attributes & FILE_ATTRIBUTE_REPARSE_POINT:
                raise RuntimeError("secure artifact path contains a reparse component")
            if not attributes & FILE_ATTRIBUTE_DIRECTORY:
                raise RuntimeError("secure artifact directory component is not a directory")
        yield tuple(handles)
    finally:
        for handle in reversed(handles):
            api.close(handle)


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
        return tuple(sorted(Path(*parts).as_posix() for parts in self._directories if parts))

    def unlink_regular(self, relative_path: str | Path) -> None:
        parts = validated_relative_parts(relative_path)
        destination = self._path.joinpath(*parts)
        os.unlink(destination)

    def enumerate_tree(
        self,
        *,
        maximum_entries: int,
        maximum_file_bytes: int,
    ) -> tuple[tuple[tuple[str, str], ...], tuple[str, ...]]:
        """Enumerate the pinned claim without following reparse entries."""

        artifacts: list[tuple[str, str]] = []
        directories: list[str] = []
        entries_seen = 0

        def visit(directory: Path, prefix: tuple[str, ...]) -> None:
            nonlocal entries_seen
            with os.scandir(directory) as entries:
                for entry in entries:
                    entries_seen += 1
                    if entries_seen > maximum_entries:
                        raise RuntimeError("Windows claim enumeration exceeds its bound")
                    metadata = entry.stat(follow_symlinks=False)
                    relative = (*prefix, entry.name)
                    relative_path = Path(*relative).as_posix()
                    if stat.S_ISLNK(metadata.st_mode):
                        raise RuntimeError("Windows claim contains a reparse entry")
                    path = directory / entry.name
                    if stat.S_ISDIR(metadata.st_mode):
                        handle = self._api.open_path(
                            os.fspath(path),
                            access=0,
                            share=FILE_SHARE_READ,
                            creation=OPEN_EXISTING,
                            flags=(FILE_FLAG_OPEN_REPARSE_POINT | FILE_FLAG_BACKUP_SEMANTICS),
                        )
                        try:
                            attributes = self._api.attributes(handle)
                            if attributes & FILE_ATTRIBUTE_REPARSE_POINT or not (
                                attributes & FILE_ATTRIBUTE_DIRECTORY
                            ):
                                raise RuntimeError("Windows claim directory is a reparse entry")
                            directories.append(relative_path)
                            visit(path, relative)
                        finally:
                            self._api.close(handle)
                    elif stat.S_ISREG(metadata.st_mode):
                        handle = self._api.open_path(
                            os.fspath(path),
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
                            try:
                                artifacts.append(
                                    (
                                        relative_path,
                                        _sha256_bounded_descriptor(
                                            descriptor,
                                            maximum=maximum_file_bytes,
                                        ),
                                    )
                                )
                            finally:
                                os.close(descriptor)
                        finally:
                            if handle != -1:
                                self._api.close(handle)
                    else:
                        raise RuntimeError("Windows claim contains a special entry")

        visit(self._path, ())
        return tuple(sorted(artifacts)), tuple(sorted(directories))


class WindowsOwnedTreeClaim:
    """Handle-bound ownership of one immutable staged publication tree.

    Every member is opened without write sharing, so content cannot change while the
    claim lives. Rename and cleanup target the exact root/member handles rather than a
    pathname that another process can exchange.
    """

    def __init__(
        self,
        api: WindowsApi,
        root: Path,
        root_handle: int,
        members: tuple[tuple[str, int, bool, str | None], ...],
        maximum_file_bytes: int,
    ) -> None:
        self._api = api
        self._root = root
        self._root_handle = root_handle
        self._members = members
        self._maximum_file_bytes = maximum_file_bytes
        self._closed = False
        self._published_handles: set[int] = set()
        self._closed_member_handles: set[int] = set()
        self._root_handle_closed = False

    @classmethod
    def acquire(
        cls,
        api: WindowsApi,
        root: Path,
        *,
        maximum_entries: int,
        maximum_file_bytes: int,
    ) -> WindowsOwnedTreeClaim:
        root = root.absolute()
        root_handle = api.open_path(
            os.fspath(root),
            access=GENERIC_READ | DELETE,
            share=FILE_SHARE_READ | FILE_SHARE_DELETE,
            creation=OPEN_EXISTING,
            flags=(
                FILE_FLAG_OPEN_REPARSE_POINT | FILE_FLAG_BACKUP_SEMANTICS | FILE_FLAG_WRITE_THROUGH
            ),
        )
        members: list[tuple[str, int, bool, str | None]] = []
        try:
            root_attributes = api.attributes(root_handle)
            if root_attributes & FILE_ATTRIBUTE_REPARSE_POINT or not (
                root_attributes & FILE_ATTRIBUTE_DIRECTORY
            ):
                raise RuntimeError("owned publication root is not a regular directory")
            pending: list[tuple[Path, str]] = [(root, "")]
            entries_seen = 0
            while pending:
                directory, prefix = pending.pop()
                with os.scandir(directory) as entries:
                    children = sorted(entries, key=lambda item: item.name)
                for child in children:
                    entries_seen += 1
                    if entries_seen > maximum_entries:
                        raise RuntimeError("owned publication tree exceeds its entry bound")
                    relative = child.name if not prefix else f"{prefix}/{child.name}"
                    path = directory / child.name
                    metadata = child.stat(follow_symlinks=False)
                    if stat.S_ISLNK(metadata.st_mode):
                        raise RuntimeError("owned publication tree contains a reparse entry")
                    is_directory = stat.S_ISDIR(metadata.st_mode)
                    if not is_directory and not stat.S_ISREG(metadata.st_mode):
                        raise RuntimeError("owned publication tree contains a special entry")
                    handle = api.open_path(
                        os.fspath(path),
                        access=(GENERIC_READ | DELETE | (0 if is_directory else GENERIC_WRITE)),
                        share=FILE_SHARE_READ | FILE_SHARE_DELETE,
                        creation=OPEN_EXISTING,
                        flags=(
                            FILE_FLAG_OPEN_REPARSE_POINT
                            | (FILE_FLAG_BACKUP_SEMANTICS if is_directory else 0)
                            | FILE_FLAG_WRITE_THROUGH
                        ),
                    )
                    try:
                        attributes = api.attributes(handle)
                        if attributes & FILE_ATTRIBUTE_REPARSE_POINT:
                            raise RuntimeError("owned publication tree contains a reparse entry")
                        if bool(attributes & FILE_ATTRIBUTE_DIRECTORY) != is_directory:
                            raise RuntimeError("owned publication member type changed")
                        digest = None
                        if is_directory:
                            pending.append((path, relative))
                        else:
                            duplicate = api.duplicate(handle)
                            descriptor = api.fd_from_handle(
                                duplicate,
                                os.O_RDONLY | getattr(os, "O_BINARY", 0),
                            )
                            try:
                                digest = _sha256_bounded_descriptor(
                                    descriptor,
                                    maximum=maximum_file_bytes,
                                )
                            finally:
                                os.close(descriptor)
                        members.append((relative, handle, is_directory, digest))
                        handle = -1
                    finally:
                        if handle != -1:
                            api.close(handle)
            return cls(api, root, root_handle, tuple(members), maximum_file_bytes)
        except Exception:
            for _, handle, _, _ in reversed(members):
                api.close(handle)
            api.close(root_handle)
            raise

    @property
    def manifest(self) -> tuple[tuple[str, str | None], ...]:
        return tuple((relative, digest) for relative, _, _, digest in self._members)

    @property
    def root_identity(self) -> tuple[int, int]:
        return self._api.identity(self._root_handle)

    def move_to(self, destination: str | Path) -> None:
        self._require_open()
        self._verify_contents()
        destination_path = Path(destination).absolute()
        destination_name = _validated_final_component(destination_path)
        with _pin_rename_directory_chain(self._api, destination_path.parent) as directory_handles:
            destination_root = self._api.create_directory_relative(
                directory_handles[-1],
                destination_name,
            )
            if ntpath.normcase(self._api.final_path(destination_root)) != ntpath.normcase(
                os.fspath(destination_path)
            ):
                self._api.delete_handle(destination_root)
                self._api.close(destination_root)
                raise RuntimeError("owned publication destination directory was substituted")
            self._transfer_tree(destination_path, destination_root)
            _require_current_directory_identity(
                self._api,
                destination_path,
                destination_root,
            )
        self._root = destination_path

    def _transfer_tree(self, destination_path: Path, destination_root: int) -> None:
        source_directories = {
            relative: handle for relative, handle, is_directory, _ in self._members if is_directory
        }
        destination_directories: dict[str, int] = {"": destination_root}
        moved_files: list[tuple[str, int]] = []
        try:
            for relative, _, is_directory, _ in sorted(
                self._members,
                key=lambda item: (item[0].count("/"), item[0]),
            ):
                if not is_directory:
                    continue
                parent, name = _split_relative_member(relative)
                destination_directories[relative] = self._api.create_directory_relative(
                    destination_directories[parent],
                    name,
                )
            for relative, handle, is_directory, _ in self._members:
                if is_directory:
                    continue
                parent, name = _split_relative_member(relative)
                self._api.rename_handle(handle, destination_directories[parent], name)
                moved_files.append((relative, handle))
                expected = destination_path.joinpath(*relative.split("/"))
                if ntpath.normcase(self._api.final_path(handle)) != ntpath.normcase(
                    os.fspath(expected)
                ):
                    raise RuntimeError("owned publication member destination was substituted")
                self._api.flush(handle)
        except Exception as error:
            rollback_errors: list[Exception] = []
            for relative, handle in reversed(moved_files):
                parent, name = _split_relative_member(relative)
                source_parent = self._root_handle if not parent else source_directories[parent]
                try:
                    self._api.rename_handle(handle, source_parent, name)
                except Exception as failure:
                    rollback_errors.append(failure)
            for relative, handle in sorted(
                destination_directories.items(),
                key=lambda item: item[0].count("/"),
                reverse=True,
            ):
                del relative
                try:
                    self._api.delete_handle(handle)
                    self._api.close(handle)
                except Exception as failure:
                    rollback_errors.append(failure)
            if rollback_errors:
                raise RuntimeError(
                    "Windows exact-handle tree transfer rollback failed: "
                    + "; ".join(str(item) for item in rollback_errors)
                ) from error
            raise

        for _, handle, _, _ in sorted(
            (item for item in self._members if item[2]),
            key=lambda item: item[0].count("/"),
            reverse=True,
        ):
            self._api.delete_handle(handle)
            self._api.close(handle)
        replacement_members = [
            (
                relative,
                destination_directories[relative] if is_directory else handle,
                is_directory,
                digest,
            )
            for relative, handle, is_directory, digest in self._members
        ]
        self._api.delete_handle(self._root_handle)
        self._api.close(self._root_handle)
        self._root_handle = destination_root
        self._members = tuple(replacement_members)

    def move_member_to(self, relative_path: str | Path, destination: str | Path) -> None:
        """Move one exact claimed regular-file handle to an additive destination."""

        self._require_open()
        self._verify_contents()
        relative = Path(*validated_relative_parts(relative_path)).as_posix()
        member = next((item for item in self._members if item[0] == relative), None)
        if member is None or member[2]:
            raise ValueError("owned publication member is not a claimed regular file")
        destination_path = Path(destination).absolute()
        destination_name = _validated_final_component(destination_path)
        with _pin_rename_directory_chain(self._api, destination_path.parent) as directory_handles:
            self._api.rename_handle(
                member[1],
                directory_handles[-1],
                destination_name,
            )
            _require_current_directory_identity(
                self._api,
                destination_path.parent,
                directory_handles[-1],
            )
        if ntpath.normcase(self._api.final_path(member[1])) != ntpath.normcase(
            os.fspath(destination_path)
        ):
            raise RuntimeError("owned publication member did not reach its destination")
        self._api.flush(member[1])
        self._published_handles.add(member[1])

    def delete_exact(self) -> None:
        self._require_open()
        files = [
            item for item in self._members if not item[2] and item[1] not in self._published_handles
        ]
        published_files = [
            item for item in self._members if not item[2] and item[1] in self._published_handles
        ]
        directories = sorted(
            (item for item in self._members if item[2]),
            key=lambda item: item[0].count("/"),
            reverse=True,
        )
        for _, handle, _, _ in files:
            self._api.delete_handle(handle)
            self._api.close(handle)
            self._closed_member_handles.add(handle)
        for _, handle, _, _ in published_files:
            self._api.close(handle)
            self._closed_member_handles.add(handle)
        for _, handle, _, _ in directories:
            self._api.delete_handle(handle)
            self._api.close(handle)
            self._closed_member_handles.add(handle)
        self._api.delete_handle(self._root_handle)
        self._api.close(self._root_handle)
        self._root_handle_closed = True
        self._closed = True

    def close(self) -> None:
        if self._closed:
            return
        for _, handle, _, _ in reversed(self._members):
            if handle not in self._closed_member_handles:
                self._api.close(handle)
        if not self._root_handle_closed:
            self._api.close(self._root_handle)
        self._closed = True

    def _require_open(self) -> None:
        if self._closed:
            raise RuntimeError("owned publication claim is closed")

    def _verify_contents(self) -> None:
        for relative, handle, is_directory, expected_digest in self._members:
            if is_directory:
                continue
            duplicate = self._api.duplicate(handle)
            descriptor = self._api.fd_from_handle(
                duplicate,
                os.O_RDONLY | getattr(os, "O_BINARY", 0),
            )
            try:
                os.lseek(descriptor, 0, os.SEEK_SET)
                actual_digest = _sha256_bounded_descriptor(
                    descriptor,
                    maximum=self._maximum_file_bytes,
                )
            finally:
                os.close(descriptor)
            if actual_digest != expected_digest:
                raise RuntimeError(f"owned publication content changed after claim: {relative}")

    def __enter__(self) -> WindowsOwnedTreeClaim:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()


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


def _validated_final_component(path: Path) -> str:
    name = path.name
    _require_safe_component(name)
    return name


def _split_relative_member(relative: str) -> tuple[str, str]:
    parent, separator, name = relative.rpartition("/")
    return (parent if separator else ""), name


def _require_current_directory_identity(
    api: WindowsApi,
    path: Path,
    expected_handle: int,
) -> None:
    with _pin_directory_chain(api, path) as current_handles:
        if api.identity(current_handles[-1]) != api.identity(expected_handle):
            raise RuntimeError("owned publication destination directory was substituted")


def _absolute_directory_chain(path: str) -> tuple[str, ...]:
    raw_drive, raw_tail = ntpath.splitdrive(path)
    if not raw_drive and os.name != "nt" and os.path.isabs(path):
        parts = Path(path).parts
        return tuple(os.path.join(*parts[:index]) for index in range(1, len(parts) + 1))
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


def _sha256_bounded_descriptor(descriptor: int, *, maximum: int) -> str:
    metadata = os.fstat(descriptor)
    if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > maximum:
        raise RuntimeError("Windows claim artifact exceeds its regular-file bound")
    digest = hashlib.sha256()
    total = 0
    while chunk := os.read(descriptor, min(1024 * 1024, maximum - total + 1)):
        total += len(chunk)
        if total > maximum:
            raise RuntimeError("Windows claim artifact exceeds its byte bound")
        digest.update(chunk)
    return digest.hexdigest()


def _require_safe_component(component: str) -> None:
    if (
        not component
        or component in (".", "..")
        or any(character in component for character in ("/", "\\", ":"))
    ):
        raise ValueError("secure publication path component is invalid")


def _raise_windows_error(path: str) -> None:
    error = cast(Any, getattr(ctypes, "get_last_error"))()
    _raise_windows_error_code(error, path)


def _raise_windows_error_code(error: int, path: str) -> None:
    if error in {_ERROR_FILE_EXISTS, _ERROR_ALREADY_EXISTS}:
        raise FileExistsError(error, "secure artifact entry already exists", path)
    raise OSError(error, f"Win32 secure artifact operation failed: {path}")


class _FileAttributeTagInfo(ctypes.Structure):
    _fields_ = (
        ("file_attributes", wintypes.DWORD),
        ("reparse_tag", wintypes.DWORD),
    )


class _ByHandleFileInformation(ctypes.Structure):
    _fields_ = (
        ("file_attributes", wintypes.DWORD),
        ("creation_time_low", wintypes.DWORD),
        ("creation_time_high", wintypes.DWORD),
        ("last_access_time_low", wintypes.DWORD),
        ("last_access_time_high", wintypes.DWORD),
        ("last_write_time_low", wintypes.DWORD),
        ("last_write_time_high", wintypes.DWORD),
        ("volume_serial_number", wintypes.DWORD),
        ("file_size_high", wintypes.DWORD),
        ("file_size_low", wintypes.DWORD),
        ("number_of_links", wintypes.DWORD),
        ("file_index_high", wintypes.DWORD),
        ("file_index_low", wintypes.DWORD),
    )


class _FileDispositionInfoEx(ctypes.Structure):
    _fields_ = (("flags", wintypes.DWORD),)


class _FileRenameInformationEx(ctypes.Structure):
    _fields_ = (
        ("flags", wintypes.DWORD),
        ("root_directory", wintypes.HANDLE),
        ("file_name_length", wintypes.DWORD),
        ("file_name", wintypes.WCHAR * 1),
    )


class _IoStatusBlock(ctypes.Structure):
    _fields_ = (
        ("status", wintypes.LPVOID),
        ("information", ctypes.c_size_t),
    )


class _UnicodeString(ctypes.Structure):
    _fields_ = (
        ("length", wintypes.USHORT),
        ("maximum_length", wintypes.USHORT),
        ("buffer", wintypes.LPWSTR),
    )


class _ObjectAttributes(ctypes.Structure):
    _fields_ = (
        ("length", wintypes.ULONG),
        ("root_directory", wintypes.HANDLE),
        ("object_name", ctypes.POINTER(_UnicodeString)),
        ("attributes", wintypes.ULONG),
        ("security_descriptor", wintypes.LPVOID),
        ("security_quality_of_service", wintypes.LPVOID),
    )


def _normalize_final_windows_path(path: str) -> str:
    if path.startswith("\\\\?\\UNC\\"):
        return "\\\\" + path[8:]
    if path.startswith("\\\\?\\"):
        return path[4:]
    return path


__all__ = [
    "CREATE_NEW",
    "FILE_FLAG_BACKUP_SEMANTICS",
    "FILE_FLAG_OPEN_REPARSE_POINT",
    "FILE_FLAG_WRITE_THROUGH",
    "FILE_SHARE_DELETE",
    "FILE_SHARE_READ",
    "MOVEFILE_REPLACE_EXISTING",
    "MOVEFILE_WRITE_THROUGH",
    "OPEN_EXISTING",
    "WindowsDirectoryClaim",
    "WindowsHandleFilesystem",
    "WindowsOwnedTreeClaim",
    "validated_relative_parts",
]
