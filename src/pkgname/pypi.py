"""Async PyPI JSON API client with retries, caching, bounded concurrency."""

from __future__ import annotations

import asyncio
import logging
from collections import OrderedDict
from dataclasses import dataclass
from types import TracebackType

import httpx
from rapidfuzz.distance import Levenshtein
from tenacity import AsyncRetrying, retry_if_exception, stop_after_attempt, wait_exponential

from . import __version__
from .models import Match
from .similarity import SimilarityConfig, generate_variants, reason_for

logger = logging.getLogger(__name__)

BASE_URL = "https://pypi.org/pypi"
USER_AGENT = f"pkgname/{__version__}"


class PyPIClientError(Exception):

    def __init__(
        self,
        message,
        *,
        retryable = False,
        status_code = None,
    ):
        super().__init__(message)
        self.retryable = retryable
        self.status_code = status_code


@dataclass(frozen=True)
class ProjectInfo:

    pass


def _is_retryable(exc):
    return isinstance(exc, PyPIClientError) and exc.retryable


class PyPIClient:

    def __init__(
        self,
        *,
        timeout = 10.0,
        concurrency = 10,
        transport = None,
        base_url = BASE_URL,
        max_retries = 3,
        backoff_multiplier = 1.0,
        cache_size = 4096,
    ):
        self._client = httpx.AsyncClient(
            timeout=timeout,
            headers={"User-Agent": USER_AGENT},
            transport=transport,
            base_url=base_url,
        )
        self._semaphore = asyncio.Semaphore(concurrency)
        self._max_retries = max_retries
        self._backoff_multiplier = backoff_multiplier
        self._cache_size = cache_size
        self._cache = OrderedDict()

    async def __aenter__(self):
        return self

    async def __aexit__(
        self,
        exc_type,
        exc_val,
        exc_tb,
    ):
        await self.close()

    async def close(self):
        await self._client.aclose()

    async def get_project(self, normalized_name):
        if normalized_name in self._cache:
            self._cache.move_to_end(normalized_name)
            return self._cache[normalized_name]
        info = await self._get_with_retry(normalized_name)
        self._cache[normalized_name] = info
        while len(self._cache) > self._cache_size:
            self._cache.popitem(last=False)
        return info

    async def probe_variants(self, name, config):
        variants = generate_variants(name, config)

        async def probe(variant):
            info = await self.get_project(variant)
            if info is None:
                return None
            distance = Levenshtein.distance(name, variant)
            return Match(
                name=info.name,
                normalized=variant,
                reason=reason_for(name, variant, distance, config),
                distance=distance,
                summary=info.summary,
                version=info.version,
                origin="pypi",
            )

        results = await asyncio.gather(*(probe(v) for v, _hint in variants))
        return sorted((r for r in results if r is not None), key=lambda m: (m.distance, m.normalized))

    async def _get_with_retry(self, normalized_name):
        async for attempt in AsyncRetrying(
            stop=stop_after_attempt(self._max_retries),
            wait=wait_exponential(
                multiplier=self._backoff_multiplier,
                min=self._backoff_multiplier,
                max=8 * self._backoff_multiplier,
            ),
            retry=retry_if_exception(_is_retryable),
            reraise=True,
        ):
            with attempt:
                return await self._get_once(normalized_name)
        return None

    async def _get_once(self, normalized_name):
        async with self._semaphore:
            try:
                response = await self._client.get(f"/{normalized_name}/json")
            except httpx.TimeoutException as exc:
                raise PyPIClientError(f"request to PyPI timed out: {exc}", retryable=True) from exc
            except httpx.TransportError as exc:
                raise PyPIClientError(f"network error contacting PyPI: {exc}", retryable=True) from exc
        if response.status_code == 200:
            try:
                payload = response.json()
            except ValueError as exc:
                raise PyPIClientError(f"invalid JSON from PyPI for {normalized_name!r}", retryable=False) from exc
            info = payload.get("info") or {}
            return ProjectInfo(
                name=str(info.get("name") or normalized_name),
                normalized=normalized_name,
                summary=info.get("summary") or None,
                version=info.get("version") or None,
            )
        if response.status_code == 404:
            return None
        if response.status_code == 429 or response.status_code >= 500:
            raise PyPIClientError(
                f"PyPI returned {response.status_code} for {normalized_name!r} (rate limited or server error)",
                retryable=True,
                status_code=response.status_code,
            )
        raise PyPIClientError(
            f"PyPI returned {response.status_code} for {normalized_name!r}",
            retryable=False,
            status_code=response.status_code,
        )
