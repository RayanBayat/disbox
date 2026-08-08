"""First run: what someone sees before they have a vault or credentials."""

from pathlib import Path

import pytest
from pytestqt.qtbot import QtBot

from disbox.core.crypto import KdfParams
from disbox.core.startup import RecentVaults, create_vault
from disbox.gui.theme.tokens import DARK
from disbox.gui.views.startup_dialog import StartupDialog

PASSPHRASE = "first run passphrase"  # noqa: S105 - fixture, not a credential
FAST = KdfParams(time_cost=1, memory_cost=8, parallelism=1)


@pytest.fixture
def recents(tmp_path: Path) -> RecentVaults:
    return RecentVaults(tmp_path / "recent.json")


def test_a_first_run_invites_you_to_create_a_vault(qtbot: QtBot, recents: RecentVaults) -> None:
    """With nothing remembered, "Open a vault" is an instruction you cannot follow."""
    dialog = StartupDialog(recents, DARK)
    qtbot.addWidget(dialog)

    assert "welcome" in dialog._heading.text().lower()


def test_a_returning_user_is_not_welcomed_again(
    qtbot: QtBot, recents: RecentVaults, tmp_path: Path
) -> None:
    known = tmp_path / "known.dbx"
    with create_vault(known, PASSPHRASE, params=FAST):
        pass
    recents.remember(known)

    dialog = StartupDialog(recents, DARK)
    qtbot.addWidget(dialog)

    assert "welcome" not in dialog._heading.text().lower()


def test_the_first_run_explains_what_a_vault_is(qtbot: QtBot, recents: RecentVaults) -> None:
    dialog = StartupDialog(recents, DARK)
    qtbot.addWidget(dialog)

    guidance = dialog.guidance_text.lower()
    assert "back" in guidance  # tells the user to back it up
    assert "vault" in guidance


def test_the_first_run_says_where_files_will_be_stored(qtbot: QtBot, recents: RecentVaults) -> None:
    """Someone with no bot token should learn that now, not at first upload."""
    dialog = StartupDialog(recents, DARK)
    qtbot.addWidget(dialog)

    assert dialog.storage_text != ""


def test_unconfigured_storage_is_named_as_local(
    qtbot: QtBot, recents: RecentVaults, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("DISBOX_BOT_TOKEN", raising=False)
    monkeypatch.delenv("DISBOX_CHANNEL_ID", raising=False)

    dialog = StartupDialog(recents, DARK, check_storage=False)
    qtbot.addWidget(dialog)

    assert "this computer" in dialog.storage_text.lower()


def test_configured_storage_is_named_as_discord(qtbot: QtBot, recents: RecentVaults) -> None:
    dialog = StartupDialog(recents, DARK, check_storage=True)
    qtbot.addWidget(dialog)

    assert "discord" in dialog.storage_text.lower()


def test_creating_without_a_passphrase_says_so(qtbot: QtBot, recents: RecentVaults) -> None:
    dialog = StartupDialog(recents, DARK)
    qtbot.addWidget(dialog)

    # No passphrase typed, so creation cannot proceed even before a path is
    # chosen; the message must name the reason rather than fail silently.
    dialog._passphrase.setText("")
    dialog.create_new(path=Path("unused.dbx"))

    assert "passphrase" in dialog.status_text.lower()
    assert dialog.vault is None


def test_creating_reports_where_the_vault_was_put(
    qtbot: QtBot, recents: RecentVaults, tmp_path: Path
) -> None:
    """The user must know the path, because it is the thing they have to keep."""
    dialog = StartupDialog(recents, DARK)
    qtbot.addWidget(dialog)
    dialog._passphrase.setText(PASSPHRASE)

    dialog.create_new(path=tmp_path / "mine.dbx", params=FAST)

    assert dialog.vault is not None
    assert "mine.dbx" in dialog.created_notice
    dialog.vault.close()


def test_a_created_vault_is_remembered(qtbot: QtBot, recents: RecentVaults, tmp_path: Path) -> None:
    dialog = StartupDialog(recents, DARK)
    qtbot.addWidget(dialog)
    dialog._passphrase.setText(PASSPHRASE)

    dialog.create_new(path=tmp_path / "kept.dbx", params=FAST)

    assert recents.paths() == [tmp_path / "kept.dbx"]
    assert dialog.vault is not None
    dialog.vault.close()
