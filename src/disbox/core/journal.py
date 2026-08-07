"""Append-only record of every mutation applied to a vault.

The journal exists so that "what happened to my files?" always has an answer.
It is the forensic trail when something goes wrong, and later it backs the GUI's
undo stack, since each entry carries enough context to describe -- and reverse
-- the change it records.

Recording is exposed as the `journaled` decorator rather than as a call every
mutation must remember to make. An operation that forgets to journal is
invisible forever, and that failure is silent, so the safe default is for the
mechanism to be impossible to omit once a method is decorated.

The entry and the mutation share a single transaction. Either both land or
neither does; there is no state in which the vault changed without a record, or
a record exists for a change that rolled back.

Nothing in this module deletes entries. That is deliberate: a forensic log that
can be trimmed by the code under investigation is not evidence.
"""

import functools
import json
import sqlite3
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Concatenate, Protocol, cast

from disbox.log import redact_processor

__all__ = ["JournalEntry", "entries", "journaled", "record"]


class HasConnection(Protocol):
    """Anything owning a vault connection can have its mutations journalled."""

    @property
    def connection(self) -> sqlite3.Connection:
        """The connection the mutation writes through."""
        ...


@dataclass(frozen=True, slots=True)
class JournalEntry:
    """One recorded mutation.

    Attributes:
        id: Monotonic row id; larger means later.
        ts: When the mutation was recorded, in UTC.
        op: Short verb naming the operation, such as ``rename`` or ``delete``.
        target_id: Node the operation acted on, when it acted on one.
        payload: Operation-specific context, already redacted of secrets.
    """

    id: int
    ts: datetime
    op: str
    target_id: uuid.UUID | None = None
    payload: dict[str, Any] = field(default_factory=dict)


def record(
    conn: sqlite3.Connection,
    op: str,
    *,
    target_id: uuid.UUID | None = None,
    payload: dict[str, Any] | None = None,
) -> int:
    """Append an entry to the journal.

    Does not commit: the caller's transaction decides whether this entry and the
    mutation it describes both survive.

    Args:
        conn: Connection to write through.
        op: Short verb naming the operation.
        target_id: Node the operation acted on, if any.
        payload: Extra context. Passed through the logging redactor, so a
            carelessly included credential is not persisted.

    Returns:
        The id of the recorded entry.
    """
    safe = redact_processor(None, "info", dict(payload or {}))
    cursor = conn.execute(
        "INSERT INTO journal (ts, op, target_id, payload) VALUES (?, ?, ?, ?)",
        (
            datetime.now(UTC).isoformat(),
            op,
            target_id.bytes if target_id else None,
            json.dumps(safe, default=str),
        ),
    )
    return int(cursor.lastrowid or 0)


def entries(
    conn: sqlite3.Connection,
    *,
    limit: int | None = None,
    op: str | None = None,
) -> list[JournalEntry]:
    """Read journal entries, newest first.

    Args:
        conn: Connection to read from.
        limit: Maximum number of entries to return. All of them if omitted.
        op: Restrict to a single operation name.

    Returns:
        Matching entries, newest first.
    """
    sql = "SELECT id, ts, op, target_id, payload FROM journal"
    params: list[object] = []
    if op is not None:
        sql += " WHERE op = ?"
        params.append(op)
    sql += " ORDER BY id DESC"
    if limit is not None:
        sql += " LIMIT ?"
        params.append(limit)

    return [
        JournalEntry(
            id=row[0],
            ts=datetime.fromisoformat(row[1]),
            op=row[2],
            target_id=uuid.UUID(bytes=row[3]) if row[3] is not None else None,
            payload=json.loads(row[4]) if row[4] else {},
        )
        for row in conn.execute(sql, params).fetchall()
    ]


def journaled[S: HasConnection, **P, R](
    op: str,
) -> Callable[[Callable[Concatenate[S, P], R]], Callable[Concatenate[S, P], R]]:
    """Record every successful call to the decorated mutation.

    The wrapped method must not manage its own transaction; this decorator opens
    one covering both the mutation and its journal entry, so a failure rolls
    back both together.

    Args:
        op: Short verb naming the operation, stored as `JournalEntry.op`.

    Returns:
        A decorator for a method on any object exposing ``connection``.
    """

    def decorate(func: Callable[Concatenate[S, P], R]) -> Callable[Concatenate[S, P], R]:
        @functools.wraps(func)
        def wrapper(self: S, *args: P.args, **kwargs: P.kwargs) -> R:
            with self.connection:
                result = func(self, *args, **kwargs)
                record(self.connection, op, target_id=_first_uuid(args))
            return result

        # functools.wraps returns a _Wrapped whose __call__ names the first
        # parameter 'self', which mypy will not unify with Concatenate[S, P].
        # The runtime signature is identical; dropping wraps to satisfy the
        # checker would lose __name__ and __doc__ on every mutation.
        return cast("Callable[Concatenate[S, P], R]", wrapper)

    return decorate


def _first_uuid(args: tuple[object, ...]) -> uuid.UUID | None:
    """Pick the operation's target from its positional arguments, if present."""
    return next((arg for arg in args if isinstance(arg, uuid.UUID)), None)
