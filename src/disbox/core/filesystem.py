"""Tree operations: create, rename, move, delete, restore.

Paths are always *derived* from `parent_id`, never stored. That single decision
removes an entire family of bugs: the previous generation of this project
renamed by replacing a substring of the stored path, so renaming
``/report/report.txt`` produced ``/final.txt/report.txt``. Here a rename writes
one column and no path can disagree with the tree.

Deletion is soft. A node is stamped with `deleted_at` and disappears from its
folder while remaining fully intact, so recovery is a column update rather than
a restore from backup. Nothing here removes a row or touches a stored blob;
reclaiming space is the garbage collector's job, and keeping the two separate is
what makes deletion safe to get wrong.

Every mutation runs inside a transaction that also writes its journal entry, so
the vault can never hold a change with no record of it.
"""

import re
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Final

from disbox.core.journal import record
from disbox.core.vault import Vault
from disbox.errors import FileSystemError

__all__ = ["FileSystem", "NameCollision", "Node"]

_MAX_NAME_LENGTH: Final = 255

# A name becomes a path when a file is downloaded, so anything a filesystem
# would read as an instruction is refused here. Backslash matters most: it is
# the separator on Windows, the primary target, and blocking only "/" left
# names like `..\..\evil` free to escape the download directory. A colon would
# open an NTFS alternate data stream rather than create a file, and the rest
# cannot be written on Windows at all.
_ILLEGAL_NAME = re.compile(r'[/\\:*?"<>|\x00-\x1f]')

#: Reserved on Windows whatever the extension: opening one talks to a device.
_DEVICE_NAMES = frozenset(
    {"con", "prn", "aux", "nul"}
    | {f"com{digit}" for digit in "123456789"}
    | {f"lpt{digit}" for digit in "123456789"}
)

_NUMBERED = re.compile(r"^(?P<stem>.*?) \((?P<count>\d+)\)$")


class NameCollision(StrEnum):
    """What to do when a name is already taken."""

    FAIL = "fail"
    KEEP_BOTH = "keep_both"
    REPLACE = "replace"


@dataclass(frozen=True, slots=True)
class Node:
    """One entry in the tree."""

    id: uuid.UUID
    parent_id: uuid.UUID | None
    name: str
    kind: str
    size: int
    deleted: bool


