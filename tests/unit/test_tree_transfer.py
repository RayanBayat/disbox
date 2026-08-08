"""Whole-folder transfer, including partial success."""

import random
from collections.abc import Iterator
from pathlib import Path
from typing import IO, Any

import pytest

from disbox.backends.local import LocalBackend
from disbox.core.chunker import ChunkSpec
from disbox.core.crypto import KdfParams
from disbox.core.engine import TransferEngine
from disbox.core.filesystem import FileSystem
from disbox.core.tree_transfer import TreeTransfer
from disbox.core.vault import Vault
from disbox.errors import FileSystemError

PASSPHRASE = "tree transfer passphrase"  # noqa: S105 - fixture, not a credential
FAST = KdfParams(time_cost=1, memory_cost=8, parallelism=1)
SPEC = ChunkSpec(min_size=256, avg_size=1024, max_size=4096)


def data(size: int, seed: int = 0) -> bytes:
    return random.Random(seed).randbytes(size)  # noqa: S311 - fixture data, not a key


@pytest.fixture
def vault(tmp_path: Path) -> Iterator[Vault]:
    with Vault.create_encrypted(tmp_path / "v.dbx", PASSPHRASE, FAST) as opened:
        yield opened


@pytest.fixture
def fs(vault: Vault) -> FileSystem:
    return FileSystem(vault)


@pytest.fixture
def tree(vault: Vault, fs: FileSystem, tmp_path: Path) -> TreeTransfer:
    backend = LocalBackend(tmp_path / "blobs", max_blob_size=64 * 1024)
    engine = TransferEngine(vault, backend, vault.unlock(PASSPHRASE), spec=SPEC, concurrency=4)
    return TreeTransfer(fs, engine)


@pytest.fixture
def sample(tmp_path: Path) -> Path:
    """A small folder tree on disk."""
    root = tmp_path / "Project"
    (root / "docs" / "deep").mkdir(parents=True)
    (root / "notes.txt").write_bytes(data(3_000))
    (root / "docs" / "spec.md").write_bytes(data(5_000, seed=1))
    (root / "docs" / "deep" / "buried.bin").write_bytes(data(8_000, seed=2))
    return root


class TestUploadFolder:
    async def test_the_whole_tree_is_uploaded(self, tree: TreeTransfer, sample: Path) -> None:
        result = await tree.upload_folder(sample, None)
        assert result.files == 3
        assert result.folders == 3  # Project, docs, deep
        assert result.complete

    async def test_structure_is_preserved(
        self, tree: TreeTransfer, fs: FileSystem, sample: Path
    ) -> None:
        await tree.upload_folder(sample, None)
        project = fs.children(None)[0]
        docs = next(c for c in fs.children(project.id) if c.name == "docs")
        deep = next(c for c in fs.children(docs.id) if c.name == "deep")
        assert [c.name for c in fs.children(deep.id)] == ["buried.bin"]

    async def test_uploading_a_file_is_refused(self, tree: TreeTransfer, sample: Path) -> None:
        with pytest.raises(FileSystemError, match="not a directory"):
            await tree.upload_folder(sample / "notes.txt", None)

    async def test_an_empty_folder_uploads(self, tree: TreeTransfer, tmp_path: Path) -> None:
        empty = tmp_path / "Empty"
        empty.mkdir()
        result = await tree.upload_folder(empty, None)
        assert result.files == 0
        assert result.folders == 1

    async def test_uploading_twice_keeps_both(
        self, tree: TreeTransfer, fs: FileSystem, sample: Path
    ) -> None:
        await tree.upload_folder(sample, None)
        await tree.upload_folder(sample, None)
        names = sorted(c.name for c in fs.children(None))
        assert names == ["Project", "Project (1)"]

    async def test_an_unreadable_file_does_not_abort_the_rest(
        self, tree: TreeTransfer, sample: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A folder of a thousand files should not be lost to one bad entry."""
        original = Path.open

        def refuse(self: Path, *args: Any, **kwargs: Any) -> IO[Any]:
            if self.name == "spec.md":
                msg = "permission denied"
                raise OSError(msg)
            return original(self, *args, **kwargs)

        monkeypatch.setattr(Path, "open", refuse)
        result = await tree.upload_folder(sample, None)

        assert result.files == 2
        assert len(result.failures) == 1
        assert "spec.md" in result.failures[0]
        assert not result.complete


class TestDownloadFolder:
    async def test_a_tree_round_trips_through_the_vault(
        self, tree: TreeTransfer, fs: FileSystem, sample: Path, tmp_path: Path
    ) -> None:
        """The property that matters: what comes back matches what went up."""
        await tree.upload_folder(sample, None)
        project = fs.children(None)[0]

        out = tmp_path / "out"
        result = await tree.download_folder(project.id, out)
        assert result.complete

        assert (out / "Project" / "notes.txt").read_bytes() == (sample / "notes.txt").read_bytes()
        assert (out / "Project" / "docs" / "deep" / "buried.bin").read_bytes() == (
            sample / "docs" / "deep" / "buried.bin"
        ).read_bytes()

    async def test_downloading_a_file_is_refused(
        self, tree: TreeTransfer, fs: FileSystem, sample: Path, tmp_path: Path
    ) -> None:
        await tree.upload_folder(sample, None)
        project = fs.children(None)[0]
        note = next(c for c in fs.children(project.id) if c.name == "notes.txt")

        with pytest.raises(FileSystemError, match="not a folder"):
            await tree.download_folder(note.id, tmp_path / "out")

    async def test_no_partial_files_are_left(
        self, tree: TreeTransfer, fs: FileSystem, sample: Path, tmp_path: Path
    ) -> None:
        """An interrupted download must not leave a truncated file behind."""
        await tree.upload_folder(sample, None)
        project = fs.children(None)[0]
        out = tmp_path / "out"
        await tree.download_folder(project.id, out)

        assert not list(out.rglob("*.partial"))
