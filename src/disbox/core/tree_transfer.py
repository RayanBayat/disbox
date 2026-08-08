"""Uploading and downloading whole folder trees.

Kept separate from the transfer engine because the concerns are different: the
engine moves the bytes of one file, this walks a tree and decides what to do
with each entry. Mixing them would put filesystem policy -- what to skip, how to
handle a collision, what counts as a failure -- inside the code that should only
know about chunks.

Partial success is the normal outcome, not an error. A folder of a thousand
files where three are locked by another process should upload nine hundred and
ninety-seven and tell you about the three. Aborting the whole operation on the
first problem is what makes bulk transfer frustrating to use.
"""

import uuid
from dataclasses import dataclass, field
from pathlib import Path

from disbox.core.engine import TransferEngine
from disbox.core.filesystem import FileSystem, NameCollision
from disbox.errors import FileSystemError, TransferError
from disbox.log import get_logger

__all__ = ["TreeResult", "TreeTransfer"]

logger = get_logger(__name__)


@dataclass(slots=True)
class TreeResult:
    """What a tree transfer accomplished.

    Attributes:
        files: How many files transferred successfully.
        folders: How many folders were created.
        bytes_moved: Total size of the files transferred.
        failures: One message per entry that could not be handled, naming it.
    """

    files: int = 0
    folders: int = 0
    bytes_moved: int = 0
    failures: list[str] = field(default_factory=list)

    @property
    def complete(self) -> bool:
        """Whether every entry was handled."""
        return not self.failures


class TreeTransfer:
    """Moves folder trees between the local disk and a vault."""

    def __init__(self, filesystem: FileSystem, engine: TransferEngine) -> None:
        """Operate on `filesystem` using `engine` for file contents."""
        self._fs = filesystem
        self._engine = engine

    async def upload_folder(
        self,
        source: Path,
        parent_id: uuid.UUID | None,
        *,
        collision: NameCollision = NameCollision.KEEP_BOTH,
    ) -> TreeResult:
        """Upload `source` and everything beneath it.

        Args:
            source: Local directory to upload.
            parent_id: Vault folder to place it in, or None for the root.
            collision: What to do when a name is already taken.

        Returns:
            A summary, including any entries that could not be uploaded.

        Raises:
            FileSystemError: If `source` is not a directory. A caller passing a
                file has made a mistake worth reporting rather than absorbing.
        """
        if not source.is_dir():
            msg = f"{source} is not a directory"
            raise FileSystemError(msg)

        result = TreeResult()
        root_name = self._fs.available_name(parent_id, source.name, collision)
        root_id = self._fs.create_directory(parent_id, root_name)
        result.folders += 1

        await self._upload_into(source, root_id, result, collision)
        logger.info(
            "folder uploaded",
            source=str(source),
            files=result.files,
            folders=result.folders,
            failures=len(result.failures),
        )
        return result

    async def _upload_into(
        self,
        source: Path,
        parent_id: uuid.UUID,
        result: TreeResult,
        collision: NameCollision,
    ) -> None:
        """Upload one directory's entries, recursing into subdirectories."""
        for entry in sorted(source.iterdir()):
            try:
                if entry.is_dir():
                    name = self._fs.available_name(parent_id, entry.name, collision)
                    child = self._fs.create_directory(parent_id, name)
                    result.folders += 1
                    await self._upload_into(entry, child, result, collision)
                    continue

                name = self._fs.available_name(parent_id, entry.name, collision)
                node = self._fs.create_file(parent_id, name)
                with entry.open("rb") as handle:
                    await self._engine.upload(node, handle)
                result.files += 1
                result.bytes_moved += entry.stat().st_size
            except (OSError, FileSystemError, TransferError) as exc:
                # One unreadable file must not abandon the other 999.
                result.failures.append(f"{entry}: {exc}")

    async def download_folder(self, node_id: uuid.UUID, destination: Path) -> TreeResult:
        """Write a vault folder and its contents to the local disk.

        Args:
            node_id: Vault folder to download.
            destination: Local directory to write into. Created if absent.

        Returns:
            A summary, including any entries that could not be written.

        Raises:
            FileSystemError: If `node_id` is not a folder.
        """
        node = self._fs.resolve(node_id)
        if node.kind != "dir":
            msg = f"{node.name!r} is not a folder"
            raise FileSystemError(msg)

        result = TreeResult()
        target = destination / node.name
        target.mkdir(parents=True, exist_ok=True)
        result.folders += 1

        await self._download_into(node_id, target, result)
        logger.info(
            "folder downloaded",
            destination=str(target),
            files=result.files,
            failures=len(result.failures),
        )
        return result

    async def _download_into(
        self, parent_id: uuid.UUID, destination: Path, result: TreeResult
    ) -> None:
        """Write one folder's children, recursing into subfolders."""
        for child in self._fs.children(parent_id):
            local = destination / child.name
            try:
                if child.kind == "dir":
                    local.mkdir(parents=True, exist_ok=True)
                    result.folders += 1
                    await self._download_into(child.id, local, result)
                    continue

                # Written to a staging name and renamed, so an interrupted
                # download never leaves a truncated file that looks complete.
                staging = local.with_suffix(local.suffix + ".partial")
                with staging.open("wb") as handle:
                    await self._engine.download(child.id, handle)
                staging.replace(local)
                result.files += 1
                result.bytes_moved += child.size
            except (OSError, FileSystemError, TransferError) as exc:
                result.failures.append(f"{child.name}: {exc}")
