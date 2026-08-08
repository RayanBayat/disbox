"""A blob store backed by a directory on disk.

Exists so the whole system can be exercised without a network: the transfer
engine, dedup, garbage collection, and rebuild all run against this in tests at
full speed and with no credentials. It is also a genuinely useful backend --
a vault kept entirely on a local disk or a NAS is a reasonable thing to want.

Because it satisfies the same protocol and passes the same conformance suite as
every other backend, a bug caught here is a bug that would have appeared over
the network too, but found in milliseconds instead of seconds.
"""

import asyncio
import json
import uuid
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Final

from disbox.backends.base import BlobRef

__all__ = ["LocalBackend"]

#: No provider limit applies to a local disk, but the engine still needs a
#: number to chunk against; this matches a typical remote ceiling so behaviour
#: in tests resembles production.
_DEFAULT_MAX_BLOB: Final = 10 * 1024 * 1024

_INDEX_NAME: Final = "idempotency.json"
_STREAM_BLOCK: Final = 64 * 1024


class LocalBackend:
    """Stores blobs as files in a directory."""

    def __init__(self, root: Path, *, max_blob_size: int = _DEFAULT_MAX_BLOB) -> None:
        """Store blobs beneath `root`, creating it if needed."""
        self._root = root
        self._max_blob_size = max_blob_size
        self._root.mkdir(parents=True, exist_ok=True)
        self._index_path = self._root / _INDEX_NAME
        self._index: dict[str, str] = self._load_index()

    @property
    def root(self) -> Path:
        """Directory holding the blobs."""
        return self._root

    @property
    def name(self) -> str:
        """Short identifier for logs and the vault's backend row."""
        return "local"

    @property
    def max_blob_size(self) -> int:
        """Largest blob accepted."""
        return self._max_blob_size

    # ------------------------------------------------------------ contract --

    async def put(self, data: bytes, *, idempotency_key: str) -> BlobRef:
        """Store `data`, returning the existing reference on a repeat call.

        Raises:
            ValueError: If `data` exceeds `max_blob_size`.
        """
        if len(data) > self._max_blob_size:
            msg = f"blob of {len(data)} bytes exceeds the {self._max_blob_size} byte limit"
            raise ValueError(msg)

        if (known := self._index.get(idempotency_key)) is not None:
            return BlobRef(locator=known, size=(self._root / known).stat().st_size)

        locator = idempotency_key.replace("/", "_").replace(":", "-")
        target = self._root / locator
        # Write beside the target and rename, so a crash mid-write cannot leave
        # a truncated blob that looks complete.
        #
        # The staging name carries a unique suffix because concurrent puts of
        # the *same* key are expected: convergent encryption makes identical
        # content produce an identical idempotency key, so two in-flight chunks
        # routinely race here. A shared staging path let them interleave their
        # writes and produce a corrupt blob.
        staging = target.with_suffix(f".{uuid.uuid4().hex}.partial")
        await asyncio.to_thread(staging.write_bytes, data)
        # replace is atomic, so whichever racer lands last wins with identical
        # content; the others simply have their staging file consumed.
        await asyncio.to_thread(staging.replace, target)

        self._index[idempotency_key] = locator
        self._save_index()
        return BlobRef(locator=locator, size=len(data))

    async def get(self, ref: BlobRef, *, byte_range: tuple[int, int] | None = None) -> bytes:
        """Retrieve stored bytes, optionally a half-open range.

        Raises:
            FileNotFoundError: If the blob is gone.
        """
        path = self._root / ref.locator
        payload: bytes = await asyncio.to_thread(path.read_bytes)
        if byte_range is None:
            return payload
        start, end = byte_range
        return payload[start:end]

    async def stream(self, ref: BlobRef) -> AsyncIterator[bytes]:
        """Yield the blob in blocks."""
        path = self._root / ref.locator
        handle = await asyncio.to_thread(path.open, "rb")
        try:
            while block := await asyncio.to_thread(handle.read, _STREAM_BLOCK):
                yield block
        finally:
            await asyncio.to_thread(handle.close)

    async def delete(self, ref: BlobRef) -> None:
        """Remove a blob. Absent blobs are not an error, so retries are safe."""
        await asyncio.to_thread((self._root / ref.locator).unlink, True)
        for key, locator in list(self._index.items()):
            if locator == ref.locator:
                del self._index[key]
        self._save_index()

    async def exists(self, ref: BlobRef) -> bool:
        """Report whether the blob is still present."""
        return await asyncio.to_thread((self._root / ref.locator).is_file)

    async def iter_all(self) -> AsyncIterator[BlobRef]:
        """Enumerate stored blobs, skipping bookkeeping files."""
        for entry in sorted(self._root.iterdir()):
            if entry.is_file() and entry.name != _INDEX_NAME and entry.suffix != ".partial":
                yield BlobRef(locator=entry.name, size=entry.stat().st_size)

    async def probe(self) -> int:
        """A local disk has no negotiated limit; report the configured one."""
        return self._max_blob_size

    async def close(self) -> None:
        """Nothing is held open between calls."""
        return

    # ----------------------------------------------------------- internals --

    def _load_index(self) -> dict[str, str]:
        """Read the idempotency map, tolerating a damaged one."""
        if not self._index_path.is_file():
            return {}
        try:
            loaded = json.loads(self._index_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError, OSError:
            # Losing the map costs a duplicate upload, never correctness, so a
            # damaged file is discarded rather than made fatal.
            return {}
        return {str(k): str(v) for k, v in loaded.items()} if isinstance(loaded, dict) else {}

    def _save_index(self) -> None:
        """Persist the idempotency map."""
        self._index_path.write_text(json.dumps(self._index, indent=0), encoding="utf-8")
