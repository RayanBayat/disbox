"""Optional chunk compression, applied only where it earns its cost.

Compressing everything is a net loss for this workload. Most large files people
store are already compressed -- video, photographs, archives, installers -- and
running an entropy coder over that data burns CPU to produce output that is the
same size or slightly larger. Compressing nothing is also wrong, because backups
and text deduplicate and shrink dramatically.

So each chunk is sampled first and compressed only when the sample suggests it
will pay. The decision is recorded per chunk rather than per file, since a
single archive can contain both kinds of data.

Uses the standard library's zstd (PEP 784, new in Python 3.14), which removes
what would otherwise be a third-party dependency on the hot path.
"""

from compression import zstd
from typing import Final

__all__ = ["compress", "decompress", "is_worth_compressing"]

#: Below this, framing overhead outweighs any plausible saving.
_MIN_SIZE: Final = 512

#: Bytes sampled when estimating compressibility. Large enough to be
#: representative, small enough that the probe is far cheaper than the attempt.
_SAMPLE_SIZE: Final = 4096

#: The sample must shrink by at least this fraction to be worth proceeding.
_MIN_RATIO: Final = 0.90

#: Level 3 is zstd's default: most of the ratio for a fraction of the time of
#: the higher levels, which matters when the network is the real bottleneck.
_LEVEL: Final = 3


def is_worth_compressing(data: bytes) -> bool:
    """Estimate whether compressing `data` will actually save space.

    Compresses a sample rather than the whole input, so the decision costs a
    fraction of the work it might avoid.

    Args:
        data: The chunk under consideration.

    Returns:
        True if the sample compressed enough to justify compressing the rest.
    """
    if len(data) < _MIN_SIZE:
        return False
    sample = data[:_SAMPLE_SIZE]
    return len(zstd.compress(sample, level=_LEVEL)) < len(sample) * _MIN_RATIO


def compress(data: bytes) -> tuple[bytes, bool]:
    """Compress `data` if that is worthwhile.

    Args:
        data: Chunk contents.

    Returns:
        ``(stored_bytes, was_compressed)``. The flag must be recorded alongside
        the chunk, because the stored form is not self-describing.
    """
    if not is_worth_compressing(data):
        return data, False

    packed = zstd.compress(data, level=_LEVEL)
    # The probe is an estimate; if the whole chunk failed to shrink, keep the
    # original rather than storing something larger than what we started with.
    if len(packed) >= len(data):
        return data, False
    return packed, True


def decompress(data: bytes, *, compressed: bool) -> bytes:
    """Reverse `compress`.

    Args:
        data: Stored bytes.
        compressed: The flag returned when the chunk was stored.

    Returns:
        The original chunk contents.
    """
    return zstd.decompress(data) if compressed else data
