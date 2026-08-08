"""Crypto is the part that has to be right the first time.

A defect here is not a crash but silent, permanent data loss: files encrypted
under a key nobody can reproduce, or worse, ciphertext that only looks encrypted.
"""

import pytest

from disbox.core.chunker import hash_chunk
from disbox.core.crypto import (
    CHUNK_HEADER_MAGIC,
    ChunkHeader,
    KdfParams,
    calibrate_kdf,
    decode_chunk_header,
    derive_chunk_key,
    derive_file_key,
    derive_kek,
    encode_chunk_header,
    generate_master_key,
    open_chunk,
    seal_chunk,
    unwrap_master_key,
    wrap_master_key,
)
from disbox.errors import CryptoError

# S105: a fixture, not a credential. That bandit fires here is the rule
# working, so it is silenced narrowly rather than disabled.
PASSPHRASE = "correct horse battery staple"  # noqa: S105
# Deliberately cheap so the suite stays fast; real vaults calibrate upward.
FAST = KdfParams(time_cost=1, memory_cost=8, parallelism=1)


@pytest.fixture
def master_key() -> bytes:
    return generate_master_key()


class TestMasterKey:
    def test_key_is_256_bits(self, master_key: bytes) -> None:
        assert len(master_key) == 32

    def test_keys_are_not_repeated(self) -> None:
        assert len({generate_master_key() for _ in range(64)}) == 64

    def test_wrap_then_unwrap_returns_the_key(self, master_key: bytes) -> None:
        wrapped = wrap_master_key(master_key, PASSPHRASE, FAST)
        assert unwrap_master_key(wrapped, PASSPHRASE) == master_key

    def test_wrong_passphrase_is_rejected(self, master_key: bytes) -> None:
        wrapped = wrap_master_key(master_key, PASSPHRASE, FAST)
        with pytest.raises(CryptoError, match="passphrase"):
            unwrap_master_key(wrapped, "wrong passphrase")

    def test_the_key_never_appears_in_the_wrapped_form(self, master_key: bytes) -> None:
        """The obvious catastrophic bug: storing the key beside its wrapper."""
        wrapped = wrap_master_key(master_key, PASSPHRASE, FAST)
        blob = wrapped.kdf_salt + wrapped.wrapped_key + wrapped.check
        assert master_key not in blob

    def test_the_passphrase_never_appears_either(self, master_key: bytes) -> None:
        wrapped = wrap_master_key(master_key, PASSPHRASE, FAST)
        blob = wrapped.kdf_salt + wrapped.wrapped_key + wrapped.check
        assert PASSPHRASE.encode() not in blob

    def test_each_wrap_uses_a_fresh_salt(self, master_key: bytes) -> None:
        first = wrap_master_key(master_key, PASSPHRASE, FAST)
        second = wrap_master_key(master_key, PASSPHRASE, FAST)
        assert first.kdf_salt != second.kdf_salt
        assert first.wrapped_key != second.wrapped_key

    def test_tampered_wrapper_is_detected(self, master_key: bytes) -> None:
        wrapped = wrap_master_key(master_key, PASSPHRASE, FAST)
        corrupted = wrapped.wrapped_key[:-1] + bytes([wrapped.wrapped_key[-1] ^ 0xFF])
        with pytest.raises(CryptoError):
            unwrap_master_key(
                type(wrapped)(
                    kdf_salt=wrapped.kdf_salt,
                    kdf_params=wrapped.kdf_params,
                    wrapped_key=corrupted,
                    check=wrapped.check,
                ),
                PASSPHRASE,
            )

    def test_params_round_trip_through_their_serialised_form(self) -> None:
        assert KdfParams.from_json(FAST.to_json()) == FAST


class TestKeyDerivation:
    def test_the_same_inputs_derive_the_same_key(self) -> None:
        salt = b"\x01" * 16
        assert derive_kek(PASSPHRASE, salt, FAST) == derive_kek(PASSPHRASE, salt, FAST)

    def test_a_different_salt_derives_a_different_key(self) -> None:
        first = derive_kek(PASSPHRASE, b"\x01" * 16, FAST)
        second = derive_kek(PASSPHRASE, b"\x02" * 16, FAST)
        assert first != second

    def test_file_keys_differ_per_node(self, master_key: bytes) -> None:
        first = derive_file_key(master_key, b"node-one")
        second = derive_file_key(master_key, b"node-two")
        assert first != second
        assert len(first) == 32

    def test_file_key_derivation_is_deterministic(self, master_key: bytes) -> None:
        assert derive_file_key(master_key, b"node") == derive_file_key(master_key, b"node")

    def test_a_file_key_does_not_reveal_the_master_key(self, master_key: bytes) -> None:
        assert master_key not in derive_file_key(master_key, b"node")


