"""A vault must have exactly one writer, enforced across processes."""

import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from disbox.core.lock import FileLock, VaultLockedError


def acquire_in_subprocess(lock_path: Path) -> subprocess.CompletedProcess[str]:
    """Try to take `lock_path` from a separate interpreter, reporting the outcome."""
    program = textwrap.dedent(f"""
        import sys
        from pathlib import Path
        from disbox.core.lock import FileLock, VaultLockedError

        try:
            with FileLock(Path({str(lock_path)!r})):
                sys.stdout.write("ACQUIRED")
        except VaultLockedError as exc:
            sys.stdout.write(f"BLOCKED: {{exc}}")
    """)
    return subprocess.run(  # noqa: S603 - fixed argv, no shell, test-controlled input
        [sys.executable, "-c", program],
        capture_output=True,
        text=True,
        encoding="utf-8",  # never inherit the Windows locale codepage
        check=True,
        timeout=60,
    )


class TestAcquisition:
    def test_uncontended_lock_is_acquired(self, tmp_path: Path) -> None:
        with FileLock(tmp_path / "vault.lock") as lock:
            assert lock.is_held

    def test_lock_file_persists_but_does_not_imply_ownership(self, tmp_path: Path) -> None:
        """The file is a rendezvous point; the OS lock is the actual signal.

        It is deliberately not unlinked on release: deleting it would let one
        process lock an inode another has already removed, while a third
        creates a fresh file and locks that -- two writers at once.
        """
        path = tmp_path / "vault.lock"
        with FileLock(path):
            assert path.exists()

        assert path.exists(), "the file is expected to remain after release"
        with FileLock(path) as lock:
            assert lock.is_held, "a leftover file must not block re-acquisition"

    def test_lock_is_reacquirable_after_release(self, tmp_path: Path) -> None:
        path = tmp_path / "vault.lock"
        FileLock(path).acquire().release()
        with FileLock(path) as lock:
            assert lock.is_held

    def test_release_is_idempotent(self, tmp_path: Path) -> None:
        lock = FileLock(tmp_path / "vault.lock").acquire()
        lock.release()
        lock.release()  # must not raise

    def test_double_acquire_in_one_process_is_rejected(self, tmp_path: Path) -> None:
        path = tmp_path / "vault.lock"
        with FileLock(path), pytest.raises(VaultLockedError):
            FileLock(path).acquire()


class TestDiagnostics:
    def test_holder_details_are_recorded_for_the_error_message(self, tmp_path: Path) -> None:
        path = tmp_path / "vault.lock"
        with FileLock(path):
            contents = path.read_text(encoding="utf-8")
        assert str(FileLock(path).pid) in contents or "pid" in contents.lower()

    def test_error_names_the_blocking_process(self, tmp_path: Path) -> None:
        path = tmp_path / "vault.lock"
        with FileLock(path):
            result = acquire_in_subprocess(path)
        assert result.stdout.startswith("BLOCKED"), result.stdout
        assert str(FileLock(path).pid) in result.stdout, "error must identify the holder"


class TestCrossProcess:
    def test_second_process_is_blocked_while_held(self, tmp_path: Path) -> None:
        path = tmp_path / "vault.lock"
        with FileLock(path):
            assert acquire_in_subprocess(path).stdout.startswith("BLOCKED")

    def test_second_process_succeeds_once_released(self, tmp_path: Path) -> None:
        path = tmp_path / "vault.lock"
        FileLock(path).acquire().release()
        assert acquire_in_subprocess(path).stdout == "ACQUIRED"

    def test_lock_is_freed_when_the_holder_dies_uncleanly(self, tmp_path: Path) -> None:
        """A killed process must not leave the vault permanently unopenable."""
        path = tmp_path / "vault.lock"
        program = textwrap.dedent(f"""
            import time
            from pathlib import Path
            from disbox.core.lock import FileLock
            with FileLock(Path({str(path)!r})):
                print("HELD", flush=True)
                time.sleep(30)
        """)
        # Popen as a context manager so the stdout pipe is closed even if the
        # assertions below fail; a leaked pipe surfaces as a ResourceWarning,
        # which this suite treats as an error.
        with subprocess.Popen(  # noqa: S603 - fixed argv, no shell
            [sys.executable, "-c", program],
            stdout=subprocess.PIPE,
            text=True,
            encoding="utf-8",
        ) as holder:
            assert holder.stdout is not None
            assert holder.stdout.readline().strip() == "HELD"
            holder.kill()
            holder.wait(timeout=30)

        # The OS drops the lock with the process, so no stale-file heuristic is needed.
        with FileLock(path) as lock:
            assert lock.is_held
