"""Opening, creating, and closing a vault -- the file everything else depends on."""

import sqlite3
from pathlib import Path

import pytest

from disbox.core.migrations import LATEST_VERSION
from disbox.core.vault import KeyMaterial, Vault
from disbox.errors import VaultError, VaultLockedError

# Placeholder key material. Real values arrive with the crypto milestone; the
# vault only needs opaque bytes of the right shape to satisfy its schema.
KEYS = KeyMaterial(
    kdf_salt=b"\x01" * 16,
    kdf_params='{"t": 3, "m": 65536, "p": 4}',
    wrapped_mk=b"\x02" * 60,
    mk_check=b"\x03" * 32,
)


@pytest.fixture
def vault_path(tmp_path: Path) -> Path:
    return tmp_path / "test.dbx"


class TestCreate:
    def test_create_produces_a_migrated_vault(self, vault_path: Path) -> None:
        with Vault.create(vault_path, KEYS) as vault:
            assert vault.schema_version == LATEST_VERSION
        assert vault_path.exists()

    def test_create_assigns_a_stable_vault_id(self, vault_path: Path) -> None:
        with Vault.create(vault_path, KEYS) as vault:
            created = vault.vault_id
        with Vault.open(vault_path) as reopened:
            assert reopened.vault_id == created

    def test_create_refuses_to_clobber_an_existing_vault(self, vault_path: Path) -> None:
        Vault.create(vault_path, KEYS).close()
        with pytest.raises(VaultError, match="already exists"):
            Vault.create(vault_path, KEYS)

    def test_create_stores_the_key_material_verbatim(self, vault_path: Path) -> None:
        with Vault.create(vault_path, KEYS) as vault:
            assert vault.key_material == KEYS


class TestOpen:
    def test_open_missing_vault_names_the_path(self, vault_path: Path) -> None:
        with pytest.raises(VaultError, match=vault_path.name):
            Vault.open(vault_path)

    def test_open_rejects_a_file_that_is_not_a_vault(self, tmp_path: Path) -> None:
        impostor = tmp_path / "notes.dbx"
        impostor.write_text("this is not a database", encoding="utf-8")
        with pytest.raises(VaultError):
            Vault.open(impostor)


class TestPragmas:
    """SPEC.md V1 requires these durability settings on every connection."""

    def test_write_ahead_logging_is_enabled(self, vault_path: Path) -> None:
        with Vault.create(vault_path, KEYS) as vault:
            mode = vault.connection.execute("PRAGMA journal_mode").fetchone()[0]
        assert mode.lower() == "wal"

    def test_synchronous_is_full(self, vault_path: Path) -> None:
        with Vault.create(vault_path, KEYS) as vault:
            assert vault.connection.execute("PRAGMA synchronous").fetchone()[0] == 2

    def test_foreign_keys_are_enforced(self, vault_path: Path) -> None:
        with Vault.create(vault_path, KEYS) as vault:
            assert vault.connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
            with pytest.raises(sqlite3.IntegrityError):
                vault.connection.execute(
                    "INSERT INTO revision_chunks (revision_id, idx, chunk_hash) "
                    "VALUES (1, 0, X'DEAD')"
                )

    def test_busy_timeout_is_set(self, vault_path: Path) -> None:
        with Vault.create(vault_path, KEYS) as vault:
            assert vault.connection.execute("PRAGMA busy_timeout").fetchone()[0] >= 5000


class TestLifecycle:
    def test_close_releases_the_lock(self, vault_path: Path) -> None:
        Vault.create(vault_path, KEYS).close()
        Vault.open(vault_path).close()  # would raise if the lock leaked

    def test_close_is_idempotent(self, vault_path: Path) -> None:
        vault = Vault.create(vault_path, KEYS)
        vault.close()
        vault.close()

    def test_a_second_open_is_rejected_while_held(self, vault_path: Path) -> None:
        with Vault.create(vault_path, KEYS), pytest.raises(VaultLockedError):
            Vault.open(vault_path)

    def test_context_manager_closes_even_when_the_body_raises(self, vault_path: Path) -> None:
        with pytest.raises(RuntimeError, match="body failed"), Vault.create(vault_path, KEYS):
            raise RuntimeError("body failed")
        Vault.open(vault_path).close()  # proves the lock was released

    def test_using_a_closed_vault_is_an_error(self, vault_path: Path) -> None:
        vault = Vault.create(vault_path, KEYS)
        vault.close()
        with pytest.raises(VaultError, match="closed"):
            _ = vault.connection
