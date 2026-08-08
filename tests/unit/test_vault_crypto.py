"""A vault's stored key material must be real, not a placeholder."""

from collections.abc import Iterator
from pathlib import Path

import pytest

from disbox.core.crypto import KdfParams, derive_file_key, open_chunk, seal_chunk
from disbox.core.vault import Vault
from disbox.errors import CryptoError

PASSPHRASE = "a passphrase for the test vault"  # noqa: S105 - fixture, not a credential
FAST = KdfParams(time_cost=1, memory_cost=8, parallelism=1)


@pytest.fixture
def vault_path(tmp_path: Path) -> Path:
    return tmp_path / "secure.dbx"


@pytest.fixture
def vault(vault_path: Path) -> Iterator[Vault]:
    with Vault.create_encrypted(vault_path, PASSPHRASE, FAST) as vault:
        yield vault


class TestCreation:
    def test_a_created_vault_unlocks_with_its_passphrase(self, vault: Vault) -> None:
        assert len(vault.unlock(PASSPHRASE)) == 32

    def test_the_wrong_passphrase_is_refused(self, vault: Vault) -> None:
        with pytest.raises(CryptoError, match="passphrase"):
            vault.unlock("not the passphrase")

    def test_the_key_survives_a_close_and_reopen(self, vault: Vault, vault_path: Path) -> None:
        original = vault.unlock(PASSPHRASE)
        vault.close()
        with Vault.open(vault_path) as reopened:
            assert reopened.unlock(PASSPHRASE) == original

    def test_two_vaults_never_share_a_key(self, tmp_path: Path) -> None:
        with (
            Vault.create_encrypted(tmp_path / "a.dbx", PASSPHRASE, FAST) as first,
            Vault.create_encrypted(tmp_path / "b.dbx", PASSPHRASE, FAST) as second,
        ):
            assert first.unlock(PASSPHRASE) != second.unlock(PASSPHRASE)

    def test_the_recorded_cost_parameters_are_used(self, vault: Vault) -> None:
        assert KdfParams.from_json(vault.key_material.kdf_params) == FAST


class TestSecrecy:
    def test_the_master_key_is_not_stored_in_the_clear(
        self, vault: Vault, vault_path: Path
    ) -> None:
        """The failure that would make all of this theatre."""
        master_key = vault.unlock(PASSPHRASE)
        vault.close()
        assert master_key not in vault_path.read_bytes()

    def test_the_passphrase_is_not_stored_either(self, vault: Vault, vault_path: Path) -> None:
        vault.close()
        assert PASSPHRASE.encode() not in vault_path.read_bytes()


class TestUsableForData:
    def test_the_unlocked_key_encrypts_and_decrypts(self, vault: Vault) -> None:
        """The whole point: a vault key that can actually protect a chunk."""
        file_key = derive_file_key(vault.unlock(PASSPHRASE), b"some-node")
        sealed = seal_chunk(file_key, 0, b"private contents")
        assert open_chunk(file_key, 0, sealed) == b"private contents"

    def test_a_key_from_another_vault_cannot_read_it(self, vault: Vault, tmp_path: Path) -> None:
        sealed = seal_chunk(derive_file_key(vault.unlock(PASSPHRASE), b"node"), 0, b"secret")
        with Vault.create_encrypted(tmp_path / "other.dbx", PASSPHRASE, FAST) as other:
            foreign = derive_file_key(other.unlock(PASSPHRASE), b"node")
            with pytest.raises(CryptoError):
                open_chunk(foreign, 0, sealed)
