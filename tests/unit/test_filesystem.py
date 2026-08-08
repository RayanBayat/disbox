"""Tree operations. The code most able to destroy data, so tested hardest."""

from collections.abc import Iterator
from pathlib import Path

import pytest

from disbox.core.crypto import KdfParams
from disbox.core.filesystem import FileSystem, NameCollision
from disbox.core.integrity import check_invariants
from disbox.core.journal import entries
from disbox.core.vault import Vault
from disbox.errors import FileSystemError

PASSPHRASE = "filesystem test passphrase"  # noqa: S105 - fixture, not a credential
FAST = KdfParams(time_cost=1, memory_cost=8, parallelism=1)


@pytest.fixture
def vault(tmp_path: Path) -> Iterator[Vault]:
    with Vault.create_encrypted(tmp_path / "v.dbx", PASSPHRASE, FAST) as opened:
        yield opened


@pytest.fixture
def fs(vault: Vault) -> FileSystem:
    return FileSystem(vault)


class TestCreate:
    def test_a_folder_appears_at_the_root(self, fs: FileSystem) -> None:
        node = fs.create_directory(None, "Documents")
        assert [child.name for child in fs.children(None)] == ["Documents"]
        assert fs.resolve(node).name == "Documents"

    def test_nested_folders_resolve_to_a_path(self, fs: FileSystem) -> None:
        parent = fs.create_directory(None, "Photos")
        child = fs.create_directory(parent, "2026")
        assert fs.path_of(child) == "/Photos/2026"

    def test_a_duplicate_name_is_refused(self, fs: FileSystem) -> None:
        fs.create_directory(None, "Documents")
        with pytest.raises(FileSystemError, match="already exists"):
            fs.create_directory(None, "Documents")

    def test_a_name_with_a_separator_is_refused(self, fs: FileSystem) -> None:
        """A slash in a name would make paths ambiguous."""
        with pytest.raises(FileSystemError):
            fs.create_directory(None, "bad/name")

    def test_an_empty_name_is_refused(self, fs: FileSystem) -> None:
        with pytest.raises(FileSystemError):
            fs.create_directory(None, "   ")

    def test_creating_inside_a_file_is_refused(self, fs: FileSystem) -> None:
        target = fs.create_file(None, "note.txt")
        with pytest.raises(FileSystemError, match="not a folder"):
            fs.create_directory(target, "impossible")


class TestRename:
    def test_a_rename_takes_effect(self, fs: FileSystem) -> None:
        node = fs.create_file(None, "draft.txt")
        fs.rename(node, "final.txt")
        assert fs.resolve(node).name == "final.txt"

    def test_renaming_does_not_corrupt_a_path_containing_the_name(self, fs: FileSystem) -> None:
        """Regression for ANALYSIS.md 5.4.

        The old client renamed by substring replacement, so /report/report.txt
        became /final.txt/report.txt. Paths are derived from parent ids here,
        which makes that unrepresentable.
        """
        folder = fs.create_directory(None, "report")
        node = fs.create_file(folder, "report.txt")
        fs.rename(node, "final.txt")
        assert fs.path_of(node) == "/report/final.txt"

    def test_renaming_onto_a_sibling_is_refused(self, fs: FileSystem) -> None:
        fs.create_file(None, "taken.txt")
        node = fs.create_file(None, "other.txt")
        with pytest.raises(FileSystemError, match="already exists"):
            fs.rename(node, "taken.txt")

    def test_renaming_to_the_same_name_is_harmless(self, fs: FileSystem) -> None:
        node = fs.create_file(None, "same.txt")
        fs.rename(node, "same.txt")
        assert fs.resolve(node).name == "same.txt"


class TestMove:
    def test_a_move_reparents_the_node(self, fs: FileSystem) -> None:
        source = fs.create_directory(None, "From")
        target = fs.create_directory(None, "To")
        node = fs.create_file(source, "f.txt")

        fs.move(node, target)
        assert fs.path_of(node) == "/To/f.txt"

    def test_moving_a_folder_carries_its_contents(self, fs: FileSystem) -> None:
        source = fs.create_directory(None, "From")
        inner = fs.create_directory(source, "Inner")
        leaf = fs.create_file(inner, "deep.txt")
        target = fs.create_directory(None, "To")

        fs.move(inner, target)
        assert fs.path_of(leaf) == "/To/Inner/deep.txt"

    def test_moving_a_folder_into_itself_is_refused(self, fs: FileSystem) -> None:
        """Otherwise the subtree detaches into a cycle and becomes unreachable."""
        folder = fs.create_directory(None, "Folder")
        with pytest.raises(FileSystemError, match="into itself"):
            fs.move(folder, folder)

    def test_moving_a_folder_into_its_own_descendant_is_refused(self, fs: FileSystem) -> None:
        outer = fs.create_directory(None, "Outer")
        inner = fs.create_directory(outer, "Inner")
        with pytest.raises(FileSystemError, match="into itself"):
            fs.move(outer, inner)

    def test_a_refused_move_leaves_the_tree_intact(self, fs: FileSystem) -> None:
        outer = fs.create_directory(None, "Outer")
        inner = fs.create_directory(outer, "Inner")
        with pytest.raises(FileSystemError):
            fs.move(outer, inner)
        assert fs.path_of(inner) == "/Outer/Inner"
        assert check_invariants(fs.vault.connection) == []

    def test_moving_onto_an_occupied_name_is_refused(self, fs: FileSystem) -> None:
        target = fs.create_directory(None, "To")
        fs.create_file(target, "f.txt")
        node = fs.create_file(None, "f.txt")
        with pytest.raises(FileSystemError, match="already exists"):
            fs.move(node, target)

    def test_moving_into_a_file_is_refused(self, fs: FileSystem) -> None:
        node = fs.create_file(None, "a.txt")
        target = fs.create_file(None, "b.txt")
        with pytest.raises(FileSystemError, match="not a folder"):
            fs.move(node, target)


