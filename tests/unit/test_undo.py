"""Undoing the last mutation, using the journal as the record of what happened."""

import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest

from disbox.core.filesystem import FileSystem
from disbox.core.undo import describe_next_undo, undo_last
from disbox.core.vault import Vault
from tests.unit.test_vault import KEYS


@pytest.fixture
def vault(tmp_path: Path) -> Iterator[Vault]:
    with Vault.create(tmp_path / "demo.dbx", KEYS) as vault:
        yield vault


@pytest.fixture
def filesystem(vault: Vault) -> FileSystem:
    return FileSystem(vault)


def names(filesystem: FileSystem, parent: uuid.UUID | None = None) -> list[str]:
    return sorted(n.name for n in filesystem.children(parent))


def test_undoing_a_create_removes_it(vault: Vault, filesystem: FileSystem) -> None:
    filesystem.create_directory(None, "Oops")

    undo_last(vault)

    assert names(filesystem) == []


def test_undoing_a_rename_restores_the_old_name(vault: Vault, filesystem: FileSystem) -> None:
    node = filesystem.create_file(None, "before.txt")
    filesystem.rename(node, "after.txt")

    undo_last(vault)

    assert names(filesystem) == ["before.txt"]


def test_undoing_a_delete_brings_it_back(vault: Vault, filesystem: FileSystem) -> None:
    node = filesystem.create_file(None, "gone.txt")
    filesystem.delete(node)

    undo_last(vault)

    assert names(filesystem) == ["gone.txt"]
    assert filesystem.trash() == []


def test_undoing_a_move_returns_it_to_the_old_parent(vault: Vault, filesystem: FileSystem) -> None:
    """The move payload must carry where it came from, or this cannot work."""
    source = filesystem.create_directory(None, "Source")
    target = filesystem.create_directory(None, "Target")
    node = filesystem.create_file(source, "wandering.txt")
    filesystem.move(node, target)

    undo_last(vault)

    assert names(filesystem, source) == ["wandering.txt"]
    assert names(filesystem, target) == []


def test_undo_works_backwards_through_several_operations(
    vault: Vault, filesystem: FileSystem
) -> None:
    node = filesystem.create_file(None, "one.txt")
    filesystem.rename(node, "two.txt")
    filesystem.rename(node, "three.txt")

    undo_last(vault)
    assert names(filesystem) == ["two.txt"]
    undo_last(vault)
    assert names(filesystem) == ["one.txt"]


def test_an_undo_is_not_itself_undone(vault: Vault, filesystem: FileSystem) -> None:
    """Otherwise a second undo would put the change straight back."""
    node = filesystem.create_file(None, "start.txt")
    filesystem.rename(node, "changed.txt")

    undo_last(vault)
    undo_last(vault)

    # The second undo reaches past the first to the create, removing the node.
    assert names(filesystem) == []


def test_undo_reports_what_it_did(vault: Vault, filesystem: FileSystem) -> None:
    node = filesystem.create_file(None, "before.txt")
    filesystem.rename(node, "after.txt")

    message = undo_last(vault)

    assert message is not None
    assert "rename" in message.lower()


def test_undo_with_nothing_to_undo_returns_none(vault: Vault) -> None:
    assert undo_last(vault) is None


def test_describe_next_undo_names_the_pending_operation(
    vault: Vault, filesystem: FileSystem
) -> None:
    filesystem.create_directory(None, "Thing")

    assert "create" in (describe_next_undo(vault) or "").lower()


def test_describe_next_undo_is_none_when_nothing_is_pending(vault: Vault) -> None:
    assert describe_next_undo(vault) is None


def test_undoing_a_rename_onto_a_taken_name_is_refused(
    vault: Vault, filesystem: FileSystem
) -> None:
    """Undo must not overwrite whatever has since claimed the old name."""
    node = filesystem.create_file(None, "original.txt")
    filesystem.rename(node, "renamed.txt")
    # Inserted directly so the rename stays the newest journalled action; going
    # through create_file would make that create the thing undo reverses.
    with vault.connection as conn:
        conn.execute(
            "INSERT INTO nodes (id, parent_id, name, kind, size, created_at, modified_at) "
            "VALUES (?, NULL, 'original.txt', 'file', 0, '2026-01-01T00:00:00Z', "
            "'2026-01-01T00:00:00Z')",
            (uuid.uuid7().bytes,),
        )

    message = undo_last(vault)

    assert message is not None
    assert "cannot undo" in message.lower()
    assert names(filesystem) == ["original.txt", "renamed.txt"]
