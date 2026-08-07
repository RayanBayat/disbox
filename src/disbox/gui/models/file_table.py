"""Table model presenting one directory of the vault.

Rows are fetched from SQLite in pages and cached only while they are near the
viewport. The alternative -- loading a directory into a Python list -- is what
makes file managers stall on large folders: a quarter of a million rows would
cost hundreds of megabytes and a visible pause before anything appeared, and
Qt only ever asks for the handful of rows it is about to paint.

The model owns no data of its own. `refresh` re-reads from the vault, so any
mutation applied through the core is picked up without the view and the
database being able to disagree.
"""

import uuid
from dataclasses import dataclass
from datetime import datetime
from enum import IntEnum
from typing import Any, Final

from PySide6.QtCore import QAbstractTableModel, QModelIndex, QPersistentModelIndex, Qt

from disbox.core.vault import Vault

__all__ = ["Column", "FileTableModel"]

_DEFAULT_PAGE_SIZE: Final = 200

# Keep at most this many pages resident. Enough to cover a viewport plus scroll
# momentum in both directions, bounded so long scrolls cannot grow without end.
_MAX_CACHED_PAGES: Final = 8

# Binary, matching how file managers report sizes.
_BYTES_PER_UNIT: Final = 1024


class Column(IntEnum):
    """Columns of the file table, in display order."""

    NAME = 0
    SIZE = 1
    KIND = 2
    MODIFIED = 3


_HEADERS: Final = {
    Column.NAME: "Name",
    Column.SIZE: "Size",
    Column.KIND: "Type",
    Column.MODIFIED: "Modified",
}


@dataclass(frozen=True, slots=True)
class Row:
    """One node as the table needs it."""

    node_id: uuid.UUID
    name: str
    kind: str
    size: int
    modified_at: str


def format_size(size: int) -> str:
    """Render a byte count the way a file manager would."""
    value = float(size)
    for unit in ("B", "KB", "MB", "GB", "TB", "PB"):
        if value < _BYTES_PER_UNIT or unit == "PB":
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= _BYTES_PER_UNIT
    return f"{value:.1f} PB"  # pragma: no cover - loop always returns first


def format_timestamp(raw: str) -> str:
    """Render a stored ISO timestamp for display, falling back to the raw text."""
    try:
        return datetime.fromisoformat(raw).strftime("%Y-%m-%d %H:%M")
    except ValueError:
        return raw


