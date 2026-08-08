"""Chaos: what survives when transfers are interrupted and storage misbehaves.

These exist to break things. A backend that fails intermittently, a blob that
comes back corrupted, an upload cancelled halfway -- each one is a thing that
will happen in production, and the question is whether the vault is still
coherent afterwards.

`check_invariants` is the oracle throughout: whatever went wrong, the vault must
never be left describing something that is not true.
"""

import asyncio
import contextlib
import random
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest

from disbox.backends.base import BlobRef
from disbox.backends.local import LocalBackend
from disbox.core.chunker import ChunkSpec
from disbox.core.crypto import KdfParams
from disbox.core.engine import TransferEngine
from disbox.core.filesystem import FileSystem
from disbox.core.integrity import check_invariants
from disbox.core.vault import Vault
from disbox.errors import DisboxError

PASSPHRASE = "chaos passphrase"  # noqa: S105 - fixture, not a credential
FAST = KdfParams(time_cost=1, memory_cost=8, parallelism=1)
SPEC = ChunkSpec(min_size=256, avg_size=1024, max_size=4096)


def payload(size: int, seed: int = 0) -> bytes:
    return random.Random(seed).randbytes(size)  # noqa: S311 - test data, not a key


@pytest.fixture
def vault(tmp_path: Path) -> Iterator[Vault]:
    with Vault.create_encrypted(tmp_path / "chaos.dbx", PASSPHRASE, FAST) as vault:
        yield vault


@pytest.fixture
def backend(tmp_path: Path) -> LocalBackend:
    return LocalBackend(tmp_path / "blobs")


@pytest.fixture
def engine(vault: Vault, backend: LocalBackend) -> TransferEngine:
    return TransferEngine(vault, backend, vault.unlock(PASSPHRASE), spec=SPEC)


class FlakyBackend(LocalBackend):
    """A LocalBackend that fails a chosen fraction of writes.

    Subclasses rather than wraps so it satisfies the backend protocol without
    having to restate every method, which is the kind of duplication that
    silently drifts from the real interface.
    """

    def __init__(self, root: Path, *, fail_every: int) -> None:
        """Fail every `fail_every`-th put."""
        super().__init__(root)
        self._fail_every = fail_every
        self._calls = 0

    async def put(self, data: bytes, *, idempotency_key: str) -> BlobRef:
        """Store the blob, or pretend the network went away."""
        self._calls += 1
        if self._calls % self._fail_every == 0:
            msg = "the network went away"
            raise OSError(msg)
        return await super().put(data, idempotency_key=idempotency_key)


def node(vault: Vault, name: str = "victim.bin") -> uuid.UUID:
    return FileSystem(vault).create_file(None, name)


@pytest.mark.asyncio
async def test_a_cancelled_upload_leaves_the_vault_coherent(
    vault: Vault, engine: TransferEngine, tmp_path: Path
) -> None:
    source = tmp_path / "big.bin"
    source.write_bytes(payload(400_000, 1))
    target = node(vault)
    cancel = asyncio.Event()

    async def cancel_soon() -> None:
        await asyncio.sleep(0.01)
        cancel.set()

    with contextlib.suppress(DisboxError, asyncio.CancelledError), source.open("rb") as handle:
        await asyncio.gather(engine.upload(target, handle, cancel=cancel), cancel_soon())

    assert check_invariants(vault.raw_connection) == []


@pytest.mark.asyncio
async def test_a_failed_upload_does_not_claim_the_file_is_stored(
    vault: Vault, tmp_path: Path
) -> None:
    """A revision pointing at chunks that were never written is a lie."""
    flaky = FlakyBackend(tmp_path / "blobs", fail_every=2)
    engine = TransferEngine(vault, flaky, vault.unlock(PASSPHRASE), spec=SPEC)
    source = tmp_path / "doomed.bin"
    source.write_bytes(payload(200_000, 2))
    target = node(vault)

    with contextlib.suppress(DisboxError, OSError), source.open("rb") as handle:
        await engine.upload(target, handle)

    assert check_invariants(vault.raw_connection) == []


@pytest.mark.asyncio
async def test_a_corrupted_blob_is_detected_on_download(
    vault: Vault, engine: TransferEngine, tmp_path: Path
) -> None:
    """Silent corruption is the failure that matters; it must never be silent.

    Caught by AES-GCM authentication rather than by the manifest hash. The chunk
    key is derived from the recorded digest, so tampered ciphertext fails its
    authentication tag before anything is decrypted to compare.
    """
    original = payload(20_000, 3)
    source = tmp_path / "good.bin"
    source.write_bytes(original)
    target = node(vault)
    with source.open("rb") as handle:
        await engine.upload(target, handle)

    # Flip bytes in every stored blob.
    for blob in (tmp_path / "blobs").iterdir():
        if blob.is_file() and blob.suffix != ".json":
            data = bytearray(blob.read_bytes())
            for index in range(0, len(data), 64):
                data[index] ^= 0xFF
            blob.write_bytes(bytes(data))

    out = tmp_path / "out.bin"
    with pytest.raises(DisboxError), out.open("wb") as sink:
        await engine.download(target, sink)


