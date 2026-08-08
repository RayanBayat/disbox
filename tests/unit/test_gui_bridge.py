"""The Qt/asyncio bridge: run coroutines off the GUI thread, report back on it."""

import asyncio
from collections.abc import Iterator

import pytest
from PySide6.QtCore import QThread
from pytestqt.qtbot import QtBot

from disbox.gui.bridge import AsyncBridge, AsyncTask


@pytest.fixture
def bridge() -> Iterator[AsyncBridge]:
    bridge = AsyncBridge()
    bridge.start()
    yield bridge
    bridge.stop()


def test_result_is_delivered_to_the_caller(qtbot: QtBot, bridge: AsyncBridge) -> None:
    async def work(_: AsyncTask) -> str:
        return "done"

    task = bridge.submit(work)
    with qtbot.waitSignal(task.finished, timeout=5000) as caught:
        pass

    assert caught.args == ["done"]


def test_coroutine_runs_off_the_gui_thread(qtbot: QtBot, bridge: AsyncBridge) -> None:
    seen: list[QThread] = []

    async def work(_: AsyncTask) -> None:
        seen.append(QThread.currentThread())

    task = bridge.submit(work)
    with qtbot.waitSignal(task.finished, timeout=5000):
        pass

    assert seen[0] is not QThread.currentThread()


def test_signals_arrive_on_the_gui_thread(qtbot: QtBot, bridge: AsyncBridge) -> None:
    """The whole point of the bridge: no Qt object is touched from the worker."""
    seen: list[QThread] = []

    async def work(_: AsyncTask) -> None:
        return None

    task = bridge.submit(work)
    task.finished.connect(lambda _: seen.append(QThread.currentThread()))
    with qtbot.waitSignal(task.finished, timeout=5000):
        pass

    assert seen == [QThread.currentThread()]


def test_failure_reports_the_message_and_not_a_result(qtbot: QtBot, bridge: AsyncBridge) -> None:
    async def work(_: AsyncTask) -> None:
        raise RuntimeError("backend refused")

    task = bridge.submit(work)
    with qtbot.waitSignal(task.failed, timeout=5000) as caught:
        pass

    assert "backend refused" in caught.args[0]


def test_progress_is_forwarded(qtbot: QtBot, bridge: AsyncBridge) -> None:
    async def work(task: AsyncTask) -> None:
        task.report_progress(3, 10)

    task = bridge.submit(work)
    with qtbot.waitSignal(task.progress, timeout=5000) as caught:
        pass

    assert caught.args == [3, 10]


def test_cancel_stops_the_work(qtbot: QtBot, bridge: AsyncBridge) -> None:
    started = asyncio.Event()

    async def work(_: AsyncTask) -> None:
        started.set()
        await asyncio.sleep(30)

    task = bridge.submit(work)
    qtbot.waitUntil(started.is_set, timeout=5000)

    with qtbot.waitSignal(task.cancelled, timeout=5000):
        task.cancel()


def test_a_failing_task_does_not_take_the_loop_down(qtbot: QtBot, bridge: AsyncBridge) -> None:
    async def boom(_: AsyncTask) -> None:
        raise ValueError("first")

    async def fine(_: AsyncTask) -> str:
        return "second"

    with qtbot.waitSignal(bridge.submit(boom).failed, timeout=5000):
        pass
    with qtbot.waitSignal(bridge.submit(fine).finished, timeout=5000) as caught:
        pass

    assert caught.args == ["second"]


def test_stop_is_idempotent() -> None:
    bridge = AsyncBridge()
    bridge.start()
    bridge.stop()
    bridge.stop()

    assert not bridge.is_running


def test_submitting_after_stop_is_refused() -> None:
    bridge = AsyncBridge()
    bridge.start()
    bridge.stop()

    async def work(_: AsyncTask) -> None:
        return None

    with pytest.raises(RuntimeError, match="not running"):
        bridge.submit(work)


def test_stop_abandons_work_still_in_flight(qtbot: QtBot) -> None:
    """Shutdown must not block on a transfer that could take minutes."""
    bridge = AsyncBridge()
    bridge.start()
    started = asyncio.Event()

    async def work(_: AsyncTask) -> None:
        started.set()
        await asyncio.sleep(60)

    bridge.submit(work)
    qtbot.waitUntil(started.is_set, timeout=5000)

    bridge.stop()

    assert not bridge.is_running


def test_many_tasks_all_report(qtbot: QtBot, bridge: AsyncBridge) -> None:
    async def work(_: AsyncTask) -> int:
        await asyncio.sleep(0)
        return 1

    # Connected at submit time, which is the supported pattern: waiting on each
    # task in turn would miss the ones that finished during an earlier wait.
    done: list[int] = []
    for _ in range(25):
        bridge.submit(work).finished.connect(done.append)

    qtbot.waitUntil(lambda: len(done) == 25, timeout=5000)

    assert done == [1] * 25
