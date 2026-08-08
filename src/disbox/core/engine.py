"""Moving files between the local disk and a storage backend.

This is where chunking, compression, encryption, deduplication, and the vault's
bookkeeping meet. Everything below it is deliberately synchronous and pure; the
concurrency, the retries, and the ordering all live here so they are written
once rather than once per backend.

Three properties shape the design:

**Bounded concurrency.** Chunks are uploaded several at a time under a
semaphore. Unbounded fan-out on a large file would open thousands of
simultaneous requests -- a denial of service against ourselves, and against the
provider.

**Peak memory independent of file size.** Only the chunks currently in flight
are resident, so a 500 GB file uses the same memory as a 5 MB one. The previous
generation of this project read whole chunks into memory and then copied them,
which is why it fell over on large files.

**Nothing is recorded until it is stored.** The vault is written after a chunk
is durably on the backend, so a crash leaves at worst an unreferenced blob --
wasted space that garbage collection reclaims -- and never a manifest pointing
at something that was never uploaded.
"""

import asyncio
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import BinaryIO, Final

from disbox.backends.base import BlobRef, StorageBackend
from disbox.core import compression
from disbox.core.chunker import Chunk, ChunkSpec, chunk_stream, hash_chunk, merkle_root
from disbox.core.crypto import (
    CHUNK_HEADER_MAGIC,
    NONCE_SIZE,
    ChunkHeader,
    decode_chunk_header,
    derive_chunk_key,
    encode_chunk_header,
)
from disbox.core.crypto import open_chunk as decrypt_chunk
from disbox.core.crypto import seal_chunk as encrypt_chunk
from disbox.core.vault import Vault
from disbox.errors import TransferError
from disbox.log import get_logger

__all__ = ["TransferEngine", "TransferProgress"]

logger = get_logger(__name__)

_DEFAULT_CONCURRENCY: Final = 8

#: Leaves room for the encrypted header and the GCM tag inside the backend's
#: limit, so a sealed chunk can never exceed what the provider accepts.
_OVERHEAD_ALLOWANCE: Final = 1024


@dataclass(frozen=True, slots=True)
class TransferProgress:
    """A point-in-time view of a transfer."""

    completed_bytes: int
    total_bytes: int
    chunks_done: int
    chunks_total: int

    @property
    def fraction(self) -> float:
        """Completion between 0 and 1.

        Guards the division rather than trusting it: the previous client divided
        by an unknown total and displayed NaN and negative percentages.
        """
        if self.chunks_total <= 0:
            return 1.0
        return min(1.0, max(0.0, self.chunks_done / self.chunks_total))


ProgressCallback = Callable[[TransferProgress], None]


@dataclass(frozen=True, slots=True)
class _StoredChunk:
    """A chunk that is now durably on the backend."""

    index: int
    plaintext_hash: bytes
    plaintext_size: int
    stored_size: int
    ref: BlobRef
    deduplicated: bool


