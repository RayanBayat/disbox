"""Choosing, creating and remembering vaults on the way in."""

from pathlib import Path

import pytest

from disbox.core.crypto import KdfParams
from disbox.core.startup import RecentVaults, create_vault, open_vault
from disbox.errors import DisboxError

PASSPHRASE = "startup test passphrase"  # noqa: S105 - fixture, not a credential
FAST = KdfParams(time_cost=1, memory_cost=8, parallelism=1)


@pytest.fixture
def recents(tmp_path: Path) -> RecentVaults:
    return RecentVaults(tmp_path / "recent.json")


def test_a_remembered_vault_is_listed(recents: RecentVaults, tmp_path: Path) -> None:
    vault = tmp_path / "one.dbx"
    vault.touch()

    recents.remember(vault)

    assert recents.paths() == [vault]


def test_the_most_recent_comes_first(recents: RecentVaults, tmp_path: Path) -> None:
    first, second = tmp_path / "a.dbx", tmp_path / "b.dbx"
    first.touch()
    second.touch()

    recents.remember(first)
    recents.remember(second)

    assert recents.paths() == [second, first]


def test_remembering_again_moves_it_to_the_front(recents: RecentVaults, tmp_path: Path) -> None:
    first, second = tmp_path / "a.dbx", tmp_path / "b.dbx"
    first.touch()
    second.touch()
    recents.remember(first)
    recents.remember(second)

    recents.remember(first)

    assert recents.paths() == [first, second]
    assert len(recents.paths()) == 2  # not duplicated


def test_vaults_that_no_longer_exist_are_dropped(recents: RecentVaults, tmp_path: Path) -> None:
    """A moved or deleted vault in the list is a dead entry, not a choice."""
    gone = tmp_path / "gone.dbx"
    gone.touch()
    recents.remember(gone)
    gone.unlink()

    assert recents.paths() == []


def test_the_list_is_capped(recents: RecentVaults, tmp_path: Path) -> None:
    for index in range(20):
        path = tmp_path / f"v{index}.dbx"
        path.touch()
        recents.remember(path)

    assert len(recents.paths()) <= 10


def test_the_list_survives_a_restart(tmp_path: Path) -> None:
    store = tmp_path / "recent.json"
    vault = tmp_path / "kept.dbx"
    vault.touch()
    RecentVaults(store).remember(vault)

    assert RecentVaults(store).paths() == [vault]


def test_a_corrupt_store_is_treated_as_empty(tmp_path: Path) -> None:
    """A broken list must not stop the application from starting."""
    store = tmp_path / "recent.json"
    store.write_text("not json at all", encoding="utf-8")

    assert RecentVaults(store).paths() == []


def test_creating_a_vault_makes_an_openable_file(tmp_path: Path) -> None:
    path = tmp_path / "fresh.dbx"

    with create_vault(path, PASSPHRASE, params=FAST) as vault:
        assert vault.is_open

    assert path.exists()


def test_a_created_vault_opens_with_its_passphrase(tmp_path: Path) -> None:
    path = tmp_path / "fresh.dbx"
    with create_vault(path, PASSPHRASE, params=FAST):
        pass

    with open_vault(path, PASSPHRASE) as vault:
        assert vault.is_open


def test_the_wrong_passphrase_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "fresh.dbx"
    with create_vault(path, PASSPHRASE, params=FAST):
        pass

    with pytest.raises(DisboxError):
        open_vault(path, "not the passphrase").close()


def test_creating_over_an_existing_vault_is_refused(tmp_path: Path) -> None:
    """Overwriting a vault would destroy every file it holds."""
    path = tmp_path / "taken.dbx"
    with create_vault(path, PASSPHRASE, params=FAST):
        pass

    with pytest.raises(DisboxError, match="already"):
        create_vault(path, PASSPHRASE, params=FAST).close()


def test_an_empty_passphrase_is_refused(tmp_path: Path) -> None:
    with pytest.raises(DisboxError, match="passphrase"):
        create_vault(tmp_path / "x.dbx", "", params=FAST).close()
