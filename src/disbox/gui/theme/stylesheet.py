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


def build_stylesheet(p: Palette) -> str:
    """Return the complete stylesheet for `p`."""
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
    background: {p.window};
}}

QWidget#TitleBar {{
    background: transparent;
}}

QLabel#TitleText {{
    font-size: {Type.BODY}px;
    font-weight: 600;
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
QToolButton#CloseButton:hover {{ background: {p.danger}; }}

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
    background: {p.surface};
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
    font-size: {Type.SUBTITLE}px;
    font-weight: 600;
    color: {p.text};
}}

QLabel#SectionLabel {{
    font-size: {Type.CAPTION}px;
    font-weight: 600;
    color: {p.text_subtle};
    letter-spacing: 0.6px;
    padding: {Space.SM}px {Space.MD}px {Space.XS}px {Space.MD}px;
}}

QLabel#MeterCaption {{
    font-size: {Type.BODY}px;
    font-weight: 600;
    color: {p.text};
}}

QLabel#StatusText {{
    color: {p.text_muted};
    font-size: {Type.CAPTION}px;
}}

QLabel#EmptyTitle {{
    font-size: {Type.TITLE}px;
    font-weight: 600;
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
QPushButton#NavItem:checked {{
    background: {p.accent_subtle};
    color: {p.text};
    font-weight: 600;
}}

/* ---- Toolbar buttons --------------------------------------------------- */

QToolButton {{
    background: transparent;
    border: none;
    border-radius: {Radius.SM}px;
    padding: {Space.XS}px;
}}
QToolButton:hover {{ background: {p.surface_hover}; }}
QToolButton:pressed {{ background: {p.surface_active}; }}
QToolButton:disabled {{ opacity: 0.4; }}

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
QPushButton#Crumb[current="true"] {{ color: {p.text}; font-weight: 600; }}

QLabel#CrumbSep {{ color: {p.text_subtle}; }}

/* ---- Search ------------------------------------------------------------ */

QLineEdit#Search {{
    background: {p.surface_raised};
    border: 1px solid {p.border};
    border-radius: {Radius.PILL}px;
    padding: {Space.SM}px {Space.MD}px {Space.SM}px 34px;
    color: {p.text};
    selection-background-color: {p.accent};
    selection-color: {p.accent_text};
}}
QLineEdit#Search:hover {{ border-color: {p.border_strong}; }}
QLineEdit#Search:focus {{ border-color: {p.accent}; background: {p.surface_raised}; }}

/* ---- File table -------------------------------------------------------- */

QTableView {{
    background: transparent;
    border: none;
    gridline-color: transparent;
    selection-background-color: {p.accent_subtle};
    selection-color: {p.text};
}}
QTableView::item {{
    border: none;
    border-radius: {Radius.SM}px;
    padding: 0px {Space.SM}px;
    color: {p.text_muted};
}}
QTableView::item:hover {{ background: {p.surface_hover}; }}
QTableView::item:selected {{ background: {p.accent_subtle}; color: {p.text}; }}

QHeaderView::section {{
    background: transparent;
    border: none;
    border-bottom: 1px solid {p.border};
    padding: {Space.SM}px;
    color: {p.text_muted};
    font-size: {Type.CAPTION}px;
    font-weight: 600;
}}
QHeaderView::section:hover {{ color: {p.text_muted}; }}

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
    background: {p.surface_raised};
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
    background: {p.surface_raised};
    border: 1px solid {p.border};
    border-radius: {Radius.SM}px;
    padding: {Space.XS}px {Space.SM}px;
    color: {p.text};
}}
"""
