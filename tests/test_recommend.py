"""Tests for candidate generation, validation, and ranking."""

from __future__ import annotations

from pathlib import Path

import httpx
import respx

from pkgname.index import LocalIndex
from pkgname.pypi import PyPIClient
from pkgname.recommend import generate_candidates, recommend, score_candidate
from pkgname.similarity import SimilarityConfig, is_too_similar

CFG = SimilarityConfig(distance=2)


def _index(path: Path, tmp_path: Path) -> LocalIndex:
    return LocalIndex(path, cache_dir=tmp_path).load()


def test_generate_candidates_strategies() -> None:
    cands = dict(generate_candidates("walk-fs"))
    assert cands["walkfs"] == "separator"
    assert cands["walk-fs-cli"] == "suffix"
    assert cands["py-walk-fs"] == "prefix"
    assert cands["traverse-fs"] == "synonym"
    assert cands["walk-filesystem"] == "synonym"
    assert cands["fs-walk"] == "reversal"
    assert "walk-fs" not in cands  # never the original


def test_generate_candidates_anchor_split() -> None:
    cands = dict(generate_candidates("walkfs"))
    assert cands["walk-fs"] == "separator"
    assert cands["walkfs-cli"] == "suffix"


def test_generate_candidates_deduped_and_bounded() -> None:
    cands = generate_candidates("walk-fs")
    names = [c for c, _ in cands]
    assert len(names) == len(set(names))
    assert len(cands) <= 2000


def test_score_candidate_ordering() -> None:
    # Edit distance is the primary ranking key, strategy rank secondary:
    # a distance-1 abbreviation beats a distance-4 suffix.
    sep = score_candidate("walk-fs", "walkfs", "separator")
    suf = score_candidate("walk-fs", "walk-fs-cli", "suffix")
    abbr = score_candidate("walk-fs", "wlk-fs", "abbreviation")
    assert sep > abbr > suf
    assert 0.0 <= suf <= 1.0


async def test_recommend_local_mode(fixture_index_path: Path, tmp_path: Path) -> None:
    idx = _index(fixture_index_path, tmp_path)
    recs = await recommend(
        "walk-fs",
        index=idx,
        client=None,
        sim_config=CFG,
        limit=10,
        verify_online=False,
    )
    assert recs, "expected some recommendations"
    assert len(recs) <= 10
    for rec in recs:
        assert rec.verified_offline is True
        assert not is_too_similar(rec.normalized, idx.names_list, idx.names_set, CFG)
        assert rec.normalized not in idx
    scores = [r.score for r in recs]
    assert scores == sorted(scores, reverse=True)


async def test_recommend_respects_limit(fixture_index_path: Path, tmp_path: Path) -> None:
    idx = _index(fixture_index_path, tmp_path)
    recs = await recommend(
        "walk-fs", index=idx, client=None, sim_config=CFG, limit=3, verify_online=False
    )
    assert len(recs) == 3


async def test_recommend_empty_when_everything_similar(
    fixture_index_path: Path, tmp_path: Path
) -> None:
    idx = _index(fixture_index_path, tmp_path)
    # An absurdly large threshold makes every candidate "too similar".
    recs = await recommend(
        "walk-fs",
        index=idx,
        client=None,
        sim_config=SimilarityConfig(distance=100),
        limit=10,
        verify_online=False,
    )
    assert recs == []


async def test_recommend_online_verification(fixture_index_path: Path, tmp_path: Path) -> None:
    idx = _index(fixture_index_path, tmp_path)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/pypi/traverse-fs/json":
            return httpx.Response(
                200,
                json={"info": {"name": "traverse-fs", "summary": "taken", "version": "1.0"}},
            )
        return httpx.Response(404)

    with respx.mock:
        respx.get(url__regex=r"https://pypi\.org/pypi/.*").mock(side_effect=handler)
        async with PyPIClient(backoff_multiplier=0.01) as client:
            recs = await recommend(
                "walk-fs",
                index=idx,
                client=client,
                sim_config=CFG,
                limit=10,
                verify_online=True,
            )
    assert recs
    assert all(r.verified_offline is False for r in recs)
    assert "traverse-fs" not in {r.normalized for r in recs}


async def test_recommend_without_index_uses_online_only() -> None:
    with respx.mock:
        respx.get(url__regex=r"https://pypi\.org/pypi/.*").mock(return_value=httpx.Response(404))
        async with PyPIClient(backoff_multiplier=0.01) as client:
            recs = await recommend(
                "walk-fs",
                index=None,
                client=client,
                sim_config=CFG,
                limit=5,
                verify_online=True,
            )
    assert len(recs) == 5
