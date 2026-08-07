"""Frameless window chrome that keeps every native behaviour.

Removing the system title bar is easy; removing it without breaking the window
is not. The naive approach tracks mouse presses to drag and drop the window,
and silently loses snap layouts, edge snapping, aero shake, keyboard move and
size, the resize cursors, and double-click to maximise. Users notice all of it,
even when they cannot name what changed.

So this does not implement any of those. It answers Windows' ``WM_NCHITTEST``
with the region the cursor is over -- caption, one of the eight resize edges,
or the maximise button -- and lets the operating system perform the behaviour
it already owns. Snap layouts appear on hover over maximise because Windows is
told that is what the button is, not because we drew a flyout.

Everything is guarded by platform: on macOS and Linux the mixin does nothing
and the window keeps its native frame.
"""

import ctypes
import sys
from ctypes import wintypes
from typing import Any, Final

from PySide6.QtCore import QByteArray, QPoint, Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import QHBoxLayout, QLabel, QToolButton, QWidget

from disbox.gui.theme import Palette, Space, icons

__all__ = ["FramelessMixin", "TitleBar"]

# Win32 hit-test results.
_WM_NCHITTEST: Final = 0x0084
_HTCLIENT: Final = 1
_HTCAPTION: Final = 2
_HTMAXBUTTON: Final = 9
_HTLEFT: Final = 10
_HTRIGHT: Final = 11
_HTTOP: Final = 12
_HTTOPLEFT: Final = 13
_HTTOPRIGHT: Final = 14
_HTBOTTOM: Final = 15
_HTBOTTOMLEFT: Final = 16
_HTBOTTOMRIGHT: Final = 17

#: Width of the invisible band along each edge that begins a resize. Eight
#: device-independent pixels is roughly what native windows use; much less and
#: the window becomes fiddly to grab.
_RESIZE_MARGIN: Final = 8

_TITLE_BAR_HEIGHT: Final = 44
_BUTTON_WIDTH: Final = 46


class TitleBar(QWidget):
    """The window's caption strip: identity on the left, controls on the right."""

    def __init__(self, window: QWidget, palette: Palette) -> None:
        """Build a title bar that drives `window`."""
        super().__init__(window)
        self._window = window
        self._palette = palette
        self.setObjectName("TitleBar")
        self.setFixedHeight(_TITLE_BAR_HEIGHT)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(Space.LG, 0, 0, 0)
        layout.setSpacing(Space.SM)

        mark = QLabel()
        mark.setPixmap(icons.pixmap("shield", palette.accent, size=18, ratio=2.0))
        self._title = QLabel(window.windowTitle())
        self._title.setObjectName("TitleText")
        layout.addWidget(mark)
        layout.addWidget(self._title)
        layout.addStretch(1)

        self.minimise_button = self._control("minimise", "Minimise", window.showMinimized)
        self.maximise_button = self._control("maximise", "Maximise", self.toggle_maximised)
        self.close_button = self._control("close", "Close", window.close)
        self.close_button.setObjectName("CloseButton")
        for button in (self.minimise_button, self.maximise_button, self.close_button):
            layout.addWidget(button)

    def _control(self, icon_name: str, tooltip: str, slot: Any) -> QToolButton:
        """Build one window-control button."""
        button = QToolButton(self)
        button.setObjectName("WindowControl")
        button.setIcon(icons.icon(icon_name, self._palette.text_muted, size=12, ratio=2.0))
        button.setToolTip(tooltip)
        button.setFixedSize(_BUTTON_WIDTH, _TITLE_BAR_HEIGHT)
        button.clicked.connect(slot)
        return button

    def set_title(self, text: str) -> None:
        """Update the displayed window title."""
        self._title.setText(text)

    def toggle_maximised(self) -> None:
        """Switch between maximised and restored."""
        if self._window.isMaximized():
            self._window.showNormal()
        else:
            self._window.showMaximized()
        self._sync_maximise_icon()

    def _sync_maximise_icon(self) -> None:
        name = "restore" if self._window.isMaximized() else "maximise"
        self.maximise_button.setIcon(icons.icon(name, self._palette.text_muted, size=12, ratio=2.0))

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:  # noqa: N802 - Qt API
        """Double-clicking the caption toggles maximised, as native windows do.

        Only reached on platforms without the Win32 hit-test path, which gives
        this behaviour for free.
        """
        if event.button() == Qt.MouseButton.LeftButton:
            self.toggle_maximised()


