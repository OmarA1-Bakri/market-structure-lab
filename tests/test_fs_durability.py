from __future__ import annotations

import importlib
import errno
import os
from pathlib import Path

import pytest


def test_posix_directory_fsync_uses_guarded_no_follow_descriptor(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = importlib.import_module("market_structure_lab.core.fs_durability")
    opened: list[tuple[Path, int]] = []
    closed: list[int] = []

    monkeypatch.setattr(module.os, "open", lambda path, flags: opened.append((path, flags)) or 71)
    monkeypatch.setattr(module.os, "fstat", lambda _descriptor: os.stat(tmp_path))
    monkeypatch.setattr(module.os, "fsync", lambda _descriptor: None)
    monkeypatch.setattr(module.os, "close", closed.append)

    module.fsync_directory_posix(tmp_path)

    assert opened == [
        (
            tmp_path,
            os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0),
        )
    ]
    assert closed == [71]


def test_posix_directory_fsync_closes_descriptor_and_propagates_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = importlib.import_module("market_structure_lab.core.fs_durability")
    closed: list[int] = []

    monkeypatch.setattr(module.os, "open", lambda _path, _flags: 72)
    monkeypatch.setattr(module.os, "fstat", lambda _descriptor: os.stat(tmp_path))
    monkeypatch.setattr(
        module.os,
        "fsync",
        lambda _descriptor: (_ for _ in ()).throw(OSError("durability unavailable")),
    )
    monkeypatch.setattr(module.os, "close", closed.append)

    with pytest.raises(OSError, match="durability unavailable"):
        module.fsync_directory_posix(tmp_path)

    assert closed == [72]


def test_posix_directory_fsync_rejects_missing_required_capability(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = importlib.import_module("market_structure_lab.core.fs_durability")
    monkeypatch.setattr(
        module,
        "_required_posix_flag",
        lambda name: 0 if name == "O_NOFOLLOW" else getattr(os, name, 0),
    )

    with pytest.raises(RuntimeError, match="no-follow directory handles"):
        module.fsync_directory_posix(tmp_path)


def test_posix_durable_move_never_replaces_existing_destination(tmp_path: Path) -> None:
    module = importlib.import_module("market_structure_lab.core.fs_durability")
    source = tmp_path / "stage"
    destination = tmp_path / "published"
    source.write_text("candidate", encoding="ascii")
    destination.write_text("winner", encoding="ascii")

    with pytest.raises(FileExistsError):
        module.durable_move_no_replace(source, destination)

    assert source.read_text(encoding="ascii") == "candidate"
    assert destination.read_text(encoding="ascii") == "winner"


def test_posix_durable_move_commits_destination_parent_after_rename(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = importlib.import_module("market_structure_lab.core.fs_durability")
    source = tmp_path / "stage"
    destination = tmp_path / "published"
    calls: list[tuple[str, Path]] = []

    monkeypatch.setattr(
        module,
        "_rename_no_replace_posix",
        lambda first, second: calls.extend(
            (("rename-source", first), ("rename-destination", second))
        ),
    )
    monkeypatch.setattr(
        module,
        "fsync_directory_posix",
        lambda path: calls.append(("fsync", path)),
    )

    module.durable_move_no_replace(source, destination)

    assert calls == [
        ("rename-source", source),
        ("rename-destination", destination),
        ("fsync", destination.parent),
    ]


def test_posix_durable_move_falls_back_when_renameat2_is_unsupported(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = importlib.import_module("market_structure_lab.core.fs_durability")
    source = tmp_path / "stage"
    destination = tmp_path / "published"
    source.write_text("candidate", encoding="ascii")
    monkeypatch.setattr(
        module,
        "_rename_no_replace_posix",
        lambda _source, target: (_ for _ in ()).throw(
            OSError(errno.EINVAL, os.strerror(errno.EINVAL), target)
        ),
    )
    monkeypatch.setattr(module, "fsync_directory_posix", lambda _path: None)

    module.durable_move_no_replace(source, destination)

    assert not source.exists()
    assert destination.read_text(encoding="ascii") == "candidate"


def test_posix_directory_fallback_is_no_replace_and_rejects_symlinks(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = importlib.import_module("market_structure_lab.core.fs_durability")
    source = tmp_path / "stage"
    source.mkdir()
    (source / "evidence.json").write_text("candidate", encoding="ascii")
    destination = tmp_path / "published"
    monkeypatch.setattr(
        module,
        "_rename_no_replace_posix",
        lambda _source, target: (_ for _ in ()).throw(
            OSError(errno.EINVAL, os.strerror(errno.EINVAL), target)
        ),
    )
    monkeypatch.setattr(module, "fsync_directory_posix", lambda _path: None)

    module.durable_move_no_replace(source, destination)

    assert not source.exists()
    assert (destination / "evidence.json").read_text(encoding="ascii") == "candidate"

    second = tmp_path / "second"
    second.mkdir()
    (second / "other.json").write_text("other", encoding="ascii")
    with pytest.raises(FileExistsError):
        module.durable_move_no_replace(second, destination)
    assert (destination / "evidence.json").read_text(encoding="ascii") == "candidate"
    assert second.exists()

    linked = tmp_path / "linked"
    linked.mkdir()
    (linked / "bad").symlink_to(destination / "evidence.json")
    with pytest.raises(RuntimeError, match="regular files and directories"):
        module.durable_move_no_replace(linked, tmp_path / "linked-published")
    assert linked.exists()
    assert not (tmp_path / "linked-published").exists()


def test_windows_durable_move_uses_secure_write_through_without_directory_open(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = importlib.import_module("market_structure_lab.core.fs_durability")
    source = tmp_path / "stage"
    destination = tmp_path / "published"
    calls: list[tuple[Path, Path]] = []

    class FakeFilesystem:
        def move_no_replace_write_through(self, first: Path, second: Path) -> None:
            calls.append((first, second))

    monkeypatch.setattr(module, "_is_windows_platform", lambda: True)
    monkeypatch.setattr(module, "WindowsHandleFilesystem", FakeFilesystem)
    monkeypatch.setattr(
        module.os,
        "open",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("Windows durability must not open a directory descriptor")
        ),
    )

    module.durable_move_no_replace(source, destination)

    assert calls == [(source, destination)]


def test_windows_durable_move_propagates_secure_api_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = importlib.import_module("market_structure_lab.core.fs_durability")

    class FakeFilesystem:
        def move_no_replace_write_through(self, _source: Path, destination: Path) -> None:
            raise FileExistsError(destination)

    monkeypatch.setattr(module, "_is_windows_platform", lambda: True)
    monkeypatch.setattr(module, "WindowsHandleFilesystem", FakeFilesystem)

    with pytest.raises(FileExistsError):
        module.durable_move_no_replace(tmp_path / "stage", tmp_path / "published")
