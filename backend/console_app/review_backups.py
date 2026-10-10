"""Explicitly consented business-review backups; source evidence is never exported."""

from __future__ import annotations

import base64
import json
import math
import time
import uuid
from typing import Literal

from fastapi import HTTPException, Request
from pydantic import Field, ValidationError, model_validator

from .domain import Profile, StrictModel, Triage, fingerprint
from .engine import MODEL_SHA, hash_file
from .operations import Chunk, Restore, UploadStart
from .reviews import ReviewPolicy, validate_policy
from .store import Store
from .workflows import validate_profile


class BusinessRecord(StrictModel):
    namespace: Literal["configs", "review_policies", "reviews"]
    id: str = Field(pattern=r"^[a-zA-Z0-9_-]{1,64}$")
    data: dict
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


class ReviewBackup(StrictModel):
    format: Literal["console-review-business-backup-v1"]
    application_version: Literal["0.4.1"]
    created_at: float = Field(allow_inf_nan=False)
    model: dict
    records: list[BusinessRecord] = Field(max_length=192)
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")

    @model_validator(mode="after")
    def hashes(self):
        d = self.model_dump()
        if fingerprint({k: v for k, v in d.items() if k != "sha256"}) != self.sha256:
            raise ValueError("bundle hash")
        if len({(r.namespace, r.id) for r in self.records}) != len(self.records):
            raise ValueError("duplicate")
        if any(fingerprint(r.data) != r.sha256 for r in self.records):
            raise ValueError("record hash")
        return self


class ExportConsent(StrictModel):
    preview_id: str = Field(pattern=r"^[a-f0-9]{32}$")
    bundle_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    confirmation: Literal["EXPORT_PRIVATE_HUMAN_BUSINESS_ONLY"]


REVIEW_FIELDS = {
    "created_at",
    "updated_at",
    "revision",
    "state",
    "task_id",
    "trace_id",
    "model_sha256",
    "provider_id",
    "config_version",
    "config_sha256",
    "policy_snapshot",
    "reasons",
    "source_sha256",
    "original_retention",
    "evidence_expires_at",
    "business_retention",
    "human_processing_policy_version",
    "history",
    "human_result",
    "required_approvals",
    "approvals",
    "business_version",
    "release_authority",
    "equipment_execution",
    "policy_binding",
    "proposed_state",
}
HISTORY_FIELDS = {
    "actor",
    "role",
    "time",
    "decision",
    "reason",
    "changes",
    "evidence_indices",
    "revision",
    "state",
    "human_result",
    "human_processing_policy_version",
}
STATES = {"pending", "awaiting_second", "accepted", "corrected", "returned", "insufficient"}


def human_projection(value):
    if value is None:
        return None
    return {**value, "evidence_spans": []}


