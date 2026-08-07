"""Disbox: encrypted, deduplicated file storage on Discord with a local-first vault.

The package is layered so each level depends only on those below it:

* ``disbox.core``     -- vault, crypto, chunking, and the transfer engine.
* ``disbox.backends`` -- pluggable blob stores; Discord is one implementation.
* ``disbox.gui``      -- PySide6 desktop client.
* ``disbox.cli``      -- Typer command line.
"""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("disbox")
except PackageNotFoundError:  # pragma: no cover - only when running from a source tree
    __version__ = "0.0.0.dev0"

__all__ = ["__version__"]
