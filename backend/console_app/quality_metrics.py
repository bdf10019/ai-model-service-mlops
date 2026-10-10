"""Additional v2 metrics; historical joint-task scores are never rewritten."""

from __future__ import annotations

METRIC_VERSION = "triage-quality-v2"
DEFINITIONS = {
    "event_accuracy": "有效輸入的event_type精確符合固定標註／全部有效輸入；無输出算錯。",
    "severity_accuracy": "severity精確符合固定標註／全部有效輸入；無输出算錯。",
    "required_review_recall": "實際轉人工且應轉人工／應轉人工；應轉由固定標註及啟動Profile加嚴規則決定。",
    "unnecessary_review_rate": "實際轉人工但不要求人工／不要求人工；不是模型任務錯誤率。",
    "automatic_completion_rate": "有效輸出且未轉人工／全部有效輸入；無输出不算自動完成。",
    "automatic_accuracy": "自動完成且event_type與severity皆對／全部自動完成；人工修正完全不列入。",
}


def review_expected(case, profile):
    e = case["expected"]
    return bool(
        e.get("must_review")
        or e.get("severity") in profile.review_severities
        or profile.review_critical_assets
        and case["input"].get("asset_criticality") == "critical"
    )


def quality_scores(case, raw, post, profile):
    if not case["expected"]["input_valid"]:
        return {"eligible": False}
    expected = review_expected(case, profile)
    result = {"eligible": True, "expected_review": expected}
    for layer, value in (("raw", raw), ("post", post)):
        result[layer] = {
            "event_correct": bool(value and value["event_type"] == case["expected"]["event_type"]),
            "severity_correct": bool(value and value["severity"] == case["expected"]["severity"]),
            "review": bool(value and value["review_required"]),
            "automatic": bool(value and not value["review_required"]),
        }
    return result


def quality_metrics(rows, profile):
    eligible = [c for c in rows if c["expected"]["input_valid"]]
    scores = {
        c["id"]: quality_scores(
            c, c.get("raw_output") if c.get("raw_schema_valid") else None, c.get("result"), profile
        )
        for c in eligible
    }
    records = {}
    for layer in ("raw", "post"):
        for name in DEFINITIONS:
            denominator, passing = [], []
            for c in eligible:
                q = scores[c["id"]]
                v = q[layer]
                use = True
                if name == "required_review_recall":
                    use = q["expected_review"]
                if name == "unnecessary_review_rate":
                    use = not q["expected_review"]
                if name == "automatic_accuracy":
                    use = v["automatic"]
                if not use:
                    continue
                denominator.append(c["id"])
                success = {
                    "event_accuracy": v["event_correct"],
                    "severity_accuracy": v["severity_correct"],
                    "required_review_recall": v["review"],
                    "unnecessary_review_rate": v["review"],
                    "automatic_completion_rate": v["automatic"],
                    "automatic_accuracy": v["event_correct"] and v["severity_correct"],
                }[name]
                if success:
                    passing.append(c["id"])
            records[layer + "_" + name] = {
                "definition": DEFINITIONS[name],
                "numerator": len(passing),
                "denominator": len(denominator),
                "case_ids": denominator,
                "numerator_case_ids": passing,
                "value": len(passing) / len(denominator) if denominator else None,
            }
    return {"version": METRIC_VERSION, "human_decisions_included": False, "records": records}
