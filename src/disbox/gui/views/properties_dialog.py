"""Everything the vault knows about one node.

The details pane summarises; this is the full record, including the identifier
worth quoting when something goes wrong. Sizes appear both readably and as exact
byte counts, because "1.0 KB" is what you want to read and 1,024 is what you
need when reconciling against something else.

A node that has vanished between the click and the dialog opening is reported
rather than raised: the user asked about something that is simply gone, which is
information, not an error.
"""

import contextlib
import sqlite3
import uuid

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from disbox.core.filesystem import FileSystem
from disbox.core.vault import Vault
from disbox.errors import DisboxError
from disbox.gui.models.file_table import format_size
from disbox.gui.theme import Palette, Space
from disbox.gui.theme.stylesheet import build_stylesheet

__all__ = ["PropertiesDialog"]


class PropertiesDialog(QDialog):
    """A read-only record of one node."""

    def __init__(
        self,
        vault: Vault,
        node_id: uuid.UUID,
        palette: Palette,
        parent: QWidget | None = None,
    ) -> None:
        """Describe `node_id` from `vault`."""
        super().__init__(parent)
        self._vault = vault
        self._node_id = node_id
        self._fields: dict[str, str] = {}

        self.setWindowTitle("Properties")
        self.setMinimumWidth(420)
        self.setStyleSheet(build_stylesheet(palette, translucent=False))

        layout = QVBoxLayout(self)
        layout.setContentsMargins(Space.LG, Space.LG, Space.LG, Space.LG)
        layout.setSpacing(Space.MD)

        self._grid = QGridLayout()
        self._grid.setHorizontalSpacing(Space.LG)
        self._grid.setVerticalSpacing(Space.SM)
        self._grid.setColumnStretch(1, 1)
        layout.addLayout(self._grid)
        layout.addStretch(1)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        close = QPushButton("Close")
        close.setObjectName("PrimaryButton")
        close.setCursor(Qt.CursorShape.PointingHandCursor)
        close.clicked.connect(self.accept)
        buttons.addWidget(close)
        layout.addLayout(buttons)

        self._collect()
        self._render()

    def field(self, label: str) -> str:
        """The value shown for `label`, or an empty string if absent."""
        return self._fields.get(label, "")

    def _collect(self) -> None:
        """Read every fact worth showing."""
        filesystem = FileSystem(self._vault)
        try:
            node = filesystem.resolve(self._node_id)
        except DisboxError:
            self._fields["Name"] = "This item was not found"
            return

        self._fields["Name"] = node.name
        self._fields["Type"] = "Folder" if node.kind == "dir" else "File"
        self._fields["Identifier"] = str(node.id)
        with contextlib.suppress(DisboxError):
            self._fields["Location"] = filesystem.path_of(node.id)

        if node.kind == "dir":
            children = filesystem.children(node.id)
            self._fields["Contents"] = f"{len(children)} item{'' if len(children) == 1 else 's'}"
        else:
            self._fields["Size"] = f"{format_size(node.size)}  ({node.size:,} bytes)"
            self._fields["Stored"] = self._stored_summary()

        self._collect_timestamps()

    def _stored_summary(self) -> str:
        """How much of the file's content actually exists in the backend."""
        row = self._vault.raw_connection.execute(
            "SELECT count(*) FROM revision_chunks rc "
            "JOIN revisions r ON r.id = rc.revision_id WHERE r.node_id = ?",
            (self._node_id.bytes,),
        ).fetchone()
        chunks = int(row[0]) if row else 0
        if chunks == 0:
            return "Not yet uploaded"
        return f"{chunks} chunk{'' if chunks == 1 else 's'}"

    def _collect_timestamps(self) -> None:
        """Add the created and modified times, if the row still has them."""
        with contextlib.suppress(sqlite3.Error):
            row = self._vault.raw_connection.execute(
                "SELECT created_at, modified_at FROM nodes WHERE id = ?",
                (self._node_id.bytes,),
            ).fetchone()
            if row is not None:
                self._fields["Created"] = str(row[0])
                self._fields["Modified"] = str(row[1])

    def _render(self) -> None:
        """Lay the collected fields out in order."""
        order = (
            "Name",
            "Type",
            "Location",
            "Size",
            "Contents",
            "Stored",
            "Created",
            "Modified",
            "Identifier",
        )
        row = 0
        for label in order:
            if label not in self._fields:
                continue
            key = QLabel(label.upper())
            key.setObjectName("DetailKey")
            value = QLabel(self._fields[label])
            value.setObjectName("DetailValue")
            value.setWordWrap(True)
            value.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            self._grid.addWidget(key, row, 0, Qt.AlignmentFlag.AlignTop)
            self._grid.addWidget(value, row, 1)
            row += 1
