"""Compression is applied only where it pays for itself."""

import random

from disbox.core.compression import compress, decompress, is_worth_compressing


def incompressible(size: int) -> bytes:
    """Stand-in for media that is already compressed."""
    return random.Random(1).randbytes(size)  # noqa: S311 - fixture data, not a key


class TestRoundTrip:
    def test_compressed_data_round_trips(self) -> None:
        payload = b"the same sentence over and over. " * 200
        stored, was_compressed = compress(payload)
        assert was_compressed
        assert decompress(stored, compressed=was_compressed) == payload

    def test_uncompressed_data_round_trips(self) -> None:
        payload = incompressible(4096)
        stored, was_compressed = compress(payload)
        assert decompress(stored, compressed=was_compressed) == payload

    def test_empty_input_round_trips(self) -> None:
        stored, was_compressed = compress(b"")
        assert decompress(stored, compressed=was_compressed) == b""


class TestEntropyProbe:
    def test_repetitive_data_is_worth_compressing(self) -> None:
        assert is_worth_compressing(b"aaaaaaaaaa" * 500)

    def test_random_data_is_not(self) -> None:
        """Compressing incompressible bytes costs CPU and can grow them."""
        assert not is_worth_compressing(incompressible(65536))

    def test_text_is_worth_compressing(self) -> None:
        text = ("the quick brown fox jumps over the lazy dog. " * 100).encode()
        assert is_worth_compressing(text)

    def test_tiny_input_is_skipped(self) -> None:
        """Below a few hundred bytes the framing overhead dominates."""
        assert not is_worth_compressing(b"short")


class TestSizeBehaviour:
    def test_compressible_data_actually_shrinks(self) -> None:
        payload = b"repeat me " * 2000
        stored, was_compressed = compress(payload)
        assert was_compressed
        assert len(stored) < len(payload) / 2

    def test_incompressible_data_is_never_grown(self) -> None:
        """The failure mode that makes naive compression worse than none."""
        payload = incompressible(65536)
        stored, _ = compress(payload)
        assert len(stored) <= len(payload)
