"""Garbage collection, verification, and rebuilding from the backend."""

import io
import random
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest

from disbox.backends.local import LocalBackend
from disbox.core.chunker import ChunkSpec
from disbox.core.crypto import KdfParams
from disbox.core.engine import TransferEngine
from disbox.core.filesystem import FileSystem
from disbox.core.integrity import check_invariants
from disbox.core.maintenance import Maintenance
from disbox.core.vault import Vault

PASSPHRASE = "maintenance test passphrase"  # noqa: S105 - fixture, not a credential
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


@pytest.fixture
def fs(vault: Vault) -> FileSystem:
    return FileSystem(vault)


@pytest.fixture
def care(vault: Vault, backend: LocalBackend) -> Maintenance:
    return Maintenance(vault, backend, vault.unlock(PASSPHRASE))


async def store(engine: TransferEngine, fs: FileSystem, name: str, payload: bytes) -> uuid.UUID:
    node = fs.create_file(None, name)
    await engine.upload(node, io.BytesIO(payload))
    return node


class TestPurge:
    async def test_purging_a_trashed_node_drops_its_refcounts(
        self, engine: TransferEngine, fs: FileSystem, care: Maintenance, vault: Vault
    ) -> None:
        node = await store(engine, fs, "f.bin", data(20_000))
        fs.delete(node)
        await care.purge(node)

        remaining = vault.connection.execute(
            "SELECT count(*) FROM chunks WHERE refcount > 0"
        ).fetchone()[0]
        assert remaining == 0

    async def test_purging_removes_the_node_entirely(
        self, engine: TransferEngine, fs: FileSystem, care: Maintenance, vault: Vault
    ) -> None:
        node = await store(engine, fs, "f.bin", data(20_000))
        fs.delete(node)
        await care.purge(node)

        row = vault.connection.execute(
            "SELECT count(*) FROM nodes WHERE id = ?", (node.bytes,)
        ).fetchone()
        assert row[0] == 0

    async def test_purging_a_live_node_is_refused(
        self, engine: TransferEngine, fs: FileSystem, care: Maintenance
    ) -> None:
        """Purging is irreversible, so it only ever applies to trashed nodes."""
        node = await store(engine, fs, "f.bin", data(10_000))
        with pytest.raises(ValueError, match="not in the trash"):
            await care.purge(node)

    async def test_a_shared_chunk_survives_purging_one_holder(
        self, engine: TransferEngine, fs: FileSystem, care: Maintenance, backend: LocalBackend
    ) -> None:
        """The failure that would destroy a file the user still has."""
        payload = data(20_000)
        first = await store(engine, fs, "a.bin", payload)
        await store(engine, fs, "b.bin", payload)

        fs.delete(first)
        await care.purge(first)
        await care.collect(grace_seconds=0)

        assert [ref async for ref in backend.iter_all()], "shared blobs were deleted"

    async def test_the_surviving_file_still_downloads(
        self, engine: TransferEngine, fs: FileSystem, care: Maintenance
    ) -> None:
        payload = data(20_000)
        first = await store(engine, fs, "a.bin", payload)
        second = await store(engine, fs, "b.bin", payload)

        fs.delete(first)
        await care.purge(first)
        await care.collect(grace_seconds=0)

        sink = io.BytesIO()
        await engine.download(second, sink)
        assert sink.getvalue() == payload


class TestCollect:
    async def test_unreferenced_blobs_are_removed(
        self, engine: TransferEngine, fs: FileSystem, care: Maintenance, backend: LocalBackend
    ) -> None:
        node = await store(engine, fs, "f.bin", data(20_000))
        fs.delete(node)
        await care.purge(node)

        removed = await care.collect(grace_seconds=0)
        assert removed > 0
        assert [ref async for ref in backend.iter_all()] == []

    async def test_the_grace_period_protects_recent_chunks(
        self, engine: TransferEngine, fs: FileSystem, care: Maintenance, backend: LocalBackend
    ) -> None:
        """A long grace period is what makes a mistaken purge recoverable."""
        node = await store(engine, fs, "f.bin", data(20_000))
        fs.delete(node)
        await care.purge(node)

        assert await care.collect(grace_seconds=3600) == 0
        assert [ref async for ref in backend.iter_all()] != []

    async def test_collecting_twice_is_harmless(
        self, engine: TransferEngine, fs: FileSystem, care: Maintenance
    ) -> None:
        """Collection retries after a crash, so it has to be idempotent."""
        node = await store(engine, fs, "f.bin", data(20_000))
        fs.delete(node)
        await care.purge(node)

        await care.collect(grace_seconds=0)
        assert await care.collect(grace_seconds=0) == 0

    async def test_live_data_is_never_collected(
        self, engine: TransferEngine, fs: FileSystem, care: Maintenance, backend: LocalBackend
    ) -> None:
        await store(engine, fs, "keep.bin", data(20_000))
        assert await care.collect(grace_seconds=0) == 0
        assert [ref async for ref in backend.iter_all()] != []

    async def test_the_vault_stays_consistent_after_collection(
        self, engine: TransferEngine, fs: FileSystem, care: Maintenance, vault: Vault
    ) -> None:
        keep = await store(engine, fs, "keep.bin", data(20_000))
        drop = await store(engine, fs, "drop.bin", data(20_000, seed=3))
        fs.delete(drop)
        await care.purge(drop)
        await care.collect(grace_seconds=0)

        assert check_invariants(vault.connection) == []
        assert keep is not None


