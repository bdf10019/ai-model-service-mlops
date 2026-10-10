from __future__ import annotations

import asyncio
import base64
import json
import math
import os
import re
import time
import uuid
from typing import Literal

import httpx
import psutil
from fastapi import HTTPException, Request
from fastapi.responses import PlainTextResponse
from pydantic import Field, ValidationError, model_validator

from .domain import Profile, StrictModel, fingerprint
from .engine import MODEL_SHA, hash_file
from .jobs import TERMINAL
from .store import Store
from .workflows import difference, percentile, validate_profile


class AlertPolicy(StrictModel):
    profile_id: Literal["service", "asset"]
    latency_ms: int = Field(ge=500, le=60000)
    error_rate: float = Field(ge=0.01, le=1)
    queue_ms: int = Field(ge=100, le=10000)
    window_seconds: int = Field(ge=15, le=900)
    minimum_samples: int = Field(ge=3, le=100)
    sustain_seconds: int = Field(ge=5, le=300)
    recovery_seconds: int = Field(ge=5, le=300)
    recovery_ratio: float = Field(ge=0.2, le=0.95)
    minimum_free_ram_mib: int = Field(ge=256, le=2048)


class LoadRequest(StrictModel):
    profile_version: str = Field(max_length=64)
    provider_id: Literal["q8", "fixture"]
    dataset_id: Literal["synthetic-smoke-v1"] = "synthetic-smoke-v1"
    concurrency: int = Field(ge=1, le=3)
    requests: int = Field(ge=1, le=20)
    request_timeout_seconds: int = Field(ge=2, le=60)
    budget_seconds: int = Field(ge=5, le=120)
    warmup_requests: int = Field(ge=0, le=2, default=1)
    confirmation: Literal["LOCAL_BOUNDED_LOAD"]


class PolicyPreview(StrictModel):
    policy: AlertPolicy
    scenario: Literal["latency", "error", "queue", "unavailable", "resource", "recovered"]


class UploadStart(StrictModel):
    bytes: int = Field(ge=1, le=524288)
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


class Chunk(StrictModel):
    index: int = Field(ge=0, le=43)
    data: str = Field(max_length=16000)


class Restore(StrictModel):
    bundle_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    confirmation: Literal["RESTORE_ISOLATED_ONLY"]


class SyncRequest(StrictModel):
    namespace: Literal["evaluations", "loads"]
    key: str = Field(pattern=r"^[a-zA-Z0-9_-]{1,64}$")


class BackupRecord(StrictModel):
    namespace: Literal["configs", "alert_policies", "evaluations", "loads", "releases"]
    id: str = Field(pattern=r"^[a-zA-Z0-9_-]{1,64}$")
    data: dict
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


class Backup(StrictModel):
    format: Literal["console-business-backup-v1"]
    product_version: Literal["0.3.0", "0.4.0", "0.4.1"]
    created_at: float
    model: dict
    records: list[BackupRecord] = Field(max_length=256)
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")

    @model_validator(mode="after")
    def hashes(self):
        d = self.model_dump()
        if fingerprint({k: v for k, v in d.items() if k != "sha256"}) != self.sha256:
            raise ValueError("backup checksum mismatch")
        if len({(r.namespace, r.id) for r in self.records}) != len(self.records):
            raise ValueError("duplicate records")
        for r in self.records:
            if fingerprint(r.data) != r.sha256:
                raise ValueError("record checksum mismatch")
        return self


class HttpTelemetry:
    def __init__(self, app, ops):
        self.app, self.ops = app, ops

    async def __call__(self, scope, receive, send):
        if (
            scope["type"] != "http"
            or scope["path"] != "/api/inferences"
            or scope["method"] != "POST"
        ):
            return await self.app(scope, receive, send)
        start, status = time.monotonic(), 500

        async def capture(message):
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
            await send(message)

        try:
            await self.app(scope, receive, capture)
        finally:
            state = scope.get("state", {})
            self.ops.record(
                "http",
                {
                    "owner": state.get("actor_id"),
                    "task_id": state.get("task_id"),
                    "request_id": state.get("request_id"),
                    "http_status": status,
                    "http_accept_ms": round((time.monotonic() - start) * 1000, 2),
                },
                limit=3000,
            )


def task_projection(t):
    keys = (
        "id",
        "trace_id",
        "status",
        "created_at",
        "finished_at",
        "profile_id",
        "profile_version",
        "provider_id",
        "model_sha256",
        "release_id",
        "error",
        "queue_ms",
        "generation_ms",
        "elapsed_ms",
        "config_snapshot_sha256",
    )
    return {k: t.get(k) for k in keys}


def summarize(tasks):
    terminal = [t for t in tasks if t["status"] in TERMINAL]
    success = sum(t["status"] in {"completed", "needs_review"} for t in terminal)
    failed = sum(t["status"] == "failed" for t in terminal)
    result = {
        "request_count": len(tasks),
        "terminal_count": len(terminal),
        "success_count": success,
        "error_count": failed,
        "success_rate": success / len(terminal) if terminal else None,
        "error_rate": failed / len(terminal) if terminal else None,
        "token_level_ttft_ms": None,
    }
    for key in ("queue_ms", "generation_ms", "elapsed_ms"):
        values = [t[key] for t in tasks if isinstance(t.get(key), (int, float))]
        result[key + "_p50"], result[key + "_p95"] = (
            percentile(values, 0.5),
            percentile(values, 0.95),
        )
        result[key + "_denominator"] = len(values)
    return result


