"""The behaviour every storage backend must exhibit.

Written once and run against each implementation. A backend that passes this is
substitutable in the transfer engine without the engine knowing which one it
has, which is the entire point of the abstraction -- and a defect caught here
against the local backend is a defect that would have shown up over the network
too, found in milliseconds rather than seconds.

Subclass `StorageBackendConformance`, provide a `backend` fixture, and inherit
the whole suite.
"""

import pytest

from disbox.backends.base import BlobRef, StorageBackend


class StorageBackendConformance:
    """Inherit this and supply a `backend` fixture."""

    @pytest.mark.asyncio
    async def test_put_then_get_returns_the_same_bytes(self, backend: StorageBackend) -> None:
        ref = await backend.put(b"payload", idempotency_key="k1")
        assert await backend.get(ref) == b"payload"

    @pytest.mark.asyncio
    async def test_stored_blob_reports_as_existing(self, backend: StorageBackend) -> None:
        ref = await backend.put(b"payload", idempotency_key="k1")
        assert await backend.exists(ref)

    @pytest.mark.asyncio
    async def test_a_missing_blob_does_not_exist(self, backend: StorageBackend) -> None:
        assert not await backend.exists(BlobRef(locator="never-stored"))

    @pytest.mark.asyncio
    async def test_repeating_a_put_does_not_store_twice(self, backend: StorageBackend) -> None:
        """A retry after an ambiguous failure must not leak a second copy."""
        first = await backend.put(b"payload", idempotency_key="same")
        second = await backend.put(b"payload", idempotency_key="same")
        assert first.locator == second.locator

        stored = [ref async for ref in backend.iter_all()]
        assert len(stored) == 1

    @pytest.mark.asyncio
    async def test_different_keys_store_separately(self, backend: StorageBackend) -> None:
        await backend.put(b"one", idempotency_key="a")
        await backend.put(b"two", idempotency_key="b")
        assert len([ref async for ref in backend.iter_all()]) == 2

    @pytest.mark.asyncio
    async def test_delete_removes_the_blob(self, backend: StorageBackend) -> None:
        ref = await backend.put(b"payload", idempotency_key="k1")
        await backend.delete(ref)
        assert not await backend.exists(ref)

    @pytest.mark.asyncio
    async def test_deleting_twice_is_not_an_error(self, backend: StorageBackend) -> None:
        """Garbage collection retries, so delete has to be idempotent."""
        ref = await backend.put(b"payload", idempotency_key="k1")
        await backend.delete(ref)
        await backend.delete(ref)

    @pytest.mark.asyncio
    async def test_deleting_something_absent_is_not_an_error(self, backend: StorageBackend) -> None:
        await backend.delete(BlobRef(locator="never-stored"))

    @pytest.mark.asyncio
    async def test_empty_payload_round_trips(self, backend: StorageBackend) -> None:
        ref = await backend.put(b"", idempotency_key="empty")
        assert await backend.get(ref) == b""

    @pytest.mark.asyncio
    async def test_binary_payload_round_trips(self, backend: StorageBackend) -> None:
        payload = bytes(range(256))
        ref = await backend.put(payload, idempotency_key="binary")
        assert await backend.get(ref) == payload

    @pytest.mark.asyncio
    async def test_range_request_returns_a_slice(self, backend: StorageBackend) -> None:
        ref = await backend.put(b"0123456789", idempotency_key="ranged")
        assert await backend.get(ref, byte_range=(2, 5)) == b"234"

    @pytest.mark.asyncio
    async def test_streaming_reassembles_the_blob(self, backend: StorageBackend) -> None:
        payload = bytes(range(256)) * 500
        ref = await backend.put(payload, idempotency_key="streamed")
        assert b"".join([block async for block in backend.stream(ref)]) == payload

    @pytest.mark.asyncio
    async def test_iter_all_enumerates_everything(self, backend: StorageBackend) -> None:
        """Rebuilding a lost vault depends on this seeing every blob."""
        for index in range(5):
            await backend.put(f"blob-{index}".encode(), idempotency_key=f"k{index}")
        assert len([ref async for ref in backend.iter_all()]) == 5

    @pytest.mark.asyncio
    async def test_iter_all_is_empty_on_a_fresh_backend(self, backend: StorageBackend) -> None:
        assert [ref async for ref in backend.iter_all()] == []

    @pytest.mark.asyncio
    async def test_max_blob_size_is_positive(self, backend: StorageBackend) -> None:
        assert backend.max_blob_size > 0

    @pytest.mark.asyncio
    async def test_probe_reports_a_usable_limit(self, backend: StorageBackend) -> None:
        assert await backend.probe() > 0

    @pytest.mark.asyncio
    async def test_oversized_payload_is_refused(self, backend: StorageBackend) -> None:
        """Better a clear error here than a provider rejection mid-upload."""
        with pytest.raises((ValueError, OSError)):
            await backend.put(b"x" * (backend.max_blob_size + 1), idempotency_key="toobig")

    @pytest.mark.asyncio
    async def test_it_satisfies_the_protocol(self, backend: StorageBackend) -> None:
        assert isinstance(backend, StorageBackend)
