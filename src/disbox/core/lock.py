"""Single-writer enforcement for a vault, backed by an OS advisory lock.

A vault is a single SQLite file that may sit in a synced folder, so two
concurrent writers -- two app instances, or one app and one CLI -- would
interleave mutations and corrupt the tree. Exactly one process may hold a vault
open for writing.

The lock is taken from the operating system rather than inferred from a PID file
because **the kernel releases it when the holding process dies**, however it
dies. A PID-based scheme has to guess whether a recorded process is still alive,
which is racy (PIDs are recycled), unreliable across hosts, and leaves users
with a vault they cannot open after a crash.

The file's *contents* are diagnostics only -- never the lock signal -- so that a
leftover file is harmless. The file is deliberately **not** deleted on release:
unlinking it would open a window where one process locks an inode that another
has already removed, letting a third create a fresh file and lock that too,
producing two simultaneous writers.
"""

import json
import os
import socket
import sys
from datetime import UTC, datetime
from pathlib import Path
from types import TracebackType
from typing import Final, Self

if sys.platform == "win32":
    import msvcrt
else:
    import fcntl

# Lock a byte past the diagnostics payload so the two never overlap; Windows
# locks byte ranges, and writing into a range we hold would be needless risk.
_LOCK_OFFSET: Final = 4096


class VaultLockedError(Exception):
    """Raised when another process already holds the vault open for writing."""


class FileLock:
    """An exclusive, process-scoped lock over a vault.

    Usable as a context manager, which is the preferred form::

        with FileLock(vault_path.with_suffix(".lock")):
            ...

    Attributes:
        path: Location of the lock file.
    """

    def __init__(self, path: Path) -> None:
        """Prepare a lock over `path`. No file is touched until `acquire`."""
        self.path = path
        self._fd: int | None = None

    @property
    def pid(self) -> int:
        """Process id used to identify this holder in diagnostics."""
        return os.getpid()

    @property
    def is_held(self) -> bool:
        """Whether this instance currently owns the lock."""
        return self._fd is not None

    def acquire(self) -> Self:
        """Take the lock.

        Returns:
            This instance, so callers may chain ``FileLock(p).acquire()``.

        Raises:
            VaultLockedError: If another process holds the vault. The message names
                the holder's pid and host so the user can find it.
            OSError: If the lock file cannot be created.
        """
        if self._fd is not None:
            return self

        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o600)
        if not _try_lock(fd):
            holder = _read_holder(fd)
            os.close(fd)
            msg = f"vault is already open by {holder}; close it there and retry"
            raise VaultLockedError(msg)

        self._fd = fd
        self._write_holder()
        return self

    def release(self) -> None:
        """Drop the lock. Safe to call when not held, and safe to call twice."""
        if self._fd is None:
            return
        fd, self._fd = self._fd, None
        try:
            _unlock(fd)
        finally:
            os.close(fd)

    def _write_holder(self) -> None:
        """Record who holds the lock, for another process to report."""
        if self._fd is None:  # pragma: no cover - guarded by callers
            return
        payload = json.dumps(
            {
                "pid": self.pid,
                "hostname": socket.gethostname(),
                "acquired_at": datetime.now(UTC).isoformat(),
            }
        ).encode("utf-8")
        os.lseek(self._fd, 0, os.SEEK_SET)
        os.truncate(self._fd, 0)
        os.write(self._fd, payload)
        os.fsync(self._fd)

    def __enter__(self) -> Self:
        """Acquire on entry."""
        return self.acquire()

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        """Release on exit, whether or not the block raised."""
        self.release()


def _read_holder(fd: int) -> str:
    """Describe the current holder from the lock file, for an error message."""
    try:
        os.lseek(fd, 0, os.SEEK_SET)
        raw = os.read(fd, _LOCK_OFFSET).decode("utf-8")
        info = json.loads(raw)
    except OSError, UnicodeDecodeError, json.JSONDecodeError:
        return "another process"
    pid = info.get("pid", "?")
    host = info.get("hostname", "?")
    since = info.get("acquired_at", "?")
    return f"pid {pid} on {host} (since {since})"


def _try_lock(fd: int) -> bool:
    """Attempt a non-blocking exclusive lock, reporting success."""
    try:
        if sys.platform == "win32":
            os.lseek(fd, _LOCK_OFFSET, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
        else:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return False
    return True


def _unlock(fd: int) -> None:
    """Release the OS lock held on `fd`."""
    try:
        if sys.platform == "win32":
            os.lseek(fd, _LOCK_OFFSET, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
        else:
            fcntl.flock(fd, fcntl.LOCK_UN)
    except OSError:  # pragma: no cover - the close below frees it regardless
        pass
