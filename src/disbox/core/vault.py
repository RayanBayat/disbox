"""The vault: a single local SQLite file holding everything but the file bytes.

The vault records the tree, the chunk manifests, and the backend references that
turn a pile of opaque blobs back into files. It is the most valuable artifact
Disbox produces -- the bytes on the backend are unusable without it -- so it is
opened under a single-writer lock, written with maximum durability, and (from
later milestones) snapshotted locally, backed up remotely, and reconstructible
from a backend rescan.

Deliberately *not* stored here: the master key in usable form. `wrapped_mk` is
sealed under a passphrase-derived key, so a stolen vault file yields the tree's
shape but none of its contents.
"""

import json
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from types import TracebackType
from typing import Final, Self

from disbox.core import integrity, migrations
from disbox.core.lock import FileLock
from disbox.errors import VaultError

__all__ = ["KeyMaterial", "Vault"]

# SPEC.md V1. WAL keeps readers from blocking the writer; synchronous=FULL
# trades throughput for surviving power loss, which is the correct trade for a
# file whose loss costs the user every stored byte.
_PRAGMAS: Final = (
    ("journal_mode", "WAL"),
    ("synchronous", "FULL"),
    ("foreign_keys", "ON"),
    ("busy_timeout", "5000"),
)


@dataclass(frozen=True, slots=True)
class KeyMaterial:
    """Everything needed to re-derive the master key from a passphrase.

    Attributes:
        kdf_salt: Random salt for the key-derivation function.
        kdf_params: JSON-encoded KDF cost parameters, recorded so a vault stays
            openable after the defaults are raised for newer hardware.
        wrapped_mk: The master key, sealed under the passphrase-derived key.
        mk_check: Verifier allowing a wrong passphrase to be rejected without
            attempting to decrypt user data.
    """

    kdf_salt: bytes
    kdf_params: str
    wrapped_mk: bytes
    mk_check: bytes


