"""Content-defined chunking, hashing, and the Merkle manifest."""

import io
import os
import random

import pytest

from disbox.core.chunker import (
    Chunk,
    ChunkSpec,
    chunk_stream,
    hash_chunk,
    merkle_root,
    verify_manifest,
)

SPEC = ChunkSpec(min_size=512, avg_size=2048, max_size=8192)


def data(size: int, seed: int = 0) -> bytes:
    """Deterministic pseudo-random bytes, so failures are reproducible.

    S311: test fixture data, never a key or a nonce.
    """
    return random.Random(seed).randbytes(size)  # noqa: S311


def chunks_of(payload: bytes, spec: ChunkSpec = SPEC) -> list[Chunk]:
    return list(chunk_stream(io.BytesIO(payload), spec))


class TestChunkBoundaries:
    def test_reassembly_reproduces_the_input(self) -> None:
        """The property everything else depends on."""
        payload = data(200_000)
        assert b"".join(c.data for c in chunks_of(payload)) == payload

    def test_offsets_are_contiguous_and_ordered(self) -> None:
        produced = chunks_of(data(120_000))
        expected_offset = 0
        for index, chunk in enumerate(produced):
            assert chunk.index == index
            assert chunk.offset == expected_offset
            expected_offset += len(chunk.data)

    def test_chunks_respect_the_maximum(self) -> None:
        for chunk in chunks_of(data(300_000)):
            assert len(chunk.data) <= SPEC.max_size

    def test_only_the_last_chunk_may_be_below_the_minimum(self) -> None:
        produced = chunks_of(data(150_000))
        for chunk in produced[:-1]:
            assert len(chunk.data) >= SPEC.min_size

    def test_chunking_is_deterministic(self) -> None:
        payload = data(80_000)
        assert [len(c.data) for c in chunks_of(payload)] == [
            len(c.data) for c in chunks_of(payload)
        ]

    def test_average_size_lands_near_the_target(self) -> None:
        produced = chunks_of(data(400_000))
        average = sum(len(c.data) for c in produced) / len(produced)
        assert SPEC.avg_size * 0.4 < average < SPEC.avg_size * 2.5, average


class TestEdgeCases:
    def test_empty_input_produces_no_chunks(self) -> None:
        assert chunks_of(b"") == []

    def test_input_smaller_than_the_minimum_is_one_chunk(self) -> None:
        produced = chunks_of(b"tiny")
        assert len(produced) == 1
        assert produced[0].data == b"tiny"

    def test_highly_repetitive_input_still_terminates(self) -> None:
        """Uniform data offers no natural boundaries; the maximum must force them."""
        produced = chunks_of(b"\x00" * 100_000)
        assert len(produced) > 1
        assert all(len(c.data) <= SPEC.max_size for c in produced)


class TestBoundaryStability:
    def test_an_insertion_near_the_start_disturbs_few_chunks(self) -> None:
        """The entire reason for content-defined chunking over fixed offsets.

        A fixed-size scheme would shift every subsequent boundary and force a
        re-upload of the whole file for a one-byte edit.
        """
        original = data(300_000)
        edited = original[:100] + b"!" + original[100:]

        before = {hash_chunk(c.data) for c in chunks_of(original)}
        after = {hash_chunk(c.data) for c in chunks_of(edited)}

        shared = before & after
        assert len(shared) > len(before) * 0.8, f"only {len(shared)} of {len(before)} survived"

    def test_appending_leaves_earlier_chunks_untouched(self) -> None:
        original = data(200_000)
        before = [hash_chunk(c.data) for c in chunks_of(original)]
        after = [hash_chunk(c.data) for c in chunks_of(original + data(5_000, seed=9))]
        assert after[: len(before) - 1] == before[: len(before) - 1]


class TestHashing:
    def test_hash_is_256_bits(self) -> None:
        assert len(hash_chunk(b"payload")) == 32

    def test_identical_data_hashes_identically(self) -> None:
        assert hash_chunk(b"same") == hash_chunk(b"same")

    def test_different_data_hashes_differently(self) -> None:
        assert hash_chunk(b"one") != hash_chunk(b"two")


class TestMerkle:
    def test_root_is_stable_for_the_same_manifest(self) -> None:
        hashes = [hash_chunk(f"chunk-{i}".encode()) for i in range(5)]
        assert merkle_root(hashes) == merkle_root(hashes)

    def test_order_matters(self) -> None:
        hashes = [hash_chunk(f"chunk-{i}".encode()) for i in range(5)]
        assert merkle_root(hashes) != merkle_root(list(reversed(hashes)))

    def test_a_changed_chunk_changes_the_root(self) -> None:
        hashes = [hash_chunk(f"chunk-{i}".encode()) for i in range(8)]
        tampered = [*hashes[:3], hash_chunk(b"substituted"), *hashes[4:]]
        assert merkle_root(hashes) != merkle_root(tampered)

    def test_empty_manifest_has_a_defined_root(self) -> None:
        assert len(merkle_root([])) == 32

    def test_single_chunk_manifest(self) -> None:
        assert len(merkle_root([hash_chunk(b"only")])) == 32

    def test_odd_counts_are_handled(self) -> None:
        """A tree with an odd level must not silently drop the last node."""
        odd = [hash_chunk(bytes([i])) for i in range(7)]
        assert merkle_root(odd) != merkle_root(odd[:6])


class TestVerification:
    def test_an_intact_manifest_verifies(self) -> None:
        payload = data(60_000)
        produced = chunks_of(payload)
        hashes = [hash_chunk(c.data) for c in produced]
        verify_manifest([c.data for c in produced], hashes, merkle_root(hashes))

    def test_a_substituted_chunk_is_rejected(self) -> None:
        produced = chunks_of(data(60_000))
        hashes = [hash_chunk(c.data) for c in produced]
        root = merkle_root(hashes)

        bodies = [c.data for c in produced]
        bodies[2] = os.urandom(len(bodies[2]))
        with pytest.raises(ValueError, match="chunk 2"):
            verify_manifest(bodies, hashes, root)

    def test_a_reordered_manifest_is_rejected(self) -> None:
        produced = chunks_of(data(60_000))
        hashes = [hash_chunk(c.data) for c in produced]
        root = merkle_root(hashes)
        with pytest.raises(ValueError):
            verify_manifest([c.data for c in produced], list(reversed(hashes)), root)

    def test_a_wrong_root_is_rejected(self) -> None:
        produced = chunks_of(data(20_000))
        hashes = [hash_chunk(c.data) for c in produced]
        with pytest.raises(ValueError, match="root"):
            verify_manifest([c.data for c in produced], hashes, b"\x00" * 32)
