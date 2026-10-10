from __future__ import annotations

import base64
import copy
import hashlib
import json
import secrets
import time
from pathlib import Path

import httpx
import pytest
from console_app.app import ROOT, create_app
from console_app.domain import fingerprint
from console_app.engine import MODEL_SHA
from fastapi.testclient import TestClient

BASE = "http://127.0.0.1:18199"
GUARD = {"X-Console-Request": "1", "Origin": BASE}


@pytest.fixture
def env(tmp_path, monkeypatch):
    app = create_app(state_dir=tmp_path, engine_port=18198)
    ops = app.state.operations
    ops.snapshot = lambda root: {
        "ram_free_gib": 4,
        "ram_total_gib": 16,
        "gpu": None,
        "evidence_kind": "fixture_resource_test",
        "sampled_at": time.time(),
    }
    ops.client_factory = lambda **kw: httpx.AsyncClient(
        **kw, transport=httpx.ASGITransport(app=app)
    )
    app.state.jobs.fixture_delay = 0.03
    password = secrets.token_urlsafe(24)
    with TestClient(app, base_url=BASE) as client:
        data = client.post(
            "/api/bootstrap", json={"username": "opsadmin", "password": password}, headers=GUARD
        ).json()
        h = {**GUARD, "X-CSRF-Token": data["csrf_token"]}
        for name, role in [("opsreview", "reviewer"), ("opsuser", "operator")]:
            assert (
                client.post(
                    "/api/users",
                    json={"username": name, "password": password, "role": role},
                    headers=h,
                ).status_code
                == 201
            )
        yield app, client, h, password, data["user"]["id"]


def wait(client, path):
    end = time.monotonic() + 10
    while time.monotonic() < end:
        d = client.get(path).json()
        if d["status"] not in ("running", "stopping", "queued"):
            return d
        time.sleep(0.02)
    raise AssertionError("operation did not complete")


def load_body(**kw):
    return {
        "profile_version": "service-v1",
        "provider_id": "fixture",
        "requests": 4,
        "concurrency": 2,
        "request_timeout_seconds": 5,
        "budget_seconds": 10,
        "warmup_requests": 1,
        "confirmation": "LOCAL_BOUNDED_LOAD",
        **kw,
    }


def upload(client, h, bundle):
    b = json.dumps(bundle, ensure_ascii=False).encode()
    u = client.post(
        "/api/backups/uploads",
        json={"bytes": len(b), "sha256": hashlib.sha256(b).hexdigest()},
        headers=h,
    ).json()["id"]
    for i, offset in enumerate(range(0, len(b), 12000)):
        assert (
            client.post(
                "/api/backups/uploads/" + u + "/chunks",
                json={"index": i, "data": base64.b64encode(b[offset : offset + 12000]).decode()},
                headers=h,
            ).status_code
            == 200
        )
    return u


def test_monitor_denominators_percentiles_and_http_errors(env):
    app, c, h, _, owner = env
    p = app.state.workflows.profile("service-v1")
    for status, queue, generation in [
        ("completed", 10, 50),
        ("needs_review", 30, 150),
        ("failed", 20, None),
    ]:
        d = app.state.jobs.new_doc("inference", owner, p)
        d.update(provider_id="q8", queue_ms=queue, generation_ms=generation, status=status)
        app.state.store.save_task(d)
    m = c.get("/api/monitoring?provider=q8").json()
    assert m["summary"]["success_rate"] == 2 / 3 and m["summary"]["error_rate"] == 1 / 3
    assert m["summary"]["generation_ms_p95"] == 145
    assert m["summary"]["generation_ms_denominator"] == 2
    assert m["summary"]["token_level_ttft_ms"] is None
    assert sum(b["request_count"] for b in m["buckets"]) == 3
    assert c.post("/api/inferences", json={}, headers=h).status_code == 422
    assert c.get("/api/monitoring").json()["summary"]["http_error_count"] == 1
    assert c.get("/api/metrics").status_code == 200
    assert c.get("/api/monitoring?seconds=999999").status_code == 422


