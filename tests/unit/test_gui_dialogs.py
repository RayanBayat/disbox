"""Widget-level tests for the dialogs whose logic was covered but whose UI was not."""

from collections.abc import Iterator
from pathlib import Path

import pytest
from PySide6.QtWidgets import QLineEdit
from pytestqt.qtbot import QtBot

from disbox.core.crypto import KdfParams
from disbox.core.startup import RecentVaults, create_vault
from disbox.gui.notifications import NotificationLog
from disbox.gui.theme.tokens import DARK
from disbox.gui.views.notifications_dialog import NotificationsDialog
from disbox.gui.views.startup_dialog import StartupDialog

PASSPHRASE = "dialog test passphrase"  # noqa: S105 - fixture, not a credential
FAST = KdfParams(time_cost=1, memory_cost=8, parallelism=1)


# ------------------------------------------------------------ notifications --


@pytest.fixture
def log() -> NotificationLog:
    return NotificationLog()


def names(dialog: NotificationsDialog) -> list[str]:
    return [dialog._list.item(i).text() for i in range(dialog._list.count())]


def test_the_dialog_lists_every_notice(qtbot: QtBot, log: NotificationLog) -> None:
    log.info("all good")
    log.error("it broke")

    dialog = NotificationsDialog(log, DARK)
    qtbot.addWidget(dialog)

    assert len(names(dialog)) == 2


def test_an_error_shows_its_identifier_in_the_list(qtbot: QtBot, log: NotificationLog) -> None:
    notice = log.error("it broke")

    dialog = NotificationsDialog(log, DARK)
    qtbot.addWidget(dialog)

    assert any(notice.diagnostic_id in name for name in names(dialog))


def test_an_empty_log_says_so(qtbot: QtBot, log: NotificationLog) -> None:
    dialog = NotificationsDialog(log, DARK)
    qtbot.addWidget(dialog)

    assert "nothing to report" in dialog._heading.text().lower()
    assert not dialog._copy.isEnabled()


def test_opening_the_dialog_marks_problems_read(qtbot: QtBot, log: NotificationLog) -> None:
    """The badge should stop nagging once the user has looked."""
    log.error("it broke")
    assert log.unread_problems == 1

    dialog = NotificationsDialog(log, DARK)
    qtbot.addWidget(dialog)

    assert log.unread_problems == 0


def test_copying_returns_the_quotable_text(qtbot: QtBot, log: NotificationLog) -> None:
    notice = log.error("upload failed")
    dialog = NotificationsDialog(log, DARK)
    qtbot.addWidget(dialog)
    dialog._list.setCurrentRow(0)

    copied = dialog.copy_selected()

    assert notice.diagnostic_id in copied
    assert "upload failed" in copied


def test_copying_with_no_selection_returns_nothing(qtbot: QtBot, log: NotificationLog) -> None:
    log.error("upload failed")
    dialog = NotificationsDialog(log, DARK)
    qtbot.addWidget(dialog)
    dialog._list.setCurrentRow(-1)

    assert dialog.copy_selected() == ""


def test_clearing_empties_the_dialog(qtbot: QtBot, log: NotificationLog) -> None:
    log.error("it broke")
    dialog = NotificationsDialog(log, DARK)
    qtbot.addWidget(dialog)

    dialog._on_clear()

    assert names(dialog) == []
    assert log.entries == []


# ------------------------------------------------------------------ startup --


@pytest.fixture
def recents(tmp_path: Path) -> RecentVaults:
    return RecentVaults(tmp_path / "recent.json")


@pytest.fixture
def existing_vault(tmp_path: Path) -> Iterator[Path]:
    path = tmp_path / "known.dbx"
    with create_vault(path, PASSPHRASE, params=FAST):
        pass
    yield path


def test_the_picker_lists_remembered_vaults(
    qtbot: QtBot, recents: RecentVaults, existing_vault: Path
) -> None:
    recents.remember(existing_vault)

    dialog = StartupDialog(recents, DARK)
    qtbot.addWidget(dialog)

    assert dialog._list.count() == 1
    assert dialog.selected_path() == existing_vault


def test_the_picker_preselects_the_most_recent(
    qtbot: QtBot, recents: RecentVaults, existing_vault: Path
) -> None:
    recents.remember(existing_vault)

    dialog = StartupDialog(recents, DARK)
    qtbot.addWidget(dialog)

    assert dialog._list.currentRow() == 0


def test_opening_with_nothing_remembered_asks_for_a_choice(
    qtbot: QtBot, recents: RecentVaults
) -> None:
    dialog = StartupDialog(recents, DARK)
    qtbot.addWidget(dialog)

    dialog.open_selected()

    assert "choose a vault" in dialog.status_text.lower()
    assert dialog.vault is None
    assert not dialog._open.isEnabled()


def test_a_wrong_passphrase_is_reported_not_raised(
    qtbot: QtBot, recents: RecentVaults, existing_vault: Path
) -> None:
    recents.remember(existing_vault)
    dialog = StartupDialog(recents, DARK)
    qtbot.addWidget(dialog)
    dialog._passphrase.setText("wrong")

    dialog.open_selected()

    assert dialog.status_text != ""
    assert dialog.vault is None


def test_the_right_passphrase_opens_the_vault(
    qtbot: QtBot, recents: RecentVaults, existing_vault: Path
) -> None:
    recents.remember(existing_vault)
    dialog = StartupDialog(recents, DARK)
    qtbot.addWidget(dialog)
    dialog._passphrase.setText(PASSPHRASE)

    dialog.open_selected()

    assert dialog.vault is not None
    assert dialog.vault.is_open
    dialog.vault.close()


def test_the_passphrase_field_is_masked(qtbot: QtBot, recents: RecentVaults) -> None:
    dialog = StartupDialog(recents, DARK)
    qtbot.addWidget(dialog)

    assert dialog._passphrase.echoMode() == QLineEdit.EchoMode.Password


def test_a_failed_unlock_does_not_hold_the_vault_lock(
    qtbot: QtBot, recents: RecentVaults, existing_vault: Path
) -> None:
    """A held lock would make the next attempt fail against a phantom."""
    recents.remember(existing_vault)
    dialog = StartupDialog(recents, DARK)
    qtbot.addWidget(dialog)
    dialog._passphrase.setText("wrong")
    dialog.open_selected()

    # The same vault opens straight afterwards, which it could not if the
    # single-writer lock were still held by the failed attempt.
    dialog._passphrase.setText(PASSPHRASE)
    dialog.open_selected()

    assert dialog.vault is not None
    dialog.vault.close()
