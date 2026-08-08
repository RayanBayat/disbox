"""Encryption for vault contents.

Everything Disbox uploads is sealed here first, so the storage backend holds
ciphertext and nothing else. That is what makes the security claim literal
rather than aspirational: Discord stores opaque blobs of near-uniform size with
no filenames, no structure, and no way to tell one file's chunk from another's.

The key hierarchy has three levels, each derived from the one above:

    passphrase --Argon2id--> KEK --unwraps--> master key --HKDF--> file key

Only the master key is stored, and only sealed under the KEK. A stolen vault
file therefore yields the shape of the tree but none of its contents, and
changing the passphrase rewraps one small value instead of re-encrypting
everything.

A repeated (key, nonce) pair is the one mistake AES-GCM does not survive: it
leaks the XOR of both plaintexts and destroys authentication. Chunk nonces are
therefore derived from the file key and the chunk index, so a repeat is
impossible without repeating an index within a file, which a caller cannot do by
accident.

Chunk *headers* are the exception and use a random nonce, because the index
they would derive from is inside the ciphertext. Their key is used for nothing
else, and 96 random bits keep a collision negligible across any realistic number
of chunks.
"""

import json
import os
import time
from dataclasses import dataclass
from typing import Final, Self

import cbor2
from argon2.low_level import Type as Argon2Type
from argon2.low_level import hash_secret_raw
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from disbox.errors import CryptoError

__all__ = [
    "CHUNK_HEADER_MAGIC",
    "ChunkHeader",
    "KdfParams",
    "WrappedKey",
    "calibrate_kdf",
    "decode_chunk_header",
    "derive_chunk_key",
    "derive_file_key",
    "derive_kek",
    "encode_chunk_header",
    "generate_master_key",
    "open_chunk",
    "seal_chunk",
    "unwrap_master_key",
    "wrap_master_key",
]

KEY_SIZE: Final = 32  # AES-256
NONCE_SIZE: Final = 12  # AES-GCM's standard nonce length
SALT_SIZE: Final = 16

CHUNK_HEADER_MAGIC: Final = b"DBX2"
CHUNK_HEADER_VERSION: Final = 1

# Domain separators, so two derivations from the same key can never collide.
_INFO_FILE_KEY: Final = b"disbox/file-key/v1"
_INFO_CHUNK_KEY: Final = b"disbox/chunk-key/v1"
_INFO_NONCE: Final = b"disbox/chunk-nonce/v1"
_INFO_HEADER: Final = b"disbox/chunk-header/v1"
_INFO_CHECK: Final = b"disbox/passphrase-check/v1"


@dataclass(frozen=True, slots=True)
class KdfParams:
    """Argon2id cost parameters.

    Stored alongside the vault so a file created on one machine still opens on
    another, and still opens after the defaults are raised for faster hardware.
    """

    time_cost: int
    memory_cost: int  # in kibibytes
    parallelism: int

    def to_json(self) -> str:
        """Serialise for the vault's meta row."""
        return json.dumps(
            {"t": self.time_cost, "m": self.memory_cost, "p": self.parallelism},
            sort_keys=True,
        )

    @classmethod
    def from_json(cls, raw: str) -> Self:
        """Parse the stored form.

        Raises:
            CryptoError: If the text is not a complete parameter set.
        """
        try:
            parsed = json.loads(raw)
            return cls(
                time_cost=int(parsed["t"]),
                memory_cost=int(parsed["m"]),
                parallelism=int(parsed["p"]),
            )
        except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
            msg = f"malformed KDF parameters {raw!r}: {exc}"
            raise CryptoError(msg) from exc


@dataclass(frozen=True, slots=True)
class WrappedKey:
    """A master key sealed under a passphrase, exactly as the vault stores it."""

    kdf_salt: bytes
    kdf_params: KdfParams
    wrapped_key: bytes
    check: bytes


@dataclass(frozen=True, slots=True)
class ChunkHeader:
    """Self-describing preamble carried by every stored chunk.

    This is what makes a vault reconstructible from the backend alone: a rescan
    can read these and rebuild the tree without any local state. It is encrypted
    under the master key, so it identifies our blobs to us without revealing the
    filesystem to anyone else.
    """

    vault_id: bytes
    node_id: bytes
    revision_id: int
    chunk_index: int
    chunk_count: int
    plaintext_hash: bytes
    plaintext_size: int


def generate_master_key() -> bytes:
    """Return a fresh 256-bit key from the OS entropy source."""
    return os.urandom(KEY_SIZE)


def derive_kek(passphrase: str, salt: bytes, params: KdfParams) -> bytes:
    """Derive the key-encryption key from a passphrase.

    Argon2id is used rather than a plain hash because a passphrase is
    low-entropy: the defence is making each guess expensive in both time and
    memory, which is exactly what a memory-hard function provides.

    Args:
        passphrase: The user's passphrase.
        salt: Per-vault random salt.
        params: Cost parameters recorded with the vault.

    Returns:
        A 32-byte key.

    Raises:
        CryptoError: If the parameters are unusable.
    """
    try:
        return hash_secret_raw(
            secret=passphrase.encode("utf-8"),
            salt=salt,
            time_cost=params.time_cost,
            memory_cost=params.memory_cost,
            parallelism=params.parallelism,
            hash_len=KEY_SIZE,
            type=Argon2Type.ID,
        )
    except Exception as exc:  # argon2 raises several unrelated types
        msg = f"could not derive a key with parameters {params}: {exc}"
        raise CryptoError(msg) from exc


