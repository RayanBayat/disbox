"""The main Disbox window.

Layout follows the shape people already know from every file manager: a
persistent sidebar for places, a header carrying navigation and search, and the
content filling the rest. Familiar structure is not a lack of ambition -- an
interface people can use without learning it is the goal, and novelty in
navigation is spent budget.

The glass comes from the compositor via `theme.backdrop`, not from painted
gradients. Surfaces are translucent so the material shows through, which is why
nothing here sets an opaque background.

Read-only for now. Upload, download, rename and delete arrive with the transfer
engine; the toolbar deliberately offers no button that would do nothing.
"""

import uuid
from typing import Final

from PySide6.QtCore import QEasingCurve, QPropertyAnimation, QSize, Qt
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFrame,
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMainWindow,
    QPushButton,
    QSizePolicy,
    QStackedWidget,
    QTableView,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from disbox.core.search import search
from disbox.core.vault import Vault
from disbox.gui.models.file_table import Column, FileTableModel, format_size
from disbox.gui.theme import Backdrop, Motion, Palette, Space, apply_backdrop, icons
from disbox.gui.theme.stylesheet import build_stylesheet
from disbox.gui.theme.tokens import DARK
from disbox.gui.views.chrome import FramelessMixin, TitleBar

__all__ = ["MainWindow"]

_SEARCH_RESULT_LIMIT: Final = 500
_ROW_HEIGHT: Final = 40
_SIDEBAR_WIDTH: Final = 208
_ICON_BUTTON: Final = 30


