from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import subprocess
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
import psutil
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware

from .auth import Auth, Credentials, NewUser, create_user, public_user
from .domain import InferRequest, load_profiles
from .engine import MODEL_SHA, Engine
from .jobs import Jobs
from .operations import HttpTelemetry, Operations, register_operations
from .review_backups import ReviewBackups, register_review_backups
from .reviews import Reviews, register_reviews
from .store import Store
from .workflows import Workflows, register_routes

ROOT = Path(__file__).resolve().parents[2]


class Boundary:
    """Read bounded request bytes before parsing; reject browser cross-site mutations."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        rid = uuid.uuid4().hex
        scope.setdefault("state", {})["request_id"] = rid
        headers = {k.decode().lower(): v.decode() for k, v in scope["headers"]}

        async def wrapped_send(message):
            if message["type"] == "http.response.start":
                message["headers"] += [
                    (b"x-request-id", rid.encode()),
                    (b"cache-control", b"no-store"),
                    (b"x-content-type-options", b"nosniff"),
                    (b"x-frame-options", b"DENY"),
                    (b"referrer-policy", b"no-referrer"),
                    (
                        b"content-security-policy",
                        b"default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'",
                    ),
                ]
            await send(message)

        async def fail(status, code):
            await JSONResponse({"error": {"code": code, "request_id": rid}}, status_code=status)(
                scope, receive, wrapped_send
            )

        if scope["method"] in {"POST", "PUT", "PATCH", "DELETE"}:
            if headers.get("x-console-request") != "1":
                return await fail(403, "BROWSER_REQUEST_GUARD")
            origin = headers.get("origin")
            expected = scope.get("scheme", "http") + "://" + headers.get("host", "")
            if origin and origin != expected or headers.get("sec-fetch-site") == "cross-site":
                return await fail(403, "ORIGIN_FORBIDDEN")
            if headers.get("content-encoding", "identity") != "identity":
                return await fail(415, "ENCODING_UNSUPPORTED")
            if headers.get("content-type", "").split(";")[0].strip().lower() != "application/json":
                return await fail(415, "JSON_REQUIRED")
            lengths = [v for k, v in scope["headers"] if k == b"content-length"]
            if (
                len(lengths) > 1
                or lengths
                and (not lengths[0].isdigit() or int(lengths[0]) > 32768)
            ):
                return await fail(413, "BODY_TOO_LARGE")
            body = bytearray()
            deadline = time.monotonic() + 10
            while True:
                try:
                    message = await asyncio.wait_for(
                        receive(), max(0.01, deadline - time.monotonic())
                    )
                except TimeoutError:
                    return await fail(408, "BODY_TIMEOUT")
                if message["type"] == "http.disconnect":
                    return
                body.extend(message.get("body", b""))
                if len(body) > 32768:
                    return await fail(413, "BODY_TOO_LARGE")
                if not message.get("more_body"):
                    break
            delivered = False

            async def buffered_receive():
                nonlocal delivered
                if not delivered:
                    delivered = True
                    return {"type": "http.request", "body": bytes(body), "more_body": False}
                return await receive()

            return await self.app(scope, buffered_receive, wrapped_send)
        return await self.app(scope, receive, wrapped_send)


def system_snapshot(root: Path) -> dict:
    memory = psutil.virtual_memory()
    disk = shutil.disk_usage(root)
    gpu = None
    try:
        r = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=name,memory.total,memory.used",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            timeout=4,
            text=True,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        if r.returncode == 0:
            parts = r.stdout.strip().splitlines()[0].split(",")
            gpu = {"name": parts[0].strip(), "total_mib": int(parts[1]), "used_mib": int(parts[2])}
    except (OSError, subprocess.TimeoutExpired, ValueError, IndexError):
        pass
    return {
        "ram_total_gib": round(memory.total / 2**30, 2),
        "ram_free_gib": round(memory.available / 2**30, 2),
        "disk_free_gib": round(disk.free / 2**30, 2),
        "gpu": gpu,
        "runtime": "container" if Path("/.dockerenv").is_file() else "local_process",
        "docker": "container_detected_not_compose_acceptance"
        if Path("/.dockerenv").is_file()
        else "not_run",
        "kubernetes": "not_run",
        "mlflow": "optional_local_tracking_worker",
        "prometheus": "exposition_only_no_server",
        "sampled_at": time.time(),
        "evidence_kind": "measured",
    }


def create_app(state_dir: Path | None = None, engine_port: int = 8121) -> FastAPI:
    state_dir = state_dir or ROOT / os.environ.get("CONSOLE_STATE_DIR", "state")
    state_dir = state_dir.resolve()
    if not state_dir.is_relative_to(ROOT):
        raise ValueError("運行狀態必須位於作品目錄")
    store = Store(state_dir)
    auth = Auth(store)
    profiles = load_profiles(ROOT)
    samples = json.loads((ROOT / "data/samples.json").read_bytes())
    engine = Engine(ROOT, state_dir, engine_port)
    jobs = Jobs(store, engine, profiles, samples)
    workflows = Workflows(ROOT, store, jobs, engine, profiles)
    operations = Operations(ROOT, store, jobs, workflows, system_snapshot)
    reviews = Reviews(ROOT, store, jobs, workflows)
    review_backups = ReviewBackups(reviews)

    @asynccontextmanager
    async def lifespan(app):
        jobs.start()
        operations.start()
        reviews.start()
        yield
        await reviews.close()
        await operations.close()
        await jobs.close()

    app = FastAPI(
        title="模型服務控制台",
        version="0.4.1",
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    app.state.store, app.state.auth, app.state.jobs, app.state.engine = store, auth, jobs, engine
    app.state.workflows = workflows
    app.state.operations = operations
    app.state.reviews = reviews
    app.state.review_backups = review_backups
    register_review_backups(app, review_backups, auth)
    register_reviews(app, reviews, auth)
    register_operations(app, operations, auth)
    app.add_middleware(Boundary)
    app.add_middleware(HttpTelemetry, ops=operations)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost", "[::1]"])

    @app.exception_handler(HTTPException)
    async def http_error(request, exc):
        detail = exc.detail if isinstance(exc.detail, dict) else {"code": exc.detail}
        return JSONResponse(
            {"error": {**detail, "request_id": getattr(request.state, "request_id", None)}},
            status_code=exc.status_code,
            headers=exc.headers,
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, exc):
        # Pydantic's input/context may contain a password or private text. Never return them.
        fields = [
            {"field": ".".join(str(x) for x in e["loc"][1:]), "type": e["type"]}
            for e in exc.errors()
        ]
        return JSONResponse(
            {
                "error": {
                    "code": "VALIDATION_ERROR",
                    "fields": fields,
                    "request_id": request.state.request_id,
                }
            },
            status_code=422,
        )

    @app.exception_handler(Exception)
    async def unexpected_error(request, exc):
        return JSONResponse(
            {
                "error": {
                    "code": "INTERNAL_ERROR",
                    "request_id": getattr(request.state, "request_id", None),
                }
            },
            status_code=500,
        )

    @app.get("/api/setup")
    async def setup():
        return {
            "initialized": auth.initialized(),
            "version": "0.4.1",
            "stage": "判讀品質、人工複核、評測與受控發布",
        }

    @app.post("/api/bootstrap", status_code=201)
    async def bootstrap(data: Credentials, request: Request, response: Response):
        user = await asyncio.to_thread(
            create_user, store, NewUser(**data.model_dump(), role="admin"), True
        )
        return auth.issue(user, response, request)

    @app.post("/api/sessions")
    async def login(data: Credentials, request: Request, response: Response):
        user = await asyncio.to_thread(auth.login, data)
        return auth.issue(user, response, request)

    @app.get("/api/session")
    async def session(request: Request):
        user = auth.require(request)
        csrf = user.pop("csrf_token")
        return {"user": user, "csrf_token": csrf}

    @app.delete("/api/session")
    async def logout(request: Request, response: Response):
        auth.logout(request, response)
        return {"logged_out": True}

    @app.get("/api/users")
    async def users(request: Request):
        auth.require(request, {"admin"})
        with store.connect() as db:
            return {
                "users": [
                    public_user(r)
                    for r in db.execute("SELECT id,username,role FROM users ORDER BY created")
                ]
            }

    @app.post("/api/users", status_code=201)
    async def new_user(data: NewUser, request: Request):
        actor = auth.require(request, {"admin"})
        user = await asyncio.to_thread(create_user, store, data)
        store.audit(actor["id"], "role_assigned", user["id"])
        return {"user": user}

    @app.get("/api/profiles")
    async def get_profiles(request: Request):
        auth.require(request)
        return {
            "profiles": [p.model_dump() for p in profiles.values()],
            "active_profiles": {
                key: workflows.profile(release["config_version"]).model_dump()
                for key in profiles
                if (release := workflows.active(key))
            },
            "isolation": "single_installation_no_tenant_isolation",
        }

    @app.get("/api/samples")
    async def get_samples(request: Request):
        auth.require(request)
        return {"samples": samples, "source": "original_synthetic", "version": "samples-v1"}

    @app.get("/api/health")
    async def health(request: Request):
        auth.require(request)
        snapshot = await asyncio.to_thread(system_snapshot, ROOT)
        state = engine.status()
        if state["ready"]:
            try:
                async with httpx.AsyncClient(trust_env=False, timeout=2) as client:
                    response = await client.get(f"http://127.0.0.1:{engine.port}/health")
                    state["ready"] = response.status_code == 200
                    if not state["ready"]:
                        state.update(status="unhealthy", reason="HEALTH_CHECK_FAILED")
            except httpx.HTTPError:
                state.update(ready=False, status="unhealthy", reason="HEALTH_CHECK_TIMEOUT")
        return {
            **snapshot,
            "engine": state,
            "queue": {
                "running": bool(jobs.active),
                "waiting": len(jobs.waiting),
                "capacity": 1,
                "accepting": not jobs.closing,
            },
            "database": "ready",
            "release_state": store.metadata("release_state") or {"ready": False},
            "active_releases": store.metadata("active_releases") or {},
        }

    @app.get("/api/models")
    async def models(request: Request):
        auth.require(request)
        manifest = json.loads((ROOT / "schemas/model-import.json").read_bytes())
        verification = store.metadata("model_verification")
        return {
            "models": [
                {
                    "id": "q8",
                    "name": "Qwen3 1.7B · Q8_0",
                    "evidence_kind": "measured",
                    "verification": verification,
                    "artifact_present": (ROOT / "artifacts/models/q8/model.gguf").is_file(),
                    "sha256": MODEL_SHA,
                    "bytes": 1834426144,
                    "version": manifest["version"],
                    "base_revision": manifest["base_revision"],
                    "license": manifest["license"],
                    "lineage_limitations": manifest["lineage_limitations"],
                    "release_status": "release_exists"
                    if store.metadata("active_releases")
                    else "not_evaluated_not_released",
                    "engine": engine.status(),
                },
                {
                    "id": "fixture",
                    "name": "Fixture · 確定性測試",
                    "evidence_kind": "fixture",
                    "ready": True,
                    "version": "fixture-v1",
                    "release_status": "sandbox_only",
                    "sha256": None,
                },
            ]
        }

    @app.post("/api/models/q8/verify", status_code=202)
    async def verify_model(request: Request):
        user = auth.require(request, {"admin"})
        return jobs.control("verify_model", user["id"])

    @app.post("/api/engine/start", status_code=202)
    async def start_engine(request: Request):
        user = auth.require(request, {"admin"})
        return jobs.control("start_engine", user["id"])

    @app.post("/api/engine/stop", status_code=202)
    async def stop_engine(request: Request):
        user = auth.require(request, {"admin"})
        return jobs.control("stop_engine", user["id"])

    @app.post("/api/inferences", status_code=202)
    async def infer(data: InferRequest, request: Request):
        user = auth.require(request)
        key = request.headers.get("idempotency-key", "")
        if not re.fullmatch(r"[A-Za-z0-9_-]{8,96}", key):
            raise HTTPException(422, "IDEMPOTENCY_KEY_REQUIRED")
        profile, release = workflows.resolve_inference(data)
        doc = jobs.submit(data, user["id"], key, profile, release)
        request.state.task_id = doc["id"]
        return doc

    @app.get("/api/tasks")
    async def tasks(request: Request):
        user = auth.require(request)
        docs = store.tasks(user["id"] if user["role"] == "operator" else None)
        # Lists contain no raw output; details are a separate authorized drilldown.
        return {
            "tasks": [
                {
                    k: v
                    for k, v in d.items()
                    if k not in {"raw_output", "result", "idempotency_digest", "request_sha256"}
                }
                for d in docs
            ],
            "running": jobs.active,
            "waiting": list(jobs.waiting),
        }

    def allowed_task(task_id: str, user: dict) -> dict:
        doc = store.task(task_id)
        if not doc:
            raise HTTPException(404, "TASK_NOT_FOUND")
        if user["role"] == "operator" and doc["owner"] != user["id"]:
            raise HTTPException(403, "TASK_FORBIDDEN")
        return doc

    @app.get("/api/tasks/{task_id}")
    async def detail(task_id: str, request: Request):
        user = auth.require(request)
        doc = jobs.detail(allowed_task(task_id, user))
        return {k: v for k, v in doc.items() if k not in {"idempotency_digest", "request_sha256"}}

    @app.post("/api/tasks/{task_id}/cancel")
    async def cancel(task_id: str, request: Request):
        user = auth.require(request)
        return jobs.cancel(allowed_task(task_id, user), user)

    @app.get("/api/audit")
    async def audit(request: Request):
        user = auth.require(request)
        with store.connect() as db:
            if user["role"] == "operator":
                rows = db.execute(
                    "SELECT * FROM audit WHERE actor=? ORDER BY id DESC LIMIT 100", (user["id"],)
                )
            else:
                rows = db.execute("SELECT * FROM audit ORDER BY id DESC LIMIT 100")
            return {
                "events": [dict(row) for row in rows],
                "immutability": "local_append_only_not_external_signed_audit",
            }

    register_routes(app, workflows, auth)
    dist = ROOT / "frontend/dist"
    if dist.exists():
        app.mount("/", StaticFiles(directory=dist, html=True), name="console")
    else:

        @app.get("/")
        async def not_built():
            return JSONResponse({"error": {"code": "FRONTEND_NOT_BUILT"}}, status_code=503)

    return app


def factory():
    return create_app(engine_port=int(os.environ.get("CONSOLE_ENGINE_PORT", "8121")))
