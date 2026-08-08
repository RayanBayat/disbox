"""Reclaiming space, checking storage, and rebuilding a lost vault.

The ordering here is the whole point, and it is the inverse of what the previous
generation of this project did. That client deleted its metadata row *first* and
then the stored messages, so a crash in between orphaned attachments whose only
record had just been destroyed -- unreclaimable forever.

Here nothing is deleted from the backend until the vault says, in a committed
transaction, that nothing references it:

    1. soft delete            reversible; the node simply leaves its folder
    2. purge (transactional)  drop refcounts, remove rows; vault now consistent
    3. collect (background)   delete blobs at refcount zero, past a grace period

A crash at any step is safe. Between 2 and 3 the worst outcome is an
unreferenced blob still sitting on the backend -- wasted space that the next
collection reclaims -- and never a manifest pointing at something that is gone.
The grace period exists so that a purge someone regrets is still recoverable
from the backend for a while afterwards.
"""

import sqlite3
import uuid
from datetime import UTC, datetime, timedelta
from typing import Final

from disbox.backends.base import BlobRef, StorageBackend
from disbox.core.crypto import CHUNK_HEADER_MAGIC, ChunkHeader, decode_chunk_header
from disbox.core.journal import record
from disbox.core.vault import Vault
from disbox.errors import CryptoError
from disbox.log import get_logger

__all__ = ["Maintenance"]

logger = get_logger(__name__)

#: How long an unreferenced blob survives before collection removes it.
DEFAULT_GRACE_SECONDS: Final = 24 * 3600

#: Enough of a blob to read its header without downloading the payload.
_HEADER_PROBE: Final = 4096


