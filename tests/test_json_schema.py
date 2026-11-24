"""Schema assertions for the JSON output (stable, versioned contract)."""

from __future__ import annotations

from pkgname.models import CheckResult, Match, Recommendation, Status

EXPECTED_TOP_KEYS = {
    "schema_version",
    "query",
    "normalized",
    "status",
    "source",
    "used_local_index",
    "warnings",
    "matches",
    "recommendations",
    "errors",
}
EXPECTED_MATCH_KEYS = {"name", "normalized", "reason", "distance", "summary", "version", "origin"}
EXPECTED_REC_KEYS = {"name", "normalized", "strategy", "score", "verified_offline"}


def _sample_result() -> CheckResult:
    return CheckResult(
        query="walkfs",
        normalized="walkfs",
        status=Status.REJECTED_SIMILAR,
        source="auto",
        used_local_index=True,
        warnings=[],
        matches=[
            Match(
                name="walk-fs",
                normalized="walk-fs",
                reason="separator variant",
                distance=1,
                summary="Filesystem walking utilities",
                version="1.4.2",
                origin="pypi",
            )
        ],
        recommendations=[
            Recommendation(
                name="walkfs-cli",
                normalized="walkfs-cli",
                strategy="suffix",
                score=0.91,
                verified_offline=False,
            )
        ],
        errors=[],
    )


def test_top_level_keys() -> None:
    payload = _sample_result().to_dict()
    assert set(payload) == EXPECTED_TOP_KEYS
    assert payload["schema_version"] == 2


def test_match_and_recommendation_keys() -> None:
    payload = _sample_result().to_dict()
    assert set(payload["matches"][0]) == EXPECTED_MATCH_KEYS
    assert set(payload["recommendations"][0]) == EXPECTED_REC_KEYS


def test_golden_payload() -> None:
    payload = _sample_result().to_dict()
    assert payload == {
        "schema_version": 2,
        "query": "walkfs",
        "normalized": "walkfs",
        "status": "REJECTED_SIMILAR",
        "source": "auto",
        "used_local_index": True,
        "warnings": [],
        "matches": [
            {
                "name": "walk-fs",
                "normalized": "walk-fs",
                "reason": "separator variant",
                "distance": 1,
                "summary": "Filesystem walking utilities",
                "version": "1.4.2",
                "origin": "pypi",
            }
        ],
        "recommendations": [
            {
                "name": "walkfs-cli",
                "normalized": "walkfs-cli",
                "strategy": "suffix",
                "score": 0.91,
                "verified_offline": False,
            }
        ],
        "errors": [],
    }


def test_status_values() -> None:
    for status in Status:
        result = CheckResult(
            query="x", normalized="x", status=status, source="auto", used_local_index=False
        )
        assert result.to_dict()["status"] == status.value


def test_defaults_are_empty_lists() -> None:
    result = CheckResult(
        query="x", normalized="x", status=Status.AVAILABLE, source="local", used_local_index=True
    )
    payload = result.to_dict()
    assert payload["warnings"] == []
    assert payload["matches"] == []
    assert payload["recommendations"] == []
    assert payload["errors"] == []
