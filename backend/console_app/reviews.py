from __future__ import annotations

import asyncio
import json
import re
import time
import uuid
from typing import Literal

from fastapi import HTTPException, Request
from pydantic import Field, ValidationError

from .context_quality import RANKS, analyse_context, safety_v3
from .domain import Profile, StrictModel, Triage, fingerprint


class ReviewPolicy(StrictModel):
    profile_id: Literal["service", "asset"]
    editable_fields: list[
        Literal["event_type", "severity", "summary_zh_tw", "missing_information"]
    ] = Field(min_length=1, max_length=4)
    reason_min_length: int = Field(ge=8, le=80)
    roles: list[Literal["reviewer", "admin"]] = Field(min_length=1, max_length=2)
    high_risk_approvals: int = Field(ge=1, le=2)
    evidence_reference_required: bool


class ReviewDecision(StrictModel):
    expected_revision: int = Field(ge=0, le=100)
    decision: Literal["accept", "correct", "return", "insufficient", "confirm_high_risk"]
    reason: str = Field(min_length=8, max_length=240)
    changes: dict = Field(default_factory=dict)
    evidence_indices: list[int] = Field(default_factory=list, max_length=2)


def validate_policy(policy):
    if len(set(policy.editable_fields)) != len(policy.editable_fields) or len(
        set(policy.roles)
    ) != len(policy.roles):
        raise HTTPException(422, "REVIEW_POLICY_DUPLICATES")
    if policy.profile_id == "asset" and (
        policy.roles != ["reviewer"]
        or policy.high_risk_approvals != 2
        or not policy.evidence_reference_required
        or policy.reason_min_length < 16
    ):
        raise HTTPException(422, "ASSET_REVIEW_FLOOR_REQUIRED")


