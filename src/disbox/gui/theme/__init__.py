"""Design system: tokens, generated stylesheet, vector icons, window backdrop."""

from disbox.gui.theme.backdrop import Backdrop, apply_backdrop, is_supported, set_dark_titlebar
from disbox.gui.theme.icons import ICON_NAMES, icon, icon_for_filename, pixmap
from disbox.gui.theme.stylesheet import build_stylesheet
from disbox.gui.theme.tokens import DARK, LIGHT, Motion, Palette, Radius, Space, Type

__all__ = [
    "DARK",
    "ICON_NAMES",
    "LIGHT",
    "Backdrop",
    "Motion",
    "Palette",
    "Radius",
    "Space",
    "Type",
    "apply_backdrop",
    "build_stylesheet",
    "icon",
    "icon_for_filename",
    "is_supported",
    "pixmap",
    "set_dark_titlebar",
]
