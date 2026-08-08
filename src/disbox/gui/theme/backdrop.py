"""Windows 11 system backdrops, for real glass rather than a painted imitation.

Qt cannot blur what is behind a window; nothing inside the process can, because
those pixels belong to other applications. The compositor can, so this asks the
Desktop Window Manager to apply Mica or Acrylic to the window frame and then
lets the translucent surfaces in the stylesheet reveal it.

The alternative -- faking depth with gradients and drawn shadows -- is exactly
what makes an interface look like an imitation of a native app. Either the
compositor provides the material or the window stays honestly opaque.

Everything degrades quietly: on macOS, Linux, or a Windows build without
support, `apply_backdrop` reports False and the interface is unchanged apart
from being solid.
"""

import ctypes
import platform
import sys

if sys.platform == "win32":
    import winreg
from ctypes import wintypes
from enum import IntEnum
from typing import Final

from disbox.log import get_logger

__all__ = [
    "Backdrop",
    "apply_backdrop",
    "is_supported",
    "round_window_corners",
    "set_dark_titlebar",
]

logger = get_logger(__name__)

# DWM window attributes. Values are fixed by the Windows API.
_DWMWA_USE_IMMERSIVE_DARK_MODE: Final = 20
_DWMWA_SYSTEMBACKDROP_TYPE: Final = 38
_DWMWA_WINDOW_CORNER_PREFERENCE: Final = 33

# DWM_WINDOW_CORNER_PREFERENCE values.
_DWMWCP_ROUND: Final = 2

# SYSTEMBACKDROP_TYPE arrived in Windows 11 22H2.
_MIN_BACKDROP_BUILD: Final = 22621


class Backdrop(IntEnum):
    """Material the compositor draws behind the window."""

    NONE = 1
    #: Desktop wallpaper, heavily blurred and tinted. The Windows 11 default
    #: for application windows -- calm, and it does not fight the content.
    MICA = 2
    #: Live blur of whatever sits behind. More obviously glassy, and more
    #: visually noisy over a busy desktop.
    ACRYLIC = 3
    #: Mica variant intended for tabbed shells.
    TABBED = 4


def _windows_build() -> int:
    """Return the Windows build number, or 0 when not on Windows."""
    if sys.platform != "win32":
        return 0
    try:
        return int(platform.version().split(".")[-1])
    except ValueError, IndexError:  # pragma: no cover - unparseable version string
        return 0


def system_prefers_dark() -> bool:
    """Whether Windows itself is in dark mode.

    This matters because Mica is drawn from the *desktop wallpaper* and tinted
    to the system theme. An application cannot make it light while Windows is
    dark, so a light app theme over a dark system leaves light translucent
    surfaces sitting on a dark material -- which renders as muddy grey rather
    than light. Callers use this to decide whether translucency is even
    viable, instead of applying it unconditionally and hoping.

    Returns:
        True when Windows is dark, and on any platform where the setting
        cannot be read, since an opaque window is the safe default.
    """
    if sys.platform != "win32":
        return True
    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize",
        ) as key:
            return not bool(winreg.QueryValueEx(key, "AppsUseLightTheme")[0])
    except OSError:  # pragma: no cover - key absent on some installs
        return True


def is_supported() -> bool:
    """Whether this system can draw a backdrop behind a window."""
    return _windows_build() >= _MIN_BACKDROP_BUILD


def _set_attribute(window_handle: int, attribute: int, value: int) -> bool:
    """Set one DWM attribute, reporting whether the call succeeded."""
    try:
        dwm = ctypes.windll.dwmapi
    except AttributeError, OSError:  # pragma: no cover - non-Windows
        return False

    result = dwm.DwmSetWindowAttribute(
        wintypes.HWND(window_handle),
        wintypes.DWORD(attribute),
        ctypes.byref(ctypes.c_int(value)),
        ctypes.sizeof(ctypes.c_int),
    )
    return bool(result == 0)  # ctypes returns Any; S_OK is zero


def set_dark_titlebar(window_handle: int, *, dark: bool) -> bool:
    """Match the native title bar to the application theme.

    Without this a dark window keeps a light title bar, which is the single
    most obvious sign that an app has been themed rather than designed.

    Args:
        window_handle: Native window handle (``int(widget.winId())``).
        dark: Whether to use the dark title bar.

    Returns:
        True if the compositor accepted the change.
    """
    if sys.platform != "win32":
        return False
    return _set_attribute(window_handle, _DWMWA_USE_IMMERSIVE_DARK_MODE, int(dark))


def round_window_corners(window_handle: int) -> bool:
    """Restore Windows 11's rounded corners on a frameless window.

    Removing the system frame also removes the rounding the compositor would
    otherwise apply, leaving hard right angles that look wrong beside every
    other window on the desktop. Only DWM can round the actual window region --
    a stylesheet radius would clip the content while the window itself stayed
    square, showing a sliver of background in each corner.

    Args:
        window_handle: Native window handle (``int(widget.winId())``).

    Returns:
        True if the compositor accepted it.
    """
    if sys.platform != "win32":
        return False
    return _set_attribute(window_handle, _DWMWA_WINDOW_CORNER_PREFERENCE, _DWMWCP_ROUND)


def apply_backdrop(window_handle: int, backdrop: Backdrop = Backdrop.MICA) -> bool:
    """Ask the compositor to draw `backdrop` behind the window.

    The window must also be told not to paint its own background, or the
    material is simply covered up.

    Args:
        window_handle: Native window handle (``int(widget.winId())``).
        backdrop: Material to request.

    Returns:
        True if the compositor accepted it; False on any system that cannot,
        in which case the window stays opaque and nothing else changes.
    """
    if not is_supported():
        logger.debug("backdrop unavailable", build=_windows_build(), platform=sys.platform)
        return False

    applied = _set_attribute(window_handle, _DWMWA_SYSTEMBACKDROP_TYPE, int(backdrop))
    logger.debug("backdrop requested", backdrop=backdrop.name, applied=applied)
    return applied