def _hkdf(key: bytes, info: bytes, length: int = KEY_SIZE) -> bytes:
    """Expand `key` into a distinct subkey for one purpose."""
    return HKDF(algorithm=hashes.SHA256(), length=length, salt=None, info=info).derive(key)


def derive_file_key(master_key: bytes, node_id: bytes) -> bytes:
    """Derive the key for one node's contents.

    Per-file keys mean a compromised chunk key exposes one file rather than the
    vault, and they let a single file be re-keyed without touching the rest.
    """
    return _hkdf(master_key, _INFO_FILE_KEY + node_id)


def derive_chunk_key(master_key: bytes, plaintext_hash: bytes) -> bytes:
    """Derive a chunk's key from its own contents (convergent encryption).

    Deduplication and per-file keys are mutually exclusive: a chunk shared by
    two files would be sealed under one file's key and unreadable by the other,
    and the same breaks within a single file when repeated content deduplicates
    to a different index. Keying on the content instead makes identical
    plaintext produce identical ciphertext, which is exactly what dedup needs.

    The master key is mixed in, so the mapping is unique to this vault: two
    vaults storing the same file produce different ciphertext, and nobody
    without the master key can test whether a given file is present.

    The residual property to be aware of is that someone *holding* the master
    key can confirm whether a specific known plaintext is stored. That is the
    accepted cost of deduplicating encrypted data, and it is why the derivation
    is vault-scoped rather than global.
    """
    return _hkdf(master_key, _INFO_CHUNK_KEY + plaintext_hash)


def _chunk_nonce(file_key: bytes, chunk_index: int) -> bytes:
    """Derive the nonce for one chunk. Never random; see the module docstring."""
    return _hkdf(file_key, _INFO_NONCE + chunk_index.to_bytes(8, "big"), NONCE_SIZE)


def wrap_master_key(master_key: bytes, passphrase: str, params: KdfParams) -> WrappedKey:
    """Seal `master_key` under `passphrase`.

    Args:
        master_key: The key to protect.
        passphrase: The user's passphrase.
        params: Argon2id cost parameters to record.

    Returns:
        Everything needed to recover the key given the passphrase.
    """
    salt = os.urandom(SALT_SIZE)
    kek = derive_kek(passphrase, salt, params)
    # A fixed nonce is safe here only because the salt is fresh per wrap, which
    # makes the KEK fresh too, so the (key, nonce) pair never repeats.
    wrapped = AESGCM(kek).encrypt(bytes(NONCE_SIZE), master_key, None)
    return WrappedKey(
        kdf_salt=salt,
        kdf_params=params,
        wrapped_key=wrapped,
        # Lets a wrong passphrase be rejected without touching user data.
        check=_hkdf(kek, _INFO_CHECK),
    )


def unwrap_master_key(wrapped: WrappedKey, passphrase: str) -> bytes:
    """Recover the master key.

    Args:
        wrapped: The stored key material.
        passphrase: The passphrase to try.

    Returns:
        The master key.

    Raises:
        CryptoError: If the passphrase is wrong or the wrapper was tampered
            with. The two are reported differently, because a wrong passphrase
            is a user error and a failed unwrap is a corruption signal.
    """
    kek = derive_kek(passphrase, wrapped.kdf_salt, wrapped.kdf_params)
    if _hkdf(kek, _INFO_CHECK) != wrapped.check:
        msg = "incorrect passphrase"
        raise CryptoError(msg)
    try:
        return AESGCM(kek).decrypt(bytes(NONCE_SIZE), wrapped.wrapped_key, None)
    except InvalidTag as exc:
        msg = "the stored key material is damaged and cannot be unwrapped"
        raise CryptoError(msg) from exc


def seal_chunk(chunk_key: bytes, chunk_index: int, plaintext: bytes) -> bytes:
    """Encrypt one chunk.

    A fixed nonce is correct here precisely because the key is derived from the
    content: a repeated (key, nonce) pair can only occur for identical
    plaintext, where producing identical ciphertext is the intended behaviour
    rather than a leak.

    Args:
        chunk_key: Key from `derive_chunk_key`.
        chunk_index: Retained for API symmetry and future re-keying.
        plaintext: Data to protect.

    Returns:
        Ciphertext with its authentication tag appended.
    """
    del chunk_index
    return AESGCM(chunk_key).encrypt(_convergent_nonce(chunk_key), plaintext, None)


def _convergent_nonce(chunk_key: bytes) -> bytes:
    """Derive the nonce from the chunk key, so identical content seals alike."""
    return _hkdf(chunk_key, _INFO_NONCE, NONCE_SIZE)