class Operations:
    def __init__(self, root, store, jobs, workflows, snapshot):
        self.root, self.store, self.jobs, self.w, self.snapshot = (
            root,
            store,
            jobs,
            workflows,
            snapshot,
        )
        self.state = store.path.parent
        self.sampler = None
        self.loads: dict[str, asyncio.Task] = {}
        self.stops: set[str] = set()
        self.uploads = {}
        self.sync_task = None
        self.jobs.external_busy = lambda: any(not t.done() for t in self.loads.values())
        self.client_factory = httpx.AsyncClient  # Test transport replacement only; no public URL.
        self.mlflow_python = root / ".venv-mlflow/Scripts/python.exe"
        for profile in ("service", "asset"):
            key = "alert-" + profile + "-v1"
            if not store.record("alert_policies", key):
                p = AlertPolicy(
                    profile_id=profile,
                    latency_ms=2000 if profile == "service" else 1500,
                    error_rate=0.2 if profile == "service" else 0.1,
                    queue_ms=2000 if profile == "service" else 1000,
                    window_seconds=60,
                    minimum_samples=3,
                    sustain_seconds=10,
                    recovery_seconds=10,
                    recovery_ratio=0.8,
                    minimum_free_ram_mib=768 if profile == "service" else 1024,
                )
                store.put_record(
                    "alert_policies",
                    {
                        "id": key,
                        "created_at": time.time(),
                        "policy": p.model_dump(),
                        "sha256": fingerprint(p.model_dump()),
                    },
                    create=True,
                )
        for d in store.records("loads"):
            if d["status"] in {"running", "stopping"}:
                d.update(status="failed", error="SERVICE_RESTARTED")
                store.put_record("loads", d)
        for d in store.records("sync"):
            if d["status"] == "running":
                d.update(status="failed", error="SERVICE_RESTARTED_RETRY_SAFE")
                store.put_record("sync", d)

    def rows(self, namespace, limit=3000):
        with self.store.connect() as db:
            rows = db.execute(
                "SELECT document FROM records WHERE namespace=? ORDER BY created DESC LIMIT ?",
                (namespace, limit),
            ).fetchall()
        return [json.loads(r[0]) for r in rows]

    def record(self, namespace, values, limit=720):
        d = {"id": uuid.uuid4().hex, "created_at": time.time(), **values}
        self.store.put_record(namespace, d, create=True)
        with self.store.connect() as db:
            db.execute(
                "DELETE FROM records WHERE namespace=? AND id NOT IN (SELECT id FROM records WHERE namespace=? ORDER BY created DESC LIMIT ?)",
                (namespace, namespace, limit),
            )
        return d

    def start(self):
        async def sample():
            while True:
                try:
                    env = await asyncio.to_thread(self.snapshot, self.root)
                    env.update(
                        cpu_percent=await asyncio.to_thread(psutil.cpu_percent, 0.1),
                        process_rss_mib=round(psutil.Process().memory_info().rss / 2**20, 2),
                        gpu_unavailable_reason=None
                        if env["gpu"]
                        else "NVIDIA_SMI_UNAVAILABLE_OR_QUERY_FAILED",
                    )
                    self.record("resources", {"environment": env})
                    self.observe_alerts(time.time(), env)
                    if env["ram_free_gib"] * 1024 < 512:
                        self.stops.update(k for k, t in self.loads.items() if not t.done())
                except asyncio.CancelledError:
                    raise
                except Exception:
                    self.record("sampling_errors", {"error": "RESOURCE_SAMPLE_FAILED"}, limit=20)
                await asyncio.sleep(5)

        self.sampler = asyncio.create_task(sample())

    async def close(self):
        self.stops.update(self.loads)
        if self.sampler:
            self.sampler.cancel()
            await asyncio.gather(self.sampler, return_exceptions=True)
        # Jobs close separately; a stopped load waits for owned in-flight requests, never kills unrelated apps.
        if self.loads:
            await asyncio.wait(list(self.loads.values()), timeout=2)
            for t in self.loads.values():
                if not t.done():
                    t.cancel()
            await asyncio.gather(*self.loads.values(), return_exceptions=True)
        if self.sync_task and not self.sync_task.done():
            self.sync_task.cancel()
            await asyncio.gather(self.sync_task, return_exceptions=True)

    def monitoring(self, user, provider="", version="", status="", seconds=3600):
        if seconds not in (60, 300, 3600):
            raise HTTPException(422, "MONITOR_WINDOW_INVALID")
        now = time.time()
        with self.store.connect() as db:
            rows = db.execute(
                "SELECT document FROM tasks WHERE kind='inference' AND created>=? ORDER BY created DESC LIMIT 3000",
                (now - seconds,),
            ).fetchall()
        tasks = [json.loads(r[0]) for r in rows]
        tasks = [
            t
            for t in tasks
            if (user["role"] != "operator" or t["owner"] == user["id"])
            and (not provider or t["provider_id"] == provider)
            and (not version or t.get("profile_version") == version)
            and (not status or t["status"] == status)
        ]
        ids = {t["id"] for t in tasks}
        http = [
            r
            for r in self.rows("http")
            if r["created_at"] >= now - seconds
            and (user["role"] != "operator" or r.get("owner") == user["id"])
            and (
                r.get("task_id") in ids
                or not (provider or version or status)
                and not r.get("task_id")
            )
        ]
        buckets = []
        width = seconds / 12
        for i in range(12):
            a, b = now - seconds + i * width, now - seconds + (i + 1) * width
            selected = [t for t in tasks if a <= t["created_at"] <= b]
            buckets.append(
                {"from": a, "to": b, **summarize(selected), "task_ids": [t["id"] for t in selected]}
            )
        summary = summarize(tasks)
        summary.update(
            http_request_count=len(http),
            http_error_count=sum(r["http_status"] >= 400 for r in http),
            http_accept_p95_ms=percentile([r["http_accept_ms"] for r in http], 0.95),
        )
        resources = self.rows("resources", 720)
        return {
            "summary": summary,
            "tasks": [task_projection(t) for t in tasks],
            "http": [{k: v for k, v in r.items() if k != "owner"} for r in http],
            "buckets": buckets,
            "resources": [r for r in resources if r["created_at"] >= now - seconds],
            "sample_errors": self.rows("sampling_errors", 5),
            "sampled_at": now,
            "limits": {
                "task_retention": "all stored task metadata; query max 3000",
                "http_samples": 3000,
                "resource_samples": 720,
                "sampling_seconds": 5,
            },
            "definitions": {
                "HTTP": "server POST acceptance/rejection wall time; not async completion",
                "queue": "created to engine-slot acquisition",
                "generation": "full engine response; non-streaming",
                "elapsed": "task created to completion; percentile uses only available timing values",
                "success": "completed or needs_review / terminal tasks; cancelled remains in denominator",
                "resource": "host samples, not per-request attribution",
                "TTFT": "unavailable",
            },
        }

    def policy(self, profile):
        key = (self.store.metadata("selected_alert_policies") or {}).get(
            profile, "alert-" + profile + "-v1"
        )
        d = self.store.record("alert_policies", key)
        if not d or d["sha256"] != fingerprint(d["policy"]):
            raise HTTPException(409, "ALERT_POLICY_HASH_MISMATCH")
        return d

    def conditions(self, p, summary, env, ready):
        enough = summary["terminal_count"] >= p.minimum_samples
        return {
            "latency": (
                enough and (summary["generation_ms_p95"] or 0) > p.latency_ms,
                summary["generation_ms_p95"],
                p.latency_ms,
            ),
            "error": (
                enough and (summary["error_rate"] or 0) > p.error_rate,
                summary["error_rate"],
                p.error_rate,
            ),
            "queue": (
                enough and (summary["queue_ms_p95"] or 0) > p.queue_ms,
                summary["queue_ms_p95"],
                p.queue_ms,
            ),
            "unavailable": (not ready, ready, True),
            "resource": (
                env["ram_free_gib"] * 1024 < p.minimum_free_ram_mib,
                env["ram_free_gib"] * 1024,
                p.minimum_free_ram_mib,
            ),
        }

    def observe_alerts(self, now, env):
        for profile in ("service", "asset"):
            policy = self.policy(profile)
            p = AlertPolicy.model_validate(policy["policy"])
            with self.store.connect() as db:
                rows = db.execute(
                    "SELECT document FROM tasks WHERE kind='inference' AND created>=?",
                    (now - p.window_seconds,),
                ).fetchall()
            tasks = [json.loads(r[0]) for r in rows]
            tasks = [
                t for t in tasks if t.get("profile_id") == profile and t.get("provider_id") == "q8"
            ]
            summary = summarize(tasks)
            for kind, (bad, value, threshold) in self.conditions(
                p, summary, env, self.jobs.engine.status()["ready"]
            ).items():
                key = policy["id"] + "-" + kind
                d = self.store.record("alerts", key)
                if not d:
                    d = {
                        "id": key,
                        "created_at": now,
                        "profile_id": profile,
                        "policy_id": policy["id"],
                        "policy_sha256": policy["sha256"],
                        "kind": kind,
                        "status": "healthy",
                        "bad_since": None,
                        "good_since": None,
                        "events": [],
                        "acknowledged": False,
                    }
                    self.store.put_record("alerts", d, create=True)
                previous = d["status"]
                recovering = not bad
                if kind in ("latency", "error", "queue"):
                    recovering = (
                        value is not None
                        and value <= threshold * p.recovery_ratio
                        and summary["terminal_count"] >= p.minimum_samples
                    )
                if kind == "resource":
                    recovering = value >= threshold / p.recovery_ratio
                if bad:
                    d["good_since"] = None
                    if d["bad_since"] is None:
                        d["bad_since"] = now
                    d["status"] = (
                        "firing" if now - d["bad_since"] >= p.sustain_seconds else "pending"
                    )
                elif d["status"] == "firing":
                    if recovering:
                        if d["good_since"] is None:
                            d["good_since"] = now
                        if now - d["good_since"] >= p.recovery_seconds:
                            d.update(status="resolved", bad_since=None)
                    else:
                        d["good_since"] = None
                else:
                    d.update(
                        status="healthy" if not d["events"] else "resolved",
                        bad_since=None,
                        good_since=None,
                    )
                if d["status"] != previous:
                    if d["status"] == "firing":
                        d["acknowledged"] = False
                    d["events"] = (
                        d["events"]
                        + [
                            {
                                "time": now,
                                "status": d["status"],
                                "value": value,
                                "threshold": threshold,
                            }
                        ]
                    )[-100:]
                d.update(
                    value=value,
                    threshold=threshold,
                    observed_at=now,
                    minimum_samples=p.minimum_samples,
                    sample_count=summary["terminal_count"],
                    task_ids=[t["id"] for t in tasks][:100],
                )
                self.store.put_record("alerts", d)

    def preview_policy(self, data):
        p = data.policy
        fake = [
            {"status": "completed", "generation_ms": 1800, "queue_ms": 1500, "elapsed_ms": 3300}
            for _ in range(p.minimum_samples)
        ]
        if data.scenario == "error":
            fake[-1]["status"] = "failed"
        env = {"ram_free_gib": 0.9 if data.scenario == "resource" else 4}
        if data.scenario == "recovered":
            for t in fake:
                t.update(generation_ms=100, queue_ms=0, elapsed_ms=100)
        conditions = self.conditions(p, summarize(fake), env, data.scenario != "unavailable")
        return {
            "evidence_kind": "fixture_policy_preview_only",
            "persisted": False,
            "conditions": conditions,
            "trigger_after_seconds": p.sustain_seconds,
            "recover_after_seconds": p.recovery_seconds,
            "summary": summarize(fake),
            "environment": env,
        }

    def get_load(self, key, user):
        d = self.store.record("loads", key)
        if not d:
            raise HTTPException(404, "LOAD_NOT_FOUND")
        if user["role"] == "operator" and d["owner"] != user["id"]:
            raise HTTPException(403, "LOAD_FORBIDDEN")
        return d

    async def begin_load(self, data, user, request):
        if any(not t.done() for t in self.loads.values()):
            raise HTTPException(409, "LOAD_ALREADY_RUNNING")
        if self.jobs.control_task and not self.jobs.control_task.done():
            raise HTTPException(409, "ENGINE_BUSY")
        p = self.w.profile(data.profile_version)
        if p.max_tokens > 384:
            raise HTTPException(422, "LOAD_TOKEN_BUDGET_EXCEEDED")
        if data.provider_id == "q8" and not self.jobs.engine.status()["ready"]:
            raise HTTPException(503, "ENGINE_UNAVAILABLE")
        env = await asyncio.to_thread(self.snapshot, self.root)
        if env["ram_free_gib"] * 1024 < 768:
            raise HTTPException(409, "LOAD_RESOURCE_BUDGET_INSUFFICIENT")
        # Target comes from the actual listening scope, never a user-supplied URL or Host forwarding.
        port = request.scope["server"][1]
        if not 1024 <= port <= 65535:
            raise HTTPException(422, "LOCAL_TARGET_INVALID")
        base = f"http://127.0.0.1:{port}"
        headers = {"X-Console-Request": "1", "X-CSRF-Token": user["csrf_token"], "Origin": base}
        cookie = request.cookies.get("console_session", "")
        d = {
            "id": uuid.uuid4().hex,
            "owner": user["id"],
            "created_at": time.time(),
            "status": "running",
            "config_version": p.version,
            "config_snapshot": p.model_dump(),
            "config_sha256": fingerprint(p.model_dump()),
            "model_sha256": MODEL_SHA if data.provider_id == "q8" else None,
            "provider_id": data.provider_id,
            "evidence_kind": "measured" if data.provider_id == "q8" else "fixture",
            "settings": data.model_dump(),
            "dataset_sha256": hash_file(self.root / "data/samples.json"),
            "contract": self.w.contract(p),
            "environment_start": env,
            "resource_samples": [],
            "rows": [],
            "warmup_rows": [],
            "completed_requests": 0,
            "stop_reason": None,
            "error": None,
            "target": "this_installation_loopback",
            "token_level_ttft_ms": None,
            "alert_policy": self.policy(p.id),
        }
        self.store.put_record("loads", d, create=True)
        self.store.audit(user["id"], "load_started", d["id"])

        async def run():
            start = time.monotonic()
            index = 0
            async with self.client_factory(
                base_url=base,
                headers=headers,
                cookies={"console_session": cookie},
                timeout=5,
                trust_env=False,
                follow_redirects=False,
            ) as client:

                async def one(number, warmup=False):
                    s = self.jobs.samples[number % len(self.jobs.samples)]
                    body = {k: v for k, v in s.items() if k != "title" and k != "id"}
                    body.update(
                        profile_id=p.id,
                        profile_version=p.version,
                        provider_id=data.provider_id,
                        mode="sandbox",
                        sample_id=s["id"],
                    )
                    row = {
                        "number": number,
                        "sample_id": s["id"],
                        "task_id": None,
                        "status": "failed",
                        "error": None,
                    }
                    request_start = time.monotonic()
                    try:
                        resp = await client.post(
                            "/api/inferences",
                            json=body,
                            headers={"Idempotency-Key": uuid.uuid4().hex},
                        )
                        row.update(
                            http_status=resp.status_code,
                            http_accept_ms=round((time.monotonic() - request_start) * 1000, 2),
                            request_id=resp.headers.get("x-request-id"),
                        )
                        if resp.status_code != 202:
                            row["error"] = resp.json().get("error", {}).get("code", "HTTP_REJECTED")
                        else:
                            task = resp.json()
                            row["task_id"] = task["id"]
                            overdue = False
                            while task["status"] not in TERMINAL:
                                elapsed = time.monotonic() - request_start
                                if elapsed > data.request_timeout_seconds:
                                    overdue = True
                                if overdue or d["id"] in self.stops:
                                    if task["status"] == "queued":
                                        await client.post(
                                            "/api/tasks/" + task["id"] + "/cancel", json={}
                                        )
                                await asyncio.sleep(0.1)
                                result = await client.get("/api/tasks/" + task["id"])
                                if result.status_code != 200:
                                    raise RuntimeError("poll unavailable")
                                task = result.json()
                            row.update(
                                **{
                                    k: task.get(k)
                                    for k in (
                                        "status",
                                        "trace_id",
                                        "queue_ms",
                                        "generation_ms",
                                        "elapsed_ms",
                                        "error",
                                        "profile_version",
                                        "model_sha256",
                                        "config_snapshot_sha256",
                                    )
                                }
                            )
                            if overdue:
                                row.update(
                                    status="failed",
                                    error="LOAD_REQUEST_DEADLINE_EXCEEDED",
                                    underlying_status=task["status"],
                                )
                    except Exception:
                        row.update(status="failed", error="LOAD_HTTP_FAILED")
                        self.stops.add(d["id"])
                        d["stop_reason"] = "HTTP_FAILURE_STOP"
                    row["client_turnaround_ms"] = round(
                        (time.monotonic() - request_start) * 1000, 2
                    )
                    (d["warmup_rows"] if warmup else d["rows"]).append(row)
                    d["completed_requests"] = len(d["rows"])
                    self.store.put_record("loads", d)

                try:
                    for n in range(data.warmup_requests):
                        if d["id"] in self.stops:
                            break
                        await one(n, True)
                    measured_start = time.monotonic()
                    while index < data.requests and d["id"] not in self.stops:
                        env = await asyncio.to_thread(self.snapshot, self.root)
                        d["resource_samples"].append(env)
                        if (
                            env["ram_free_gib"] * 1024 < 512
                            or env.get("gpu")
                            and env["gpu"]["total_mib"] - env["gpu"]["used_mib"] < 128
                        ):
                            d["stop_reason"] = "HARD_RESOURCE_FLOOR"
                            self.stops.add(d["id"])
                            break
                        if time.monotonic() - start >= data.budget_seconds:
                            d["stop_reason"] = "LOAD_TIME_BUDGET"
                            self.stops.add(d["id"])
                            break
                        count = min(data.concurrency, data.requests - index)
                        await asyncio.gather(*(one(n) for n in range(index, index + count)))
                        index += count
                        if (
                            len(d["rows"]) >= 3
                            and sum(r["status"] == "failed" for r in d["rows"]) / len(d["rows"])
                            > 0.5
                        ):
                            d["stop_reason"] = "ERROR_RATE_STOP"
                            self.stops.add(d["id"])
                        await asyncio.sleep(
                            0.2
                        )  # yield between bounded bursts for ordinary inference
                    if d["id"] in self.stops and not d["stop_reason"]:
                        d["stop_reason"] = "USER_SAFE_STOP"
                    d["measurement_seconds"] = round(time.monotonic() - measured_start, 3)
                    d.update(
                        status="stopped" if d["id"] in self.stops else "completed",
                        completed_at=time.time(),
                        wall_seconds=round(time.monotonic() - start, 3),
                        metrics=summarize(d["rows"]),
                    )
                    d["metrics"].update(
                        throughput_rps=len(d["rows"]) / max(0.001, d["measurement_seconds"]),
                        http_accept_p95_ms=percentile(
                            [r["http_accept_ms"] for r in d["rows"] if "http_accept_ms" in r], 0.95
                        ),
                        client_turnaround_p95_ms=percentile(
                            [r["client_turnaround_ms"] for r in d["rows"]], 0.95
                        ),
                    )
                    d["report_sha256"] = fingerprint(d)
                except asyncio.CancelledError:
                    d.update(status="failed", error="SERVICE_RESTARTED_OR_SHUTDOWN")
                    raise
                finally:
                    self.store.put_record("loads", d)
                    self.store.audit(user["id"], "load_" + d["status"], d["id"])

        self.loads[d["id"]] = asyncio.create_task(run())
        return d

    def export_backup(self):
        records = []
        for namespace in ("configs", "alert_policies", "evaluations", "loads", "releases"):
            with self.store.connect() as db:
                count = db.execute(
                    "SELECT count(*) FROM records WHERE namespace=?", (namespace,)
                ).fetchone()[0]
            if count > 64:
                raise HTTPException(409, "BACKUP_RECORD_LIMIT_SELECT_SMALLER_INSTALLATION")
            for d in self.store.records(namespace):
                if namespace == "configs":
                    data = {
                        "config": d["config"],
                        "config_sha256": d["sha256"],
                        "selected": self.w.profiles[d["config"]["id"]].version == d["id"],
                    }
                elif namespace == "alert_policies":
                    data = {
                        "policy": d["policy"],
                        "policy_sha256": d["sha256"],
                        "selected": self.policy(d["policy"]["profile_id"])["id"] == d["id"],
                    }
                else:
                    data = {
                        k: d.get(k)
                        for k in (
                            "config_version",
                            "model_sha256",
                            "provider_id",
                            "status",
                            "created_at",
                            "report_sha256",
                            "bundle_sha256",
                        )
                    }
                    if namespace in ("evaluations", "loads"):
                        data["metrics"] = d.get("metrics", {})
                    if namespace == "evaluations":
                        data["dataset_id"] = d["dataset"]["id"]
                    data["record_use"] = "historical_import_only_no_release_authority"
                records.append(
                    {
                        "namespace": namespace,
                        "id": d["id"],
                        "data": data,
                        "sha256": fingerprint(data),
                    }
                )
        d = {
            "format": "console-business-backup-v1",
            "product_version": "0.4.1",
            "created_at": time.time(),
            "model": {
                "sha256": MODEL_SHA,
                "manifest_sha256": hash_file(self.root / "schemas/model-import.json"),
                "weights_included": False,
            },
            "records": records,
        }
        d["sha256"] = fingerprint(d)
        if len(json.dumps(d, ensure_ascii=False).encode()) > 524288:
            raise HTTPException(409, "BACKUP_TOO_LARGE")
        return d

    def validate_backup(self, raw):
        try:
            d = Backup.model_validate(raw).model_dump()
            if d["model"] != {
                "sha256": MODEL_SHA,
                "manifest_sha256": hash_file(self.root / "schemas/model-import.json"),
                "weights_included": False,
            }:
                raise ValueError("incompatible model")
            config_ids = set()
            for r in d["records"]:
                x = r["data"]
                if r["namespace"] == "configs":
                    if set(x) != {"config", "config_sha256", "selected"} or not isinstance(
                        x["selected"], bool
                    ):
                        raise ValueError("unknown config keys")
                    p = Profile.model_validate(x["config"])
                    validate_profile(p)
                    if p.version != r["id"] or fingerprint(x["config"]) != x["config_sha256"]:
                        raise ValueError("bad config")
                    config_ids.add(r["id"])
                elif r["namespace"] == "alert_policies":
                    if set(x) != {"policy", "policy_sha256", "selected"} or not isinstance(
                        x["selected"], bool
                    ):
                        raise ValueError("policy fields")
                    AlertPolicy.model_validate(x["policy"])
                    if fingerprint(x["policy"]) != x["policy_sha256"]:
                        raise ValueError("policy checksum")
                else:
                    allowed = {
                        "config_version",
                        "model_sha256",
                        "provider_id",
                        "status",
                        "created_at",
                        "report_sha256",
                        "bundle_sha256",
                        "record_use",
                    }
                    if r["namespace"] in ("evaluations", "loads"):
                        allowed.add("metrics")
                    if r["namespace"] == "evaluations":
                        allowed.add("dataset_id")
                    if (
                        set(x) != allowed
                        or x["record_use"] != "historical_import_only_no_release_authority"
                    ):
                        raise ValueError("unknown record fields")
                    if x["model_sha256"] not in (None, MODEL_SHA) or x["provider_id"] not in (
                        None,
                        "q8",
                        "fixture",
                    ):
                        raise ValueError("model")
                    if x["status"] not in (
                        "completed",
                        "failed",
                        "stopped",
                        "running",
                        "stopping",
                        "queued",
                        "pending",
                        "approved",
                        "rejected",
                    ):
                        raise ValueError("status")
                    for k in ("report_sha256", "bundle_sha256"):
                        if x[k] is not None and not re.fullmatch("[a-f0-9]{64}", x[k]):
                            raise ValueError("hash")
                    if "metrics" in x and (
                        len(x["metrics"]) > 40
                        or any(
                            not re.fullmatch("[a-z0-9_]{1,64}", k)
                            or not isinstance(v, (int, float, type(None)))
                            or isinstance(v, (int, float))
                            and not math.isfinite(v)
                            for k, v in x["metrics"].items()
                        )
                    ):
                        raise ValueError("metrics")
                    if "dataset_id" in x and x["dataset_id"] not in (
                        "equipment-triage-v1",
                        "equipment-holdout-v1",
                        "equipment-holdout-v2",
                    ):
                        raise ValueError("dataset")
                    if not isinstance(x["created_at"], (int, float)) or not math.isfinite(
                        x["created_at"]
                    ):
                        raise ValueError("time")
            if any(
                r["data"]["config_version"] not in config_ids
                for r in d["records"]
                if r["namespace"] not in ("configs", "alert_policies")
            ):
                raise ValueError("missing config reference")
            for ns, field in (("configs", "config"), ("alert_policies", "policy")):
                selected = [
                    r["data"][field]["id" if field == "config" else "profile_id"]
                    for r in d["records"]
                    if r["namespace"] == ns and r["data"]["selected"]
                ]
                if len(selected) != len(set(selected)):
                    raise ValueError("duplicate selected versions")
            return d
        except (ValidationError, ValueError, TypeError, KeyError, HTTPException) as exc:
            raise HTTPException(422, "BACKUP_FORMAT_HASH_OR_COMPATIBILITY_INVALID") from exc

    def upload(self, key, owner):
        self.uploads = {k: v for k, v in self.uploads.items() if v["expires"] > time.time()}
        d = self.uploads.get(key)
        if not d or d["owner"] != owner:
            raise HTTPException(404, "BACKUP_UPLOAD_EXPIRED")
        return d

    def restore_isolated(self, upload, data, owner):
        bundle = upload.get("validated")
        if not bundle or bundle["sha256"] != data.bundle_sha256:
            raise HTTPException(409, "BACKUP_PREVIEW_REQUIRED")
        if self.jobs.active or self.jobs.waiting or any(not t.done() for t in self.loads.values()):
            raise HTTPException(409, "ENGINE_BUSY")
        target = self.state / "restores" / uuid.uuid4().hex
        target.resolve().relative_to(self.root.resolve())
        restored = Store(target)
        with restored.connect() as db:
            for r in bundle["records"]:
                ns = (
                    r["namespace"]
                    if r["namespace"] in ("configs", "alert_policies")
                    else "imported_" + r["namespace"]
                )
                d = {
                    "id": r["id"],
                    "created_at": bundle["created_at"],
                    "imported_from_backup": bundle["sha256"],
                    **r["data"],
                }
                if ns == "configs":
                    d.update(
                        sha256=r["data"]["config_sha256"],
                        author="backup-import",
                        restored_from=None,
                    )
                if ns == "alert_policies":
                    d["sha256"] = r["data"]["policy_sha256"]
                db.execute(
                    "INSERT INTO records VALUES(?,?,?,?)",
                    (ns, d["id"], json.dumps(d, ensure_ascii=False), d["created_at"]),
                )
            selected_configs = {
                r["data"]["config"]["id"]: r["id"]
                for r in bundle["records"]
                if r["namespace"] == "configs" and r["data"]["selected"]
            }
            db.execute(
                "INSERT INTO metadata VALUES(?,?)",
                ("selected_configs", json.dumps(selected_configs)),
            )
            selected_policies = {
                r["data"]["policy"]["profile_id"]: r["id"]
                for r in bundle["records"]
                if r["namespace"] == "alert_policies" and r["data"]["selected"]
            }
            db.execute(
                "INSERT INTO metadata VALUES(?,?)",
                ("selected_alert_policies", json.dumps(selected_policies)),
            )
            db.execute(
                "INSERT INTO metadata VALUES(?,?)",
                (
                    "restore_receipt",
                    json.dumps(
                        {
                            "source_sha256": bundle["sha256"],
                            "active_releases_restored": False,
                            "model_reference_verified": True,
                        }
                    ),
                ),
            )
        receipt = {
            "id": target.name,
            "created_at": time.time(),
            "bundle_sha256": bundle["sha256"],
            "status": "isolated_restore_verified_no_active_release",
            "relative_installation": target.relative_to(self.root).as_posix(),
            "records": len(bundle["records"]),
            "users_restored": 0,
            "sessions_restored": 0,
            "weights_restored": False,
            "active_releases_restored": False,
            "model_sha256": MODEL_SHA,
            "model_file_verified": False,
            "model_reference_verified": True,
            "next_step": "建立隔離安裝管理者，重新取得／核對權重，再評測核准；不覆寫目前服務。",
        }
        with restored.connect() as db:
            assert db.execute("SELECT count(*) FROM records").fetchone()[0] == len(
                bundle["records"]
            )
            assert db.execute("SELECT count(*) FROM users").fetchone()[0] == 0
        self.store.put_record("restore_receipts", receipt, create=True)
        self.store.audit(owner, "isolated_backup_restored", receipt["id"])
        return receipt

    async def sync(self, data, user):
        if self.sync_task and not self.sync_task.done():
            raise HTTPException(409, "MLFLOW_SYNC_BUSY")
        source = (
            self.w.evaluation(data.key, user)
            if data.namespace == "evaluations"
            else self.get_load(data.key, user)
        )
        if source["status"] not in ("completed", "stopped") or source["provider_id"] != "q8":
            raise HTTPException(409, "MLFLOW_REQUIRES_REAL_REPORT")
        body = {k: v for k, v in source.items() if k != "report_sha256"}
        if fingerprint(body) != source.get("report_sha256"):
            raise HTTPException(409, "REPORT_HASH_MISMATCH")
        key = data.namespace + "-" + source["id"]
        old = self.store.record("sync", key)
        if old and old["status"] == "synced":
            return old
        d = {
            "id": key,
            "created_at": time.time(),
            "namespace": data.namespace,
            "source_id": source["id"],
            "source_sha256": source["report_sha256"],
            "status": "running",
            "attempts": (old or {}).get("attempts", 0) + 1,
            "error": None,
            "owner": user["id"],
        }
        self.store.put_record("sync", d, create=old is None)

        async def execute():
            proc = None
            try:
                if not self.mlflow_python.is_file():
                    d["error"] = "MLFLOW_DEPENDENCY_UNAVAILABLE"
                    raise RuntimeError("dependency unavailable")
                if psutil.virtual_memory().available < 512 * 2**20:
                    d["error"] = "MLFLOW_RESOURCE_BUDGET_INSUFFICIENT"
                    raise RuntimeError("resource floor")
                artifact = {
                    "namespace": data.namespace,
                    "source_id": source["id"],
                    "source_sha256": source["report_sha256"],
                    "config_version": source["config_version"],
                    "config_sha256": source["config_sha256"],
                    "model_sha256": source["model_sha256"],
                    "provider_id": "q8",
                    "evidence_kind": source.get("evidence_use", "measured"),
                    "metrics": source["metrics"],
                    "parameters": {
                        k: source["config_snapshot"][k]
                        for k in ("prompt_version", "max_tokens", "seed", "temperature")
                    },
                    "historical_import": source["contract"]
                    != self.w.contract(Profile.model_validate(source["config_snapshot"])),
                }
                env = {
                    **os.environ,
                    "MLFLOW_TRACKING_URI": "",
                    "OTEL_SDK_DISABLED": "true",
                    "MLFLOW_ENABLE_ASYNC_LOGGING": "false",
                    "PYTHONUTF8": "1",
                }
                proc = await asyncio.create_subprocess_exec(
                    str(self.mlflow_python),
                    str(self.root / "scripts/mlflow_sync.py"),
                    str(self.state / "mlflow"),
                    stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    env=env,
                    **({"creationflags": 0x08000000} if os.name == "nt" else {}),
                )
                communication = asyncio.create_task(proc.communicate(json.dumps(artifact).encode()))
                deadline = time.monotonic() + 60
                peak = 0.0
                while not communication.done():
                    if getattr(proc, "pid", None):
                        try:
                            process = psutil.Process(proc.pid)
                            rss = (
                                sum(
                                    p.memory_info().rss
                                    for p in [process, *process.children(recursive=True)]
                                )
                                / 2**20
                            )
                            peak = max(peak, rss)
                        except psutil.Error:
                            pass
                    if peak > 512 or psutil.virtual_memory().available < 256 * 2**20:
                        d["error"] = "MLFLOW_WORKER_RESOURCE_LIMIT_STOP"
                        raise RuntimeError("worker resource limit")
                    if time.monotonic() > deadline:
                        d["error"] = "MLFLOW_SYNC_TIMEOUT_RETRY_SAFE"
                        raise TimeoutError("worker timeout")
                    await asyncio.sleep(0.1)
                out, err = await communication
                d["worker_peak_rss_mib"] = round(peak, 2)
                if proc.returncode:
                    d["error"] = "MLFLOW_LOCAL_TRACKING_WORKER_FAILED"
                    raise RuntimeError("tracking unavailable")
                result = json.loads(out.decode().splitlines()[-1])
                d.update(status="synced", **result)
            except asyncio.CancelledError:
                d.update(status="failed", error="SERVICE_RESTARTED_RETRY_SAFE")
                raise
            except Exception:
                d.update(status="failed", error="MLFLOW_UNAVAILABLE_OR_RESOURCE_FLOOR_RETRY_SAFE")
            finally:
                if proc and proc.returncode is None:
                    if getattr(proc, "pid", None):
                        try:
                            for child in psutil.Process(proc.pid).children(recursive=True):
                                child.kill()
                        except psutil.Error:
                            pass
                    proc.kill()
                    await proc.wait()
                self.store.put_record("sync", d)
                self.store.audit(user["id"], "mlflow_" + d["status"], key)

        self.sync_task = asyncio.create_task(execute())
        return d


