"""Shared fixtures: index-singleton isolation and the fixture index path."""

from __future__ import annotations

from pathlib import Path

import pytest

from pkgname.index import reset_index


@pytest.fixture(autouse=True)
def _reset_index_singleton() -> object:
    reset_index()
    yield
    reset_index()


@pytest.fixture(autouse=True)
def _isolate_cache_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep tests from touching the real ~/.cache/pkgname."""
    monkeypatch.setenv("PKGNAME_CACHE_DIR", str(tmp_path / "cache"))


@pytest.fixture
def fixture_index_path() -> Path:
    return Path(__file__).parent / "fixtures" / "pip_small.json"
