"""Index explicit slice-three batches, retaining failures and first observation."""

from __future__ import annotations

import hashlib
import json
import xml.etree.ElementTree as ET
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPORTS = ROOT / "reports"
BATCHES = (
    "e2e-initial-batch.json",
    "e2e-operations-batch.json",
    "e2e-visual-batch.json",
)


def read(name):
    return json.loads((REPORTS / name).read_text(encoding="utf-8"))


def cases(suites):
    for suite in suites:
        for spec in suite.get("specs", []):
            for test in spec.get("tests", []):
                yield {
                    "file": suite.get("file", spec.get("file")),
                    "title": spec["title"],
                    "project": test.get("projectName"),
                    "status": test["status"],
                    "attempts": [r["status"] for r in test.get("results", [])],
                }
        yield from cases(suite.get("suites", []))


def main():
    coverage, batches = {}, []
    for name in BATCHES:
        data = read(name)
        rows = list(cases(data["suites"]))
        batches.append({"file": "reports/" + name, "stats": data["stats"], "cases": rows})
        for row in rows:
            key = (row["file"], row["title"], row["project"])
            record = coverage.setdefault(key, {**row, "batch_history": []})
            record["batch_history"].append({"file": name, "status": row["status"]})
            record["status"] = row["status"]
            record["attempts"] = row["attempts"]
    assert len(coverage) == 13, "Expected thirteen distinct slice-three journeys"
    assert all(
        r["status"] == "expected" and r["attempts"][-1] == "passed" for r in coverage.values()
    )
    backend = ET.parse(REPORTS / "backend-tests.xml").getroot().find("testsuite")
    frontend, first, actual = (
        read("frontend-tests.json"),
        read("holdout-first-report.json"),
        read("operations-real.json"),
    )
    assert first["evidence_use"] == "holdout_first_evaluation_frozen_candidate"
    assert actual["holdout"]["evidence_use"] == "observed_or_changed_candidate_development"
    assert len(actual["experiments"]["sync"]) == 2
    assert all(s["status"] == "synced" for s in actual["experiments"]["sync"])
    names = [
        *BATCHES,
        "backend-tests.xml",
        "frontend-tests.json",
        "holdout-first-report.json",
        "operations-real.json",
        "privacy-check.json",
        "container-readiness-0.3.0.json",
        "service-health-0.3.0.json",
        "real-release-journey.json",
    ]
    index = {
        "schema_version": "slice3-explicit-batches-v1",
        "generated_at": datetime.now(UTC).isoformat(),
        "scope": "local_0.3.0_operations_delivery_not_container_not_full_product_completion",
        "method": "Explicit chronological batches; final coverage is NOT a single thirteen-pass test run.",
        "batches": batches,
        "final_distinct_e2e_passed": len(coverage),
        "final_case_coverage": list(coverage.values()),
        "backend": dict(backend.attrib),
        "frontend": {"passed": frontend["numPassedTests"], "failed": frontend["numFailedTests"]},
        "first_holdout": {
            "report_sha256": first["report_sha256"],
            "use": first["evidence_use"],
            "metrics": first["metrics"],
        },
        "repeat_validation_holdout": {
            "report_sha256": actual["holdout"]["report_sha256"],
            "use": actual["holdout"]["evidence_use"],
        },
        "actual_loads": [
            {
                "id": actual[k]["id"],
                "settings": actual[k]["settings"],
                "metrics": actual[k]["metrics"],
            }
            for k in ("load_A", "load_B")
        ],
        "mlflow": [
            {
                k: s.get(k)
                for k in (
                    "run_id",
                    "experiment_id",
                    "source_sha256",
                    "status",
                    "attempts",
                    "worker_peak_rss_mib",
                    "tracking_backend",
                    "release_authority",
                )
            }
            for s in actual["experiments"]["sync"]
        ],
        "privacy": read("privacy-check.json"),
        "container": read("container-readiness-0.3.0.json"),
        "limitations": "Hash index is not an external signature. Fixture tests are not real GPU performance. First holdout failures retained; later observations are development evidence. MLflow has no model registry or release authority. Backup is isolated business metadata restore, not full disaster recovery.",
        "files": [
            {
                "path": "reports/" + n,
                "bytes": (REPORTS / n).stat().st_size,
                "sha256": hashlib.sha256((REPORTS / n).read_bytes()).hexdigest(),
            }
            for n in names
        ],
    }
    (REPORTS / "slice3-evidence-index.json").write_text(
        json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(
        json.dumps(
            {"distinct_e2e_passed": len(coverage), "files": len(names), "single_batch_claim": False}
        )
    )


if __name__ == "__main__":
    main()
