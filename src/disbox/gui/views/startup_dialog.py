"""The first thing the application shows: which vault, and the passphrase for it.

A file dialog alone was the whole of the old entry path, which assumed the user
already had a vault and knew where it was. Someone opening Disbox for the first
time had nothing to click.

Passphrase entry lives here rather than in a later prompt so a wrong one is
reported while the user is still looking at the field they typed it into.
"""

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from disbox.config import load_settings
from disbox.core.crypto import KdfParams
from disbox.core.startup import RecentVaults, create_vault, open_vault
from disbox.core.vault import Vault
from disbox.errors import DisboxError
from disbox.gui.theme import Palette, Space
from disbox.gui.theme.stylesheet import build_stylesheet

__all__ = ["StartupDialog"]

_PATH_ROLE = Qt.ItemDataRole.UserRole


class StartupDialog(QDialog):
    """Opens or creates a vault, returning the opened one."""

    def __init__(
        self,
        recents: RecentVaults,
        palette: Palette,
        parent: QWidget | None = None,
        *,
        check_storage: bool | None = None,
    ) -> None:
        """Offer the vaults in `recents`, or a way to reach another.

        Args:
            recents: Previously opened vaults.
            palette: Theme to render with.
            parent: Owning widget.
            check_storage: Whether Discord is configured. Read from settings
                when omitted; passed explicitly by tests, which must not depend
                on whether the machine running them happens to have a token.
        """
        super().__init__(parent)
        self._recents = recents
        self._vault: Vault | None = None
        self._master_key: bytes | None = None
        self._status = ""
        self._created_notice = ""
        self._first_run = not recents.paths()

        if check_storage is None:
            settings = load_settings()
            check_storage = settings.bot_token is not None and settings.channel_id is not None
        self._discord_ready = check_storage

        self.setWindowTitle("Disbox")
        self.setMinimumSize(520, 440)
        self.setStyleSheet(build_stylesheet(palette, translucent=False))

        layout = QVBoxLayout(self)
        layout.setContentsMargins(Space.XL, Space.XL, Space.XL, Space.XL)
        layout.setSpacing(Space.MD)

        # "Open a vault" is an instruction a first-time user cannot follow,
        # because there is nothing to open yet.
        self._heading = QLabel("Welcome to Disbox" if self._first_run else "Open a vault")
        self._heading.setObjectName("EmptyTitle")
        layout.addWidget(self._heading)

        self._guidance = QLabel(
            "Start by creating a vault. The vault file is the index of "
            "everything you store: keep it somewhere you back up, because "
            "without it your files cannot be found again."
            if self._first_run
            else "Your vault file is the index of everything you have stored. "
            "Keep it backed up: without it, your files cannot be found again."
        )
        self._guidance.setObjectName("StatusText")
        self._guidance.setWordWrap(True)
        layout.addWidget(self._guidance)

        self._storage = QLabel(
            "Files will be stored on Discord."
            if self._discord_ready
            else "No Discord bot token is configured, so files will be stored "
            "on this computer beside the vault. Add a token in Settings to "
            "use Discord."
        )
        self._storage.setObjectName("StatusText")
        self._storage.setWordWrap(True)
        layout.addWidget(self._storage)

        self._list = QListWidget()
        self._list.setObjectName("TrashList")
        self._list.setAccessibleName("Recently opened vaults")
        self._list.itemDoubleClicked.connect(lambda _: self.open_selected())
        layout.addWidget(self._list, 1)

        self._passphrase = QLineEdit()
        self._passphrase.setEchoMode(QLineEdit.EchoMode.Password)
        self._passphrase.setPlaceholderText("Passphrase")
        self._passphrase.setAccessibleName("Vault passphrase")
        self._passphrase.returnPressed.connect(self.open_selected)
        layout.addWidget(self._passphrase)

        self._message = QLabel()
        self._message.setObjectName("StatusText")
        self._message.setWordWrap(True)
        layout.addWidget(self._message)

        layout.addLayout(self._build_buttons())

        self.reload()

    def _build_buttons(self) -> QHBoxLayout:
        """The row of actions along the bottom."""
        row = QHBoxLayout()
        for label, name, slot in (
            ("Browse…", "Browse for a vault", self.browse),
            ("Create new…", "Create a new vault", self.create_new),
        ):
            button = QPushButton(label)
            button.setAccessibleName(name)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.clicked.connect(slot)
            row.addWidget(button)

        row.addStretch(1)
        self._open = QPushButton("Open")
        self._open.setObjectName("PrimaryButton")
        self._open.setAccessibleName("Open the selected vault")
        self._open.setCursor(Qt.CursorShape.PointingHandCursor)
        self._open.clicked.connect(self.open_selected)
        row.addWidget(self._open)
        return row

    @property
    def vault(self) -> Vault | None:
        """The vault that was opened, if any."""
        return self._vault

    @property
    def guidance_text(self) -> str:
        """What the dialog says about the vault file."""
        return self._guidance.text()

    @property
    def storage_text(self) -> str:
        """What the dialog says about where files will go."""
        return self._storage.text()

    @property
    def created_notice(self) -> str:
        """What was reported after creating a vault, empty if none was."""
        return self._created_notice

    @property
    def master_key(self) -> bytes | None:
        """The key for the opened vault, needed to build a transfer engine."""
        return self._master_key

    @property
    def status_text(self) -> str:
        """The message currently shown."""
        return self._status

    def reload(self) -> None:
        """Re-read the recent list."""
        self._list.clear()
        for path in self._recents.paths():
            item = QListWidgetItem(f"{path.stem}\n{path.parent}")
            item.setData(_PATH_ROLE, str(path))
            self._list.addItem(item)
        if self._list.count():
            self._list.setCurrentRow(0)
        self._open.setEnabled(self._list.count() > 0)

    def selected_path(self) -> Path | None:
        """The vault currently chosen in the list."""
        item = self._list.currentItem()
        if item is None:
            return None  # type: ignore[unreachable]
        return Path(str(item.data(_PATH_ROLE)))

    def browse(self) -> None:
        """Pick a vault from anywhere on disk."""
        selected, _ = QFileDialog.getOpenFileName(
            self, "Open a Disbox vault", str(Path.home()), "Disbox vaults (*.dbx)"
        )
        if selected:
            self._recents.remember(Path(selected))
            self.reload()

    def create_new(self, *, path: Path | None = None, params: KdfParams | None = None) -> None:
        """Create a vault, then open it.

        Args:
            path: Where to create it. Asked for when omitted; supplied directly
                by tests, which cannot answer a modal file dialog.
            params: KDF cost, for tests that cannot afford the real one.
        """
        passphrase = self._passphrase.text()
        if not passphrase:
            # Checked before asking for a location, so the user is not sent
            # through a file dialog only to be refused afterwards.
            self._report("Enter a passphrase before creating a vault.")
            return

        if path is None:
            selected, _ = QFileDialog.getSaveFileName(
                self, "Create a Disbox vault", str(Path.home()), "Disbox vaults (*.dbx)"
            )
            if not selected:
                return
            path = Path(selected)

        target = path.with_suffix(".dbx")
        try:
            self._vault = create_vault(target, passphrase, params=params)
            self._master_key = self._vault.unlock(passphrase)
        except DisboxError as exc:
            self._report(str(exc))
            return

        # The path is the thing the user has to keep, so it is stated rather
        # than left for them to find out later.
        self._created_notice = (
            f"Vault created at {target}. Back this file up: it is the only "
            "record of where your files are."
        )
        self._recents.remember(target)
        self.accept()

    def open_selected(self) -> None:
        """Open the chosen vault with the passphrase entered."""
        path = self.selected_path()
        if path is None:
            self._report("Choose a vault, or create one.")
            return

        try:
            self._vault, self._master_key = open_vault(path, self._passphrase.text())
        except DisboxError as exc:
            self._report(str(exc))
            return

        self._recents.remember(path)
        self.accept()

    def _report(self, message: str) -> None:
        """Show `message` without interrupting."""
        self._status = message
        self._message.setText(message)
