"""Optional integration tests (real network / real ./pip.json).

Skipped unless PKGNAME_RUN_INTEGRATION=1. Run with:
    PKGNAME_RUN_INTEGRATION=1 pytest -q -m integration
    PKGNAME_RUN_INTEGRATION=1 pytest -q -m integration_index
"""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from pkgname.index import LocalIndex
from pkgname.pypi import PyPIClient

RUN = os.environ.get("PKGNAME_RUN_INTEGRATION") == "1"
needs_flag = pytest.mark.skipif(not RUN, reason="set PKGNAME_RUN_INTEGRATION=1 to run")


@pytest.mark.integration
@needs_flag
async def test_real_pypi_taken_and_available() -> None:
    async with PyPIClient() as client:
        assert await client.get_project("requests") is not None
        assert await client.get_project("pkgname-definitely-not-real-xyz-12345") is None


@pytest.mark.integration_index
@needs_flag
def test_real_index_load(tmp_path: Path) -> None:
    path = Path("./pip.json")
    if not path.exists():
        pytest.skip("pip.json not present in the working directory")
    start = time.perf_counter()
    idx = LocalIndex(path, cache_dir=tmp_path).load()
    elapsed_ms = (time.perf_counter() - start) * 1000
    assert idx.available
    assert idx.stats().entry_count > 100_000
    assert elapsed_ms < 10_000  # cold parse of the real snapshot
    # Warm cache load must be fast.
    start = time.perf_counter()
    warm = LocalIndex(path, cache_dir=tmp_path).load()
    warm_ms = (time.perf_counter() - start) * 1000
    assert warm.available
    assert warm_ms < 1000