@pytest.mark.asyncio
async def test_a_missing_blob_is_reported_not_silently_truncated(
    vault: Vault, engine: TransferEngine, tmp_path: Path
) -> None:
    source = tmp_path / "vanishing.bin"
    source.write_bytes(payload(30_000, 4))
    target = node(vault)
    with source.open("rb") as handle:
        await engine.upload(target, handle)

    for blob in (tmp_path / "blobs").iterdir():
        if blob.is_file() and blob.suffix != ".json":
            blob.unlink()

    out = tmp_path / "out.bin"
    with pytest.raises(DisboxError), out.open("wb") as sink:
        await engine.download(target, sink)


@pytest.mark.asyncio
async def test_uploading_the_same_file_twice_stores_it_once(
    vault: Vault, engine: TransferEngine, tmp_path: Path
) -> None:
    """Deduplication is a correctness property, not only a saving."""
    source = tmp_path / "twice.bin"
    source.write_bytes(payload(50_000, 5))

    for name in ("first.bin", "second.bin"):
        with source.open("rb") as handle:
            await engine.upload(node(vault, name), handle)

    stored = [b for b in (tmp_path / "blobs").iterdir() if b.suffix != ".json"]
    chunks = vault.raw_connection.execute("SELECT count(*) FROM chunks").fetchone()[0]
    assert len(stored) == chunks
    assert check_invariants(vault.raw_connection) == []


@pytest.mark.asyncio
async def test_an_interrupted_upload_can_be_retried(
    vault: Vault, backend: LocalBackend, tmp_path: Path
) -> None:
    """The second attempt must succeed, not trip over the first one's remains."""
    source = tmp_path / "retry.bin"
    source.write_bytes(payload(120_000, 6))
    target = node(vault)

    flaky = FlakyBackend(tmp_path / "blobs", fail_every=3)
    failing = TransferEngine(vault, flaky, vault.unlock(PASSPHRASE), spec=SPEC)
    with contextlib.suppress(DisboxError, OSError), source.open("rb") as handle:
        await failing.upload(target, handle)

    healthy = TransferEngine(vault, backend, vault.unlock(PASSPHRASE), spec=SPEC)
    with source.open("rb") as handle:
        await healthy.upload(target, handle)

    out = tmp_path / "out.bin"
    with out.open("wb") as sink:
        await healthy.download(target, sink)
    assert out.read_bytes() == source.read_bytes()
    assert check_invariants(vault.raw_connection) == []


@pytest.mark.asyncio
async def test_concurrent_uploads_of_identical_content_agree(
    vault: Vault, engine: TransferEngine, tmp_path: Path
) -> None:
    """Convergent encryption makes racing writers target the same blob."""
    source = tmp_path / "raced.bin"
    source.write_bytes(payload(80_000, 7))

    async def upload(name: str) -> None:
        with source.open("rb") as handle:
            await engine.upload(node(vault, name), handle)

    await asyncio.gather(*(upload(f"copy{i}.bin") for i in range(4)))

    assert check_invariants(vault.raw_connection) == []


@pytest.mark.asyncio
async def test_a_chunk_that_decrypts_to_the_wrong_bytes_is_rejected(
    vault: Vault, engine: TransferEngine, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The manifest hash guards against internal corruption, not an attacker.

    Because the chunk key is derived from the recorded digest, a blob that
    decrypts at all was sealed under that digest, so the comparison cannot be
    reached by tampering with storage. What it does catch is this codebase
    producing the wrong bytes -- a decompression bug, say -- which is simulated
    here because nothing else in the suite exercises that line.
    """
    source = tmp_path / "internal.bin"
    source.write_bytes(payload(10_000, 8))
    target = node(vault)
    with source.open("rb") as handle:
        await engine.upload(target, handle)

    monkeypatch.setattr(
        "disbox.core.engine.compression.decompress",
        lambda _data, *, compressed: b"not what was stored",  # noqa: ARG005
    )

    out = tmp_path / "out.bin"
    with pytest.raises(DisboxError, match="hash"), out.open("wb") as sink:
        await engine.download(target, sink)


def test_a_truncated_vault_file_is_refused(tmp_path: Path) -> None:
    """Opening a damaged vault must fail loudly, not half-work."""
    path = tmp_path / "torn.dbx"
    with Vault.create_encrypted(path, PASSPHRASE, FAST):
        pass

    data = bytearray(path.read_bytes())
    del data[len(data) // 2 :]
    path.write_bytes(bytes(data))

    with pytest.raises(DisboxError):
        Vault.open(path).close()
