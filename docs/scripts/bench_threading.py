"""Measure whether AES-GCM and BLAKE3 release the GIL under a thread pool.

The architecture in ``SPEC.md`` assumes CPU-bound chunk work can be parallelized
with a plain ``ThreadPoolExecutor`` because the underlying C extensions drop the
GIL. This script measures that assumption. Results live in ``docs/compat.md``.

Run with::

    uv run --no-project python docs/scripts/bench_threading.py
"""

import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor

import blake3
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

MIB = 1024 * 1024
CHUNK_SIZE = 8 * MIB
CHUNK_COUNT = 64
THREAD_COUNTS = (1, 2, 4, 8)

_PAYLOAD = os.urandom(CHUNK_SIZE)
_KEY = AESGCM.generate_key(bit_length=256)


def hash_and_encrypt(index: int) -> int:
    """Hash then encrypt one chunk, mirroring the real upload path.

    Args:
        index: Chunk index, used to derive a unique nonce.

    Returns:
        Total bytes produced, to keep the work from being optimized away.
    """
    digest = blake3.blake3(_PAYLOAD).digest()
    ciphertext = AESGCM(_KEY).encrypt(index.to_bytes(12, "big"), _PAYLOAD, None)
    return len(digest) + len(ciphertext)


def run(threads: int) -> float:
    """Process every chunk across `threads` workers and return elapsed seconds."""
    started = time.perf_counter()
    if threads == 1:
        for index in range(CHUNK_COUNT):
            hash_and_encrypt(index)
    else:
        with ThreadPoolExecutor(max_workers=threads) as pool:
            list(pool.map(hash_and_encrypt, range(CHUNK_COUNT)))
    return time.perf_counter() - started


def main() -> None:
    """Print a throughput and speedup table across the configured thread counts."""
    gil = sys._is_gil_enabled() if hasattr(sys, "_is_gil_enabled") else True
    total_mib = CHUNK_COUNT * CHUNK_SIZE // MIB
    sys.stdout.write(
        f"python {sys.version.split()[0]}  gil_enabled={gil}  cpus={os.cpu_count()}\n"
        f"workload: BLAKE3 + AES-256-GCM over {total_mib} MiB\n\n"
    )

    baseline: float | None = None
    for threads in THREAD_COUNTS:
        elapsed = run(threads)
        if baseline is None:
            baseline = elapsed
        sys.stdout.write(
            f"  {threads:>2} thread(s): {elapsed:6.2f}s  "
            f"{total_mib / elapsed:7.1f} MiB/s  speedup {baseline / elapsed:.2f}x\n"
        )


if __name__ == "__main__":
    main()
