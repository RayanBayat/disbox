"""Dragging vault files out to the file manager.

The naive approach downloads everything the moment a drag begins, which means a
user who picks up a 2 GB folder and changes their mind has still paid for it.
This defers instead: `QMimeData.retrieveData` is called when the *drop* target
asks for the data, so nothing is fetched until the drag is actually completed
somewhere that wants it.

That is not the same as Windows' `CFSTR_FILECONTENTS` deferred rendering, which
streams each file on demand through a COM interface and lets Explorer show its
own progress. This materialises whole files to a temporary directory at drop
time. The difference matters for very large files, where Explorer will appear to
pause while the data is fetched. Doing it properly means implementing
`IDataObject`, which is a considerably larger piece of work.
"""

import tempfile
import uuid
from collections.abc import Callable
from pathlib import Path

import structlog
from PySide6.QtCore import QMimeData, QUrl

__all__ = ["DeferredFileMimeData"]

logger = structlog.get_logger(__name__)

#: What the file manager asks for when it wants dropped files.
_URI_LIST = "text/uri-list"

type Materialiser = Callable[[list[uuid.UUID], Path], list[Path]]


class DeferredFileMimeData(QMimeData):
    """Mime data that writes its files only when they are asked for."""

    def __init__(self, nodes: list[uuid.UUID], materialise: Materialiser) -> None:
        """Carry `nodes`, fetching them through `materialise` on demand.

        Args:
            nodes: Vault nodes being dragged.
            materialise: Writes the given nodes into a directory and returns the
                paths written. Injected rather than reaching for the engine, so
                the deferral can be tested without a transfer.
        """
        super().__init__()
        self._nodes = nodes
        self._materialise = materialise
        self._paths: list[Path] | None = None

        # Declaring the format up front is what makes the drop target accept the
        # drag at all; the data behind it is produced later.
        self.setData(_URI_LIST, b"")

    @property
    def materialised(self) -> bool:
        """Whether the files have been written yet."""
        return self._paths is not None

    def formats(self) -> list[str]:
        """Advertise the URI list even before the files exist."""
        return [_URI_LIST]

    def hasUrls(self) -> bool:  # noqa: N802 - Qt override
        """Always true: this exists to carry files."""
        return True

    def urls(self) -> list[QUrl]:
        """The dropped files, writing them on first request."""
        return [QUrl.fromLocalFile(str(path)) for path in self._ensure()]

    def retrieveData(self, mime_type: str, preferred: object = None) -> object:  # noqa: N802
        """Produce the data Qt asks for, fetching the files if needed."""
        if mime_type != _URI_LIST:
            return super().retrieveData(mime_type, preferred)  # type: ignore[arg-type]

        body = "\r\n".join(str(QUrl.fromLocalFile(str(p)).toString()) for p in self._ensure())
        return body.encode("utf-8")

    def _ensure(self) -> list[Path]:
        """Write the files once, and reuse them afterwards.

        A drop target asks for the data more than once. Fetching again each time
        would download the same file repeatedly for a single drop.
        """
        if self._paths is None:
            destination = Path(tempfile.mkdtemp(prefix="disbox-drag-"))
            self._paths = self._materialise(self._nodes, destination)
            logger.debug("drag materialised", files=len(self._paths))
        return self._paths
