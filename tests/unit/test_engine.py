"""The transfer engine: files in, files out, unchanged."""

import io
import random
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest

from disbox.backends.local import LocalBackend
from disbox.core.chunker import ChunkSpec
from disbox.core.crypto import KdfParams
from disbox.core.engine import TransferEngine, TransferProgress
from disbox.core.integrity import check_invariants
from disbox.core.vault import Vault
from disbox.errors import CryptoError, TransferError

PASSPHRASE = "engine test passphrase"  # noqa: S105 - fixture, not a credential
FAST = KdfParams(time_cost=1, memory_cost=8, parallelism=1)
SPEC = ChunkSpec(min_size=256, avg_size=1024, max_size=4096)


def data(size: int, seed: int = 0) -> bytes:
    return random.Random(seed).randbytes(size)  # noqa: S311 - fixture data, not a key


@pytest.fixture
def vault(tmp_path: Path) -> Iterator[Vault]:
    with Vault.create_encrypted(tmp_path / "v.dbx", PASSPHRASE, FAST) as opened:
        yield opened


@pytest.fixture
def backend(tmp_path: Path) -> LocalBackend:
    return LocalBackend(tmp_path / "blobs", max_blob_size=64 * 1024)


@pytest.fixture
def engine(vault: Vault, backend: LocalBackend) -> TransferEngine:
    return TransferEngine(vault, backend, vault.unlock(PASSPHRASE), spec=SPEC, concurrency=4)


def make_node(vault: Vault, name: str) -> uuid.UUID:
    """Insert an empty file node and return its id."""
    node_id = uuid.uuid7()
    with vault.connection as conn:
        conn.execute(
            "INSERT INTO nodes (id, parent_id, name, kind, created_at, modified_at) "
            "VALUES (?, NULL, ?, 'file', '2026-01-01', '2026-01-01')",
            (node_id.bytes, name),
        )
    return node_id


async def store(engine: TransferEngine, vault: Vault, name: str, payload: bytes) -> uuid.UUID:
    """Create a node and upload `payload` into it."""
    node_id = make_node(vault, name)
    await engine.upload(node_id, io.BytesIO(payload))
    return node_id


class TestRoundTrip:
    async def test_a_file_comes_back_byte_identical(
        self, engine: TransferEngine, vault: Vault
    ) -> None:
        payload = data(50_000)
        node = await store(engine, vault, "f.bin", payload)
        sink = io.BytesIO()
        await engine.download(node, sink)
        assert sink.getvalue() == payload

    async def test_an_empty_file_round_trips(self, engine: TransferEngine, vault: Vault) -> None:
        node = await store(engine, vault, "empty.bin", b"")
        sink = io.BytesIO()
        await engine.download(node, sink)
        assert sink.getvalue() == b""

    async def test_a_file_smaller_than_one_chunk_round_trips(
        self, engine: TransferEngine, vault: Vault
    ) -> None:
        node = await store(engine, vault, "tiny.bin", b"hello")
        sink = io.BytesIO()
        await engine.download(node, sink)
        assert sink.getvalue() == b"hello"

    async def test_a_highly_compressible_file_round_trips(
        self, engine: TransferEngine, vault: Vault
    ) -> None:
        payload = b"repeat " * 8000
        node = await store(engine, vault, "text.bin", payload)
        sink = io.BytesIO()
        await engine.download(node, sink)
        assert sink.getvalue() == payload


class TestEncryptionAtRest:
    async def test_the_backend_never_holds_plaintext(
        self, engine: TransferEngine, vault: Vault, backend: LocalBackend
    ) -> None:
        """The claim the whole design rests on."""
        marker = b"TOP-SECRET-MARKER-STRING"
        await store(engine, vault, "s.bin", marker * 500)

        async for ref in backend.iter_all():
            assert marker not in await backend.get(ref)

    async def test_another_vaults_key_cannot_read_the_blobs(
        self, engine: TransferEngine, vault: Vault, backend: LocalBackend, tmp_path: Path
    ) -> None:
        node = await store(engine, vault, "s.bin", data(20_000))
        with Vault.create_encrypted(tmp_path / "other.dbx", PASSPHRASE, FAST) as other:
            intruder = TransferEngine(vault, backend, other.unlock(PASSPHRASE), spec=SPEC)
            with pytest.raises((CryptoError, TransferError)):
                await intruder.download(node, io.BytesIO())


