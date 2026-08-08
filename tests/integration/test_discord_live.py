"""Live checks against a real Discord channel.

Marked ``live`` and excluded from every default run: these hit the network,
create real messages, and need credentials. Everything in the Discord backend
is already covered against a mocked API; this exists to prove the real
handshake, which no mock can.

Run with::

    uv run pytest -m live -v

Requires DISBOX_BOT_TOKEN and DISBOX_CHANNEL_ID, read from the environment or a
gitignored .env. Every message this creates is deleted again.
"""

import io
import os
import uuid
from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from disbox.backends.base import BlobRef
from disbox.backends.discord import DiscordBackend
from disbox.config import load_settings
from disbox.core.chunker import ChunkSpec
from disbox.core.crypto import KdfParams
from disbox.core.engine import TransferEngine
from disbox.core.vault import Vault

pytestmark = pytest.mark.live

PASSPHRASE = "live smoke test passphrase"  # noqa: S105 - fixture, not a credential
FAST = KdfParams(time_cost=1, memory_cost=8, parallelism=1)


@pytest.fixture
async def backend() -> AsyncIterator[DiscordBackend]:
    """A backend bound to the configured channel, closed afterwards."""
    settings = load_settings()
    if not settings.discord_configured:
        pytest.skip("DISBOX_BOT_TOKEN and DISBOX_CHANNEL_ID are not set")

    assert settings.bot_token is not None
    assert settings.channel_id is not None
    created = DiscordBackend(settings.bot_token.get_secret_value(), settings.channel_id)
    try:
        yield created
    finally:
        await created.close()


class TestLiveBackend:
    async def test_a_blob_round_trips_through_discord(self, backend: DiscordBackend) -> None:
        payload = os.urandom(4096)
        key = f"smoke-{uuid.uuid4().hex}"
        ref = await backend.put(payload, idempotency_key=key)
        try:
            assert await backend.get(ref) == payload
            assert await backend.exists(ref)
        finally:
            await backend.delete(ref)

    async def test_a_deleted_blob_is_gone(self, backend: DiscordBackend) -> None:
        ref = await backend.put(b"transient", idempotency_key=f"smoke-{uuid.uuid4().hex}")
        await backend.delete(ref)
        assert not await backend.exists(ref)

    async def test_the_reference_holds_no_url(self, backend: DiscordBackend) -> None:
        """A signed CDN link must never be what we persist."""
        ref = await backend.put(b"payload", idempotency_key=f"smoke-{uuid.uuid4().hex}")
        try:
            assert "http" not in ref.locator
        finally:
            await backend.delete(ref)

    async def test_a_resolved_url_actually_works(self, backend: DiscordBackend) -> None:
        """Resolving on demand is what replaced storing an expiring link."""
        ref = await backend.put(b"resolve me", idempotency_key=f"smoke-{uuid.uuid4().hex}")
        try:
            url = await backend._resolve(ref)
            assert url.startswith("https://")
            assert await backend.get(ref) == b"resolve me"
        finally:
            await backend.delete(ref)


class TestLiveEndToEnd:
    async def test_a_real_file_round_trips_encrypted(
        self, backend: DiscordBackend, tmp_path: Path
    ) -> None:
        """The whole stack against the real service: chunk, seal, store, rebuild."""
        payload = os.urandom(300_000)

        with Vault.create_encrypted(tmp_path / "live.dbx", PASSPHRASE, FAST) as vault:
            engine = TransferEngine(
                vault,
                backend,
                vault.unlock(PASSPHRASE),
                spec=ChunkSpec(min_size=32_768, avg_size=65_536, max_size=131_072),
                concurrency=4,
            )
            node_id = uuid.uuid7()
            with vault.connection as conn:
                conn.execute(
                    "INSERT INTO nodes (id, parent_id, name, kind, created_at, modified_at) "
                    "VALUES (?, NULL, 'live.bin', 'file', '2026-01-01', '2026-01-01')",
                    (node_id.bytes,),
                )

            refs = []
            try:
                await engine.upload(node_id, io.BytesIO(payload))
                rows = vault.connection.execute(
                    "SELECT message_id, attach_id FROM chunks"
                ).fetchall()
                refs = [BlobRef(locator=r[0], secondary=r[1]) for r in rows]

                sink = io.BytesIO()
                await engine.download(node_id, sink)
                assert sink.getvalue() == payload
            finally:
                for ref in refs:
                    await backend.delete(ref)
