"""Run asyncio work off the GUI thread and report results back onto it.

Transfers are asynchronous and can run for minutes; Qt's event loop must keep
painting throughout. The two loops cannot share a thread, so an asyncio loop
runs on a `QThread` and everything crossing back does so as a Qt signal.

The rule this module exists to enforce: **the worker never touches a Qt widget.**
Signals are the only channel, and because they are emitted across a thread
boundary Qt queues them and runs the slots on the receiving object's thread --
the GUI thread. Calling into a widget from the coroutine instead would work
almost always and crash unpredictably, which is worse than failing outright.

Coroutines receive their `AsyncTask` so they can report progress without
capturing anything else, and cooperative cancellation is `asyncio.Task.cancel`
rather than a flag the coroutine has to remember to check.

`submit` guarantees the coroutine has not started when it returns, so
connections made straight after it cannot miss the outcome. Without that,
anything short enough to finish first would report into no listeners and look
like it silently did nothing -- which is precisely how fast tasks failed before
the guarantee existed.
"""

import asyncio
import concurrent.futures
import contextlib
import threading
from collections.abc import Callable, Coroutine
from typing import Any

import structlog
from PySide6.QtCore import QObject, QThread, QTimer, Signal

__all__ = ["AsyncBridge", "AsyncTask"]

logger = structlog.get_logger(__name__)

#: How long to wait for the loop thread to unwind before giving up on it.
_SHUTDOWN_TIMEOUT_MS = 3000

type Work = Callable[["AsyncTask"], Coroutine[Any, Any, Any]]


class AsyncTask(QObject):
    """One unit of asynchronous work, and the signals reporting on it.

    Lives on the GUI thread. The coroutine runs elsewhere and only ever emits
    these signals, so every connected slot runs on the GUI thread.
    """

    #: Emitted with the coroutine's return value.
    finished = Signal(object)
    #: Emitted with a human-readable message. Never both this and `finished`.
    failed = Signal(str)
    #: Emitted with (completed, total). Totals may change as work is discovered.
    progress = Signal(int, int)
    #: Emitted once when cancellation has actually taken effect.
    cancelled = Signal()

    def __init__(self) -> None:
        """Create a task that is not yet attached to a running coroutine."""
        super().__init__()
        self._future: concurrent.futures.Future[Any] | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._cancel_requested = False

    def report_progress(self, completed: int, total: int) -> None:
        """Report progress from inside the coroutine.

        Safe to call from the worker thread: emitting a signal across threads is
        queued by Qt, which is exactly the handoff this class is for.
        """
        self.progress.emit(completed, total)

    def cancel(self) -> None:
        """Ask the coroutine to stop.

        Cancellation is cooperative and takes effect at the coroutine's next
        suspension point, so `cancelled` may arrive some time after this
        returns. A task that has already finished ignores this.
        """
        # Cancelling during the deferral window, before a future exists, must
        # still count -- otherwise the work starts anyway and the caller's
        # cancel is silently ignored.
        self._cancel_requested = True
        if self._future is None or self._future.done():
            return
        # run_coroutine_threadsafe hands back a concurrent Future whose cancel
        # is already thread-safe and propagates into the asyncio task, so this
        # needs no marshalling of its own.
        self._future.cancel()


class _LoopThread(QThread):
    """A QThread whose whole job is to host an asyncio event loop."""

    def __init__(self) -> None:
        """Prepare the thread. The loop exists only once `run` is executing."""
        super().__init__()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._ready = threading.Event()

    def run(self) -> None:
        """Own an event loop for the lifetime of the thread."""
        loop = asyncio.new_event_loop()
        self._loop = loop
        asyncio.set_event_loop(loop)
        self._ready.set()
        try:
            loop.run_forever()
        finally:
            # Abandon whatever is still in flight rather than waiting for it:
            # a transfer may have minutes left, and shutdown cannot block on it.
            for pending in asyncio.all_tasks(loop):
                pending.cancel()
            with contextlib.suppress(RuntimeError):
                loop.run_until_complete(loop.shutdown_asyncgens())
            asyncio.set_event_loop(None)
            loop.close()

    def wait_until_ready(self, timeout: float = 5.0) -> asyncio.AbstractEventLoop:
        """Block until the loop exists, and return it.

        Raises:
            RuntimeError: If the thread did not start a loop in time.
        """
        if not self._ready.wait(timeout) or self._loop is None:
            raise RuntimeError("asyncio loop thread failed to start")
        return self._loop


