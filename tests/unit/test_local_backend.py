"""LocalBackend against the shared conformance suite, plus its own specifics."""

from collections.abc import Iterator
from pathlib import Path

import pytest

from disbox.backends.local import LocalBackend
from tests.conformance import StorageBackendConformance


@pytest.fixture
def backend(tmp_path: Path) -> Iterator[LocalBackend]:
    yield LocalBackend(tmp_path / "blobs")


class TestLocalBackendConformance(StorageBackendConformance):
    """Every backend behaviour, applied to the local implementation."""


class TestLocalSpecifics:
    @pytest.mark.asyncio
    async def test_no_partial_file_survives_a_write(self, backend: LocalBackend) -> None:
        """Blobs land atomically, so a crash cannot leave a truncated one."""
        await backend.put(b"payload", idempotency_key="k")
        assert not list(backend._root.glob("*.partial"))

    @pytest.mark.asyncio
    async def test_a_damaged_index_costs_a_duplicate_not_correctness(self, tmp_path: Path) -> None:
        root = tmp_path / "blobs"
        first = LocalBackend(root)
        await first.put(b"payload", idempotency_key="k")
        (root / "idempotency.json").write_text("not json at all", encoding="utf-8")

        reopened = LocalBackend(root)
        ref = await reopened.put(b"payload", idempotency_key="k")
        assert await reopened.get(ref) == b"payload"