def test_monitor_operator_isolation_and_filter(env):
    app, c, h, password, owner = env
    p = app.state.workflows.profile("service-v1")
    d = app.state.jobs.new_doc("inference", owner, p)
    d.update(provider_id="q8", status="completed")
    app.state.store.save_task(d)
    assert (
        len(
            c.get("/api/monitoring?version=service-v1&status=completed&provider=q8").json()["tasks"]
        )
        == 1
    )
    c.post("/api/sessions", json={"username": "opsuser", "password": password}, headers=GUARD)
    assert c.get("/api/monitoring").json()["tasks"] == []
    assert c.get("/api/metrics").status_code == 403


def test_load_real_http_contract_progress_comparison_and_limits(env):
    app, c, h, _, _ = env
    for change in (
        {"concurrency": 4},
        {"requests": 21},
        {"budget_seconds": 121},
        {"target": "https://invalid"},
    ):
        assert c.post("/api/loads", json=load_body(**change), headers=h).status_code == 422
    a = c.post("/api/loads", json=load_body(), headers=h)
    assert a.status_code == 202
    assert c.post("/api/engine/stop", json={}, headers=h).status_code == 409
    a = wait(c, "/api/loads/" + a.json()["id"])
    assert a["status"] == "completed" and a["completed_requests"] == 4
    assert len(a["warmup_rows"]) == 1
    assert all(r["http_status"] == 202 and r["trace_id"] and r["queue_ms"] >= 0 for r in a["rows"])
    assert all(r["config_snapshot_sha256"] == a["config_sha256"] for r in a["rows"])
    b = wait(c, "/api/loads/" + c.post("/api/loads", json=load_body(), headers=h).json()["id"])
    assert c.get("/api/loads/compare", params={"left": a["id"], "right": b["id"]}).json()[
        "comparable"
    ]
    assert fingerprint({k: v for k, v in a.items() if k != "report_sha256"}) == a["report_sha256"]


def test_load_safe_stop_does_not_cancel_running_job_and_resource_floor(env):
    app, c, h, _, _ = env
    app.state.jobs.fixture_delay = 0.15
    d = c.post("/api/loads", json=load_body(requests=20, warmup_requests=0), headers=h).json()
    assert c.post("/api/loads/" + d["id"] + "/stop", json={}, headers=h).status_code == 200
    result = wait(c, "/api/loads/" + d["id"])
    assert result["status"] == "stopped" and result["completed_requests"] < 20
    assert result["stop_reason"] == "USER_SAFE_STOP"
    app.state.operations.snapshot = lambda root: {"ram_free_gib": 0.5, "gpu": None}
    assert c.post("/api/loads", json=load_body(), headers=h).status_code == 409


def test_alert_two_profiles_sustain_ack_hysteresis_recovery(env, monkeypatch):
    app, c, h, _, owner = env
    ops = app.state.operations
    monkeypatch.setattr(app.state.engine, "status", lambda: {"ready": True})
    now = time.time()
    tasks = []
    for profile in ("service", "asset"):
        for _ in range(3):
            d = app.state.jobs.new_doc(
                "inference", owner, app.state.workflows.profile(profile + "-v1")
            )
            d.update(provider_id="q8", generation_ms=1800, queue_ms=1500, status="completed")
            app.state.store.save_task(d)
            tasks.append(d)
    ops.observe_alerts(now, {"ram_free_gib": 4})
    ops.observe_alerts(now + 11, {"ram_free_gib": 4})
    assert app.state.store.record("alerts", "alert-service-v1-latency")["status"] == "healthy"
    a = app.state.store.record("alerts", "alert-asset-v1-latency")
    assert a["status"] == "firing"
    assert (
        c.post("/api/alerts/" + a["id"] + "/ack", json={}, headers=h).json()["status"] == "firing"
    )
    for d in tasks:
        d.update(generation_ms=100, queue_ms=0)
        app.state.store.save_task(d)
    ops.observe_alerts(now + 12, {"ram_free_gib": 4})
    ops.observe_alerts(now + 23, {"ram_free_gib": 4})
    assert app.state.store.record("alerts", a["id"])["status"] == "resolved"