class ReviewBackups:
    def __init__(self, reviews):
        self.r = reviews
        self.root, self.store = reviews.root, reviews.store
        self.clock = time.time
        self.previews, self.uploads = {}, {}

    def model(self):
        return {
            "sha256": MODEL_SHA,
            "manifest_sha256": hash_file(self.root / "schemas/model-import.json"),
            "weights_included": False,
        }

    def export_projection(self):
        self.r.cleanup_expired()
        records = []
        for ns in ("configs", "review_policies", "reviews"):
            with self.store.connect() as db:
                count = db.execute(
                    "SELECT count(*) FROM records WHERE namespace=?", (ns,)
                ).fetchone()[0]
            if count > 64:
                raise HTTPException(409, "REVIEW_BACKUP_RECORD_LIMIT_64_PER_NAMESPACE")
            for doc in self.store.records(ns):
                if ns == "configs":
                    data = {
                        "config": doc["config"],
                        "config_sha256": doc["sha256"],
                        "selected": self.r.workflows.profiles[doc["config"]["id"]].version
                        == doc["id"],
                    }
                elif ns == "review_policies":
                    data = {
                        "policy": doc["policy"],
                        "policy_sha256": doc["sha256"],
                        "selected": self.r.selected(doc["policy"]["profile_id"])["id"] == doc["id"],
                    }
                else:
                    data = {k: v for k, v in doc.items() if k in REVIEW_FIELDS}
                    data["human_result"] = human_projection(data.get("human_result"))
                    data["history"] = [
                        {
                            **{k: v for k, v in h.items() if k in HISTORY_FIELDS},
                            "human_result": human_projection(h.get("human_result")),
                        }
                        for h in data["history"]
                    ]
                    data["policy_snapshot"] = {
                        k: v
                        for k, v in data["policy_snapshot"].items()
                        if k in {"id", "policy", "sha256"}
                    }
                records.append(
                    {"namespace": ns, "id": doc["id"], "data": data, "sha256": fingerprint(data)}
                )
        body = {
            "format": "console-review-business-backup-v1",
            "application_version": "0.4.1",
            "created_at": self.clock(),
            "model": self.model(),
            "records": records,
        }
        body["sha256"] = fingerprint(body)
        if len(json.dumps(body, ensure_ascii=False).encode()) > 524288:
            raise HTTPException(409, "BACKUP_TOO_LARGE")
        return self.validate(body)

    def validate(self, raw):
        try:
            body = ReviewBackup.model_validate(raw).model_dump()
            if body["model"] != self.model():
                raise ValueError("model incompatible")
            configs, policies = {}, {}
            selected = set()
            for rec in body["records"]:
                ns, d = rec["namespace"], rec["data"]
                if ns in {"configs", "review_policies"}:
                    field, sha = (
                        ("config", "config_sha256")
                        if ns == "configs"
                        else ("policy", "policy_sha256")
                    )
                    if (
                        set(d) != {field, sha, "selected"}
                        or type(d["selected"]) is not bool
                        or fingerprint(d[field]) != d[sha]
                    ):
                        raise ValueError("version fields")
                    if ns == "configs":
                        p = Profile.model_validate(d[field])
                        validate_profile(p)
                        if p.version != rec["id"]:
                            raise ValueError("config version")
                        configs[rec["id"]] = d[sha]
                        key = p.id
                    else:
                        p = ReviewPolicy.model_validate(d[field])
                        validate_policy(p)
                        policies[rec["id"]] = (d[sha], d[field])
                        key = p.profile_id
                    if d["selected"]:
                        if (ns, key) in selected:
                            raise ValueError("selected duplicate")
                        selected.add((ns, key))
                else:
                    if (
                        set(d) - REVIEW_FIELDS
                        or d["state"] not in STATES
                        or d["release_authority"] != "none"
                        or d["equipment_execution"] != "unsupported_no_commands_executed"
                    ):
                        raise ValueError("unknown or authoritative review")
                    policy = d["policy_snapshot"]
                    if (
                        set(policy) != {"id", "policy", "sha256"}
                        or fingerprint(policy["policy"]) != policy["sha256"]
                    ):
                        raise ValueError("snapshot")
                    validate_policy(ReviewPolicy.model_validate(policy["policy"]))
                    if d["model_sha256"] not in {None, MODEL_SHA} or d["provider_id"] not in {
                        "q8",
                        "fixture",
                    }:
                        raise ValueError("model reference")
                    if d["task_id"] != rec["id"] or len(d["history"]) != d["revision"]:
                        raise ValueError("revision")
                    if type(d["revision"]) is not int or not 0 <= d["revision"] <= 100:
                        raise ValueError("revision range")
                    if not isinstance(d["approvals"], list) or len(set(d["approvals"])) != len(
                        d["approvals"]
                    ):
                        raise ValueError("duplicate actors")
                    if (
                        d["state"] in {"accepted", "corrected"}
                        and d["required_approvals"] == 2
                        and len(d["approvals"]) != 2
                    ):
                        raise ValueError("missing independent approval")
                    for i, h in enumerate(d["history"], 1):
                        if (
                            set(h) - HISTORY_FIELDS
                            or h["revision"] != i
                            or set(h["changes"]) - set(policy["policy"]["editable_fields"])
                        ):
                            raise ValueError("history")
                        if (
                            not isinstance(h["reason"], str)
                            or not policy["policy"]["reason_min_length"] <= len(h["reason"]) <= 240
                            or h["role"] not in policy["policy"]["roles"]
                        ):
                            raise ValueError("history role or reason")
                        if type(h["time"]) not in (int, float) or not math.isfinite(h["time"]):
                            raise ValueError("history time")
                        if (
                            h["human_result"] is not None
                            and Triage.model_validate(h["human_result"]).evidence_spans
                        ):
                            raise ValueError("source spans")
                    if (
                        d["human_result"] is not None
                        and Triage.model_validate(d["human_result"]).evidence_spans
                    ):
                        raise ValueError("source spans")
            for rec in body["records"]:
                if rec["namespace"] == "reviews":
                    d = rec["data"]
                    p = d["policy_snapshot"]
                    if configs.get(d["config_version"]) != d["config_sha256"] or policies.get(
                        p["id"]
                    ) != (p["sha256"], p["policy"]):
                        raise ValueError("missing historical reference")
            return body
        except (ValidationError, ValueError, KeyError, TypeError, HTTPException) as exc:
            raise HTTPException(422, "REVIEW_BACKUP_FORMAT_HASH_OR_REFERENCE_INVALID") from exc

    def ticket(self, tickets, key, owner):
        for k, v in list(tickets.items()):
            if v["expires"] <= self.clock():
                tickets.pop(k)
        d = tickets.get(key)
        if not d or d["owner"] != owner:
            raise HTTPException(404, "REVIEW_BACKUP_PREVIEW_EXPIRED")
        return d

    def preview(self, owner):
        for k, v in list(self.previews.items()):
            if v["expires"] <= self.clock():
                self.previews.pop(k)
        if len(self.previews) >= 2:
            raise HTTPException(409, "BACKUP_UPLOAD_CAPACITY")
        body = self.export_projection()
        key = uuid.uuid4().hex
        self.previews[key] = {"owner": owner, "expires": self.clock() + 120, "bundle": body}
        return {
            "preview_id": key,
            "bundle_sha256": body["sha256"],
            "counts": {
                ns: sum(r["namespace"] == ns for r in body["records"])
                for ns in ("configs", "review_policies", "reviews")
            },
            "bytes": len(json.dumps(body, ensure_ascii=False).encode()),
            "includes_private_human_content": True,
            "includes": "版本設定、業務政策、人工摘要／理由／修正／歷史及模型參照",
            "excludes": "原始輸入、raw、政策後原始輸出、證據span、密鑰、帳號、Session、權重、發布權威",
            "review_preview": [
                {
                    "id": r["id"],
                    "state": r["data"]["state"],
                    "human_result": r["data"]["human_result"],
                    "history": r["data"]["history"],
                }
                for r in body["records"]
                if r["namespace"] == "reviews"
            ],
        }

    def restore(self, upload, data, owner):
        body = upload.get("validated")
        if not body or body["sha256"] != data.bundle_sha256:
            raise HTTPException(409, "BACKUP_PREVIEW_REQUIRED")
        body = self.validate(body)
        if self.r.jobs.active or self.r.jobs.waiting or self.r.jobs.external_busy():
            raise HTTPException(409, "ENGINE_BUSY")
        target = self.store.path.parent / "review-restores" / uuid.uuid4().hex
        target.resolve().relative_to(self.root.resolve())
        restored = Store(target)
        for rec in body["records"]:
            ns, d = rec["namespace"], rec["data"]
            doc = {"id": rec["id"], "created_at": body["created_at"], **d}
            if ns == "configs":
                doc.update(sha256=d["config_sha256"], author="backup-import", restored_from=None)
            elif ns == "review_policies":
                doc.update(sha256=d["policy_sha256"], author="backup-import")
            else:
                doc.update(
                    owner="backup-import",
                    imported_from_backup=body["sha256"],
                    restored_read_only=True,
                )
            restored.put_record(ns, doc, create=True)
        for ns, metadata in (
            ("configs", "selected_configs"),
            ("review_policies", "selected_review_policies"),
        ):
            field = "config" if ns == "configs" else "policy"
            restored.metadata(
                metadata,
                {
                    (
                        rec["data"][field]["id"]
                        if ns == "configs"
                        else rec["data"][field]["profile_id"]
                    ): rec["id"]
                    for rec in body["records"]
                    if rec["namespace"] == ns and rec["data"]["selected"]
                },
            )
        verified = all(
            restored.record(rec["namespace"], rec["id"]) is not None for rec in body["records"]
        )
        with restored.connect() as db:
            verified = (
                verified
                and db.execute("SELECT count(*) FROM users").fetchone()[0] == 0
                and db.execute(
                    "SELECT count(*) FROM records WHERE namespace='review_evidence'"
                ).fetchone()[0]
                == 0
            )
        if not verified:
            raise HTTPException(409, "REVIEW_RESTORE_VERIFICATION_FAILED")
        receipt = {
            "id": target.name,
            "created_at": self.clock(),
            "relative_installation": target.relative_to(self.root).as_posix(),
            "bundle_sha256": body["sha256"],
            "status": "isolated_review_restore_verified_read_only_without_source",
            "records": len(body["records"]),
            "source_evidence_restored": False,
            "active_releases_restored": False,
            "users_restored": 0,
            "model_reference_verified": True,
            "model_file_verified": False,
            "business_records_read_only": True,
        }
        self.store.put_record("review_restore_receipts", receipt, create=True)
        self.store.audit(owner, "review_business_isolated_restore", receipt["id"])
        upload.pop("validated", None)
        return receipt

    def restored_reviews(self, key):
        receipt = self.store.record("review_restore_receipts", key)
        if not receipt:
            raise HTTPException(404, "RESTORE_NOT_FOUND")
        path = (self.root / receipt["relative_installation"]).resolve()
        path.relative_to(self.root.resolve())
        if not (path / "console.db").is_file():
            raise HTTPException(404, "RESTORE_NOT_FOUND")
        store = Store(path)
        return [
            {
                **d,
                "input": None,
                "raw_output": None,
                "policy_result": None,
                "evidence_available": False,
                "source_status": "restored_without_evidence_read_only",
            }
            for d in store.records("reviews")
        ]


