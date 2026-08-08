"""Capture the real composited window, backdrop and all.

QWidget.grab() renders only the widget's own painting, so it cannot show Mica,
which the compositor draws behind the window. PrintWindow with
PW_RENDERFULLCONTENT asks Windows for the window as actually composited, which
is the only way to check a glass effect without a human looking at it.
"""

import ctypes
import os
import sys
from ctypes import wintypes
from pathlib import Path

os.environ["QT_QPA_PLATFORM"] = "windows"

from PySide6.QtCore import QTimer
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication

from disbox.core.vault import Vault
from disbox.gui.theme.tokens import DARK
from disbox.gui.views.main_window import MainWindow

OUT = Path(__file__).parent
PW_RENDERFULLCONTENT = 0x00000002


def capture(hwnd: int, path: Path) -> bool:
    """Save the composited window to `path`. Returns whether it worked."""
    user32 = ctypes.windll.user32
    gdi32 = ctypes.windll.gdi32

    rect = wintypes.RECT()
    # The extended frame bounds exclude the invisible resize border.
    ctypes.windll.dwmapi.DwmGetWindowAttribute(
        wintypes.HWND(hwnd), wintypes.DWORD(9), ctypes.byref(rect), ctypes.sizeof(rect)
    )
    width, height = rect.right - rect.left, rect.bottom - rect.top
    if width <= 0 or height <= 0:
        user32.GetWindowRect(wintypes.HWND(hwnd), ctypes.byref(rect))
        width, height = rect.right - rect.left, rect.bottom - rect.top

    window_dc = user32.GetWindowDC(hwnd)
    memory_dc = gdi32.CreateCompatibleDC(window_dc)
    bitmap = gdi32.CreateCompatibleBitmap(window_dc, width, height)
    gdi32.SelectObject(memory_dc, bitmap)

    ok = user32.PrintWindow(wintypes.HWND(hwnd), memory_dc, PW_RENDERFULLCONTENT)

    class BITMAPINFOHEADER(ctypes.Structure):
        _fields_ = (
            ("biSize", wintypes.DWORD),
            ("biWidth", wintypes.LONG),
            ("biHeight", wintypes.LONG),
            ("biPlanes", wintypes.WORD),
            ("biBitCount", wintypes.WORD),
            ("biCompression", wintypes.DWORD),
            ("biSizeImage", wintypes.DWORD),
            ("biXPelsPerMeter", wintypes.LONG),
            ("biYPelsPerMeter", wintypes.LONG),
            ("biClrUsed", wintypes.DWORD),
            ("biClrImportant", wintypes.DWORD),
        )

    header = BITMAPINFOHEADER()
    header.biSize = ctypes.sizeof(BITMAPINFOHEADER)
    header.biWidth = width
    header.biHeight = -height  # negative: top-down rows
    header.biPlanes = 1
    header.biBitCount = 32
    header.biCompression = 0

    buffer = ctypes.create_string_buffer(width * height * 4)
    gdi32.GetDIBits(memory_dc, bitmap, 0, height, buffer, ctypes.byref(header), 0)

    image = QImage(bytes(buffer), width, height, QImage.Format.Format_RGB32)
    image.save(str(path))

    gdi32.DeleteObject(bitmap)
    gdi32.DeleteDC(memory_dc)
    user32.ReleaseDC(hwnd, window_dc)
    return bool(ok)


def main() -> None:
    """Open the window and save a capture of it."""
    app = QApplication([])
    vault = Vault.open(Path("demo/my-files.dbx"))
    window = MainWindow(vault, DARK)
    window.resize(1180, 700)
    window.show()

    steps = iter(
        [
            ("cap_dark.png", None),
            ("cap_light.png", window.toggle_theme),
            ("cap_dark_again.png", window.toggle_theme),
        ]
    )

    def step() -> None:
        try:
            name, action = next(steps)
        except StopIteration:
            window.close()
            vault.close()
            app.quit()
            return
        if action is not None:
            action()
        app.processEvents()
        ok = capture(int(window.winId()), OUT / name)
        sys.stdout.write(f"{name}: {'captured' if ok else 'PrintWindow failed'}\n")
        QTimer.singleShot(400, step)

    QTimer.singleShot(600, step)
    app.exec()


if __name__ == "__main__":
    main()
