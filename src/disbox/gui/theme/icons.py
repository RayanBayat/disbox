"""Vector icons, drawn from source rather than shipped as bitmaps.

Every icon is an SVG path authored on a 24x24 grid with a 1.75px stroke, round
caps and round joins. Keeping to one grid and one stroke weight is what makes a
set look drawn by one hand instead of collected from several; it is the single
cheapest thing that separates a considered interface from an assembled one.

They are rendered at request time, tinted to a colour and scaled to a device
pixel ratio, so they stay crisp on any display and recolour with the theme.
A bitmap set would need a file per icon, per colour, per scale factor.

Authored here rather than pulled from an icon library: no dependency to track,
no licence to honour, and total control over the visual language.
"""

from typing import Final

from PySide6.QtCore import QByteArray, QRectF, Qt
from PySide6.QtGui import QColor, QIcon, QImage, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer

__all__ = ["ICON_NAMES", "icon", "icon_for_filename", "pixmap"]

# Path data only; the wrapper below supplies the canvas and stroke attributes.
_PATHS: Final[dict[str, str]] = {
    # Navigation
    "arrow-left": "M19 12H5M12 19l-7-7 7-7",
    "arrow-up": "M12 19V5M5 12l7-7 7 7",
    "chevron-right": "M9 18l6-6-6-6",
    "chevron-down": "M6 9l6 6 6-6",
    "search": "M11 19a8 8 0 100-16 8 8 0 000 16zM21 21l-4.35-4.35",
    "x": "M18 6L6 18M6 6l12 12",
    # Window controls. Drawn lighter than the rest because they sit in the
    # caption, where they should be reachable but never draw the eye.
    "minimise": "M5 12h14",
    "maximise": "M6 6h12v12H6z",
    "restore": "M9 9V5h10v10h-4M5 9h10v10H5z",
    "close": "M6 6l12 12M18 6L6 18",
    # Places
    "vault": "M3 7a2 2 0 012-2h4l2 2h8a2 2 0 012 2v9a2 2 0 01-2 2H5a2 2 0 01-2-2V7z",
    "trash": (
        "M3 6h18M8 6V4a1 1 0 011-1h6a1 1 0 011 1v2M19 6l-1 14a2 2 0 01-2 2H8a2 2 0 01-2-2L5 6"
        "M10 11v6M14 11v6"
    ),
    "clock": "M12 21a9 9 0 100-18 9 9 0 000 18zM12 7v5l3 2",
    "settings": "M4 7h9M17 7h3M4 17h3M11 17h9M15 5v4M9 15v4",
    # File kinds
    "folder": "M3 7a2 2 0 012-2h4l2 2h8a2 2 0 012 2v8a2 2 0 01-2 2H5a2 2 0 01-2-2V7z",
    "file": "M14 3H7a2 2 0 00-2 2v14a2 2 0 002 2h10a2 2 0 002-2V8l-5-5zM14 3v5h5",
    "file-text": (
        "M14 3H7a2 2 0 00-2 2v14a2 2 0 002 2h10a2 2 0 002-2V8l-5-5zM14 3v5h5M9 13h6M9 17h6"
    ),
    "image": (
        "M4 5a1 1 0 011-1h14a1 1 0 011 1v14a1 1 0 01-1 1H5a1 1 0 01-1-1V5z"
        "M9 10a1.5 1.5 0 100-3 1.5 1.5 0 000 3zM20 15l-5-5L5 20"
    ),
    "video": "M4 6a2 2 0 012-2h8a2 2 0 012 2v12a2 2 0 01-2 2H6a2 2 0 01-2-2V6zM16 10l5-3v10l-5-3",
    "audio": "M9 18V6l10-2v12M9 18a3 3 0 11-6 0 3 3 0 016 0zM19 16a3 3 0 11-6 0 3 3 0 016 0z",
    "archive": "M4 4h16v4H4zM5 8v11a1 1 0 001 1h12a1 1 0 001-1V8M10 12h4",
    "code": "M9 18l-6-6 6-6M15 6l6 6-6 6",
    # Actions
    "upload": "M12 16V4M7 9l5-5 5 5M4 16v3a1 1 0 001 1h14a1 1 0 001-1v-3",
    "download": "M12 4v12M7 11l5 5 5-5M4 16v3a1 1 0 001 1h14a1 1 0 001-1v-3",
    "refresh": "M20 11A8 8 0 006.3 6.3L3 9M4 13a8 8 0 0013.7 4.7L21 15M21 5v4h-4M3 19v-4h4",
    "info": "M12 21a9 9 0 100-18 9 9 0 000 18zM12 11v5M12 8h.01",
    "shield": "M12 3l8 3v6c0 4.4-3.4 7.9-8 9-4.6-1.1-8-4.6-8-9V6l8-3z",
    "contrast": "M12 3a9 9 0 100 18 9 9 0 000-18zm0 2v14a7 7 0 000-14z",
}

