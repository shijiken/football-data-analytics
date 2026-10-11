"""Fixture-file helpers shared by unit and integration tests."""

from pathlib import Path

import pytest

FIXTURES_DIR = Path(__file__).parent / "fixtures"


def fixture_path(name: str) -> Path:
    """Return the absolute path of a file in ``tests/fixtures``."""
    return FIXTURES_DIR / name


@pytest.fixture
def fixtures_dir() -> Path:
    """Directory holding the CSV fixtures."""
    return FIXTURES_DIR
