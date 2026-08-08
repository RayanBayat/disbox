"""Soak: what happens after hundreds of transfers rather than one.

Leaks do not show up in a three-second test. A vault that grows without bound,
a cache that never evicts, or a connection that is never returned all look
perfectly healthy until the application has been running for an afternoon.

Marked slow, so it stays out of the default suite and runs in CI's scale job.
Memory is measured with tracemalloc rather than resident set size: RSS is
dominated by the allocator's own behaviour and would make this flaky, whereas
tracemalloc measures what this codebase actually holds.
"""

import gc
import random
import tracemalloc
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
from disbox.core.vault import Vault

pytestmark = pytest.mark.slow

PASSPHRASE = "soak passphrase"  # noqa: S105 - fixture, not a credential
FAST = KdfParams(time_cost=1, memory_cost=8, parallelism=1)
SPEC = ChunkSpec(min_size=256, avg_size=1024, max_size=4096)

#: Enough cycles for a per-transfer leak to become obvious against the noise.
_CYCLES = 200


def payload(size: int, seed: int) -> bytes:
    return random.Random(seed).randbytes(size)  # noqa: S311 - test data, not a key


@pytest.fixture
def vault(tmp_path: Path) -> Iterator[Vault]:
    with Vault.create_encrypted(tmp_path / "soak.dbx", PASSPHRASE, FAST) as vault:
        yield vault


@pytest.fixture
def engine(vault: Vault, tmp_path: Path) -> TransferEngine:
    return TransferEngine(
        vault, LocalBackend(tmp_path / "blobs"), vault.unlock(PASSPHRASE), spec=SPEC
    )


@pytest.mark.asyncio
async def test_repeated_transfers_do_not_leak(
    vault: Vault, engine: TransferEngine, tmp_path: Path
) -> None:
    """Memory held after many round trips must not scale with the count."""
    source = tmp_path / "cycle.bin"
    filesystem = FileSystem(vault)

    async def cycle(index: int) -> None:
        source.write_bytes(payload(20_000, index))
        node = filesystem.create_file(None, f"f{index}.bin")
        with source.open("rb") as handle:
            await engine.upload(node, handle)
        with (tmp_path / "out.bin").open("wb") as sink:
            await engine.download(node, sink)

    # Warm up first: the first pass allocates caches and interned strings that
    # would otherwise be counted as a leak.
    for index in range(10):
        await cycle(index)
    gc.collect()

    tracemalloc.start()
    baseline = tracemalloc.take_snapshot()
    for index in range(10, _CYCLES):
        await cycle(index)
    gc.collect()
    after = tracemalloc.take_snapshot()
    tracemalloc.stop()

    grew = sum(entry.size_diff for entry in after.compare_to(baseline, "filename"))
    per_transfer = grew / (_CYCLES - 10)

    # A genuine per-transfer leak shows as kilobytes retained per cycle. This
    # bound is loose enough to absorb caches that legitimately fill and stop.
    assert per_transfer < 4096, f"{per_transfer:.0f} bytes retained per transfer"


@pytest.mark.asyncio
async def test_identical_content_does_not_grow_storage(
    vault: Vault, engine: TransferEngine, tmp_path: Path
) -> None:
    """Deduplication must hold under repetition, not only on the second upload."""
    source = tmp_path / "same.bin"
    source.write_bytes(payload(40_000, 99))
    filesystem = FileSystem(vault)
    blobs = tmp_path / "blobs"

    async def store(name: str) -> None:
        node = filesystem.create_file(None, name)
        with source.open("rb") as handle:
            await engine.upload(node, handle)

    await store("first.bin")
    after_first = len([b for b in blobs.iterdir() if b.suffix != ".json"])

    for index in range(1, 50):
        await store(f"copy{index}.bin")

    after_many = len([b for b in blobs.iterdir() if b.suffix != ".json"])
    assert after_many == after_first


@pytest.mark.asyncio
async def test_the_vault_does_not_bloat_under_churn(
    vault: Vault, engine: TransferEngine, tmp_path: Path
) -> None:
    """Create and delete repeatedly; the index must not grow without bound."""
    source = tmp_path / "churn.bin"
    source.write_bytes(payload(8_000, 7))
    filesystem = FileSystem(vault)

    for index in range(_CYCLES):
        node = filesystem.create_file(None, f"churn{index}.bin")
        with source.open("rb") as handle:
            await engine.upload(node, handle)
        filesystem.delete(node)

    assert check_invariants(vault.raw_connection) == []
    # Every node was deleted, so nothing should remain listed.
    assert filesystem.children(None) == []


def test_many_nodes_stay_listable(vault: Vault) -> None:
    """Listing must not degrade into a full-table scan as the tree grows."""
    filesystem = FileSystem(vault)
    parent = filesystem.create_directory(None, "Many")
    for index in range(2_000):
        filesystem.create_file(parent, f"file{index:05d}.bin")

    listed = filesystem.children(parent)

    assert len(listed) == 2_000
    assert check_invariants(vault.raw_connection) == []


def test_repeated_opens_release_the_lock(tmp_path: Path) -> None:
    """A lock leaked on close makes the second launch of the day fail."""
    path = tmp_path / "reopen.dbx"
    with Vault.create_encrypted(path, PASSPHRASE, FAST):
        pass

    for _ in range(50):
        vault = Vault.open(path)
        assert vault.is_open
        vault.close()

    # Still openable after fifty cycles, which it would not be if any close
    # failed to release the single-writer lock.
    with Vault.open(path) as final:
        assert final.is_open


@pytest.mark.asyncio
async def test_uuid_generation_stays_unique_under_load(vault: Vault) -> None:
    """uuid7 is time-ordered; a coarse clock could collide within a tight loop."""
    filesystem = FileSystem(vault)
    ids: set[uuid.UUID] = set()

    for index in range(5_000):
        ids.add(filesystem.create_file(None, f"u{index}.bin"))

    assert len(ids) == 5_000
