"""Shared defaults and vocabularies for pkgname.

Everything tunable lives here so CLI flags, tests, and docs stay in sync.
"""

from __future__ import annotations

SCHEMA_VERSION = 2

MAX_NAME_LENGTH = 214

DEFAULT_DISTANCE = 2
DEFAULT_LIMIT = 10
DEFAULT_TIMEOUT = 30.0
DEFAULT_CONCURRENCY = 8

DEFAULT_INDEX_PATH = "/sdcard/data/pip.json"
INDEX_ENV_VAR = "PKGNAME_INDEX"
CACHE_ENV_VAR = "PKGNAME_CACHE_DIR"

CONFUSABLE_AFFIXES = (
    "py-",
    "python-",
    "py",
    "-py",
    "-cli",
    "-tool",
    "-lib",
    "-kit",
    "-utils",
    "-dev",
    "-core",
    "get-",
    "-next",
    "-ng",
    "-alt",
)

RECOMMEND_PREFIXES = ("py-", "python-", "get-")
RECOMMEND_SUFFIXES = (
    "-cli",
    "-tool",
    "-lib",
    "-kit",
    "-utils",
    "-dev",
    "-core",
    "-next",
    "-ng",
    "-alt",
    "-lite",
    "-mini",
    "-py",
    "2",
    "3",
)

SYNONYMS = {
    "fs": ("filesystem",),
    "walk": ("traverse", "scan", "iter"),
    "dir": ("directory",),
    "pkg": ("package",),
    "cfg": ("config",),
    "util": ("utils",),
    "db": ("database",),
}

KNOWN_BRANDS = frozenset(
    {
        "requests",
        "numpy",
        "pandas",
        "django",
        "flask",
        "fastapi",
        "sqlalchemy",
        "pillow",
        "pytest",
        "click",
        "typer",
        "httpx",
        "pydantic",
        "matplotlib",
        "scipy",
        "tensorflow",
        "torch",
        "google",
        "microsoft",
        "apple",
        "amazon",
        "facebook",
        "twitter",
    }
)
