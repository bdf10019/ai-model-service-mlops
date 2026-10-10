from __future__ import annotations

from pathlib import Path
from typing import Literal

from .context_quality import prompt_v5 as prompt_v51
from .context_quality import safety_v3 as safety_v31
from .context_quality_v3 import prompt_v5, safety_v3
from .legacy_domain import (
    Action,
    Event,
    QualityGate,
    Reason,
    Severity,
    StrictModel,
    Triage,
    fingerprint,
    fixture,
)
from .legacy_domain import InferRequest as LegacyInferRequest
from .legacy_domain import Profile as LegacyProfile
from .legacy_domain import prompt as legacy_prompt
from .legacy_domain import safety as legacy_safety


class Profile(LegacyProfile):
    prompt_version: Literal[
        "triage-service-v1",
        "triage-asset-v1",
        "triage-v2",
        "triage-v3",
        "triage-v4",
        "triage-v5",
        "triage-v5.1",
    ]


class InferRequest(LegacyInferRequest):
    review_retention: Literal["memory_only", "local_review_7_days"] = "memory_only"


def load_profiles(root: Path) -> dict[str, Profile]:
    profiles = {}
    for name in ("service", "asset"):
        p = Profile.model_validate_json((root / "profiles" / f"{name}.json").read_bytes())
        if p.id != name or not {"high", "critical", "unknown"}.issubset(p.review_severities):
            raise ValueError("設定不能降低安全底線")
        profiles[name] = p
    return profiles


def safety(raw, request, profile):
    if profile.prompt_version == "triage-v5.1":
        return safety_v31(raw, request, profile)
    return (
        safety_v3(raw, request, profile)
        if profile.prompt_version == "triage-v5"
        else legacy_safety(raw, request, profile)
    )


def prompt(request, profile):
    if profile.prompt_version == "triage-v5.1":
        return prompt_v51(request, profile)
    return (
        prompt_v5(request, profile)
        if profile.prompt_version == "triage-v5"
        else legacy_prompt(request, profile)
    )


__all__ = [
    "Action",
    "Event",
    "InferRequest",
    "QualityGate",
    "Reason",
    "Severity",
    "StrictModel",
    "Triage",
    "fingerprint",
    "fixture",
    "Profile",
    "load_profiles",
    "safety",
    "prompt",
]
