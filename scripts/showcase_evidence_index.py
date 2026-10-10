"""Create a compact local verification index after showcase checks pass."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SHOWCASE = ROOT / "showcase"
REPORTS = SHOWCASE / "reports"


def digest(path: Path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    unit = json.loads((REPORTS / "vitest-report.json").read_text(encoding="utf-8"))
    e2e = json.loads((REPORTS / "playwright-report.json").read_text(encoding="utf-8"))
    artifacts = [
        SHOWCASE / "dist/index.html",
        SHOWCASE / "public/downloads/作品摘要.md",
        SHOWCASE / "public/downloads/evidence-summary.json",
        REPORTS / "showcase-desktop.png",
        REPORTS / "showcase-mobile.png",
    ]
    missing = [str(path.relative_to(ROOT)) for path in artifacts if not path.is_file()]
    if missing:
        raise SystemExit("missing showcase verification artifacts: " + ", ".join(missing))
    result = {
        "schema_version": "showcase-verification-v1",
        "generated_at": datetime.now(UTC).isoformat(),
        "showcase_version": "1.1.0",
        "product_version": "0.4.1",
        "build": "passed",
        "privacy_projection": "passed",
        "unit": {
            "passed": unit["numPassedTests"],
            "failed": unit["numFailedTests"],
            "suites_passed": unit["numPassedTestSuites"],
        },
        "e2e": {
            "passed": e2e["stats"]["expected"],
            "failed": e2e["stats"]["unexpected"],
            "skipped": e2e["stats"]["skipped"],
        },
        "artifacts": [
            {
                "path": str(path.relative_to(ROOT)).replace("\\", "/"),
                "bytes": path.stat().st_size,
                "sha256": digest(path),
            }
            for path in artifacts
        ],
        "scope": "local standalone showcase; no publication or control-console mutation",
    }
    (REPORTS / "showcase-verification.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps({"unit": result["unit"], "e2e": result["e2e"]}))


if __name__ == "__main__":
    main()
