"""Damage detection and recovery for a vault.

Three layers, cheapest first:

* `quick_check` -- SQLite's own structural scan, minus the expensive index
  cross-checks. Cheap enough to run on every open.
* `full_check` -- the complete structural check plus foreign-key verification.
  Run on demand or on a schedule.
* `check_invariants` -- application-level rules SQLite cannot express as
  constraints. Two matter most:

  - ``chunks.refcount`` must match reality. Drift is dangerous in both
    directions: too high leaks storage forever, too low lets the collector
    delete a blob a live file still needs.
  - The tree must be acyclic. Foreign keys cannot enforce this, and a cycle
    makes an entire subtree unreachable while every row remains individually
    valid.

Recovery never destroys evidence. A vault replaced from a snapshot is moved
aside rather than overwritten, because a damaged database is frequently still
partially readable, and the bytes it holds may exist nowhere else.
"""

import shutil
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from disbox.errors import IntegrityError, VaultError
from disbox.log import get_logger

__all__ = ["check_invariants", "full_check", "quick_check", "restore_from_snapshot"]

logger = get_logger(__name__)


def quick_check(conn: sqlite3.Connection) -> None:
    """Run SQLite's fast structural check.

    Args:
        conn: Connection to check.

    Raises:
        IntegrityError: If the database is damaged.
    """
    _run_sqlite_check(conn, "quick_check")


def full_check(conn: sqlite3.Connection) -> None:
    """Run the complete structural check plus foreign-key verification.

    Slower than `quick_check` because it also cross-checks every index.

    Args:
        conn: Connection to check.

    Raises:
        IntegrityError: If the database is damaged or a foreign key dangles.
    """
    _run_sqlite_check(conn, "integrity_check")

    violations = conn.execute("PRAGMA foreign_key_check").fetchall()
    if violations:
        msg = f"foreign key violations found: {violations[:5]}"
        raise IntegrityError(msg)


def _run_sqlite_check(conn: sqlite3.Connection, pragma: str) -> None:
    """Run a check pragma, normalising both ways SQLite reports damage.

    A damaged page makes the pragma *raise*, while subtler problems make it
    *return* rows describing them. Both must be treated as failure.

    Raises:
        IntegrityError: If the check does not report exactly "ok".
    """
    try:
        rows = conn.execute(f"PRAGMA {pragma}").fetchall()
    except sqlite3.DatabaseError as exc:
        msg = f"{pragma} failed: {exc}"
        raise IntegrityError(msg) from exc

    problems = [row[0] for row in rows if row[0] != "ok"]
    if problems:
        msg = f"{pragma} reported damage: {problems[:5]}"
        raise IntegrityError(msg)


def check_invariants(conn: sqlite3.Connection) -> list[str]:
    """Verify the application-level rules SQLite cannot enforce itself.

    Args:
        conn: Connection to inspect.

    Returns:
        Human-readable descriptions of every violation found. Empty when the
        vault is consistent.
    """
    problems: list[str] = []

    drifted = conn.execute(
        """
        SELECT c.hash, c.refcount, count(rc.chunk_hash) AS actual
        FROM chunks AS c
        LEFT JOIN revision_chunks AS rc ON rc.chunk_hash = c.hash
        GROUP BY c.hash, c.refcount
        HAVING c.refcount <> actual
        """
    ).fetchall()
    problems.extend(
        f"chunk {row[0].hex()} records refcount {row[1]} but has {row[2]} references"
        for row in drifted
    )

    orphans = conn.execute(
        """
        SELECT rc.revision_id, rc.idx, rc.chunk_hash
        FROM revision_chunks AS rc
        LEFT JOIN chunks AS c ON c.hash = rc.chunk_hash
        WHERE c.hash IS NULL
        """
    ).fetchall()
    problems.extend(
        f"revision {row[0]} chunk {row[1]} points at missing chunk {row[2].hex()}"
        for row in orphans
    )

    missing_parents = conn.execute(
        """
        SELECT n.id, n.name
        FROM nodes AS n
        LEFT JOIN nodes AS p ON p.id = n.parent_id
        WHERE n.parent_id IS NOT NULL AND p.id IS NULL
        """
    ).fetchall()
    problems.extend(
        f"node {row[1]!r} references a parent that does not exist" for row in missing_parents
    )

    # Foreign keys cannot prevent a parent cycle: inserting A with no parent,
    # then B under A, then repointing A at B leaves both rows referentially
    # valid and mutually unreachable. Such a subtree is invisible to every tree
    # walk and would hang a naive recursive traversal.
    #
    # Rather than chasing parent chains, walk down from the roots and report
    # whatever the walk cannot reach. UNION (not UNION ALL) discards rows
    # already seen, so the recursion terminates even though the data loops.
    # Joining to the parent row excludes nodes that are unreachable merely
    # because their parent is missing -- a distinct fault, reported above.
    cycles = conn.execute(
        """
        WITH RECURSIVE reachable(id) AS (
            SELECT id FROM nodes WHERE parent_id IS NULL
            UNION
            SELECT n.id FROM nodes AS n JOIN reachable AS r ON n.parent_id = r.id
        )
        SELECT n.id, n.name
        FROM nodes AS n
        JOIN nodes AS p ON p.id = n.parent_id
        WHERE n.id NOT IN (SELECT id FROM reachable)
        ORDER BY n.name
        """
    ).fetchall()
    problems.extend(
        f"node {row[1]!r} is unreachable from any root and lies on a parent cycle" for row in cycles
    )

    return problems


def restore_from_snapshot(vault_path: Path, snapshot_path: Path) -> Path | None:
    """Replace a vault with a snapshot, quarantining whatever was there.

    The vault must not be open. The snapshot is verified before anything is
    touched, so a failed restore leaves the existing file exactly as it was.

    Args:
        vault_path: Vault to replace.
        snapshot_path: Snapshot to restore from.

    Returns:
        Where the previous vault was moved, or None if there was no vault.

    Raises:
        VaultError: If the snapshot does not exist.
        IntegrityError: If the snapshot is itself damaged.
    """
    if not snapshot_path.is_file():
        msg = f"no snapshot at {snapshot_path}"
        raise VaultError(msg)

    # Verify before disturbing anything, so a bad snapshot cannot cost the user
    # the (damaged but possibly readable) vault they still have.
    verify_conn = sqlite3.connect(snapshot_path)
    try:
        quick_check(verify_conn)
    finally:
        verify_conn.close()

    quarantined: Path | None = None
    if vault_path.exists():
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S_%f")
        quarantined = vault_path.with_suffix(f"{vault_path.suffix}.corrupt-{stamp}")
        vault_path.replace(quarantined)

    try:
        shutil.copy2(snapshot_path, vault_path)
    except OSError:
        if quarantined is not None:  # put the original back rather than leave nothing
            quarantined.replace(vault_path)
        raise

    logger.warning(
        "vault restored from snapshot",
        vault=str(vault_path),
        snapshot=str(snapshot_path),
        quarantined=str(quarantined) if quarantined else None,
    )
    return quarantined
