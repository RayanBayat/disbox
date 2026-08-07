"""A vault must notice it is damaged, and be recoverable when it is."""

import shutil
import sqlite3
from collections.abc import Iterator
from contextlib import closing
from pathlib import Path

import pytest

from disbox.core.integrity import (
    check_invariants,
    full_check,
    quick_check,
    restore_from_snapshot,
)
from disbox.core.snapshots import SnapshotStore
from disbox.core.vault import Vault
from disbox.errors import IntegrityError, VaultError
from tests.unit.test_vault import KEYS

CORRUPTION_OFFSET = 4096


@pytest.fixture
def vault_path(tmp_path: Path) -> Path:
    return tmp_path / "test.dbx"


@pytest.fixture
def vault(vault_path: Path) -> Iterator[Vault]:
    with Vault.create(vault_path, KEYS) as vault:
        # Enough rows that the database spans several pages, so corrupting one
        # actually damages content rather than empty space.
        with vault.connection as conn:
            conn.executemany(
                "INSERT INTO nodes (id, parent_id, name, kind, created_at, modified_at) "
                "VALUES (?, NULL, ?, 'file', '2026-01-01', '2026-01-01')",
                [(index.to_bytes(16), f"file-{index}-{'x' * 200}") for index in range(400)],
            )
        yield vault


def corrupt(path: Path) -> None:
    """Overwrite a page of `path` with garbage."""
    with path.open("r+b") as handle:
        handle.seek(CORRUPTION_OFFSET)
        handle.write(b"\xff" * 2048)


class TestChecks:
    def test_healthy_vault_passes_quick_check(self, vault: Vault) -> None:
        quick_check(vault.connection)

    def test_healthy_vault_passes_full_check(self, vault: Vault) -> None:
        full_check(vault.connection)

    def test_healthy_vault_has_no_invariant_violations(self, vault: Vault) -> None:
        assert check_invariants(vault.connection) == []

    def test_corrupted_vault_fails_quick_check(self, vault: Vault, vault_path: Path) -> None:
        vault.close()
        corrupt(vault_path)

        # contextlib.closing, not `with sqlite3.connect(...)`: the connection's
        # own context manager commits or rolls back the transaction and leaves
        # the handle open.
        with closing(sqlite3.connect(vault_path)) as conn, pytest.raises(IntegrityError):
            quick_check(conn)


class TestInvariants:
    def test_refcount_drift_is_detected(self, vault: Vault) -> None:
        """chunks.refcount must match the real number of references."""
        with vault.connection as conn:
            conn.execute(
                "INSERT INTO backends (id, kind, label, config_enc, max_blob) "
                "VALUES (1, 'local', 'test', X'00', 1024)"
            )
            conn.execute(
                "INSERT INTO chunks (hash, size, stored_size, backend_id, message_id, "
                "attach_id, refcount) VALUES (X'AA', 10, 20, 1, 'm1', 'a1', 7)"
            )

        problems = check_invariants(vault.connection)
        assert any("refcount" in problem for problem in problems), problems

    def test_orphaned_chunk_reference_is_detected(self, vault: Vault) -> None:
        with vault.connection as conn:
            conn.execute("PRAGMA foreign_keys = OFF")
            conn.execute(
                "INSERT INTO nodes (id, parent_id, name, kind, created_at, modified_at) "
                "VALUES (X'F1', NULL, 'orphan.txt', 'file', '2026-01-01', '2026-01-01')"
            )
            conn.execute(
                "INSERT INTO revisions (id, node_id, created_at, size, merkle_root, "
                "chunk_count) VALUES (1, X'F1', '2026-01-01', 10, X'00', 1)"
            )
            conn.execute(
                "INSERT INTO revision_chunks (revision_id, idx, chunk_hash) VALUES (1, 0, X'DEAD')"
            )
            conn.execute("PRAGMA foreign_keys = ON")

        problems = check_invariants(vault.connection)
        assert problems, "a manifest pointing at a missing chunk must be reported"


class TestRestore:
    def test_restore_replaces_a_corrupt_vault(self, vault: Vault, vault_path: Path) -> None:
        store = SnapshotStore(vault_path.parent / "snapshots")
        snapshot = store.take(vault)
        original_count = vault.connection.execute("SELECT count(*) FROM nodes").fetchone()[0]
        vault.close()
        corrupt(vault_path)

        restore_from_snapshot(vault_path, snapshot.path)

        with Vault.open(vault_path) as restored:
            assert restored.connection.execute("SELECT count(*) FROM nodes").fetchone()[0] == (
                original_count
            )

    def test_corrupt_vault_is_quarantined_not_deleted(self, vault: Vault, vault_path: Path) -> None:
        """The damaged file may still be partially recoverable by hand."""
        store = SnapshotStore(vault_path.parent / "snapshots")
        snapshot = store.take(vault)
        vault.close()
        corrupt(vault_path)

        quarantined = restore_from_snapshot(vault_path, snapshot.path)

        assert quarantined is not None
        assert quarantined.exists(), "the damaged vault must be kept for forensics"
        assert quarantined != vault_path

    def test_restore_refuses_a_corrupt_snapshot(self, vault: Vault, vault_path: Path) -> None:
        store = SnapshotStore(vault_path.parent / "snapshots")
        snapshot = store.take(vault)
        vault.close()
        corrupt(snapshot.path)

        with pytest.raises(IntegrityError):
            restore_from_snapshot(vault_path, snapshot.path)

    def test_a_refused_restore_leaves_the_original_untouched(
        self, vault: Vault, vault_path: Path
    ) -> None:
        store = SnapshotStore(vault_path.parent / "snapshots")
        snapshot = store.take(vault)
        vault.close()
        before = vault_path.read_bytes()
        corrupt(snapshot.path)

        with pytest.raises(IntegrityError):
            restore_from_snapshot(vault_path, snapshot.path)
        assert vault_path.read_bytes() == before

    def test_restore_requires_the_snapshot_to_exist(self, vault_path: Path) -> None:
        with pytest.raises(VaultError):
            restore_from_snapshot(vault_path, vault_path.parent / "nope.dbx")


class TestOpenRejectsDamage:
    def test_opening_a_corrupt_vault_raises(self, vault: Vault, vault_path: Path) -> None:
        vault.close()
        corrupt(vault_path)

        with pytest.raises(VaultError):
            Vault.open(vault_path)

    def test_the_lock_is_released_when_open_rejects_a_corrupt_vault(
        self, vault: Vault, vault_path: Path, tmp_path: Path
    ) -> None:
        vault.close()
        corrupt(vault_path)
        with pytest.raises(VaultError):
            Vault.open(vault_path)

        # Restoring needs the lock; a leaked one would make the vault
        # unrecoverable precisely when recovery matters most.
        store = SnapshotStore(tmp_path / "snapshots")
        shutil.copy(vault_path, tmp_path / "unused.dbx")
        assert store.snapshots() == []
        with pytest.raises(VaultError):
            Vault.open(vault_path)
