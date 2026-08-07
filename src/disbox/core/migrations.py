"""Forward-only schema migrations keyed on SQLite's ``PRAGMA user_version``.

Alembic would be overkill here: the vault is a single-user local file with no
online rollout to coordinate, so a numbered sequence of SQL scripts applied in
order is both sufficient and far easier to audit.

Each migration runs inside a transaction together with its version bump, so a
failure leaves the database at its previous version rather than half-migrated.
SQLite makes this possible because DDL is transactional, unlike most engines.

To add a migration, drop ``000N_description.sql`` into ``schema/``. It is picked
up automatically; the numeric prefix defines the order.
"""

import re
import sqlite3
from dataclasses import dataclass
from importlib.resources import files
from typing import Final

from disbox.errors import MigrationError

__all__ = [
    "LATEST_VERSION",
    "MIGRATIONS",
    "Migration",
    "MigrationError",
    "current_version",
    "migrate",
]

_SCHEMA_PACKAGE: Final = "disbox.core.schema"
_FILENAME_PATTERN: Final = re.compile(r"^(\d{4})_[a-z0-9_]+\.sql$")


@dataclass(frozen=True, slots=True)
class Migration:
    """One numbered schema migration."""

    version: int
    name: str
    sql: str


def _discover() -> tuple[Migration, ...]:
    """Load every migration script from the schema package, ordered by version.

    Returns:
        Migrations sorted ascending by version.

    Raises:
        MigrationError: If a filename is malformed or a version is duplicated.
    """
    found: dict[int, Migration] = {}
    for entry in files(_SCHEMA_PACKAGE).iterdir():
        if not entry.name.endswith(".sql"):
            continue
        match = _FILENAME_PATTERN.match(entry.name)
        if match is None:
            msg = f"malformed migration filename {entry.name!r}; expected NNNN_name.sql"
            raise MigrationError(msg)
        version = int(match.group(1))
        if version in found:
            msg = f"duplicate migration version {version}: {found[version].name} and {entry.name}"
            raise MigrationError(msg)
        found[version] = Migration(
            version=version,
            name=entry.name,
            sql=entry.read_text(encoding="utf-8"),
        )

    if not found:
        msg = f"no migrations found in {_SCHEMA_PACKAGE}"
        raise MigrationError(msg)

    expected = list(range(1, max(found) + 1))
    if sorted(found) != expected:
        msg = f"migration versions must be contiguous from 1; got {sorted(found)}"
        raise MigrationError(msg)

    return tuple(found[version] for version in expected)


MIGRATIONS: Final = _discover()
LATEST_VERSION: Final = MIGRATIONS[-1].version


def current_version(conn: sqlite3.Connection) -> int:
    """Return the schema version recorded in the database, 0 if never migrated."""
    row = conn.execute("PRAGMA user_version").fetchone()
    return int(row[0])


def migrate(conn: sqlite3.Connection) -> int:
    """Apply every migration the database has not yet seen.

    Safe to call on every open: already-applied migrations are skipped, so this
    is idempotent.

    Args:
        conn: Connection to migrate. Committed on success.

    Returns:
        The schema version after migrating.

    Raises:
        MigrationError: If the database is newer than this build understands, or
            a migration script fails.
    """
    version = current_version(conn)
    if version > LATEST_VERSION:
        msg = (
            f"vault schema version {version} is newer than this build supports "
            f"({LATEST_VERSION}); upgrade Disbox to open it"
        )
        raise MigrationError(msg)

    for migration in MIGRATIONS:
        if migration.version <= version:
            continue
        try:
            with conn:
                conn.executescript(migration.sql)
                # PRAGMA cannot be parameterised. The value is an int parsed
                # from a filename matched against _FILENAME_PATTERN, so it can
                # only ever be four digits.
                conn.execute(f"PRAGMA user_version = {migration.version:d}")
        except sqlite3.Error as exc:
            msg = f"migration {migration.name} failed: {exc}"
            raise MigrationError(msg) from exc

    return current_version(conn)
