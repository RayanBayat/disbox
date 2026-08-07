"""Structured logging with mandatory secret redaction.

Disbox handles Discord bot tokens and webhook URLs that grant complete access to
a user's stored files. A single leaked log line is a total compromise, so
redaction is not opt-in: it runs as a processor on every event, after exception
formatting, so it also scrubs tracebacks -- the most common leak path, since
HTTP clients routinely embed the full request URL in error messages.

Two independent layers run, because either alone is insufficient:

1. **Key-based** -- any field whose name suggests a credential is replaced
   wholesale, regardless of the value's shape.
2. **Pattern-based** -- known credential formats are matched inside arbitrary
   strings, catching secrets embedded in messages, URLs, and tracebacks where
   no helpful field name exists.
"""

import logging
import re
import sys
from collections.abc import Mapping
from typing import Any, Final

import structlog
from structlog.typing import EventDict, WrappedLogger

REDACTED: Final = "***REDACTED***"

# Keys whose entire value is replaced. Exact matches first, then substrings so
# that `discord_bot_token` and `x_authorization_header` are covered too.
_SENSITIVE_KEYS: Final = frozenset({"auth", "key", "token", "secret", "credentials"})
_SENSITIVE_MARKERS: Final = (
    "api_key",
    "authorization",
    "cookie",
    "credential",
    "master_key",
    "passphrase",
    "password",
    "private_key",
    "secret",
    "session_id",
    "token",
    "webhook_url",
)

# Order matters: the webhook rule runs first so it can preserve the channel id
# before a broader rule would swallow the whole URL.
_PATTERNS: Final = (
    # Discord webhook URL -- keep the id (useful, not secret), drop the token.
    (re.compile(r"(/api/webhooks/\d{17,20}/)[\w-]{50,}"), r"\1" + REDACTED),
    # Discord bot token: <base64 id>.<base64 timestamp>.<hmac>
    (re.compile(r"\b[A-Za-z0-9_-]{23,28}\.[A-Za-z0-9_-]{6,7}\.[A-Za-z0-9_-]{27,}\b"), REDACTED),
    # Authorization header values of either scheme.
    (re.compile(r"\b(Bot|Bearer)\s+[A-Za-z0-9_.\-]{20,}", re.IGNORECASE), r"\1 " + REDACTED),
)


def redact(text: str) -> str:
    """Replace any recognised credential inside `text`.

    Args:
        text: Arbitrary text that may embed a secret.

    Returns:
        The text with every recognised credential replaced, and all surrounding
        context preserved so the line stays useful for debugging.
    """
    for pattern, replacement in _PATTERNS:
        text = pattern.sub(replacement, text)
    return text


def _is_sensitive(key: str) -> bool:
    """Report whether a field name suggests the value is a credential."""
    lowered = key.lower()
    return lowered in _SENSITIVE_KEYS or any(m in lowered for m in _SENSITIVE_MARKERS)


def _scrub(value: Any, *, key: str | None = None) -> Any:
    """Recursively redact `value`, replacing it wholesale if `key` looks sensitive."""
    if key is not None and _is_sensitive(key):
        return REDACTED
    if isinstance(value, str):
        return redact(value)
    if isinstance(value, Mapping):
        return {k: _scrub(v, key=str(k)) for k, v in value.items()}
    if isinstance(value, list | tuple | set | frozenset):
        scrubbed = [_scrub(item) for item in value]
        # Rebuild the original container type, but only for the exact builtins.
        # Subclasses such as namedtuple take positional fields, not an iterable,
        # so reconstructing them would raise inside the logger.
        exact = type(value)
        return exact(scrubbed) if exact in {list, tuple, set, frozenset} else scrubbed
    return value


def redact_processor(
    logger: WrappedLogger,  # noqa: ARG001 - required by the structlog processor signature
    method_name: str,  # noqa: ARG001 - required by the structlog processor signature
    event_dict: EventDict,
) -> EventDict:
    """Strip credentials from an event before it is rendered.

    Args:
        logger: Unused; part of the structlog processor contract.
        method_name: Unused; part of the structlog processor contract.
        event_dict: The event about to be rendered.

    Returns:
        The event with every credential removed from keys, values, nested
        structures, and formatted tracebacks.
    """
    return {key: _scrub(value, key=str(key)) for key, value in event_dict.items()}


def configure(*, level: str = "INFO", json_output: bool = False) -> None:
    """Install the logging pipeline. Call once, at process start.

    Args:
        level: Minimum level to emit, as a standard logging level name.
        json_output: Emit one JSON object per line instead of coloured console
            output. Use for files and CI; console output is for humans.

    Raises:
        ValueError: If `level` is not a recognised logging level name.
    """
    levels = logging.getLevelNamesMapping()
    if level.upper() not in levels:
        msg = f"unknown log level {level!r}; expected one of {sorted(levels)}"
        raise ValueError(msg)

    renderer: Any = (
        structlog.processors.JSONRenderer()
        if json_output
        else structlog.dev.ConsoleRenderer(colors=False)
    )

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            # Runs after exception formatting so tracebacks are scrubbed too,
            # and immediately before rendering so nothing can reintroduce a
            # secret downstream.
            redact_processor,
            renderer,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(levels[level.upper()]),
        logger_factory=structlog.PrintLoggerFactory(file=sys.stdout),
        cache_logger_on_first_use=False,
    )


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    """Return a bound logger, conventionally named after the calling module."""
    return structlog.get_logger(name)  # type: ignore[no-any-return]  # structlog returns Any