def test_policy_preview_version_apply_permission(env):
    app, c, h, password, _ = env
    p = app.state.operations.policy("asset")["policy"]
    result = c.post(
        "/api/alert-policies/preview", json={"policy": p, "scenario": "resource"}, headers=h
    ).json()
    assert (
        result["evidence_kind"] == "fixture_policy_preview_only"
        and result["conditions"]["resource"][0]
    )
    assert not result["persisted"]
    invalid = {**p, "minimum_samples": 0}
    assert c.post("/api/alert-policies", json=invalid, headers=h).status_code == 422
    d = c.post("/api/alert-policies", json={**p, "latency_ms": 1600}, headers=h).json()
    assert (
        c.post("/api/alert-policies/" + d["id"] + "/select", json={}, headers=h).status_code == 200
    )
    assert app.state.operations.policy("asset")["id"] == d["id"]
    login = c.post(
        "/api/sessions", json={"username": "opsuser", "password": password}, headers=GUARD
    ).json()
    oh = {**GUARD, "X-CSRF-Token": login["csrf_token"]}
    for path, body in [
        ("/api/alert-policies", p),
        ("/api/loads", load_body()),
        ("/api/backups/export", {}),
    ]:
        assert c.post(path, json=body, headers=oh).status_code == 403


def test_backup_chunks_restore_configs_policy_without_auth_or_active_overwrite(env):
    app, c, h, _, _ = env
    p = app.state.workflows.profile("service-v1").model_copy(update={"name": "還原驗證台"})
    new = app.state.workflows.save_config(p, "test")
    app.state.workflows.select(new["id"], "test")
    bundle = c.post("/api/backups/export", json={}, headers=h).json()
    text = json.dumps(bundle)
    assert all(
        x not in text
        for x in ("csrf_token", "console_session", "password", "raw_output", 'incident_text": "')
    )
    u = upload(c, h, bundle)
    preview = c.post("/api/backups/uploads/" + u + "/preview", json={}, headers=h)
    assert preview.status_code == 200 and preview.json()["counts"]["alert_policies"] == 2
    receipt = c.post(
        "/api/backups/uploads/" + u + "/restore",
        json={"bundle_sha256": bundle["sha256"], "confirmation": "RESTORE_ISOLATED_ONLY"},
        headers=h,
    ).json()
    isolated = create_app(state_dir=ROOT / receipt["relative_installation"], engine_port=18197)
    assert isolated.state.workflows.profiles["service"].name == "還原驗證台"
    assert isolated.state.operations.policy("asset")["policy"]["latency_ms"] == 1500
    assert not isolated.state.auth.initialized()
    assert isolated.state.store.metadata("active_releases") is None
    assert app.state.workflows.profiles["service"].version == new["id"]


def test_backup_tampered_unknown_secrets_bad_reference_and_expired_upload(env):
    app, c, h, _, _ = env
    original = app.state.operations.export_backup()
    for mutation in ("tamper", "secret", "reference"):
        b = copy.deepcopy(original)
        if mutation == "tamper":
            b["records"][0]["data"]["config"]["name"] = "tampered"
        elif mutation == "secret":
            b["records"][0]["data"]["secret"] = "not_allowed"
        else:
            b["model"]["sha256"] = "0" * 64
        if mutation != "tamper":
            for r in b["records"]:
                r["sha256"] = fingerprint(r["data"])
            b["sha256"] = fingerprint({k: v for k, v in b.items() if k != "sha256"})
        u = upload(c, h, b)
        assert (
            c.post("/api/backups/uploads/" + u + "/preview", json={}, headers=h).status_code == 422
        )
        app.state.operations.uploads[u]["expires"] = 0
        assert (
            c.post(
                "/api/backups/uploads/" + u + "/restore",
                json={"bundle_sha256": b["sha256"], "confirmation": "RESTORE_ISOLATED_ONLY"},
                headers=h,
            ).status_code
            == 404
        )


