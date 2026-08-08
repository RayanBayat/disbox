"""Measure the transfer pipeline: chunking, sealing, upload, download.

Uses the local backend, so the numbers describe this codebase rather than a
network. That is the point: a Discord measurement would be dominated by the
round trip and would say nothing about whether the chunker or the cipher is the
bottleneck.

Run from the repository root:
    uv run python docs/scripts/bench_transfer.py
"""

import asyncio
import io
import random
import shutil
import statistics
import sys
import tempfile
import time
from pathlib import Path

from disbox.backends.local import LocalBackend
from disbox.core.chunker import ChunkSpec, chunk_stream
from disbox.core.crypto import KdfParams
from disbox.core.engine import TransferEngine
from disbox.core.filesystem import FileSystem
from disbox.core.vault import Vault

PASSPHRASE = "benchmark passphrase"  # noqa: S105 - local fixture, not a credential
FAST = KdfParams(time_cost=1, memory_cost=8, parallelism=1)

#: Production defaults, so the numbers describe what users actually get.
SPEC = ChunkSpec(min_size=256 * 1024, avg_size=1024 * 1024, max_size=4 * 1024 * 1024)

SIZES = (1, 8, 64)  # MiB
REPEATS = 3


def payload(size: int) -> bytes:
    """Incompressible, undeduplicable data: the honest worst case."""
    return random.Random(size).randbytes(size)  # noqa: S311 - test data, not a key


def rate(size_bytes: int, seconds: float) -> str:
    """Throughput in MiB/s."""
    return f"{size_bytes / (1024 * 1024) / seconds:.1f}"


def bench_chunking(data: bytes) -> float:
    """Seconds to split `data` into content-defined chunks."""
    start = time.perf_counter()
    list(chunk_stream(io.BytesIO(data), SPEC))
    return time.perf_counter() - start


async def bench_round_trip(root: Path, data: bytes) -> tuple[float, float]:
    """Seconds to upload and to download `data`."""
    source = root / "input.bin"
    source.write_bytes(data)

    with Vault.create_encrypted(root / "bench.dbx", PASSPHRASE, FAST) as vault:
        engine = TransferEngine(
            vault, LocalBackend(root / "blobs"), vault.unlock(PASSPHRASE), spec=SPEC
        )
        node = FileSystem(vault).create_file(None, "payload.bin")

        start = time.perf_counter()
        with source.open("rb") as handle:
            await engine.upload(node, handle)
        upload = time.perf_counter() - start

        start = time.perf_counter()
        with (root / "output.bin").open("wb") as sink:
            await engine.download(node, sink)
        download = time.perf_counter() - start

    if (root / "output.bin").read_bytes() != data:
        msg = "the round trip did not return what went in"
        raise RuntimeError(msg)
    return upload, download


async def main() -> None:
    """Run every size and print a table."""
    sys.stdout.write(
        f"{'Size':>8} | {'Chunk':>12} | {'Upload':>12} | {'Download':>12}\n"
    )
    sys.stdout.write(f"{'-' * 8}-+-{'-' * 12}-+-{'-' * 12}-+-{'-' * 12}\n")

    for megabytes in SIZES:
        size = megabytes * 1024 * 1024
        data = payload(size)

        chunk_times = [bench_chunking(data) for _ in range(REPEATS)]
        uploads: list[float] = []
        downloads: list[float] = []
        for _ in range(REPEATS):
            root = Path(tempfile.mkdtemp())
            try:
                up, down = await bench_round_trip(root, data)
                uploads.append(up)
                downloads.append(down)
            finally:
                shutil.rmtree(root, ignore_errors=True)

        sys.stdout.write(
            f"{megabytes:>6} MiB | "
            f"{rate(size, statistics.median(chunk_times)):>8} MiB/s | "
            f"{rate(size, statistics.median(uploads)):>8} MiB/s | "
            f"{rate(size, statistics.median(downloads)):>8} MiB/s\n"
        )


if __name__ == "__main__":
    asyncio.run(main())
