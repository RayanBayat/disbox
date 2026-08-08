"""The main Disbox window.

Layout follows the shape people already know from every file manager: a
persistent sidebar for places, a header carrying navigation and search, and the
content filling the rest. Familiar structure is not a lack of ambition -- an
interface people can use without learning it is the goal, and novelty in
navigation is spent budget.

The glass comes from the compositor via `theme.backdrop`, not from painted
gradients. Surfaces are translucent so the material shows through, which is why
nothing here sets an opaque background.

Create, rename and delete work against the vault directly. Upload and download
still wait on the transfer engine being wired in; the toolbar deliberately
offers no button that would do nothing.

Operations are exposed as plain methods taking their arguments, with the dialogs
as thin `prompt_*` wrappers. That split is what makes them testable -- a method
that opens a modal to ask for a name cannot be driven from a test without
stubbing Qt out from under it.
"""

import uuid
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Final

from PySide6.QtCore import QMimeData, QPoint, QSize, Qt, Signal
from PySide6.QtGui import QAction, QDrag, QDragEnterEvent, QDropEvent, QKeySequence
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMenu,
    QPushButton,
    QSizePolicy,
    QStackedWidget,
    QTableView,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from disbox.config import load_settings
from disbox.core.engine import TransferEngine
from disbox.core.filesystem import FileSystem, NameCollision
from disbox.core.search import search
from disbox.core.tree_transfer import TreeTransfer
from disbox.core.undo import describe_next_undo, undo_last
from disbox.core.vault import Vault
from disbox.errors import DisboxError
from disbox.gui.bridge import AsyncBridge, AsyncTask, Work
from disbox.gui.drag import DeferredFileMimeData
from disbox.gui.models.file_table import Column, FileTableModel, format_size
from disbox.gui.notifications import NotificationLog
from disbox.gui.theme import Backdrop, Palette, Space, apply_backdrop, icons
from disbox.gui.theme.backdrop import (
    round_window_corners,
    set_dark_titlebar,
    system_prefers_dark,
)
from disbox.gui.theme.stylesheet import build_stylesheet
from disbox.gui.theme.tokens import DARK, LIGHT
from disbox.gui.views.chrome import FramelessMixin, TitleBar
from disbox.gui.views.details_pane import DetailsPane
from disbox.gui.views.folder_tree import FolderTree
from disbox.gui.views.notifications_dialog import NotificationsDialog
from disbox.gui.views.properties_dialog import PropertiesDialog
from disbox.gui.views.row_delegate import AnimatedRowDelegate
from disbox.gui.views.settings_dialog import SettingsDialog
from disbox.gui.views.transfer_dock import TransferDock
from disbox.gui.views.trash_dialog import TrashDialog

__all__ = ["MainWindow"]

_SEARCH_RESULT_LIMIT: Final = 500
_ROW_HEIGHT: Final = 44
_SIDEBAR_WIDTH: Final = 208
_ICON_BUTTON: Final = 30