class TestVerify:
    async def test_an_intact_vault_reports_no_problems(
        self, engine: TransferEngine, fs: FileSystem, care: Maintenance
    ) -> None:
        await store(engine, fs, "f.bin", data(20_000))
        assert await care.verify() == []

    async def test_a_missing_blob_is_reported_with_its_file(
        self, engine: TransferEngine, fs: FileSystem, care: Maintenance, backend: LocalBackend
    ) -> None:
        """Naming the affected file is the difference between a report and a shrug."""
        await store(engine, fs, "important.bin", data(20_000))
        refs = [ref async for ref in backend.iter_all()]
        await backend.delete(refs[0])

        problems = await care.verify()
        assert problems
        assert any("important.bin" in problem for problem in problems)

    async def test_verification_covers_every_chunk(
        self, engine: TransferEngine, fs: FileSystem, care: Maintenance, backend: LocalBackend
    ) -> None:
        await store(engine, fs, "f.bin", data(30_000))
        for ref in [r async for r in backend.iter_all()]:
            await backend.delete(ref)
        assert len(await care.verify()) > 1


class TestRebuild:
    async def test_a_vault_is_rebuilt_from_the_backend_alone(
        self,
        engine: TransferEngine,
        fs: FileSystem,
        backend: LocalBackend,
        vault: Vault,
        tmp_path: Path,
    ) -> None:
        """Losing the vault file must cost a rescan, not the data.

        Chunk *contents* are fully recoverable this way. Filenames are not:
        they live only in the vault, and the chunk header carries no name hint
        yet, so rebuilt nodes get a placeholder name. Full fidelity comes from
        the encrypted vault backup; this path is for when that is gone too.
        """
        payload = data(30_000)
        await store(engine, fs, "recovered.bin", payload)
        master_key = vault.unlock(PASSPHRASE)
        vault_id = vault.vault_id
        vault.close()

        with Vault.create_encrypted(tmp_path / "rebuilt.dbx", PASSPHRASE, FAST) as rebuilt:
            care = Maintenance(rebuilt, backend, master_key)
            found = await care.rebuild(vault_id)
            assert found > 0

            chunks = rebuilt.connection.execute("SELECT count(*) FROM chunks").fetchone()[0]
            nodes = rebuilt.connection.execute("SELECT count(*) FROM nodes").fetchone()[0]
            assert chunks == found, "every scanned chunk should be recorded"
            assert nodes >= 1, "a placeholder node should stand in for the lost name"

    async def test_rebuild_ignores_blobs_from_another_vault(
        self,
        engine: TransferEngine,
        fs: FileSystem,
        backend: LocalBackend,
        vault: Vault,
        tmp_path: Path,
    ) -> None:
        await store(engine, fs, "mine.bin", data(10_000))
        master_key = vault.unlock(PASSPHRASE)
        vault.close()

        with Vault.create_encrypted(tmp_path / "other.dbx", PASSPHRASE, FAST) as other:
            care = Maintenance(other, backend, master_key)
            assert await care.rebuild(uuid.uuid7()) == 0

    async def test_rebuild_skips_foreign_blobs(
        self, backend: LocalBackend, vault: Vault, tmp_path: Path
    ) -> None:
        """A shared channel may hold anything; none of it may crash a rescan."""
        await backend.put(b"not a disbox chunk at all", idempotency_key="stranger")
        master_key = vault.unlock(PASSPHRASE)
        vault_id = vault.vault_id
        vault.close()

        with Vault.create_encrypted(tmp_path / "rebuilt.dbx", PASSPHRASE, FAST) as rebuilt:
            care = Maintenance(rebuilt, backend, master_key)
            assert await care.rebuild(vault_id) == 0