class TestDeduplication:
    async def test_identical_files_share_their_chunks(
        self, engine: TransferEngine, vault: Vault, backend: LocalBackend
    ) -> None:
        payload = data(40_000)
        await store(engine, vault, "a.bin", payload)
        after_first = len([ref async for ref in backend.iter_all()])

        await store(engine, vault, "b.bin", payload)
        assert len([ref async for ref in backend.iter_all()]) == after_first

    async def test_refcounts_track_shared_chunks(
        self, engine: TransferEngine, vault: Vault
    ) -> None:
        payload = data(40_000)
        await store(engine, vault, "a.bin", payload)
        await store(engine, vault, "b.bin", payload)

        counts = vault.connection.execute("SELECT DISTINCT refcount FROM chunks").fetchall()
        assert [row[0] for row in counts] == [2]

    async def test_a_deduplicated_file_still_downloads(
        self, engine: TransferEngine, vault: Vault
    ) -> None:
        payload = data(40_000)
        await store(engine, vault, "a.bin", payload)
        node = await store(engine, vault, "b.bin", payload)
        sink = io.BytesIO()
        await engine.download(node, sink)
        assert sink.getvalue() == payload


class TestVaultRecords:
    async def test_upload_records_a_revision_and_manifest(
        self, engine: TransferEngine, vault: Vault
    ) -> None:
        await store(engine, vault, "f.bin", data(30_000))
        revisions = vault.connection.execute("SELECT count(*) FROM revisions").fetchone()[0]
        manifest = vault.connection.execute("SELECT count(*) FROM revision_chunks").fetchone()[0]
        assert revisions == 1
        assert manifest > 1

    async def test_the_node_size_is_recorded(self, engine: TransferEngine, vault: Vault) -> None:
        payload = data(30_000)
        node = await store(engine, vault, "f.bin", payload)
        row = vault.connection.execute(
            "SELECT size FROM nodes WHERE id = ?", (node.bytes,)
        ).fetchone()
        assert row[0] == len(payload)

    async def test_the_vault_stays_consistent(self, engine: TransferEngine, vault: Vault) -> None:
        await store(engine, vault, "a.bin", data(30_000))
        await store(engine, vault, "b.bin", data(30_000, seed=2))
        assert check_invariants(vault.connection) == []

    async def test_reuploading_creates_a_second_revision(
        self, engine: TransferEngine, vault: Vault
    ) -> None:
        node = await store(engine, vault, "f.bin", data(20_000))
        await engine.upload(node, io.BytesIO(data(20_000, seed=5)))
        count = vault.connection.execute(
            "SELECT count(*) FROM revisions WHERE node_id = ?", (node.bytes,)
        ).fetchone()[0]
        assert count == 2


class TestIntegrity:
    async def test_a_corrupted_chunk_is_detected(
        self, engine: TransferEngine, vault: Vault, backend: LocalBackend
    ) -> None:
        node = await store(engine, vault, "f.bin", data(30_000))
        refs = [ref async for ref in backend.iter_all()]
        (backend.root / refs[0].locator).write_bytes(b"garbage" * 100)

        with pytest.raises((CryptoError, TransferError, ValueError)):
            await engine.download(node, io.BytesIO())

    async def test_a_missing_chunk_is_reported(
        self, engine: TransferEngine, vault: Vault, backend: LocalBackend
    ) -> None:
        node = await store(engine, vault, "f.bin", data(30_000))
        refs = [ref async for ref in backend.iter_all()]
        await backend.delete(refs[0])

        with pytest.raises((OSError, TransferError, ValueError)):
            await engine.download(node, io.BytesIO())


class TestProgress:
    async def test_progress_completes(self, engine: TransferEngine, vault: Vault) -> None:
        seen: list[TransferProgress] = []
        node = make_node(vault, "f.bin")
        payload = data(40_000)
        await engine.upload(node, io.BytesIO(payload), on_progress=seen.append)

        assert seen, "no progress was reported"
        assert seen[-1].chunks_done == seen[-1].chunks_total
        assert seen[-1].completed_bytes == len(payload)

    async def test_progress_values_stay_sane(self, engine: TransferEngine, vault: Vault) -> None:
        """The old client produced NaN, negative, and >100% values."""
        seen: list[TransferProgress] = []
        node = make_node(vault, "f.bin")
        await engine.upload(node, io.BytesIO(data(40_000)), on_progress=seen.append)

        for update in seen:
            assert 0 <= update.chunks_done <= update.chunks_total
            assert 0.0 <= update.fraction <= 1.0

    async def test_download_reports_progress(self, engine: TransferEngine, vault: Vault) -> None:
        node = await store(engine, vault, "f.bin", data(40_000))
        seen: list[TransferProgress] = []
        await engine.download(node, io.BytesIO(), on_progress=seen.append)
        assert seen and seen[-1].fraction == 1.0
