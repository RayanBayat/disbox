"""Smoke tests proving the package is installed and importable."""

import tomllib
from pathlib import Path

import disbox

PYPROJECT = Path(__file__).resolve().parents[2] / "pyproject.toml"


def test_package_exposes_a_version() -> None:
    assert disbox.__version__ != "0.0.0.dev0", "package is not installed; run `uv sync`"


def test_version_matches_pyproject() -> None:
    declared = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))["project"]["version"]
    assert disbox.__version__ == declared


def test_package_is_marked_as_typed() -> None:
    marker = Path(disbox.__file__).parent / "py.typed"
    assert marker.is_file(), "PEP 561 marker missing; type hints will not be exported"
