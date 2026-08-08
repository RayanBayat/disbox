"""A tree of directories, for jumping straight to a folder.

Directories only. A tree that also lists files is a second file list competing
with the real one, and it makes the structure harder to read rather than easier.

Expansion is lazy. Reading a whole vault's tree up front is work proportional to
the vault rather than to what is on screen, which is the same mistake the paged
table model exists to avoid.
"""

import uuid

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QTreeWidget, QTreeWidgetItem, QWidget

from disbox.core.filesystem import FileSystem
from disbox.core.vault import Vault
from disbox.gui.theme import Palette, icons

__all__ = ["FolderTree"]

#: Where the node id lives on each item.
_ID_ROLE = Qt.ItemDataRole.UserRole
#: Marks an item whose children have not been read yet.
_LOADED_ROLE = Qt.ItemDataRole.UserRole + 1


class FolderTree(QTreeWidget):
    """Shows the vault's directory structure, filling in branches on demand."""

    #: Emitted with the selected directory's id.
    directory_selected = Signal(object)

    def __init__(self, vault: Vault, palette: Palette, parent: QWidget | None = None) -> None:
        """Build a tree over `vault`, showing its top-level folders."""
        super().__init__(parent)
        self.setObjectName("FolderTree")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._vault = vault
        self._palette = palette
        self._filesystem = FileSystem(vault)

        self.setHeaderHidden(True)
        self.setColumnCount(1)
        self.setIndentation(14)
        self.setUniformRowHeights(True)  # lets Qt skip measuring every row
        self.setFocusPolicy(Qt.FocusPolicy.TabFocus)

        self.itemExpanded.connect(self._on_expanded)
        self.currentItemChanged.connect(self._on_current_changed)

        self.reload()

    def set_palette(self, palette: Palette) -> None:
        """Adopt a new palette, retinting every visible icon."""
        self._palette = palette
        self.reload()

    def node_id_of(self, item: QTreeWidgetItem) -> uuid.UUID | None:
        """The directory id `item` stands for."""
        value = item.data(0, _ID_ROLE)
        return value if isinstance(value, uuid.UUID) else None

    def reload(self) -> None:
        """Rebuild from the top, discarding any expansion state."""
        self.clear()
        self._populate(None, self.invisibleRootItem())

    def _populate(self, parent_id: uuid.UUID | None, into: QTreeWidgetItem) -> None:
        """Add the directories directly under `parent_id`."""
        for node in self._filesystem.children(parent_id):
            if node.kind != "dir":
                continue
            item = QTreeWidgetItem(into, [node.name])
            item.setData(0, _ID_ROLE, node.id)
            item.setData(0, _LOADED_ROLE, False)
            item.setIcon(0, icons.icon("folder", self._palette.accent, size=16, ratio=2.0))
            self._mark_expandable(item, node.id)
        into.setData(0, _LOADED_ROLE, True)

    def _mark_expandable(self, item: QTreeWidgetItem, node_id: uuid.UUID) -> None:
        """Give `item` a placeholder child if it has subdirectories.

        The placeholder is what makes the expand arrow appear without reading
        the subtree. Checking for any child directory is one query, against the
        arbitrarily many a full descent would cost.
        """
        if any(child.kind == "dir" for child in self._filesystem.children(node_id)):
            QTreeWidgetItem(item, [""])

    def _on_expanded(self, item: QTreeWidgetItem) -> None:
        """Replace the placeholder with the real children, once."""
        if item.data(0, _LOADED_ROLE):
            return
        item.takeChildren()  # drop the placeholder
        node_id = self.node_id_of(item)
        if node_id is not None:
            self._populate(node_id, item)

    def _on_current_changed(
        self, current: QTreeWidgetItem | None, _previous: QTreeWidgetItem | None
    ) -> None:
        """Announce the newly selected directory."""
        if current is None:
            return
        node_id = self.node_id_of(current)
        if node_id is not None:
            self.directory_selected.emit(node_id)
