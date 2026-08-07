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
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import IntEnum
from typing import Any, Final

from PySide6.QtCore import QAbstractTableModel, QModelIndex, QPersistentModelIndex, Qt
from PySide6.QtGui import QBrush, QColor, QFont, QIcon

from disbox.core.vault import Vault
from disbox.gui.theme import icons
from disbox.gui.theme.tokens import DARK, Palette

__all__ = ["Column", "FileTableModel"]

_DEFAULT_PAGE_SIZE: Final = 200

# Keep at most this many pages resident. Enough to cover a viewport plus scroll
# momentum in both directions, bounded so long scrolls cannot grow without end.
_MAX_CACHED_PAGES: Final = 8

# Binary, matching how file managers report sizes.
_BYTES_PER_UNIT: Final = 1024

# Thresholds for relative time, in seconds.
_MINUTE: Final = 60
_HOUR: Final = 60 * _MINUTE
_DAY: Final = 24 * _HOUR
_WEEK: Final = 7 * _DAY


class Column(IntEnum):
    """Columns of the file table, in display order."""

    NAME = 0
    SIZE = 1
    MODIFIED = 2


_HEADERS: Final = {
    Column.NAME: "Name",
    Column.SIZE: "Size",
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


def _tooltip(row: Row, column: Column) -> str | None:
    """Extra detail for a cell, shown on hover.

    The Modified column displays an approximation such as "2 days ago", so the
    exact stamp has to stay reachable somewhere.
    """
    if column is Column.MODIFIED:
        return row.modified_at
    if column is Column.NAME:
        return row.name
    return None


def _display_value(row: Row, column: Column) -> str:
    """Render one cell's text."""
    if column is Column.NAME:
        return row.name
    if column is Column.SIZE:
        return "" if row.kind == "dir" else format_size(row.size)
    return format_timestamp(row.modified_at)


def format_timestamp(raw: str, *, now: datetime | None = None) -> str:
    """Render a stored timestamp the way a person would say it.

    Recent times read as "2 hours ago" because that is what someone scanning a
    folder actually wants; anything older falls back to a date. The exact value
    is still available in the cell's tooltip.
    """
    try:
        moment = datetime.fromisoformat(raw)
    except ValueError:
        return raw
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)

    seconds = ((now or datetime.now(UTC)) - moment).total_seconds()
    if seconds < 0 or seconds >= _WEEK:
        return moment.strftime("%d %b %Y")
    if seconds < _MINUTE:
        return "Just now"
    if seconds < _HOUR:
        return f"{int(seconds // _MINUTE)} min ago"
    if seconds < _DAY:
        hours = int(seconds // _HOUR)
        return f"{hours} hour{'' if hours == 1 else 's'} ago"
    days = int(seconds // _DAY)
    return f"{days} day{'' if days == 1 else 's'} ago"


class FileTableModel(QAbstractTableModel):
    """A paged, read-through view of one directory."""

    def __init__(
        self,
        vault: Vault,
        parent: Any = None,
        *,
        page_size: int = _DEFAULT_PAGE_SIZE,
        palette: Palette = DARK,
    ) -> None:
        """Present `vault` contents; call `set_directory` to choose one."""
        super().__init__(parent)
        self._vault = vault
        self._palette = palette
        self._icon_cache: dict[tuple[str, str], QIcon] = {}
        self._name_font = QFont()
        self._name_font.setWeight(QFont.Weight.DemiBold)
        self._page_size = page_size
        self._directory: uuid.UUID | None = None
        self._row_count = 0
        self._pages: dict[int, list[Row]] = {}
        self._results: list[Row] | None = None
        self._sort_column = Column.NAME
        self._sort_ascending = True

    # ----------------------------------------------------------- navigation --

    def set_directory(self, directory: uuid.UUID | None) -> None:
        """Show the contents of `directory`, or the vault root when None."""
        self.beginResetModel()
        self._directory = directory
        self._results = None
        self._pages.clear()
        self._row_count = self._count_rows()
        self.endResetModel()

    def set_results(self, node_ids: Sequence[uuid.UUID]) -> None:
        """Show an explicit set of nodes instead of a directory.

        Used for search, where matches come from all over the tree and have no
        common parent. The set is bounded by the caller's result limit, so it is
        fetched in one go rather than paged.
        """
        self.beginResetModel()
        self._directory = None
        self._pages.clear()
        self._results = self._fetch_nodes(node_ids)
        self._row_count = len(self._results)
        self.endResetModel()

    def _fetch_nodes(self, node_ids: Sequence[uuid.UUID]) -> list[Row]:
        """Load full rows for `node_ids`, preserving the order given."""
        if not node_ids:
            return []
        placeholders = ", ".join("?" for _ in node_ids)
        cursor = self._vault.connection.execute(
            "SELECT id, name, kind, size, modified_at FROM nodes "  # noqa: S608 - placeholders only
            f"WHERE id IN ({placeholders})",
            tuple(node_id.bytes for node_id in node_ids),
        )
        by_id = {
            uuid.UUID(bytes=record[0]): Row(
                node_id=uuid.UUID(bytes=record[0]),
                name=record[1],
                kind=record[2],
                size=record[3],
                modified_at=record[4],
            )
            for record in cursor.fetchall()
        }
        return [by_id[node_id] for node_id in node_ids if node_id in by_id]

    def set_sort(self, column: Column, *, ascending: bool) -> None:
        """Reorder the current directory.

        Folders stay grouped above files whatever the sort, because mixing them
        makes a directory much harder to scan; only the order within each group
        changes.
        """
        self._sort_column = column
        self._sort_ascending = ascending
        self.refresh()

    def _order_clause(self) -> str:
        """Build the ORDER BY matching the current sort."""
        direction = "ASC" if self._sort_ascending else "DESC"
        key = {
            Column.NAME: "name COLLATE NOCASE",
            Column.SIZE: "size",
            Column.MODIFIED: "modified_at",
        }[self._sort_column]
        return f"(kind = 'dir') DESC, {key} {direction}"

    def refresh(self) -> None:
        """Re-read the current directory from the vault."""
        self.set_directory(self._directory)

    def node_id_at(self, row: int) -> uuid.UUID | None:
        """Return the node shown at `row`, or None if there is none."""
        fetched = self._row_at(row)
        return fetched.node_id if fetched else None

    def rows_cached(self) -> int:
        """Number of rows currently held in memory. Used by tests and diagnostics."""
        if self._results is not None:
            return len(self._results)
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
        """Column titles and alignment for the horizontal header."""
        if orientation != Qt.Orientation.Horizontal:
            return None
        try:
            column = Column(section)
        except ValueError:  # Qt may probe sections beyond columnCount
            return None

        # A header centred over left-aligned content is the classic sign of an
        # unstyled table; each header takes the alignment of its own column.
        if role == Qt.ItemDataRole.TextAlignmentRole:
            return int(icons.alignment_for_column(column is Column.SIZE))
        if role != Qt.ItemDataRole.DisplayRole:
            return None
        return _HEADERS[column]

    def data(  # noqa: PLR0911 - one branch per Qt role; a dispatch dict would
        # allocate on every cell of every repaint, which is the one place in
        # this class where that cost is actually visible.
        self,
        index: QModelIndex | QPersistentModelIndex,
        role: int = Qt.ItemDataRole.DisplayRole,
    ) -> Any:
        """Cell contents for the requested role."""
        row = self._row_at(index.row()) if index.isValid() else None
        if row is None:
            return None

        # A type icon beside the name is what lets someone scan a folder
        # without reading it. Cached because Qt asks for the same icon on every
        # repaint, and re-rasterising an SVG per frame would show up in scroll.
        if role == Qt.ItemDataRole.DecorationRole:
            return self._icon_for(row) if index.column() == Column.NAME else None
        column = Column(index.column())
        match role:
            case Qt.ItemDataRole.DisplayRole:
                return _display_value(row, column)
            case Qt.ItemDataRole.ToolTipRole:
                return _tooltip(row, column)
            case Qt.ItemDataRole.TextAlignmentRole:
                return int(icons.alignment_for_column(column is Column.SIZE))
            case Qt.ItemDataRole.FontRole | Qt.ItemDataRole.ForegroundRole:
                # The name is what people scan; size and date support it and
                # should recede rather than compete.
                return self._name_emphasis(column, role)
            case _:
                return None

    def _name_emphasis(self, column: Column, role: int) -> object:
        """Weight and colour that lift the name column above its neighbours."""
        if column is not Column.NAME:
            return None
        if role == Qt.ItemDataRole.FontRole:
            return self._name_font
        return QBrush(QColor(self._palette.text))

    def set_palette(self, palette: Palette) -> None:
        """Re-tint the type icons when the theme changes."""
        self._palette = palette
        self._icon_cache.clear()
        if self._row_count:
            self.dataChanged.emit(
                self.index(0, Column.NAME),
                self.index(self._row_count - 1, Column.NAME),
                [Qt.ItemDataRole.DecorationRole],
            )

    def _icon_for(self, row: Row) -> QIcon:
        """Return the tinted type icon for `row`, rasterising at most once."""
        name, role = icons.icon_for_filename(row.name, is_directory=row.kind == "dir")
        key = (name, role)
        if key not in self._icon_cache:
            colour = getattr(self._palette, role)
            self._icon_cache[key] = icons.icon(name, colour, size=18, ratio=2.0)
        return self._icon_cache[key]

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
        if self._results is not None:
            return self._results[row]
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
            f"ORDER BY {self._order_clause()} LIMIT ? OFFSET ?",
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
