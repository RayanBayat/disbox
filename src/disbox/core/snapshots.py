"""Local point-in-time copies of a vault, taken with SQLite's Online Backup API.

The backup API is used rather than copying the file because a vault is normally
*open* when a snapshot is due. Copying an open WAL database with the filesystem
would capture a torn state -- main database and write-ahead log caught at
different instants -- producing a snapshot that looks fine and restores broken.
The backup API instead walks pages under SQLite's own locking and yields between
batches, so the writer is never stalled for long.

Snapshots land atomically: SQLite writes to a temporary name which is then
renamed over the final one. A crash mid-backup therefore leaves a discardable
``.tmp`` file rather than a truncated snapshot that would pass a casual check
and fail when it is actually needed.
"""

import contextlib
import re
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Final

from disbox.log import get_logger

if TYPE_CHECKING:
    from disbox.core.vault import Vault

__all__ = ["Snapshot", "SnapshotPolicy", "SnapshotStore", "parse_snapshot_name"]

logger = get_logger(__name__)

# Colons are legal in ISO-8601 and illegal in Windows filenames, so the
# timestamp is written compactly. Microseconds are included because snapshots
# can legitimately be taken twice within the same second.
_STAMP_FORMAT: Final = "%Y%m%dT%H%M%S_%f"
_NAME_PATTERN: Final = re.compile(r"^vault-(\d{8}T\d{6}_\d{6})Z\.dbx$")

# Copy in batches so a long backup yields the write lock between steps instead
# of holding it for the whole operation.
_PAGES_PER_STEP: Final = 256


@dataclass(frozen=True, slots=True)
class SnapshotPolicy:
    """How many snapshots to retain.

    Attributes:
        keep_recent: Always keep this many newest snapshots, whatever their age.
        keep_daily_days: Additionally keep the newest snapshot of each calendar
            day within this many days.
    """

    keep_recent: int = 15
    keep_daily_days: int = 30


@dataclass(frozen=True, slots=True)
class Snapshot:
    """One snapshot on disk."""

    path: Path
    taken_at: datetime


def parse_snapshot_name(name: str) -> datetime | None:
    """Recover the timestamp encoded in a snapshot filename.

    Args:
        name: Bare filename, not a full path.

    Returns:
        The UTC instant the snapshot was taken, or None if `name` is not one of
        ours -- which is how unrelated files in the directory stay safe from
        pruning.
    """
    match = _NAME_PATTERN.match(name)
    if match is None:
        return None
    return datetime.strptime(match.group(1), _STAMP_FORMAT).replace(tzinfo=UTC)


class SnapshotStore:
    """A directory of vault snapshots, with a retention policy."""

    def __init__(self, directory: Path, policy: SnapshotPolicy | None = None) -> None:
        """Manage snapshots under `directory`, creating it lazily."""
        self.directory = directory
        self.policy = policy or SnapshotPolicy()

    def filename_for(self, when: datetime) -> str:
        """Return the filename a snapshot taken at `when` would use."""
        return f"vault-{when.astimezone(UTC).strftime(_STAMP_FORMAT)}Z.dbx"

    def take(self, vault: Vault, *, now: datetime | None = None) -> Snapshot:
        """Copy `vault` into a new snapshot.

        Args:
            vault: An open vault. It remains usable and is not locked out.
            now: Timestamp to record. Defaults to the current time.

        Returns:
            The snapshot written.

        Raises:
            sqlite3.Error: If the backup could not be completed.
        """
        taken_at = (now or datetime.now(UTC)).astimezone(UTC)
        self.directory.mkdir(parents=True, exist_ok=True)
        final = self.directory / self.filename_for(taken_at)
        staging = final.with_suffix(".tmp")

        try:
            with contextlib.closing(sqlite3.connect(staging)) as destination:
                vault.connection.backup(destination, pages=_PAGES_PER_STEP)
            staging.replace(final)
        except BaseException:
            staging.unlink(missing_ok=True)
            raise

        logger.info("snapshot taken", path=str(final), size=final.stat().st_size)
        return Snapshot(path=final, taken_at=taken_at)

    def snapshots(self) -> list[Snapshot]:
        """Return every recognised snapshot, newest first."""
        if not self.directory.is_dir():
            return []
        found = [
            Snapshot(path=entry, taken_at=taken_at)
            for entry in self.directory.iterdir()
            if entry.is_file() and (taken_at := parse_snapshot_name(entry.name)) is not None
        ]
        return sorted(found, key=lambda snap: snap.taken_at, reverse=True)

    def latest(self) -> Snapshot | None:
        """Return the newest snapshot, or None if there are none."""
        snapshots = self.snapshots()
        return snapshots[0] if snapshots else None

    def prune(self, *, now: datetime | None = None) -> list[Path]:
        """Delete snapshots the policy no longer requires.

        Only files matching the snapshot naming scheme are ever considered, so
        anything else sharing the directory is left untouched.

        Args:
            now: Reference time for the daily window. Defaults to the present.

        Returns:
            The paths removed.
        """
        reference = (now or datetime.now(UTC)).astimezone(UTC)
        snapshots = self.snapshots()
        keep = {snap.path for snap in snapshots[: self.policy.keep_recent]}

        # Newest snapshot of each day still inside the daily window.
        cutoff = reference.date()
        seen_days: set[object] = set()
        for snap in snapshots:  # already newest-first, so the first per day wins
            age = (cutoff - snap.taken_at.date()).days
            if 0 <= age < self.policy.keep_daily_days and snap.taken_at.date() not in seen_days:
                seen_days.add(snap.taken_at.date())
                keep.add(snap.path)

        removed: list[Path] = []
        for snap in snapshots:
            if snap.path in keep:
                continue
            snap.path.unlink(missing_ok=True)
            removed.append(snap.path)

        if removed:
            logger.info("pruned snapshots", count=len(removed))
        return removed