# mypy flags nativeEvent as conflicting across the two bases; the mixin's
# signature matches what Qt actually calls, and the runtime override works.
class MainWindow(FramelessMixin, QMainWindow):  # type: ignore[misc]
    """Browse a vault: navigate directories, search, and inspect."""

    def __init__(self, vault: Vault, palette: Palette = DARK) -> None:
        """Open a window onto `vault`, showing its root directory."""
        super().__init__()
        self._vault = vault
        self._palette = palette
        self._directory: uuid.UUID | None = None
        self._history: list[uuid.UUID | None] = []
        self._search_results: list[str] = []
        self._searching = False
        self._crumbs: list[tuple[str, uuid.UUID | None]] = []

        self.setWindowTitle(f"Disbox — {vault.path.stem}")
        self.resize(1180, 720)
        self.setMinimumSize(720, 420)

        self.table_model = FileTableModel(vault, palette=palette)
        self._build_ui()
        self.setStyleSheet(build_stylesheet(palette))
        self._apply_window_material()
        self._refresh()

    # -------------------------------------------------------------- chrome --

    def _apply_window_material(self) -> None:
        """Request a compositor backdrop, and stay opaque if there is none."""
        handle = int(self.winId())
        if apply_backdrop(handle, Backdrop.MICA):
            # Only stop painting our own background once the material is
            # confirmed; otherwise the window would render as a hole.
            self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)

    def _icon_button(self, name: str, tooltip: str) -> QToolButton:
        """Build a flat, icon-only button in the header."""
        button = QToolButton()
        button.setIcon(icons.icon(name, self._palette.text_muted, size=18, ratio=2.0))
        button.setIconSize(QSize(18, 18))
        button.setToolTip(tooltip)
        button.setFixedSize(_ICON_BUTTON, _ICON_BUTTON)
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        return button

    def _build_sidebar(self) -> QWidget:
        """Places, and the vault's identity."""
        sidebar = QWidget()
        sidebar.setObjectName("Sidebar")
        sidebar.setFixedWidth(_SIDEBAR_WIDTH)

        layout = QVBoxLayout(sidebar)
        layout.setContentsMargins(0, Space.LG, 0, Space.MD)
        layout.setSpacing(0)

        vault_row = QHBoxLayout()
        vault_row.setContentsMargins(Space.LG, 0, Space.LG, Space.LG)
        vault_row.setSpacing(Space.SM)
        mark = QLabel()
        mark.setPixmap(icons.pixmap("vault", self._palette.accent, size=18, ratio=2.0))
        name = QLabel(self._vault.path.stem)
        name.setObjectName("BrandName")
        name.setToolTip(str(self._vault.path))
        vault_row.addWidget(mark)
        vault_row.addWidget(name)
        vault_row.addStretch(1)
        layout.addLayout(vault_row)

        section = QLabel("PLACES")
        section.setObjectName("SectionLabel")
        layout.addWidget(section)

        self._nav_buttons: dict[str, QPushButton] = {}
        for key, label, icon_name in (("vault", "All files", "vault"),):  # more places with M7
            button = QPushButton(f"  {label}")
            button.setObjectName("NavItem")
            button.setIcon(icons.icon(icon_name, self._palette.text_muted, size=18, ratio=2.0))
            button.setIconSize(QSize(18, 18))
            button.setCheckable(True)
            button.setChecked(key == "vault")
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.clicked.connect(lambda _=False: self.navigate_to(None))
            self._nav_buttons[key] = button
            layout.addWidget(button)

        layout.addStretch(1)
        layout.addWidget(self._build_storage_meter())
        return sidebar

    def _build_storage_meter(self) -> QWidget:
        """Show what the vault holds, so the sidebar ends on information."""
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(Space.LG, Space.SM, Space.LG, Space.SM)
        layout.setSpacing(Space.XS)

        row = self._vault.connection.execute(
            "SELECT count(*), coalesce(sum(size), 0) FROM nodes "
            "WHERE deleted_at IS NULL AND kind = 'file'"
        ).fetchone()
        stored = format_size(row[1])

        caption = QLabel(f"{stored} stored")
        caption.setObjectName("MeterCaption")
        detail = QLabel(f"{row[0]:,} file{'' if row[0] == 1 else 's'}")
        detail.setObjectName("StatusText")

        # No quota exists to divide by, so the bar is a presence indicator
        # rather than a percentage; it must not imply a limit we cannot know.
        track = QFrame()
        track.setObjectName("MeterTrack")
        track.setFixedHeight(6)
        fill = QFrame(track)
        fill.setObjectName("MeterFill")
        fill.setFixedHeight(6)
        fill.setFixedWidth(int(_SIDEBAR_WIDTH * 0.42))

        layout.addWidget(caption)
        layout.addWidget(track)
        layout.addWidget(detail)
        return panel

    def _build_header(self) -> QWidget:
        """Navigation, breadcrumb, and search."""
        header = QWidget()
        header.setObjectName("HeaderBar")
        header.setFixedHeight(56)

        layout = QHBoxLayout(header)
        layout.setContentsMargins(Space.MD, Space.SM, Space.LG, Space.SM)
        layout.setSpacing(Space.XS)

        self._back_button = self._icon_button("arrow-left", "Back")
        self._back_button.clicked.connect(self.navigate_back)
        self._up_button = self._icon_button("arrow-up", "Up one level")
        self._up_button.clicked.connect(self.navigate_up)
        layout.addWidget(self._back_button)
        layout.addWidget(self._up_button)

        self._crumb_bar = QWidget()
        self._crumb_layout = QHBoxLayout(self._crumb_bar)
        self._crumb_layout.setContentsMargins(Space.SM, 0, 0, 0)
        self._crumb_layout.setSpacing(2)
        layout.addWidget(self._crumb_bar)
        layout.addStretch(1)

        search_wrap = QWidget()
        search_wrap.setFixedWidth(300)
        wrap_layout = QHBoxLayout(search_wrap)
        wrap_layout.setContentsMargins(0, 0, 0, 0)

        self._search_box = QLineEdit()
        self._search_box.setObjectName("Search")
        self._search_box.setPlaceholderText("Search everything")
        self._search_box.setClearButtonEnabled(True)
        self._search_box.textChanged.connect(self.apply_search)
        wrap_layout.addWidget(self._search_box)

        # Overlaid rather than in the layout, so the glyph sits inside the pill.
        glyph = QLabel(search_wrap)
        glyph.setPixmap(icons.pixmap("search", self._palette.text_subtle, size=15, ratio=2.0))
        glyph.move(12, 11)
        glyph.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)

        layout.addWidget(search_wrap)
        return header

    def _build_table(self) -> QTableView:
        """The file list."""
        table = QTableView()
        table.setModel(self.table_model)
        table.setObjectName("FileTable")
        table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        table.setShowGrid(False)
        table.setAlternatingRowColors(False)
        table.setFrameShape(QFrame.Shape.NoFrame)
        table.setSortingEnabled(False)
        table.setMouseTracking(True)  # required for per-row hover styling
        table.doubleClicked.connect(self._on_double_click)

        vertical = table.verticalHeader()
        vertical.setVisible(False)
        vertical.setSectionResizeMode(QHeaderView.ResizeMode.Fixed)
        vertical.setDefaultSectionSize(_ROW_HEIGHT)

        header = table.horizontalHeader()
        header.setSectionResizeMode(Column.NAME, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(Column.SIZE, QHeaderView.ResizeMode.Fixed)
        header.setSectionResizeMode(Column.MODIFIED, QHeaderView.ResizeMode.Fixed)
        header.setHighlightSections(False)
        table.setColumnWidth(Column.SIZE, 110)
        table.setColumnWidth(Column.MODIFIED, 160)
        return table

    def _build_empty_state(self) -> QWidget:
        """Shown instead of an empty grid, which reads as a bug."""
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.setSpacing(Space.SM)

        glyph = QLabel()
        glyph.setPixmap(icons.pixmap("folder", self._palette.text_subtle, size=44, ratio=2.0))
        glyph.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self._empty_title = QLabel("Nothing here yet")
        self._empty_title.setObjectName("EmptyTitle")
        self._empty_title.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self._empty_hint = QLabel("This folder is empty.")
        self._empty_hint.setObjectName("EmptyHint")
        self._empty_hint.setAlignment(Qt.AlignmentFlag.AlignCenter)

        layout.addWidget(glyph)
        layout.addWidget(self._empty_title)
        layout.addWidget(self._empty_hint)
        return panel

    def _build_ui(self) -> None:
        """Assemble sidebar, header, content, and status strip."""
        root = QWidget()
        root.setObjectName("Root")
        outer = QVBoxLayout(root)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        self.title_bar = TitleBar(self, self._palette)
        outer.addWidget(self.title_bar)

        body = QWidget()
        root_layout = QHBoxLayout(body)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)
        root_layout.addWidget(self._build_sidebar())

        content = QWidget()
        content.setObjectName("Content")
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(0)
        content_layout.addWidget(self._build_header())

        self._table = self._build_table()
        self._stack = QStackedWidget()
        self._stack.addWidget(self._table)
        self._stack.addWidget(self._build_empty_state())
        self._stack.setContentsMargins(Space.SM, Space.SM, Space.SM, 0)
        content_layout.addWidget(self._stack, 1)

        self._status = QLabel()
        self._status.setObjectName("StatusText")
        self._status.setContentsMargins(Space.LG, Space.SM, Space.LG, Space.SM)
        self._status.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        content_layout.addWidget(self._status)

        root_layout.addWidget(content, 1)
        outer.addWidget(body, 1)
        self.setCentralWidget(root)
        self.install_frameless_chrome(self.title_bar)

        # A short cross-fade on the content makes a directory change read as
        # movement rather than a jump cut, without delaying anything.
        self._fade_effect = QGraphicsOpacityEffect(self._stack)
        self._stack.setGraphicsEffect(self._fade_effect)
        self._fade = QPropertyAnimation(self._fade_effect, b"opacity", self)
        self._fade.setDuration(Motion.FAST)
        self._fade.setEasingCurve(QEasingCurve.Type.OutCubic)

        for sequence, slot in (
            (QKeySequence.StandardKey.Back, self.navigate_back),
            (QKeySequence("Alt+Up"), self.navigate_up),
            (QKeySequence.StandardKey.Find, self._search_box.setFocus),
        ):
            action = QAction(self)
            action.setShortcut(sequence)
            action.triggered.connect(slot)
            self.addAction(action)

    # ---------------------------------------------------------- navigation --

    @property
    def current_directory(self) -> uuid.UUID | None:
        """Directory currently shown, or None for the vault root."""
        return self._directory

    def navigate_to(self, directory: uuid.UUID | None) -> None:
        """Show `directory`, recording where we came from."""
        self._history.append(self._directory)
        self._directory = directory
        self._reset_search()
        self._refresh()

    def navigate_back(self) -> None:
        """Return to the previously shown directory, if there is one."""
        if not self._history:
            return
        self._directory = self._history.pop()
        self._reset_search()
        self._refresh()

    def navigate_up(self) -> None:
        """Move to the parent of the current directory."""
        if self._directory is None:
            return
        row = self._vault.connection.execute(
            "SELECT parent_id FROM nodes WHERE id = ?", (self._directory.bytes,)
        ).fetchone()
        self.navigate_to(uuid.UUID(bytes=row[0]) if row and row[0] is not None else None)

    def status_text(self) -> str:
        """The status strip's current message."""
        return self._status.text()

    def breadcrumb_text(self) -> str:
        """Path to the current directory, as text. Used by tests."""
        return " / ".join(label for label, _ in self._crumbs)

    # -------------------------------------------------------------- search --

    def apply_search(self, query: str) -> None:
        """Show matches for `query` from anywhere in the tree, or clear it."""
        text = query.strip()
        if not text:
            self._reset_search()
            self._refresh()
            return

        hits = search(self._vault.connection, text, limit=_SEARCH_RESULT_LIMIT)
        self._searching = True
        self._search_results = [hit.name for hit in hits]
        self.table_model.set_results([hit.node_id for hit in hits])

        self._set_crumbs([(f"Results for “{text}”", None)])
        self._empty_title.setText("No matches")
        self._empty_hint.setText(f"Nothing in this vault matches “{text}”.")
        self._stack.setCurrentIndex(0 if hits else 1)
        self._status.setText(f"{len(hits)} match{'' if len(hits) == 1 else 'es'}")
        self._back_button.setEnabled(bool(self._history))
        self._up_button.setEnabled(False)

    def result_names(self) -> list[str]:
        """Names of the current search results, empty when not searching."""
        return list(self._search_results)

    def _reset_search(self) -> None:
        self._searching = False
        self._search_results = []
        if self._search_box.text():
            self._search_box.blockSignals(True)
            self._search_box.clear()
            self._search_box.blockSignals(False)

    # ------------------------------------------------------------- helpers --

    def _refresh(self) -> None:
        """Re-read the current directory and update the surrounding chrome."""
        self.table_model.set_directory(self._directory)
        self._set_crumbs(self._compute_crumbs())

        self._play_fade()
        count = self.table_model.rowCount()
        self._empty_title.setText("Nothing here yet")
        self._empty_hint.setText("This folder is empty.")
        self._stack.setCurrentIndex(0 if count else 1)
        self._status.setText(f"{count} item{'' if count == 1 else 's'}")
        self._up_button.setEnabled(self._directory is not None)
        self._back_button.setEnabled(bool(self._history))

    def _play_fade(self) -> None:
        """Fade the content back in after its contents change."""
        if not hasattr(self, "_fade"):
            return  # first refresh runs before the animation is built
        self._fade.stop()
        self._fade.setStartValue(0.35)
        self._fade.setEndValue(1.0)
        self._fade.start()

    def _compute_crumbs(self) -> list[tuple[str, uuid.UUID | None]]:
        """Walk from the current directory to the root, building the trail."""
        trail: list[tuple[str, uuid.UUID | None]] = []
        node = self._directory
        seen: set[uuid.UUID] = set()
        while node is not None and node not in seen:
            seen.add(node)  # a corrupt tree must not hang the window
            row = self._vault.connection.execute(
                "SELECT name, parent_id FROM nodes WHERE id = ?", (node.bytes,)
            ).fetchone()
            if row is None:
                break
            trail.append((row[0], node))
            node = uuid.UUID(bytes=row[1]) if row[1] is not None else None
        return [("All files", None), *reversed(trail)]

    def _set_crumbs(self, crumbs: list[tuple[str, uuid.UUID | None]]) -> None:
        """Rebuild the breadcrumb as clickable chips."""
        self._crumbs = crumbs
        while (item := self._crumb_layout.takeAt(0)) is not None:
            if (widget := item.widget()) is not None:
                widget.deleteLater()

        for position, (label, target) in enumerate(crumbs):
            if position:
                separator = QLabel()
                separator.setObjectName("CrumbSep")
                separator.setPixmap(
                    icons.pixmap("chevron-right", self._palette.text_subtle, size=13, ratio=2.0)
                )
                self._crumb_layout.addWidget(separator)

            chip = QPushButton(label)
            chip.setObjectName("Crumb")
            chip.setProperty("current", "true" if position == len(crumbs) - 1 else "false")
            chip.setCursor(Qt.CursorShape.PointingHandCursor)
            chip.clicked.connect(lambda _=False, node=target: self.navigate_to(node))
            self._crumb_layout.addWidget(chip)

    def _on_double_click(self, index: object) -> None:
        """Enter a folder when its row is double-clicked."""
        if self._searching:
            return
        row = getattr(index, "row", lambda: -1)()
        node_id = self.table_model.node_id_at(row)
        if node_id is None:
            return
        kind = self._vault.connection.execute(
            "SELECT kind FROM nodes WHERE id = ?", (node_id.bytes,)
        ).fetchone()
        if kind and kind[0] == "dir":
            self.navigate_to(node_id)