class FileTableModel(QAbstractTableModel):
    """A paged, read-through view of one directory."""

    def __init__(
        self,
        vault: Vault,
        parent: Any = None,
        *,
        page_size: int = _DEFAULT_PAGE_SIZE,
    ) -> None:
        """Present `vault` contents; call `set_directory` to choose one."""
        super().__init__(parent)
        self._vault = vault
        self._page_size = page_size
        self._directory: uuid.UUID | None = None
        self._row_count = 0
        self._pages: dict[int, list[Row]] = {}

    # ----------------------------------------------------------- navigation --

    def set_directory(self, directory: uuid.UUID | None) -> None:
        """Show the contents of `directory`, or the vault root when None."""
        self.beginResetModel()
        self._directory = directory
        self._pages.clear()
        self._row_count = self._count_rows()
        self.endResetModel()

    def refresh(self) -> None:
        """Re-read the current directory from the vault."""
        self.set_directory(self._directory)

    def node_id_at(self, row: int) -> uuid.UUID | None:
        """Return the node shown at `row`, or None if there is none."""
        fetched = self._row_at(row)
        return fetched.node_id if fetched else None

    def rows_cached(self) -> int:
        """Number of rows currently held in memory. Used by tests and diagnostics."""
        return sum(len(page) for page in self._pages.values())

    # -------------------------------------------------------- Qt model API --

    def rowCount(self, parent: QModelIndex | QPersistentModelIndex | None = None) -> int:  # noqa: N802 - Qt API
        """Number of rows in the current directory."""
        if parent is not None and parent.isValid():
            return 0
        return self._row_count

    def columnCount(self, parent: QModelIndex | QPersistentModelIndex | None = None) -> int:  # noqa: N802 - Qt API
        """Number of columns, which is fixed."""
        if parent is not None and parent.isValid():
            return 0
        return len(Column)

    def headerData(  # noqa: N802 - Qt API
        self,
        section: int,
        orientation: Qt.Orientation,
        role: int = Qt.ItemDataRole.DisplayRole,
    ) -> Any:
        """Column titles for the horizontal header."""
        if role != Qt.ItemDataRole.DisplayRole or orientation != Qt.Orientation.Horizontal:
            return None
        try:
            column = Column(section)
        except ValueError:  # Qt may probe sections beyond columnCount
            return None
        return _HEADERS[column]

    def data(
        self,
        index: QModelIndex | QPersistentModelIndex,
        role: int = Qt.ItemDataRole.DisplayRole,
    ) -> Any:
        """Cell contents for the requested role."""
        if not index.isValid() or role != Qt.ItemDataRole.DisplayRole:
            return None
        row = self._row_at(index.row())
        if row is None:
            return None

        match Column(index.column()):
            case Column.NAME:
                return row.name
            case Column.SIZE:
                return "" if row.kind == "dir" else format_size(row.size)
            case Column.KIND:
                return "Folder" if row.kind == "dir" else "File"
            case Column.MODIFIED:
                return format_timestamp(row.modified_at)

    # ------------------------------------------------------------- fetching --

    def _count_rows(self) -> int:
        """Count live children of the current directory."""
        where, params = self._directory_filter()
        cursor = self._vault.connection.execute(
            f"SELECT count(*) FROM nodes WHERE {where}",  # noqa: S608 - both branches are literals
            params,
        )
        return int(cursor.fetchone()[0])

    def _directory_filter(self) -> tuple[str, tuple[bytes, ...]]:
        """Return the WHERE clause selecting the current directory's children.

        The root and non-root cases are separate statements rather than one
        clause with an OR. Written as
        ``(? IS NULL AND parent_id IS NULL) OR parent_id = ?`` SQLite plans a
        multi-index OR and falls back to sorting the whole directory, which
        costs more than the sort the listing index exists to avoid.
        """
        if self._directory is None:
            return "parent_id IS NULL AND deleted_at IS NULL", ()
        return "parent_id = ? AND deleted_at IS NULL", (self._directory.bytes,)

    def _row_at(self, row: int) -> Row | None:
        """Return the row at `row`, loading its page if necessary."""
        if row < 0 or row >= self._row_count:
            return None
        page_number, offset = divmod(row, self._page_size)
        page = self._pages.get(page_number)
        if page is None:
            page = self._load_page(page_number)
        return page[offset] if offset < len(page) else None

    def _load_page(self, page_number: int) -> list[Row]:
        """Read one page of rows, evicting the furthest page if the cache is full."""
        where, params = self._directory_filter()
        cursor = self._vault.connection.execute(
            # The ORDER BY must match idx_nodes_listing exactly -- same
            # expression, direction, and collation -- or SQLite sorts instead of
            # walking the index. Folders first, then by name, as file managers do.
            f"SELECT id, name, kind, size, modified_at FROM nodes WHERE {where} "  # noqa: S608
            "ORDER BY (kind = 'dir') DESC, name COLLATE NOCASE LIMIT ? OFFSET ?",
            (*params, self._page_size, page_number * self._page_size),
        )
        page = [
            Row(
                node_id=uuid.UUID(bytes=record[0]),
                name=record[1],
                kind=record[2],
                size=record[3],
                modified_at=record[4],
            )
            for record in cursor.fetchall()
        ]

        if len(self._pages) >= _MAX_CACHED_PAGES:
            furthest = max(self._pages, key=lambda existing: abs(existing - page_number))
            del self._pages[furthest]
        self._pages[page_number] = page
        return page
