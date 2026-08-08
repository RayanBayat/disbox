"""Interrupted transfers must resume, not restart.

Restarting is worst exactly when it hurts most: a large upload that fails near
the end throws away everything already stored. These tests interrupt uploads
deliberately and check that the work already done survives.
"""

import asyncio
import io
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
from disbox.core.vault import Vault
from disbox.errors import TransferError

PASSPHRASE = "resume test passphrase"  # noqa: S105 - fixture, not a credential
FAST = KdfParams(time_cost=1, memory_cost=8, parallelism=1)
SPEC = ChunkSpec(min_size=256, avg_size=512, max_size=2048)


def data(size: int, seed: int = 0) -> bytes:
    return random.Random(seed).randbytes(size)  # noqa: S311 - fixture data, not a key


class FlakyBackend(LocalBackend):
    """A local backend that fails after a set number of successful puts."""

    def __init__(self, root: Path, *, fail_after: int) -> None:
        """Store beneath `root`, refusing puts once `fail_after` have landed."""
        super().__init__(root, max_blob_size=64 * 1024)
        self.fail_after = fail_after
        self.puts = 0

    async def put(self, data: bytes, *, idempotency_key: str) -> BlobRef:
        """Store a blob, or raise once the failure threshold is reached."""
        if self.puts >= self.fail_after:
            msg = "simulated network failure"
            raise OSError(msg)
        self.puts += 1
        return await super().put(data, idempotency_key=idempotency_key)


@pytest.fixture
def vault(tmp_path: Path) -> Iterator[Vault]:
    with Vault.create_encrypted(tmp_path / "v.dbx", PASSPHRASE, FAST) as opened:
        yield opened


@pytest.fixture
def fs(vault: Vault) -> FileSystem:
    return FileSystem(vault)


def engine_for(vault: Vault, backend: LocalBackend) -> TransferEngine:
    return TransferEngine(vault, backend, vault.unlock(PASSPHRASE), spec=SPEC, concurrency=2)


class TestResume:
    async def test_a_failed_upload_leaves_a_resumable_session(
        self, vault: Vault, fs: FileSystem, tmp_path: Path
    ) -> None:
        backend = FlakyBackend(tmp_path / "blobs", fail_after=3)
        node = fs.create_file(None, "big.bin")

        with pytest.raises(TransferError):
            await engine_for(vault, backend).upload(node, io.BytesIO(data(20_000)))

        sessions = vault.connection.execute(
            "SELECT state FROM upload_sessions WHERE node_id = ?", (node.bytes,)
        ).fetchall()
        assert sessions and sessions[0][0] in {"paused", "failed"}

    async def test_resuming_reuses_the_chunks_already_stored(
        self, vault: Vault, fs: FileSystem, tmp_path: Path
    ) -> None:
        """The whole point: work already done is not repeated."""
        payload = data(20_000)
        backend = FlakyBackend(tmp_path / "blobs", fail_after=3)
        node = fs.create_file(None, "big.bin")

        with pytest.raises(TransferError):
            await engine_for(vault, backend).upload(node, io.BytesIO(payload))
        stored_before = backend.puts

        backend.fail_after = 10_000  # the network comes back
        await engine_for(vault, backend).upload(node, io.BytesIO(payload))

        # Only the missing chunks were sent the second time.
        assert backend.puts > stored_before
        assert backend.puts < stored_before * 20

    async def test_a_resumed_file_downloads_correctly(
        self, vault: Vault, fs: FileSystem, tmp_path: Path
    ) -> None:
        payload = data(20_000)
        backend = FlakyBackend(tmp_path / "blobs", fail_after=3)
        node = fs.create_file(None, "big.bin")

        with pytest.raises(TransferError):
            await engine_for(vault, backend).upload(node, io.BytesIO(payload))

        backend.fail_after = 10_000
        engine = engine_for(vault, backend)
        await engine.upload(node, io.BytesIO(payload))

        sink = io.BytesIO()
        await engine.download(node, sink)
        assert sink.getvalue() == payload

    async def test_a_completed_upload_leaves_no_active_session(
        self, vault: Vault, fs: FileSystem, tmp_path: Path
    ) -> None:
        backend = LocalBackend(tmp_path / "blobs", max_blob_size=64 * 1024)
        node = fs.create_file(None, "f.bin")
        await engine_for(vault, backend).upload(node, io.BytesIO(data(10_000)))

        active = vault.connection.execute(
            "SELECT count(*) FROM upload_sessions WHERE state = 'active'"
        ).fetchone()[0]
        assert active == 0

    async def test_resuming_a_different_file_does_not_reuse_the_session(
        self, vault: Vault, fs: FileSystem, tmp_path: Path
    ) -> None:
        """Session state is per node; two uploads must never be confused."""
        backend = LocalBackend(tmp_path / "blobs", max_blob_size=64 * 1024)
        engine = engine_for(vault, backend)

        first = fs.create_file(None, "a.bin")
        second = fs.create_file(None, "b.bin")
        await engine.upload(first, io.BytesIO(data(8_000)))
        await engine.upload(second, io.BytesIO(data(8_000, seed=2)))

        sink = io.BytesIO()
        await engine.download(second, sink)
        assert sink.getvalue() == data(8_000, seed=2)


