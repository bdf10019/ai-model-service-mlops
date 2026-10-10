from __future__ import annotations

import base64
import copy
import hashlib
import json
import time

import pytest
from console_app.app import ROOT, create_app
from console_app.context_quality import analyse_context
from console_app.domain import InferRequest, Profile, Triage, fingerprint, safety
from fastapi.testclient import TestClient
from test_reviews_quality import GUARD, decide, infer, login
from test_reviews_quality import env as env


@pytest.mark.parametrize(
    "text,floor",
    [
        ("現場未發生火花。", None),
        ("現場尚未發生火花。", None),
        ("現場已發生火花。", "critical"),
        ("火花尚未出現。", None),
        ("火花明確不存在。", None),
        ("未曾發生火花。", None),
        ("無法排除火花。", "critical"),
        ("尚未確認是否起火。", "critical"),
        ("並非沒有火花。", "critical"),
        ("去年實際發生爆炸，現在暫時恢復。", "critical"),
        ("去年實際發生爆炸，現在完整恢復。", "critical"),
        ("手冊範例寫「發生火花應通報」，今天未見異常。", None),
        ("現場回報「已發生火花」，設備尚未恢復。", "critical"),
        ("假設發生起火，需要通報。", None),
        ("如果發生火花，但現場確實出現焦味。", "critical"),
        ("手冊假設火花；現場目前已大量洩漏。", "critical"),
        ("不可把壓力偏移當作爆炸；現場仍有焦味。", "critical"),
        ("沒有火花且電流已超限。", "high"),
        ("未發生火花，後來確實出現電弧。", "critical"),
        ("未發生火花卻仍聞到焦味。", "critical"),
        ("沒有火花但仍無法排除爆炸可能。", "critical"),
        ("不得將溫度偏移視為起火。", None),
        ("火花已發生後尚未出現第二次火花。", "critical"),
    ],
)
def test_general_scope_and_actual_hazard_counterexamples(text, floor):
    context = analyse_context(text)
    assert context["minimum_severity"] == floor
    assert all(text[m["start"] : m["end"]] == m["text"] for m in context["mentions"])
    p = Profile.model_validate_json((ROOT / "profiles/quality-fix-service.json").read_bytes())
    raw = Triage(
        event_type="electrical",
        severity="low",
        summary_zh_tw="受控反例。",
        evidence_spans=[text[:30]],
        missing_information=[],
        safe_actions=["observe"],
        review_required=False,
        reason_codes=[],
    ).model_dump()
    before = copy.deepcopy(raw)
    post, _ = safety(
        raw,
        InferRequest(
            profile_id="service",
            provider_id="fixture",
            incident_text=text + "現場紀錄待核對。",
            asset_id="DEMO-BOUNDARY",
        ),
        p,
    )
    assert raw == before
    if floor:
        assert post["severity"] == floor and post["review_required"]
    else:
        assert post["severity"] == "low"


def preview_export(c, h):
    preview = c.post("/api/review-backups/export-preview", json={}, headers=h)
    assert preview.status_code == 200, preview.text
    p = preview.json()
    r = c.post(
        "/api/review-backups/export",
        json={
            "preview_id": p["preview_id"],
            "bundle_sha256": p["bundle_sha256"],
            "confirmation": "EXPORT_PRIVATE_HUMAN_BUSINESS_ONLY",
        },
        headers=h,
    )
    assert r.status_code == 200, r.text
    return r.json()


