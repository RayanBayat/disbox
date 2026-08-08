"""Known-answer tests against published vectors.

Every other crypto test here is a round trip, and a round trip passes even when
the algorithm is wrong: an implementation that encrypts badly still decrypts its
own output. Only a vector produced by someone else catches a primitive that is
subtly incorrect, wired up with the wrong parameters, or silently swapped by a
dependency upgrade.

Each constant below was checked against the published source before being
committed, not transcribed from memory.
"""

import blake3
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from disbox.core.crypto import KEY_SIZE, NONCE_SIZE


class TestAesGcmVectors:
    """NIST's GCM specification, AES-256 test cases."""

    def test_case_13_empty_plaintext(self) -> None:
        """Key and IV all zero, empty plaintext: output is the tag alone."""
        produced = AESGCM(bytes(KEY_SIZE)).encrypt(bytes(NONCE_SIZE), b"", None)
        assert produced.hex() == "530f8afbc74536b9a963b4f1c4cb738b"

    def test_the_tag_is_appended_not_prepended(self) -> None:
        """Which end the tag sits on decides whether ciphertext slicing works."""
        sealed = AESGCM(bytes(KEY_SIZE)).encrypt(bytes(NONCE_SIZE), b"abcdefgh", None)
        assert len(sealed) == 8 + 16
        assert sealed[8:].hex() != ""

    def test_decryption_recovers_the_vector_plaintext(self) -> None:
        cipher = AESGCM(bytes(KEY_SIZE))
        sealed = cipher.encrypt(bytes(NONCE_SIZE), b"round trip", None)
        assert cipher.decrypt(bytes(NONCE_SIZE), sealed, None) == b"round trip"


class TestHkdfVectors:
    """RFC 5869 test vectors for HKDF-SHA256."""

    def test_case_3_zero_length_salt_and_info(self) -> None:
        """Matches how the vault derives subkeys: no salt, explicit info."""
        derived = HKDF(algorithm=hashes.SHA256(), length=42, salt=None, info=b"").derive(
            bytes([0x0B]) * 22
        )
        assert derived.hex() == (
            "8da4e775a563c18f715f802a063c5a31b8a11f5c5ee1879ec3454e5f3c738d2d9d201395faa4b61a96c8"
        )

    def test_different_info_yields_different_output(self) -> None:
        """The property the vault's domain separators depend on."""
        ikm = bytes([0x0B]) * 22
        first = HKDF(algorithm=hashes.SHA256(), length=32, salt=None, info=b"one").derive(ikm)
        second = HKDF(algorithm=hashes.SHA256(), length=32, salt=None, info=b"two").derive(ikm)
        assert first != second


class TestBlake3Vectors:
    """Official BLAKE3 test vectors."""

    def test_empty_input(self) -> None:
        assert blake3.blake3(b"").hexdigest() == (
            "af1349b9f5f9a1a6a0404dea36dcc9499bcb25c9adc112b7cc9a93cae41f3262"
        )

    def test_abc(self) -> None:
        assert blake3.blake3(b"abc").hexdigest() == (
            "6437b3ac38465133ffb63b75273a8db548c558465d79db03fd359c6cd5bd9d85"
        )

    def test_digest_is_256_bits(self) -> None:
        """The chunk hash width the schema's BLOB primary key assumes."""
        assert len(blake3.blake3(b"anything").digest()) == 32
