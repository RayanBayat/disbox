"""Design tokens: the single source of truth for how Disbox looks.

Every colour, radius, and spacing value in the application resolves to a token
here. Hard-coding a colour in a widget is what makes an interface drift into
looking assembled rather than designed, so the stylesheet is generated from this
file and nothing else defines appearance.

The palette is built from a neutral ramp plus one accent. Restraint is the
point: depth comes from layering translucent surfaces over the window backdrop
and separating them with hairline borders, not from drop shadows or from a
second competing hue.

Spacing follows a 4px grid. Aligning to a grid is most of what separates a
polished layout from an arbitrary one.
"""

from dataclasses import dataclass
from typing import Final

__all__ = ["DARK", "LIGHT", "Motion", "Palette", "Radius", "Space", "Type"]


@dataclass(frozen=True, slots=True)
class Palette:
    """Semantic colour roles. Widgets ask for a role, never a hex value."""

    name: str
    is_dark: bool

    # Surfaces, from furthest back to nearest the user.
    window: str
    surface: str
    surface_raised: str
    surface_hover: str
    surface_active: str

    # Hairlines and dividers.
    border: str
    border_strong: str

    # Text, in descending emphasis.
    text: str
    text_muted: str
    text_subtle: str

    # The single accent, plus states derived from it.
    accent: str
    accent_hover: str
    accent_text: str
    accent_subtle: str

    # Status.
    danger: str
    warning: str
    success: str

    # Per-type file icon tints, so a folder scan reads at a glance.
    icon_folder: str
    icon_image: str
    icon_video: str
    icon_audio: str
    icon_archive: str
    icon_document: str
    icon_code: str
    icon_generic: str


DARK: Final = Palette(
    name="dark",
    is_dark=True,
    # Alpha on the surfaces is deliberate: the Mica backdrop shows through, so
    # the window picks up the desktop behind it the way native Windows 11 apps do.
    window="rgba(22, 22, 26, 0.72)",
    surface="rgba(30, 30, 36, 0.66)",
    surface_raised="rgba(38, 38, 46, 0.80)",
    surface_hover="rgba(255, 255, 255, 0.055)",
    surface_active="rgba(255, 255, 255, 0.085)",
    border="rgba(255, 255, 255, 0.075)",
    border_strong="rgba(255, 255, 255, 0.14)",
    text="#F2F3F7",
    text_muted="#A8ABB8",
    text_subtle="#6E7280",
    accent="#7C6CFF",
    accent_hover="#8E80FF",
    accent_text="#FFFFFF",
    accent_subtle="rgba(124, 108, 255, 0.16)",
    danger="#FF6B6B",
    warning="#FFB454",
    success="#4ADE80",
    icon_folder="#7C9CFF",
    icon_image="#4ADE80",
    icon_video="#FF8FA3",
    icon_audio="#C084FC",
    icon_archive="#FFB454",
    icon_document="#60C6F0",
    icon_code="#8FE388",
    icon_generic="#8A8F9E",
)

LIGHT: Final = Palette(
    name="light",
    is_dark=False,
    window="rgba(246, 247, 250, 0.80)",
    surface="rgba(255, 255, 255, 0.72)",
    surface_raised="rgba(255, 255, 255, 0.90)",
    surface_hover="rgba(0, 0, 0, 0.040)",
    surface_active="rgba(0, 0, 0, 0.065)",
    border="rgba(0, 0, 0, 0.075)",
    border_strong="rgba(0, 0, 0, 0.14)",
    text="#14161C",
    text_muted="#5A5F6E",
    text_subtle="#8A8F9E",
    accent="#5B4BE8",
    accent_hover="#4A3AD6",
    accent_text="#FFFFFF",
    accent_subtle="rgba(91, 75, 232, 0.12)",
    danger="#D93A3A",
    warning="#B26A00",
    success="#1F9D55",
    icon_folder="#4C6EF5",
    icon_image="#1F9D55",
    icon_video="#E0567A",
    icon_audio="#8B4BD6",
    icon_archive="#B26A00",
    icon_document="#1E88C7",
    icon_code="#2F9E44",
    icon_generic="#6E7280",
)


class Space:
    """A 4px spacing grid."""

    XS: Final = 4
    SM: Final = 8
    MD: Final = 12
    LG: Final = 16
    XL: Final = 24
    XXL: Final = 32


class Radius:
    """Corner radii. Larger surfaces take larger radii so curvature reads evenly."""

    SM: Final = 6
    MD: Final = 8
    LG: Final = 12
    PILL: Final = 999


class Type:
    """Typography.

    Segoe UI Variable is the Windows 11 system face and is what makes an app
    look native there; the rest are fallbacks for other platforms.
    """

    FAMILY: Final = '"Segoe UI Variable Display", "Segoe UI", Inter, system-ui, sans-serif'
    MONO: Final = '"Cascadia Code", "JetBrains Mono", Consolas, monospace'

    CAPTION: Final = 11
    BODY: Final = 13
    SUBTITLE: Final = 14
    TITLE: Final = 17
    DISPLAY: Final = 22


class Motion:
    """Durations in milliseconds.

    Short enough to feel immediate; long enough to be perceived as movement
    rather than a jump. Anything past ~250ms starts to feel sluggish.
    """

    INSTANT: Final = 90
    FAST: Final = 140
    NORMAL: Final = 200
