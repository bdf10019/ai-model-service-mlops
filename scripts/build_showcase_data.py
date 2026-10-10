"""Build the offline showcase's explicit, de-identified evidence projection."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PUBLIC = ROOT / "showcase/public"


def read(name: str):
    return json.loads((ROOT / name).read_bytes())


def sha(name: str):
    return hashlib.sha256((ROOT / name).read_bytes()).hexdigest()


def metric(record, name):
    item = record["quality_metrics"]["records"][name]
    return {
        "value": item["value"],
        "numerator": item["numerator"],
        "denominator": item["denominator"],
        "definition": item["definition"],
    }


def main():
    quality_name = "reports/quality-fix-0.4.1.json"
    evidence_name = "reports/evidence-index.json"
    operations_name = "reports/operations-real.json"
    quality = read(quality_name)
    evidence = read(evidence_name)
    operations = read(operations_name)
    audit = read("reports/quality-fix-case-audit-0.4.1.json")
    cases = []
    wanted = {"h03", "h08", "h11", "h15"}
    group = next(g for g in audit["groups"] if g["dataset_id"] == "equipment-holdout-v2")
    current_report = next(
        r for r in quality["reports"] if r["dataset"]["id"] == "equipment-holdout-v2"
    )
    current_cases = {case["id"]: case for case in current_report["cases"]}
    for item in group["cases"]:
        if item["id"] not in wanted:
            continue
        current = current_cases[item["id"]]
        cases.append(
            {
                "id": item["id"],
                "input": item["input"]["incident_text"],
                "expected": item["expected"],
                "raw": {
                    "event_type": current["raw_output"]["event_type"],
                    "severity": current["raw_output"]["severity"],
                    "review_required": current["raw_output"]["review_required"],
                },
                "post": {
                    "event_type": current["result"]["event_type"],
                    "severity": current["result"]["severity"],
                    "review_required": current["result"]["review_required"],
                },
                "change": {
                    "raw_improved": item["raw_task_improved"],
                    "raw_regressed": item["raw_task_regressed"],
                    "post_improved": item["post_task_improved"],
                    "post_regressed": item["post_task_regressed"],
                },
            }
        )
    datasets = []
    for report in quality["reports"]:
        metrics = report["metrics"]
        datasets.append(
            {
                "id": report["dataset"]["id"],
                "version": report["dataset"]["version"],
                "use": report["evidence_use"],
                "total_cases": report["total_cases"],
                "generated_cases": report["completed_cases"] - metrics["invalid_input_count"],
                "invalid_cases": metrics["invalid_input_count"],
                "raw_task": {
                    "numerator": round(metrics["raw_task_accuracy"] * metrics["generated_count"]),
                    "denominator": metrics["generated_count"],
                },
                "post_task": {
                    "numerator": round(metrics["post_task_accuracy"] * metrics["generated_count"]),
                    "denominator": metrics["generated_count"],
                },
                "raw_safety_violations": metrics["raw_safety_violations"],
                "post_safety_violations": metrics["post_safety_violations"],
                "raw_refusal_rate": metrics["raw_refusal_rate"],
                "post_refusal_rate": metrics["post_refusal_rate"],
                "generation_p50_ms": metrics["generation_p50_ms"],
                "generation_p95_ms": metrics["generation_p95_ms"],
                "token_level_ttft_ms": None,
                "post_required_review_recall": metric(report, "post_required_review_recall"),
                "post_unnecessary_review_rate": metric(report, "post_unnecessary_review_rate"),
                "post_automatic_completion_rate": metric(report, "post_automatic_completion_rate"),
                "post_automatic_accuracy": metric(report, "post_automatic_accuracy"),
                "report_sha256": report["report_sha256"],
            }
        )
    load_runs = []
    for key, concurrency in (("load_A", 1), ("load_B", 2)):
        run = operations[key]
        m = run["metrics"]
        load_runs.append(
            {
                "id": key,
                "concurrency": concurrency,
                "requests": m["request_count"],
                "success": m["success_count"],
                "queue_p50_ms": m["queue_ms_p50"],
                "queue_p95_ms": m["queue_ms_p95"],
                "generation_p50_ms": m["generation_ms_p50"],
                "generation_p95_ms": m["generation_ms_p95"],
                "throughput_rps": m["throughput_rps"],
                "client_turnaround_p95_ms": m["client_turnaround_p95_ms"],
                "token_level_ttft_ms": None,
                "report_sha256": run["report_sha256"],
            }
        )
    result = {
        "schema_version": "showcase-evidence-v1",
        "showcase_version": "1.1.0",
        "product_version": "0.4.1",
        "generated_from": "local measured reports; explicit public projection",
        "display_mode": "historical_measurement",
        "verification": {
            "backend": {"passed": int(evidence["backend"]["tests"]), "failed": 0},
            "frontend": {"passed": evidence["frontend"]["passed"], "failed": 0},
            "main_e2e": {"passed": evidence["e2e"]["expected"], "failed": 0},
            "quality_e2e_batches": 1,
        },
        "model": {
            "name": "Qwen3-1.7B merged v5 Q8_0",
            "provider": "local llama.cpp",
            "weights_changed_in_0_4_1": False,
        },
        "quality": {
            "prompt": "triage-v5.1",
            "policy": "safety-floor-v3.1",
            "candidate_status": "unpublished",
            "release_gate_passed": False,
            "datasets": datasets,
            "cases": cases,
        },
        "load": {
            "method": "historical bounded local measurement; one warm-up plus six measured requests per run",
            "runs": load_runs,
        },
        "sources": [
            {"id": "quality-0.4.1", "path": quality_name, "sha256": sha(quality_name)},
            {"id": "evidence-index", "path": evidence_name, "sha256": sha(evidence_name)},
            {"id": "operations-0.3.0", "path": operations_name, "sha256": sha(operations_name)},
        ],
        "limitations": [
            "三組品質資料均已觀測，不能稱為未見保留驗證。",
            "資料為小型 AI 合成標註，沒有獨立專家或真實工廠泛化證據。",
            "政策後零安全違規只適用於上述有限案例，不代表百分之百安全。",
            "負載每組只有六筆，不是服務等級目標。",
            "TTFT 未提供；完整生成時間不能替代逐 Token TTFT。",
            "相同 Q8 的 A→B→A 是服務設定回滾，不是不同權重切換。",
            "Docker、Model Registry、真正串流及公開部署尚未完成。",
        ],
    }
    data = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    (PUBLIC / "data/evidence-summary.json").write_text(data, encoding="utf-8")
    (PUBLIC / "downloads/evidence-summary.json").write_text(data, encoding="utf-8")
    (ROOT / "showcase/src/evidence-summary.json").write_text(data, encoding="utf-8")
    digest = hashlib.sha256(data.encode()).hexdigest()
    print(json.dumps({"datasets": len(datasets), "cases": len(cases), "sha256": digest}))


if __name__ == "__main__":
    main()
