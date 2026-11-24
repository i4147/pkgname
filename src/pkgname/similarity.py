"""Similarity heuristics: fuzzy scan, affix/separator rules, verdicts.

PyPI's exact upload-time squatting rule is not public and may change, so
this module implements a conservative, configurable heuristic (see
:class:`SimilarityConfig`). All inputs are PEP 503-normalized names.
"""

from __future__ import annotations

from dataclasses import dataclass

from rapidfuzz import process
from rapidfuzz.distance import Levenshtein

from .config import CONFUSABLE_AFFIXES, SYNONYMS
from .models import Match, Status


@dataclass(frozen=True)
class SimilarityConfig:

    distance = 2
    affixes = CONFUSABLE_AFFIXES


def reason_for(query, candidate, distance, config):
    if distance == 0:
        return "exact match"
    if query.replace("-", "") == candidate.replace("-", ""):
        return "separator variant"
    for affix in config.affixes:
        if candidate == query + affix or query == candidate + affix:
            return f"suffix variant `{affix}`"
        if candidate == affix + query or query == affix + candidate:
            return f"prefix variant `{affix}`"
    return f"edit distance {distance}"


def _length_filtered(
    query,
    names,
    distance,
    buckets = None,
):
    if buckets is not None:
        pool = []
        for length in range(len(query) - distance, len(query) + distance + 1):
            pool.extend(buckets.get(length, ()))
        return pool
    return [n for n in names if abs(len(n) - len(query)) <= distance]


def find_similar(
    query,
    names_list,
    names_set,
    config,
    *,
    origin = "local index (piwheels)",
    limit = 50,
    length_buckets = None,
):
    found = {}

    def add(candidate):
        if candidate in found:
            return
        d = Levenshtein.distance(query, candidate)
        found[candidate] = Match(
            name=candidate,
            normalized=candidate,
            reason=reason_for(query, candidate, d, config),
            distance=d,
            origin=origin,
        )

    pool = _length_filtered(query, names_list, config.distance, length_buckets)
    if pool:
        hits = process.extract(
            query,
            pool,
            scorer=Levenshtein.distance,
            limit=limit,
            score_cutoff=config.distance,
        )
        for candidate, _score, _idx in hits:
            if Levenshtein.distance(query, candidate) <= config.distance:
                add(candidate)

    for affix in config.affixes:
        for variant in (query + affix, affix + query):
            if variant in names_set:
                add(variant)
        if query.startswith(affix) and len(query) > len(affix):
            stripped = query[len(affix) :]
            if stripped in names_set:
                add(stripped)
        if query.endswith(affix) and len(query) > len(affix):
            stripped = query[: -len(affix)]
            if stripped in names_set:
                add(stripped)

    return sorted(found.values(), key=lambda m: (m.distance, m.normalized))[:limit]


def is_too_similar(
    candidate,
    names_list,
    names_set,
    config,
    *,
    length_buckets = None,
):
    if candidate in names_set:
        return True
    for affix in config.affixes:
        if candidate + affix in names_set or affix + candidate in names_set:
            return True
        if candidate.startswith(affix) and candidate[len(affix) :] in names_set:
            return True
        if candidate.endswith(affix) and candidate[: -len(affix)] in names_set:
            return True
    pool = _length_filtered(candidate, names_list, config.distance, length_buckets)
    if pool:
        hit = process.extractOne(candidate, pool, scorer=Levenshtein.distance, score_cutoff=config.distance)
        if hit is not None and Levenshtein.distance(candidate, hit[0]) <= config.distance:
            return True
    return False


def segment_with_keys(name, keys):
    ordered = sorted(keys, key=len, reverse=True)
    n = len(name)
    dp = [None] * (n + 1)
    dp[n] = []
    for i in range(n - 1, -1, -1):
        for key in ordered:
            if name.startswith(key, i):
                rest = dp[i + len(key)]
                if rest is not None:
                    dp[i] = [key, *rest]
                    break
    return dp[0]


def generate_variants(name, config):
    variants = {}

    def add(variant, hint):
        if variant and variant != name:
            variants.setdefault(variant, hint)

    segments = name.split("-")
    if len(segments) == 1:
        split = segment_with_keys(name, list(SYNONYMS))
        if split and len(split) > 1:
            add("-".join(split), "separator variant")
    elif len(segments) > 1:
        add("".join(segments), "separator variant")

    for affix in config.affixes:
        add(name + affix, f"suffix variant `{affix}`")
        add(affix + name, f"prefix variant `{affix}`")
        if name.startswith(affix) and len(name) > len(affix):
            add(name[len(affix) :], f"prefix variant `{affix}`")
        if name.endswith(affix) and len(name) > len(affix):
            add(name[: -len(affix)], f"suffix variant `{affix}`")

    for i in range(len(name)):
        add(name[:i] + name[i + 1 :], "edit distance 1")

    return sorted(variants.items())


def classify(*, exact, matches):
    if exact:
        return Status.TAKEN
    if matches:
        return Status.REJECTED_SIMILAR
    return Status.AVAILABLE
