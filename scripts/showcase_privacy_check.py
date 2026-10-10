"""Fail if the standalone showcase leaks disallowed report fields or obvious secrets."""

from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SHOWCASE = ROOT / "showcase"
EVIDENCE = SHOWCASE / "public/downloads/evidence-summary.json"
FORBIDDEN_KEYS = {
    "owner",
    "username",
    "password",
    "password_hash",
    "session",
    "session_id",
    "token",
    "secret",
    "hostname",
    "raw_report",
}
FORBIDDEN_TEXT = [
    re.compile(r"C:\\Users\\", re.IGNORECASE),
    re.compile(r"bdf11", re.IGNORECASE),
    re.compile(r"(?:api[_-]?key|bearer)\s*[:=]\s*[A-Za-z0-9_-]{12,}", re.IGNORECASE),
]


def walk_keys(value, location="$"):
    problems = []
    if isinstance(value, dict):
        for key, item in value.items():
            if key.lower() in FORBIDDEN_KEYS:
                problems.append(f"{location}.{key}")
            problems.extend(walk_keys(item, f"{location}.{key}"))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            problems.extend(walk_keys(item, f"{location}[{index}]"))
    return problems


def main():
    evidence = json.loads(EVIDENCE.read_text(encoding="utf-8"))
    problems = walk_keys(evidence)
    roots = [SHOWCASE / "src", SHOWCASE / "public", SHOWCASE / "e2e"]
    suffixes = {".tsx", ".ts", ".css", ".html", ".md", ".json"}
    for root in roots:
        for path in root.rglob("*"):
            if not path.is_file() or path.suffix.lower() not in suffixes:
                continue
            content = path.read_text(encoding="utf-8")
            for pattern in FORBIDDEN_TEXT:
                if pattern.search(content):
                    problems.append(f"{path.relative_to(ROOT)} matches {pattern.pattern}")
    if problems:
        raise SystemExit("showcase privacy check failed:\n" + "\n".join(problems))
    print(
        json.dumps(
            {
                "status": "passed",
                "projection_schema": evidence["schema_version"],
                "datasets": len(evidence["quality"]["datasets"]),
                "public_cases": len(evidence["quality"]["cases"]),
                "note": "binary screenshots were reviewed separately from this text scan",
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
