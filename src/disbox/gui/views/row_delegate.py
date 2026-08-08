"""Row painting with eased hover and selection.

Qt Style Sheets have no transitions. A `:hover` rule swaps colour on the frame
the cursor arrives and swaps it back on the frame it leaves, which is the
difference between an interface that responds and one that merely reacts. The
only way to ease it is to paint the background ourselves.

Each row carries a value between 0 and 1 for hover and for selection, advanced
by a single shared timer. One timer for the whole table matters: a timer per row
would put thousands of them on a large directory, and the cost would show up as
exactly the stutter this exists to remove. Rows at rest are dropped from the
map, so an idle table animates nothing at all.
"""

from typing import Final

from PySide6.QtCore import QModelIndex, QPersistentModelIndex, QRectF, Qt, QTimer
from PySide6.QtGui import QBrush, QColor, QPainter
from PySide6.QtWidgets import QStyle, QStyledItemDelegate, QStyleOptionViewItem, QTableView

from disbox.gui.theme.tokens import Palette, Radius

__all__ = ["AnimatedRowDelegate"]

#: ~60fps. Anything finer is invisible and only costs repaints.
_TICK_MS: Final = 16

#: Fraction of the transition completed per tick. 0.18 lands in roughly 90ms,
#: fast enough to feel immediate and slow enough to read as movement.
_STEP: Final = 0.18

#: Below this a row is treated as fully at rest and stops being tracked.
_EPSILON: Final = 0.01


class AnimatedRowDelegate(QStyledItemDelegate):
    """Paints table rows with eased hover and selection backgrounds."""

    def __init__(self, view: QTableView, palette: Palette) -> None:
        """Animate rows for `view`."""
        super().__init__(view)
        self._view = view
        self._palette = palette
        self._hover: dict[int, float] = {}
        self._selection: dict[int, float] = {}

        self._timer = QTimer(self)
        self._timer.setInterval(_TICK_MS)
        self._timer.timeout.connect(self._advance)

    def set_palette(self, palette: Palette) -> None:
        """Adopt a new palette."""
        self._palette = palette
        self._view.viewport().update()

    # ------------------------------------------------------------ animation --

    def _target(self, row: int) -> tuple[float, float]:
        """Where `row` should end up, as (hover, selection)."""
        cursor = self._view.viewport().mapFromGlobal(self._view.cursor().pos())
        hovered = self._view.indexAt(cursor).row() == row and self._view.underMouse()
        selected = row in {index.row() for index in self._view.selectionModel().selectedRows()}
        return float(hovered), float(selected)

    def _advance(self) -> None:
        """Step every tracked row towards its target, and repaint what moved."""
        moved = False
        for row in list(self._hover.keys() | self._selection.keys()):
            hover_goal, select_goal = self._target(row)
            for store, goal in ((self._hover, hover_goal), (self._selection, select_goal)):
                current = store.get(row, 0.0)
                if abs(goal - current) < _EPSILON:
                    if goal == 0.0:
                        store.pop(row, None)  # at rest: stop tracking it
                    else:
                        store[row] = goal
                    continue
                store[row] = current + (goal - current) * _STEP
                moved = True

        if moved:
            self._view.viewport().update()
        elif not (self._hover or self._selection):
            self._timer.stop()  # nothing is moving; do no work at all

    def _track(self, row: int) -> None:
        """Begin animating `row` if it is not already being animated."""
        hover_goal, select_goal = self._target(row)
        if hover_goal or select_goal or row in self._hover or row in self._selection:
            self._hover.setdefault(row, 0.0)
            self._selection.setdefault(row, 0.0)
            if not self._timer.isActive():
                self._timer.start()

    # -------------------------------------------------------------- painting --

    def paint(
        self,
        painter: QPainter,
        option: QStyleOptionViewItem,
        index: QModelIndex | QPersistentModelIndex,
    ) -> None:
        """Draw the row background, then let Qt draw the cell's content."""
        row = index.row()
        self._track(row)

        hover = self._hover.get(row, 0.0)
        selected = self._selection.get(row, 0.0)

        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        # A delegate paints one cell at a time, so rounding every cell's rect
        # draws the row as a string of separate pills with seams between them.
        # Interior cells are widened by the corner radius instead: their
        # rounded corners then fall outside the cell's own clip, and each rect
        # overlaps its neighbour, leaving a single continuous shape.
        # Clip to the true cell first: the widened rect below would otherwise
        # spill into the neighbouring cell, and the fill is semi-transparent,
        # so the overlap paints twice and shows as a bright band on every
        # column boundary.
        painter.setClipRect(option.rect)

        rect = QRectF(option.rect)
        last_column = index.model().columnCount() - 1
        if index.column() > 0:
            rect.setLeft(rect.left() - Radius.SM)
        if index.column() < last_column:
            rect.setRight(rect.right() + Radius.SM)

        if selected > _EPSILON or hover > _EPSILON:
            colour = QColor(self._palette.accent if selected > hover else "#FFFFFF")
            strength = max(selected * 0.22, hover * 0.06)
            colour.setAlphaF(min(1.0, strength))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(colour)
            painter.drawRoundedRect(rect, Radius.SM, Radius.SM)

        painter.restore()

        # Strip the states Qt would otherwise paint over our background.
        cell = QStyleOptionViewItem(option)
        self.initStyleOption(cell, index)
        cell.state &= ~QStyle.StateFlag.State_Selected
        cell.state &= ~QStyle.StateFlag.State_MouseOver
        # The current-cell focus indicator draws a bar on one column's edge,
        # which breaks the row into pieces again. Selection is already shown by
        # the row fill.
        cell.state &= ~QStyle.StateFlag.State_HasFocus
        cell.backgroundBrush = QBrush(Qt.BrushStyle.NoBrush)
        super().paint(painter, cell, index)
