from __future__ import annotations

import json
import secrets
import time
import uuid

import pytest
from console_app.app import ROOT, create_app
from console_app.domain import Triage, fingerprint
from console_app.engine import MODEL_SHA, EngineError
from fastapi.testclient import TestClient

GUARD = {"X-Console-Request": "1", "Origin": "http://127.0.0.1"}


def wait(client, path, seconds=8):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        doc = client.get(path).json()
        if doc["status"] in {"completed", "failed", "needs_review"}:
            return doc
        time.sleep(0.01)
    raise AssertionError("workflow did not finish")


@pytest.fixture
def env(tmp_path, monkeypatch):
    import console_app.workflows as module

    app = create_app(state_dir=tmp_path, engine_port=18191)
    app.state.jobs.fixture_delay = 0.001
    calls = []
    cases = json.loads((ROOT / "data/evaluation-v1.json").read_bytes())["cases"]

    async def generate(req, profile):
        calls.append({"config": profile.model_dump(), "text": req.incident_text})
        case = next(c for c in cases if c["input"]["incident_text"] == req.incident_text)
        expected = case["expected"]
        severity = expected["severity"]
        return Triage(
            event_type=expected["event_type"],
            severity=severity,
            summary_zh_tw="測試替身：固定標註分流。",
            evidence_spans=[req.incident_text[:35]],
            missing_information=["量測值"],
            safe_actions=[
                "stop_and_escalate"
                if severity == "critical"
                else "isolate_and_escalate"
                if severity == "high"
                else "collect_evidence"
            ],
            review_required=expected["must_review"],
            reason_codes=["UNTRUSTED_INSTRUCTION"] if expected["refusal_required"] else [],
        ).model_dump(), {"ttft_ms": None, "max_tokens": profile.max_tokens, "seed": profile.seed}

    async def start():
        return app.state.engine.status()

    async def stop():
        pass

    monkeypatch.setattr(app.state.engine, "generate", generate)
    monkeypatch.setattr(
        app.state.engine,
        "status",
        lambda: {"ready": True, "status": "ready", "model_sha256": MODEL_SHA},
    )
    monkeypatch.setattr(app.state.engine, "start", start)
    monkeypatch.setattr(app.state.engine, "stop", stop)
    monkeypatch.setattr(app.state.engine, "drain_after_error", stop)
    monkeypatch.setattr(
        module, "verify_import", lambda root: {"model_sha256": MODEL_SHA, "test_double": True}
    )
    password = secrets.token_urlsafe(24)
    with TestClient(app, base_url="http://127.0.0.1") as client:
        response = client.post(
            "/api/bootstrap",
            json={"username": "pipelineadmin", "password": password},
            headers=GUARD,
        )
        h = {**GUARD, "X-CSRF-Token": response.json()["csrf_token"]}
        for name, role in [
            ("reviewone", "reviewer"),
            ("reviewtwo", "reviewer"),
            ("operatorone", "operator"),
        ]:
            assert (
                client.post(
                    "/api/users",
                    json={"username": name, "password": password, "role": role},
                    headers=h,
                ).status_code
                == 201
            )
        yield app, client, password, calls


def login(client, password, name="pipelineadmin"):
    data = client.post(
        "/api/sessions", json={"username": name, "password": password}, headers=GUARD
    ).json()
    return {**GUARD, "X-CSRF-Token": data["csrf_token"]}


def config(client, headers, profile="service", **updates):
    p = next(p for p in client.get("/api/profiles").json()["profiles"] if p["id"] == profile)
    p.update(
        prompt_version="triage-v2",
        quality_gate={
            "schema_rate": 1.0,
            "task_accuracy": 0.8,
            "raw_refusal_rate": 1.0,
            "max_generation_p95_ms": 5000,
        },
        **updates,
    )
    response = client.post("/api/configs", json=p, headers=headers)
    assert response.status_code == 201, response.text
    version = response.json()["id"]
    assert (
        client.post("/api/configs/" + version + "/select", json={}, headers=headers).status_code
        == 200
    )
    return version


