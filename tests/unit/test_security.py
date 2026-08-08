"""Security review findings, as regression tests.

Names entering the vault become paths on the way out: a download writes
``destination / node.name``. Anything the name check lets through is therefore a
filesystem instruction on the machine doing the downloading, which is the
mechanism these tests exist to close.
"""

from collections.abc import Iterator
from pathlib import Path

import pytest

from disbox.core.filesystem import FileSystem
from disbox.core.vault import Vault
from disbox.errors import FileSystemError
from tests.unit.test_vault import KEYS


@pytest.fixture
def filesystem(tmp_path: Path) -> Iterator[FileSystem]:
    """A filesystem over a vault that is closed again afterwards.

    Returning without closing leaks the SQLite connection for every test that
    uses it, and pytest fails the run at teardown over the unraisable
    ResourceWarning -- after reporting every test as passed.
    """
    with Vault.create(tmp_path / "sec.dbx", KEYS) as vault:
        yield FileSystem(vault)


@pytest.mark.parametrize(
    "name",
    [
        r"..\..\evil",
        r"a\b",
        r"\absolute",
        "..",
        "../escape",
    ],
)
def test_a_name_that_is_a_path_is_refused(filesystem: FileSystem, name: str) -> None:
    """Backslash is a separator on Windows, which is the primary target."""
    with pytest.raises(FileSystemError):
        filesystem.create_file(None, name)


@pytest.mark.parametrize("name", ["a:b", "stream:hidden"])
def test_a_colon_is_refused(filesystem: FileSystem, name: str) -> None:
    """On NTFS, `a:b` writes an alternate data stream of `a` rather than a file."""
    with pytest.raises(FileSystemError):
        filesystem.create_file(None, name)


@pytest.mark.parametrize("name", ["CON", "nul", "Com1", "LPT9", "aux"])
def test_windows_device_names_are_refused(filesystem: FileSystem, name: str) -> None:
    """Opening these on Windows talks to a device, not a file."""
    with pytest.raises(FileSystemError):
        filesystem.create_file(None, name)


@pytest.mark.parametrize("name", ["trailing.", "dots..."])
def test_trailing_dots_are_refused(filesystem: FileSystem, name: str) -> None:
    """Windows silently strips them, so two distinct names collide on disk."""
    with pytest.raises(FileSystemError):
        filesystem.create_file(None, name)


def test_trailing_spaces_are_normalised_rather_than_refused(
    filesystem: FileSystem,
) -> None:
    """Stripped on the way in, so no vault name can differ from its file by one."""
    node = filesystem.create_file(None, "spaced   ")

    assert filesystem.resolve(node).name == "spaced"


@pytest.mark.parametrize("name", ['quote"', "star*", "question?", "pipe|", "lt<", "gt>"])
def test_characters_windows_cannot_write_are_refused(filesystem: FileSystem, name: str) -> None:
    """A name that cannot become a file is a download that fails at the end."""
    with pytest.raises(FileSystemError):
        filesystem.create_file(None, name)


def test_ordinary_names_still_work(filesystem: FileSystem) -> None:
    """The rules must not reject the names people actually use."""
    for name in (
        "report.pdf",
        "Ünïcödé.txt",
        "with spaces.txt",
        "hyphen-and_underscore.bin",
        "dotted.name.tar.gz",
        "日本語.txt",
        "emoji 🎉.png",
    ):
        assert filesystem.create_file(None, name)


def test_a_name_is_never_interpreted_as_a_parent_directory(
    filesystem: FileSystem, tmp_path: Path
) -> None:
    """The end-to-end property: a vault name cannot escape a download folder."""
    destination = tmp_path / "downloads"
    destination.mkdir()

    with pytest.raises(FileSystemError):
        filesystem.create_file(None, r"..\outside.txt")

    # Nothing was created, so nothing can be written outside the destination.
    assert list(destination.iterdir()) == []