def register_operations(app, ops, auth):
    @app.get("/api/monitoring")
    async def monitor(
        request: Request,
        provider: str = "",
        version: str = "",
        status: str = "",
        seconds: int = 3600,
    ):
        return ops.monitoring(auth.require(request), provider, version, status, seconds)

    @app.get("/api/metrics")
    async def metrics(request: Request):
        user = auth.require(request, {"admin", "reviewer"})
        m = ops.monitoring(user)["summary"]
        return PlainTextResponse(
            "\n".join(f"console_{k} {v}" for k, v in m.items() if v is not None) + "\n",
            media_type="text/plain; version=0.0.4",
        )

    @app.get("/api/alert-policies")
    async def policies(request: Request):
        auth.require(request)
        return {
            "versions": ops.store.records("alert_policies"),
            "selected": {p: ops.policy(p)["id"] for p in ("service", "asset")},
        }

    @app.post("/api/alert-policies/preview")
    async def preview(data: PolicyPreview, request: Request):
        auth.require(request)
        return ops.preview_policy(data)

    @app.post("/api/alert-policies", status_code=201)
    async def save_policy(data: AlertPolicy, request: Request):
        user = auth.require(request, {"admin"})
        d = ops.record(
            "alert_policies",
            {"policy": data.model_dump(), "sha256": fingerprint(data.model_dump())},
            limit=100,
        )
        ops.store.audit(user["id"], "alert_policy_saved", d["id"])
        return d

    @app.post("/api/alert-policies/{key}/select")
    async def select_policy(key: str, request: Request):
        user = auth.require(request, {"admin"})
        d = ops.store.record("alert_policies", key)
        if not d or d["sha256"] != fingerprint(d["policy"]):
            raise HTTPException(409, "ALERT_POLICY_HASH_MISMATCH")
        selected = ops.store.metadata("selected_alert_policies") or {}
        selected[d["policy"]["profile_id"]] = key
        ops.store.metadata("selected_alert_policies", selected)
        ops.store.audit(user["id"], "alert_policy_selected", key)
        return {"selected": key}

    @app.get("/api/alerts")
    async def alerts(request: Request):
        user = auth.require(request)
        alerts = ops.store.records("alerts")
        if user["role"] == "operator":
            own = {t["id"] for t in ops.store.tasks(user["id"])}
            alerts = [{**a, "task_ids": [k for k in a["task_ids"] if k in own]} for a in alerts]
        return {"alerts": alerts}

    @app.post("/api/alerts/{key}/ack")
    async def ack(key: str, request: Request):
        user = auth.require(request, {"admin", "reviewer"})
        d = ops.store.record("alerts", key)
        if not d:
            raise HTTPException(404, "ALERT_NOT_FOUND")
        d.update(acknowledged=True)
        d["events"] = (d["events"] + [{"time": time.time(), "status": "acknowledged"}])[-100:]
        ops.store.put_record("alerts", d)
        ops.store.audit(user["id"], "alert_acknowledged", key)
        return d

    @app.get("/api/loads")
    async def loads(request: Request):
        user = auth.require(request)
        return {
            "runs": [
                d
                for d in ops.store.records("loads")
                if user["role"] != "operator" or d["owner"] == user["id"]
            ]
        }

    @app.post("/api/loads", status_code=202)
    async def load(data: LoadRequest, request: Request):
        user = auth.require(request, {"admin"})
        return await ops.begin_load(data, user, request)

    @app.get("/api/loads/compare")
    async def compare(left: str, right: str, request: Request):
        user = auth.require(request)
        a, b = ops.get_load(left, user), ops.get_load(right, user)
        compatible = all(a[k] == b[k] for k in ("provider_id", "dataset_sha256", "contract")) and {
            k: v for k, v in a["settings"].items() if k not in ("profile_version", "confirmation")
        } == {
            k: v for k, v in b["settings"].items() if k not in ("profile_version", "confirmation")
        }
        return {
            "left": a,
            "right": b,
            "comparable": compatible,
            "config_diff": difference(a["config_snapshot"], b["config_snapshot"]),
            "metric_delta": {
                k: b["metrics"][k] - v
                for k, v in a.get("metrics", {}).items()
                if compatible
                and isinstance(v, (int, float))
                and isinstance(b.get("metrics", {}).get(k), (int, float))
            },
        }

    @app.get("/api/loads/{key}")
    async def load_detail(key: str, request: Request):
        return ops.get_load(key, auth.require(request))

    @app.post("/api/loads/{key}/stop")
    async def stop(key: str, request: Request):
        user = auth.require(request, {"admin"})
        d = ops.get_load(key, user)
        if d["status"] not in ("running", "stopping"):
            raise HTTPException(409, "LOAD_NOT_RUNNING")
        ops.stops.add(key)
        d.update(status="stopping", stop_reason="USER_SAFE_STOP")
        ops.store.put_record("loads", d)
        ops.store.audit(user["id"], "load_stop_requested", key)
        return d

    @app.get("/api/experiments")
    async def experiments(request: Request):
        user = auth.require(request)
        return {
            "tracking": "local SQLite, on-demand worker; no MLflow server",
            "dependency_available": ops.mlflow_python.is_file(),
            "sync": [
                d
                for d in ops.store.records("sync")
                if user["role"] != "operator" or d["owner"] == user["id"]
            ],
            "release_authority": "console_only_no_mlflow_active_state",
        }

    @app.post("/api/experiments/sync", status_code=202)
    async def sync(data: SyncRequest, request: Request):
        return await ops.sync(data, auth.require(request, {"admin", "reviewer"}))

    @app.get("/api/backups")
    async def backups(request: Request):
        auth.require(request, {"admin"})
        return {
            "receipts": ops.store.records("restore_receipts"),
            "includes": "immutable configs and historical business metadata/metrics/model references",
            "excludes": "users/password hashes/sessions/secrets/private input/raw model outputs/weights/active release authority",
        }

    @app.post("/api/backups/export")
    async def export(request: Request):
        user = auth.require(request, {"admin"})
        d = ops.export_backup()
        ops.store.audit(user["id"], "business_backup_exported", d["sha256"])
        return d

    @app.post("/api/backups/export-file")
    async def export_file(request: Request):
        import hashlib

        user = auth.require(request, {"admin"})
        d = ops.export_backup()
        b = json.dumps(d, ensure_ascii=False, indent=2).encode("utf-8")
        ops.store.audit(user["id"], "business_backup_file_exported", d["sha256"])
        return {
            "payload_base64": base64.b64encode(b).decode("ascii"),
            "file_sha256": hashlib.sha256(b).hexdigest(),
            "bundle_sha256": d["sha256"],
        }

    @app.post("/api/backups/uploads", status_code=201)
    async def start_upload(data: UploadStart, request: Request):
        user = auth.require(request, {"admin"})
        ops.uploads = {k: v for k, v in ops.uploads.items() if v["expires"] > time.time()}
        if len(ops.uploads) >= 2:
            raise HTTPException(409, "BACKUP_UPLOAD_CAPACITY")
        key = uuid.uuid4().hex
        ops.uploads[key] = {
            "owner": user["id"],
            "expires": time.time() + 120,
            "spec": data.model_dump(),
            "chunks": {},
        }
        return {"id": key, "expires_seconds": 120}

    @app.post("/api/backups/uploads/{key}/chunks")
    async def upload_chunk(key: str, data: Chunk, request: Request):
        user = auth.require(request, {"admin"})
        u = ops.upload(key, user["id"])
        try:
            b = base64.b64decode(data.data, validate=True)
        except ValueError as exc:
            raise HTTPException(422, "BACKUP_CHUNK_INVALID") from exc
        if (
            len(b) > 12000
            or data.index in u["chunks"]
            or sum(map(len, u["chunks"].values())) + len(b) > u["spec"]["bytes"]
        ):
            raise HTTPException(422, "BACKUP_CHUNK_INVALID")
        u["chunks"][data.index] = b
        u.pop("validated", None)
        return {"received_bytes": sum(map(len, u["chunks"].values()))}

    @app.post("/api/backups/uploads/{key}/preview")
    async def preview_upload(key: str, request: Request):
        user = auth.require(request, {"admin"})
        u = ops.upload(key, user["id"])
        chunks = u["chunks"]
        if set(chunks) != set(range(len(chunks))):
            raise HTTPException(422, "BACKUP_CHUNK_INVALID")
        b = b"".join(chunks[i] for i in range(len(chunks)))
        import hashlib

        if len(b) != u["spec"]["bytes"] or hashlib.sha256(b).hexdigest() != u["spec"]["sha256"]:
            raise HTTPException(422, "BACKUP_FORMAT_HASH_OR_COMPATIBILITY_INVALID")
        try:
            raw = json.loads(b)
        except ValueError as exc:
            raise HTTPException(422, "BACKUP_FORMAT_HASH_OR_COMPATIBILITY_INVALID") from exc
        u["validated"] = ops.validate_backup(raw)
        return {
            "bundle_sha256": u["validated"]["sha256"],
            "counts": {
                n: sum(r["namespace"] == n for r in u["validated"]["records"])
                for n in ("configs", "alert_policies", "evaluations", "loads", "releases")
            },
            "effect": "new isolated installation; existing state unchanged; accounts/active releases not restored",
        }

    @app.post("/api/backups/uploads/{key}/restore", status_code=201)
    async def restore(key: str, data: Restore, request: Request):
        user = auth.require(request, {"admin"})
        return ops.restore_isolated(ops.upload(key, user["id"]), data, user["id"])
