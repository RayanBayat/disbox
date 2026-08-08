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
        self, recents: RecentVaults, palette: Palette, parent: QWidget | None = None
    ) -> None:
        """Offer the vaults in `recents`, or a way to reach another."""
        super().__init__(parent)
        self._recents = recents
        self._vault: Vault | None = None
        self._status = ""

        self.setWindowTitle("Disbox")
        self.setMinimumSize(520, 440)
        self.setStyleSheet(build_stylesheet(palette, translucent=False))

        layout = QVBoxLayout(self)
        layout.setContentsMargins(Space.XL, Space.XL, Space.XL, Space.XL)
        layout.setSpacing(Space.MD)

        heading = QLabel("Open a vault")
        heading.setObjectName("EmptyTitle")
        layout.addWidget(heading)

        hint = QLabel(
            "Your vault file is the index of everything you have stored. "
            "Keep it safe: without it, your files cannot be found again."
        )
        hint.setObjectName("StatusText")
        hint.setWordWrap(True)
        layout.addWidget(hint)

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

    def create_new(self) -> None:
        """Create a vault, then open it."""
        selected, _ = QFileDialog.getSaveFileName(
            self, "Create a Disbox vault", str(Path.home()), "Disbox vaults (*.dbx)"
        )
        if not selected:
            return

        passphrase = self._passphrase.text()
        if not passphrase:
            self._report("Enter a passphrase before creating a vault.")
            return

        path = Path(selected).with_suffix(".dbx")
        try:
            self._vault = create_vault(path, passphrase)
        except DisboxError as exc:
            self._report(str(exc))
            return

        self._recents.remember(path)
        self.accept()

    def open_selected(self) -> None:
        """Open the chosen vault with the passphrase entered."""
        path = self.selected_path()
        if path is None:
            self._report("Choose a vault, or create one.")
            return

        try:
            self._vault = open_vault(path, self._passphrase.text())
        except DisboxError as exc:
            self._report(str(exc))
            return

        self._recents.remember(path)
        self.accept()

    def _report(self, message: str) -> None:
        """Show `message` without interrupting."""
        self._status = message
        self._message.setText(message)
