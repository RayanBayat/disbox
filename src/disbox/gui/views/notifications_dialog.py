"""The notification history, with a way to copy a problem out.

Copying matters more than it looks: a diagnostic identifier that has to be
transcribed by hand from a screenshot is one that arrives in bug reports wrong.
"""

from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication
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

from disbox.gui.notifications import Level, NotificationLog
from disbox.gui.theme import Palette, Space
from disbox.gui.theme.stylesheet import build_stylesheet

__all__ = ["NotificationsDialog"]

_TEXT_ROLE = Qt.ItemDataRole.UserRole

_PREFIX = {Level.INFO: "", Level.WARNING: "Warning: ", Level.ERROR: "Error: "}


class NotificationsDialog(QDialog):
    """Shows every notice the application has raised this session."""

    def __init__(
        self, log: NotificationLog, palette: Palette, parent: QWidget | None = None
    ) -> None:
        """Show `log`."""
        super().__init__(parent)
        self._log = log

        self.setWindowTitle("Notifications")
        self.setMinimumSize(560, 400)
        self.setStyleSheet(build_stylesheet(palette, translucent=False))

        layout = QVBoxLayout(self)
        layout.setContentsMargins(Space.LG, Space.LG, Space.LG, Space.LG)
        layout.setSpacing(Space.MD)

        self._heading = QLabel()
        self._heading.setObjectName("EmptyTitle")
        layout.addWidget(self._heading)

        self._list = QListWidget()
        self._list.setObjectName("TrashList")  # same surface treatment
        self._list.setWordWrap(True)
        layout.addWidget(self._list, 1)

        buttons = QHBoxLayout()
        self._copy = QPushButton("Copy")
        self._copy.setToolTip("Copy the selected notice, including its identifier")
        self._copy.setCursor(Qt.CursorShape.PointingHandCursor)
        self._copy.clicked.connect(self.copy_selected)
        clear = QPushButton("Clear")
        clear.setCursor(Qt.CursorShape.PointingHandCursor)
        clear.clicked.connect(self._on_clear)
        buttons.addWidget(self._copy)
        buttons.addWidget(clear)
        buttons.addStretch(1)
        close = QPushButton("Close")
        close.setObjectName("PrimaryButton")
        close.setCursor(Qt.CursorShape.PointingHandCursor)
        close.clicked.connect(self.accept)
        buttons.addWidget(close)
        layout.addLayout(buttons)

        self.reload()
        # Opening the log is reading it, so the badge should stop nagging.
        log.mark_read()

    def reload(self) -> None:
        """Re-read the log."""
        self._list.clear()
        for notice in self._log.entries:
            label = f"{_PREFIX[notice.level]}{notice.message}"
            if notice.diagnostic_id:
                label = f"{label}    [{notice.diagnostic_id}]"
            item = QListWidgetItem(label)
            item.setData(_TEXT_ROLE, notice.copyable)
            self._list.addItem(item)

        empty = self._list.count() == 0
        self._heading.setText("Nothing to report" if empty else "Recent notices")
        self._copy.setEnabled(not empty)

    def copy_selected(self) -> str:
        """Put the selected notice on the clipboard.

        Returns:
            The text copied, empty when there was no selection.
        """
        item = self._list.currentItem()
        # currentItem is typed non-optional by PySide6 but returns None with no
        # selection, so mypy sees this guard as dead code. It is not.
        if item is None:
            return ""  # type: ignore[unreachable]
        text = str(item.data(_TEXT_ROLE))
        clipboard = QGuiApplication.clipboard()
        if clipboard is not None:
            clipboard.setText(text)
        return text

    def _on_clear(self) -> None:
        """Discard the history."""
        self._log.clear()
        self.reload()
