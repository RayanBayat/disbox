"""The transfer dock: visible only while something is moving."""

from pytestqt.qtbot import QtBot

from disbox.gui.theme.tokens import DARK, LIGHT
from disbox.gui.views.transfer_dock import TransferDock


def make(qtbot: QtBot) -> TransferDock:
    dock = TransferDock(DARK)
    qtbot.addWidget(dock)
    return dock


def test_starts_hidden(qtbot: QtBot) -> None:
    assert not make(qtbot).isVisibleTo(None)


def test_begin_shows_what_is_happening(qtbot: QtBot) -> None:
    dock = make(qtbot)

    dock.begin("Uploading notes.txt")

    assert dock.label_text == "Uploading notes.txt"


def test_report_shows_both_byte_counts(qtbot: QtBot) -> None:
    dock = make(qtbot)
    dock.begin("Uploading big.bin")

    dock.report(512, 2048)

    assert "512" in dock.detail_text
    assert "2.0 KB" in dock.detail_text


def test_an_unknown_total_stays_indeterminate(qtbot: QtBot) -> None:
    """Better an indeterminate bar than a confident zero for unmeasured work."""
    dock = make(qtbot)
    dock.begin("Uploading")

    dock.report(0, 0)

    assert dock._bar.maximum() == 0
    assert dock.detail_text == ""


def test_end_hides_the_dock_again(qtbot: QtBot) -> None:
    dock = make(qtbot)
    dock.begin("Uploading")
    dock.report(1, 2)

    dock.end()

    assert not dock.isVisibleTo(None)
    assert dock.label_text == ""


def test_cancel_button_asks_to_cancel(qtbot: QtBot) -> None:
    dock = make(qtbot)
    dock.begin("Uploading")

    with qtbot.waitSignal(dock.cancel_requested, timeout=1000):
        dock._cancel.click()


def test_progress_tracks_the_reported_total(qtbot: QtBot) -> None:
    dock = make(qtbot)
    dock.begin("Uploading")

    dock.report(750, 1000)

    assert dock._bar.maximum() == 1000
    assert dock._bar.value() == 750


def test_palette_change_is_accepted(qtbot: QtBot) -> None:
    dock = make(qtbot)

    dock.set_palette(LIGHT)

    assert not dock._cancel.icon().isNull()
