"""Configure where blobs are stored.

The bot token is write-only in this dialog. It is never rendered back, not even
masked with its real length, because a secret that can be read out of the UI is
a secret that can be shoulder-surfed, screenshotted, or recovered from a widget
tree. The dialog reports only whether one is configured.

An empty token field therefore means "leave it alone" rather than "erase it",
which is the only reading that lets someone change the channel without
retyping a credential they may not have to hand.

Values are written to `.env`, rewriting only the keys this dialog owns so
anything else in the file survives.
"""

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from disbox.config import Settings
from disbox.gui.theme import Palette, Space
from disbox.gui.theme.stylesheet import build_stylesheet

__all__ = ["SettingsDialog"]

_TOKEN_KEY = "DISBOX_BOT_TOKEN"  # noqa: S105 - a variable name, not a secret
_CHANNEL_KEY = "DISBOX_CHANNEL_ID"


class SettingsDialog(QDialog):
    """Edits the Discord storage configuration."""

    def __init__(
        self,
        settings: Settings,
        palette: Palette,
        *,
        env_path: Path,
        parent: QWidget | None = None,
    ) -> None:
        """Edit `settings`, persisting to `env_path`."""
        super().__init__(parent)
        self._settings = settings
        self._env_path = env_path
        self._status = ""

        self.setWindowTitle("Settings")
        self.setMinimumWidth(460)
        self.setStyleSheet(build_stylesheet(palette, translucent=False))

        layout = QVBoxLayout(self)
        layout.setContentsMargins(Space.LG, Space.LG, Space.LG, Space.LG)
        layout.setSpacing(Space.MD)

        heading = QLabel("Discord storage")
        heading.setObjectName("EmptyTitle")
        layout.addWidget(heading)

        form = QFormLayout()
        form.setHorizontalSpacing(Space.LG)
        form.setVerticalSpacing(Space.SM)

        self._token = QLineEdit()
        self._token.setEchoMode(QLineEdit.EchoMode.Password)
        self._token.setPlaceholderText("Paste a new token to replace the current one")
        self._token_state = QLabel()
        self._token_state.setObjectName("StatusText")

        self._channel = QLineEdit()
        self._channel.setPlaceholderText("Channel ID")
        if settings.channel_id is not None:
            self._channel.setText(str(settings.channel_id))

        form.addRow("Bot token", self._token)
        form.addRow("", self._token_state)
        form.addRow("Channel ID", self._channel)
        layout.addLayout(form)

        self._message = QLabel()
        self._message.setObjectName("StatusText")
        self._message.setWordWrap(True)
        layout.addWidget(self._message)
        layout.addStretch(1)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        save = QPushButton("Save")
        save.setObjectName("PrimaryButton")
        save.setCursor(Qt.CursorShape.PointingHandCursor)
        save.clicked.connect(self.save)
        cancel = QPushButton("Cancel")
        cancel.setCursor(Qt.CursorShape.PointingHandCursor)
        cancel.clicked.connect(self.reject)
        buttons.addWidget(save)
        buttons.addWidget(cancel)
        layout.addLayout(buttons)

        self._describe_token()

    @property
    def status_text(self) -> str:
        """The message currently shown, empty when there is none."""
        return self._status

    def _describe_token(self) -> None:
        """Say whether a token exists, without saying anything about its value."""
        configured = self._settings.bot_token is not None
        self._token_state.setText(
            "A token is set. Leave this blank to keep it."
            if configured
            else "No token is set, so uploads are unavailable."
        )

    def save(self) -> None:
        """Validate, then write the owned keys back to the env file."""
        updates: dict[str, str] = {}

        channel = self._channel.text().strip()
        if channel:
            if not channel.isdigit():
                self._report("Channel ID must be a number.")
                return
            updates[_CHANNEL_KEY] = channel

        token = self._token.text().strip()
        if token:
            updates[_TOKEN_KEY] = token

        if not updates:
            self.accept()
            return

        self._write_env(updates)
        self._report("")
        self.accept()

    def _write_env(self, updates: dict[str, str]) -> None:
        """Merge `updates` into the env file, leaving other entries untouched."""
        lines: list[str] = []
        if self._env_path.exists():
            lines = self._env_path.read_text(encoding="utf-8").splitlines()

        remaining = dict(updates)
        merged: list[str] = []
        for line in lines:
            key = line.split("=", 1)[0].strip()
            if key in remaining:
                merged.append(f"{key}={remaining.pop(key)}")
            else:
                merged.append(line)
        merged.extend(f"{key}={value}" for key, value in remaining.items())

        self._env_path.parent.mkdir(parents=True, exist_ok=True)
        self._env_path.write_text("\n".join(merged) + "\n", encoding="utf-8")

    def _report(self, message: str) -> None:
        """Show `message`, or clear the area when it is empty."""
        self._status = message
        self._message.setText(message)