class Vault:
    """An open vault. Hold it briefly; close it to release the writer lock.

    Prefer the context-manager form, which closes on any exit path::

        with Vault.open(path) as vault:
            ...
    """

    def __init__(self, path: Path, conn: sqlite3.Connection, lock: FileLock) -> None:
        """Wrap an already-opened connection. Use `open` or `create` instead."""
        self.path = path
        self._conn: sqlite3.Connection | None = conn
        self._lock = lock

    # ---------------------------------------------------------------- open --

    @classmethod
    def create(cls, path: Path, key_material: KeyMaterial) -> Self:
        """Create a new vault at `path`.

        Args:
            path: Destination file. Its parent directory is created if absent.
            key_material: Wrapped master key and KDF parameters to record.

        Returns:
            The newly created, open vault.

        Raises:
            VaultError: If `path` already exists, or the file cannot be created.
            VaultLockedError: If another process holds the vault.
        """
        if path.exists():
            msg = f"a vault already exists at {path}; open it instead of creating one"
            raise VaultError(msg)

        vault = cls._connect(path)
        try:
            with vault.connection as conn:
                conn.execute(
                    "INSERT INTO meta (vault_id, schema_version, created_at, kdf_salt, "
                    "kdf_params, wrapped_mk, mk_check) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        uuid.uuid7().bytes,
                        migrations.LATEST_VERSION,
                        datetime.now(UTC).isoformat(),
                        key_material.kdf_salt,
                        key_material.kdf_params,
                        key_material.wrapped_mk,
                        key_material.mk_check,
                    ),
                )
        except Exception:
            vault.close()
            path.unlink(missing_ok=True)
            raise
        return vault

    @classmethod
    def open(cls, path: Path) -> Self:
        """Open an existing vault, applying any pending migrations.

        Args:
            path: Vault file to open.

        Returns:
            The open vault.

        Raises:
            VaultError: If `path` does not exist or is not a Disbox vault.
            VaultLockedError: If another process holds the vault.
        """
        if not path.exists():
            msg = f"no vault at {path}"
            raise VaultError(msg)

        vault = cls._connect(path)
        try:
            row = vault.connection.execute("SELECT count(*) FROM meta").fetchone()
        except sqlite3.DatabaseError as exc:
            vault.close()
            msg = f"{path} is not a Disbox vault: {exc}"
            raise VaultError(msg) from exc
        if row[0] != 1:
            vault.close()
            msg = f"{path} is missing its metadata row and cannot be opened"
            raise VaultError(msg)
        return vault

    @classmethod
    def _connect(cls, path: Path) -> Self:
        """Lock, connect, apply pragmas, and migrate. Shared by open and create."""
        path.parent.mkdir(parents=True, exist_ok=True)
        lock = FileLock(path.with_suffix(path.suffix + ".lock")).acquire()
        conn: sqlite3.Connection | None = None
        try:
            conn = sqlite3.connect(path, isolation_level="DEFERRED")
            _apply_pragmas(conn)
            # SPEC.md V5: refuse to write to a damaged vault. Checked before
            # migrating, since migrating damaged pages only spreads the harm.
            integrity.quick_check(conn)
            migrations.migrate(conn)
        except sqlite3.DatabaseError as exc:
            # Raised when the file exists but is not a SQLite database at all.
            # Surface it as a VaultError naming the path, rather than leaking a
            # driver-level "file is not a database" to the user.
            _discard(conn, lock)
            msg = f"{path} is not a usable Disbox vault: {exc}"
            raise VaultError(msg) from exc
        except Exception:
            _discard(conn, lock)
            raise
        return cls(path, conn, lock)

    # ------------------------------------------------------------- access --

    @property
    def connection(self) -> sqlite3.Connection:
        """The underlying connection.

        Raises:
            VaultError: If the vault has been closed.
        """
        if self._conn is None:
            msg = f"vault at {self.path} is closed"
            raise VaultError(msg)
        return self._conn

    @property
    def is_open(self) -> bool:
        """Whether the vault is still usable."""
        return self._conn is not None

    @property
    def schema_version(self) -> int:
        """Schema version currently applied to this vault."""
        return migrations.current_version(self.connection)

    @property
    def vault_id(self) -> uuid.UUID:
        """Stable identifier stamped into every blob this vault uploads."""
        row = self.connection.execute("SELECT vault_id FROM meta").fetchone()
        return uuid.UUID(bytes=row[0])

    @property
    def key_material(self) -> KeyMaterial:
        """Wrapped master key and KDF parameters recorded for this vault."""
        row = self.connection.execute(
            "SELECT kdf_salt, kdf_params, wrapped_mk, mk_check FROM meta"
        ).fetchone()
        return KeyMaterial(kdf_salt=row[0], kdf_params=row[1], wrapped_mk=row[2], mk_check=row[3])

    # ---------------------------------------------------------- lifecycle --

    def close(self) -> None:
        """Close the connection and release the writer lock. Safe to call twice."""
        if self._conn is None:
            return
        conn, self._conn = self._conn, None
        try:
            conn.rollback()  # discard anything a failed caller left open
            conn.close()
        finally:
            self._lock.release()

    def __enter__(self) -> Self:
        """Return the already-open vault."""
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        """Close on exit, whether or not the block raised."""
        self.close()


def _discard(conn: sqlite3.Connection | None, lock: FileLock) -> None:
    """Tear down a half-built vault, leaking neither the handle nor the lock."""
    if conn is not None:
        conn.close()
    lock.release()


def _apply_pragmas(conn: sqlite3.Connection) -> None:
    """Apply the durability settings required by SPEC.md V1.

    Raises:
        VaultError: If a pragma does not take effect, which would silently
            weaken the guarantees the rest of the design assumes.
    """
    for name, value in _PRAGMAS:
        # Pragma names and values are module constants, never caller input.
        conn.execute(f"PRAGMA {name} = {value}")

    mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
    if str(mode).lower() != "wal":
        msg = f"could not enable write-ahead logging (journal_mode is {mode!r})"
        raise VaultError(msg)


def parse_kdf_params(raw: str) -> dict[str, int]:
    """Decode the stored KDF parameters.

    Args:
        raw: JSON text as recorded in `meta.kdf_params`.

    Returns:
        The cost parameters as integers.

    Raises:
        VaultError: If the text is not a JSON object of integers.
    """
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        msg = f"kdf_params is not valid JSON: {exc}"
        raise VaultError(msg) from exc
    if not isinstance(parsed, dict) or not all(isinstance(v, int) for v in parsed.values()):
        msg = f"kdf_params must be a JSON object of integers, got {raw!r}"
        raise VaultError(msg)
    return parsed
