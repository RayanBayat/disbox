"""Substring search over node names, backed by an FTS5 trigram index.

The trigram tokeniser is chosen over the default one because people look for
files by fragments -- typing ``port`` to find ``quarterly-report.pdf`` -- and a
word-based index cannot match inside a token. The cost is that queries shorter
than three characters cannot be answered from the index at all, so those fall
back to a scan; on a tree small enough for a one- or two-character query to be
useful, that is imperceptible.

User input is never interpolated into FTS5's query grammar. Terms like ``NOT``,
``OR``, ``*`` and ``col:value`` are operators there, so an unescaped filename
would either match the wrong thing or raise a syntax error. Every query is
wrapped as a single quoted phrase, which FTS5 treats literally.
"""

import sqlite3
import uuid
from dataclasses import dataclass
from typing import Final

__all__ = ["SearchHit", "search"]

# FTS5's trigram tokeniser cannot index fragments shorter than this.
_MIN_TRIGRAM_LENGTH: Final = 3


@dataclass(frozen=True, slots=True)
class SearchHit:
    """One matching node.

    Attributes:
        node_id: Identifier of the matching node.
        name: The node's name as stored.
        kind: Either ``dir`` or ``file``.
        deleted: Whether the node is currently in the trash.
    """

    node_id: uuid.UUID
    name: str
    kind: str
    deleted: bool


def _as_phrase(query: str) -> str:
    """Quote `query` so FTS5 treats it as literal text rather than syntax."""
    escaped = query.replace('"', '""')
    return f'"{escaped}"'


def search(
    conn: sqlite3.Connection,
    query: str,
    *,
    limit: int = 100,
    include_trashed: bool = False,
) -> list[SearchHit]:
    """Find nodes whose name contains `query`.

    Args:
        conn: Vault connection to search.
        query: Text to look for. Matched literally, case-insensitively, and
            anywhere within the name.
        limit: Maximum number of hits to return.
        include_trashed: Include nodes that are in the trash.

    Returns:
        Matching nodes, at most `limit` of them. Empty if `query` is blank.
    """
    needle = query.strip()
    if not needle:
        return []

    # The trash filter is bound as a parameter rather than concatenated, so no
    # part of either statement is ever built by string interpolation.
    if len(needle) >= _MIN_TRIGRAM_LENGTH:
        sql = (
            "SELECT n.id, n.name, n.kind, n.deleted_at FROM nodes_fts "
            "JOIN nodes AS n ON n.rowid = nodes_fts.rowid "
            "WHERE nodes_fts MATCH ? AND (? OR n.deleted_at IS NULL) LIMIT ?"
        )
        params: tuple[object, ...] = (_as_phrase(needle), include_trashed, limit)
    else:
        # Below the trigram floor the index cannot help; ESCAPE keeps a literal
        # % or _ in the query from behaving as a wildcard.
        escaped = needle.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        sql = (
            "SELECT n.id, n.name, n.kind, n.deleted_at FROM nodes AS n "
            "WHERE n.name LIKE ? ESCAPE '\\' AND (? OR n.deleted_at IS NULL) LIMIT ?"
        )
        params = (f"%{escaped}%", include_trashed, limit)

    return [
        SearchHit(
            node_id=uuid.UUID(bytes=row[0]),
            name=row[1],
            kind=row[2],
            deleted=row[3] is not None,
        )
        for row in conn.execute(sql, params).fetchall()
    ]
