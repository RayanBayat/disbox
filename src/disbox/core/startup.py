"""Getting into a vault: creating one, opening one, and remembering which.

The vault file is the whole point of this design -- lose it and the files on
Discord are unreachable -- so the application keeps a list of the ones it has
opened. That list is a convenience, never a source of truth: entries pointing at
files that have moved or been deleted are dropped rather than shown as choices
that fail when clicked.

A corrupt list is treated as an empty one. Being unable to remember which vaults
were opened last is a small loss; refusing to start over it is not.
"""

import json
from pathlib import Path
from typing import Final

import structlog

from disbox.core.crypto import KdfParams
from disbox.core.vault import Vault
from disbox.errors import VaultError

__all__ = ["RecentVaults", "create_vault", "open_vault"]

logger = structlog.get_logger(__name__)

#: Enough to cover the vaults someone actually switches between.
_MAX_REMEMBERED: Final = 10


class RecentVaults:
    """The vaults this installation has opened, most recent first."""

    def __init__(self, store: Path) -> None:
        """Persist the list to `store`."""
        self._store = store

    def paths(self) -> list[Path]:
        """Vaults that were opened before and still exist."""
        return [path for path in self._read() if path.is_file()]

    def remember(self, path: Path) -> None:
        """Record `path` as the most recently used vault."""
        resolved = path.resolve()
        kept = [item for item in self._read() if item != resolved]
        kept.insert(0, resolved)
        self._write(kept[:_MAX_REMEMBERED])

    def forget(self, path: Path) -> None:
        """Drop `path` from the list."""
        resolved = path.resolve()
        self._write([item for item in self._read() if item != resolved])

    def _read(self) -> list[Path]:
        """Load the stored list, treating anything unreadable as empty."""
        if not self._store.is_file():
            return []
        try:
            raw = json.loads(self._store.read_text(encoding="utf-8"))
            return [Path(item) for item in raw]
        except OSError, ValueError, TypeError:
            # A list of recently used files is not worth failing a launch over.
            logger.debug("recent vault list unreadable", path=str(self._store))
            return []

    def _write(self, paths: list[Path]) -> None:
        """Save the list, ignoring a failure to do so."""
        try:
            self._store.parent.mkdir(parents=True, exist_ok=True)
            self._store.write_text(json.dumps([str(item) for item in paths]), encoding="utf-8")
        except OSError:  # pragma: no cover - depends on the filesystem
            logger.debug("recent vault list could not be saved")


def create_vault(path: Path, passphrase: str, *, params: KdfParams | None = None) -> Vault:
    """Create an encrypted vault at `path`.

    Args:
        path: Where to create it. Must not already exist.
        passphrase: Used to derive the key that wraps the master key.
        params: KDF cost, for tests that cannot afford the real one.

    Returns:
        The opened vault.

    Raises:
        VaultError: If `path` exists or the passphrase is empty.
    """
    if not passphrase:
        # An empty passphrase derives a key anyone can derive, which is worse
        # than no encryption because it looks like protection.
        msg = "a passphrase is required to create a vault"
        raise VaultError(msg)
    if path.exists():
        msg = f"{path} already exists; creating over it would destroy its contents"
        raise VaultError(msg)

    return (
        Vault.create_encrypted(path, passphrase, params)
        if params is not None
        else Vault.create_encrypted(path, passphrase)
    )


def open_vault(path: Path, passphrase: str) -> Vault:
    """Open an existing vault and verify the passphrase.

    The passphrase is checked here rather than at first use, so a wrong one is
    reported while the user is still looking at the prompt.

    Raises:
        VaultError: If the vault cannot be opened.
        CryptoError: If the passphrase is wrong.
    """
    vault = Vault.open(path)
    try:
        vault.unlock(passphrase)
    except Exception:
        vault.close()  # never leave the single-writer lock held on a failure
        raise
    return vault