class FramelessMixin:
    """Give a QWidget window frameless chrome without losing native behaviour.

    Mix in before the Qt base class and call `install_frameless_chrome` once
    the title bar exists.
    """

    def install_frameless_chrome(self, title_bar: TitleBar) -> None:
        """Drop the system frame and start answering hit tests."""
        self._title_bar = title_bar
        widget: QWidget = self  # type: ignore[assignment]  # always mixed into a QWidget
        widget.setWindowFlag(Qt.WindowType.FramelessWindowHint, True)

    def nativeEvent(  # noqa: N802 - Qt API
        self, event_type: QByteArray | bytes, message: int
    ) -> tuple[bool, int]:
        """Tell Windows which part of the frame the cursor is over.

        Returning the correct region is what preserves resize cursors, edge
        snapping, snap layouts, and double-click to maximise: Windows performs
        them itself once it knows what it is pointing at.
        """
        if sys.platform != "win32" or event_type not in (
            b"windows_generic_MSG",
            b"windows_dispatcher_MSG",
        ):
            return False, 0

        msg = ctypes.cast(ctypes.c_void_p(int(message)), ctypes.POINTER(_Msg)).contents
        if msg.message != _WM_NCHITTEST:
            return False, 0

        widget: QWidget = self  # type: ignore[assignment]
        # lParam packs the screen position as two signed 16-bit halves.
        x = ctypes.c_short(msg.lParam & 0xFFFF).value
        y = ctypes.c_short((msg.lParam >> 16) & 0xFFFF).value
        local = widget.mapFromGlobal(QPoint(x, y))

        return True, self._hit_test(widget, local)

    def _hit_test(self, widget: QWidget, point: QPoint) -> int:
        """Classify a point in window coordinates into a Win32 hit-test region."""
        # A maximised window has no resizable edge.
        edge = 0 if widget.isMaximized() else _RESIZE_MARGIN

        width, height = widget.width(), widget.height()
        left = point.x() < edge
        right = point.x() > width - edge
        top = point.y() < edge
        bottom = point.y() > height - edge

        # (top, bottom, left, right) -> region. Corners must be tested before
        # edges, which an ordered mapping gives for free.
        edges: dict[tuple[bool, bool, bool, bool], int] = {
            (True, False, True, False): _HTTOPLEFT,
            (True, False, False, True): _HTTOPRIGHT,
            (False, True, True, False): _HTBOTTOMLEFT,
            (False, True, False, True): _HTBOTTOMRIGHT,
            (True, False, False, False): _HTTOP,
            (False, True, False, False): _HTBOTTOM,
            (False, False, True, False): _HTLEFT,
            (False, False, False, True): _HTRIGHT,
        }
        if (region := edges.get((top, bottom, left, right))) is not None:
            return region

        title_bar = getattr(self, "_title_bar", None)
        if title_bar is not None and title_bar.geometry().contains(point):
            # Reporting the maximise button as such is what makes Windows 11
            # show its snap-layouts flyout on hover.
            button = title_bar.maximise_button
            if button.geometry().contains(title_bar.mapFrom(widget, point)):
                return _HTMAXBUTTON
            for control in (title_bar.minimise_button, title_bar.close_button):
                if control.geometry().contains(title_bar.mapFrom(widget, point)):
                    return _HTCLIENT
            return _HTCAPTION

        return _HTCLIENT


class _Msg(ctypes.Structure):
    """The Win32 MSG structure, as far as hit testing needs it."""

    _fields_ = (
        ("hWnd", wintypes.HWND),
        ("message", wintypes.UINT),
        ("wParam", ctypes.c_size_t),
        ("lParam", ctypes.c_ssize_t),
        ("time", wintypes.DWORD),
        ("pt_x", wintypes.LONG),
        ("pt_y", wintypes.LONG),
    )