ICON_NAMES: Final = frozenset(_PATHS)

# Filename suffix -> (icon name, palette attribute holding its tint).
_SUFFIX_ICONS: Final[dict[str, tuple[str, str]]] = {
    **dict.fromkeys(
        ("png", "jpg", "jpeg", "gif", "webp", "bmp", "svg", "heic", "raw", "tiff"),
        ("image", "icon_image"),
    ),
    **dict.fromkeys(("mp4", "mkv", "mov", "avi", "webm", "wmv"), ("video", "icon_video")),
    **dict.fromkeys(("mp3", "flac", "wav", "aac", "ogg", "m4a"), ("audio", "icon_audio")),
    **dict.fromkeys(("zip", "gz", "zst", "tar", "7z", "rar", "xz"), ("archive", "icon_archive")),
    **dict.fromkeys(
        ("pdf", "doc", "docx", "txt", "md", "rtf", "odt", "xlsx", "csv"),
        ("file-text", "icon_document"),
    ),
    **dict.fromkeys(
        ("py", "js", "ts", "rs", "go", "c", "cpp", "java", "sql", "json", "toml", "yaml"),
        ("code", "icon_code"),
    ),
}

_VIEWBOX: Final = 24
_STROKE: Final = 1.75


def _svg(name: str, colour: str) -> bytes:
    """Wrap a stored path in a complete SVG document tinted to `colour`."""
    path = _PATHS[name]
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {_VIEWBOX} {_VIEWBOX}" '
        f'fill="none" stroke="{colour}" stroke-width="{_STROKE}" '
        f'stroke-linecap="round" stroke-linejoin="round">'
        f'<path d="{path}"/></svg>'
    ).encode()


def pixmap(name: str, colour: str, size: int = 20, ratio: float = 1.0) -> QPixmap:
    """Render an icon to a pixmap.

    Args:
        name: Key from `ICON_NAMES`.
        colour: Any CSS colour the SVG renderer accepts.
        size: Logical edge length in pixels.
        ratio: Device pixel ratio, so the result stays sharp on HiDPI screens.

    Returns:
        The rendered pixmap, already tagged with its device pixel ratio.

    Raises:
        KeyError: If `name` is not a known icon.
    """
    if name not in _PATHS:
        msg = f"unknown icon {name!r}; known icons are {sorted(_PATHS)}"
        raise KeyError(msg)

    physical = max(1, round(size * ratio))
    image = QImage(physical, physical, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(QColor(0, 0, 0, 0))

    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    QSvgRenderer(QByteArray(_svg(name, colour))).render(painter, QRectF(0, 0, physical, physical))
    painter.end()

    rendered = QPixmap.fromImage(image)
    rendered.setDevicePixelRatio(ratio)
    return rendered


def icon(name: str, colour: str, size: int = 20, ratio: float = 1.0) -> QIcon:
    """Return an icon suitable for a button or a table cell."""
    return QIcon(pixmap(name, colour, size, ratio))


def icon_for_filename(name: str, *, is_directory: bool) -> tuple[str, str]:
    """Choose an icon and a palette role for a node.

    Args:
        name: The node's name, used for its suffix.
        is_directory: Whether the node is a folder.

    Returns:
        ``(icon_name, palette_attribute)``. The caller resolves the attribute
        against the active palette, so the same node tints correctly in either
        theme.
    """
    if is_directory:
        return "folder", "icon_folder"
    suffix = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    return _SUFFIX_ICONS.get(suffix, ("file", "icon_generic"))


def alignment_for_column(is_numeric: bool) -> Qt.AlignmentFlag:
    """Right-align numbers so digits line up, left-align everything else."""
    if is_numeric:
        return Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
    return Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