# mypy flags nativeEvent as conflicting across the two bases; the mixin's
# signature matches what Qt actually calls, and the runtime override works.
class MainWindow(FramelessMixin, QMainWindow):  # type: ignore[misc]
    """Browse a vault: navigate directories, search, transfer, and inspect."""

    #: (completed, total) bytes for the transfer currently running.
    transfer_progress = Signal(int, int)
    #: Emitted when the transfer queue empties, successfully or not.
    transfers_idle = Signal()

    def __init__(
        self,
        vault: Vault,
        palette: Palette = DARK,
        *,
        bridge: AsyncBridge | None = None,
        engine: TransferEngine | None = None,
    ) -> None:
        """Open a window onto `vault`, showing its root directory.

        Args:
            vault: The vault to browse.
            palette: Starting theme.
            bridge: Where asynchronous work is submitted. Optional so tests and
                read-only use need not start a thread they will not use.
            engine: Moves file contents. Without one the window still browses,
                and transfer actions report that storage is not configured
                rather than failing at the point of use.
        """
        super().__init__()
        self._vault = vault
        self._bridge = bridge
        self._engine = engine
        self._queue: list[Path] = []
        self._transferring = False
        self._current_task: AsyncTask | None = None
        self.notifications = NotificationLog()
        self._palette = palette
        self._directory: uuid.UUID | None = None
        self._history: list[uuid.UUID | None] = []
        self._search_results: list[str] = []
        self._searching = False
        self._crumbs: list[tuple[str, uuid.UUID | None]] = []
        # Observation point for tests: the compositor call itself cannot be
        # asserted on, since it writes to a window handle and returns nothing
        # visible from Python.
        self._material_hook: Callable[[bool], None] | None = None

        self.setWindowTitle(f"Disbox — {vault.path.stem}")
        self.resize(1180, 720)
        self.setMinimumSize(720, 420)

        self.table_model = FileTableModel(vault, palette=palette)
        self._build_ui()
        # The material decides whether surfaces may be translucent, so it is
        # settled before the stylesheet that depends on it.
        self._apply_window_material()
        self.setStyleSheet(build_stylesheet(palette, translucent=self._translucent))
        self._refresh()

    # -------------------------------------------------------------- chrome --

    def _apply_window_material(self) -> None:
        """Request a compositor backdrop matching the current palette.

        DWMWA_USE_IMMERSIVE_DARK_MODE is not only about the caption: it decides
        which way the compositor tints Mica. Without it the material follows the
        *system* theme, so a light palette over a dark Windows leaves light
        translucent surfaces sitting on dark Mica -- which reads as muddy grey
        rather than light. It must therefore be reapplied on every theme
        change, not set once at construction.
        """
        handle = int(self.winId())
        set_dark_titlebar(handle, dark=self._palette.is_dark)
        if self._material_hook is not None:
            self._material_hook(self._palette.is_dark)

        # A frameless window loses the compositor's corner rounding; ask for it
        # back, or the app sits on the desktop with hard right angles that no
        # other Windows 11 window has.
        round_window_corners(handle)

        # Glass only when the app theme agrees with the system's. Mica is drawn
        # from the wallpaper and tinted by Windows, so a light app over a dark
        # system puts light translucent surfaces on a dark material and renders
        # as muddy grey. An opaque window that looks correct beats a
        # translucent one that does not.
        self._translucent = self._palette.is_dark == system_prefers_dark()
        if self._translucent and apply_backdrop(handle, Backdrop.MICA):
            self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        else:
            apply_backdrop(handle, Backdrop.NONE)
            self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, False)

    def _icon_button(self, name: str, tooltip: str) -> QToolButton:
        """Build a flat, icon-only button in the header."""
        button = QToolButton()
        button.setIcon(icons.icon(name, self._palette.text_muted, size=18, ratio=2.0))
        button.setIconSize(QSize(18, 18))
        button.setToolTip(tooltip)
        # A tooltip needs a pointer to appear, so it is no substitute for a
        # name a screen reader can announce. Both come from the same text.
        button.setAccessibleName(tooltip)
        button.setFixedSize(_ICON_BUTTON, _ICON_BUTTON)
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        return button

    def _build_sidebar(self) -> QWidget:
        """Places, and the vault's identity."""
        sidebar = QWidget()
        sidebar.setObjectName("Sidebar")
        sidebar.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
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

        section = QLabel("Places")
        section.setObjectName("SectionLabel")
        layout.addWidget(section)

        self._nav_buttons: dict[str, QPushButton] = {}
        places = (("vault", "All files", "vault"), ("trash", "Trash", "trash"))
        for key, label, icon_name in places:
            button = QPushButton(f"  {label}")
            button.setObjectName("NavItem")
            button.setIcon(icons.icon(icon_name, self._palette.text_muted, size=18, ratio=2.0))
            button.setIconSize(QSize(18, 18))
            button.setCheckable(True)
            button.setChecked(key == "vault")
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.setFocusPolicy(Qt.FocusPolicy.TabFocus)
            button.setAccessibleName(label)
            if key == "trash":
                button.setCheckable(False)
                button.clicked.connect(lambda _=False: self.open_trash())
            else:
                button.clicked.connect(lambda _=False: self.navigate_to(None))
            self._nav_buttons[key] = button
            layout.addWidget(button)

        folders = QLabel("Folders")
        folders.setObjectName("SectionLabel")
        layout.addWidget(folders)

        # The tree takes the stretch the sidebar used to waste on empty space.
        self.tree = FolderTree(self._vault, self._palette)
        self.tree.setAccessibleName("Folder tree")
        self.tree.directory_selected.connect(self.navigate_to)
        layout.addWidget(self.tree, 1)

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
        header.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        header.setFixedHeight(56)

        layout = QHBoxLayout(header)
        layout.setContentsMargins(Space.MD, Space.SM, Space.LG, Space.SM)
        layout.setSpacing(Space.XS)

        self._theme_button = self._icon_button("contrast", "Switch theme")
        self._theme_button.clicked.connect(self.toggle_theme)
        self._notices_button = self._icon_button("info", "Notifications")
        self._notices_button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self._notices_button.clicked.connect(self.open_notifications)
        self._settings_button = self._icon_button("settings", "Settings")
        self._settings_button.clicked.connect(self.open_settings)

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
        # Not a fixed width: the details pane takes 288px off this row when it
        # opens, and a header that cannot shrink simply overflows and gets
        # painted over by the pane.
        search_wrap.setMinimumWidth(168)
        search_wrap.setMaximumWidth(300)
        search_wrap.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        wrap_layout = QHBoxLayout(search_wrap)
        wrap_layout.setContentsMargins(0, 0, 0, 0)

        self._search_box = QLineEdit()
        self._search_box.setObjectName("Search")
        self._search_box.setPlaceholderText("Search everything")
        self._search_box.setAccessibleName("Search everything")
        self._search_box.setClearButtonEnabled(True)
        self._search_box.textChanged.connect(self.apply_search)
        wrap_layout.addWidget(self._search_box)

        # Overlaid rather than in the layout, so the glyph sits inside the pill.
        glyph = QLabel(search_wrap)
        glyph.setPixmap(icons.pixmap("search", self._palette.text_subtle, size=15, ratio=2.0))
        glyph.move(12, 11)
        glyph.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)

        layout.addWidget(search_wrap)
        layout.addWidget(self._notices_button)
        layout.addWidget(self._theme_button)
        layout.addWidget(self._settings_button)
        return header

    def _build_table(self) -> QTableView:
        """The file list."""
        table = QTableView()
        table.setModel(self.table_model)
        table.setObjectName("FileTable")
        table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        table.setAccessibleName("Files in this folder")
        # Dragging out is handled by the window, so the view only has to start
        # the gesture rather than know what a vault node is.
        table.setDragEnabled(True)
        table.setDragDropMode(QAbstractItemView.DragDropMode.DragOnly)
        table.startDrag = self._start_drag  # type: ignore[method-assign]
        table.setShowGrid(False)
        table.setAlternatingRowColors(False)
        table.setFrameShape(QFrame.Shape.NoFrame)
        table.setSortingEnabled(True)
        table.setMouseTracking(True)  # required for per-row hover styling
        table.doubleClicked.connect(self._on_double_click)
        # Qt Style Sheets cannot ease anything, so hover and selection are
        # painted by a delegate instead of declared in QSS.
        self.row_delegate = AnimatedRowDelegate(table, self._palette)
        table.setItemDelegate(self.row_delegate)

        vertical = table.verticalHeader()
        vertical.setVisible(False)
        vertical.setSectionResizeMode(QHeaderView.ResizeMode.Fixed)
        vertical.setDefaultSectionSize(_ROW_HEIGHT)

        header = table.horizontalHeader()
        header.setSectionResizeMode(Column.NAME, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(Column.SIZE, QHeaderView.ResizeMode.Fixed)
        header.setSectionResizeMode(Column.MODIFIED, QHeaderView.ResizeMode.Fixed)
        header.setHighlightSections(False)
        header.setSortIndicatorShown(False)
        header.setSectionsClickable(True)
        header.sortIndicatorChanged.connect(self._on_sort_changed)
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
        root.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
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
        content.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
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

        self.dock = TransferDock(self._palette)
        self.dock.cancel_requested.connect(self.cancel_transfers)
        content_layout.addWidget(self.dock)

        self._status = QLabel()
        self._status.setObjectName("StatusText")
        self._status.setContentsMargins(Space.LG, Space.SM, Space.LG, Space.SM)
        self._status.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        content_layout.addWidget(self._status)

        root_layout.addWidget(content, 1)
        self.details = DetailsPane(self._vault, self._palette)
        root_layout.addWidget(self.details)
        self._table.selectionModel().selectionChanged.connect(self._on_selection_changed)
        outer.addWidget(body, 1)
        self.setCentralWidget(root)
        self.install_frameless_chrome(self.title_bar)

        for sequence, slot in (
            (QKeySequence.StandardKey.Back, self.navigate_back),
            (QKeySequence("Alt+Up"), self.navigate_up),
            (QKeySequence.StandardKey.Find, self._search_box.setFocus),
            (QKeySequence("Ctrl+Shift+N"), self.prompt_new_folder),
            (QKeySequence("F2"), self.prompt_rename),
            (QKeySequence("Alt+Return"), self.show_properties),
            (QKeySequence.StandardKey.Undo, self.undo_last_change),
            (QKeySequence.StandardKey.Delete, self.delete_selected),
        ):
            action = QAction(self)
            action.setShortcut(sequence)
            action.triggered.connect(slot)
            self.addAction(action)

        # Dropping onto the window, not only the table: the whole content area
        # reads as the current folder, and a drop that lands two pixels off the
        # list would otherwise be silently discarded.
        self.setAcceptDrops(True)

        self._table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._table.customContextMenuRequested.connect(self._show_context_menu)

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:  # noqa: N802 - Qt override
        """Accept a drag only when it carries local files."""
        if self._dropped_paths(event.mimeData()):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event: QDropEvent) -> None:  # noqa: N802 - Qt override
        """Upload whatever was dropped into the open folder."""
        paths = self._dropped_paths(event.mimeData())
        if not paths:
            event.ignore()
            return
        event.acceptProposedAction()
        self.upload_files(paths)

    @staticmethod
    def _dropped_paths(mime: QMimeData) -> list[Path]:
        """The local files and folders in `mime`, ignoring anything else.

        A drag from a browser or a text editor carries URLs that are not files;
        uploading those would mean fetching them, which is not what a drop onto
        a file list means.
        """
        if not mime.hasUrls():
            return []
        return [
            Path(url.toLocalFile())
            for url in mime.urls()
            if url.isLocalFile() and Path(url.toLocalFile()).exists()
        ]

    def _show_context_menu(self, position: QPoint) -> None:
        """Offer the operations that apply to what is under the cursor."""
        menu = QMenu(self)
        selection = self.selected_nodes

        if len(selection) == 1:
            menu.addAction("Rename", self.prompt_rename).setShortcut(QKeySequence("F2"))
        if selection:
            label = "Delete" if len(selection) == 1 else f"Delete {len(selection)} items"
            menu.addAction(label, self.delete_selected)
            menu.addSeparator()
        if len(selection) == 1:
            menu.addAction("Properties", self.show_properties).setShortcut(
                QKeySequence("Alt+Return")
            )
        menu.addAction("New folder", self.prompt_new_folder).setShortcut(
            QKeySequence("Ctrl+Shift+N")
        )

        pending = describe_next_undo(self._vault)
        if pending is not None:
            menu.addSeparator()
            menu.addAction(f"Undo {pending}", self.undo_last_change).setShortcut(
                QKeySequence.StandardKey.Undo
            )

        menu.exec(self._table.viewport().mapToGlobal(position))

    def undo_last_change(self) -> None:
        """Reverse the last reversible mutation, reporting the outcome."""
        message = undo_last(self._vault)
        if message is None:
            self._report("Nothing to undo")
            return
        self._report(message)
        self._on_vault_changed()

    def show_properties(self) -> None:
        """Describe the single selected node in full."""
        nodes = self.selected_nodes
        if len(nodes) != 1:
            return
        PropertiesDialog(self._vault, nodes[0], self._palette, self).exec()

    def open_settings(self) -> None:
        """Edit the storage configuration.

        Changes land in the env file and take effect the next time the app
        starts, since the backend and engine are built once at startup.
        """
        dialog = SettingsDialog(load_settings(), self._palette, env_path=Path(".env"), parent=self)
        if dialog.exec():
            self._report("Settings saved. Restart Disbox for them to take effect.")

    def open_trash(self) -> None:
        """Show the trash, refreshing this window if anything is restored."""
        dialog = TrashDialog(self._vault, self._palette, self)
        dialog.vault_changed.connect(self._on_vault_changed)
        dialog.exec()

    def _on_vault_changed(self) -> None:
        """Re-read everything that reflects the tree."""
        self._refresh()
        self.tree.reload()

    def prompt_new_folder(self) -> None:
        """Ask for a folder name, then create it."""
        name, accepted = QInputDialog.getText(self, "New folder", "Name:", text="New folder")
        if accepted and name.strip():
            self.create_folder(name.strip())

    def prompt_rename(self) -> None:
        """Ask for a new name for the single selected node."""
        nodes = self.selected_nodes
        if len(nodes) != 1:
            return

        current = FileSystem(self._vault).resolve(nodes[0]).name
        name, accepted = QInputDialog.getText(self, "Rename", "Name:", text=current)
        if accepted and name.strip() and name.strip() != current:
            self.rename_selected(name.strip())

    def toggle_theme(self) -> None:
        """Switch between the dark and light palettes, live."""
        self.apply_palette(LIGHT if self._palette.is_dark else DARK)

    def apply_palette(self, palette: Palette) -> None:
        """Re-theme every part of the window without rebuilding it.

        Icons are re-rendered rather than recoloured, because they are
        rasterised from SVG at their tint; the model and the delegate hold
        their own copies of the palette and must be told too.
        """
        self._palette = palette
        # The material decides whether surfaces may be translucent, so it is
        # settled before the stylesheet that depends on it.
        self._apply_window_material()
        self.setStyleSheet(build_stylesheet(palette, translucent=self._translucent))
        self.table_model.set_palette(palette)
        self.row_delegate.set_palette(palette)
        self.details.set_palette(palette)
        self.dock.set_palette(palette)
        self.tree.set_palette(palette)
        self._retint_icons()
        self._set_crumbs(self._crumbs)
        self.update()

    def _retint_icons(self) -> None:
        """Redraw every icon in the chrome for the current palette."""
        for button, name in (
            (self._back_button, "arrow-left"),
            (self._up_button, "arrow-up"),
            (self._theme_button, "settings"),
        ):
            button.setIcon(icons.icon(name, self._palette.text_muted, size=18, ratio=2.0))
        self.title_bar.retint(self._palette)

    # ---------------------------------------------------------- navigation --

    @property
    def palette_name(self) -> str:
        """Name of the palette currently applied."""
        return self._palette.name

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

        count = self.table_model.rowCount()
        self._empty_title.setText("Nothing here yet")
        self._empty_hint.setText("This folder is empty.")
        self._stack.setCurrentIndex(0 if count else 1)
        self._status.setText(f"{count} item{'' if count == 1 else 's'}")
        self._up_button.setEnabled(self._directory is not None)
        self._back_button.setEnabled(bool(self._history))

    # ---------------------------------------------------------- transfers --

    def upload_files(self, sources: Sequence[Path]) -> None:
        """Upload `sources` into the open directory, one after another.

        Serial rather than concurrent: the engine already parallelises the
        chunks within a file, and a second file competing for the same rate
        limit buys nothing while making progress harder to read.
        """
        if not sources:
            return
        if self._engine is None:
            self._report_problem("Storage is not configured, so uploads are unavailable")
            return

        self._queue.extend(sources)
        if not self._transferring:
            self._start_next_upload()

    def _start_next_upload(self) -> None:
        """Take the next queued file, or announce that the queue is empty."""
        if self._bridge is None or self._engine is None:
            return
        if not self._queue:
            self._transferring = False
            self._current_task = None
            self.dock.end()
            self._refresh()
            self.transfers_idle.emit()
            return

        source = self._queue.pop(0)
        self._transferring = True
        engine, directory = self._engine, self._directory

        if source.is_dir():
            self._run_transfer(self._folder_upload(source, directory), f"Uploading {source.name}")
            return

        async def work(task: AsyncTask) -> str:
            # Opened before the node is created: creating it first leaves an
            # empty node behind for a file that turned out to be unreadable,
            # and the user sees a phantom entry for something never uploaded.
            with source.open("rb") as handle:
                filesystem = FileSystem(self._vault)
                name = filesystem.available_name(directory, source.name, NameCollision.KEEP_BOTH)
                node_id = filesystem.create_file(directory, name)
                await engine.upload(
                    node_id,
                    handle,
                    on_progress=lambda p: task.report_progress(p.completed_bytes, p.total_bytes),
                )
            return name

        self._run_transfer(work, f"Uploading {source.name}")

    def _start_drag(self, _supported: Qt.DropAction) -> None:
        """Begin dragging the selection out to the file manager."""
        mime = self.build_drag_mime()
        if mime is None:
            return
        drag = QDrag(self._table)
        drag.setMimeData(mime)
        drag.exec(self.drag_actions())

    def drag_actions(self) -> Qt.DropAction:
        """What a drag out of the file list may do.

        Copy only. A move would delete from the vault when the drop succeeded,
        and a drag to the file manager must never remove the original.
        """
        return Qt.DropAction.CopyAction

    def build_drag_mime(self) -> DeferredFileMimeData | None:
        """Mime data for the current selection, or None if nothing is selected.

        The files are not fetched here. They are written when the drop target
        asks for them, so picking up a large folder and thinking better of it
        costs nothing.
        """
        nodes = self.selected_nodes
        if not nodes or self._engine is None or self._bridge is None:
            return None
        return DeferredFileMimeData(nodes, self._materialise_for_drag)

    def _materialise_for_drag(self, nodes: list[uuid.UUID], destination: Path) -> list[Path]:
        """Download `nodes` into `destination`, blocking until they are there.

        Blocking is unavoidable: the drop target is waiting for bytes and has no
        way to be told "later". It happens at drop rather than at drag start,
        which is the part that matters.
        """
        engine, bridge = self._engine, self._bridge
        if engine is None or bridge is None:  # pragma: no cover - guarded above
            return []

        filesystem = FileSystem(self._vault)
        written: list[Path] = []

        async def work(_: AsyncTask) -> None:
            walker = TreeTransfer(filesystem, engine)
            for node_id in nodes:
                node = filesystem.resolve(node_id)
                if node.kind == "dir":
                    await walker.download_folder(node_id, destination)
                    written.append(destination / node.name)
                    continue
                target = destination / node.name
                with target.open("wb") as handle:
                    await engine.download(node_id, handle)
                written.append(target)

        task = bridge.submit(work)
        # Drive the Qt loop while waiting, or the application freezes solid
        # rather than merely being busy.
        while task._future is None or not task._future.done():
            QApplication.processEvents()
        return written

    def _folder_upload(self, source: Path, directory: uuid.UUID | None) -> Work:
        """Work that uploads `source` and everything beneath it."""
        engine = self._engine

        async def work(_: AsyncTask) -> str:
            if engine is None:  # pragma: no cover - guarded by the caller
                return source.name
            walker = TreeTransfer(FileSystem(self._vault), engine)
            result = await walker.upload_folder(source, directory)
            # Partial success is the common case for a tree, so the count is
            # reported rather than the whole thing being called a failure.
            if result.failures:
                return f"{source.name}: {len(result.failures)} entries could not be uploaded"
            return f"{source.name}: {result.files} files"

        return work

    def download_selected(self, destination: Path) -> None:
        """Write every selected file into `destination`."""
        nodes = self.selected_nodes
        if not nodes:
            return
        if self._engine is None:
            self._report_problem("Storage is not configured, so downloads are unavailable")
            return

        engine = self._engine

        async def work(task: AsyncTask) -> str:
            filesystem = FileSystem(self._vault)
            walker = TreeTransfer(filesystem, engine)
            for node_id in nodes:
                node = filesystem.resolve(node_id)
                if node.kind == "dir":
                    # download_folder creates the named folder itself, so it
                    # takes the destination directory rather than the path the
                    # folder should end up at.
                    await walker.download_folder(node_id, destination)
                    continue
                with (destination / node.name).open("wb") as handle:
                    await engine.download(
                        node_id,
                        handle,
                        on_progress=lambda p: task.report_progress(
                            p.completed_bytes, p.total_bytes
                        ),
                    )
            return str(destination)

        self._run_transfer(work, f"Downloading to {destination.name}")

    def _run_transfer(self, work: Work, label: str) -> None:
        """Submit `work`, forward its progress, and move the queue along."""
        if self._bridge is None:
            self._report("Transfers are unavailable in this window")
            return

        self._report(label)
        self.dock.begin(label)
        task = self._bridge.submit(work)
        self._current_task = task
        task.progress.connect(self.transfer_progress.emit)
        task.progress.connect(self.dock.report)
        task.finished.connect(lambda _: self._on_transfer_done())
        task.failed.connect(self._on_transfer_failed)
        task.cancelled.connect(self._on_transfer_done)

    def cancel_transfers(self) -> None:
        """Cancel the running transfer and abandon whatever is queued.

        Clearing the queue is the point: cancelling only the current file would
        immediately start the next one, which is not what the button appears to
        promise.
        """
        self._queue.clear()
        if self._current_task is not None:
            self._current_task.cancel()

    def _on_transfer_done(self) -> None:
        """One transfer ended; continue with whatever is queued."""
        self._refresh()
        self._start_next_upload()

    def _on_transfer_failed(self, message: str) -> None:
        """Report a failed transfer and carry on with the rest of the queue.

        One unreadable file must not abandon the others the user selected.
        """
        self._report_problem(message)
        self._start_next_upload()

    # ------------------------------------------------------- file operations --

    @property
    def selected_nodes(self) -> list[uuid.UUID]:
        """The node ids of every selected row, in view order."""
        rows = sorted({index.row() for index in self._table.selectionModel().selectedRows()})
        return [
            node_id
            for node_id in (self.table_model.node_id_at(row) for row in rows)
            if node_id is not None
        ]

    def create_folder(self, name: str = "New folder") -> uuid.UUID | None:
        """Create a folder in the open directory and select it.

        Args:
            name: Desired name. A name already in use is given a numbered
                suffix rather than rejected, since the user asked for a new
                folder and refusing outright loses the gesture.

        Returns:
            The new folder's id, or None if it could not be created.
        """
        filesystem = FileSystem(self._vault)
        free = filesystem.available_name(self._directory, name, NameCollision.KEEP_BOTH)
        try:
            node_id = filesystem.create_directory(self._directory, free)
        except DisboxError as exc:
            self._report_problem(str(exc))
            return None

        self._refresh()
        self.tree.reload()
        self._select_node(node_id)
        return node_id

    def rename_selected(self, new_name: str) -> None:
        """Rename the single selected node.

        A rename is only meaningful for one node, so a multiple selection is
        ignored rather than applied to an arbitrary member of it.
        """
        nodes = self.selected_nodes
        if len(nodes) != 1:
            return

        try:
            FileSystem(self._vault).rename(nodes[0], new_name)
        except DisboxError as exc:
            self._report_problem(str(exc))
            return

        self._refresh()
        self._select_node(nodes[0])

    def delete_selected(self) -> int:
        """Move every selected node to the trash.

        Returns:
            How many nodes were affected, counting the contents of folders.
        """
        nodes = self.selected_nodes
        if not nodes:
            return 0

        filesystem = FileSystem(self._vault)
        affected = 0
        try:
            for node_id in nodes:
                affected += filesystem.delete(node_id)
        except DisboxError as exc:
            self._report_problem(str(exc))

        self._refresh()
        self.tree.reload()
        # The selection is gone, so the details pane must stop describing it.
        self._table.clearSelection()
        self._on_selection_changed()
        return affected

    def _select_node(self, node_id: uuid.UUID) -> None:
        """Select `node_id` if it is in the current listing."""
        for row in range(self.table_model.rowCount()):
            if self.table_model.node_id_at(row) == node_id:
                self._table.selectRow(row)
                self._on_selection_changed()
                return

    def _report(self, message: str) -> None:
        """Note something that went as intended."""
        if message:
            self.notifications.info(message)
        self._status.setText(message)
        self._update_notice_badge()

    def _report_problem(self, message: str) -> None:
        """Record a failure without interrupting the user.

        A blocking modal for something the user can retry is what SPEC M8-12
        rules out, so this goes to the log and the status bar. The identifier is
        shown inline because a user who never opens the log still needs
        something quotable.
        """
        notice = self.notifications.error(message)
        self._status.setText(f"{message}  [{notice.diagnostic_id}]")
        self._update_notice_badge()

    def _update_notice_badge(self) -> None:
        """Show how many problems are waiting to be read."""
        pending = self.notifications.unread_problems
        self._notices_button.setText(str(pending) if pending else "")
        # The icon buttons are a fixed square, which clips a count. Widen this
        # one only while it is carrying one.
        self._notices_button.setFixedSize(
            _ICON_BUTTON + 14 if pending else _ICON_BUTTON, _ICON_BUTTON
        )
        self._notices_button.setToolTip(
            f"{pending} unread problem{'' if pending == 1 else 's'}" if pending else "Notifications"
        )

    def open_notifications(self) -> None:
        """Show the notification history."""
        NotificationsDialog(self.notifications, self._palette, self).exec()
        self._update_notice_badge()

    def _on_selection_changed(self) -> None:
        """Describe the selection, but only when it is unambiguous."""
        rows = {index.row() for index in self._table.selectionModel().selectedRows()}
        was_visible = self.details.isVisible()
        if len(rows) != 1:
            self.details.show_node(None)
        else:
            self.details.show_node(self.table_model.node_id_at(next(iter(rows))))

        if self.details.isVisible() != was_visible:
            # Showing or hiding the pane resizes everything to its left. A
            # translucent window does not clear the area a widget vacates, so
            # without an explicit repaint the previous layout stays on screen
            # underneath -- which is why the header appeared twice.
            self._repaint_all()

    def _repaint_all(self) -> None:
        """Force a clean repaint after a layout change.

        Needed only because the window is translucent: Qt leaves whatever was
        painted before in place, and a compositor material behind it means
        there is no opaque fill to hide the remains.
        """
        central = self.centralWidget()
        if central is not None:
            central.updateGeometry()
            central.update()
        self.update()

    def _on_sort_changed(self, column: int, order: Qt.SortOrder) -> None:
        """Re-read the directory in the requested order."""
        self.table_model.set_sort(Column(column), ascending=order == Qt.SortOrder.AscendingOrder)

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
            chip.setFocusPolicy(Qt.FocusPolicy.TabFocus)
            chip.setAccessibleName(f"Go to {label}")
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