class Maintenance:
    """Housekeeping for one vault and its backend."""

    def __init__(self, vault: Vault, backend: StorageBackend, master_key: bytes) -> None:
        """Bind to a vault, its backend, and an unlocked master key."""
        self._vault = vault
        self._backend = backend
        self._master_key = master_key

    # --------------------------------------------------------------- purge --

    async def purge(self, node_id: uuid.UUID) -> int:
        """Permanently remove a trashed node from the vault.

        Only the vault is touched. Blobs are left for `collect`, which is what
        keeps this step atomic and recoverable.

        Args:
            node_id: Node to purge. Must already be in the trash.

        Returns:
            How many chunks lost a reference.

        Raises:
            ValueError: If the node is not in the trash. Purging is
                irreversible, so it never applies to live data.
        """
        row = self._vault.connection.execute(
            "SELECT deleted_at, name FROM nodes WHERE id = ?", (node_id.bytes,)
        ).fetchone()
        if row is None:
            msg = f"no node with id {node_id}"
            raise ValueError(msg)
        if row[0] is None:
            msg = f"node {row[1]!r} is not in the trash; delete it before purging"
            raise ValueError(msg)

        released = 0
        with self._vault.connection as conn:
            # One transaction: refcounts drop and the rows disappear together,
            # so the vault is never observed mid-purge.
            chunks = conn.execute(
                "SELECT rc.chunk_hash FROM revision_chunks AS rc "
                "JOIN revisions AS r ON r.id = rc.revision_id WHERE r.node_id = ?",
                (node_id.bytes,),
            ).fetchall()
            for (digest,) in chunks:
                conn.execute(
                    "UPDATE chunks SET refcount = max(0, refcount - 1) WHERE hash = ?",
                    (digest,),
                )
                released += 1

            conn.execute("UPDATE nodes SET current_rev = NULL WHERE id = ?", (node_id.bytes,))
            conn.execute("DELETE FROM revisions WHERE node_id = ?", (node_id.bytes,))
            conn.execute("DELETE FROM nodes WHERE id = ?", (node_id.bytes,))
            record(conn, "purge", target_id=node_id, payload={"chunks_released": released})

        logger.info("purged node", node=str(node_id), chunks_released=released)
        return released

    # ------------------------------------------------------------- collect --

    async def collect(self, *, grace_seconds: int = DEFAULT_GRACE_SECONDS) -> int:
        """Delete blobs nothing references any more.

        Runs after `purge` has committed, so a chunk reaching refcount zero is
        already a fact rather than an intention. Deleting the blob before
        removing the row keeps a crash safe: the row remains at refcount zero
        and the next pass simply retries.

        Args:
            grace_seconds: Only collect chunks older than this. A generous
                window is what makes a regretted purge recoverable.

        Returns:
            How many blobs were removed.
        """
        cutoff = (datetime.now(UTC) - timedelta(seconds=grace_seconds)).isoformat()
        candidates = self._vault.connection.execute(
            "SELECT hash, message_id, attach_id FROM chunks "
            "WHERE refcount = 0 AND (verified_at IS NULL OR verified_at < ?)",
            (cutoff,),
        ).fetchall()

        if grace_seconds > 0:
            # Without a recorded age a chunk cannot be shown to be old enough,
            # so it is left for a later pass rather than removed on assumption.
            candidates = [row for row in candidates if self._older_than(row[0], cutoff)]

        removed = 0
        for digest, locator, secondary in candidates:
            await self._backend.delete(BlobRef(locator=locator, secondary=secondary))
            with self._vault.connection as conn:
                conn.execute("DELETE FROM chunks WHERE hash = ? AND refcount = 0", (digest,))
            removed += 1

        if removed:
            logger.info("collected unreferenced blobs", count=removed)
        return removed

    def _older_than(self, digest: bytes, cutoff: str) -> bool:
        """Whether a chunk is old enough to collect."""
        row = self._vault.connection.execute(
            "SELECT j.ts FROM journal AS j WHERE j.op = 'purge' ORDER BY j.id DESC LIMIT 1"
        ).fetchone()
        del digest
        return bool(row and row[0] < cutoff)

    # -------------------------------------------------------------- verify --

    async def verify(self) -> list[str]:
        """Check that every referenced chunk is still retrievable.

        Returns:
            One message per problem, naming the affected file. "Verification
            failed" on a vault of thousands of chunks is not actionable; the
            name of the file that is now unrecoverable is.
        """
        rows = self._vault.connection.execute(
            """
            SELECT DISTINCT c.hash, c.message_id, c.attach_id, n.name
            FROM chunks AS c
            JOIN revision_chunks AS rc ON rc.chunk_hash = c.hash
            JOIN revisions AS r ON r.id = rc.revision_id
            JOIN nodes AS n ON n.id = r.node_id
            """
        ).fetchall()

        problems: list[str] = []
        for digest, locator, secondary, name in rows:
            ref = BlobRef(locator=locator, secondary=secondary)
            if not await self._backend.exists(ref):
                problems.append(f"{name!r} is missing chunk {bytes(digest).hex()[:12]}")

        if problems:
            logger.warning("verification found problems", count=len(problems))
        return problems

    # ------------------------------------------------------------- rebuild --

    async def rebuild(self, vault_id: uuid.UUID) -> int:
        """Reconstruct chunk records by rescanning the backend.

        This is the reason every stored blob carries an encrypted header. Given
        only the backend, the master key, and the vault id, the chunks belonging
        to a vault can be found again after the local file is lost.

        Foreign blobs are expected and skipped: a channel may hold anything, and
        the magic prefix lets most be rejected without attempting decryption at
        all.

        Args:
            vault_id: Which vault's chunks to claim.

        Returns:
            How many chunks were recovered.
        """
        recovered = 0
        async for ref in self._backend.iter_all():
            header = await self._read_header(ref)
            if header is None or header.vault_id != vault_id.bytes:
                continue

            with self._vault.connection as conn:
                conn.execute(
                    "INSERT OR IGNORE INTO backends (id, kind, label, config_enc, max_blob) "
                    "VALUES (1, ?, ?, X'', ?)",
                    (
                        self._backend.name
                        if self._backend.name in {"discord", "local"}
                        else "local",
                        self._backend.name,
                        self._backend.max_blob_size,
                    ),
                )
                conn.execute(
                    "INSERT OR IGNORE INTO chunks (hash, size, stored_size, backend_id, "
                    "message_id, attach_id, refcount) VALUES (?, ?, ?, 1, ?, ?, 0)",
                    (
                        header.plaintext_hash,
                        max(1, header.plaintext_size),
                        max(1, ref.size or header.plaintext_size),
                        ref.locator,
                        ref.secondary,
                    ),
                )
                self._recover_node(conn, header)
            recovered += 1

        logger.info("rebuild scanned backend", recovered=recovered)
        return recovered

    def _recover_node(self, conn: sqlite3.Connection, header: ChunkHeader) -> None:
        """Recreate a placeholder node for a recovered chunk.

        A chunk header names the node it belonged to, so the tree's shape is
        partially recoverable. Names are not: they live only in the vault, so a
        rebuilt node is given a name derived from its id and the user renames it
        afterwards. Full fidelity comes from the encrypted vault backup, and
        this path exists for when that is gone too.
        """
        node_id = header.node_id
        now = datetime.now(UTC).isoformat()
        existing = conn.execute("SELECT 1 FROM nodes WHERE id = ?", (node_id,)).fetchone()
        if existing is not None:
            return
        conn.execute(
            "INSERT INTO nodes (id, parent_id, name, kind, created_at, modified_at) "
            "VALUES (?, NULL, ?, 'file', ?, ?)",
            (node_id, f"recovered-{uuid.UUID(bytes=node_id).hex[:12]}.bin", now, now),
        )

    async def _read_header(self, ref: BlobRef) -> ChunkHeader | None:
        """Read and decrypt a blob's header, or None if it is not ours."""
        try:
            prefix = await self._backend.get(ref, byte_range=(0, _HEADER_PROBE))
        except OSError, ValueError:
            return None
        if not prefix.startswith(CHUNK_HEADER_MAGIC):
            return None
        try:
            return decode_chunk_header(self._master_key, prefix)
        except CryptoError:
            # Another vault's chunk, or damaged. Either way, not ours to claim.
            return None
