"""Discord as a blob store.

Discord is one implementation of `StorageBackend`, deliberately not a special
case. Everything specific to it lives here: bot authentication, its rate-limit
protocol, and the fact that its CDN links expire.

Three details drive the design.

**Attachment URLs are signed and short-lived.** They cannot be persisted; doing
so is what left the previous generation of this project handing out share links
that silently stopped working after a day. Only message and attachment ids are
stored, and a fresh URL is resolved at read time.

**Rate limits are per-route buckets, not global.** Discord names the bucket in a
response header, so the limiter keys on what the server says rather than on a
guess. Reacting to `X-RateLimit-Remaining` before a 429 is what keeps throughput
high; treating 429 as the signal means you are already being punished.

**Uploads must be idempotent.** A request can succeed on the server and fail on
the wire, and a blind retry would post the chunk twice, leaving an orphan no
garbage collection can find. The idempotency key is written into the attachment
filename so a retry can look for its own earlier attempt.
"""

import asyncio
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any, Final

import httpx
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential_jitter,
)

from disbox.backends.base import BlobRef
from disbox.errors import BackendError
from disbox.log import get_logger

__all__ = ["DiscordBackend", "RateLimitedError"]

logger = get_logger(__name__)

_API_ROOT: Final = "https://discord.com/api/v10"
_MAX_ATTEMPTS: Final = 6
_PAGE_SIZE: Final = 100
_STREAM_BLOCK: Final = 64 * 1024

#: Discord's documented ceiling for a bot, applied across every route.
_GLOBAL_RATE: Final = 50

#: Size ladder probed at setup, smallest first. Which applies depends on the
#: guild's boost tier and any Nitro on the account, so it is measured rather
#: than assumed -- the previous client hardcoded 25 MB and broke when the limit
#: moved.
_SIZE_LADDER: Final = (10, 25, 50, 100, 500)


class RateLimitedError(Exception):
    """Discord asked us to wait. Retried, never surfaced to callers."""

    def __init__(self, retry_after: float) -> None:
        """Record how long Discord asked us to wait."""
        super().__init__(f"rate limited for {retry_after:.2f}s")
        self.retry_after = retry_after


@dataclass
class _Bucket:
    """What the server last told us about one rate-limit bucket."""

    remaining: int = 1
    resets_at: float = 0.0
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


