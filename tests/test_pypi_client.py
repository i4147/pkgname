"""Tests for the async PyPI client (respx-mocked; no real network)."""

from __future__ import annotations

import httpx
import pytest
import respx

from pkgname.pypi import PyPIClient, PyPIClientError
from pkgname.similarity import SimilarityConfig

INFO = {"info": {"name": "requests", "summary": "Python HTTP for Humans.", "version": "2.31.0"}}


def _client(**kwargs: object) -> PyPIClient:
    kwargs.setdefault("backoff_multiplier", 0.01)
    return PyPIClient(**kwargs)  # type: ignore[arg-type]


async def test_get_project_200() -> None:
    with respx.mock:
        respx.get("https://pypi.org/pypi/requests/json").mock(
            return_value=httpx.Response(200, json=INFO)
        )
        async with _client() as client:
            info = await client.get_project("requests")
    assert info is not None
    assert info.name == "requests"
    assert info.summary == "Python HTTP for Humans."
    assert info.version == "2.31.0"
    assert info.normalized == "requests"


async def test_get_project_404() -> None:
    with respx.mock:
        respx.get("https://pypi.org/pypi/nope/json").mock(return_value=httpx.Response(404))
        async with _client() as client:
            assert await client.get_project("nope") is None


async def test_retry_on_429_then_success() -> None:
    with respx.mock:
        route = respx.get("https://pypi.org/pypi/requests/json").mock(
            side_effect=[httpx.Response(429), httpx.Response(200, json=INFO)]
        )
        async with _client() as client:
            info = await client.get_project("requests")
    assert info is not None and info.version == "2.31.0"
    assert len(route.calls) == 2


async def test_retry_on_500_then_success() -> None:
    with respx.mock:
        route = respx.get("https://pypi.org/pypi/requests/json").mock(
            side_effect=[httpx.Response(500), httpx.Response(200, json=INFO)]
        )
        async with _client() as client:
            info = await client.get_project("requests")
    assert info is not None
    assert len(route.calls) == 2


async def test_429_exhausted_raises_typed_error() -> None:
    with respx.mock:
        route = respx.get("https://pypi.org/pypi/requests/json").mock(
            return_value=httpx.Response(429)
        )
        async with _client(max_retries=3) as client:
            with pytest.raises(PyPIClientError) as excinfo:
                await client.get_project("requests")
    assert excinfo.value.retryable is True
    assert excinfo.value.status_code == 429
    assert len(route.calls) == 3


async def test_non_retryable_4xx_fails_fast() -> None:
    with respx.mock:
        route = respx.get("https://pypi.org/pypi/requests/json").mock(
            return_value=httpx.Response(403)
        )
        async with _client() as client:
            with pytest.raises(PyPIClientError) as excinfo:
                await client.get_project("requests")
    assert excinfo.value.retryable is False
    assert len(route.calls) == 1


async def test_timeout_is_retryable() -> None:
    with respx.mock:
        route = respx.get("https://pypi.org/pypi/requests/json").mock(
            side_effect=httpx.TimeoutException("timed out")
        )
        async with _client(max_retries=2) as client:
            with pytest.raises(PyPIClientError) as excinfo:
                await client.get_project("requests")
    assert excinfo.value.retryable is True
    assert len(route.calls) == 2


async def test_invalid_json_body() -> None:
    with respx.mock:
        respx.get("https://pypi.org/pypi/requests/json").mock(
            return_value=httpx.Response(200, text="not json")
        )
        async with _client() as client:
            with pytest.raises(PyPIClientError, match="invalid JSON"):
                await client.get_project("requests")


async def test_cache_avoids_second_request() -> None:
    with respx.mock:
        route = respx.get("https://pypi.org/pypi/requests/json").mock(
            return_value=httpx.Response(200, json=INFO)
        )
        async with _client() as client:
            first = await client.get_project("requests")
            second = await client.get_project("requests")
    assert first == second
    assert len(route.calls) == 1


async def test_probe_variants_finds_existing() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/pypi/walk-fs/json":
            return httpx.Response(
                200,
                json={
                    "info": {
                        "name": "walk-fs",
                        "summary": "Filesystem walking utilities",
                        "version": "1.4.2",
                    }
                },
            )
        return httpx.Response(404)

    with respx.mock:
        respx.get(url__regex=r"https://pypi\.org/pypi/.*").mock(side_effect=handler)
        async with _client() as client:
            matches = await client.probe_variants("walkfs", SimilarityConfig(distance=2))
    assert [m.normalized for m in matches] == ["walk-fs"]
    assert matches[0].reason == "separator variant"
    assert matches[0].origin == "pypi"
    assert matches[0].version == "1.4.2"
    assert matches[0].summary == "Filesystem walking utilities"


async def test_probe_variants_none_exist() -> None:
    with respx.mock:
        respx.get(url__regex=r"https://pypi\.org/pypi/.*").mock(return_value=httpx.Response(404))
        async with _client() as client:
            matches = await client.probe_variants("walkfs", SimilarityConfig(distance=2))
    assert matches == []