def register_review_backups(app, backups, auth):
    b = backups

    @app.get("/api/review-backups")
    async def index(request: Request):
        auth.require(request, {"admin"})
        return {
            "receipts": b.store.records("review_restore_receipts"),
            "format": "console-review-business-backup-v1",
        }

    @app.post("/api/review-backups/export-preview")
    async def export_preview(request: Request):
        return b.preview(auth.require(request, {"admin"})["id"])

    @app.post("/api/review-backups/export")
    async def export(data: ExportConsent, request: Request):
        user = auth.require(request, {"admin"})
        ticket = b.ticket(b.previews, data.preview_id, user["id"])
        if ticket["bundle"]["sha256"] != data.bundle_sha256:
            raise HTTPException(409, "BACKUP_PREVIEW_REQUIRED")
        b.store.audit(user["id"], "private_human_review_backup_exported", data.bundle_sha256)
        return ticket["bundle"]

    @app.post("/api/review-backups/uploads", status_code=201)
    async def upload(data: UploadStart, request: Request):
        user = auth.require(request, {"admin"})
        for k, v in list(b.uploads.items()):
            if v["expires"] <= b.clock():
                b.uploads.pop(k)
        if len(b.uploads) >= 2:
            raise HTTPException(409, "BACKUP_UPLOAD_CAPACITY")
        key = uuid.uuid4().hex
        b.uploads[key] = {
            "owner": user["id"],
            "expires": b.clock() + 120,
            "spec": data.model_dump(),
            "chunks": {},
        }
        return {"id": key}

    @app.post("/api/review-backups/uploads/{key}/chunks")
    async def chunk(key: str, data: Chunk, request: Request):
        u = b.ticket(b.uploads, key, auth.require(request, {"admin"})["id"])
        try:
            raw = base64.b64decode(data.data, validate=True)
        except ValueError as exc:
            raise HTTPException(422, "BACKUP_CHUNK_INVALID") from exc
        if (
            len(raw) > 12000
            or data.index in u["chunks"]
            or sum(map(len, u["chunks"].values())) + len(raw) > u["spec"]["bytes"]
        ):
            raise HTTPException(422, "BACKUP_CHUNK_INVALID")
        u["chunks"][data.index] = raw
        u.pop("validated", None)
        return {"received_bytes": sum(map(len, u["chunks"].values()))}

    @app.post("/api/review-backups/uploads/{key}/preview")
    async def preview(key: str, request: Request):
        u = b.ticket(b.uploads, key, auth.require(request, {"admin"})["id"])
        if set(u["chunks"]) != set(range(len(u["chunks"]))):
            raise HTTPException(422, "BACKUP_CHUNK_INVALID")
        raw = b"".join(u["chunks"][i] for i in range(len(u["chunks"])))
        import hashlib

        if len(raw) != u["spec"]["bytes"] or hashlib.sha256(raw).hexdigest() != u["spec"]["sha256"]:
            b.uploads.pop(key, None)
            raise HTTPException(422, "REVIEW_BACKUP_FORMAT_HASH_OR_REFERENCE_INVALID")
        try:
            body = json.loads(raw)
        except ValueError as exc:
            b.uploads.pop(key, None)
            raise HTTPException(422, "REVIEW_BACKUP_FORMAT_HASH_OR_REFERENCE_INVALID") from exc
        try:
            u["validated"] = b.validate(body)
        except HTTPException:
            b.uploads.pop(key, None)
            raise
        return {
            "bundle_sha256": u["validated"]["sha256"],
            "counts": {
                n: sum(r["namespace"] == n for r in body["records"])
                for n in ("configs", "review_policies", "reviews")
            },
            "effect": "只新建隔離安裝；原始證據不存在，人工紀錄唯讀，不能重新批准或發布。",
        }

    @app.post("/api/review-backups/uploads/{key}/restore", status_code=201)
    async def restore(key: str, data: Restore, request: Request):
        user = auth.require(request, {"admin"})
        return b.restore(b.ticket(b.uploads, key, user["id"]), data, user["id"])

    @app.get("/api/review-backups/restores/{key}/reviews")
    async def restored(key: str, request: Request):
        auth.require(request, {"admin"})
        return {"reviews": b.restored_reviews(key)}
