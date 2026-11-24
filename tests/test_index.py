"""Tests for the streaming piwheels index loader and its cache."""

from __future__ import annotations

import json
import re
from pathlib import Path
from unittest.mock import patch

import pytest

import pkgname.index as index_mod
from pkgname.index import LocalIndex, get_index, reset_index, resolve_index_path

FIXTURE_COUNT = 20


def _load(path: Path, tmp_path: Path, **kwargs: object) -> LocalIndex:
    return LocalIndex(path, cache_dir=tmp_path, **kwargs).load()  # type: ignore[arg-type]


def test_load_fixture(fixture_index_path: Path, tmp_path: Path) -> None:
    idx = _load(fixture_index_path, tmp_path)
    assert idx.available
    assert idx.error is None
    assert len(idx.names_list) == FIXTURE_COUNT
    assert "walk-fs" in idx
    assert "pyqt-search-bar" in idx
    # Fixture contains "Foo_Bar" -> normalized on load.
    assert "foo-bar" in idx
    assert "Foo_Bar" not in idx


def test_stats(fixture_index_path: Path, tmp_path: Path) -> None:
    idx = _load(fixture_index_path, tmp_path)
    stats = idx.stats()
    assert stats.available
    assert stats.entry_count == FIXTURE_COUNT
    assert stats.source_path == str(fixture_index_path)
    assert stats.file_size == fixture_index_path.stat().st_size
    assert re.fullmatch(r"[0-9a-f]{64}", stats.sha256)
    assert stats.cache_path is not None
    assert stats.load_time_ms >= 0
    as_dict = stats.to_dict()
    assert as_dict["entry_count"] == FIXTURE_COUNT
    assert as_dict["available"] is True


def test_cache_hit_skips_parse(fixture_index_path: Path, tmp_path: Path) -> None:
    first = _load(fixture_index_path, tmp_path)
    assert first.available
    cache_file = Path(first.stats().cache_path or "")
    assert cache_file.is_file()

    with patch.object(LocalIndex, "_parse_source", side_effect=AssertionError("must use cache")):
        second = _load(fixture_index_path, tmp_path)
    assert second.available
    assert second.names_list == first.names_list


def test_cache_invalidation_on_content_change(tmp_path: Path) -> None:
    src = tmp_path / "pip.json"
    src.write_text('[\n["alpha",0,1],\n["beta",0,2]\n]\n', encoding="utf-8")
    first = _load(src, tmp_path / "cache")
    assert first.available and "alpha" in first

    src.write_text('[\n["alpha",0,1],\n["gamma",0,3]\n]\n', encoding="utf-8")
    second = _load(src, tmp_path / "cache")
    assert second.available
    assert "gamma" in second
    assert "beta" not in second


def test_use_cache_false_forces_parse(fixture_index_path: Path, tmp_path: Path) -> None:
    _load(fixture_index_path, tmp_path)
    calls: list[int] = []
    orig = LocalIndex._parse_source

    def counting(self: LocalIndex) -> tuple[list[str], int]:
        calls.append(1)
        return orig(self)

    with patch.object(LocalIndex, "_parse_source", counting):
        idx = LocalIndex(fixture_index_path, cache_dir=tmp_path, use_cache=False).load()
    assert idx.available
    assert calls


def test_missing_file_never_crashes(tmp_path: Path) -> None:
    idx = _load(tmp_path / "does-not-exist.json", tmp_path)
    assert not idx.available
    assert idx.error is not None and "not found" in idx.error
    assert idx.stats().entry_count == 0
    assert "anything" not in idx


def test_corrupt_file(tmp_path: Path) -> None:
    src = tmp_path / "pip.json"
    src.write_text("this is not json {{{ \n\x00\x01 garbage\n", encoding="utf-8")
    idx = _load(src, tmp_path)
    assert not idx.available
    assert idx.error is not None and "corrupt" in idx.error


def test_empty_file_is_treated_as_corrupt(tmp_path: Path) -> None:
    src = tmp_path / "pip.json"
    src.write_text("", encoding="utf-8")
    idx = _load(src, tmp_path)
    assert not idx.available


