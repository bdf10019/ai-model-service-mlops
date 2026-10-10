from __future__ import annotations

import asyncio
import hashlib
import json
import os
import socket
import stat
import subprocess
import time
from pathlib import Path

import httpx
import psutil

from .domain import InferRequest, Profile, Triage, prompt

MODEL_SHA = "d4729ea11ea540d46be45effe7b41ffbe55bc51ce7e42bf7443cd2bbf84ca9ed"


class EngineError(Exception):
    pass


def checked_file(root: Path, relative: str) -> Path:
    p = root / relative
    if not p.resolve().is_relative_to(root.resolve()):
        raise EngineError("ARTIFACT_PATH_INVALID")
    cursor = p
    while cursor != root.parent:
        info = cursor.lstat()
        if cursor.is_symlink() or getattr(info, "st_file_attributes", 0) & getattr(
            stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400
        ):
            raise EngineError("ARTIFACT_LINK_FORBIDDEN")
        if cursor == root:
            break
        cursor = cursor.parent
    if not p.is_file():
        raise EngineError("ARTIFACT_MISSING")
    return p


def hash_file(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        while chunk := f.read(1024 * 1024):
            h.update(chunk)
    return h.hexdigest()


def verify_import(root: Path) -> dict:
    try:
        data = (root / "schemas/model-import.json").read_bytes()
        if len(data) > 128 * 1024:
            raise EngineError("MANIFEST_TOO_LARGE")
        manifest = json.loads(data)
        if manifest["schema_version"] != "1.0" or manifest["model_id"] != "q8":
            raise EngineError("MANIFEST_UNSUPPORTED")
        model = next(f for f in manifest["files"] if f["path"] == "artifacts/models/q8/model.gguf")
        if model["sha256"] != MODEL_SHA:
            raise EngineError("MODEL_IDENTITY_MISMATCH")
        license_file = checked_file(root, "docs/licenses/Qwen3-Apache-2.0.txt")
        if hash_file(license_file) != manifest["license_sha256"]:
            raise EngineError("LICENSE_MISMATCH")
        files = manifest["files"]
        if len(files) > 100 or len({f["path"] for f in files}) != len(files):
            raise EngineError("MANIFEST_INVALID")
        for entry in files:
            if not entry["path"].startswith(("tools/llama/", "artifacts/models/q8/")):
                raise EngineError("ARTIFACT_PATH_INVALID")
            p = checked_file(root, entry["path"])
            if p.stat().st_size != entry["bytes"] or hash_file(p) != entry["sha256"]:
                raise EngineError("ARTIFACT_HASH_MISMATCH")
        return {
            "verified_at": time.time(),
            "manifest_sha256": hashlib.sha256(data).hexdigest(),
            "model_sha256": MODEL_SHA,
            "file_count": len(files),
            "version": manifest["version"],
            "lineage_limitations": manifest["lineage_limitations"],
        }
    except EngineError:
        raise
    except (OSError, ValueError, KeyError, StopIteration) as e:
        raise EngineError("ARTIFACT_MISSING_OR_INVALID") from e


class Engine:
    def __init__(self, root: Path, state: Path, port: int = 8121):
        self.root, self.state, self.port = root, state, port
        self.process: subprocess.Popen | None = None
        self.phase = "stopped"
        self.reason: str | None = None
        self.log = None
        self.lock = asyncio.Lock()
        self.started_at: float | None = None

    def status(self) -> dict:
        if self.process is not None and self.process.poll() is not None:
            self.phase = "failed"
            self.reason = "ENGINE_EXITED"
        return {
            "status": self.phase,
            "reason": self.reason,
            "ready": self.phase == "ready",
            "runtime": "local_process",
            "started_at": self.started_at,
            "context_tokens": 4096,
            "concurrency": 1,
            "model_sha256": MODEL_SHA if self.phase == "ready" else None,
        }

    async def start(self) -> dict:
        async with self.lock:
            if self.status()["ready"]:
                return self.status()
            self.phase = "verifying"
            try:
                evidence = await asyncio.to_thread(verify_import, self.root)
                with socket.socket() as probe:
                    if probe.connect_ex(("127.0.0.1", self.port)) == 0:
                        raise EngineError("ENGINE_PORT_OCCUPIED")
                self.phase = "loading"
                self.state.mkdir(parents=True, exist_ok=True)
                self.log = (self.state / "engine-private.log").open("ab")
                binary = checked_file(self.root, "tools/llama/llama-server.exe")
                env = dict(os.environ)
                env["TMP"] = env["TEMP"] = str(self.root / "tmp")
                self.process = subprocess.Popen(
                    [
                        str(binary),
                        "--model",
                        "artifacts/models/q8/model.gguf",
                        "--alias",
                        "q8",
                        "--host",
                        "127.0.0.1",
                        "--port",
                        str(self.port),
                        "--ctx-size",
                        "4096",
                        "--parallel",
                        "1",
                        "--n-gpu-layers",
                        "all",
                        "--flash-attn",
                        "on",
                        "--slots",
                    ],
                    cwd=self.root,
                    env=env,
                    stdout=self.log,
                    stderr=subprocess.STDOUT,
                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
                )
                deadline = time.monotonic() + 90
                async with httpx.AsyncClient(trust_env=False, timeout=2) as client:
                    while time.monotonic() < deadline:
                        if self.process.poll() is not None:
                            raise EngineError("ENGINE_START_FAILED")
                        try:
                            response = await client.get(f"http://127.0.0.1:{self.port}/health")
                            if response.status_code == 200:
                                self.phase, self.reason = "ready", None
                                self.started_at = time.time()
                                return {**self.status(), "verification": evidence}
                        except httpx.HTTPError:
                            pass
                        await asyncio.sleep(0.3)
                raise EngineError("ENGINE_START_TIMEOUT")
            except Exception as e:
                await self.stop()
                self.phase = "failed"
                self.reason = str(e) if isinstance(e, EngineError) else "ENGINE_START_FAILED"
                raise EngineError(self.reason) from e

    async def stop(self):
        process = self.process
        if process is not None and process.poll() is None:
            # Terminate only this launch and its children, never unrelated model services.
            try:
                owned = psutil.Process(process.pid)
                children = owned.children(recursive=True)
                for child in children:
                    child.terminate()
                process.terminate()
                try:
                    await asyncio.to_thread(process.wait, 5)
                except subprocess.TimeoutExpired:
                    for child in children:
                        if child.is_running():
                            child.kill()
                    process.kill()
                    await asyncio.to_thread(process.wait, 5)
            except psutil.NoSuchProcess:
                pass
        self.process = None
        if self.log:
            self.log.close()
            self.log = None
        self.phase = "stopped"
        self.reason = None

    async def generate(self, request: InferRequest, profile: Profile) -> tuple[dict, dict]:
        if not self.status()["ready"]:
            raise EngineError("ENGINE_UNAVAILABLE")
        body = {
            "model": "q8",
            "messages": prompt(request, profile),
            "temperature": profile.temperature,
            "seed": profile.seed,
            "max_tokens": profile.max_tokens,
            "stream": False,
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": "triage",
                    "strict": True,
                    "schema": Triage.model_json_schema(),
                },
            },
            "chat_template_kwargs": {"enable_thinking": False},
        }
        try:
            async with httpx.AsyncClient(
                trust_env=False, timeout=180, follow_redirects=False
            ) as client:
                async with client.stream(
                    "POST", f"http://127.0.0.1:{self.port}/v1/chat/completions", json=body
                ) as response:
                    if response.status_code != 200:
                        raise EngineError("ENGINE_RESPONSE_ERROR")
                    chunks = bytearray()
                    async for chunk in response.aiter_bytes():
                        if len(chunks) + len(chunk) > 256 * 1024:
                            raise EngineError("ENGINE_RESPONSE_TOO_LARGE")
                        chunks.extend(chunk)
            envelope = json.loads(chunks)
            choice = envelope["choices"][0]
            if choice.get("finish_reason") != "stop":
                raise EngineError("MODEL_OUTPUT_TRUNCATED")
            raw = json.loads(choice["message"]["content"])
            return raw, {
                "usage": envelope.get("usage", {}),
                "ttft_ms": None,
                "streaming": "not_requested",
                "max_tokens": profile.max_tokens,
                "temperature": profile.temperature,
                "seed": profile.seed,
                "context_tokens": 4096,
            }
        except EngineError:
            raise
        except (httpx.HTTPError, ValueError, KeyError, IndexError) as e:
            raise EngineError("MODEL_INFERENCE_ERROR") from e

    async def drain_after_error(self):
        # Closing HTTP is insufficient proof that generation stopped.
        try:
            async with httpx.AsyncClient(trust_env=False, timeout=2) as client:
                response = await client.get(f"http://127.0.0.1:{self.port}/slots")
                if response.status_code == 200 and not any(
                    s.get("is_processing", True) for s in response.json()
                ):
                    return
        except (httpx.HTTPError, ValueError, TypeError):
            pass
        await self.stop()
        self.phase, self.reason = "failed", "ENGINE_STOPPED_FOR_CAPACITY_SAFETY"
