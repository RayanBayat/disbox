"""Secrets must never reach the log, whatever shape they arrive in."""

import pytest

from disbox.log import REDACTED, configure, get_logger, redact, redact_processor

# Structurally valid but entirely fabricated — never a real credential.
# S105: bandit cannot tell a test fixture from a leaked secret; that it fires
# here is evidence the rule is working.
BOT_TOKEN = "MTA5NDU2NzgxMjM0NTY3ODkw"".""GaBcDe"".""FgHiJkLmNoPqRsTuVwXyZ1234567890abcdef"  # noqa: S105
WEBHOOK_URL = (
    "https://discord.com/api/webhooks/1094567812345678900/"
    "aBcDeFgHiJkLmNoPqRsTuVwXyZ0123456789aBcDeFgHiJkLmNoPqRsTuVwXyZ012"
)


class TestRedactString:
    def test_bare_bot_token_is_redacted(self) -> None:
        assert BOT_TOKEN not in redact(BOT_TOKEN)

    def test_token_embedded_in_a_message_is_redacted(self) -> None:
        message = f"request failed: Authorization: Bot {BOT_TOKEN} returned 401"
        result = redact(message)
        assert BOT_TOKEN not in result
        assert "returned 401" in result, "surrounding context must survive"

    def test_webhook_token_is_redacted_but_id_survives(self) -> None:
        result = redact(WEBHOOK_URL)
        assert WEBHOOK_URL.rsplit("/", 1)[1] not in result
        assert "1094567812345678900" in result, "the id is not a secret and aids debugging"

    def test_ordinary_text_is_untouched(self) -> None:
        message = "uploaded chunk 7 of 42 to channel 1094567812345678900"
        assert redact(message) == message


class TestRedactProcessor:
    def test_sensitive_key_is_redacted_regardless_of_value(self) -> None:
        event = redact_processor(None, "info", {"event": "auth", "token": "anything-at-all"})
        assert event["token"] == REDACTED

    @pytest.mark.parametrize(
        "key",
        ["token", "bot_token", "password", "passphrase", "secret", "api_key", "authorization"],
    )
    def test_every_sensitive_key_name_is_covered(self, key: str) -> None:
        event = redact_processor(None, "info", {"event": "x", key: "sensitive"})
        assert event[key] == REDACTED

    def test_key_matching_is_case_insensitive(self) -> None:
        event = redact_processor(None, "info", {"event": "x", "Authorization": "Bot abc"})
        assert event["Authorization"] == REDACTED

    def test_token_in_an_innocuous_key_is_still_caught(self) -> None:
        event = redact_processor(None, "info", {"event": "req failed", "url": WEBHOOK_URL})
        assert WEBHOOK_URL.rsplit("/", 1)[1] not in event["url"]

    def test_nested_structures_are_redacted(self) -> None:
        event = redact_processor(
            None, "info", {"event": "x", "ctx": {"headers": {"authorization": BOT_TOKEN}}}
        )
        assert event["ctx"]["headers"]["authorization"] == REDACTED

    def test_tokens_inside_lists_are_redacted(self) -> None:
        event = redact_processor(None, "info", {"event": "x", "urls": [WEBHOOK_URL]})
        assert WEBHOOK_URL.rsplit("/", 1)[1] not in event["urls"][0]

    def test_non_string_values_pass_through(self) -> None:
        event = redact_processor(None, "info", {"event": "x", "count": 42, "ok": True})
        assert event["count"] == 42
        assert event["ok"] is True


class TestEndToEnd:
    def test_configured_logger_never_emits_a_token(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        configure(level="INFO", json_output=True)
        get_logger(__name__).info("upload failed", url=WEBHOOK_URL, bot_token=BOT_TOKEN)

        output = capsys.readouterr().out
        assert output, "logger produced no output"
        assert BOT_TOKEN not in output
        assert WEBHOOK_URL.rsplit("/", 1)[1] not in output