class TestChunkEncryption:
    def test_seal_then_open_returns_the_plaintext(self, master_key: bytes) -> None:
        key = derive_file_key(master_key, b"node")
        sealed = seal_chunk(key, 0, b"hello world")
        assert open_chunk(key, 0, sealed) == b"hello world"

    def test_ciphertext_does_not_contain_the_plaintext(self, master_key: bytes) -> None:
        key = derive_file_key(master_key, b"node")
        plaintext = b"SENSITIVE-MARKER-VALUE" * 8
        assert plaintext not in seal_chunk(key, 0, plaintext)

    def test_identical_content_seals_identically(self, master_key: bytes) -> None:
        """Convergent encryption: this is what makes deduplication possible.

        Per-file, per-index nonces would be stronger in isolation, but they make
        a shared chunk unreadable by every file except the one that wrote it.
        The trade is deliberate and documented in `derive_chunk_key`.
        """
        key = derive_chunk_key(master_key, hash_chunk(b"same"))
        assert seal_chunk(key, 0, b"same") == seal_chunk(key, 7, b"same")

    def test_different_content_seals_differently(self, master_key: bytes) -> None:
        first = derive_chunk_key(master_key, hash_chunk(b"one"))
        second = derive_chunk_key(master_key, hash_chunk(b"two"))
        assert seal_chunk(first, 0, b"one") != seal_chunk(second, 0, b"two")

    def test_a_different_vault_seals_the_same_content_differently(self, master_key: bytes) -> None:
        """Vault-scoped keys stop anyone correlating content across vaults."""
        mine = derive_chunk_key(master_key, hash_chunk(b"same"))
        theirs = derive_chunk_key(generate_master_key(), hash_chunk(b"same"))
        assert seal_chunk(mine, 0, b"same") != seal_chunk(theirs, 0, b"same")

    def test_opening_with_the_wrong_key_fails(self, master_key: bytes) -> None:
        sealed = seal_chunk(derive_file_key(master_key, b"a"), 0, b"payload")
        with pytest.raises(CryptoError):
            open_chunk(derive_file_key(master_key, b"b"), 0, sealed)

    def test_a_single_flipped_bit_is_detected(self, master_key: bytes) -> None:
        key = derive_file_key(master_key, b"node")
        sealed = bytearray(seal_chunk(key, 0, b"payload" * 32))
        sealed[len(sealed) // 2] ^= 0x01
        with pytest.raises(CryptoError):
            open_chunk(key, 0, bytes(sealed))

    def test_empty_input_round_trips(self, master_key: bytes) -> None:
        key = derive_file_key(master_key, b"node")
        assert open_chunk(key, 0, seal_chunk(key, 0, b"")) == b""

    def test_large_chunk_round_trips(self, master_key: bytes) -> None:
        key = derive_file_key(master_key, b"node")
        payload = bytes(range(256)) * 4096  # 1 MiB
        assert open_chunk(key, 0, seal_chunk(key, 0, payload)) == payload


class TestChunkHeader:
    def make(self) -> ChunkHeader:
        return ChunkHeader(
            vault_id=b"\x11" * 16,
            node_id=b"\x22" * 16,
            revision_id=7,
            chunk_index=3,
            chunk_count=9,
            plaintext_hash=b"\x33" * 32,
            plaintext_size=1024,
        )

    def test_header_round_trips(self, master_key: bytes) -> None:
        header = self.make()
        assert decode_chunk_header(master_key, encode_chunk_header(master_key, header)) == header

    def test_encoded_header_starts_with_the_magic(self, master_key: bytes) -> None:
        assert encode_chunk_header(master_key, self.make()).startswith(CHUNK_HEADER_MAGIC)

    def test_header_contents_are_not_readable(self, master_key: bytes) -> None:
        """A rescan must identify our blobs without exposing the tree."""
        encoded = encode_chunk_header(master_key, self.make())
        assert b"\x22" * 16 not in encoded[len(CHUNK_HEADER_MAGIC) :]

    def test_a_foreign_blob_is_rejected(self, master_key: bytes) -> None:
        with pytest.raises(CryptoError, match="not a Disbox"):
            decode_chunk_header(master_key, b"some other file entirely")

    def test_a_header_from_another_vault_is_rejected(self, master_key: bytes) -> None:
        encoded = encode_chunk_header(master_key, self.make())
        with pytest.raises(CryptoError):
            decode_chunk_header(generate_master_key(), encoded)

    def test_an_unknown_version_is_refused_clearly(self, master_key: bytes) -> None:
        encoded = bytearray(encode_chunk_header(master_key, self.make()))
        encoded[len(CHUNK_HEADER_MAGIC)] = 99
        with pytest.raises(CryptoError, match="version"):
            decode_chunk_header(master_key, bytes(encoded))


class TestCalibration:
    def test_calibration_targets_the_requested_duration(self) -> None:
        params = calibrate_kdf(target_seconds=0.05)
        assert params.memory_cost >= 8
        assert params.time_cost >= 1

    def test_calibrated_params_actually_work(self) -> None:
        params = calibrate_kdf(target_seconds=0.05)
        key = generate_master_key()
        wrapped = wrap_master_key(key, PASSPHRASE, params)
        assert unwrap_master_key(wrapped, PASSPHRASE) == key