class TestTrash:
    def test_deleting_hides_a_node_from_its_folder(self, fs: FileSystem) -> None:
        node = fs.create_file(None, "gone.txt")
        fs.delete(node)
        assert fs.children(None) == []

    def test_a_deleted_node_appears_in_the_trash(self, fs: FileSystem) -> None:
        node = fs.create_file(None, "gone.txt")
        fs.delete(node)
        assert [item.id for item in fs.trash()] == [node]

    def test_restoring_returns_it_to_its_folder(self, fs: FileSystem) -> None:
        folder = fs.create_directory(None, "Docs")
        node = fs.create_file(folder, "f.txt")
        fs.delete(node)
        fs.restore(node)
        assert fs.path_of(node) == "/Docs/f.txt"

    def test_deleting_a_folder_takes_its_contents_with_it(self, fs: FileSystem) -> None:
        """The old client could not delete a non-empty folder at all."""
        folder = fs.create_directory(None, "Docs")
        inner = fs.create_directory(folder, "Inner")
        leaf = fs.create_file(inner, "deep.txt")

        fs.delete(folder)
        assert fs.children(None) == []
        assert {item.id for item in fs.trash()} >= {folder, inner, leaf}

    def test_restoring_a_folder_restores_its_contents(self, fs: FileSystem) -> None:
        folder = fs.create_directory(None, "Docs")
        leaf = fs.create_file(folder, "f.txt")
        fs.delete(folder)
        fs.restore(folder)
        assert fs.path_of(leaf) == "/Docs/f.txt"

    def test_a_name_is_reusable_once_trashed(self, fs: FileSystem) -> None:
        first = fs.create_file(None, "notes.txt")
        fs.delete(first)
        second = fs.create_file(None, "notes.txt")
        assert second != first

    def test_restoring_into_a_taken_name_is_refused(self, fs: FileSystem) -> None:
        """Restoring must never silently overwrite whatever took the name."""
        first = fs.create_file(None, "notes.txt")
        fs.delete(first)
        fs.create_file(None, "notes.txt")
        with pytest.raises(FileSystemError, match="already exists"):
            fs.restore(first)

    def test_restoring_something_not_deleted_is_harmless(self, fs: FileSystem) -> None:
        node = fs.create_file(None, "here.txt")
        fs.restore(node)
        assert fs.path_of(node) == "/here.txt"


class TestCollisionPolicy:
    def test_keep_both_picks_an_available_name(self, fs: FileSystem) -> None:
        fs.create_file(None, "report.pdf")
        chosen = fs.available_name(None, "report.pdf", NameCollision.KEEP_BOTH)
        assert chosen == "report (1).pdf"

    def test_keep_both_counts_upward(self, fs: FileSystem) -> None:
        fs.create_file(None, "report.pdf")
        fs.create_file(None, "report (1).pdf")
        assert fs.available_name(None, "report.pdf", NameCollision.KEEP_BOTH) == "report (2).pdf"

    def test_a_free_name_is_returned_unchanged(self, fs: FileSystem) -> None:
        assert fs.available_name(None, "fresh.txt", NameCollision.KEEP_BOTH) == "fresh.txt"

    def test_extensionless_names_are_handled(self, fs: FileSystem) -> None:
        fs.create_file(None, "README")
        assert fs.available_name(None, "README", NameCollision.KEEP_BOTH) == "README (1)"


class TestConsistency:
    def test_the_tree_stays_consistent_through_many_operations(self, fs: FileSystem) -> None:
        a = fs.create_directory(None, "A")
        b = fs.create_directory(None, "B")
        for index in range(10):
            fs.create_file(a, f"f{index}.txt")

        moved = fs.children(a)[0].id
        fs.move(moved, b)
        fs.rename(moved, "renamed.txt")
        fs.delete(a)
        fs.restore(a)

        assert check_invariants(fs.vault.connection) == []

    def test_every_mutation_is_journalled(self, fs: FileSystem) -> None:
        node = fs.create_directory(None, "Docs")
        fs.rename(node, "Documents")
        fs.delete(node)

        recorded = {entry.op for entry in entries(fs.vault.connection)}
        assert {"create", "rename", "delete"} <= recorded