class AsyncBridge(QObject):
    """Submits coroutines to a background asyncio loop.

    Start it once for the application and stop it on shutdown. Submitted work
    receives an `AsyncTask` to report through.
    """

    def __init__(self) -> None:
        """Create a bridge with no thread running yet."""
        super().__init__()
        self._thread: _LoopThread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        # The bridge owns tasks in flight. A caller that does not keep the
        # returned object would otherwise have it collected mid-transfer,
        # destroying the QObject and losing the outcome with no error anywhere.
        self._active: set[AsyncTask] = set()

    @property
    def is_running(self) -> bool:
        """Whether work can currently be submitted."""
        return self._loop is not None and not self._loop.is_closed()

    def start(self) -> None:
        """Start the loop thread. Starting an already-started bridge does nothing."""
        if self.is_running:
            return
        self._thread = _LoopThread()
        self._thread.start()
        self._loop = self._thread.wait_until_ready()
        logger.debug("async bridge started")

    def submit(self, work: Work) -> AsyncTask:
        """Run `work` on the loop thread.

        Args:
            work: Called with the task and returning a coroutine. Taking a
                factory rather than a coroutine keeps creation on the loop's
                side of the boundary, so the coroutine is never left
                un-awaited if submission fails.

        Returns:
            A task whose signals report the outcome, on the GUI thread. The
            coroutine has not started yet, so connecting to the returned task
            immediately cannot miss anything it reports.

        Raises:
            RuntimeError: If the bridge is not running.
        """
        if self._loop is None or not self.is_running:
            raise RuntimeError("async bridge is not running")

        task = AsyncTask()
        task._loop = self._loop
        self._active.add(task)
        for signal in (task.finished, task.failed, task.cancelled):
            signal.connect(lambda *_, ref=task: self._active.discard(ref))
        # Deferred by one GUI event-loop turn so the caller can connect first.
        QTimer.singleShot(0, lambda: self._schedule(task, work))
        return task

    def _schedule(self, task: AsyncTask, work: Work) -> None:
        """Hand `work` to the loop, unless it was cancelled or we have stopped."""
        if self._loop is None or not self.is_running:
            return
        if task._cancel_requested:
            task.cancelled.emit()
            return
        task._future = asyncio.run_coroutine_threadsafe(self._run(task, work), self._loop)

    @staticmethod
    async def _run(task: AsyncTask, work: Work) -> None:
        """Await `work` and turn its outcome into exactly one signal."""
        try:
            result = await work(task)
        except asyncio.CancelledError:
            task.cancelled.emit()
            raise  # never swallow cancellation; the loop needs to see it
        except Exception as exc:
            # A failure in one task must not reach the loop's exception handler
            # and take the bridge down with it.
            logger.debug("async task failed", error=str(exc))
            task.failed.emit(f"{exc}")
        else:
            task.finished.emit(result)

    def stop(self) -> None:
        """Stop the loop and join the thread. Safe to call more than once."""
        thread, loop = self._thread, self._loop
        self._thread, self._loop = None, None
        if thread is None or loop is None:
            return

        loop.call_soon_threadsafe(loop.stop)
        if not thread.wait(_SHUTDOWN_TIMEOUT_MS):
            logger.warning("async loop thread did not exit; terminating")
            thread.terminate()
            thread.wait()
        logger.debug("async bridge stopped")
