"""A strip along the bottom reporting the transfer in progress.

Hidden whenever nothing is moving: a permanently visible progress bar reading
zero is noise, and the user should be able to tell at a glance whether the
application is busy.
"""

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QToolButton,
    QWidget,
)

from disbox.gui.models.file_table import format_size
from disbox.gui.theme import Palette, Space, icons

__all__ = ["TransferDock"]

_HEIGHT = 52


class TransferDock(QWidget):
    """Shows what is transferring, how far along it is, and a way to stop it."""

    #: Emitted when the user asks to cancel the running transfer.
    cancel_requested = Signal()

    def __init__(self, palette: Palette, parent: QWidget | None = None) -> None:
        """Build the dock, hidden until there is something to report."""
        super().__init__(parent)
        self.setObjectName("TransferDock")
        # A plain QWidget draws no stylesheet background without this.
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setFixedHeight(_HEIGHT)
        self._palette = palette

        layout = QHBoxLayout(self)
        layout.setContentsMargins(Space.LG, Space.SM, Space.LG, Space.SM)
        layout.setSpacing(Space.MD)

        self._label = QLabel()
        self._label.setObjectName("TransferLabel")

        self._bar = QProgressBar()
        self._bar.setObjectName("TransferBar")
        self._bar.setTextVisible(False)
        self._bar.setFixedHeight(6)
        # Indeterminate until a total is known, rather than showing a confident
        # zero for work whose size has not been measured yet.
        self._bar.setRange(0, 0)

        self._detail = QLabel()
        self._detail.setObjectName("TransferDetail")

        self._cancel = QToolButton()
        self._cancel.setObjectName("TransferCancel")
        self._cancel.setToolTip("Cancel")
        self._cancel.setAccessibleName("Cancel transfer")
        self._cancel.setCursor(Qt.CursorShape.PointingHandCursor)
        self._cancel.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        self._cancel.clicked.connect(self.cancel_requested.emit)

        layout.addWidget(self._label)
        layout.addWidget(self._bar, 1)
        layout.addWidget(self._detail)
        layout.addWidget(self._cancel)

        self.set_palette(palette)
        self.hide()

    def set_palette(self, palette: Palette) -> None:
        """Adopt a new palette, retinting the icon."""
        self._palette = palette
        self._cancel.setIcon(icons.icon("close", palette.text_muted, size=14, ratio=2.0))

    def begin(self, label: str) -> None:
        """Announce that `label` has started, with progress not yet known."""
        self._label.setText(label)
        self._detail.clear()
        self._bar.setRange(0, 0)
        self.show()

    def report(self, completed: int, total: int) -> None:
        """Update the bar.

        A total of zero stays indeterminate rather than dividing by it.
        """
        if total <= 0:
            self._bar.setRange(0, 0)
            self._detail.clear()
            return

        self._bar.setRange(0, total)
        self._bar.setValue(completed)
        self._detail.setText(f"{format_size(completed)} of {format_size(total)}")

    def end(self) -> None:
        """Hide the dock; there is nothing left to report."""
        self._label.clear()
        self._detail.clear()
        self.hide()

    @property
    def label_text(self) -> str:
        """What the dock currently says it is doing."""
        return self._label.text()

    @property
    def detail_text(self) -> str:
        """The byte counts currently shown."""
        return self._detail.text()
