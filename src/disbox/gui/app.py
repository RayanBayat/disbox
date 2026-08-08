"""Application entry point for the Disbox desktop client."""

import sys
from pathlib import Path

from platformdirs import user_data_path
from PySide6.QtWidgets import QApplication, QMessageBox

from disbox.core.startup import RecentVaults
from disbox.gui.bridge import AsyncBridge
from disbox.gui.theme.backdrop import system_prefers_dark
from disbox.gui.theme.tokens import DARK, LIGHT
from disbox.gui.views.main_window import MainWindow
from disbox.gui.views.startup_dialog import StartupDialog
from disbox.log import configure, get_logger

__all__ = ["main"]

logger = get_logger(__name__)


def recent_vaults() -> RecentVaults:
    """The list of vaults this installation has opened."""
    return RecentVaults(user_data_path("disbox", appauthor=False) / "recent.json")


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

    palette = DARK if system_prefers_dark() else LIGHT
    recents = recent_vaults()

    if args:
        # A path on the command line preselects that vault, but it still goes
        # through the picker: an encrypted vault needs its passphrase, and
        # opening it without one only defers the failure to first use.
        path = Path(args[0])
        if not path.is_file():
            QMessageBox.critical(None, "Cannot open vault", f"{path} does not exist")
            logger.warning("vault path does not exist", path=str(path))
            return 1
        recents.remember(path)

    dialog = StartupDialog(recents, palette)
    if not dialog.exec() or dialog.vault is None:
        return 0
    vault = dialog.vault

    bridge = AsyncBridge()
    bridge.start()
    try:
        window = MainWindow(vault, palette, bridge=bridge)
        window.show()
        return app.exec()
    finally:
        # Order matters: the loop must stop before the vault closes, or work
        # still in flight would reach a closed database.
        bridge.stop()
        vault.close()  # always release the single-writer lock


if __name__ == "__main__":
    raise SystemExit(main())
