"""Snapshots are the local safety net under the vault file."""

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from disbox.core.snapshots import SnapshotPolicy, SnapshotStore, parse_snapshot_name
from disbox.core.vault import Vault
from tests.unit.test_vault import KEYS


@pytest.fixture
def vault(tmp_path: Path) -> Vault:
    return Vault.create(tmp_path / "test.dbx", KEYS)


@pytest.fixture
def store(tmp_path: Path) -> SnapshotStore:
    return SnapshotStore(tmp_path / "snapshots")


def touch_snapshot(store: SnapshotStore, when: datetime) -> Path:
    """Create a placeholder snapshot file stamped `when`, for retention tests."""
    store.directory.mkdir(parents=True, exist_ok=True)
    path = store.directory / store.filename_for(when)
    path.write_bytes(b"placeholder")
    return path


class TestNaming:
    def test_filename_has_no_characters_windows_forbids(self, store: SnapshotStore) -> None:
        name = store.filename_for(datetime(2026, 8, 7, 19, 37, 50, 518341, tzinfo=UTC))
        assert not set(name) & set(':<>"|?*\\/'), f"{name!r} is not a legal Windows filename"

    def test_name_round_trips_through_the_parser(self, store: SnapshotStore) -> None:
        when = datetime(2026, 8, 7, 19, 37, 50, 518341, tzinfo=UTC)
        assert parse_snapshot_name(store.filename_for(when)) == when

    def test_unrelated_files_are_not_parsed_as_snapshots(self) -> None:
        assert parse_snapshot_name("vault.dbx") is None
        assert parse_snapshot_name("notes.txt") is None

    def test_names_are_distinct_within_the_same_second(self, store: SnapshotStore) -> None:
        base = datetime(2026, 8, 7, 19, 37, 50, tzinfo=UTC)
        first = store.filename_for(base)
        second = store.filename_for(base.replace(microsecond=1))
        assert first != second, "sub-second precision is required or rapid snapshots collide"


class TestTake:
    def test_snapshot_is_a_usable_vault(self, vault: Vault, store: SnapshotStore) -> None:
        original_id = vault.vault_id
        snapshot = store.take(vault)
        vault.close()

        with Vault.open(snapshot.path) as restored:
            assert restored.vault_id == original_id

    def test_snapshot_captures_data_written_before_it(
        self, vault: Vault, store: SnapshotStore
    ) -> None:
        with vault.connection as conn:
            conn.execute(
                "INSERT INTO nodes (id, parent_id, name, kind, created_at, modified_at) "
                "VALUES (X'01', NULL, 'before.txt', 'file', '2026-01-01', '2026-01-01')"
            )
        snapshot = store.take(vault)
        vault.close()

        with Vault.open(snapshot.path) as restored:
            names = restored.connection.execute("SELECT name FROM nodes").fetchall()
        assert [row[0] for row in names] == ["before.txt"]

    def test_source_vault_stays_usable_after_a_snapshot(
        self, vault: Vault, store: SnapshotStore
    ) -> None:
        store.take(vault)
        with vault.connection as conn:
            conn.execute(
                "INSERT INTO nodes (id, parent_id, name, kind, created_at, modified_at) "
                "VALUES (X'02', NULL, 'after.txt', 'file', '2026-01-01', '2026-01-01')"
            )
        assert vault.connection.execute("SELECT count(*) FROM nodes").fetchone()[0] == 1
        vault.close()

    def test_no_partial_file_is_left_behind(self, vault: Vault, store: SnapshotStore) -> None:
        """Snapshots land atomically, so a crash cannot leave a truncated one."""
        store.take(vault)
        vault.close()
        leftovers = [p.name for p in store.directory.iterdir() if p.suffix == ".tmp"]
        assert leftovers == []


class TestRetention:
    def test_recent_snapshots_are_all_kept(self, tmp_path: Path) -> None:
        store = SnapshotStore(
            tmp_path / "snaps", SnapshotPolicy(keep_recent=15, keep_daily_days=30)
        )
        now = datetime(2026, 8, 7, 12, 0, tzinfo=UTC)
        for minutes in range(20):
            touch_snapshot(store, now - timedelta(minutes=minutes))

        store.prune(now=now)
        assert len(store.snapshots()) >= 15

    def test_one_snapshot_survives_per_recent_day(self, tmp_path: Path) -> None:
        store = SnapshotStore(tmp_path / "snaps", SnapshotPolicy(keep_recent=2, keep_daily_days=30))
        now = datetime(2026, 8, 7, 12, 0, tzinfo=UTC)
        for day in range(5):
            for hour in (9, 13, 17):
                touch_snapshot(store, (now - timedelta(days=day)).replace(hour=hour))

        store.prune(now=now)
        kept_days = {snap.taken_at.date() for snap in store.snapshots()}
        assert len(kept_days) == 5, "each of the last five days must retain a snapshot"

    def test_snapshots_beyond_the_daily_window_are_removed(self, tmp_path: Path) -> None:
        store = SnapshotStore(tmp_path / "snaps", SnapshotPolicy(keep_recent=1, keep_daily_days=30))
        now = datetime(2026, 8, 7, 12, 0, tzinfo=UTC)
        ancient = touch_snapshot(store, now - timedelta(days=200))
        touch_snapshot(store, now)

        removed = store.prune(now=now)
        assert ancient.name in {p.name for p in removed}
        assert not ancient.exists()

    def test_prune_on_an_empty_directory_is_a_no_op(self, store: SnapshotStore) -> None:
        assert store.prune() == []

    def test_list_is_newest_first(self, tmp_path: Path) -> None:
        store = SnapshotStore(tmp_path / "snaps")
        now = datetime(2026, 8, 7, 12, 0, tzinfo=UTC)
        for day in (3, 1, 2):
            touch_snapshot(store, now - timedelta(days=day))

        taken = [snap.taken_at for snap in store.snapshots()]
        assert taken == sorted(taken, reverse=True)

    def test_foreign_files_are_never_deleted(self, tmp_path: Path) -> None:
        store = SnapshotStore(tmp_path / "snaps", SnapshotPolicy(keep_recent=0, keep_daily_days=0))
        store.directory.mkdir(parents=True)
        bystander = store.directory / "important-notes.txt"
        bystander.write_text("do not delete", encoding="utf-8")

        store.prune(now=datetime(2026, 8, 7, tzinfo=UTC))
        assert bystander.exists(), "pruning must only ever touch files it recognises"