def test_partially_corrupt_file_keeps_good_lines(tmp_path: Path) -> None:
    src = tmp_path / "pip.json"
    src.write_text('[\n["alpha",0,1],\nnot json at all\n["beta",0,2]\n]\n', encoding="utf-8")
    idx = _load(src, tmp_path)
    assert idx.available
    assert "alpha" in idx and "beta" in idx


def test_single_line_array_layout(tmp_path: Path) -> None:
    src = tmp_path / "pip.json"
    src.write_text('[["alpha",0,1],["beta",0,2]]', encoding="utf-8")
    idx = _load(src, tmp_path)
    assert idx.available
    assert "alpha" in idx and "beta" in idx


def test_text_cache_fallback(
    fixture_index_path: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(index_mod, "_HAS_MSGPACK", False)
    first = _load(fixture_index_path, tmp_path)
    assert first.available
    cache_file = Path(first.stats().cache_path or "")
    assert cache_file.suffix == ".txt"

    with patch.object(LocalIndex, "_parse_source", side_effect=AssertionError("must use cache")):
        second = _load(fixture_index_path, tmp_path)
    assert second.available
    assert second.names_list == first.names_list


def test_corrupt_cache_rebuilds(fixture_index_path: Path, tmp_path: Path) -> None:
    first = _load(fixture_index_path, tmp_path)
    cache_file = Path(first.stats().cache_path or "")
    cache_file.write_bytes(b"\x00\x01 not a valid cache \xff")
    second = _load(fixture_index_path, tmp_path)
    assert second.available
    assert second.names_list == first.names_list


def test_singleton(fixture_index_path: Path, tmp_path: Path) -> None:
    a = get_index(fixture_index_path)
    b = get_index(fixture_index_path)
    assert a is b
    reset_index()
    c = get_index(fixture_index_path)
    assert c is not a


def test_resolve_index_path_precedence(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    explicit = tmp_path / "explicit.json"
    env_path = tmp_path / "env.json"
    monkeypatch.setenv("PKGNAME_INDEX", str(env_path))
    assert resolve_index_path(explicit) == explicit
    assert resolve_index_path() == env_path
    monkeypatch.delenv("PKGNAME_INDEX")
    assert resolve_index_path() == Path("./pip.json")


def test_cache_meta_roundtrip(fixture_index_path: Path, tmp_path: Path) -> None:
    idx = _load(fixture_index_path, tmp_path)
    cache_file = Path(idx.stats().cache_path or "")
    raw = cache_file.read_bytes()
    if cache_file.suffix == ".msgpack":
        import msgpack

        data = msgpack.unpackb(raw, raw=False)
        assert data["sha256"] == idx.stats().sha256
        assert len(data["names"]) == FIXTURE_COUNT
    else:
        lines = raw.decode("utf-8").splitlines()
        meta = json.loads(lines[0])
        assert meta["sha256"] == idx.stats().sha256
        assert len(lines) - 1 == FIXTURE_COUNT


def test_default_cache_dir_env_override_and_xdg(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("PKGNAME_CACHE_DIR", raising=False)
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "xdg"))
    assert index_mod.default_cache_dir() == tmp_path / "xdg" / "pkgname"
    monkeypatch.delenv("XDG_CACHE_HOME", raising=False)
    assert index_mod.default_cache_dir() == Path.home() / ".cache" / "pkgname"


def test_load_twice_returns_self(fixture_index_path: Path, tmp_path: Path) -> None:
    idx = LocalIndex(fixture_index_path, cache_dir=tmp_path)
    assert idx.load() is idx.load()


def test_stats_loads_implicitly(fixture_index_path: Path, tmp_path: Path) -> None:
    idx = LocalIndex(fixture_index_path, cache_dir=tmp_path)
    stats = idx.stats()
    assert stats.available
    assert stats.entry_count == FIXTURE_COUNT


def test_unreadable_hash_failure_is_graceful(
    fixture_index_path: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _boom(self: LocalIndex) -> str:
        raise OSError("denied")

    monkeypatch.setattr(LocalIndex, "_hash_source", _boom)
    idx = LocalIndex(fixture_index_path, cache_dir=tmp_path).load()
    assert not idx.available
    assert idx.error is not None and "unreadable" in idx.error


def test_unsalvageable_entry_counts_as_skipped(tmp_path: Path) -> None:
    src = tmp_path / "pip.json"
    # The garbage glue makes both the broken element and the entry after it
    # unparseable; the good first entry survives.
    src.write_text('[\n["good",0,1],\n[broken,\n["b",0,1]\n]\n', encoding="utf-8")
    idx = _load(src, tmp_path)
    assert idx.available
    assert "good" in idx


def test_salvage_retry_failure_counts_as_skipped(tmp_path: Path) -> None:
    src = tmp_path / "pip.json"
    src.write_text('[\n["ok",0,1],\ngarbage[nope]\n]', encoding="utf-8")
    idx = _load(src, tmp_path)
    assert "ok" in idx


def test_escaped_backslash_in_string(tmp_path: Path) -> None:
    src = tmp_path / "pip.json"
    src.write_text('[["a\\\\b",0,1]]', encoding="utf-8")
    idx = _load(src, tmp_path)
    assert idx.available
    assert idx.names_list  # entry parsed despite the escape sequence


def test_truncated_stream_yields_tail(tmp_path: Path) -> None:
    src = tmp_path / "pip.json"
    src.write_text('[["alpha",0,1]', encoding="utf-8")  # no closing bracket
    idx = _load(src, tmp_path)
    assert "alpha" in idx


def _rewrite_cache(cache_file: Path, mutate: object) -> None:
    import msgpack

    data = msgpack.unpackb(cache_file.read_bytes(), raw=False)
    if callable(mutate):
        data.update(mutate(data))  # type: ignore[operator]
    cache_file.write_bytes(msgpack.packb(data))


def test_cache_meta_mismatch_rebuilds(fixture_index_path: Path, tmp_path: Path) -> None:
    pytest.importorskip("msgpack")
    first = _load(fixture_index_path, tmp_path)
    cache_file = Path(first.stats().cache_path or "")
    if cache_file.suffix == ".msgpack":
        _rewrite_cache(cache_file, lambda d: {"mtime_ns": 1})
        calls: list[int] = []
        orig = LocalIndex._parse_source

        def counting(self: LocalIndex) -> tuple[list[str], int]:
            calls.append(1)
            return orig(self)

        with patch.object(LocalIndex, "_parse_source", counting):
            second = _load(fixture_index_path, tmp_path)
        assert calls
        assert second.names_list == first.names_list


def test_cache_with_zero_names_rebuilds(fixture_index_path: Path, tmp_path: Path) -> None:
    pytest.importorskip("msgpack")
    first = _load(fixture_index_path, tmp_path)
    cache_file = Path(first.stats().cache_path or "")
    if cache_file.suffix == ".msgpack":
        _rewrite_cache(cache_file, lambda d: {"names": []})
        calls: list[int] = []
        orig = LocalIndex._parse_source

        def counting(self: LocalIndex) -> tuple[list[str], int]:
            calls.append(1)
            return orig(self)

        with patch.object(LocalIndex, "_parse_source", counting):
            second = _load(fixture_index_path, tmp_path)
        assert calls
        assert second.names_list == first.names_list


def test_write_cache_without_path_is_noop() -> None:
    idx = LocalIndex(Path("ignored.json"), cache_dir=Path("ignored-cache"))
    assert idx._write_cache() is None


def test_write_cache_oserror_is_logged(
    fixture_index_path: Path, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    blocker = tmp_path / "cache-blocker"
    blocker.write_text("i am a file, not a directory", encoding="utf-8")
    idx = LocalIndex(fixture_index_path, cache_dir=blocker).load()
    assert idx.available  # cache-write failure must not break loading
    assert any("could not write index cache" in r.message for r in caplog.records)


def test_parse_oserror_is_graceful(
    fixture_index_path: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _boom(fh: object) -> object:
        raise OSError("boom")

    monkeypatch.setattr(LocalIndex, "_iter_entry_texts", staticmethod(_boom))
    idx = LocalIndex(fixture_index_path, cache_dir=tmp_path).load()
    assert not idx.available