def upload(c, h, bundle):
    data = json.dumps(bundle, ensure_ascii=False).encode()
    r = c.post(
        "/api/review-backups/uploads",
        json={"bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()},
        headers=h,
    )
    assert r.status_code == 201
    key = r.json()["id"]
    for n, i in enumerate(range(0, len(data), 12000)):
        assert (
            c.post(
                f"/api/review-backups/uploads/{key}/chunks",
                json={"index": n, "data": base64.b64encode(data[i : i + 12000]).decode()},
                headers=h,
            ).status_code
            == 200
        )
    return key, c.post(f"/api/review-backups/uploads/{key}/preview", json={}, headers=h)


def test_controlled_expiry_deletes_source_and_prevents_reapproval(env):
    app, c, h, _ = env
    clock = [time.time()]
    app.state.reviews.clock = lambda: clock[0]
    task = infer(c, h, text="配電箱火花與私人原始內容，尚未恢復。", retain="local_review_7_days")
    key = task["id"]
    detail = c.get("/api/reviews/" + key).json()
    assert detail["evidence_expires_at"] == clock[0] + 604800
    assert detail["evidence_available"]
    clock[0] += 604800
    detail = c.get("/api/reviews/" + key).json()
    assert not detail["evidence_available"] and detail["raw_output"] is None
    assert app.state.store.record("review_evidence", key) is None
    assert key not in app.state.jobs.ephemeral
    assert decide(c, h, key).status_code == 410
    assert (
        decide(c, h, key, "correct", changes={"summary_zh_tw": "禁止過期修改。"}).status_code == 410
    )
    assert app.state.store.record("reviews", key)["history"] == []
    assert c.get("/api/tasks/" + key).json().get("raw_output") is None


def test_consented_backup_preserves_human_history_without_source_and_isolated_restore(env):
    app, c, h, password = env
    source = "私有來源標記_DEMO：配電箱火花仍持續，需要人工處理。"
    task = infer(c, h, text=source, retain="local_review_7_days")
    key = task["id"]
    reason = "人工私人業務理由_DEMO：已核對來源，只記錄業務分流。"
    assert (
        decide(
            c,
            h,
            key,
            "correct",
            reason=reason,
            changes={"summary_zh_tw": "人工摘要_DEMO：已向現場主管轉交。"},
        ).status_code
        == 200
    )
    bundle = preview_export(c, h)
    assert source not in json.dumps(bundle, ensure_ascii=False)
    assert reason in json.dumps(bundle, ensure_ascii=False)
    assert all(r["namespace"] != "review_evidence" for r in bundle["records"])
    original = app.state.store.record("reviews", key)
    u, p = upload(c, h, bundle)
    assert p.status_code == 200, p.text
    restore = c.post(
        f"/api/review-backups/uploads/{u}/restore",
        json={"bundle_sha256": bundle["sha256"], "confirmation": "RESTORE_ISOLATED_ONLY"},
        headers=h,
    )
    assert restore.status_code == 201, restore.text
    receipt = restore.json()
    target = ROOT / receipt["relative_installation"]
    assert app.state.store.record("reviews", key) == original
    assert not receipt["source_evidence_restored"] and not receipt["active_releases_restored"]
    isolated = create_app(target, engine_port=18197)
    with TestClient(isolated, base_url="http://127.0.0.1") as clone:
        hh = {
            **GUARD,
            "X-CSRF-Token": clone.post(
                "/api/bootstrap",
                json={"username": "isolatedadmin", "password": password},
                headers=GUARD,
            ).json()["csrf_token"],
        }
        d = clone.get("/api/reviews/" + key).json()
        assert not d["evidence_available"] and d["input"] is None
        assert d["human_result"]["summary_zh_tw"] == original["human_result"]["summary_zh_tw"]
        assert d["history"][0]["actor"] == original["history"][0]["actor"]
        assert d["config_sha256"] == original["config_sha256"]
        assert decide(clone, hh, key).status_code == 409
        assert clone.get("/api/reviews/" + key + "/export").json()["model_metrics_changed"] is False
    views = c.get("/api/review-backups/restores/" + receipt["id"] + "/reviews").json()["reviews"]
    assert views[0]["raw_output"] is None and views[0]["human_result"]


def test_backup_incompatible_and_reference_tampering_rejected(env):
    app, c, h, _ = env
    task = infer(c, h)
    assert decide(c, h, task["id"]).status_code == 200
    bundle = preview_export(c, h)
    for mutate in ["version", "reference", "source", "hash"]:
        bad = copy.deepcopy(bundle)
        rec = next(r for r in bad["records"] if r["namespace"] == "reviews")
        if mutate == "version":
            bad["format"] = "unsupported-v999"
        if mutate == "reference":
            rec["data"]["config_version"] = "missing-version"
        if mutate == "source":
            rec["data"]["raw_output"] = {"private": "forbidden"}
        if mutate == "hash":
            bad["sha256"] = "0" * 64
        else:
            rec["sha256"] = fingerprint(rec["data"])
            bad["sha256"] = fingerprint({k: v for k, v in bad.items() if k != "sha256"})
        _, p = upload(c, h, bad)
        assert p.status_code == 422
    # Previous general business format remains supported by its own existing contract.
    old = c.post("/api/backups/export", json={}, headers=h).json()
    assert app.state.operations.validate_backup(old)["format"] == "console-business-backup-v1"
    for version in ("0.3.0", "0.4.0", "0.4.1"):
        compatible = {**old, "product_version": version}
        compatible["sha256"] = fingerprint({k: v for k, v in compatible.items() if k != "sha256"})
        assert app.state.operations.validate_backup(compatible)["product_version"] == version


def test_backup_authorization_preview_binding_and_expiry(env):
    app, c, h, password = env
    r = c.post("/api/review-backups/export-preview", json={}, headers=h).json()
    reviewer = login(c, "reviewone", password)
    assert (
        c.post("/api/review-backups/export-preview", json={}, headers=reviewer).status_code == 403
    )
    valid_export = {
        "preview_id": r["preview_id"],
        "bundle_sha256": r["bundle_sha256"],
        "confirmation": "EXPORT_PRIVATE_HUMAN_BUSINESS_ONLY",
    }
    assert (
        c.post("/api/review-backups/export", json=valid_export, headers=reviewer).status_code == 403
    )
    assert c.get("/api/review-backups").status_code == 403
    h = login(c, "reviewadmin", password)
    data = {
        "preview_id": r["preview_id"],
        "bundle_sha256": r["bundle_sha256"],
        "confirmation": "EXPORT_PRIVATE_HUMAN_BUSINESS_ONLY",
    }
    assert (
        c.post(
            "/api/review-backups/export", json={**data, "bundle_sha256": "0" * 64}, headers=h
        ).status_code
        == 409
    )
    app.state.review_backups.clock = lambda: time.time() + 121
    assert c.post("/api/review-backups/export", json=data, headers=h).status_code == 404