class FileSystem:
    """Tree operations over one vault."""

    def __init__(self, vault: Vault) -> None:
        """Operate on `vault`."""
        self.vault = vault

    @property
    def connection(self) -> sqlite3.Connection:
        """Connection the journal decorator writes through."""
        return self.vault.connection

    # --------------------------------------------------------------- reads --

    def resolve(self, node_id: uuid.UUID) -> Node:
        """Return one node.

        Raises:
            FileSystemError: If no such node exists.
        """
        row = self.connection.execute(
            "SELECT id, parent_id, name, kind, size, deleted_at FROM nodes WHERE id = ?",
            (node_id.bytes,),
        ).fetchone()
        if row is None:
            msg = f"no node with id {node_id}"
            raise FileSystemError(msg)
        return _to_node(row)

    def children(self, parent_id: uuid.UUID | None) -> list[Node]:
        """List the live contents of a folder, folders first."""
        if parent_id is None:
            rows = self.connection.execute(
                "SELECT id, parent_id, name, kind, size, deleted_at FROM nodes "
                "WHERE parent_id IS NULL AND deleted_at IS NULL "
                "ORDER BY (kind = 'dir') DESC, name COLLATE NOCASE"
            ).fetchall()
        else:
            rows = self.connection.execute(
                "SELECT id, parent_id, name, kind, size, deleted_at FROM nodes "
                "WHERE parent_id = ? AND deleted_at IS NULL "
                "ORDER BY (kind = 'dir') DESC, name COLLATE NOCASE",
                (parent_id.bytes,),
            ).fetchall()
        return [_to_node(row) for row in rows]

    def path_of(self, node_id: uuid.UUID) -> str:
        """Build the absolute path of a node by walking to the root.

        Carries a seen-set: a corrupt tree must produce an error rather than
        loop forever inside a UI thread.
        """
        parts: list[str] = []
        seen: set[uuid.UUID] = set()
        current: uuid.UUID | None = node_id

        while current is not None:
            if current in seen:
                msg = f"node {node_id} lies on a parent cycle"
                raise FileSystemError(msg)
            seen.add(current)
            node = self.resolve(current)
            parts.append(node.name)
            current = node.parent_id

        return "/" + "/".join(reversed(parts))

    def trash(self) -> list[Node]:
        """List deleted nodes, most recently deleted first."""
        rows = self.connection.execute(
            "SELECT id, parent_id, name, kind, size, deleted_at FROM nodes "
            "WHERE deleted_at IS NOT NULL ORDER BY deleted_at DESC"
        ).fetchall()
        return [_to_node(row) for row in rows]

    # ------------------------------------------------------------- creation --

    def create_directory(self, parent_id: uuid.UUID | None, name: str) -> uuid.UUID:
        """Create a folder. Returns its id."""
        return self._create(parent_id, name, "dir")

    def create_file(self, parent_id: uuid.UUID | None, name: str) -> uuid.UUID:
        """Create an empty file node. Contents arrive via the transfer engine."""
        return self._create(parent_id, name, "file")

    def _create(self, parent_id: uuid.UUID | None, name: str, kind: str) -> uuid.UUID:
        """Insert a node after validating its name and destination."""
        clean = _validate_name(name)
        self._require_folder(parent_id)
        self._require_free(parent_id, clean)

        node_id = uuid.uuid7()
        now = datetime.now(UTC).isoformat()
        with self.connection as conn:
            conn.execute(
                "INSERT INTO nodes (id, parent_id, name, kind, created_at, modified_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (node_id.bytes, parent_id.bytes if parent_id else None, clean, kind, now, now),
            )
            record(conn, "create", target_id=node_id, payload={"name": clean, "kind": kind})
        return node_id

    # ------------------------------------------------------------ mutation --

    def rename(self, node_id: uuid.UUID, new_name: str) -> None:
        """Rename a node in place.

        Raises:
            FileSystemError: If the name is invalid or taken by a sibling.
        """
        clean = _validate_name(new_name)
        node = self.resolve(node_id)
        if clean == node.name:
            return
        self._require_free(node.parent_id, clean)

        with self.connection as conn:
            conn.execute(
                "UPDATE nodes SET name = ?, modified_at = ?, version = version + 1 WHERE id = ?",
                (clean, datetime.now(UTC).isoformat(), node_id.bytes),
            )
            record(conn, "rename", target_id=node_id, payload={"from": node.name, "to": clean})

    def move(self, node_id: uuid.UUID, new_parent: uuid.UUID | None) -> None:
        """Reparent a node.

        Raises:
            FileSystemError: If the destination is a file, already holds the
                name, or lies inside the node being moved.
        """
        node = self.resolve(node_id)
        if new_parent == node.parent_id:
            return

        self._require_folder(new_parent)
        # Moving a folder inside itself would detach the subtree into a cycle:
        # every node stays individually valid while the whole branch becomes
        # unreachable from the root.
        if new_parent is not None and self._is_within(new_parent, node_id):
            msg = "cannot move a folder into itself or one of its own descendants"
            raise FileSystemError(msg)
        self._require_free(new_parent, node.name)

        with self.connection as conn:
            conn.execute(
                "UPDATE nodes SET parent_id = ?, modified_at = ?, version = version + 1 "
                "WHERE id = ?",
                (
                    new_parent.bytes if new_parent else None,
                    datetime.now(UTC).isoformat(),
                    node_id.bytes,
                ),
            )
            # The origin is recorded so the move can be reversed. Without it
            # the journal says where a node went but not where it came from,
            # which is not enough to put it back.
            record(
                conn,
                "move",
                target_id=node_id,
                payload={"from": str(node.parent_id), "to": str(new_parent)},
            )

    def delete(self, node_id: uuid.UUID) -> int:
        """Move a node, and everything beneath it, to the trash.

        Nothing is erased: rows keep their contents and their place, so restore
        is a column update. Reclaiming storage is the collector's job.

        Returns:
            How many nodes were affected.
        """
        affected = self._subtree(node_id)
        stamp = datetime.now(UTC).isoformat()

        with self.connection as conn:
            for target in affected:
                conn.execute(
                    "UPDATE nodes SET deleted_at = ?, version = version + 1 WHERE id = ?",
                    (stamp, target.bytes),
                )
            record(conn, "delete", target_id=node_id, payload={"nodes": len(affected)})
        return len(affected)

    def restore(self, node_id: uuid.UUID) -> int:
        """Return a trashed node and its contents to the tree.

        Raises:
            FileSystemError: If the original name is now taken. Restoring must
                never silently overwrite whatever claimed it.
        """
        node = self.resolve(node_id)
        if not node.deleted:
            return 0

        self._require_free(node.parent_id, node.name)
        affected = self._subtree(node_id, include_deleted=True)

        with self.connection as conn:
            for target in affected:
                conn.execute(
                    "UPDATE nodes SET deleted_at = NULL, version = version + 1 WHERE id = ?",
                    (target.bytes,),
                )
            record(conn, "restore", target_id=node_id, payload={"nodes": len(affected)})
        return len(affected)

    # -------------------------------------------------------------- naming --

    def available_name(self, parent_id: uuid.UUID | None, name: str, policy: NameCollision) -> str:
        """Return a usable name under `policy`.

        Raises:
            FileSystemError: If the name is taken and the policy is FAIL.
        """
        if self._is_free(parent_id, name):
            return name
        if policy is not NameCollision.KEEP_BOTH:
            msg = f"a node named {name!r} already exists here"
            raise FileSystemError(msg)

        stem, dot, extension = name.rpartition(".")
        if not dot:
            stem, extension = name, ""
        if (match := _NUMBERED.match(stem)) is not None:
            stem = match.group("stem")

        for counter in range(1, 10_000):
            candidate = f"{stem} ({counter})" + (f".{extension}" if dot else "")
            if self._is_free(parent_id, candidate):
                return candidate

        msg = f"could not find a free name based on {name!r}"
        raise FileSystemError(msg)

    # ----------------------------------------------------------- internals --

    def _is_free(self, parent_id: uuid.UUID | None, name: str) -> bool:
        """Whether `name` is unused among a folder's live children."""
        if parent_id is None:
            row = self.connection.execute(
                "SELECT 1 FROM nodes WHERE parent_id IS NULL AND name = ? AND deleted_at IS NULL",
                (name,),
            ).fetchone()
        else:
            row = self.connection.execute(
                "SELECT 1 FROM nodes WHERE parent_id = ? AND name = ? AND deleted_at IS NULL",
                (parent_id.bytes, name),
            ).fetchone()
        return row is None

    def _require_free(self, parent_id: uuid.UUID | None, name: str) -> None:
        """Raise unless `name` is available in that folder."""
        if not self._is_free(parent_id, name):
            msg = f"a node named {name!r} already exists here"
            raise FileSystemError(msg)

    def _require_folder(self, parent_id: uuid.UUID | None) -> None:
        """Raise unless the destination is the root or a folder."""
        if parent_id is None:
            return
        if self.resolve(parent_id).kind != "dir":
            msg = f"{parent_id} is not a folder"
            raise FileSystemError(msg)

    def _is_within(self, candidate: uuid.UUID, ancestor: uuid.UUID) -> bool:
        """Whether `candidate` is `ancestor` or sits beneath it."""
        current: uuid.UUID | None = candidate
        seen: set[uuid.UUID] = set()
        while current is not None and current not in seen:
            if current == ancestor:
                return True
            seen.add(current)
            current = self.resolve(current).parent_id
        return False

    def _subtree(self, root: uuid.UUID, *, include_deleted: bool = False) -> list[uuid.UUID]:
        """Return `root` and every descendant, breadth first."""
        collected = [root]
        frontier = [root]
        clause = "" if include_deleted else " AND deleted_at IS NULL"

        while frontier:
            current = frontier.pop()
            rows = self.connection.execute(
                f"SELECT id FROM nodes WHERE parent_id = ?{clause}",  # noqa: S608 - literal clause
                (current.bytes,),
            ).fetchall()
            for row in rows:
                child = uuid.UUID(bytes=row[0])
                if child not in collected:  # a cycle must not make this loop
                    collected.append(child)
                    frontier.append(child)
        return collected


