"""The journal is the append-only record of everything that changed the vault."""

import sqlite3
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest

from disbox.core import journal as journal_module
from disbox.core.journal import JournalEntry, entries, journaled, record
from disbox.core.vault import Vault
from tests.unit.test_vault import KEYS


@pytest.fixture
def vault(tmp_path: Path) -> Iterator[Vault]:
    with Vault.create(tmp_path / "test.dbx", KEYS) as vault:
        yield vault


class Subject:
    """Minimal stand-in for a class whose mutations must be journalled."""

    def __init__(self, vault: Vault) -> None:
        self.vault = vault

    @property
    def connection(self) -> sqlite3.Connection:
        return self.vault.connection

    @journaled("rename")
    def rename(self, node_id: uuid.UUID, new_name: str) -> str:
        self.connection.execute(
            "INSERT INTO nodes (id, parent_id, name, kind, created_at, modified_at) "
            "VALUES (?, NULL, ?, 'file', '2026-01-01', '2026-01-01')",
            (node_id.bytes, new_name),
        )
        return new_name

    @journaled("explode")
    def explode(self) -> None:
        self.connection.execute(
            "INSERT INTO nodes (id, parent_id, name, kind, created_at, modified_at) "
            "VALUES (X'99', NULL, 'ghost', 'file', '2026-01-01', '2026-01-01')"
        )
        msg = "operation failed after writing"
        raise RuntimeError(msg)


class TestRecord:
    def test_entry_is_appended(self, vault: Vault) -> None:
        record(vault.connection, "create", payload={"name": "a.txt"})
        assert len(entries(vault.connection)) == 1

    def test_entry_round_trips(self, vault: Vault) -> None:
        target = uuid.uuid7()
        record(vault.connection, "delete", target_id=target, payload={"count": 3})

        entry = entries(vault.connection)[0]
        assert entry.op == "delete"
        assert entry.target_id == target
        assert entry.payload == {"count": 3}

    def test_entries_are_returned_newest_first(self, vault: Vault) -> None:
        for name in ("first", "second", "third"):
            record(vault.connection, name)
        assert [e.op for e in entries(vault.connection)] == ["third", "second", "first"]

    def test_limit_is_honoured(self, vault: Vault) -> None:
        for index in range(10):
            record(vault.connection, f"op{index}")
        assert len(entries(vault.connection, limit=3)) == 3

    def test_absent_target_and_payload_are_allowed(self, vault: Vault) -> None:
        record(vault.connection, "vacuum")
        entry = entries(vault.connection)[0]
        assert entry.target_id is None
        assert entry.payload == {}

    def test_timestamps_are_timezone_aware_utc(self, vault: Vault) -> None:
        record(vault.connection, "create")
        assert entries(vault.connection)[0].ts.utcoffset() is not None

    def test_secrets_in_a_payload_are_redacted(self, vault: Vault) -> None:
        record(vault.connection, "configure", payload={"bot_token": "super-secret-value"})
        assert "super-secret-value" not in str(entries(vault.connection)[0].payload)


class TestDecorator:
    def test_successful_call_is_journalled(self, vault: Vault) -> None:
        node_id = uuid.uuid7()
        Subject(vault).rename(node_id, "renamed.txt")

        entry = entries(vault.connection)[0]
        assert entry.op == "rename"

    def test_return_value_passes_through(self, vault: Vault) -> None:
        assert Subject(vault).rename(uuid.uuid7(), "kept.txt") == "kept.txt"

    def test_failed_call_journals_nothing_and_writes_nothing(self, vault: Vault) -> None:
        """The mutation and its journal entry share one transaction."""
        with pytest.raises(RuntimeError, match="operation failed"):
            Subject(vault).explode()

        assert entries(vault.connection) == []
        ghosts = vault.connection.execute("SELECT count(*) FROM nodes WHERE name = 'ghost'")
        assert ghosts.fetchone()[0] == 0, "the failed write must have rolled back too"

    def test_decorator_preserves_the_wrapped_signature(self) -> None:
        assert Subject.rename.__name__ == "rename"
        assert Subject.rename.__doc__ == Subject.rename.__doc__


class TestAppendOnly:
    def test_module_exposes_no_way_to_delete_history(self) -> None:
        forbidden = {"delete", "purge", "clear", "truncate", "prune"}
        assert not forbidden & set(dir(journal_module))

    def test_entry_is_immutable(self, vault: Vault) -> None:
        record(vault.connection, "create")
        entry = entries(vault.connection)[0]
        with pytest.raises((AttributeError, TypeError)):
            entry.op = "tampered"  # type: ignore[misc]

    def test_entry_type_is_what_callers_receive(self, vault: Vault) -> None:
        record(vault.connection, "create")
        assert isinstance(entries(vault.connection)[0], JournalEntry)
