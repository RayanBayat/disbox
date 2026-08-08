"""Application entry point for the Disbox desktop client."""

import sys
from pathlib import Path

from PySide6.QtWidgets import QApplication, QFileDialog, QMessageBox

from disbox.core.vault import Vault
from disbox.errors import DisboxError
from disbox.gui.bridge import AsyncBridge
from disbox.gui.views.main_window import MainWindow
from disbox.log import configure, get_logger

__all__ = ["main"]

logger = get_logger(__name__)


def choose_vault() -> Path | None:
    """Ask the user which vault to open.

    Returns:
        The chosen path, or None if the dialog was dismissed.
    """
    selected, _ = QFileDialog.getOpenFileName(
        None, "Open a Disbox vault", str(Path.home()), "Disbox vaults (*.dbx)"
    )
    return Path(selected) if selected else None


def main(argv: list[str] | None = None) -> int:
    """Run the desktop client.

    Args:
        argv: Command-line arguments. A single optional vault path.

    Returns:
        A process exit code.
    """
    configure(level="INFO")
    args = sys.argv[1:] if argv is None else argv
    app = QApplication(sys.argv[:1])
    # Qt's native Windows 11 style paints its own Fluent decorations that no
    # stylesheet or delegate can suppress -- notably an accent indicator at the
    # leading edge of every selected cell, which broke rows into visible pieces.
    # Fusion draws nothing on its own, so the stylesheet is the only authority.
    app.setStyle("Fusion")

    path = Path(args[0]) if args else choose_vault()
    if path is None:
        return 0

    try:
        vault = Vault.open(path)
    except DisboxError as exc:
        # A vault that is damaged or already open is an expected outcome, not a
        # crash; the message from core already explains what to do about it.
        QMessageBox.critical(None, "Cannot open vault", str(exc))
        logger.warning("vault could not be opened", path=str(path), reason=str(exc))
        return 1

    bridge = AsyncBridge()
    bridge.start()
    try:
        window = MainWindow(vault, bridge=bridge)
        window.show()
        return app.exec()
    finally:
        # Order matters: the loop must stop before the vault closes, or work
        # still in flight would reach a closed database.
        bridge.stop()
        vault.close()  # always release the single-writer lock


if __name__ == "__main__":
    raise SystemExit(main())