def evaluate(client, headers, version, provider="q8"):
    r = client.post(
        "/api/evaluations",
        json={
            "profile_version": version,
            "provider_id": provider,
            "dataset_id": "equipment-triage-v1",
        },
        headers=headers,
    )
    assert r.status_code == 202, r.text
    return wait(client, "/api/evaluations/" + r.json()["id"])


def candidate(env, **updates):
    app, client, password, calls = env
    h = login(client, password)
    version = config(client, h, **updates)
    report = evaluate(client, h, version)
    check = client.post(
        "/api/releases/check", json={"evaluation_id": report["id"]}, headers=h
    ).json()
    assert check["passed"], check
    release = client.post("/api/releases", json={"evaluation_id": report["id"]}, headers=h).json()
    for name in ["reviewone", "reviewtwo"][: release["approvals_required"]]:
        h = login(client, password, name)
        r = client.post(
            "/api/releases/" + release["id"] + "/approval",
            json={"bundle_sha256": release["bundle_sha256"], "decision": "approve"},
            headers=h,
        )
        assert r.status_code == 200, r.text
        release = r.json()
    return release, report, login(client, password)


def switch(client, h, release, action="publish", current=None, maintenance=False):
    response = client.post(
        "/api/releases/" + release["id"] + "/" + action,
        json={
            "confirmation": {
                "publish": "APPLY_VERSION",
                "rollback": "ROLLBACK_VERSION",
                "recover": "RECOVER_VERSION",
            }[action],
            "expected_active_id": current,
            "maintenance_window": maintenance,
        },
        headers=h,
    )
    assert response.status_code == 202, response.text
    return wait(client, "/api/tasks/" + response.json()["id"])


def test_config_preview_validation_restore_and_secret_rejection(env):
    app, client, password, calls = env
    h = login(client, password)
    defaults = client.get("/api/configs").json()["defaults"]
    a = client.post("/api/configs/preview", json=defaults["service"], headers=h).json()
    b = client.post("/api/configs/preview", json=defaults["asset"], headers=h).json()
    assert (
        a["evidence_kind"] == "fixture"
        and not a["result"]["review_required"]
        and b["result"]["review_required"]
    )
    bad = {**defaults["service"], "review_severities": ["critical"]}
    assert client.post("/api/configs", json=bad, headers=h).status_code == 422
    for extra in [
        {"focus": "http://arbitrary.invalid"},
        {"secret": secrets.token_urlsafe(30)},
        {"name": "DROP TABLE;"},
        {"max_tokens": 99999},
        {"required_fields": ["incident_text", "asset_id", "sql"]},
    ]:
        assert (
            client.post(
                "/api/configs", json={**defaults["service"], **extra}, headers=h
            ).status_code
            == 422
        )
    bad_asset = {**defaults["asset"], "approvals_required": 1}
    assert client.post("/api/configs", json=bad_asset, headers=h).status_code == 422
    restored = client.post("/api/configs/service/restore", json={}, headers=h).json()
    assert restored["restored_from"] == "service" and restored["id"] != "service-v1"
    assert app.state.workflows.profiles["service"].version == "service-v1"


def test_formal_report_scoring_seal_and_fixture_release_rejection(env):
    app, client, password, calls = env
    h = login(client, password)
    version = config(client, h)
    report = evaluate(client, h, version)
    assert len(report["cases"]) == 24 and report["metrics"]["generated_count"] == 20
    assert (
        report["metrics"]["raw_task_accuracy"] == 1
        and report["metrics"]["post_safety_violations"] == 0
    )
    assert (
        report["metrics"]["input_rejection_rate"] == 1
        and report["metrics"]["token_level_ttft_ms"] is None
    )
    assert report["report_sha256"] == fingerprint(
        {k: v for k, v in report.items() if k != "report_sha256"}
    )
    f = evaluate(client, h, version, "fixture")
    gate = client.post("/api/releases/check", json={"evaluation_id": f["id"]}, headers=h).json()
    assert "FIXTURE_CANNOT_RELEASE" in gate["reasons"] and not gate["passed"]
    assert (
        client.post("/api/releases", json={"evaluation_id": f["id"]}, headers=h).status_code == 409
    )


