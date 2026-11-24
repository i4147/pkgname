"""Local piwheels index: streaming loader, persistent cache, stats.

The index is a ``pip.json`` snapshot from https://piwheels.org/packages.json:
a JSON array of arrays, one per package, e.g. ``["dgio", 0, 26]``. Only
element [0] (the name) is used; the other fields are piwheels-specific
counters.

**Caveat:** piwheels only lists packages for which wheels were built/known
to piwheels. It is *not* the complete PyPI namespace. ``name in index``
means almost certainly taken on PyPI; ``name not in index`` means *unknown*
and must be confirmed against the live PyPI API unless the user explicitly
chose ``--source local``. See README "Assumptions & limitations".

The published file is large (tens of MB) and minified, so it is parsed with
a streaming tokenizer that splits top-level array elements incrementally —
never via ``json.load`` on the whole file. (A line-oriented layout with one
entry per line is handled identically.) Normalized names are cached to disk
(msgpack when available, newline text otherwise) keyed by the source file's
sha256+mtime+size, so warm runs load in well under 200 ms.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .config import CACHE_ENV_VAR, DEFAULT_INDEX_PATH, INDEX_ENV_VAR
from .normalize import normalize

logger = logging.getLogger(__name__)

try:
    import msgpack

    _HAS_MSGPACK = True
except ImportError:
    msgpack = None
    _HAS_MSGPACK = False


class IndexUnavailableError(Exception):
    pass


@dataclass
class IndexStats:

    error = None

    def to_dict(self):
        return {
            "source_path": self.source_path,
            "file_size": self.file_size,
            "sha256": self.sha256,
            "entry_count": self.entry_count,
            "cache_path": self.cache_path,
            "load_time_ms": round(self.load_time_ms, 2),
            "available": self.available,
            "error": self.error,
        }


def resolve_index_path(path = None):
    if path is not None:
        return Path(path)
    env = os.environ.get(INDEX_ENV_VAR)
    if env:
        return Path(env)
    return Path(DEFAULT_INDEX_PATH)


def default_cache_dir():
    override = os.environ.get(CACHE_ENV_VAR)
    if override:
        return Path(override)
    base = os.environ.get("XDG_CACHE_HOME") or str(Path.home() / ".cache")
    return Path(base) / "pkgname"


class LocalIndex:

    def __init__(
        self,
        path,
        *,
        cache_dir = None,
        use_cache = True,
    ):
        self.path = Path(path)
        self.cache_dir = Path(cache_dir) if cache_dir is not None else default_cache_dir()
        self.use_cache = use_cache
        self._names_set = None
        self._names_list = []
        self._length_buckets = None
        self.available = False
        self.error = None
        self._sha256 = ""
        self._file_size = 0
        self._mtime_ns = 0
        self._load_time_ms = 0.0
        self._cache_path = None
        self._loaded = False

    def load(self):
        if self._loaded:
            return self
        self._loaded = True
        start = time.perf_counter()

        if not self.path.is_file():
            self.error = f"index file not found: {self.path}"
            logger.warning(self.error)
            self._load_time_ms = (time.perf_counter() - start) * 1000
            return self

        try:
            stat = self.path.stat()
            self._file_size = stat.st_size
            self._mtime_ns = stat.st_mtime_ns
            self._sha256 = self._hash_source()
        except OSError as exc:
            self.error = f"index file unreadable: {exc}"
            logger.warning(self.error)
            self._load_time_ms = (time.perf_counter() - start) * 1000
            return self

        self._cache_path = self._cache_file_for(self._sha256)
        if self.use_cache and self._try_load_cache():
            self.available = True
            self._load_time_ms = (time.perf_counter() - start) * 1000
            logger.debug("loaded %d names from cache %s", len(self._names_list), self._cache_path)
            return self

        names, skipped = self._parse_source()
        if not names:
            self.error = "index file appears corrupt or empty (no package entries parsed)"
            logger.warning("%s: %s", self.error, self.path)
            self._load_time_ms = (time.perf_counter() - start) * 1000
            return self

        self._names_list = names
        self.available = True
        if skipped:
            logger.warning("skipped %d unparseable lines in %s", skipped, self.path)
        self._write_cache()
        self._load_time_ms = (time.perf_counter() - start) * 1000
        logger.debug("parsed %d names from %s in %.1f ms", len(names), self.path, self._load_time_ms)
        return self

    def __contains__(self, normalized_name):
        return normalized_name in self.names_set

    @property
    def names_set(self):
        if self._names_set is None:
            self._names_set = set(self._names_list)
        return self._names_set

    @property
    def names_list(self):
        return self._names_list

    @property
    def length_buckets(self):
        if self._length_buckets is None:
            buckets = {}
            for name in self._names_list:
                buckets.setdefault(len(name), []).append(name)
            self._length_buckets = buckets
        return self._length_buckets

    def stats(self):
        if not self._loaded:
            self.load()
        return IndexStats(
            source_path=str(self.path),
            file_size=self._file_size,
            sha256=self._sha256,
            entry_count=len(self._names_list),
            cache_path=str(self._cache_path) if self._cache_path else None,
            load_time_ms=self._load_time_ms,
            available=self.available,
            error=self.error,
        )

    def _hash_source(self):
        digest = hashlib.sha256()
        with self.path.open("rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                digest.update(chunk)
        return digest.hexdigest()

    def _cache_file_for(self, sha256):
        suffix = "msgpack" if _HAS_MSGPACK else "txt"
        return self.cache_dir / f"index-{sha256}.{suffix}"

    def _cache_meta(self):
        return {
            "v": 1,
            "sha256": self._sha256,
            "mtime_ns": self._mtime_ns,
            "size": self._file_size,
        }

    def _try_load_cache(self):
        cache_path = self._cache_path
        if cache_path is None or not cache_path.is_file():
            return False
        try:
            if _HAS_MSGPACK and cache_path.suffix == ".msgpack":
                assert msgpack is not None
                data = msgpack.unpackb(cache_path.read_bytes(), raw=False)
                meta = {k: data[k] for k in ("sha256", "mtime_ns", "size")}
                names = list(data["names"])
            else:
                lines = cache_path.read_text(encoding="utf-8").splitlines()
                meta = json.loads(lines[0])
                names = lines[1:]
            if (
                meta.get("sha256") != self._sha256
                or meta.get("size") != self._file_size
                or meta.get("mtime_ns") != self._mtime_ns
            ):
                return False
            if not names:
                return False
            self._names_list = names
            return True
        except Exception as exc:
            logger.debug("ignoring unreadable cache %s: %s", cache_path, exc)
            return False

    def _write_cache(self):
        cache_path = self._cache_path
        if cache_path is None:
            return
        try:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            tmp = cache_path.with_suffix(cache_path.suffix + ".tmp")
            if _HAS_MSGPACK and msgpack is not None:
                tmp.write_bytes(msgpack.packb({**self._cache_meta(), "names": self._names_list}))
            else:
                tmp.write_text(
                    json.dumps(self._cache_meta()) + "\n" + "\n".join(self._names_list) + "\n",
                    encoding="utf-8",
                )
            tmp.replace(cache_path)
        except OSError as exc:
            logger.warning("could not write index cache %s: %s", cache_path, exc)

    def _parse_source(self):
        names = []
        seen = set()
        skipped = 0
        try:
            with self.path.open("r", encoding="utf-8") as fh:
                for entry_text in self._iter_entry_texts(fh):
                    parsed = self._names_from_entry(entry_text)
                    if parsed is None:
                        skipped += 1
                        continue
                    for raw in parsed:
                        name = normalize(raw)
                        if name and name not in seen:
                            seen.add(name)
                            names.append(name)
        except OSError as exc:
            logger.warning("error reading index %s: %s", self.path, exc)
            return [], skipped
        return names, skipped

    @staticmethod
    def _iter_entry_texts(fh):
        depth = 0
        started = False
        in_string = False
        escape = False
        buf = []
        for chunk in iter(lambda: fh.read(1 << 16), ""):
            for ch in chunk:
                if not started:
                    if ch == "[":
                        started = True
                        depth = 1
                    continue
                if in_string:
                    buf.append(ch)
                    if escape:
                        escape = False
                    elif ch == "\\":
                        escape = True
                    elif ch == '"':
                        in_string = False
                    continue
                if ch == '"':
                    in_string = True
                    buf.append(ch)
                elif ch == "[":
                    depth += 1
                    buf.append(ch)
                elif ch == "]":
                    depth -= 1
                    if depth == 0:
                        text = "".join(buf).strip().rstrip(",").strip()
                        if text:
                            yield text
                        return
                    buf.append(ch)
                elif ch == "," and depth == 1:
                    text = "".join(buf).strip()
                    if text:
                        yield text
                    buf = []
                else:
                    buf.append(ch)
        text = "".join(buf).strip().rstrip(",").strip()
        if text:
            yield text

    @staticmethod
    def _names_from_entry(text):
        try:
            entry = json.loads(text)
        except json.JSONDecodeError:
            start = text.rfind("[")
            if start <= 0:
                return None
            try:
                entry = json.loads(text[start:])
            except json.JSONDecodeError:
                return None
        if isinstance(entry, list) and entry and isinstance(entry[0], str):
            return [entry[0]]
        return None


_instances = {}


def get_index(path = None):
    resolved = resolve_index_path(path)
    key = str(resolved)
    if key not in _instances:
        _instances[key] = LocalIndex(resolved).load()
    return _instances[key]


def reset_index():
    _instances.clear()
