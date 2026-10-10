"""Index this verification run without claiming old measurements as new evidence."""

from __future__ import annotations

import hashlib
import json
import statistics
import xml.etree.ElementTree as ET
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main():
    reports = ROOT / "reports"
    backend = ET.parse(reports / "backend-tests.xml").getroot().find("testsuite")
    frontend = json.loads((reports / "frontend-tests.json").read_text(encoding="utf-8"))
    e2e = json.loads((reports / "e2e-results.json").read_text(encoding="utf-8"))["stats"]
    privacy = json.loads((reports / "privacy-check.json").read_text(encoding="utf-8"))
    names = ["backend-tests.xml", "frontend-tests.json", "e2e-results.json", "privacy-check.json"]
    summary = {
        "schema_version": "1.0",
        "generated_at": datetime.now(UTC).isoformat(),
        "scope": "local_patch_0.4.1_policy_fixes_review_lifecycle_backups_and_related_regressions_not_container_deployment",
        "backend": dict(backend.attrib),
        "frontend": {
            "passed": frontend["numPassedTests"],
            "failed": frontend["numFailedTests"],
        },
        "e2e": e2e,
        "privacy": privacy,
        "real_model_smoke": {"executed_in_this_run": False},
        "formal_release_journey": {"executed_in_this_run": False},
    }
    start = datetime.fromisoformat(e2e["startTime"].replace("Z", "+00:00")).timestamp()
    smoke_path = reports / "serial-smoke-baseline.json"
    if not e2e["skipped"] and smoke_path.is_file() and smoke_path.stat().st_mtime >= start:
        smoke = json.loads(smoke_path.read_text(encoding="utf-8"))
        cases = smoke["cases"]
        latency = sorted(c["generation_ms"] for c in cases)
        # Linear sample percentile; six serial samples do not establish a latency SLO.
        index = (len(latency) - 1) * 0.95
        low = int(index)
        p95 = latency[low] + (latency[min(low + 1, len(latency) - 1)] - latency[low]) * (
            index - low
        )
        summary["real_model_smoke"] = {
            "executed_in_this_run": True,
            "method": smoke["method"],
            "requests": len(cases),
            "raw_schema_valid": sum(c["raw_schema_valid"] for c in cases),
            "needs_review": sum(c["status"] == "needs_review" for c in cases),
            "generation_p50_ms": statistics.median(latency),
            "generation_p95_ms": round(p95, 2),
            "token_level_ttft_ms": None,
            "ram_free_gib_range": [
                min(c["resource"]["ram_free_gib"] for c in cases),
                max(c["resource"]["ram_free_gib"] for c in cases),
            ],
            "limitations": "六筆循序冒煙；不是固定品質評測、負載測試或效能目標。",
        }
        names += ["serial-smoke-baseline.json", "real-inference.png"]
    overview = reports / "console-overview.png"
    if overview.is_file() and overview.stat().st_mtime >= start:
        names.append("console-overview.png")
    journey_path = reports / "real-release-journey.json"
    if not e2e["skipped"] and journey_path.is_file() and journey_path.stat().st_mtime >= start:
        journey = json.loads(journey_path.read_text(encoding="utf-8"))
        stages = journey["stages"]
        evaluated = [s for s in stages if "report" in s]
        switched = [s for s in stages if s.get("task", {}).get("kind") in {"publish", "rollback"}]
        summary["formal_release_journey"] = {
            "executed_in_this_run": True,
            "scope": journey["scope"],
            "evaluations": [
                {
                    "stage": s["stage"],
                    "config_version": s["report"]["config_version"],
                    "report_sha256": s["report"].get("report_sha256"),
                    "metrics": s["report"].get("metrics"),
                    "generation_parameters": {
                        key: s["report"]["config_snapshot"][key]
                        for key in ("max_tokens", "temperature", "seed")
                    },
                }
                for s in evaluated
            ],
            "switches": [
                {
                    "stage": s["stage"],
                    "status": s["task"]["status"],
                    "task_id": s["task"]["id"],
                    "release_id": (s["task"].get("workflow_result") or {}).get("release_id"),
                }
                for s in switched
            ],
            "actual_inferences": [
                {
                    "stage": s["stage"],
                    "release_id": s["task"]["release_id"],
                    "config_version": s["task"]["profile_version"],
                    "model_sha256": s["task"]["model_sha256"],
                    "max_tokens": s["task"]["generation"]["max_tokens"],
                    "seed": s["task"]["generation"]["seed"],
                }
                for s in stages
                if s.get("task", {}).get("kind") == "inference"
            ],
            "limitations": "固定合成回歸集，曾用於提示診斷；不是独立保留集泛化證據。服務設定切換，不是不同權重回滾。",
        }
        names.append("real-release-journey.json")
        for name in ("formal-evaluation.png", "verified-rollback.png"):
            path = reports / name
            if path.is_file() and path.stat().st_mtime >= start:
                names.append(name)
    operations_path = reports / "operations-real.json"
    summary["operations_journey"] = {"executed_in_this_run": False}
    if (
        not e2e["skipped"]
        and operations_path.is_file()
        and operations_path.stat().st_mtime >= start
    ):
        operations = json.loads(operations_path.read_text(encoding="utf-8"))
        complete = all(key in operations for key in ("holdout", "load_A", "load_B", "experiments"))
        summary["operations_journey"] = {
            "executed_in_this_run": True,
            "complete_report": complete,
            "holdout_use": operations.get("holdout", {}).get("evidence_use"),
            "container_executed": False,
        }
        names.append("operations-real.json")
    quality_path = reports / "quality-first-validation.json"
    if quality_path.exists():
        quality = json.loads(quality_path.read_bytes())
        summary["historical_slice4_first_quality_validation_not_current_measurement"] = {
            "evidence_kind": quality["evidence_kind"],
            "release_check": quality["release_check"],
            "reports": [
                {
                    "dataset": r["dataset"]["id"],
                    "evidence_use": r["evidence_use"],
                    "report_sha256": r["report_sha256"],
                    "metrics": r["metrics"],
                    "quality_metrics": r["quality_metrics"],
                }
                for r in quality["reports"]
            ],
            "limitations": "AI-authored synthetic data, no independent human review. First new holdout consumed; no candidate release.",
        }
        names.extend(["quality-first-validation.json", "quality-first-case-audit.json"])
    patch_path = reports / "quality-fix-0.4.1.json"
    if patch_path.is_file():
        patch = json.loads(patch_path.read_bytes())
        batches = sorted((reports / "archive").glob("patch-quality-e2e-*.json"))
        summary["policy_patch_observed_quality_separate_batch"] = {
            "evidence_kind": patch["evidence_kind"],
            "application_version": "0.4.1",
            "release_check": patch["release_check"],
            "reports": [
                {
                    "dataset": r["dataset"]["id"],
                    "report_sha256": r["report_sha256"],
                    "evidence_use": r["evidence_use"],
                    "contract": r["contract"],
                    "metrics": r["metrics"],
                    "quality_metrics": r["quality_metrics"],
                }
                for r in patch["reports"]
            ],
            "e2e_batches": [
                {
                    "path": p.relative_to(ROOT).as_posix(),
                    "stats": json.loads(p.read_bytes())["stats"],
                }
                for p in batches
            ],
            "limitations": "All three datasets already observed; no new unseen validation, no model candidate publication; human edits excluded.",
        }
        names.extend(["quality-fix-0.4.1.json", "quality-fix-case-audit-0.4.1.json"])
        names.extend(p.relative_to(reports).as_posix() for p in batches)
        for name in (
            "quality-workbench.png",
            "review-business-backup-roundtrip.json",
            "review-lifecycle-backup.png",
            "patch-resources.json",
            "patch-integrity.json",
            "live-0.4.1-check.json",
        ):
            if (reports / name).is_file():
                names.append(name)
    summary["files"] = [
        {
            "path": "reports/" + name,
            "bytes": (reports / name).stat().st_size,
            "sha256": hashlib.sha256((reports / name).read_bytes()).hexdigest(),
        }
        for name in names
    ]
    (reports / "evidence-index.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "indexed_files": len(names),
                "real_model_executed": summary["real_model_smoke"]["executed_in_this_run"],
                "formal_release_journey": summary["formal_release_journey"]["executed_in_this_run"],
            }
        )
    )


if __name__ == "__main__":
    main()