def _validate_name(name: str) -> str:
    """Return a cleaned name, or raise if it cannot be used.

    Raises:
        FileSystemError: If the name is empty, too long, or contains a
            separator or control character.
    """
    clean = name.strip()
    if not clean:
        msg = "a name cannot be empty"
        raise FileSystemError(msg)
    if len(clean) > _MAX_NAME_LENGTH:
        msg = f"name is {len(clean)} characters; the limit is {_MAX_NAME_LENGTH}"
        raise FileSystemError(msg)
    if _ILLEGAL_NAME.search(clean):
        msg = f"name {clean!r} contains a separator or control character"
        raise FileSystemError(msg)
    if clean in {".", ".."}:
        msg = f"{clean!r} is not a usable name"
        raise FileSystemError(msg)
    if clean.rstrip(". ") != clean:
        # Windows silently strips these, so "report." and "report" would become
        # the same file on disk while remaining distinct in the vault.
        msg = f"name {clean!r} may not end with a dot or a space"
        raise FileSystemError(msg)
    if clean.split(".")[0].lower() in _DEVICE_NAMES:
        msg = f"name {clean!r} is reserved by the operating system"
        raise FileSystemError(msg)
    return clean


def _to_node(row: sqlite3.Row | tuple[Any, ...]) -> Node:
    """Build a Node from a database row."""
    return Node(
        id=uuid.UUID(bytes=row[0]),
        parent_id=uuid.UUID(bytes=row[1]) if row[1] is not None else None,
        name=str(row[2]),
        kind=str(row[3]),
        size=int(row[4]),
        deleted=row[5] is not None,
    )
