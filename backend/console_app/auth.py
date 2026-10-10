from __future__ import annotations

import hashlib
import hmac
import secrets
import time
import uuid

from fastapi import HTTPException, Request, Response
from pydantic import Field

from .domain import StrictModel
from .store import Store


class Credentials(StrictModel):
    username: str = Field(pattern=r"^[a-z][a-z0-9_-]{2,31}$")
    password: str = Field(min_length=12, max_length=128)


class NewUser(Credentials):
    role: str = Field(pattern=r"^(operator|reviewer|admin)$")


def password_hash(password: str, salt: str | None = None) -> str:
    salt = salt or secrets.token_hex(16)
    digest = hashlib.scrypt(
        password.encode(), salt=bytes.fromhex(salt), n=16384, r=8, p=1, maxmem=64 * 1024 * 1024
    ).hex()
    return f"{salt}:{digest}"


def digest_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def public_user(row) -> dict:
    return {"id": row["id"], "username": row["username"], "role": row["role"]}


def create_user(store: Store, data: NewUser, bootstrap: bool = False) -> dict:
    encoded = password_hash(data.password)
    with store.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        if bootstrap and db.execute("SELECT COUNT(*) FROM users").fetchone()[0]:
            raise HTTPException(409, "INITIALIZED")
        if db.execute("SELECT 1 FROM users WHERE username=?", (data.username,)).fetchone():
            raise HTTPException(409, "USERNAME_EXISTS")
        user = {"id": uuid.uuid4().hex, "username": data.username, "role": data.role}
        db.execute(
            "INSERT INTO users VALUES(?,?,?,?,?)",
            (user["id"], data.username, encoded, data.role, time.time()),
        )
    store.audit(
        user["id"] if bootstrap else "admin",
        "bootstrap" if bootstrap else "user_created",
        user["id"],
    )
    return user


class Auth:
    def __init__(self, store: Store):
        self.store = store
        self.failures: list[float] = []

    def initialized(self) -> bool:
        with self.store.connect() as db:
            return bool(db.execute("SELECT COUNT(*) FROM users").fetchone()[0])

    def login(self, data: Credentials) -> dict:
        now = time.time()
        self.failures = [t for t in self.failures if now - t < 60]
        if len(self.failures) >= 10:
            raise HTTPException(429, "LOGIN_RATE_LIMITED", headers={"Retry-After": "60"})
        with self.store.connect() as db:
            row = db.execute("SELECT * FROM users WHERE username=?", (data.username,)).fetchone()
        # Use a dummy hash for unknown accounts to avoid skipping the expensive check.
        saved = row["password"] if row else "0" * 32 + ":" + "0" * 128
        salt = saved.split(":")[0]
        if not hmac.compare_digest(password_hash(data.password, salt), saved) or row is None:
            self.failures.append(now)
            raise HTTPException(401, "INVALID_CREDENTIALS")
        return public_user(row)

    def issue(self, user: dict, response: Response, request: Request) -> dict:
        token = secrets.token_urlsafe(32)
        csrf = secrets.token_urlsafe(32)
        with self.store.connect() as db:
            old = request.cookies.get("console_session")
            if old:
                db.execute("DELETE FROM sessions WHERE digest=?", (digest_token(old),))
            db.execute("DELETE FROM sessions WHERE expires<?", (time.time(),))
            db.execute(
                "INSERT INTO sessions VALUES(?,?,?,?)",
                (digest_token(token), user["id"], csrf, time.time() + 8 * 3600),
            )
        response.set_cookie(
            "console_session",
            token,
            max_age=8 * 3600,
            httponly=True,
            samesite="strict",
            secure=request.url.scheme == "https",
            path="/",
        )
        self.store.audit(user["id"], "login", user["id"])
        return {"user": user, "csrf_token": csrf}

    def require(self, request: Request, roles: set[str] | None = None) -> dict:
        token = request.cookies.get("console_session", "")
        with self.store.connect() as db:
            row = db.execute(
                "SELECT u.id,u.username,u.role,s.csrf FROM sessions s JOIN users u ON u.id=s.user_id WHERE s.digest=? AND s.expires>?",
                (digest_token(token), time.time()),
            ).fetchone()
        if row is None:
            raise HTTPException(401, "AUTH_REQUIRED")
        request.state.actor_id = row["id"]
        if roles and row["role"] not in roles:
            raise HTTPException(403, "ROLE_FORBIDDEN")
        if request.method not in {"GET", "HEAD", "OPTIONS"}:
            supplied = request.headers.get("x-csrf-token", "")
            if not hmac.compare_digest(supplied, row["csrf"]):
                raise HTTPException(403, "CSRF_INVALID")
        return {**public_user(row), "csrf_token": row["csrf"]}

    def logout(self, request: Request, response: Response):
        user = self.require(request)
        with self.store.connect() as db:
            db.execute(
                "DELETE FROM sessions WHERE digest=?",
                (digest_token(request.cookies.get("console_session", "")),),
            )
        response.delete_cookie("console_session", path="/")
        self.store.audit(user["id"], "logout", user["id"])
