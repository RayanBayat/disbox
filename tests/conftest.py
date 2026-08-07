"""Fixtures shared by the whole suite."""

import os

# Qt must render offscreen during tests: on a developer machine real windows
# would flash up and steal focus, and on CI there is no display at all. This
# runs before any Qt import, which is the only point at which it takes effect.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

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
