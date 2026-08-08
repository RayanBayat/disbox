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
    window="rgba(26, 24, 28, 0.74)",
    surface="rgba(35, 32, 38, 0.68)",
    surface_raised="rgba(45, 41, 49, 0.82)",
    surface_hover="rgba(255, 255, 255, 0.055)",
    surface_active="rgba(255, 255, 255, 0.085)",
    border="rgba(255, 255, 255, 0.075)",
    border_strong="rgba(255, 255, 255, 0.14)",
    text="#F5F2F4",
    text_muted="#B0A9B4",
    text_subtle="#9A929E",
    accent="#8B7BFF",
    accent_hover="#9E90FF",
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
    window="rgba(250, 248, 249, 0.82)",
    surface="rgba(255, 255, 255, 0.74)",
    surface_raised="rgba(255, 255, 255, 0.90)",
    surface_hover="rgba(0, 0, 0, 0.040)",
    surface_active="rgba(0, 0, 0, 0.065)",
    border="rgba(0, 0, 0, 0.075)",
    border_strong="rgba(0, 0, 0, 0.14)",
    text="#1A1519",
    text_muted="#635C66",
    text_subtle="#6E666E",
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
    """Fluent 2's spacing ramp, on a 4px base.

    The 2, 6 and 10 steps exist because Fluent icons carry internal padding;
    they are what let an icon sit on the four-pixel grid without being nudged
    by hand. Omitting them is why icon rows previously needed manual offsets.
    """

    XXS: Final = 2
    XS: Final = 4
    SM_XS: Final = 6
    SM: Final = 8
    SM_MD: Final = 10
    MD: Final = 12
    LG: Final = 16
    LG_XL: Final = 20
    XL: Final = 24
    XL_XXL: Final = 28
    XXL: Final = 32
    XXXL: Final = 40


class Radius:
    """Corner radii. Larger surfaces take larger radii so curvature reads evenly."""

    SM: Final = 8
    MD: Final = 12
    LG: Final = 16
    XL: Final = 20
    PILL: Final = 999


class Type:
    """The Windows 11 type ramp.

    Sizes and line heights are taken from Microsoft's published ramp rather than
    chosen, because Windows sets a hard legibility floor -- 14px Regular and
    12px Semibold -- below which "text is illegible in some languages". The
    previous scale sat a step under that at 13px body and 11px captions.

    Line heights are part of the ramp, not decoration: Fluent distributes
    vertical space by baseline alignment, so a size without its paired line
    height cannot produce a consistent rhythm.

    Segoe UI Variable is the system face, and its optical-size axis is what
    keeps small text legible; substituting a bundled font would look imported
    rather than native.

    Bold and italic are deliberately absent. The ramp uses Semibold for
    emphasis, and italic is excluded because it reduces readability,
    particularly for readers with dyslexia.
    """

    FAMILY: Final = '"Segoe UI Variable Display", "Segoe UI", Inter, system-ui, sans-serif'
    MONO: Final = '"Cascadia Code", "JetBrains Mono", Consolas, monospace'

    #: 12/16 -- the smallest permitted, and only ever at Semibold.
    CAPTION: Final = 12
    CAPTION_LEADING: Final = 16

    #: 14/20 -- the default for everything the user reads.
    BODY: Final = 14
    BODY_LEADING: Final = 20

    #: 14/20 Semibold. Emphasis without a size change.
    BODY_STRONG: Final = 14

    #: 20/28.
    SUBTITLE: Final = 20
    SUBTITLE_LEADING: Final = 28

    #: 28/36.
    TITLE: Final = 28
    TITLE_LEADING: Final = 36

    #: 40/52.
    DISPLAY: Final = 40
    DISPLAY_LEADING: Final = 52

    #: Semibold, per the ramp. Bold is not part of it.
    WEIGHT_REGULAR: Final = 400
    WEIGHT_SEMIBOLD: Final = 600


class Motion:
    """Durations in milliseconds.

    Short enough to feel immediate; long enough to be perceived as movement
    rather than a jump. Anything past ~250ms starts to feel sluggish.
    """

    INSTANT: Final = 90
    FAST: Final = 140
    NORMAL: Final = 200
