"""Tests for PEP 503 normalization and PEP 508 validation."""

from __future__ import annotations

import pytest

from pkgname.normalize import InvalidNameError, normalize, validate, warnings_for


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Foo.Bar", "foo-bar"),
        ("foo_bar", "foo-bar"),
        ("FOO--BAR", "foo-bar"),
        ("a..b__c", "a-b-c"),
        ("walkfs", "walkfs"),
        ("PyQt-Search-Bar", "pyqt-search-bar"),
        ("a.-_b", "a-b"),
    ],
)
def test_normalize(raw: str, expected: str) -> None:
    assert normalize(raw) == expected


@pytest.mark.parametrize(
    "name",
    ["a", "ab", "a-b", "A1", "pkg.name", "pkg_name", "x" * 214, "0", "a0-._b"],
)
def test_validate_accepts(name: str) -> None:
    validate(name)  # must not raise


@pytest.mark.parametrize(
    "name",
    ["", "-abc", "abc-", ".abc", "abc.", "_abc", "abc_", "bad name!", "a b", "éclair", "x" * 215],
)
def test_validate_rejects(name: str) -> None:
    with pytest.raises(InvalidNameError):
        validate(name)


def test_validate_error_message_mentions_limit() -> None:
    with pytest.raises(InvalidNameError, match="214"):
        validate("x" * 215)


def test_warnings_stdlib_module() -> None:
    warnings = warnings_for("json")
    assert any("standard library" in w for w in warnings)


def test_warnings_keyword() -> None:
    warnings = warnings_for("class")
    assert any("keyword" in w for w in warnings)


def test_warnings_brand() -> None:
    warnings = warnings_for("requests")
    assert any("brand" in w for w in warnings)


def test_warnings_none_for_random_name() -> None:
    assert warnings_for("some-unique-random-name-xyz") == []
