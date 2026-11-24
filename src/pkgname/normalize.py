"""PEP 503 normalization and PEP 508 name validation.

Pure functions, no I/O. Everything in pkgname funnels through
:func:`normalize` so comparisons are always on normalized names.
"""

from __future__ import annotations

import keyword
import re
import sys

from .config import KNOWN_BRANDS, MAX_NAME_LENGTH

_NAME_RE = re.compile(r"^([a-zA-Z0-9]|[a-zA-Z0-9][a-zA-Z0-9._-]*[a-zA-Z0-9])$")
_SEPARATOR_RE = re.compile(r"[-_.]+")


class InvalidNameError(ValueError):
    pass


def normalize(name):
    return _SEPARATOR_RE.sub("-", name).lower()


def validate(name):
    if not name:
        raise InvalidNameError("package name must not be empty")
    if len(name) > MAX_NAME_LENGTH:
        raise InvalidNameError(f"package name is {len(name)} characters; PyPI allows at most {MAX_NAME_LENGTH}")
    if not _NAME_RE.match(name):
        raise InvalidNameError(
            f"invalid package name {name!r}: names must start and end with a letter "
            "or digit and may only contain letters, digits, '-', '_' and '.'"
        )


def warnings_for(normalized_name):
    warnings = []
    if normalized_name in sys.stdlib_module_names:
        warnings.append(f"{normalized_name!r} is a Python standard library module name")
    if normalized_name in keyword.kwlist:
        warnings.append(f"{normalized_name!r} is a Python keyword")
    if normalized_name in KNOWN_BRANDS:
        warnings.append(f"{normalized_name!r} is a well-known brand/project name")
    return warnings