def test_quality_failure_keeps_raw_and_policy_results_separate(env, monkeypatch):
    app, client, password, calls = env
    original = app.state.engine.generate

    async def wrong(req, p):
        raw, metadata = await original(req, p)
        raw.update(severity="low", review_required=False, safe_actions=["observe"], reason_codes=[])
        return raw, metadata

    monkeypatch.setattr(app.state.engine, "generate", wrong)
    h = login(client, password)
    report = evaluate(client, h, config(client, h))
    assert report["metrics"]["raw_task_accuracy"] < report["metrics"]["post_task_accuracy"]
    gate = client.post(
        "/api/releases/check", json={"evaluation_id": report["id"]}, headers=h
    ).json()
    assert any(r.startswith("TASK_ACCURACY_FAILED") for r in gate["reasons"])
    high = next(c for c in report["cases"] if c["id"] == "h01")
    assert high["raw_output"]["severity"] == "low" and high["result"]["severity"] == "critical"


def test_expired_changed_selected_and_tampered_reports_rejected(env):
    app, client, password, calls = env
    h = login(client, password)
    version = config(client, h)
    report = evaluate(client, h, version)
    config(client, h, seed=84)
    check = client.post(
        "/api/releases/check", json={"evaluation_id": report["id"]}, headers=h
    ).json()
    assert "SELECTED_CONFIG_CHANGED" in check["reasons"]
    client.post("/api/configs/" + version + "/select", json={}, headers=h)
    report["expires_at"] = time.time() - 1
    report["report_sha256"] = fingerprint({k: v for k, v in report.items() if k != "report_sha256"})
    app.state.store.put_record("evaluations", report)
    assert "EVALUATION_EXPIRED" in app.state.workflows.gate(report)["reasons"]
    report["metrics"]["raw_task_accuracy"] = 0.1
    app.state.store.put_record("evaluations", report)
    assert "REPORT_HASH_MISMATCH" in app.state.workflows.gate(report)["reasons"]


def test_self_approval_duplicate_roles_and_double_approval(env):
    app, client, password, calls = env
    h = login(client, password, "reviewone")
    p = next(p for p in client.get("/api/profiles").json()["profiles"] if p["id"] == "asset")
    p["quality_gate"] = {
        "schema_rate": 1.0,
        "task_accuracy": 0.8,
        "raw_refusal_rate": 1.0,
        "max_generation_p95_ms": 5000,
    }
    v = client.post("/api/configs", json=p, headers=h).json()["id"]
    assert client.post("/api/configs/" + v + "/select", json={}, headers=h).status_code == 403
    admin = login(client, password)
    client.post("/api/configs/" + v + "/select", json={}, headers=admin)
    h = login(client, password, "reviewone")
    report = evaluate(client, h, v)
    release = client.post("/api/releases", json={"evaluation_id": report["id"]}, headers=h).json()
    path = "/api/releases/" + release["id"]
    data = {"bundle_sha256": release["bundle_sha256"], "decision": "approve"}
    assert client.post(path + "/approval", json=data, headers=h).status_code == 403
    h = login(client, password, "reviewtwo")
    assert (
        client.post(path + "/approval", json=data, headers=h).json()["status"] == "pending_approval"
    )
    assert client.post(path + "/approval", json=data, headers=h).status_code == 409
    h = login(client, password)
    assert client.post(path + "/approval", json=data, headers=h).status_code == 403
    assert (
        client.post(
            path + "/publish",
            json={
                "expected_active_id": None,
                "confirmation": "APPLY_VERSION",
                "maintenance_window": True,
            },
            headers=h,
        ).status_code
        == 409
    )