class DiscordBackend:
    """Stores blobs as message attachments in one Discord channel."""

    def __init__(
        self,
        token: str,
        channel_id: int,
        *,
        client: httpx.AsyncClient | None = None,
        max_blob_size: int = 10 * 1024 * 1024,
    ) -> None:
        """Bind to a channel.

        Args:
            token: Bot token. Never logged; the redaction processor also
                strips it from any message that manages to include it.
            channel_id: Channel used as the blob store.
            client: Injected for tests. One is built if omitted.
            max_blob_size: Starting assumption, refined by `probe`.
        """
        self._channel_id = channel_id
        self._max_blob_size = max_blob_size
        self._buckets: dict[str, _Bucket] = {}
        self._url_cache: dict[str, tuple[str, float]] = {}
        self._client = client or httpx.AsyncClient(
            base_url=_API_ROOT,
            headers={"Authorization": f"Bot {token}"},
            timeout=httpx.Timeout(15.0, read=300.0),
            limits=httpx.Limits(max_connections=20),
            http2=False,
        )

    @property
    def name(self) -> str:
        """Short identifier for logs and the vault's backend row."""
        return "discord"

    @property
    def max_blob_size(self) -> int:
        """Largest attachment this channel currently accepts."""
        return self._max_blob_size

    # ----------------------------------------------------------- transport --

    async def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        """Issue one API call, honouring the bucket Discord assigns it."""
        bucket = self._buckets.setdefault(f"{method}:{path}", _Bucket())

        async with bucket.lock:
            # Wait *before* sending when the bucket is spent. Reacting only to
            # 429 means the penalty has already been applied.
            if bucket.remaining <= 0 and (delay := bucket.resets_at - time.monotonic()) > 0:
                await asyncio.sleep(delay)

            response = await self._client.request(method, path, **kwargs)
            self._observe(bucket, response)

        if response.status_code == httpx.codes.TOO_MANY_REQUESTS:
            raise RateLimitedError(float(response.headers.get("Retry-After", 1.0)))
        if response.status_code >= httpx.codes.BAD_REQUEST:
            msg = f"Discord returned {response.status_code} for {method} {path}"
            raise BackendError(msg)
        return response

    @staticmethod
    def _observe(bucket: _Bucket, response: httpx.Response) -> None:
        """Update a bucket from the server's own accounting."""
        remaining = response.headers.get("X-RateLimit-Remaining")
        reset_after = response.headers.get("X-RateLimit-Reset-After")
        if remaining is not None:
            bucket.remaining = int(float(remaining))
        if reset_after is not None:
            bucket.resets_at = time.monotonic() + float(reset_after)

    # ------------------------------------------------------------ contract --

    @retry(
        retry=retry_if_exception_type((RateLimitedError, httpx.TransportError)),
        wait=wait_exponential_jitter(initial=0.5, max=30),
        stop=stop_after_attempt(_MAX_ATTEMPTS),
        reraise=True,
    )
    async def put(self, data: bytes, *, idempotency_key: str) -> BlobRef:
        """Post `data` as an attachment.

        Retries transport failures and rate limits with jittered backoff, and is
        bounded: an unbounded retry against a persistently failing endpoint is a
        hang, not resilience.

        Raises:
            ValueError: If `data` exceeds what the channel accepts.
            BackendError: If Discord refuses the upload.
        """
        if len(data) > self._max_blob_size:
            msg = f"blob of {len(data)} bytes exceeds the {self._max_blob_size} byte limit"
            raise ValueError(msg)

        if (existing := await self._find_by_key(idempotency_key)) is not None:
            return existing

        response = await self._request(
            "POST",
            f"/channels/{self._channel_id}/messages",
            files={"files[0]": (idempotency_key, data, "application/octet-stream")},
        )
        message = response.json()
        attachment = message["attachments"][0]
        return BlobRef(
            locator=str(message["id"]),
            secondary=str(attachment["id"]),
            size=int(attachment.get("size", len(data))),
        )

    async def _find_by_key(self, idempotency_key: str) -> BlobRef | None:
        """Look for an earlier attempt with this key, so a retry cannot duplicate."""
        async for ref, filename in self._iter_with_names():
            if filename == idempotency_key:
                return ref
        return None

    async def get(self, ref: BlobRef, *, byte_range: tuple[int, int] | None = None) -> bytes:
        """Download a blob, resolving a fresh URL first."""
        url = await self._resolve(ref)
        headers = {"Range": f"bytes={byte_range[0]}-{byte_range[1] - 1}"} if byte_range else {}
        response = await self._client.get(url, headers=headers)
        if response.status_code in (httpx.codes.FORBIDDEN, httpx.codes.NOT_FOUND):
            # The signature expired between resolving and fetching; drop the
            # cached URL and take one fresh attempt.
            self._url_cache.pop(ref.locator, None)
            response = await self._client.get(await self._resolve(ref), headers=headers)
        response.raise_for_status()
        return response.content

    async def stream(self, ref: BlobRef) -> AsyncIterator[bytes]:
        """Yield a blob progressively, so a large one never sits in memory."""
        url = await self._resolve(ref)
        async with self._client.stream("GET", url) as response:
            response.raise_for_status()
            async for block in response.aiter_bytes(chunk_size=_STREAM_BLOCK):
                yield block

    async def _resolve(self, ref: BlobRef) -> str:
        """Return a currently valid CDN URL for `ref`.

        Cached only briefly and never persisted: these URLs are signed and
        expire, so storing one produces a reference that silently rots.
        """
        cached = self._url_cache.get(ref.locator)
        if cached is not None and cached[1] > time.monotonic():
            return cached[0]

        response = await self._request(
            "GET", f"/channels/{self._channel_id}/messages/{ref.locator}"
        )
        attachments = response.json().get("attachments") or []
        if not attachments:
            msg = f"message {ref.locator} carries no attachment"
            raise BackendError(msg)

        url = str(attachments[0]["url"])
        self._url_cache[ref.locator] = (url, time.monotonic() + 600)
        return url

    async def delete(self, ref: BlobRef) -> None:
        """Delete the message holding a blob. An absent message is not an error."""
        try:
            await self._request("DELETE", f"/channels/{self._channel_id}/messages/{ref.locator}")
        except BackendError as exc:
            # Garbage collection retries, so a blob already gone is success.
            if "404" not in str(exc):
                raise
        self._url_cache.pop(ref.locator, None)

    async def exists(self, ref: BlobRef) -> bool:
        """Report whether the message is still present."""
        try:
            await self._request("GET", f"/channels/{self._channel_id}/messages/{ref.locator}")
        except BackendError:
            return False
        return True

    async def iter_all(self) -> AsyncIterator[BlobRef]:
        """Page through the channel, yielding every attachment.

        This is what makes a vault rebuildable after losing the local file.
        """
        async for ref, _name in self._iter_with_names():
            yield ref

    async def _iter_with_names(self) -> AsyncIterator[tuple[BlobRef, str]]:
        """Page through the channel, yielding each blob with its filename."""
        before: str | None = None
        while True:
            params: dict[str, Any] = {"limit": _PAGE_SIZE}
            if before is not None:
                params["before"] = before

            response = await self._request(
                "GET", f"/channels/{self._channel_id}/messages", params=params
            )
            messages = response.json()
            if not messages:
                return

            for message in messages:
                for attachment in message.get("attachments") or []:
                    yield (
                        BlobRef(
                            locator=str(message["id"]),
                            secondary=str(attachment["id"]),
                            size=int(attachment.get("size", 0)),
                        ),
                        str(attachment.get("filename", "")),
                    )
            before = str(messages[-1]["id"])

    async def probe(self) -> int:
        """Measure the largest attachment this channel accepts.

        Measured rather than assumed: the limit depends on the guild's boost
        tier and has changed repeatedly. Hardcoding it is what broke the
        previous client when Discord moved the number.
        """
        largest = _SIZE_LADDER[0] * 1024 * 1024
        for megabytes in _SIZE_LADDER:
            size = megabytes * 1024 * 1024
            try:
                probe_ref = await self.put(b"\0" * size, idempotency_key=f"probe-{megabytes}")
            except ValueError, BackendError:
                break
            await self.delete(probe_ref)
            largest = size

        self._max_blob_size = largest
        logger.info("probed attachment limit", bytes=largest)
        return largest

    async def close(self) -> None:
        """Release the HTTP connection pool."""
        await self._client.aclose()
