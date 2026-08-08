"""The settings dialog: configure storage without ever showing the token back."""

from pathlib import Path

from pydantic import SecretStr
from PySide6.QtWidgets import QLineEdit
from pytestqt.qtbot import QtBot

from disbox.config import Settings
from disbox.gui.theme.tokens import DARK
from disbox.gui.views.settings_dialog import SettingsDialog

TOKEN = "fake.token.value"  # noqa: S105 - fixture, not a credential


def open_dialog(qtbot: QtBot, settings: Settings, env: Path) -> SettingsDialog:
    dialog = SettingsDialog(settings, DARK, env_path=env)
    qtbot.addWidget(dialog)
    return dialog


def test_channel_id_is_shown_because_it_is_not_secret(qtbot: QtBot, tmp_path: Path) -> None:
    settings = Settings(_env_file=None, bot_token=SecretStr(TOKEN), channel_id=12345)

    dialog = open_dialog(qtbot, settings, tmp_path / ".env")

    assert dialog._channel.text() == "12345"


def test_the_token_is_never_rendered_back(qtbot: QtBot, tmp_path: Path) -> None:
    """Reading a secret out of the UI is a way to leak it, so it is not there."""
    settings = Settings(_env_file=None, bot_token=SecretStr(TOKEN), channel_id=1)

    dialog = open_dialog(qtbot, settings, tmp_path / ".env")

    assert TOKEN not in dialog._token.text()
    assert dialog._token.text() == ""


def test_a_configured_token_is_reported_as_set_without_its_value(
    qtbot: QtBot, tmp_path: Path
) -> None:
    settings = Settings(_env_file=None, bot_token=SecretStr(TOKEN), channel_id=1)

    dialog = open_dialog(qtbot, settings, tmp_path / ".env")

    assert "set" in dialog._token_state.text().lower()
    assert TOKEN not in dialog._token_state.text()


def test_an_absent_token_is_reported_as_missing(qtbot: QtBot, tmp_path: Path) -> None:
    dialog = open_dialog(qtbot, Settings(_env_file=None), tmp_path / ".env")

    assert "no token is set" in dialog._token_state.text().lower()


def test_the_token_field_is_masked(qtbot: QtBot, tmp_path: Path) -> None:
    dialog = open_dialog(qtbot, Settings(_env_file=None), tmp_path / ".env")

    assert dialog._token.echoMode() == QLineEdit.EchoMode.Password


def test_saving_writes_the_channel_id(qtbot: QtBot, tmp_path: Path) -> None:
    env = tmp_path / ".env"
    dialog = open_dialog(qtbot, Settings(_env_file=None), env)
    dialog._channel.setText("999")

    dialog.save()

    assert "DISBOX_CHANNEL_ID=999" in env.read_text(encoding="utf-8")


def test_saving_a_new_token_writes_it(qtbot: QtBot, tmp_path: Path) -> None:
    env = tmp_path / ".env"
    dialog = open_dialog(qtbot, Settings(_env_file=None), env)
    dialog._token.setText(TOKEN)

    dialog.save()

    assert f"DISBOX_BOT_TOKEN={TOKEN}" in env.read_text(encoding="utf-8")


def test_leaving_the_token_blank_keeps_the_existing_one(qtbot: QtBot, tmp_path: Path) -> None:
    """An empty field means "unchanged", not "erase what is configured"."""
    env = tmp_path / ".env"
    env.write_text(f"DISBOX_BOT_TOKEN={TOKEN}\n", encoding="utf-8")
    dialog = open_dialog(qtbot, Settings(_env_file=None, bot_token=SecretStr(TOKEN)), env)
    dialog._channel.setText("7")

    dialog.save()

    body = env.read_text(encoding="utf-8")
    assert f"DISBOX_BOT_TOKEN={TOKEN}" in body
    assert "DISBOX_CHANNEL_ID=7" in body


def test_saving_does_not_duplicate_existing_keys(qtbot: QtBot, tmp_path: Path) -> None:
    env = tmp_path / ".env"
    env.write_text("DISBOX_CHANNEL_ID=1\n", encoding="utf-8")
    dialog = open_dialog(qtbot, Settings(_env_file=None, channel_id=1), env)
    dialog._channel.setText("2")

    dialog.save()

    body = env.read_text(encoding="utf-8")
    assert body.count("DISBOX_CHANNEL_ID") == 1
    assert "DISBOX_CHANNEL_ID=2" in body


def test_unrelated_env_entries_survive(qtbot: QtBot, tmp_path: Path) -> None:
    env = tmp_path / ".env"
    env.write_text("OTHER_APP_KEY=keep-me\n", encoding="utf-8")
    dialog = open_dialog(qtbot, Settings(_env_file=None), env)
    dialog._channel.setText("3")

    dialog.save()

    assert "OTHER_APP_KEY=keep-me" in env.read_text(encoding="utf-8")


def test_a_non_numeric_channel_is_refused(qtbot: QtBot, tmp_path: Path) -> None:
    env = tmp_path / ".env"
    dialog = open_dialog(qtbot, Settings(_env_file=None), env)
    dialog._channel.setText("not-a-number")

    dialog.save()

    assert "number" in dialog.status_text.lower()
    assert not env.exists()
