"""A record of what the application has told the user.

SPEC M8-12 rules out blocking modals for errors, which means a failure has to go
somewhere the user can find it later rather than into a dialog they dismiss and
forget. This is that somewhere.

Problems carry a short diagnostic identifier. It exists so a user reporting
something has a token the logs can be searched by; without one, "the upload
failed" is unmatchable against a log file. Informational notices get none, since
there is nothing to investigate and an identifier would only be noise.

The log is bounded. A window left open for days would otherwise accumulate every
notice it ever showed.
"""

import secrets
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Final

from PySide6.QtCore import QObject, Signal

__all__ = ["Level", "Notification", "NotificationLog"]

#: Long enough to be unambiguous in a log, short enough to read aloud.
_ID_BYTES: Final = 4

#: How many notices to keep before discarding the oldest.
_DEFAULT_LIMIT: Final = 200


class Level(StrEnum):
    """How much attention a notice deserves."""

    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


@dataclass(frozen=True, slots=True)
class Notification:
    """One thing the application reported.

    Attributes:
        level: Severity.
        message: What to show the user, already phrased for them.
        diagnostic_id: Token to quote when reporting the problem. Empty for
            informational notices.
        at: When it was raised, in UTC.
    """

    level: Level
    message: str
    diagnostic_id: str = ""
    at: datetime = field(default_factory=lambda: datetime.now(UTC))

    @property
    def copyable(self) -> str:
        """The notice as text worth pasting into a bug report."""
        stamp = self.at.isoformat(timespec="seconds")
        if not self.diagnostic_id:
            return f"[{stamp}] {self.level.value}: {self.message}"
        return f"[{stamp}] {self.level.value} ({self.diagnostic_id}): {self.message}"


class NotificationLog(QObject):
    """Collects notices and announces each one as it arrives."""

    #: Emitted with the new notice.
    added = Signal(object)

    def __init__(self, *, limit: int = _DEFAULT_LIMIT) -> None:
        """Keep at most `limit` notices, newest first."""
        super().__init__()
        self._entries: list[Notification] = []
        self._limit = limit
        self._unread = 0

    @property
    def entries(self) -> list[Notification]:
        """Every notice held, newest first."""
        return list(self._entries)

    @property
    def unread_problems(self) -> int:
        """How many warnings and errors have arrived since they were last read."""
        return self._unread

    def info(self, message: str) -> Notification:
        """Record something that went as intended."""
        return self._add(Level.INFO, message)

    def warning(self, message: str) -> Notification:
        """Record something the user should know about but need not act on."""
        return self._add(Level.WARNING, message)

    def error(self, message: str) -> Notification:
        """Record a failure, with an identifier for chasing it up."""
        return self._add(Level.ERROR, message)

    def mark_read(self) -> None:
        """Forget the unread count, having shown the user the log."""
        self._unread = 0

    def clear(self) -> None:
        """Discard every notice."""
        self._entries.clear()
        self._unread = 0

    def _add(self, level: Level, message: str) -> Notification:
        """Record a notice and announce it."""
        notice = Notification(
            level=level,
            message=message,
            diagnostic_id="" if level is Level.INFO else secrets.token_hex(_ID_BYTES),
        )
        self._entries.insert(0, notice)
        del self._entries[self._limit :]
        if level is not Level.INFO:
            self._unread += 1
        self.added.emit(notice)
        return notice