class Reviews:
    def __init__(self, root, store, jobs, workflows):
        self.root, self.store, self.jobs, self.workflows = root, store, jobs, workflows
        self.clock = time.time
        self.cleanup_task = None
        self.defaults = {}
        for key in ("service", "asset"):
            policy = ReviewPolicy(
                profile_id=key,
                editable_fields=["event_type", "severity", "summary_zh_tw"]
                + (["missing_information"] if key == "asset" else []),
                reason_min_length=8 if key == "service" else 16,
                roles=["reviewer", "admin"] if key == "service" else ["reviewer"],
                high_risk_approvals=1 if key == "service" else 2,
                evidence_reference_required=key == "asset",
            )
            record = {
                "id": "review-" + key + "-v1",
                "created_at": time.time(),
                "policy": policy.model_dump(),
                "sha256": fingerprint(policy.model_dump()),
                "author": "system",
            }
            if not store.record("review_policies", record["id"]):
                store.put_record("review_policies", record, create=True)
            self.defaults[key] = record["id"]
        jobs.review_policy_for = self.selected
        jobs.review_completed = self.completed
        jobs.retention_admit = self.retention_admit
        self.cleanup_expired()

    def cleanup_expired(self):
        now = self.clock()
        deleted = 0
        with self.store.connect() as db:
            db.execute("PRAGMA secure_delete=ON")
            for row in db.execute(
                "SELECT id,document FROM records WHERE namespace='review_evidence'"
            ).fetchall():
                doc = json.loads(row["document"])
                if doc["expires_at"] <= now:
                    db.execute(
                        "DELETE FROM records WHERE namespace='review_evidence' AND id=?",
                        (row["id"],),
                    )
                    self.jobs.ephemeral.pop(row["id"], None)
                    deleted += 1
        for key, value in list(self.jobs.ephemeral.items()):
            if value[0] <= now:
                self.jobs.ephemeral.pop(key, None)
        return deleted

    def start(self):
        async def sweep():
            while True:
                self.cleanup_expired()
                await asyncio.sleep(30)

        self.cleanup_task = asyncio.create_task(sweep())

    async def close(self):
        if self.cleanup_task:
            self.cleanup_task.cancel()
            await asyncio.gather(self.cleanup_task, return_exceptions=True)

    def retention_admit(self, request):
        self.cleanup_expired()
        if request.review_retention != "local_review_7_days":
            return
        with self.store.connect() as db:
            count = db.execute(
                "SELECT count(*) FROM records WHERE namespace='review_evidence'"
            ).fetchone()[0]
        pending = sum(
            r.review_retention == "local_review_7_days" for r in self.jobs.payloads.values()
        )
        active = self.store.task(self.jobs.active) if self.jobs.active else None
        if (
            count
            + pending
            + bool(active and active.get("input_retention") == "local_review_opt_in")
            >= 100
        ):
            raise HTTPException(503, "REVIEW_RETENTION_CAPACITY")

    def policy(self, key):
        doc = self.store.record("review_policies", key)
        if not doc:
            raise HTTPException(404, "REVIEW_POLICY_NOT_FOUND")
        if fingerprint(doc["policy"]) != doc["sha256"]:
            raise HTTPException(409, "REVIEW_POLICY_HASH_MISMATCH")
        validate_policy(ReviewPolicy.model_validate(doc["policy"]))
        return doc

    def selected(self, profile_id):
        return self.policy(
            (self.store.metadata("selected_review_policies") or {}).get(
                profile_id, self.defaults[profile_id]
            )
        )

    def save(self, policy, user):
        validate_policy(policy)
        doc = {
            "id": "review-" + policy.profile_id + "-" + uuid.uuid4().hex[:12],
            "created_at": time.time(),
            "policy": policy.model_dump(),
            "sha256": fingerprint(policy.model_dump()),
            "author": user["id"],
        }
        self.store.put_record("review_policies", doc, create=True)
        self.store.audit(user["id"], "business_review_policy_saved", doc["id"])
        return doc

    def select(self, key, user):
        doc = self.policy(key)
        selected = self.store.metadata("selected_review_policies") or {}
        selected[doc["policy"]["profile_id"]] = key
        self.store.metadata("selected_review_policies", selected)
        self.store.audit(user["id"], "business_review_policy_selected", key)
        return {
            "selected": key,
            "scope": "new_tasks_only_existing_review_policy_snapshots_unchanged",
        }

    def input_for(self, task, details):
        if details.get("input"):
            return details["input"]
        if task.get("input_retention") == "synthetic_sample":
            sample = next((s for s in self.jobs.samples if s["id"] == task.get("sample_id")), None)
            if sample:
                return {
                    k: sample[k]
                    for k in (
                        "incident_text",
                        "asset_id",
                        "asset_criticality",
                        "maintenance_window",
                    )
                }
        return None

    def completed(self, task, details):
        if task.get("result_summary", {}).get("review_required") is not True:
            return
        if self.store.record("reviews", task["id"]):
            return
        source_input = self.input_for(task, details)
        if not source_input or not details.get("raw_output") or not details.get("result"):
            return
        policy = task.get("business_review_policy") or self.policy(
            self.defaults[task["profile_id"]]
        )
        if task.get("input_retention") == "local_review_opt_in" and not self.store.record(
            "review_evidence", task["id"]
        ):
            self.store.put_record(
                "review_evidence",
                {
                    "id": task["id"],
                    "created_at": self.clock(),
                    "expires_at": self.clock() + 604800,
                    "input": source_input,
                    "raw_output": details["raw_output"],
                    "result": details["result"],
                    "context": details.get("context"),
                },
                create=True,
            )
        record = {
            "id": task["id"],
            "created_at": time.time(),
            "owner": task["owner"],
            "revision": 0,
            "state": "pending",
            "task_id": task["id"],
            "trace_id": task["trace_id"],
            "model_sha256": task.get("model_sha256"),
            "provider_id": task["provider_id"],
            "config_version": task["profile_version"],
            "config_sha256": task["config_snapshot_sha256"],
            "policy_snapshot": policy,
            "reasons": list(
                dict.fromkeys(details.get("warnings", []) + details["result"]["reason_codes"])
            ),
            "source_sha256": fingerprint(
                {"input": source_input, "raw": details["raw_output"], "post": details["result"]}
            ),
            "original_retention": task["input_retention"],
            "evidence_expires_at": self.clock()
            + (604800 if task["input_retention"] == "local_review_opt_in" else 900)
            if task["input_retention"] != "synthetic_sample"
            else None,
            "business_retention": "no_automatic_expiry_local_installation_until_authorized_retirement",
            "human_processing_policy_version": "safety-floor-v3.1",
            "history": [],
            "human_result": None,
            "required_approvals": None,
            "approvals": [],
            "business_version": "business-review-v1",
            "release_authority": "none",
            "equipment_execution": "unsupported_no_commands_executed",
            "policy_binding": "task_start_snapshot"
            if task.get("business_review_policy")
            else "legacy_default_bootstrap",
        }
        self.store.put_record("reviews", record, create=True)
        self.store.audit(task["owner"], "business_review_created", record["id"])

    def get(self, key, user, *, evidence=True):
        self.cleanup_expired()
        row = self.store.record("reviews", key)
        if not row:
            raise HTTPException(404, "REVIEW_NOT_FOUND")
        if user["role"] == "operator" and row["owner"] != user["id"]:
            raise HTTPException(403, "REVIEW_FORBIDDEN")
        if not evidence:
            return row
        task = self.store.task(key)
        details = self.jobs.detail(task) if task else {}
        retained = self.store.record("review_evidence", key)
        if retained and retained["expires_at"] > self.clock():
            details = retained
        source_input = self.input_for(task or {}, details)
        available = bool(source_input and details.get("raw_output") and details.get("result"))
        expiry = row.get("evidence_expires_at")
        if expiry is None and row["original_retention"] != "synthetic_sample":
            expiry = (
                retained["expires_at"] if retained else (self.jobs.ephemeral.get(key) or (None,))[0]
            )
            if expiry is None:
                expiry = row["created_at"] + (
                    604800 if row["original_retention"] == "local_review_opt_in" else 900
                )
        if row.get("imported_from_backup") or expiry is not None and expiry <= self.clock():
            available = False
        return {
            **row,
            "evidence_available": available,
            "evidence_expires_at": expiry,
            "business_retention": row.get(
                "business_retention",
                "no_automatic_expiry_local_installation_until_authorized_retirement",
            ),
            "source_status": "restored_without_evidence_read_only"
            if row.get("imported_from_backup")
            else "available"
            if available
            else "expired_or_unavailable",
            "input": source_input if available else None,
            "raw_output": details.get("raw_output") if available else None,
            "policy_result": details.get("result") if available else None,
            "context": details.get("context") if available else None,
            "evidence_unavailable_reason": None
            if available
            else "REVIEW_EVIDENCE_EXPIRED_OR_RESTARTED",
        }

    def inbox(self, user, status="", reason="", provider="", version="", profile=""):
        for task in self.store.tasks(user["id"] if user["role"] == "operator" else None):
            if task["kind"] == "inference" and task["status"] == "needs_review":
                self.completed(task, self.jobs.detail(task))
        result = []
        for r in self.store.records("reviews"):
            if user["role"] == "operator" and r["owner"] != user["id"]:
                continue
            if (
                status
                and r["state"] != status
                or reason
                and reason not in r["reasons"]
                or provider
                and r["provider_id"] != provider
                or version
                and r["config_version"] != version
                or profile
                and r["policy_snapshot"]["policy"]["profile_id"] != profile
            ):
                continue
            detail = self.get(r["id"], user)
            result.append(
                {
                    k: v
                    for k, v in detail.items()
                    if k
                    not in {
                        "input",
                        "raw_output",
                        "policy_result",
                        "context",
                        "human_result",
                        "history",
                    }
                }
            )
        return result

    def decide(self, key, data, user, idempotency_key):
        if not re.fullmatch(r"[A-Za-z0-9_-]{8,96}", idempotency_key):
            raise HTTPException(422, "IDEMPOTENCY_KEY_REQUIRED")
        detail = self.get(key, user)
        if detail.get("imported_from_backup"):
            raise HTTPException(409, "RESTORED_REVIEW_READ_ONLY_NO_EVIDENCE")
        policy = ReviewPolicy.model_validate(detail["policy_snapshot"]["policy"])
        validate_policy(policy)
        if fingerprint(policy.model_dump()) != detail["policy_snapshot"]["sha256"]:
            raise HTTPException(409, "REVIEW_POLICY_HASH_MISMATCH")
        if user["role"] not in policy.roles:
            raise HTTPException(403, "BUSINESS_REVIEW_ROLE_REQUIRED")
        digest = fingerprint({"actor": user["id"], "key": idempotency_key})
        payload_hash = fingerprint(data.model_dump())
        previous = next((h for h in detail["history"] if h["idempotency_digest"] == digest), None)
        if previous:
            if previous["payload_sha256"] != payload_hash:
                raise HTTPException(409, "REVIEW_IDEMPOTENCY_CONFLICT")
            return {**self.get(key, user), "idempotent_replay": True}
        if not detail["evidence_available"]:
            raise HTTPException(410, "REVIEW_EVIDENCE_EXPIRED_OR_RESTARTED")
        if (
            fingerprint(
                {
                    "input": detail["input"],
                    "raw": detail["raw_output"],
                    "post": detail["policy_result"],
                }
            )
            != detail["source_sha256"]
        ):
            raise HTTPException(409, "REVIEW_SOURCE_CHANGED")
        if len(data.reason) < policy.reason_min_length or any(ord(c) < 32 for c in data.reason):
            raise HTTPException(422, "REVIEW_REASON_REQUIRED")
        if any(
            type(i) is not int or i < 0 or i >= len(detail["policy_result"]["evidence_spans"])
            for i in data.evidence_indices
        ):
            raise HTTPException(422, "REVIEW_EVIDENCE_REFERENCE_INVALID")
        if (
            policy.evidence_reference_required
            and data.decision not in {"return", "insufficient"}
            and not data.evidence_indices
        ):
            raise HTTPException(422, "REVIEW_EVIDENCE_REFERENCE_REQUIRED")
        if set(data.changes) - set(policy.editable_fields):
            raise HTTPException(422, "REVIEW_FIELD_NOT_ALLOWED")
        if data.decision != "correct" and data.changes:
            raise HTTPException(422, "REVIEW_UNEXPECTED_CHANGES")
        if data.decision == "correct" and not data.changes:
            raise HTTPException(422, "REVIEW_CORRECTION_REQUIRED")
        task = self.store.task(key)
        if (
            not task
            or task["profile_version"] != detail["config_version"]
            or fingerprint(task["config_snapshot"]) != detail["config_sha256"]
            or task.get("model_sha256") != detail["model_sha256"]
            or task["provider_id"] != detail["provider_id"]
        ):
            raise HTTPException(409, "REVIEW_SOURCE_CHANGED")
        profile = Profile.model_validate(task["config_snapshot"])
        human = None
        if data.decision in {"accept", "correct"}:
            try:
                human = Triage.model_validate(
                    {**detail["policy_result"], **data.changes}
                ).model_dump()
            except ValidationError as exc:
                raise HTTPException(422, "REVIEW_CORRECTION_INVALID") from exc
            context = analyse_context(detail["input"]["incident_text"])
            if (
                context["minimum_severity"]
                and RANKS[human["severity"]] < RANKS[context["minimum_severity"]]
            ):
                raise HTTPException(422, "REVIEW_SAFETY_FLOOR_REQUIRED")
            from .domain import InferRequest

            req = InferRequest.model_validate(
                {
                    **detail["input"],
                    "profile_id": profile.id,
                    "provider_id": task["provider_id"],
                    "mode": "sandbox",
                }
            )
            human, _ = safety_v3(human, req, profile)
            if task["input_retention"] != "synthetic_sample":
                human["evidence_spans"] = []
        # SQLite revision compare, immutable history, final result, and audit commit together.
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = json.loads(
                db.execute(
                    "SELECT document FROM records WHERE namespace='reviews' AND id=?", (key,)
                ).fetchone()[0]
            )
            if row["revision"] != data.expected_revision:
                raise HTTPException(409, "REVIEW_REVISION_CONFLICT")
            if row["state"] not in {"pending", "awaiting_second"}:
                raise HTTPException(409, "REVIEW_ALREADY_RESOLVED")
            if data.decision == "confirm_high_risk":
                if row["state"] != "awaiting_second":
                    raise HTTPException(409, "REVIEW_SECOND_APPROVAL_NOT_REQUIRED")
                if user["id"] in row["approvals"]:
                    raise HTTPException(403, "REVIEW_INDEPENDENT_APPROVER_REQUIRED")
                row["approvals"].append(user["id"])
                row["state"] = row["proposed_state"]
            elif data.decision in {"return", "insufficient"}:
                row["state"] = "returned" if data.decision == "return" else "insufficient"
                row["human_result"] = None
            else:
                if row["state"] != "pending":
                    raise HTTPException(409, "REVIEW_SECOND_APPROVAL_REQUIRED")
                proposed = "accepted" if data.decision == "accept" else "corrected"
                risk = (
                    human["severity"] in {"high", "critical"}
                    or detail["raw_output"]["severity"] in {"high", "critical"}
                    or detail["policy_result"]["severity"] in {"high", "critical"}
                )
                row["required_approvals"] = policy.high_risk_approvals if risk else 1
                row["approvals"] = [user["id"]]
                row["human_result"], row["proposed_state"] = human, proposed
                row["state"] = "awaiting_second" if row["required_approvals"] == 2 else proposed
            row["revision"] += 1
            row["updated_at"] = time.time()
            row["history"].append(
                {
                    "actor": user["id"],
                    "role": user["role"],
                    "time": row["updated_at"],
                    "decision": data.decision,
                    "human_processing_policy_version": "safety-floor-v3.1",
                    "reason": data.reason,
                    "changes": data.changes,
                    "evidence_indices": data.evidence_indices,
                    "revision": row["revision"],
                    "state": row["state"],
                    "human_result": row["human_result"],
                    "idempotency_digest": digest,
                    "payload_sha256": payload_hash,
                }
            )
            db.execute(
                "UPDATE records SET document=? WHERE namespace='reviews' AND id=?",
                (json.dumps(row, ensure_ascii=False), key),
            )
            db.execute(
                "INSERT INTO audit(actor,action,target,time) VALUES(?,?,?,?)",
                (user["id"], "business_review_" + data.decision, key, row["updated_at"]),
            )
        return self.get(key, user)