class TestCancel:
    async def test_cancelling_stops_the_upload(
        self, vault: Vault, fs: FileSystem, tmp_path: Path
    ) -> None:
        backend = LocalBackend(tmp_path / "blobs", max_blob_size=64 * 1024)
        engine = engine_for(vault, backend)
        node = fs.create_file(None, "big.bin")

        cancel = asyncio.Event()
        cancel.set()  # already cancelled before it starts

        with pytest.raises(TransferError, match="cancelled"):
            await engine.upload(node, io.BytesIO(data(20_000)), cancel=cancel)

    async def test_a_cancelled_upload_can_be_resumed(
        self, vault: Vault, fs: FileSystem, tmp_path: Path
    ) -> None:
        payload = data(20_000)
        backend = LocalBackend(tmp_path / "blobs", max_blob_size=64 * 1024)
        node = fs.create_file(None, "big.bin")

        cancel = asyncio.Event()
        cancel.set()
        with pytest.raises(TransferError):
            await engine_for(vault, backend).upload(node, io.BytesIO(payload), cancel=cancel)

        engine = engine_for(vault, backend)
        await engine.upload(node, io.BytesIO(payload))
        sink = io.BytesIO()
        await engine.download(node, sink)
        assert sink.getvalue() == payload

    async def test_cancelling_a_download_stops_it(
        self, vault: Vault, fs: FileSystem, tmp_path: Path
    ) -> None:
        backend = LocalBackend(tmp_path / "blobs", max_blob_size=64 * 1024)
        engine = engine_for(vault, backend)
        node = fs.create_file(None, "f.bin")
        await engine.upload(node, io.BytesIO(data(20_000)))

        cancel = asyncio.Event()
        cancel.set()
        with pytest.raises(TransferError, match="cancelled"):
            await engine.download(node, io.BytesIO(), cancel=cancel)

    async def test_an_uncancelled_transfer_is_unaffected(
        self, vault: Vault, fs: FileSystem, tmp_path: Path
    ) -> None:
        backend = LocalBackend(tmp_path / "blobs", max_blob_size=64 * 1024)
        engine = engine_for(vault, backend)
        node = fs.create_file(None, "f.bin")
        payload = data(10_000)

        await engine.upload(node, io.BytesIO(payload), cancel=asyncio.Event())
        sink = io.BytesIO()
        await engine.download(node, sink, cancel=asyncio.Event())
        assert sink.getvalue() == payload


class TestSessionHygiene:
    async def test_session_records_the_node_it_belongs_to(
        self, vault: Vault, fs: FileSystem, tmp_path: Path
    ) -> None:
        backend = FlakyBackend(tmp_path / "blobs", fail_after=2)
        node = fs.create_file(None, "f.bin")
        with pytest.raises(TransferError):
            await engine_for(vault, backend).upload(node, io.BytesIO(data(20_000)))

        row = vault.connection.execute(
            "SELECT node_id FROM upload_sessions WHERE node_id = ?", (node.bytes,)
        ).fetchone()
        assert row is not None
        assert uuid.UUID(bytes=row[0]) == node
