"""The notification log: nothing blocks, and every problem is quotable."""

from pytestqt.qtbot import QtBot

from disbox.gui.notifications import Level, NotificationLog


def test_an_info_notice_is_recorded() -> None:
    log = NotificationLog()

    log.info("Uploaded 3 files")

    assert log.entries[0].message == "Uploaded 3 files"
    assert log.entries[0].level is Level.INFO


def test_the_newest_notice_comes_first() -> None:
    log = NotificationLog()

    log.info("first")
    log.info("second")

    assert [entry.message for entry in log.entries] == ["second", "first"]


def test_an_error_carries_a_diagnostic_id() -> None:
    """A user reporting a problem needs something the logs can be searched by."""
    log = NotificationLog()

    log.error("Upload failed")

    assert log.entries[0].diagnostic_id != ""
    assert len(log.entries[0].diagnostic_id) >= 6


def test_diagnostic_ids_are_distinct() -> None:
    log = NotificationLog()

    log.error("one")
    log.error("two")

    assert log.entries[0].diagnostic_id != log.entries[1].diagnostic_id


def test_an_info_notice_needs_no_diagnostic_id() -> None:
    """There is nothing to investigate, so an identifier would be noise."""
    log = NotificationLog()

    log.info("All done")

    assert log.entries[0].diagnostic_id == ""


def test_adding_a_notice_announces_it(qtbot: QtBot) -> None:
    log = NotificationLog()

    with qtbot.waitSignal(log.added, timeout=1000) as caught:
        log.warning("Disk nearly full")

    assert caught.args[0].message == "Disk nearly full"


def test_the_log_is_bounded() -> None:
    """An unbounded log is a slow leak in a long-running window."""
    log = NotificationLog(limit=5)

    for index in range(20):
        log.info(f"notice {index}")

    assert len(log.entries) == 5
    assert log.entries[0].message == "notice 19"


def test_unread_errors_are_counted() -> None:
    log = NotificationLog()

    log.error("bad")
    log.warning("hmm")
    log.info("fine")

    assert log.unread_problems == 2


def test_marking_read_clears_the_count() -> None:
    log = NotificationLog()
    log.error("bad")

    log.mark_read()

    assert log.unread_problems == 0


def test_copyable_text_includes_the_identifier() -> None:
    log = NotificationLog()
    log.error("Upload failed")

    text = log.entries[0].copyable

    assert "Upload failed" in text
    assert log.entries[0].diagnostic_id in text


def test_clearing_empties_the_log() -> None:
    log = NotificationLog()
    log.info("something")

    log.clear()

    assert log.entries == []