def register_reviews(app, reviews, auth):
    r = reviews

    @app.get("/api/quality-status")
    async def quality_status(request: Request):
        auth.require(request)
        from .engine import MODEL_SHA

        status = json.loads((r.root / "schemas/product-status-0.4.1.json").read_bytes())
        status["model_weight_sha256"] = MODEL_SHA
        path = r.root / "reports/quality-fix-0.4.1.json"
        status["observed_evaluations"] = (
            json.loads(path.read_bytes()).get("reports", []) if path.exists() else []
        )
        return status

    @app.get("/api/error-analysis")
    async def error_analysis(request: Request):
        auth.require(request)
        data = json.loads((r.root / "data/error-analysis-v1.json").read_bytes())
        from .quality_metrics import quality_metrics

        data["retrospective_quality_metrics"] = quality_metrics(
            data["cases"], Profile.model_validate(data["source_profile_snapshot"])
        )
        data["retrospective_use"] = "new_definition_audit_only_original_report_unchanged"
        return data

    @app.get("/api/review-policies")
    async def policies(request: Request):
        auth.require(request)
        return {
            "versions": r.store.records("review_policies"),
            "selected": {k: r.selected(k)["id"] for k in r.defaults},
            "defaults": r.defaults,
            "schema": ReviewPolicy.model_json_schema(),
        }

    @app.post("/api/review-policies/preview")
    async def preview(data: ReviewPolicy, request: Request):
        auth.require(request)
        validate_policy(data)
        from .workflows import difference

        return {
            "kind": "fixture_policy_preview_no_inference",
            "diff": difference(r.selected(data.profile_id)["policy"], data.model_dump()),
            "editable_fields": data.editable_fields,
            "reason_min_length": data.reason_min_length,
            "roles": data.roles,
            "high_risk_approvals": data.high_risk_approvals,
            "evidence_reference_required": data.evidence_reference_required,
        }

    @app.post("/api/review-policies", status_code=201)
    async def save(data: ReviewPolicy, request: Request):
        return r.save(data, auth.require(request, {"admin"}))

    @app.post("/api/review-policies/{key}/select")
    async def select(key: str, request: Request):
        return r.select(key, auth.require(request, {"admin"}))

    @app.get("/api/reviews")
    async def inbox(
        request: Request,
        status: str = "",
        reason: str = "",
        provider: str = "",
        version: str = "",
        profile: str = "",
    ):
        return {
            "reviews": r.inbox(auth.require(request), status, reason, provider, version, profile),
            "limits": "Most recent 100 business reviews; ordinary source evidence memory-only 15 minutes",
        }

    @app.get("/api/reviews/{key}")
    async def detail(key: str, request: Request):
        return r.get(key, auth.require(request))

    @app.post("/api/reviews/{key}/decisions")
    async def decision(key: str, data: ReviewDecision, request: Request):
        return r.decide(
            key, data, auth.require(request), request.headers.get("idempotency-key", "")
        )

    @app.get("/api/reviews/{key}/export")
    async def export(key: str, request: Request):
        row = r.get(key, auth.require(request), evidence=False)
        if row["state"] in {"pending", "awaiting_second"}:
            raise HTTPException(409, "REVIEW_NOT_FINAL")
        body = {
            "export_version": "human-business-result-v1",
            "equipment_execution": "unsupported",
            "model_metrics_changed": False,
            "data": {k: v for k, v in row.items() if k not in {"source_sha256", "owner"}},
        }
        for h in body["data"]["history"]:
            h.pop("idempotency_digest", None)
            h.pop("payload_sha256", None)
        return {**body, "sha256": fingerprint(body)}