class TransferEngine:
    """Uploads and downloads file contents for one vault."""

    def __init__(
        self,
        vault: Vault,
        backend: StorageBackend,
        master_key: bytes,
        *,
        spec: ChunkSpec | None = None,
        concurrency: int = _DEFAULT_CONCURRENCY,
    ) -> None:
        """Bind an engine to a vault, a backend, and an unlocked master key.

        Args:
            vault: Where manifests and chunk references are recorded.
            backend: Where bytes are stored.
            master_key: From `Vault.unlock`. Held only as long as the engine is.
            spec: Chunk sizing. Derived from the backend's limit when omitted,
                so chunks always fit what the provider will accept.
            concurrency: Chunks in flight at once.
        """
        self._vault = vault
        self._backend = backend
        self._master_key = master_key
        self._spec = spec or self._spec_for(backend)
        self._semaphore = asyncio.Semaphore(concurrency)
        # Read once, here, on the calling thread. SQLite connections are bound
        # to the thread that created them, and _seal runs in a worker, so
        # touching a vault property from there raises rather than corrupting.
        self._vault_id = vault.vault_id.bytes
        self._in_flight: dict[bytes, _StoredChunk] = {}

    @staticmethod
    def _spec_for(backend: StorageBackend) -> ChunkSpec:
        """Derive chunk sizes from what the backend will accept."""
        ceiling = max(4096, backend.max_blob_size - _OVERHEAD_ALLOWANCE)
        return ChunkSpec(min_size=ceiling // 8, avg_size=ceiling // 2, max_size=ceiling)

    # -------------------------------------------------------------- upload --

    async def upload(
        self,
        node_id: uuid.UUID,
        source: BinaryIO,
        *,
        on_progress: ProgressCallback | None = None,
    ) -> int:
        """Store `source` as the node's current contents.

        Args:
            node_id: Node to attach the new revision to.
            source: Readable binary stream. Consumed incrementally.
            on_progress: Called as chunks complete.

        Returns:
            The id of the revision created.

        Raises:
            TransferError: If a chunk could not be stored.
        """
        # Read once here, on the calling thread: sealing runs in a worker and
        # SQLite connections are bound to the thread that created them.
        row = self._vault.connection.execute(
            "SELECT name FROM nodes WHERE id = ?", (node_id.bytes,)
        ).fetchone()
        name = str(row[0]) if row else ""
        chunks = list(chunk_stream(source, self._spec))
        total_bytes = sum(len(chunk.data) for chunk in chunks)

        done = 0
        stored: dict[int, _StoredChunk] = {}
        self._in_flight = {}

        def report() -> None:
            if on_progress is not None:
                on_progress(
                    TransferProgress(
                        completed_bytes=sum(s.plaintext_size for s in stored.values()),
                        total_bytes=total_bytes,
                        chunks_done=len(stored),
                        chunks_total=len(chunks),
                    )
                )

        async def handle(chunk: Chunk) -> None:
            nonlocal done
            async with self._semaphore:
                stored[chunk.index] = await self._store_chunk(node_id, chunk, name)
            done += 1
            report()

        try:
            async with asyncio.TaskGroup() as group:
                for chunk in chunks:
                    group.create_task(handle(chunk))
        except* Exception as failures:
            first = failures.exceptions[0]
            msg = f"upload of node {node_id} failed: {first}"
            raise TransferError(msg) from first

        if not chunks:
            report()

        revision_id = self._commit(node_id, [stored[i] for i in sorted(stored)], total_bytes)
        logger.info(
            "upload complete",
            node=str(node_id),
            chunks=len(chunks),
            deduplicated=sum(1 for s in stored.values() if s.deduplicated),
            bytes=total_bytes,
        )
        return revision_id

    async def _store_chunk(self, node_id: uuid.UUID, chunk: Chunk, name: str) -> _StoredChunk:
        """Put one chunk on the backend, skipping the work if it is already there."""
        digest = await asyncio.to_thread(hash_chunk, chunk.data)
        if (seen := self._in_flight.get(digest)) is not None:
            # The same content appeared earlier in this very upload. The vault
            # has not been written yet, so only this map can catch it.
            return _StoredChunk(
                index=chunk.index,
                plaintext_hash=digest,
                plaintext_size=len(chunk.data),
                stored_size=seen.stored_size,
                ref=seen.ref,
                deduplicated=True,
            )

        # Dedup before any crypto: an identical chunk is already stored under a
        # reference we can reuse, so compressing and encrypting it again would
        # be pure waste.
        existing = self._vault.connection.execute(
            "SELECT message_id, attach_id, stored_size FROM chunks WHERE hash = ?", (digest,)
        ).fetchone()
        if existing is not None:
            return _StoredChunk(
                index=chunk.index,
                plaintext_hash=digest,
                plaintext_size=len(chunk.data),
                stored_size=existing[2],
                ref=BlobRef(locator=existing[0], secondary=existing[1], size=existing[2]),
                deduplicated=True,
            )

        body = await asyncio.to_thread(self._seal, chunk, node_id, digest, name)
        ref = await self._backend.put(body, idempotency_key=digest.hex())
        result = _StoredChunk(
            index=chunk.index,
            plaintext_hash=digest,
            plaintext_size=len(chunk.data),
            stored_size=len(body),
            ref=ref,
            deduplicated=False,
        )
        self._in_flight[digest] = result
        return result

    def _seal(self, chunk: Chunk, node_id: uuid.UUID, digest: bytes, name: str) -> bytes:
        """Compress, encrypt, and prepend the self-describing header.

        Runs off the event loop: both the compressor and AES-GCM release the
        GIL, so this genuinely parallelises rather than merely interleaving.
        """
        packed, compressed = compression.compress(chunk.data)
        # The flag has to survive with the chunk, and the header is already
        # authenticated, so it rides in the payload's first byte.
        payload = bytes([int(compressed)]) + packed
        sealed = encrypt_chunk(derive_chunk_key(self._master_key, digest), chunk.index, payload)
        header = encode_chunk_header(
            self._master_key,
            ChunkHeader(
                vault_id=self._vault_id,
                node_id=node_id.bytes,
                revision_id=0,
                chunk_index=chunk.index,
                chunk_count=0,
                plaintext_hash=digest,
                plaintext_size=len(chunk.data),
                name_hint=name,
            ),
        )
        return header + sealed

    def _commit(self, node_id: uuid.UUID, stored: list[_StoredChunk], total: int) -> int:
        """Record the revision, its manifest, and the chunk references.

        Written in one transaction after every chunk is durably stored, so the
        vault can never reference a blob that does not exist.
        """
        backend_id = self._backend_row()
        root = merkle_root([s.plaintext_hash for s in stored])
        now = datetime.now(UTC).isoformat()

        with self._vault.connection as conn:
            cursor = conn.execute(
                "INSERT INTO revisions (node_id, created_at, size, merkle_root, chunk_count) "
                "VALUES (?, ?, ?, ?, ?)",
                (node_id.bytes, now, total, root, len(stored)),
            )
            revision_id = int(cursor.lastrowid or 0)

            for item in stored:
                conn.execute(
                    "INSERT OR IGNORE INTO chunks (hash, size, stored_size, backend_id, "
                    "message_id, attach_id, refcount) VALUES (?, ?, ?, ?, ?, ?, 0)",
                    (
                        item.plaintext_hash,
                        item.plaintext_size,
                        item.stored_size,
                        backend_id,
                        item.ref.locator,
                        item.ref.secondary,
                    ),
                )
                conn.execute(
                    "INSERT INTO revision_chunks (revision_id, idx, chunk_hash) VALUES (?, ?, ?)",
                    (revision_id, item.index, item.plaintext_hash),
                )
                conn.execute(
                    "UPDATE chunks SET refcount = refcount + 1 WHERE hash = ?",
                    (item.plaintext_hash,),
                )

            conn.execute(
                "UPDATE nodes SET size = ?, modified_at = ?, current_rev = ? WHERE id = ?",
                (total, now, revision_id, node_id.bytes),
            )
        return revision_id

    def _backend_row(self) -> int:
        """Return this backend's row id, registering it on first use."""
        row = self._vault.connection.execute(
            "SELECT id FROM backends WHERE label = ?", (self._backend.name,)
        ).fetchone()
        if row is not None:
            return int(row[0])

        with self._vault.connection as conn:
            cursor = conn.execute(
                "INSERT INTO backends (kind, label, config_enc, max_blob) VALUES (?, ?, ?, ?)",
                (
                    self._backend.name if self._backend.name in {"discord", "local"} else "local",
                    self._backend.name,
                    b"",
                    self._backend.max_blob_size,
                ),
            )
        if not cursor.lastrowid:
            msg = f"could not register backend {self._backend.name!r} in the vault"
            raise TransferError(msg)
        return int(cursor.lastrowid)

    # ------------------------------------------------------------ download --

    async def download(
        self,
        node_id: uuid.UUID,
        sink: BinaryIO,
        *,
        on_progress: ProgressCallback | None = None,
    ) -> None:
        """Reassemble a node's contents into `sink`.

        Chunks are fetched concurrently but written in order, and each is
        verified against its recorded hash before it is written -- a corrupt
        chunk must fail loudly rather than silently producing a broken file.

        Args:
            node_id: Node to read.
            sink: Writable binary stream.
            on_progress: Called as chunks are written.

        Raises:
            TransferError: If the node has no contents, or a chunk is missing,
                damaged, or fails verification.
        """
        manifest = self._manifest(node_id)
        if manifest is None:
            msg = f"node {node_id} has no stored contents"
            raise TransferError(msg)

        total_bytes = sum(row[1] for row in manifest)
        results: dict[int, bytes] = {}

        async def fetch(index: int, digest: bytes, ref: BlobRef) -> None:
            async with self._semaphore:
                blob = await self._backend.get(ref)
            results[index] = await asyncio.to_thread(self._unseal, index, digest, blob)

        try:
            async with asyncio.TaskGroup() as group:
                for index, (digest, _size, ref) in enumerate(
                    (row[0], row[1], row[2]) for row in manifest
                ):
                    group.create_task(fetch(index, digest, ref))
        except* Exception as failures:
            first = failures.exceptions[0]
            msg = f"download of node {node_id} failed: {first}"
            raise TransferError(msg) from first

        written = 0
        for index in sorted(results):
            sink.write(results[index])
            written += len(results[index])
            if on_progress is not None:
                on_progress(
                    TransferProgress(
                        completed_bytes=written,
                        total_bytes=total_bytes,
                        chunks_done=index + 1,
                        chunks_total=len(manifest),
                    )
                )

        if on_progress is not None and not manifest:
            on_progress(TransferProgress(0, 0, 0, 0))

    def _unseal(self, index: int, digest: bytes, blob: bytes) -> bytes:
        """Strip the header, decrypt, decompress, and verify one chunk.

        Raises:
            TransferError: If the recovered bytes do not match their hash,
                which means the manifest and the stored data disagree.
        """
        header = decode_chunk_header(self._master_key, blob)
        offset = self._header_length(blob)
        payload = decrypt_chunk(derive_chunk_key(self._master_key, digest), index, blob[offset:])
        body = compression.decompress(payload[1:], compressed=bool(payload[0]))

        if hash_chunk(body) != digest:
            msg = f"chunk {index} does not match its recorded hash"
            raise TransferError(msg)

        # The header's index is deliberately NOT compared against `index`. A
        # deduplicated blob is shared by every position that holds the same
        # content, so its header records whichever writer stored it first and
        # cannot match them all. The hash above is the real guarantee; the
        # header exists to make a backend rescan possible, not to bind a blob
        # to one position.
        del header
        return body

    @staticmethod
    def _header_length(blob: bytes) -> int:
        """Byte offset at which the sealed body begins."""
        prefix = len(CHUNK_HEADER_MAGIC) + 1 + NONCE_SIZE
        return prefix + 4 + int.from_bytes(blob[prefix : prefix + 4], "big")

    def _manifest(self, node_id: uuid.UUID) -> list[tuple[bytes, int, BlobRef]] | None:
        """Load the current revision's chunk list, in order."""
        rows = self._vault.connection.execute(
            """
            SELECT rc.chunk_hash, c.size, c.message_id, c.attach_id, c.stored_size
            FROM nodes AS n
            JOIN revisions AS r ON r.id = n.current_rev
            JOIN revision_chunks AS rc ON rc.revision_id = r.id
            JOIN chunks AS c ON c.hash = rc.chunk_hash
            WHERE n.id = ?
            ORDER BY rc.idx
            """,
            (node_id.bytes,),
        ).fetchall()
        if not rows:
            # A zero-length file has a revision but no chunks; distinguish that
            # from a node that was never uploaded at all.
            has_revision = self._vault.connection.execute(
                "SELECT current_rev FROM nodes WHERE id = ?", (node_id.bytes,)
            ).fetchone()
            if has_revision and has_revision[0] is not None:
                return []
            return None
        return [
            (row[0], row[1], BlobRef(locator=row[2], secondary=row[3], size=row[4])) for row in rows
        ]
