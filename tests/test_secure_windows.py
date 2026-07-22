from __future__ import annotations

from contextlib import ExitStack
import importlib

import pytest


class FakeWindowsApi:
    def __init__(self, *, reparse_call: int | None = None) -> None:
        self.reparse_call = reparse_call
        self.opens: list[tuple[str, int, int, int, int]] = []
        self.closed: list[int] = []

    def open_path(
        self,
        path: str,
        *,
        access: int,
        share: int,
        creation: int,
        flags: int,
    ) -> int:
        self.opens.append((path, access, share, creation, flags))
        return len(self.opens)

    def attributes(self, handle: int) -> int:
        directory = 0x10
        reparse = 0x400 if handle == self.reparse_call else 0
        return directory | reparse

    def close(self, handle: int) -> None:
        self.closed.append(handle)


def test_windows_directory_chain_pins_every_component_with_reparse_safe_flags() -> None:
    secure_windows = importlib.import_module("market_structure_lab.core.secure_windows")
    api = FakeWindowsApi()
    filesystem = secure_windows.WindowsHandleFilesystem(api=api)

    with ExitStack() as stack:
        handles = stack.enter_context(filesystem.pin_directory_chain(r"C:\trusted\nested"))
        assert len(handles) == 3

    assert [path for path, *_ in api.opens] == ["C:\\", r"C:\trusted", r"C:\trusted\nested"]
    for _, access, share, creation, flags in api.opens:
        assert access == 0
        assert share == secure_windows.FILE_SHARE_READ
        assert creation == secure_windows.OPEN_EXISTING
        assert flags & secure_windows.FILE_FLAG_OPEN_REPARSE_POINT
        assert flags & secure_windows.FILE_FLAG_BACKUP_SEMANTICS
    assert api.closed == [3, 2, 1]


def test_windows_directory_chain_rejects_reparse_component_and_closes_handles() -> None:
    secure_windows = importlib.import_module("market_structure_lab.core.secure_windows")
    api = FakeWindowsApi(reparse_call=2)
    filesystem = secure_windows.WindowsHandleFilesystem(api=api)

    with pytest.raises(RuntimeError, match="reparse"):
        with filesystem.pin_directory_chain(r"C:\trusted\nested"):
            pass

    assert api.closed == [2, 1]
