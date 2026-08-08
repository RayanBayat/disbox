"""Content-defined chunking, hashing, and manifest verification.

Files are split on boundaries chosen by their *contents* rather than at fixed
offsets. The difference matters enormously for a system that re-uploads what
changed: inserting one byte near the start of a file shifts every fixed-offset
boundary after it, so the entire file becomes new data. Content-defined
boundaries move with the bytes around them, so an edit disturbs only the chunks
it actually touches and everything else deduplicates.

The algorithm is FastCDC. A rolling "gear" hash summarises a sliding window in
one shift and one add per byte, and a boundary is declared wherever the low bits
of that hash hit a target pattern. Because the hash depends only on recent
bytes, the same content yields the same boundary wherever it appears in a file.

Two refinements keep chunk sizes sane. A minimum skips the hash entirely until
it is passed, which is both faster and prevents a run of tiny chunks. And the
mask is tightened before the target size and loosened after -- normalized
chunking -- which pulls the distribution towards the average instead of the long
exponential tail a single mask produces.
"""

import io
from collections.abc import Iterator
from dataclasses import dataclass
from typing import BinaryIO, Final

import blake3

__all__ = [
    "Chunk",
    "ChunkSpec",
    "chunk_stream",
    "hash_chunk",
    "merkle_root",
    "verify_manifest",
]

HASH_SIZE: Final = 32

# Domain separators keep a leaf hash from ever colliding with an interior node,
# which is what stops a crafted manifest from forging a root.
_LEAF_PREFIX: Final = b"\x00"
_NODE_PREFIX: Final = b"\x01"

_MASK_BITS: Final = 64
_READ_BLOCK: Final = 1 << 20  # 1 MiB reads; boundaries are found within them


def _build_gear() -> tuple[int, ...]:
    """One pseudo-random 64-bit value per byte value.

    Generated from a fixed seed rather than shipped as a literal table: the
    values only need to be well distributed and identical across runs, and a
    derivation is far easier to audit than 256 magic constants.
    """
    table = []
    state = 0x1FE35A7BD3579BD3
    for _ in range(256):
        state = (state * 6364136223846793005 + 1442695040888963407) & 0xFFFFFFFFFFFFFFFF
        table.append(state)
    return tuple(table)


_GEAR: Final = _build_gear()


@dataclass(frozen=True, slots=True)
class ChunkSpec:
    """Chunk size bounds.

    Attributes:
        min_size: No boundary is declared before this many bytes.
        avg_size: The size the mask is tuned to produce on average.
        max_size: A boundary is forced here, whatever the content says.
    """

    min_size: int
    avg_size: int
    max_size: int

    def __post_init__(self) -> None:
        """Reject bounds that cannot produce sensible chunks."""
        if not 0 < self.min_size <= self.avg_size <= self.max_size:
            msg = f"chunk sizes must satisfy 0 < min <= avg <= max, got {self}"
            raise ValueError(msg)


@dataclass(frozen=True, slots=True)
class Chunk:
    """One piece of a file."""

    index: int
    offset: int
    data: bytes


def _mask_for(size: int) -> int:
    """Return a bit mask whose expected match interval is about `size` bytes."""
    return (1 << max(1, min(size.bit_length() - 1, _MASK_BITS - 1))) - 1


def _cut_point(buffer: bytes, spec: ChunkSpec) -> int:
    """Find where the next chunk should end within `buffer`.

    Args:
        buffer: Candidate bytes, starting at the chunk's first byte.
        spec: Size bounds to honour.

    Returns:
        The length of the next chunk. Equals ``len(buffer)`` when no boundary
        was found, which the caller treats as "read more".
    """
    length = len(buffer)
    if length <= spec.min_size:
        return length

    # Tight mask below the target, loose above: this is normalized chunking,
    # which narrows the size distribution around avg_size.
    strict = _mask_for(spec.avg_size) << 1 | 1
    loose = _mask_for(spec.avg_size) >> 1

    limit = min(length, spec.max_size)
    normal = min(spec.avg_size, limit)

    fingerprint = 0
    for position in range(spec.min_size, limit):
        fingerprint = ((fingerprint << 1) + _GEAR[buffer[position]]) & 0xFFFFFFFFFFFFFFFF
        mask = strict if position < normal else loose
        if not fingerprint & mask:
            return position + 1

    return limit


