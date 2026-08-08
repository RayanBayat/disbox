"""What was deleted, and how to get it back.

Deletion is soft, so without somewhere to see the trash the recoverability is
real but invisible -- which is no comfort to someone who has just deleted the
wrong folder.

Restoring can fail, because a restore must never overwrite whatever has since
claimed the original name. That refusal is reported in the dialog rather than
raised, since it is an ordinary outcome the user can resolve by renaming.
"""

import uuid

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from disbox.core.filesystem import FileSystem
from disbox.core.vault import Vault
from disbox.errors import DisboxError
from disbox.gui.models.file_table import format_size
from disbox.gui.theme import Palette, Space
from disbox.gui.theme.stylesheet import build_stylesheet

__all__ = ["TrashDialog"]

_ID_ROLE = Qt.ItemDataRole.UserRole


class TrashDialog(QDialog):
    """Lists deleted nodes and restores the selected one."""

    #: Emitted when the vault's contents changed, so the window can refresh.
    vault_changed = Signal()

    def __init__(self, vault: Vault, palette: Palette, parent: QWidget | None = None) -> None:
        """Open a trash view onto `vault`."""
        super().__init__(parent)
        self._vault = vault
        self._filesystem = FileSystem(vault)
        self._status = ""

        self.setWindowTitle("Trash")
        self.setMinimumSize(460, 380)
        self.setStyleSheet(build_stylesheet(palette, translucent=False))

        layout = QVBoxLayout(self)
        layout.setContentsMargins(Space.LG, Space.LG, Space.LG, Space.LG)
        layout.setSpacing(Space.MD)

        self._heading = QLabel("Deleted items")
        self._heading.setObjectName("EmptyTitle")
        layout.addWidget(self._heading)

        self._list = QListWidget()
        self._list.setObjectName("TrashList")
        layout.addWidget(self._list, 1)

        self._message = QLabel()
        self._message.setObjectName("StatusText")
        self._message.setWordWrap(True)
        layout.addWidget(self._message)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        self._restore = QPushButton("Restore")
        self._restore.setObjectName("PrimaryButton")
        self._restore.setCursor(Qt.CursorShape.PointingHandCursor)
        self._restore.clicked.connect(self.restore_selected)
        close = QPushButton("Close")
        close.setCursor(Qt.CursorShape.PointingHandCursor)
        close.clicked.connect(self.accept)
        buttons.addWidget(self._restore)
        buttons.addWidget(close)
        layout.addLayout(buttons)

        self.reload()

    @property
    def status_text(self) -> str:
        """The message currently shown, empty when there is none."""
        return self._status

    def reload(self) -> None:
        """Re-read the trash."""
        self._list.clear()
        for node in self._filesystem.trash():
            kind = "Folder" if node.kind == "dir" else format_size(node.size)
            item = QListWidgetItem(f"{node.name}    ({kind})")
            item.setData(_ID_ROLE, node.id)
            self._list.addItem(item)

        empty = self._list.count() == 0
        self._heading.setText("Trash is empty" if empty else "Deleted items")
        self._restore.setEnabled(not empty)

    def restore_selected(self) -> None:
        """Put the selected node back, or explain why it cannot go back."""
        item = self._list.currentItem()
        # PySide6 types currentItem as non-optional, but it really does return
        # None when nothing is selected, so this guard is load-bearing and the
        # unreachability mypy sees is an error in the stub.
        if item is None:
            return  # type: ignore[unreachable]

        node_id: object = item.data(_ID_ROLE)
        if not isinstance(node_id, uuid.UUID):
            return

        try:
            self._filesystem.restore(node_id)
        except DisboxError as exc:
            self._report(str(exc))
            return

        self._report("")
        self.reload()
        self.vault_changed.emit()

    def _report(self, message: str) -> None:
        """Show `message`, or clear the area when it is empty."""
        self._status = message
        self._message.setText(message)
