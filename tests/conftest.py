"""Fixtures shared by the whole suite."""

from collections.abc import Iterator

import pytest
import structlog


@pytest.fixture(autouse=True)
def _reset_logging_configuration() -> Iterator[None]:
    """Undo any logging setup a test performs.

    ``structlog.configure`` mutates global state, so without this a single test
    that configures logging changes the behaviour of every test that runs after
    it -- and the failure surfaces far from its cause.
    """
    try:
        yield
    finally:
        structlog.reset_defaults()