def test_real_routing_version_apply_and_verified_rollback_with_mock_engine(env):
    app, client, password, calls = env
    a, report_a, h = candidate(env, max_tokens=320)
    assert switch(client, h, a)["status"] == "completed"
    b, report_b, h = candidate(env, max_tokens=384, seed=84)
    assert switch(client, h, b, current=a["id"])["status"] == "completed"
    sample = app.state.workflows.dataset()[0]["cases"][0]["input"]

    def infer():
        response = client.post(
            "/api/inferences",
            json={**sample, "provider_id": "q8", "profile_id": "service"},
            headers={**h, "Idempotency-Key": uuid.uuid4().hex},
        )
        assert response.status_code == 202, response.text
        return wait(client, "/api/tasks/" + response.json()["id"])

    active = infer()
    assert active["release_id"] == b["id"] and calls[-1]["config"]["seed"] == 84
    # Sandbox candidate remains B; rollback must actually route to A, not copy its label.
    task = switch(client, h, a, "rollback", b["id"])
    assert task["status"] == "completed"
    rolled = infer()
    assert rolled["release_id"] == a["id"] and rolled["profile_version"] == a["config_version"]
    assert calls[-1]["config"]["max_tokens"] == 320 and calls[-1]["config"]["seed"] == 42
    assert app.state.workflows.profiles["service"].version == b["config_version"]


def test_publish_failure_restores_old_pointer_and_records_failure(env, monkeypatch):
    app, client, password, calls = env
    a, _, h = candidate(env)
    switch(client, h, a)
    b, _, h = candidate(env, seed=84)
    original = app.state.engine.generate

    async def fail_candidate(req, profile):
        if profile.seed == 84:
            raise EngineError("MODEL_INFERENCE_ERROR")
        return await original(req, profile)

    monkeypatch.setattr(app.state.engine, "generate", fail_candidate)
    task = switch(client, h, b, current=a["id"])
    assert task["status"] == "failed"
    index = client.get("/api/releases").json()
    assert index["active"]["service"] == a["id"] and index["state"]["ready"]
    failed = app.state.workflows.release(b["id"])["operations"][-1]
    assert (
        failed["status"] == "failed"
        and failed["restored_previous"]
        and failed["restoration_proof"]["cases"]
    )


def test_rollback_failure_is_not_success_and_failed_restore_blocks_requests(env, monkeypatch):
    app, client, password, calls = env
    a, _, h = candidate(env)
    switch(client, h, a)
    b, _, h = candidate(env, seed=84)
    switch(client, h, b, current=a["id"])

    async def unavailable(req, profile):
        raise EngineError("ENGINE_UNAVAILABLE")

    monkeypatch.setattr(app.state.engine, "generate", unavailable)
    task = switch(client, h, a, "rollback", b["id"])
    assert task["status"] == "failed"
    index = client.get("/api/releases").json()
    assert index["active"]["service"] == b["id"] and not index["state"]["ready"]
    sample = app.state.workflows.dataset()[0]["cases"][0]["input"]
    r = client.post(
        "/api/inferences",
        json={**sample, "provider_id": "q8", "profile_id": "service"},
        headers={**h, "Idempotency-Key": uuid.uuid4().hex},
    )
    assert r.status_code == 503 and r.json()["error"]["code"] == "RELEASE_NOT_READY"


def test_busy_switch_and_version_race_do_not_change_snapshot(env):
    app, client, password, calls = env
    a, _, h = candidate(env)
    sample = app.state.workflows.dataset()[0]["cases"][0]["input"]
    app.state.jobs.fixture_delay = 0.4
    r = client.post(
        "/api/inferences",
        json={**sample, "provider_id": "fixture", "profile_id": "service"},
        headers={**h, "Idempotency-Key": uuid.uuid4().hex},
    ).json()
    time.sleep(0.03)
    body = {
        "confirmation": "APPLY_VERSION",
        "expected_active_id": None,
        "maintenance_window": False,
    }
    assert (
        client.post("/api/releases/" + a["id"] + "/publish", json=body, headers=h).status_code
        == 409
    )
    saved = app.state.store.task(r["id"])["config_snapshot_sha256"]
    config(client, h, seed=84)
    assert wait(client, "/api/tasks/" + r["id"])["config_snapshot_sha256"] == saved
    assert not client.get("/api/releases").json()["active"]
    body["expected_active_id"] = "stale-pointer"
    assert (
        client.post("/api/releases/" + a["id"] + "/publish", json=body, headers=h).json()["error"][
            "code"
        ]
        == "ACTIVE_VERSION_CHANGED"
    )


