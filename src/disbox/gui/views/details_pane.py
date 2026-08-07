"""Detail panel for the selected node.

A file list can only show what fits in a column, so the exact size, the full
timestamps, and the storage facts have to live somewhere. Putting them in a
panel rather than a dialog means they are simply present while browsing, with
nothing to open and nothing to dismiss.

It appears only when exactly one row is selected. With none there is nothing to
describe, and with several a single-item panel would be lying about which one
it means -- so it gets out of the way instead.
"""

import uuid
from dataclasses import dataclass
from typing import Final

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFrame, QLabel, QSizePolicy, QVBoxLayout, QWidget

from disbox.core.vault import Vault
from disbox.gui.models.file_table import format_size, format_timestamp
from disbox.gui.theme import Palette, Space, icons

__all__ = ["DetailsPane"]

_PANE_WIDTH: Final = 288
_HERO_ICON: Final = 52


@dataclass(frozen=True, slots=True)
class NodeFacts:
    """Everything the panel reports about one node."""

    name: str
    kind: str
    size: int
    created_at: str
    modified_at: str
    child_count: int | None
    revision_count: int
    chunk_count: int


class DetailsPane(QWidget):
    """Describes the currently selected node."""

    def __init__(self, vault: Vault, palette: Palette) -> None:
        """Build an empty panel bound to `vault`."""
        super().__init__()
        self._vault = vault
        self._palette = palette
        self._node_id: uuid.UUID | None = None

        self.setObjectName("Details")
        self.setFixedWidth(_PANE_WIDTH)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Expanding)

        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(Space.LG, Space.XL, Space.LG, Space.LG)
        self._layout.setSpacing(Space.SM)

        self._icon = QLabel()
        self._icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._name = QLabel()
        self._name.setObjectName("DetailName")
        self._name.setWordWrap(True)
        self._name.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self._layout.addWidget(self._icon)
        self._layout.addWidget(self._name)
        self._layout.addSpacing(Space.MD)

        self._rows_container = QVBoxLayout()
        self._rows_container.setSpacing(Space.MD)
        self._layout.addLayout(self._rows_container)
        self._layout.addStretch(1)

        self.hide()

    # ---------------------------------------------------------------- state --

    @property
    def node_id(self) -> uuid.UUID | None:
        """The node currently described, if any."""
        return self._node_id

    def show_node(self, node_id: uuid.UUID | None) -> None:
        """Describe `node_id`, or hide the panel when it is None."""
        self._node_id = node_id
        if node_id is None:
            self.hide()
            return

        facts = self._load(node_id)
        if facts is None:  # selected row vanished from under us
            self.hide()
            return

        icon_name, tint_role = icons.icon_for_filename(facts.name, is_directory=facts.kind == "dir")
        self._icon.setPixmap(
            icons.pixmap(icon_name, getattr(self._palette, tint_role), size=_HERO_ICON, ratio=2.0)
        )
        self._name.setText(facts.name)
        self._render_rows(facts)
        self.show()

    def set_palette(self, palette: Palette) -> None:
        """Re-tint for a new theme."""
        self._palette = palette
        if self._node_id is not None:
            self.show_node(self._node_id)

    # -------------------------------------------------------------- internals --

    def _load(self, node_id: uuid.UUID) -> NodeFacts | None:
        """Gather everything known about `node_id`."""
        row = self._vault.connection.execute(
            "SELECT name, kind, size, created_at, modified_at FROM nodes WHERE id = ?",
            (node_id.bytes,),
        ).fetchone()
        if row is None:
            return None

        children = None
        if row[1] == "dir":
            children = self._vault.connection.execute(
                "SELECT count(*) FROM nodes WHERE parent_id = ? AND deleted_at IS NULL",
                (node_id.bytes,),
            ).fetchone()[0]

        revisions = self._vault.connection.execute(
            "SELECT count(*) FROM revisions WHERE node_id = ?", (node_id.bytes,)
        ).fetchone()[0]
        chunks = self._vault.connection.execute(
            "SELECT count(*) FROM revision_chunks rc "
            "JOIN revisions r ON r.id = rc.revision_id WHERE r.node_id = ?",
            (node_id.bytes,),
        ).fetchone()[0]

        return NodeFacts(
            name=row[0],
            kind=row[1],
            size=row[2],
            created_at=row[3],
            modified_at=row[4],
            child_count=children,
            revision_count=revisions,
            chunk_count=chunks,
        )

    def _render_rows(self, facts: NodeFacts) -> None:
        """Rebuild the key/value list for `facts`."""
        while (item := self._rows_container.takeAt(0)) is not None:
            if (widget := item.widget()) is not None:
                widget.deleteLater()

        entries: list[tuple[str, str]] = [("Type", "Folder" if facts.kind == "dir" else "File")]
        if facts.kind == "dir":
            entries.append(
                ("Contains", f"{facts.child_count} item{'' if facts.child_count == 1 else 's'}")
            )
        else:
            # Exact bytes as well as the rounded figure: the list already shows
            # the readable one, so repeating it alone would add nothing.
            entries.append(("Size", f"{format_size(facts.size)}  ({facts.size:,} bytes)"))
            entries.append(("Versions", str(facts.revision_count or 1)))
            entries.append(
                (
                    "Chunks",
                    str(facts.chunk_count) if facts.chunk_count else "Not yet uploaded",
                )
            )

        entries.append(("Modified", format_timestamp(facts.modified_at)))
        entries.append(("Created", format_timestamp(facts.created_at)))

        for key, value in entries:
            self._rows_container.addWidget(self._detail_row(key, value))

        divider = QFrame()
        divider.setFrameShape(QFrame.Shape.HLine)
        divider.setFixedHeight(1)
        self._rows_container.addWidget(divider)

    def _detail_row(self, key: str, value: str) -> QWidget:
        """One label-over-value pair."""
        row = QWidget()
        layout = QVBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(1)

        caption = QLabel(key.upper())
        caption.setObjectName("DetailKey")
        body = QLabel(value)
        body.setObjectName("DetailValue")
        body.setWordWrap(True)

        layout.addWidget(caption)
        layout.addWidget(body)
        return row
