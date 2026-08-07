"""Every deliberate failure must be catchable as one root exception."""

import inspect

import pytest

from disbox import errors
from disbox.errors import DisboxError

PROJECT_EXCEPTIONS = [
    obj
    for _, obj in inspect.getmembers(errors, inspect.isclass)
    if issubclass(obj, BaseException) and obj.__module__ == errors.__name__
]


def test_the_module_actually_defines_exceptions() -> None:
    assert len(PROJECT_EXCEPTIONS) > 1, "guard against the discovery above silently emptying"


@pytest.mark.parametrize("exc_type", PROJECT_EXCEPTIONS, ids=lambda t: t.__name__)
def test_every_project_exception_descends_from_the_root(exc_type: type[BaseException]) -> None:
    assert issubclass(exc_type, DisboxError)


def test_root_is_catchable_as_a_plain_exception() -> None:
    with pytest.raises(Exception, match="boom"):
        raise DisboxError("boom")