def test_mlflow_unavailable_preserves_report_retry_and_dedup(env, monkeypatch):
    app, c, h, _, owner = env
    ops = app.state.operations
    p = app.state.workflows.profile("service-v1")
    d = {
        "id": "testreport",
        "owner": owner,
        "created_at": time.time(),
        "status": "completed",
        "provider_id": "q8",
        "config_version": p.version,
        "config_snapshot": p.model_dump(),
        "config_sha256": fingerprint(p.model_dump()),
        "model_sha256": MODEL_SHA,
        "metrics": {"raw_task_accuracy": 0.5},
        "contract": app.state.workflows.contract(),
    }
    d["report_sha256"] = fingerprint(d)
    app.state.store.put_record("evaluations", d, create=True)
    source = {"namespace": "evaluations", "key": d["id"]}
    ops.mlflow_python = Path("tmp/nonexistent-python")
    assert c.post("/api/experiments/sync", json=source, headers=h).status_code == 202
    end = time.monotonic() + 3
    while (
        app.state.store.record("sync", "evaluations-testreport")["status"] == "running"
        and time.monotonic() < end
    ):
        time.sleep(0.01)
    assert app.state.store.record("sync", "evaluations-testreport")["status"] == "failed"
    assert app.state.store.record("evaluations", "testreport") == d
    calls = []

    class Process:
        returncode = 0

        async def communicate(self, body):
            calls.append(body)
            return b'{"run_id":"test-double-run","mlflow_version":"test-double"}', b""

    async def spawn(*args, **kw):
        return Process()

    monkeypatch.setattr("console_app.operations.asyncio.create_subprocess_exec", spawn)
    ops.mlflow_python = Path(__file__)
    c.post("/api/experiments/sync", json=source, headers=h)
    end = time.monotonic() + 3
    while (
        app.state.store.record("sync", "evaluations-testreport")["status"] == "running"
        and time.monotonic() < end
    ):
        time.sleep(0.01)
    assert (
        c.post("/api/experiments/sync", json=source, headers=h).json()["run_id"]
        == "test-double-run"
    )
    assert len(calls) == 1


def test_holdout_manifest_frozen_candidate_and_second_run_classification(env):
    app, c, h, _, owner = env
    d, m = app.state.workflows.dataset("equipment-holdout-v1")
    assert (
        len(d["cases"]) == 16
        and m["sha256"] == hashlib.sha256((ROOT / "data/holdout-v1.json").read_bytes()).hexdigest()
    )
    frozen = json.loads((ROOT / "schemas/holdout-candidate-freeze.json").read_bytes())
    p = json.loads((ROOT / "profiles/recommended-service.json").read_bytes())
    assert (
        fingerprint({k: v for k, v in p.items() if k != "version"}) == frozen["profiles"]["service"]
    )
    r = c.post(
        "/api/evaluations",
        json={
            "profile_version": "service-v1",
            "provider_id": "fixture",
            "dataset_id": "equipment-holdout-v1",
        },
        headers=h,
    )
    assert r.status_code == 202
    result = wait(c, "/api/evaluations/" + r.json()["id"])
    assert result["total_cases"] == 16 and result["metrics"]["generated_count"] == 14
    assert result["evidence_use"] == "observed_or_changed_candidate_development"
    assert "HOLDOUT_REPORT_NOT_RELEASE_AUTHORITY" in app.state.workflows.gate(result)["reasons"]


def test_alert_missing_window_samples_do_not_resolve_firing(env, monkeypatch):
    app, c, h, password, owner = env
    ops = app.state.operations
    monkeypatch.setattr(app.state.engine, "status", lambda: {"ready": True})
    now = time.time()
    ops.observe_alerts(now, {"ram_free_gib": 4})
    d = app.state.store.record("alerts", "alert-asset-v1-latency")
    d.update(status="firing", bad_since=now, good_since=None)
    app.state.store.put_record("alerts", d)
    ops.observe_alerts(now + 30, {"ram_free_gib": 4})
    assert app.state.store.record("alerts", d["id"])["status"] == "firing"
