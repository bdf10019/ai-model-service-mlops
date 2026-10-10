from __future__ import annotations

import asyncio
import time
import uuid
from collections import deque

from fastapi import HTTPException
from pydantic import ValidationError

from .domain import InferRequest, Profile, fingerprint, fixture, safety
from .engine import MODEL_SHA, Engine, EngineError
from .store import Store

TERMINAL = {"completed", "needs_review", "failed", "cancelled"}


class Jobs:
    def __init__(
        self, store: Store, engine: Engine, profiles: dict[str, Profile], samples: list[dict]
    ):
        self.store, self.engine, self.profiles, self.samples = store, engine, profiles, samples
        self.waiting: deque[str] = deque()
        self.payloads: dict[str, InferRequest] = {}
        self.ephemeral: dict[str, tuple[float, dict]] = {}
        self.active: str | None = None
        self.control_task: asyncio.Task | None = None
        self.control_id: str | None = None
        self.event = asyncio.Event()
        self.worker: asyncio.Task | None = None
        self.closing = False
        self.external_busy = lambda: False
        self.review_policy_for = lambda profile_id: None
        self.review_completed = lambda task, details: None
        self.retention_admit = lambda request: None
        self.fixture_delay = 0.12
        self.timeout_override: float | None = None  # Test-only, no API configuration.

    def start(self):
        self.worker = asyncio.create_task(self.run())

    def new_doc(self, kind: str, owner: str, profile: Profile | None = None) -> dict:
        doc = {
            "id": uuid.uuid4().hex,
            "trace_id": uuid.uuid4().hex,
            "owner": owner,
            "kind": kind,
            "status": "queued",
            "created_at": time.time(),
            "events": [],
            "error": None,
        }
        if profile:
            doc.update(
                profile_id=profile.id,
                profile_version=profile.version,
                config_snapshot=profile.model_dump(),
                config_snapshot_sha256=fingerprint(profile.model_dump()),
                prompt_version=profile.prompt_version,
                schema_version="triage-v1",
                policy_version="safety-floor-v3.1"
                if profile.prompt_version == "triage-v5.1"
                else "safety-floor-v3"
                if profile.prompt_version == "triage-v5"
                else "safety-floor-v2",
            )
        return doc

    def transition(self, doc: dict, stage: str, reason: str | None = None):
        doc["status"] = stage
        doc["events"].append(
            {"stage": stage, "time": time.time(), **({"reason": reason} if reason else {})}
        )
        if stage in TERMINAL:
            doc["finished_at"] = time.time()
        if reason:
            doc["error"] = reason
        self.store.save_task(doc)

    def is_sample(self, request: InferRequest) -> bool:
        sample = next((s for s in self.samples if s["id"] == request.sample_id), None)
        return bool(
            sample
            and all(
                getattr(request, k) == sample[k]
                for k in ("incident_text", "asset_id", "asset_criticality", "maintenance_window")
            )
        )

    def submit(
        self,
        request: InferRequest,
        owner: str,
        key: str,
        profile: Profile | None = None,
        release: dict | None = None,
    ) -> dict:
        if self.closing:
            raise HTTPException(503, "SERVICE_DRAINING")
        p = profile or self.profiles[request.profile_id]
        fields = [f for f in p.required_fields if getattr(request, f, None) is None]
        if fields:
            raise HTTPException(422, {"code": "PROFILE_FIELDS_REQUIRED", "fields": fields})
        request_hash = fingerprint(
            {
                "request": request.model_dump(),
                "config": p.model_dump(),
                "release": release.get("id") if release else None,
            }
        )
        key_hash = fingerprint({"owner": owner, "key": key})
        existing = next(
            (d for d in self.store.tasks(owner) if d.get("idempotency_digest") == key_hash), None
        )
        if existing:
            if existing["request_sha256"] != request_hash:
                raise HTTPException(409, "IDEMPOTENCY_CONFLICT")
            return existing
        if self.control_task is not None and not self.control_task.done():
            raise HTTPException(409, "ENGINE_CONTROL_IN_PROGRESS")
        if request.provider_id == "q8" and not self.engine.status()["ready"]:
            raise HTTPException(503, "ENGINE_UNAVAILABLE")
        if len(self.waiting) >= p.queue_limit:
            raise HTTPException(503, "QUEUE_FULL", headers={"Retry-After": "2"})
        self.retention_admit(request)
        doc = self.new_doc("inference", owner, p)
        doc["business_review_policy"] = self.review_policy_for(p.id)
        doc.update(
            release_id=release["id"] if release else None,
            execution_mode="released" if release else "sandbox",
        )
        if release:
            doc["report_sha256"] = release["report_sha256"]
        doc.update(
            provider_id=request.provider_id,
            evidence_kind="measured" if request.provider_id == "q8" else "fixture",
            model_sha256=MODEL_SHA if request.provider_id == "q8" else None,
            model_version="qwen3-1.7b-merged-v5-q8-import-v1"
            if request.provider_id == "q8"
            else "fixture-v1",
            idempotency_digest=key_hash,
            request_sha256=request_hash,
            input_retention="synthetic_sample"
            if self.is_sample(request)
            else "local_review_opt_in"
            if request.review_retention == "local_review_7_days"
            else "memory_15_minutes",
        )
        if self.is_sample(request):
            doc["sample_id"] = request.sample_id
        self.transition(doc, "queued")
        self.payloads[doc["id"]] = request
        self.waiting.append(doc["id"])
        self.event.set()
        self.store.audit(owner, "inference_submitted", doc["id"])
        return doc

    async def run(self):
        while not self.closing:
            await self.event.wait()
            self.event.clear()
            while self.waiting and not self.closing:
                task_id = self.waiting.popleft()
                doc = self.store.task(task_id)
                request = self.payloads.pop(task_id, None)
                if not doc or request is None or doc["status"] == "cancelled":
                    continue
                p = Profile.model_validate(doc["config_snapshot"])
                doc["queue_ms"] = round((time.time() - doc["created_at"]) * 1000, 2)
                if doc["queue_ms"] > p.queue_timeout_seconds * 1000:
                    self.transition(doc, "failed", "QUEUE_TIMEOUT")
                    continue
                self.active = task_id
                started = time.monotonic()
                self.transition(doc, "running")

                async def generate(req=request, profile=p):
                    if req.provider_id == "fixture":
                        await asyncio.sleep(self.fixture_delay)
                        return fixture(req), {
                            "ttft_ms": None,
                            "usage": {},
                            "streaming": "not_supported",
                        }
                    return await self.engine.generate(req, profile)

                generation = asyncio.create_task(generate())
                try:
                    raw, metadata = await asyncio.wait_for(
                        asyncio.shield(generation),
                        self.timeout_override or p.generation_timeout_seconds,
                    )
                    doc["generation_ms"] = round((time.monotonic() - started) * 1000, 2)
                    self.transition(doc, "validating")
                    result, warnings = safety(raw, request, p)
                    details = {
                        "input": {
                            k: getattr(request, k)
                            for k in (
                                "incident_text",
                                "asset_id",
                                "asset_criticality",
                                "maintenance_window",
                            )
                        },
                        "result": result,
                        "raw_output": raw,
                        "warnings": warnings,
                        "generation": metadata,
                    }
                    if p.prompt_version in {"triage-v5", "triage-v5.1"}:
                        from .context_quality import analyse_context
                        from .context_quality_v3 import analyse_context as original_context

                        details["context"] = (
                            analyse_context
                            if p.prompt_version == "triage-v5.1"
                            else original_context
                        )(request.incident_text)
                    doc.update(
                        warnings=warnings,
                        raw_schema_valid=True,
                        result_summary={
                            "event_type": result["event_type"],
                            "severity": result["severity"],
                            "review_required": result["review_required"],
                        },
                    )
                    if doc["input_retention"] == "synthetic_sample":
                        doc.update(details)
                    else:
                        self.ephemeral = {
                            k: v for k, v in self.ephemeral.items() if v[0] > time.time()
                        }
                        if len(self.ephemeral) >= 100:
                            self.ephemeral.pop(next(iter(self.ephemeral)))
                        self.ephemeral[task_id] = (time.time() + 900, details)
                    self.transition(
                        doc, "needs_review" if result["review_required"] else "completed"
                    )
                    try:
                        self.review_completed(doc, details)
                    except Exception:
                        doc.setdefault("warnings", []).append("REVIEW_INBOX_CREATE_FAILED")
                except TimeoutError:
                    self.transition(doc, "failed", "MODEL_TIMEOUT")
                    # Keep capacity until underlying generation exits or this engine is stopped.
                    try:
                        await asyncio.wait_for(asyncio.shield(generation), 30)
                    except Exception:
                        if request.provider_id == "q8":
                            await self.engine.stop()
                        generation.cancel()
                        await asyncio.gather(generation, return_exceptions=True)
                except ValidationError:
                    doc["raw_schema_valid"] = False
                    self.transition(doc, "failed", "MODEL_OUTPUT_INVALID")
                except (EngineError, ValueError, TypeError) as e:
                    reason = str(e) if isinstance(e, EngineError) else "MODEL_OUTPUT_INVALID"
                    self.transition(doc, "failed", reason)
                    if request.provider_id == "q8":
                        await self.engine.drain_after_error()
                except asyncio.CancelledError:
                    generation.cancel()
                    await asyncio.gather(generation, return_exceptions=True)
                    if doc["status"] not in TERMINAL:
                        self.transition(doc, "failed", "SERVICE_DRAINING")
                    raise
                except Exception:
                    self.transition(doc, "failed", "INFERENCE_INTERNAL_ERROR")
                    if request.provider_id == "q8":
                        await self.engine.drain_after_error()
                finally:
                    doc["elapsed_ms"] = round((time.time() - doc["created_at"]) * 1000, 2)
                    self.store.save_task(doc)
                    self.active = None

    def detail(self, doc: dict) -> dict:
        ephemeral = self.ephemeral.get(doc["id"])
        if ephemeral and ephemeral[0] > time.time():
            return {**doc, **ephemeral[1]}
        if ephemeral:
            self.ephemeral.pop(doc["id"], None)
        return doc

    def cancel(self, doc: dict, user: dict) -> dict:
        if doc["owner"] != user["id"] and user["role"] != "admin":
            raise HTTPException(403, "TASK_FORBIDDEN")
        if doc["id"] == self.active:
            raise HTTPException(409, "RUNNING_NOT_CANCELLABLE")
        if doc["status"] != "queued" or doc["kind"] != "inference":
            raise HTTPException(409, "TASK_NOT_CANCELLABLE")
        if doc["id"] in self.waiting:
            self.waiting.remove(doc["id"])
        self.payloads.pop(doc["id"], None)
        self.transition(doc, "cancelled")
        self.store.audit(user["id"], "task_cancelled", doc["id"])
        return doc

    def control(self, action: str, owner: str) -> dict:
        if (
            self.external_busy()
            or self.active
            or self.waiting
            or self.control_task is not None
            and not self.control_task.done()
        ):
            raise HTTPException(409, "ENGINE_BUSY")
        doc = self.new_doc(action, owner)
        self.transition(doc, "queued")
        self.control_id = doc["id"]

        async def execute():
            self.transition(doc, "running")
            try:
                if action == "verify_model":
                    from .engine import verify_import

                    data = await asyncio.to_thread(verify_import, self.engine.root)
                    self.store.metadata("model_verification", data)
                    doc["verification"] = data
                elif action == "start_engine":
                    doc["engine"] = await self.engine.start()
                    self.store.metadata(
                        "model_verification", doc["engine"]["verification"]
                    ) if "verification" in doc["engine"] else None
                else:
                    await self.engine.stop()
                self.transition(doc, "completed")
            except EngineError as e:
                self.transition(doc, "failed", str(e))
            except asyncio.CancelledError:
                self.transition(doc, "failed", "SERVICE_DRAINING")
                raise
            except Exception:
                self.transition(doc, "failed", "ENGINE_CONTROL_FAILED")
            self.store.audit(owner, action, doc["id"])

        self.control_task = asyncio.create_task(execute())
        return doc

    async def close(self):
        self.closing = True
        self.event.set()
        for task_id in list(self.waiting):
            doc = self.store.task(task_id)
            if doc:
                self.transition(doc, "failed", "SERVICE_DRAINING")
        self.waiting.clear()
        self.payloads.clear()
        if self.worker:
            try:
                await asyncio.wait_for(asyncio.shield(self.worker), 8)
            except TimeoutError:
                await self.engine.stop()
                self.worker.cancel()
                await asyncio.gather(self.worker, return_exceptions=True)
        if self.control_task and not self.control_task.done():
            self.control_task.cancel()
            await asyncio.gather(self.control_task, return_exceptions=True)
        await self.engine.stop()

    def exclusive(self, kind: str, owner: str, profile: Profile, runner) -> dict:
        if self.closing:
            raise HTTPException(503, "SERVICE_DRAINING")
        if (
            self.external_busy()
            or self.active
            or self.waiting
            or self.control_task is not None
            and not self.control_task.done()
        ):
            raise HTTPException(409, "ENGINE_BUSY")
        doc = self.new_doc(kind, owner, profile)
        self.transition(doc, "queued")
        self.control_id = doc["id"]

        async def execute():
            self.transition(doc, "running")
            try:
                doc["workflow_result"] = await runner(doc)
                self.transition(doc, "completed")
            except asyncio.CancelledError:
                self.transition(doc, "failed", "SERVICE_DRAINING")
                raise
            except Exception as exc:
                reason = str(exc) if isinstance(exc, EngineError) else "WORKFLOW_FAILED"
                self.transition(doc, "failed", reason)
            finally:
                self.store.audit(owner, kind + "_" + doc["status"], doc["id"])

        self.control_task = asyncio.create_task(execute())
        return doc