def test_comparison_ownership_and_contract_change(env):
    app, client, password, calls = env
    h = login(client, password)
    a = evaluate(client, h, config(client, h))
    b = evaluate(client, h, config(client, h, seed=84), "fixture")
    compare = client.get(
        "/api/evaluations/compare", params={"left": a["id"], "right": b["id"]}
    ).json()
    assert not compare["comparable"] and not compare["metric_delta"]
    assert any(d["field"] == "seed" for d in compare["config_diff"])
    report = dict(a)
    report["contract"] = {"obsolete": True}
    report["report_sha256"] = fingerprint({k: v for k, v in report.items() if k != "report_sha256"})
    assert "EVALUATION_CONTRACT_CHANGED" in app.state.workflows.gate(report)["reasons"]
    login(client, password, "operatorone")
    assert client.get("/api/evaluations/" + a["id"]).status_code == 403
    assert client.get("/api/evaluations").json()["runs"] == []


def test_invalid_model_response_is_counted_and_blocks_release(env, monkeypatch):
    app, client, password, calls = env

    async def malformed(req, profile):
        return {"untrusted_extra": "invalid"}, {}

    monkeypatch.setattr(app.state.engine, "generate", malformed)
    h = login(client, password)
    report = evaluate(client, h, config(client, h))
    assert report["metrics"]["raw_schema_rate"] == 0 and report["metrics"]["error_count"] == 20
    assert report["metrics"]["generation_p95_ms"] is None
    assert "SAFETY_FLOOR_FAILED" in app.state.workflows.gate(report)["reasons"]


def test_release_binding_cannot_reduce_approval_count(env):
    app, client, password, calls = env
    release, _, h = candidate(env, profile="asset")
    release["approvals_required"] = 1
    app.state.store.put_record("releases", release)
    response = client.post(
        "/api/releases/" + release["id"] + "/publish",
        json={
            "expected_active_id": None,
            "confirmation": "APPLY_VERSION",
            "maintenance_window": True,
        },
        headers=h,
    )
    assert response.status_code == 409 and response.json()["error"]["code"] == "BUNDLE_CHANGED"
    assert not app.state.workflows.active("asset")


def test_formal_invalid_inputs_also_fail_at_public_inference_boundary(env):
    app, client, password, calls = env
    h = login(client, password)
    dataset, _ = app.state.workflows.dataset()
    for case in dataset["cases"]:
        if not case["expected"]["input_valid"]:
            response = client.post(
                "/api/inferences",
                json={**case["input"], "profile_id": "service", "provider_id": "q8"},
                headers={**h, "Idempotency-Key": uuid.uuid4().hex},
            )
            assert response.status_code == 422
    assert not calls


def test_asset_publish_enforces_maintenance_and_actual_parameters(env):
    app, client, password, calls = env
    release, _, h = candidate(env, profile="asset", max_tokens=384, seed=84)
    body = {
        "expected_active_id": None,
        "confirmation": "APPLY_VERSION",
        "maintenance_window": False,
    }
    r = client.post("/api/releases/" + release["id"] + "/publish", json=body, headers=h)
    assert r.status_code == 409 and r.json()["error"]["code"] == "MAINTENANCE_WINDOW_REQUIRED"
    assert switch(client, h, release, maintenance=True)["status"] == "completed"
    proof = app.state.workflows.release(release["id"])["operations"][-1]["proof"]
    assert (
        proof["cases"][0]["generation"]["max_tokens"] == 384
        and proof["cases"][0]["result"]["review_required"]
    )
