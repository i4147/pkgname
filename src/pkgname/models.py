"""Data model: verdicts, matches, recommendations, and the JSON schema."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from .config import SCHEMA_VERSION


class Status(str, Enum):

    AVAILABLE = "AVAILABLE"
    TAKEN = "TAKEN"
    REJECTED_SIMILAR = "REJECTED_SIMILAR"


@dataclass
class Match:

    summary = None
    version = None
    origin = "local index (piwheels)"

    def to_dict(self):
        return {
            "name": self.name,
            "normalized": self.normalized,
            "reason": self.reason,
            "distance": self.distance,
            "summary": self.summary,
            "version": self.version,
            "origin": self.origin,
        }


@dataclass
class Recommendation:

    verified_offline = False

    def to_dict(self):
        return {
            "name": self.name,
            "normalized": self.normalized,
            "strategy": self.strategy,
            "score": self.score,
            "verified_offline": self.verified_offline,
        }


@dataclass
class CheckResult:

    warnings = field(default_factory=list)
    matches = field(default_factory=list)
    recommendations = field(default_factory=list)
    errors = field(default_factory=list)

    def to_dict(self):
        return {
            "schema_version": SCHEMA_VERSION,
            "query": self.query,
            "normalized": self.normalized,
            "status": self.status.value,
            "source": self.source,
            "used_local_index": self.used_local_index,
            "warnings": list(self.warnings),
            "matches": [m.to_dict() for m in self.matches],
            "recommendations": [r.to_dict() for r in self.recommendations],
            "errors": list(self.errors),
        }