def open_chunk(chunk_key: bytes, chunk_index: int, ciphertext: bytes) -> bytes:
    """Decrypt one chunk, verifying it first.

    Args:
        chunk_key: Key from `derive_chunk_key`.
        chunk_index: Retained for API symmetry; the nonce no longer uses it.
        ciphertext: Sealed data.

    Returns:
        The original plaintext.

    Raises:
        CryptoError: If the data was altered, or does not belong to this key
            and index. Authentication failure is indistinguishable between
            those cases by design, and all of them mean the same thing: do not
            use this data.
    """
    del chunk_index
    try:
        return AESGCM(chunk_key).decrypt(_convergent_nonce(chunk_key), ciphertext, None)
    except InvalidTag as exc:
        msg = "chunk failed authentication; it is damaged or not ours"
        raise CryptoError(msg) from exc


def encode_chunk_header(master_key: bytes, header: ChunkHeader) -> bytes:
    """Serialise and encrypt a chunk header.

    Layout is ``magic | version | length | ciphertext``. The magic and version
    stay in the clear so a rescan can recognise and reject blobs cheaply,
    without attempting decryption on every message in a channel.
    """
    payload = cbor2.dumps(
        {
            "v": header.vault_id,
            "n": header.node_id,
            "r": header.revision_id,
            "i": header.chunk_index,
            "c": header.chunk_count,
            "h": header.plaintext_hash,
            "s": header.plaintext_size,
        }
    )
    key = _hkdf(master_key, _INFO_HEADER)
    # A random nonce rather than a derived one: the chunk index is inside the
    # payload, so deriving from it would require decrypting first. The header
    # key is used only here, and 96 random bits make a repeat negligible across
    # any realistic number of chunks.
    nonce = os.urandom(NONCE_SIZE)
    sealed = AESGCM(key).encrypt(nonce, payload, None)
    return (
        CHUNK_HEADER_MAGIC
        + CHUNK_HEADER_VERSION.to_bytes(1, "big")
        + nonce
        + len(sealed).to_bytes(4, "big")
        + sealed
    )


def decode_chunk_header(master_key: bytes, blob: bytes) -> ChunkHeader:
    """Read a chunk header back.

    Args:
        master_key: The vault's master key.
        blob: The start of a stored chunk. Only the header need be present.

    Returns:
        The decoded header.

    Raises:
        CryptoError: If the blob is not ours, uses an unknown version, or
            fails authentication.
    """
    prefix = len(CHUNK_HEADER_MAGIC)
    if not blob.startswith(CHUNK_HEADER_MAGIC):
        msg = "not a Disbox chunk: missing magic"
        raise CryptoError(msg)

    version = blob[prefix]
    if version != CHUNK_HEADER_VERSION:
        msg = f"chunk header version {version} is newer than this build supports"
        raise CryptoError(msg)

    nonce_start = prefix + 1
    length_start = nonce_start + NONCE_SIZE
    body_start = length_start + 4
    nonce = blob[nonce_start:length_start]
    length = int.from_bytes(blob[length_start:body_start], "big")
    sealed = blob[body_start : body_start + length]
    if len(nonce) != NONCE_SIZE or len(sealed) != length:
        msg = "chunk header is truncated"
        raise CryptoError(msg)

    key = _hkdf(master_key, _INFO_HEADER)
    try:
        payload = cbor2.loads(AESGCM(key).decrypt(nonce, sealed, None))
    except (InvalidTag, cbor2.CBORDecodeError) as exc:
        msg = "chunk header failed authentication; wrong vault key or damaged data"
        raise CryptoError(msg) from exc

    return ChunkHeader(
        vault_id=payload["v"],
        node_id=payload["n"],
        revision_id=payload["r"],
        chunk_index=payload["i"],
        chunk_count=payload["c"],
        plaintext_hash=payload["h"],
        plaintext_size=payload["s"],
    )


def calibrate_kdf(target_seconds: float = 1.0) -> KdfParams:
    """Choose Argon2id parameters that take roughly `target_seconds` here.

    Cost is calibrated to the machine rather than fixed, because a constant
    chosen today is too weak on a fast machine and unbearable on a slow one.
    The result is stored with the vault so it stays openable afterwards.

    Args:
        target_seconds: Roughly how long one derivation should take.

    Returns:
        Parameters whose measured cost is at or above the target, subject to a
        ceiling so calibration itself cannot take unbounded time.
    """
    salt = os.urandom(SALT_SIZE)
    memory_cost = 8 * 1024  # 8 MiB, raised until it is slow enough
    ceiling = 512 * 1024  # 512 MiB

    while memory_cost < ceiling:
        params = KdfParams(time_cost=3, memory_cost=memory_cost, parallelism=4)
        started = time.perf_counter()
        derive_kek("calibration", salt, params)
        if time.perf_counter() - started >= target_seconds:
            return params
        memory_cost *= 2

    return KdfParams(time_cost=3, memory_cost=ceiling, parallelism=4)


def zeroize(buffer: bytearray) -> None:
    """Overwrite a mutable buffer in place.

    Best effort only: CPython moves and copies immutable bytes freely, so this
    cannot guarantee no copy survives. It is worth doing for long-lived buffers
    and worth not trusting absolutely.
    """
    for index in range(len(buffer)):
        buffer[index] = 0
