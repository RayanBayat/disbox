"""Generate the application stylesheet from design tokens.

Qt Style Sheets are the only practical way to restyle Qt's widgets wholesale,
but they are also easy to turn into an unmaintainable pile of hex codes. Every
value here therefore comes from `tokens`, so a palette change is one edit and
no widget can quietly invent its own colour.

Deliberately restrained: hairline borders and translucent fills carry the
depth, because Qt cannot draw a real shadow inside a stylesheet and faked ones
look worse than none. The glass effect comes from the window backdrop instead,
which the surfaces here are translucent enough to reveal.
"""

from disbox.gui.theme.tokens import Palette, Radius, Space, Type

__all__ = ["build_stylesheet"]


def build_stylesheet(p: Palette, *, translucent: bool = True) -> str:
    """Return the complete stylesheet for `p`.

    Args:
        p: Palette to render.
        translucent: Whether the window has a compositor material behind it.
            When it does not, surfaces are painted opaque -- a translucent
            surface with nothing behind it shows the desktop through the app.

    Qt Style Sheets have a trap that shapes everything here: styling *any*
    property of a widget makes Qt render that widget entirely from the
    stylesheet, so every state left undefined falls back to the platform style
    and looks nothing like the rest. Each control therefore declares all of its
    states -- hover, pressed, checked, focus, disabled -- rather than only the
    ones that seemed to need it.
    """
    window_bg = p.window if translucent else _opaque(p.window)
    raised = p.surface_raised if translucent else _opaque(p.surface_raised)
    panel = p.panel if translucent else _opaque(p.panel)
    # Scrollbars deserve special mention: Qt's defaults are chunky and dated,
    # and a thin overlay bar is one of the clearest signals of a modern app.
    return f"""
* {{
    font-family: {Type.FAMILY};
    font-size: {Type.BODY}px;
    color: {p.text};
    outline: none;
}}

QWidget#Root {{
    background: {window_bg};
}}

QWidget#TitleBar {{
    background: transparent;
}}

QLabel#TitleText {{
    font-size: {Type.BODY}px;
    font-weight: {Type.WEIGHT_SEMIBOLD};
    color: {p.text_muted};
}}

/* Window controls follow the platform's shape: square, full-height, and
   flush to the corner, so they land where muscle memory expects. */
QToolButton#WindowControl {{
    background: transparent;
    border: none;
    border-radius: 0;
}}
QToolButton#WindowControl:hover {{ background: {p.surface_hover}; }}
QToolButton#WindowControl:pressed {{ background: {p.surface_active}; }}
QToolButton#WindowControl:focus {{ background: transparent; }}
QToolButton#CloseButton:hover {{ background: {p.danger}; }}
QToolButton#CloseButton:pressed {{ background: {p.danger}; }}

QFrame#MeterTrack {{
    background: {p.surface_hover};
    border: none;
    border-radius: 3px;
}}
QFrame#MeterFill {{
    background: {p.accent};
    border: none;
    border-radius: 3px;
}}

QWidget#Sidebar {{
    background: {panel};
    border-right: 1px solid {p.border};
}}

QWidget#Content {{
    background: transparent;
}}

QWidget#HeaderBar {{
    background: transparent;
    border-bottom: 1px solid {p.border};
}}

QLabel#BrandName {{
    font-size: {Type.BODY_STRONG}px;
    font-weight: {Type.WEIGHT_SEMIBOLD};
    color: {p.text};
}}

QLabel#SectionLabel {{
    font-size: {Type.CAPTION}px;
    font-weight: {Type.WEIGHT_SEMIBOLD};
    color: {p.text_muted};
    padding: {Space.SM}px {Space.MD}px {Space.XS}px {Space.MD}px;
}}

QLabel#MeterCaption {{
    font-size: {Type.BODY}px;
    font-weight: {Type.WEIGHT_SEMIBOLD};
    color: {p.text};
}}

QLabel#StatusText {{
    color: {p.text_muted};
    font-size: {Type.CAPTION}px;
}}

QLabel#EmptyTitle {{
    font-size: {Type.SUBTITLE}px;
    font-weight: {Type.WEIGHT_SEMIBOLD};
    color: {p.text_muted};
}}

QLabel#EmptyHint {{
    font-size: {Type.BODY}px;
    color: {p.text_subtle};
}}

/* ---- Sidebar navigation ------------------------------------------------ */

QPushButton#NavItem {{
    background: transparent;
    border: none;
    border-radius: {Radius.MD}px;
    padding: {Space.SM}px {Space.MD}px;
    margin: 1px {Space.SM}px;
    text-align: left;
    color: {p.text_muted};
    font-size: {Type.BODY}px;
    font-weight: 500;
}}
QPushButton#NavItem:hover {{
    background: {p.surface_hover};
    color: {p.text};
}}
QPushButton#NavItem:pressed {{ background: {p.surface_active}; color: {p.text}; }}
QPushButton#NavItem:focus {{ background: {p.surface_hover}; color: {p.text}; }}
QPushButton#NavItem:checked {{
    background: {p.accent_subtle};
    color: {p.text};
    font-weight: {Type.WEIGHT_SEMIBOLD};
}}
QPushButton#NavItem:checked:hover {{ background: {p.accent_subtle}; color: {p.text}; }}
QPushButton#NavItem:checked:pressed {{ background: {p.accent_subtle}; color: {p.text}; }}
QPushButton#NavItem:disabled {{ background: transparent; color: {p.text_subtle}; }}

/* ---- Toolbar buttons --------------------------------------------------- */

QToolButton {{
    background: transparent;
    border: none;
    border-radius: {Radius.SM}px;
    padding: {Space.XS}px;
}}
QToolButton:hover {{ background: {p.surface_hover}; }}
QToolButton:pressed {{ background: {p.surface_active}; }}
QToolButton:focus {{ background: {p.surface_hover}; }}
QToolButton:checked {{ background: {p.surface_active}; }}
QToolButton:disabled {{ background: transparent; color: {p.text_subtle}; }}

/* ---- Breadcrumb -------------------------------------------------------- */

QPushButton#Crumb {{
    background: transparent;
    border: none;
    border-radius: {Radius.SM}px;
    padding: 3px {Space.SM}px;
    color: {p.text_muted};
    font-size: {Type.BODY}px;
}}
QPushButton#Crumb:hover {{ background: {p.surface_hover}; color: {p.text}; }}
QPushButton#Crumb:pressed {{ background: {p.surface_active}; color: {p.text}; }}
QPushButton#Crumb:focus {{ background: {p.surface_hover}; color: {p.text}; }}
QPushButton#Crumb:disabled {{ background: transparent; color: {p.text_subtle}; }}
QPushButton#Crumb[current="true"] {{
    color: {p.text};
    font-weight: {Type.WEIGHT_SEMIBOLD};
}}

QLabel#CrumbSep {{ color: {p.text_subtle}; }}

/* ---- Search ------------------------------------------------------------ */

QLineEdit#Search {{
    background: {raised};
    border: 1px solid {p.border};
    border-radius: {Radius.PILL}px;
    padding: {Space.SM}px {Space.MD}px {Space.SM}px 34px;
    color: {p.text};
    selection-background-color: {p.accent};
    selection-color: {p.accent_text};
}}
QLineEdit#Search:hover {{ border-color: {p.border_strong}; }}
QLineEdit#Search:focus {{ border-color: {p.accent}; background: {raised}; }}

/* ---- File table -------------------------------------------------------- */

QTableView {{
    background: transparent;
    border: none;
    gridline-color: transparent;
    selection-background-color: {p.accent_subtle};
    selection-color: {p.text};
}}
/* Hover and selection backgrounds belong to AnimatedRowDelegate, which paints
   them across the whole row. A rule here cannot: the stylesheet style is
   applied per cell and re-derives :selected from the index, so it repaints a
   rounded pill inside every cell and the row breaks into pieces with visible
   seams at the column boundaries. Only geometry and text colour are set. */
QTableView::item {{
    border: none;
    padding: 0px {Space.SM}px;
}}
QTableView::item:selected {{ color: {p.text}; }}

QHeaderView::section {{
    background: transparent;
    border: none;
    border-bottom: 1px solid {p.border};
    padding: {Space.SM}px;
    color: {p.text_muted};
    font-size: {Type.CAPTION}px;
    font-weight: {Type.WEIGHT_SEMIBOLD};
}}
QHeaderView::section:hover {{ color: {p.text}; }}
QHeaderView::up-arrow, QHeaderView::down-arrow {{
    subcontrol-origin: content;
    subcontrol-position: center right;
    width: 8px;
    height: 8px;
}}

/* ---- Details pane ------------------------------------------------------ */

QWidget#Details {{
    background: {panel};
    border-left: 1px solid {p.border};
}}
QLabel#DetailName {{
    font-size: {Type.BODY_STRONG}px;
    font-weight: {Type.WEIGHT_SEMIBOLD};
    color: {p.text};
}}
QLabel#DetailKey {{
    font-size: {Type.CAPTION}px;
    font-weight: {Type.WEIGHT_SEMIBOLD};
    color: {p.text_muted};
}}
QLabel#DetailValue {{
    font-size: {Type.BODY}px;
    color: {p.text_muted};
}}

/* ---- Scrollbars: thin overlay, not Qt's default slab -------------------- */

QScrollBar:vertical {{
    background: transparent;
    width: 10px;
    margin: 0;
}}
QScrollBar::handle:vertical {{
    background: {p.border_strong};
    border-radius: 5px;
    min-height: 32px;
}}
QScrollBar::handle:vertical:hover {{ background: {p.text_subtle}; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 0; }}
QScrollBar::handle:horizontal {{
    background: {p.border_strong};
    border-radius: 5px;
    min-width: 32px;
}}
QScrollBar::handle:horizontal:hover {{ background: {p.text_subtle}; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}

/* ---- Menus and tooltips ------------------------------------------------- */

QMenu {{
    background: {raised};
    border: 1px solid {p.border};
    border-radius: {Radius.MD}px;
    padding: {Space.XS}px;
}}
QMenu::item {{
    padding: {Space.SM}px {Space.LG}px;
    border-radius: {Radius.SM}px;
    color: {p.text_muted};
}}
QMenu::item:selected {{ background: {p.surface_hover}; color: {p.text}; }}
QMenu::separator {{ height: 1px; background: {p.border}; margin: {Space.XS}px; }}

QToolTip {{
    background: {raised};
    border: 1px solid {p.border};
    border-radius: {Radius.SM}px;
    padding: {Space.XS}px {Space.SM}px;
    color: {p.text};
}}
"""


def _opaque(colour: str) -> str:
    """Drop the alpha from an rgba() token.

    Used when no compositor material sits behind the window: a translucent
    surface with nothing behind it shows the desktop straight through the app.
    """
    if not colour.startswith("rgba("):
        return colour
    parts = [part.strip() for part in colour.removeprefix("rgba(").rstrip(")").split(",")]
    red, green, blue = parts[0], parts[1], parts[2]
    return f"rgb({red}, {green}, {blue})"
