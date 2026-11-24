"""Tests for the similarity heuristic and verdict classification."""

from __future__ import annotations

from pkgname.models import Status
from pkgname.similarity import (
    SimilarityConfig,
    classify,
    find_similar,
    generate_variants,
    is_too_similar,
    reason_for,
    segment_with_keys,
)

NAMES = [
    "walk-fs",
    "walk-files",
    "pywalkfs",
    "pyqt-search-bar",
    "requests",
    "flask",
    "my-pkg",
    "config-tools",
    "database",
    "filesystem",
    "traverse",
    "scan-dir",
    "package",
    "utils",
    "my-lib",
    "hello-world",
    "foo-bar",
    "zzz-unique",
]
NAMES_SET = set(NAMES)
CFG = SimilarityConfig(distance=2)


def _find(query: str, cfg: SimilarityConfig = CFG) -> list[str]:
    return [m.normalized for m in find_similar(query, NAMES, NAMES_SET, cfg)]


def test_exact_match() -> None:
    matches = find_similar("walk-fs", NAMES, NAMES_SET, CFG)
    assert matches[0].normalized == "walk-fs"
    assert matches[0].reason == "exact match"
    assert matches[0].distance == 0
    assert matches[0].origin == "local index (piwheels)"


def test_separator_variant() -> None:
    matches = find_similar("walkfs", NAMES, NAMES_SET, CFG)
    by_name = {m.normalized: m for m in matches}
    assert "walk-fs" in by_name
    assert by_name["walk-fs"].reason == "separator variant"
    assert by_name["walk-fs"].distance == 1
    # pywalkfs is within distance 2 AND differs by the confusable `py`
    # prefix; the affix rule takes precedence in the reason string.
    assert "pywalkfs" in by_name
    assert by_name["pywalkfs"].reason == "prefix variant `py`"


def test_edit_distance_reason() -> None:
    matches = find_similar("walk-fx", NAMES, NAMES_SET, CFG)
    by_name = {m.normalized: m for m in matches}
    assert by_name["walk-fs"].reason == "edit distance 1"


def test_suffix_affix_beyond_distance() -> None:
    # walk-fs-cli is distance 4 from walk-fs, but the -cli affix rule catches it.
    matches = find_similar("walk-fs-cli", NAMES, NAMES_SET, CFG)
    by_name = {m.normalized: m for m in matches}
    assert "walk-fs" in by_name
    assert by_name["walk-fs"].reason == "suffix variant `-cli`"
    assert by_name["walk-fs"].distance == 4


def test_prefix_affix_beyond_distance() -> None:
    matches = find_similar("py-walk-fs", NAMES, NAMES_SET, CFG)
    by_name = {m.normalized: m for m in matches}
    assert "walk-fs" in by_name
    assert by_name["walk-fs"].reason == "prefix variant `py-`"


def test_affix_addition_direction() -> None:
    # Query is the base; existing name carries the affix.
    matches = find_similar("walk-fs", NAMES, NAMES_SET, CFG)
    # walk-fs + -cli is not in the index, so no affix match expected here;
    # but querying the base of an existing affixed name must match.
    names = NAMES + ["walk-fs-cli"]
    matches = find_similar("walk-fs", names, set(names), CFG)
    by_name = {m.normalized: m for m in matches}
    assert "walk-fs-cli" in by_name
    assert by_name["walk-fs-cli"].reason == "suffix variant `-cli`"


def test_threshold_respected() -> None:
    strict = SimilarityConfig(distance=1)
    # walk-fxx is distance 2 from walk-fs and not affix-related.
    assert "walk-fs" not in _find("walk-fxx", strict)
    assert "walk-fs" in _find("walk-fxx", CFG)
    # Affix rules are independent of the distance threshold.
    assert "pywalkfs" in _find("walkfs", strict)


def test_no_matches_for_distant_name() -> None:
    assert _find("some-unique-random-name-xyz") == []


def test_matches_sorted_by_distance() -> None:
    matches = find_similar("walkfs", NAMES, NAMES_SET, CFG)
    distances = [m.distance for m in matches]
    assert distances == sorted(distances)


def test_is_too_similar() -> None:
    assert is_too_similar("walk-fs", NAMES, NAMES_SET, CFG)  # exact
    assert is_too_similar("walkfs", NAMES, NAMES_SET, CFG)  # distance 1
    assert is_too_similar("walk-fs-cli", NAMES, NAMES_SET, CFG)  # affix
    assert not is_too_similar("some-unique-random-name-xyz", NAMES, NAMES_SET, CFG)


def test_length_buckets_match_scan() -> None:
    buckets: dict[int, list[str]] = {}
    for name in NAMES:
        buckets.setdefault(len(name), []).append(name)
    plain = find_similar("walkfs", NAMES, NAMES_SET, CFG)
    bucketed = find_similar("walkfs", NAMES, NAMES_SET, CFG, length_buckets=buckets)
    assert [m.normalized for m in bucketed] == [m.normalized for m in plain]
    assert is_too_similar("walkfs", NAMES, NAMES_SET, CFG, length_buckets=buckets)
    assert not is_too_similar(
        "some-unique-random-name-xyz", NAMES, NAMES_SET, CFG, length_buckets=buckets
    )


def test_reason_for() -> None:
    assert reason_for("a", "a", 0, CFG) == "exact match"
    assert reason_for("walkfs", "walk-fs", 1, CFG) == "separator variant"
    assert reason_for("walk-fs", "walk-fs-cli", 4, CFG) == "suffix variant `-cli`"
    assert reason_for("walk-fs", "py-walk-fs", 3, CFG) == "prefix variant `py-`"
    assert reason_for("abc", "abd", 1, CFG) == "edit distance 1"


def test_segment_with_keys() -> None:
    assert segment_with_keys("walkfs", ["walk", "fs"]) == ["walk", "fs"]
    assert segment_with_keys("mywalkfs", ["walk", "fs"]) is None
    assert segment_with_keys("walk", ["walk", "fs"]) == ["walk"]


def test_generate_variants() -> None:
    variants = dict(generate_variants("walkfs", CFG))
    assert "walk-fs" in variants  # separator form via anchor split
    assert "walkfs-cli" in variants  # affix addition
    assert "py-walkfs" in variants  # prefix addition
    assert "alkfs" in variants  # single-char deletion
    assert "walkfs" not in variants  # never the name itself
    # Deduped.
    raw = generate_variants("walkfs", CFG)
    assert len(raw) == len({v for v, _ in raw})


def test_generate_variants_hyphenated() -> None:
    variants = dict(generate_variants("walk-fs", CFG))
    assert "walkfs" in variants  # joined form
    assert "walk-fs" not in variants


def test_classify() -> None:
    assert classify(exact=True, matches=[]) == Status.TAKEN
    assert classify(exact=False, matches=[]) == Status.AVAILABLE
    from pkgname.models import Match

    m = Match(name="walk-fs", normalized="walk-fs", reason="separator variant", distance=1)
    assert classify(exact=False, matches=[m]) == Status.REJECTED_SIMILAR
