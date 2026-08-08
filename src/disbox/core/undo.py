"""Undo the last mutation, driven by the journal.

The journal already records every mutation, so undo reads from it rather than
keeping a second history that could disagree with it. An in-memory stack would
also be lost on restart and would say nothing about what another session did.

Each undo is itself journalled, as an ``undo`` entry naming both the entry it
reversed and the entries its own inverse operation produced. Recording only the
former is not enough: reversing a rename performs a rename, which journals a
fresh entry that the next undo would happily reverse, leaving the two undoing
each other forever instead of walking back through history.

Not every operation is reversible. An undo that would overwrite something is
refused, on the same principle as restore-from-trash: undo returns the tree to a
previous state, and it may not destroy anything to do it.
"""

import uuid

from disbox.core import journal
from disbox.core.filesystem import FileSystem
from disbox.core.vault import Vault
from disbox.errors import DisboxError

__all__ = ["describe_next_undo", "undo_last"]

#: Operations that can be reversed, and how they read to a user.
_REVERSIBLE = {
    "create": "create",
    "rename": "rename",
    "delete": "delete",
    "restore": "restore",
    "move": "move",
}

#: How far back to look for something to undo. Beyond this the history is old
#: enough that reversing it is more likely to surprise than to help.
_SEARCH_DEPTH = 200


def _next_entry(vault: Vault) -> journal.JournalEntry | None:
    """The most recent reversible entry that has not already been reversed."""
    recent = journal.entries(vault.raw_connection, limit=_SEARCH_DEPTH)

    spent: set[int] = set()
    for entry in recent:
        if entry.op != "undo":
            continue
        if "of" in entry.payload:
            spent.add(int(entry.payload["of"]))
        # The inverse operation journalled itself; those entries describe the
        # undo rather than a user action, so they are not undoable either.
        spent.update(int(value) for value in entry.payload.get("via", []))

    for entry in recent:  # newest first
        if entry.op in _REVERSIBLE and entry.id not in spent:
            return entry
    return None


def describe_next_undo(vault: Vault) -> str | None:
    """Name the operation that `undo_last` would reverse, if any."""
    entry = _next_entry(vault)
    return None if entry is None else _REVERSIBLE[entry.op]


def undo_last(vault: Vault) -> str | None:
    """Reverse the most recent reversible mutation.

    Args:
        vault: Vault to act on.

    Returns:
        A description of what was undone, or None when there was nothing to
        undo. A refusal is returned as a message explaining why, since being
        unable to undo safely is an outcome the user needs to read rather than
        an exception to handle.
    """
    entry = _next_entry(vault)
    if entry is None or entry.target_id is None:
        return None

    filesystem = FileSystem(vault)
    high_water = _latest_id(vault)
    try:
        applied = _apply_inverse(filesystem, entry)
    except DisboxError as exc:
        return f"Cannot undo {_REVERSIBLE[entry.op]}: {exc}"

    if not applied:
        return None

    produced = [
        item.id
        for item in journal.entries(vault.raw_connection, limit=_SEARCH_DEPTH)
        if item.id > high_water
    ]
    with vault.connection as conn:
        journal.record(
            conn,
            "undo",
            target_id=entry.target_id,
            payload={"of": entry.id, "via": produced},
        )
    return f"Undid {_REVERSIBLE[entry.op]}"


def _latest_id(vault: Vault) -> int:
    """The newest journal id, or zero when the journal is empty."""
    recent = journal.entries(vault.raw_connection, limit=1)
    return recent[0].id if recent else 0


def _apply_inverse(filesystem: FileSystem, entry: journal.JournalEntry) -> bool:
    """Apply the inverse of `entry`. Returns whether anything was done."""
    target = entry.target_id
    if target is None:  # pragma: no cover - guarded by the caller
        return False

    match entry.op:
        case "create":
            filesystem.delete(target)
        case "delete":
            filesystem.restore(target)
        case "restore":
            filesystem.delete(target)
        case "rename":
            previous = entry.payload.get("from")
            if not isinstance(previous, str):
                return False
            filesystem.rename(target, previous)
        case "move":
            origin = entry.payload.get("from")
            if not isinstance(origin, str):
                # Recorded before the payload carried an origin, so there is
                # nowhere to put it back.
                return False
            filesystem.move(target, None if origin == "None" else uuid.UUID(origin))
        case _:  # pragma: no cover - _REVERSIBLE gates this
            return False
    return True
