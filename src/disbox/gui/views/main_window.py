"""The main Disbox window: a browser over one vault.

Navigation state lives here rather than in the model, so the model stays a
simple projection of "one directory" and the window owns history. Search
replaces the table's contents rather than filtering them, because a match may
live anywhere in the tree and the directory view has no way to show that.

Read-only for now. Upload, download, rename and delete arrive with the transfer
engine; the toolbar deliberately does not offer buttons that would do nothing.
"""

import uuid
from typing import Final

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMainWindow,
    QTableView,
    QToolBar,
    QVBoxLayout,
    QWidget,
)

from disbox.core.search import search
from disbox.core.vault import Vault
from disbox.gui.models.file_table import Column, FileTableModel

__all__ = ["MainWindow"]

_SEARCH_RESULT_LIMIT: Final = 500


class MainWindow(QMainWindow):
    """Browse a vault: navigate directories, search, and inspect."""

    def __init__(self, vault: Vault) -> None:
        """Open a window onto `vault`, showing its root directory."""
        super().__init__()
        self._vault = vault
        self._directory: uuid.UUID | None = None
        self._history: list[uuid.UUID | None] = []
        self._search_results: list[str] = []
        self._searching = False

        self.setWindowTitle(f"Disbox - {vault.path.stem}")
        self.resize(1100, 680)

        self.table_model = FileTableModel(vault)
        self._build_ui()
        self._refresh()

    # ------------------------------------------------------------------ ui --

    def _build_ui(self) -> None:
        """Assemble the toolbar, breadcrumb, table, and status bar."""
        toolbar = QToolBar("Navigation")
        toolbar.setMovable(False)
        self.addToolBar(toolbar)

        self._back_action = QAction("Back", self)
        self._back_action.setShortcut(QKeySequence.StandardKey.Back)
        self._back_action.triggered.connect(self.navigate_back)
        toolbar.addAction(self._back_action)

        self._up_action = QAction("Up", self)
        self._up_action.triggered.connect(self.navigate_up)
        toolbar.addAction(self._up_action)
        toolbar.addSeparator()

        self._search_box = QLineEdit()
        self._search_box.setPlaceholderText("Search all files and folders")
        self._search_box.setClearButtonEnabled(True)
        self._search_box.textChanged.connect(self.apply_search)
        self._search_box.setMaximumWidth(360)
        toolbar.addWidget(self._search_box)

        self._breadcrumb = QLabel()
        self._breadcrumb.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)

        self._table = QTableView()
        self._table.setModel(self.table_model)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setAlternatingRowColors(True)
        self._table.verticalHeader().setVisible(False)
        # Uniform row heights let Qt skip measuring every row, which is what
        # keeps scrolling cheap on a very large directory.
        self._table.verticalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Fixed)
        self._table.setSortingEnabled(False)
        self._table.doubleClicked.connect(self._on_double_click)
        header = self._table.horizontalHeader()
        header.setSectionResizeMode(Column.NAME, QHeaderView.ResizeMode.Stretch)

        layout = QVBoxLayout()
        layout.setContentsMargins(8, 8, 8, 0)
        layout.addWidget(self._breadcrumb)
        layout.addWidget(self._table)
        container = QWidget()
        container.setLayout(layout)
        self.setCentralWidget(container)

        self.statusBar().showMessage("")

    # ---------------------------------------------------------- navigation --

    @property
    def current_directory(self) -> uuid.UUID | None:
        """Directory currently shown, or None for the vault root."""
        return self._directory

    def navigate_to(self, directory: uuid.UUID | None) -> None:
        """Show `directory`, recording where we came from."""
        self._history.append(self._directory)
        self._directory = directory
        self._clear_search()
        self._refresh()

    def navigate_back(self) -> None:
        """Return to the previously shown directory, if there is one."""
        if not self._history:
            return
        self._directory = self._history.pop()
        self._clear_search()
        self._refresh()

    def navigate_up(self) -> None:
        """Move to the parent of the current directory."""
        if self._directory is None:
            return
        row = self._vault.connection.execute(
            "SELECT parent_id FROM nodes WHERE id = ?", (self._directory.bytes,)
        ).fetchone()
        parent = uuid.UUID(bytes=row[0]) if row and row[0] is not None else None
        self.navigate_to(parent)

    def breadcrumb_text(self) -> str:
        """Human-readable path to the current directory."""
        return self._breadcrumb.text()

    # -------------------------------------------------------------- search --

    def apply_search(self, query: str) -> None:
        """Show matches for `query` from anywhere in the tree, or clear it."""
        text = query.strip()
        if not text:
            self._clear_search()
            self._refresh()
            return

        hits = search(self._vault.connection, text, limit=_SEARCH_RESULT_LIMIT)
        self._searching = True
        self._search_results = [hit.name for hit in hits]
        self.table_model.set_results([hit.node_id for hit in hits])
        self._breadcrumb.setText(f"Search results for {text!r}")
        self.statusBar().showMessage(f"{len(hits)} match{'' if len(hits) == 1 else 'es'}")

    def result_names(self) -> list[str]:
        """Names of the current search results, empty when not searching."""
        return list(self._search_results)

    def _clear_search(self) -> None:
        self._searching = False
        self._search_results = []

    # -------------------------------------------------------------- helpers --

    def _refresh(self) -> None:
        """Re-read the current directory and update the surrounding chrome."""
        self.table_model.set_directory(self._directory)
        self._breadcrumb.setText(self._compute_breadcrumb())
        self._up_action.setEnabled(self._directory is not None)
        self._back_action.setEnabled(bool(self._history))

        count = self.table_model.rowCount()
        self.statusBar().showMessage(f"{count} item{'' if count == 1 else 's'}")

    def _compute_breadcrumb(self) -> str:
        """Build the path text by walking from the current directory to the root."""
        parts: list[str] = []
        node = self._directory
        seen: set[uuid.UUID] = set()
        while node is not None and node not in seen:
            seen.add(node)  # a corrupt tree must not hang the window
            row = self._vault.connection.execute(
                "SELECT name, parent_id FROM nodes WHERE id = ?", (node.bytes,)
            ).fetchone()
            if row is None:
                break
            parts.append(row[0])
            node = uuid.UUID(bytes=row[1]) if row[1] is not None else None
        return " / ".join(["Vault", *reversed(parts)])

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
