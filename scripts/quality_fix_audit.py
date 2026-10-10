"""Compare frozen historical and current observed reports without changing evidence."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main():
    baseline_path = ROOT / "reports/quality-first-validation.json"
    current_path = ROOT / "reports/quality-fix-0.4.1.json"
    baseline = json.loads(baseline_path.read_bytes())
    current = json.loads(current_path.read_bytes())
    groups = []
    for report in current["reports"]:
        previous = next(
            r for r in baseline["reports"] if r["dataset"]["id"] == report["dataset"]["id"]
        )
        old_cases = {r["id"]: r for r in previous["cases"]}
        assert set(old_cases) == {r["id"] for r in report["cases"]}
        changes = []
        for case in report["cases"]:
            old = old_cases[case["id"]]
            assert old["input"] == case["input"] and old["expected"] == case["expected"]
            changes.append(
                {
                    "id": case["id"],
                    "input": case["input"],
                    "expected": case["expected"],
                    "previous_score": old["score"],
                    "current_score": case["score"],
                    "previous_quality_score": old["quality_score"],
                    "current_quality_score": case["quality_score"],
                    "raw_task_improved": old["score"]["raw_task_correct"] is False
                    and case["score"]["raw_task_correct"] is True,
                    "raw_task_regressed": old["score"]["raw_task_correct"] is True
                    and case["score"]["raw_task_correct"] is False,
                    "post_task_improved": old["score"]["post_task_correct"] is False
                    and case["score"]["post_task_correct"] is True,
                    "post_task_regressed": old["score"]["post_task_correct"] is True
                    and case["score"]["post_task_correct"] is False,
                }
            )
        groups.append(
            {
                "dataset_id": report["dataset"]["id"],
                "previous_report_sha256": previous["report_sha256"],
                "current_report_sha256": report["report_sha256"],
                "previous_metrics": previous["metrics"],
                "current_metrics": report["metrics"],
                "previous_quality_metrics": previous["quality_metrics"],
                "current_quality_metrics": report["quality_metrics"],
                "cases": changes,
                "task_changes": {
                    key: [c["id"] for c in changes if c[key]]
                    for key in (
                        "raw_task_improved",
                        "raw_task_regressed",
                        "post_task_improved",
                        "post_task_regressed",
                    )
                },
                "current_failures": {
                    key: [c["id"] for c in report["cases"] if c["score"][key] is False]
                    for key in ("raw_task_correct", "post_task_correct", "raw_refusal_correct")
                },
                "raw_safety_violation_cases": [
                    c["id"] for c in report["cases"] if c["score"]["raw_safety_violation"]
                ],
            }
        )
    output = {
        "application_version": "0.4.1",
        "scope": "three_already_observed_synthetic_datasets_no_unseen_validation",
        "human_decisions_included": False,
        "labels_inputs_case_counts_unchanged": True,
        "sources": [
            {
                "path": p.relative_to(ROOT).as_posix(),
                "sha256": hashlib.sha256(p.read_bytes()).hexdigest(),
            }
            for p in (baseline_path, current_path)
        ],
        "groups": groups,
        "limitations": "Prompt and policy changed together; paired task results do not isolate all causal effects. AI-authored synthetic labels, no independent human review.",
    }
    (ROOT / "reports/quality-fix-case-audit-0.4.1.json").write_text(
        json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    for group in groups:
        print(
            json.dumps(
                {
                    k: group[k]
                    for k in (
                        "dataset_id",
                        "task_changes",
                        "current_failures",
                        "raw_safety_violation_cases",
                    )
                },
                ensure_ascii=False,
            )
        )


if __name__ == "__main__":
    main()