def chunk_stream(stream: BinaryIO, spec: ChunkSpec) -> Iterator[Chunk]:
    """Split `stream` into content-defined chunks.

    Args:
        stream: Any readable binary stream. Read incrementally, so a file far
            larger than memory is fine.
        spec: Size bounds.

    Yields:
        Chunks in order. Every chunk but the last is at least `min_size`; none
        exceeds `max_size`.
    """
    buffer = bytearray()
    index = 0
    offset = 0
    exhausted = False

    while True:
        # Keep enough buffered that a boundary can be found without another read.
        while not exhausted and len(buffer) < spec.max_size:
            block = stream.read(_READ_BLOCK)
            if not block:
                exhausted = True
                break
            buffer.extend(block)

        if not buffer:
            return

        size = _cut_point(bytes(buffer), spec)
        if not exhausted and size == len(buffer):
            continue  # no boundary yet and more data exists; read further

        yield Chunk(index=index, offset=offset, data=bytes(buffer[:size]))
        del buffer[:size]
        index += 1
        offset += size


def hash_chunk(data: bytes) -> bytes:
    """Return the BLAKE3 digest identifying a chunk's contents.

    BLAKE3 rather than SHA-256 because this runs over every byte stored: it is
    several times faster, and it releases the GIL, so a thread pool actually
    parallelises it.
    """
    return blake3.blake3(data).digest()


def merkle_root(hashes: list[bytes]) -> bytes:
    """Reduce a manifest of chunk hashes to a single root.

    A flat hash of the concatenation would prove the same thing, but a tree
    allows a single chunk to be proven later without holding the whole manifest,
    which matters once verification happens against a remote backend.

    Args:
        hashes: Chunk hashes, in file order.

    Returns:
        A 32-byte root. Empty input yields the hash of the empty string, so a
        zero-length file still has a well-defined root.
    """
    if not hashes:
        return blake3.blake3(b"").digest()

    level = [blake3.blake3(_LEAF_PREFIX + digest).digest() for digest in hashes]
    while len(level) > 1:
        pairs = zip(level[::2], level[1::2], strict=False)
        parents = [blake3.blake3(_NODE_PREFIX + left + right).digest() for left, right in pairs]
        if len(level) % 2:
            # Carry an unpaired node up rather than duplicating it, which would
            # make a manifest of n and one of n+1 collide.
            parents.append(level[-1])
        level = parents
    return level[0]


def verify_manifest(bodies: list[bytes], hashes: list[bytes], root: bytes) -> None:
    """Check reassembled chunks against their manifest.

    Args:
        bodies: Chunk contents, in order.
        hashes: Expected chunk hashes, in order.
        root: Expected Merkle root.

    Raises:
        ValueError: If any chunk does not match its hash, if the counts differ,
            or if the manifest does not reduce to `root`. The message names the
            offending index, because "verification failed" on a 5000-chunk file
            is not actionable.
    """
    if len(bodies) != len(hashes):
        msg = f"manifest has {len(hashes)} hashes but {len(bodies)} chunks were supplied"
        raise ValueError(msg)

    for index, (body, expected) in enumerate(zip(bodies, hashes, strict=True)):
        if hash_chunk(body) != expected:
            msg = f"chunk {index} does not match its recorded hash"
            raise ValueError(msg)

    if merkle_root(hashes) != root:
        msg = "manifest does not reduce to the expected root"
        raise ValueError(msg)


def chunk_bytes(payload: bytes, spec: ChunkSpec) -> list[Chunk]:
    """Convenience wrapper for chunking data already in memory."""
    return list(chunk_stream(io.BytesIO(payload), spec))
