"""The Discord backend, against a mocked API.

No test here touches the network. The conformance suite is driven by a fake
that mimics the parts of Discord's behaviour the backend depends on, so the
implementation can be finished and trusted before anyone supplies a token.
"""

import re
from collections.abc import Iterator
from typing import Any

import httpx
import pytest

from disbox.backends.base import BlobRef
from disbox.backends.discord import DiscordBackend, RateLimitedError
from disbox.errors import BackendError
from tests.conformance import StorageBackendConformance

TOKEN = "MTA5.GaBcDe.FakeTokenForTests"  # noqa: S105 - fixture, not a credential
CHANNEL = 1094567812345678900


class FakeDiscord:
    """A minimal stand-in for one Discord channel."""

    def __init__(self) -> None:
        self.messages: list[dict[str, Any]] = []
        # Bodies live beside the messages: the message dict is serialised as
        # JSON, and raw bytes cannot go through that.
        self.bodies: dict[str, bytes] = {}
        self._next_id = 1000
        self.rate_limit_once = False

    @staticmethod
    def _apply_range(request: httpx.Request, body: bytes) -> httpx.Response:
        """Honour a Range header the way a CDN would."""
        header = request.headers.get("Range")
        if header is None:
            return httpx.Response(200, content=body)
        start, _, end = header.removeprefix("bytes=").partition("-")
        stop = int(end) + 1 if end else len(body)
        return httpx.Response(206, content=body[int(start) : stop])

    def handler(self, request: httpx.Request) -> httpx.Response:
        """Route a request to the matching behaviour."""
        path = request.url.path
        if self.rate_limit_once:
            self.rate_limit_once = False
            return httpx.Response(429, headers={"Retry-After": "0"}, json={})

        if "cdn" in request.url.host:
            return self._download(request)
        if request.method == "POST":
            return self._create(request)
        if request.method == "DELETE":
            return self._delete(path)
        if re.search(r"/messages/[^/]+$", path):
            return self._one(path)
        return self._list(request)

    def _create(self, request: httpx.Request) -> httpx.Response:
        content = request.content
        marker = b'filename="'
        start = content.index(marker) + len(marker)
        filename = content[start : content.index(b'"', start)].decode()

        body = content.split(b"\r\n\r\n", 1)[1].rsplit(b"\r\n--", 1)[0]
        self._next_id += 1
        message = {
            "id": str(self._next_id),
            "attachments": [
                {
                    "id": f"a{self._next_id}",
                    "filename": filename,
                    "size": len(body),
                    "url": f"https://cdn.discordapp.com/attachments/{self._next_id}?ex=deadbeef",
                }
            ],
        }
        self.bodies[str(self._next_id)] = body
        self.messages.append(message)
        return httpx.Response(200, json=message)

    def _find(self, message_id: str) -> dict[str, Any] | None:
        return next((m for m in self.messages if m["id"] == message_id), None)

    def _one(self, path: str) -> httpx.Response:
        message = self._find(path.rsplit("/", 1)[1])
        return httpx.Response(200, json=message) if message else httpx.Response(404, json={})

    def _list(self, request: httpx.Request) -> httpx.Response:
        before = request.url.params.get("before")
        ordered = list(reversed(self.messages))
        if before is not None:
            index = next((i for i, m in enumerate(ordered) if m["id"] == before), -1)
            ordered = ordered[index + 1 :]
        return httpx.Response(200, json=ordered[: int(request.url.params.get("limit", 100))])

    def _delete(self, path: str) -> httpx.Response:
        message = self._find(path.rsplit("/", 1)[1])
        if message is None:
            return httpx.Response(404, json={})
        self.messages.remove(message)
        self.bodies.pop(message["id"], None)
        return httpx.Response(204)

    def _download(self, request: httpx.Request) -> httpx.Response:
        body = self.bodies.get(request.url.path.rsplit("/", 1)[1])
        if body is None:
            return httpx.Response(404)
        return self._apply_range(request, body)


@pytest.fixture
def api() -> FakeDiscord:
    return FakeDiscord()


@pytest.fixture
def backend(api: FakeDiscord) -> Iterator[DiscordBackend]:
    transport = httpx.MockTransport(api.handler)
    client = httpx.AsyncClient(
        base_url="https://discord.com/api/v10",
        headers={"Authorization": f"Bot {TOKEN}"},
        transport=transport,
    )
    yield DiscordBackend(TOKEN, CHANNEL, client=client, max_blob_size=64 * 1024)


class TestDiscordConformance(StorageBackendConformance):
    """Every backend behaviour, applied to the Discord implementation."""


class TestDiscordSpecifics:
    async def test_urls_are_never_persisted_in_the_reference(self, backend: DiscordBackend) -> None:
        """Signed CDN links expire; storing one produces a rotting reference."""
        ref = await backend.put(b"payload", idempotency_key="k")
        assert "http" not in ref.locator
        assert "http" not in ref.secondary

    async def test_a_retry_does_not_post_twice(
        self, backend: DiscordBackend, api: FakeDiscord
    ) -> None:
        """A request can succeed on the server and fail on the wire."""
        await backend.put(b"payload", idempotency_key="same-key")
        await backend.put(b"payload", idempotency_key="same-key")
        assert len(api.messages) == 1

    async def test_a_rate_limit_is_retried_not_raised(
        self, backend: DiscordBackend, api: FakeDiscord
    ) -> None:
        """A 429 is Discord asking us to wait, not a failure to report."""
        api.rate_limit_once = True
        ref = await backend.put(b"payload", idempotency_key="k")
        assert await backend.get(ref) == b"payload"

    async def test_rate_limit_headers_are_recorded(self, backend: DiscordBackend) -> None:
        """Reacting before a 429 is what keeps throughput up."""
        await backend.put(b"payload", idempotency_key="k")
        assert backend._buckets, "no bucket state was tracked"

    async def test_a_deleted_blob_stops_existing(self, backend: DiscordBackend) -> None:
        ref = await backend.put(b"payload", idempotency_key="k")
        await backend.delete(ref)
        assert not await backend.exists(ref)

    async def test_pagination_walks_the_whole_channel(self, backend: DiscordBackend) -> None:
        """Rebuilding a lost vault depends on seeing every message."""
        for index in range(250):
            await backend.put(f"blob-{index}".encode(), idempotency_key=f"k{index}")
        assert len([ref async for ref in backend.iter_all()]) == 250

    async def test_an_api_error_is_reported_as_a_backend_error(
        self, backend: DiscordBackend
    ) -> None:
        with pytest.raises(BackendError):
            await backend._resolve(BlobRef(locator="99999"))

    def test_rate_limited_carries_its_delay(self) -> None:
        assert RateLimitedError(2.5).retry_after == 2.5
