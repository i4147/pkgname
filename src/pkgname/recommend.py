"""Recommendation engine: candidate generation, validation, ranking.

When a name is TAKEN or REJECTED_SIMILAR (or on ``--recommend``), generate
alternative names and keep only those that are themselves AVAILABLE:

- Phase A: filter against the local index (exact + similarity rules).
- Phase B: confirm survivors against the PyPI API (404 + variant probe),
  skipped in ``--source local`` (then ``verified_offline`` is True).

Ranking is deterministic: score desc, then edit distance, strategy rank,
length, name. Generation is bounded and Phase B stops early once
``limit`` valid recommendations are found.
"""

from __future__ import annotations

import logging

from rapidfuzz.distance import Levenshtein

from .config import RECOMMEND_PREFIXES, RECOMMEND_SUFFIXES, SYNONYMS
from .index import LocalIndex
from .models import Recommendation
from .normalize import normalize
from .pypi import PyPIClient
from .similarity import SimilarityConfig, is_too_similar, segment_with_keys

logger = logging.getLogger(__name__)

STRATEGY_RANK = {
    "separator": 0,
    "suffix": 1,
    "prefix": 2,
    "synonym": 3,
    "reversal": 3,
    "abbreviation": 4,
}
MAX_CANDIDATES = 2000


def _segments(name):
    parts = name.split("-")
    if len(parts) == 1:
        split = segment_with_keys(name, list(SYNONYMS))
        if split and len(split) > 1:
            return split
    return parts


def _drop_trailing_vowels(segment):
    result = segment
    while len(result) > 1 and result[-1] in "aeiou":
        result = result[:-1]
    return result


def _drop_vowels(segment):
    return "".join(ch for ch in segment if ch not in "aeiou")


def generate_candidates(name):
    candidates = {}

    def add(candidate, strategy):
        normalized = normalize(candidate)
        if normalized and normalized != name and normalized not in candidates:
            candidates[normalized] = strategy

    segments = _segments(name)

    if len(segments) > 1:
        add("".join(segments), "separator")
        add("-".join(segments), "separator")

    for suffix in RECOMMEND_SUFFIXES:
        add(name + suffix, "suffix")
    for prefix in RECOMMEND_PREFIXES:
        add(prefix + name, "prefix")

    for i, segment in enumerate(segments):
        for synonym in SYNONYMS.get(segment, ()):
            replaced = segments.copy()
            replaced[i] = synonym
            add("-".join(replaced), "synonym")

    if len(segments) > 1:
        add("-".join(reversed(segments)), "reversal")

    add("-".join(_drop_trailing_vowels(s) for s in segments), "abbreviation")
    vowelless = [_drop_vowels(s) for s in segments]
    if all(len(s) >= 2 for s in vowelless):
        add("-".join(vowelless), "abbreviation")

    ordered = sorted(candidates.items(), key=lambda kv: (STRATEGY_RANK.get(kv[1], 5), kv[0]))
    return ordered[:MAX_CANDIDATES]


def score_candidate(original, candidate, strategy):
    distance = Levenshtein.distance(original, candidate)
    rank = STRATEGY_RANK.get(strategy, 5)
    score = 1.0 - 0.08 * distance - 0.04 * rank - 0.005 * len(candidate)
    return round(max(0.0, score), 4)


async def recommend(
    name,
    *,
    index,
    client,
    sim_config,
    limit,
    verify_online,
):
    candidates = generate_candidates(name)

    survivors = []
    for candidate, strategy in candidates:
        if (
            index is not None
            and index.available
            and is_too_similar(
                candidate,
                index.names_list,
                index.names_set,
                sim_config,
                length_buckets=index.length_buckets,
            )
        ):
            continue
        survivors.append((candidate, strategy))

    survivors.sort(
        key=lambda cs: (
            -score_candidate(name, cs[0], cs[1]),
            Levenshtein.distance(name, cs[0]),
            STRATEGY_RANK.get(cs[1], 5),
            len(cs[0]),
            cs[0],
        )
    )

    results = []
    for candidate, strategy in survivors:
        if len(results) >= limit:
            break
        verified_offline = True
        if verify_online and client is not None:
            if await client.get_project(candidate) is not None:
                continue
            if await client.probe_variants(candidate, sim_config):
                continue
            verified_offline = False
        results.append(
            Recommendation(
                name=candidate,
                normalized=candidate,
                strategy=strategy,
                score=score_candidate(name, candidate, strategy),
                verified_offline=verified_offline,
            )
        )
    return results
