"""Uploading and downloading from the main window, through the async bridge."""

import random
from collections.abc import Iterator
from pathlib import Path

import pytest
from pytestqt.qtbot import QtBot

from disbox.backends.local import LocalBackend
from disbox.core.chunker import ChunkSpec
from disbox.core.crypto import KdfParams
from disbox.core.engine import TransferEngine
from disbox.core.filesystem import FileSystem
from disbox.core.vault import Vault
from disbox.gui.bridge import AsyncBridge
from disbox.gui.views.main_window import MainWindow
from tests.unit.test_gui_window import visible_names

PASSPHRASE = "gui transfer passphrase"  # noqa: S105 - fixture, not a credential
FAST = KdfParams(time_cost=1, memory_cost=8, parallelism=1)
SPEC = ChunkSpec(min_size=256, avg_size=1024, max_size=4096)


@pytest.fixture
def vault(tmp_path: Path) -> Iterator[Vault]:
    with Vault.create_encrypted(tmp_path / "demo.dbx", PASSPHRASE, FAST) as vault:
        yield vault


@pytest.fixture
def bridge() -> Iterator[AsyncBridge]:
    bridge = AsyncBridge()
    bridge.start()
    yield bridge
    bridge.stop()


@pytest.fixture
def window(qtbot: QtBot, vault: Vault, bridge: AsyncBridge, tmp_path: Path) -> MainWindow:
    engine = TransferEngine(
        vault, LocalBackend(tmp_path / "blobs"), vault.unlock(PASSPHRASE), spec=SPEC
    )
    window = MainWindow(vault, bridge=bridge, engine=engine)
    qtbot.addWidget(window)
    return window


def test_upload_adds_the_file_to_the_listing(
    qtbot: QtBot, window: MainWindow, tmp_path: Path
) -> None:
    source = tmp_path / "notes.txt"
    source.write_bytes(b"x" * 5000)

    with qtbot.waitSignal(window.transfers_idle, timeout=15000):
        window.upload_files([source])

    assert visible_names(window) == ["notes.txt"]


def test_upload_reports_progress(qtbot: QtBot, window: MainWindow, tmp_path: Path) -> None:
    source = tmp_path / "big.bin"
    # Distinct bytes throughout: a file of one repeated byte dedupes to a
    # handful of chunks, so completed_bytes legitimately never reaches the
    # file's size and the progress assertion below would be testing dedup.
    source.write_bytes(random.Random(1).randbytes(200_000))  # noqa: S311 - test data
    seen: list[tuple[int, int]] = []
    window.transfer_progress.connect(lambda done, total: seen.append((done, total)))

    with qtbot.waitSignal(window.transfers_idle, timeout=15000):
        window.upload_files([source])

    assert seen, "no progress was reported"
    assert seen[-1][0] == seen[-1][1]


def test_uploaded_bytes_come_back_unchanged(
    qtbot: QtBot, window: MainWindow, tmp_path: Path
) -> None:
    payload = bytes(range(256)) * 40
    source = tmp_path / "round.bin"
    source.write_bytes(payload)
    with qtbot.waitSignal(window.transfers_idle, timeout=15000):
        window.upload_files([source])

    window._table.selectRow(0)
    destination = tmp_path / "out"
    destination.mkdir()
    with qtbot.waitSignal(window.transfers_idle, timeout=15000):
        window.download_selected(destination)

    assert (destination / "round.bin").read_bytes() == payload


def test_upload_into_the_open_directory(
    qtbot: QtBot, window: MainWindow, vault: Vault, tmp_path: Path
) -> None:
    folder = window.create_folder("Target")
    assert folder is not None
    window.navigate_to(folder)
    source = tmp_path / "inner.txt"
    source.write_bytes(b"z")

    with qtbot.waitSignal(window.transfers_idle, timeout=15000):
        window.upload_files([source])

    assert [n.name for n in FileSystem(vault).children(folder)] == ["inner.txt"]


def test_upload_of_several_files_reports_idle_once_at_the_end(
    qtbot: QtBot, window: MainWindow, tmp_path: Path
) -> None:
    sources = []
    for index in range(3):
        source = tmp_path / f"f{index}.txt"
        source.write_bytes(b"a" * 100)
        sources.append(source)

    with qtbot.waitSignal(window.transfers_idle, timeout=15000):
        window.upload_files(sources)

    assert sorted(visible_names(window)) == ["f0.txt", "f1.txt", "f2.txt"]


def test_a_missing_source_is_reported_and_does_not_stop_the_rest(
    qtbot: QtBot, window: MainWindow, tmp_path: Path
) -> None:
    good = tmp_path / "good.txt"
    good.write_bytes(b"ok")
    missing = tmp_path / "not-there.txt"

    with qtbot.waitSignal(window.transfers_idle, timeout=15000):
        window.upload_files([missing, good])

    assert "good.txt" in visible_names(window)
    assert "not-there.txt" not in visible_names(window)


def test_upload_without_an_engine_is_refused_cleanly(
    qtbot: QtBot, vault: Vault, tmp_path: Path
) -> None:
    """The window is usable read-only, so this reports rather than crashes."""
    window = MainWindow(vault)
    qtbot.addWidget(window)
    source = tmp_path / "x.txt"
    source.write_bytes(b"x")

    window.upload_files([source])

    assert "not configured" in window.status_text().lower()


def test_download_with_nothing_selected_does_nothing(window: MainWindow, tmp_path: Path) -> None:
    window.download_selected(tmp_path)

    assert not list(tmp_path.glob("*.txt"))


def test_upload_of_nothing_is_harmless(window: MainWindow) -> None:
    window.upload_files([])

    assert visible_names(window) == []
