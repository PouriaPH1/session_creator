"""Authentication for every HTTP and WebSocket entry point."""

import asyncio
import hmac
import ipaddress
import logging
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel, Field, SecretStr
from starlette.requests import HTTPConnection

from server.auth_service import LoginThrottled

logger = logging.getLogger(__name__)
COOKIE = "panel_session"
router = APIRouter()
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


def same_origin(connection: HTTPConnection) -> bool:
    origin = connection.headers.get("origin")
    expected = str(connection.base_url).rstrip("/")
    expected = expected.replace("wss://", "https://", 1).replace("ws://", "http://", 1)
    return connection.headers.get("sec-fetch-site") != "cross-site" and (
        origin is None or origin == expected
    )


def secure_transport(connection: HTTPConnection) -> bool:
    if connection.url.scheme in {"https", "wss"}:
        return True
    try:
        return ipaddress.ip_address(connection.client.host).is_loopback
    except (ValueError, AttributeError):
        return False


class AdminAuthMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] not in {"http", "websocket"}:
            return await self.app(scope, receive, send)
        connection = HTTPConnection(scope)
        auth = connection.app.state.auth
        path = scope["path"]
        is_websocket = scope["type"] == "websocket"

        async def secured_send(message):
            if message["type"] == "http.response.start":
                message["headers"] = list(message.get("headers", [])) + [
                    (b"cache-control", b"no-store"),
                    (b"x-content-type-options", b"nosniff"),
                    (b"x-frame-options", b"DENY"),
                    (b"referrer-policy", b"no-referrer"),
                    (b"content-security-policy", b"frame-ancestors 'none'; base-uri 'self'; object-src 'none'; form-action 'self'"),
                ]
            await send(message)

        async def deny(code, detail):
            if is_websocket:
                await send({"type": "websocket.close", "code": 4401 if code == 401 else 4403})
            else:
                await JSONResponse({"detail": detail}, status_code=code)(scope, receive, secured_send)

        # Health checks expose no account data and accept no writes.
        if path == "/health" and scope.get("method") in {"GET", "HEAD"}:
            return await self.app(scope, receive, secured_send)
        if not secure_transport(connection) and not connection.app.state.auth_allow_http:
            return await deny(426, "برای ورود از HTTPS یا تونل SSH استفاده کنید.")
        if not same_origin(connection):
            return await deny(403, "مبدأ درخواست مجاز نیست.")
        if not is_websocket and path in {"/login", "/api/auth/login"}:
            return await self.app(scope, receive, secured_send)
        token = connection.cookies.get(COOKIE, "")
        session = auth.session(token)
        if session is None:
            bearer = connection.headers.get("authorization", "")
            worker_path = path in {"/sessions", "/api/config/telegram"} or path.startswith(("/otp/", "/api/otp/"))
            if not is_websocket and scope["method"] == "GET" and worker_path and bearer.startswith("Bearer "):
                if auth.valid_worker_token(bearer[7:]):
                    return await self.app(scope, receive, secured_send)
            if not is_websocket and scope["method"] == "GET" and path in {"/", "/docs", "/redoc"}:
                return await RedirectResponse("/login", status_code=303)(scope, receive, secured_send)
            return await deny(401, "ورود به پنل لازم است.")
        scope.setdefault("state", {})["admin_session"] = session
        if not is_websocket:
            if scope["method"] not in SAFE_METHODS:
                csrf = connection.headers.get("x-csrf-token", "")
                if not hmac.compare_digest(csrf.encode(), session["csrf"].encode()):
                    return await deny(403, "توکن امنیتی درخواست معتبر نیست.")
            return await self.app(scope, receive, secured_send)

        # Close an already open socket after logout, expiry or credential changes.
        # Keep its receive operation alive across heartbeat checks.
        task = asyncio.create_task(self.app(scope, receive, send))
        try:
            while not task.done():
                done, _ = await asyncio.wait({task}, timeout=5)
                if done:
                    return await task
                if auth.session(token) is None:
                    await send({"type": "websocket.close", "code": 4401})
                    return
        finally:
            if not task.done():
                task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: SecretStr = Field(min_length=1, max_length=256)


class CredentialsRequest(BaseModel):
    username: str = Field(min_length=3, max_length=64)
    password: SecretStr = Field(min_length=12, max_length=256)
    current_password: SecretStr = Field(min_length=1, max_length=256)


def clear_cookie(response: Response):
    response.delete_cookie(COOKIE, path="/", httponly=True, samesite="strict")


@router.get("/login", response_class=HTMLResponse, include_in_schema=False)
async def login_page():
    return HTMLResponse((Path(__file__).parent / "templates/login.html").read_text(encoding="utf-8"))


@router.post("/api/auth/login")
async def login(payload: LoginRequest, request: Request, response: Response):
    auth = request.app.state.auth
    if not auth.configured():
        raise HTTPException(503, "ابتدا مدیر را با دستور python -m cli.admin_auth روی سرور تنظیم کنید.")
    try:
        result = await asyncio.to_thread(auth.login, payload.username, payload.password.get_secret_value(), request.client.host)
    except LoginThrottled:
        raise HTTPException(429, "تعداد تلاش‌های ورود زیاد است. ۱۵ دقیقه دیگر تلاش کنید.", headers={"Retry-After": "900"})
    if result is None:
        logger.warning("Administrator login rejected from %s", request.client.host)
        raise HTTPException(401, "نام کاربری یا رمز عبور نادرست است.")
    token, csrf = result
    response.set_cookie(COOKIE, token, max_age=auth.session_seconds, httponly=True,
                        secure=request.url.scheme == "https", samesite="strict", path="/")
    logger.info("Administrator login accepted from %s", request.client.host)
    return {"csrf_token": csrf}


@router.get("/api/auth/me")
async def me(request: Request):
    session = request.state.admin_session
    return {"username": session["username"], "csrf_token": session["csrf"]}


@router.post("/api/auth/logout")
async def logout(request: Request, response: Response):
    request.app.state.auth.logout(request.cookies.get(COOKIE, ""))
    clear_cookie(response)
    return {"status": "logged_out"}


@router.post("/api/auth/credentials")
async def change_credentials(payload: CredentialsRequest, request: Request, response: Response):
    try:
        request.app.state.auth.reserve_attempt(request.client.host)
        changed = await asyncio.to_thread(
            request.app.state.auth.change_credentials, request.cookies.get(COOKIE, ""),
            payload.username, payload.password.get_secret_value(), payload.current_password.get_secret_value(),
        )
    except LoginThrottled:
        raise HTTPException(429, "تعداد تلاش‌ها زیاد است. ۱۵ دقیقه دیگر تلاش کنید.", headers={"Retry-After": "900"})
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    if not changed:
        raise HTTPException(403, "رمز فعلی نادرست است یا نشست ورود منقضی شده است.")
    clear_cookie(response)
    logger.info("Administrator credentials changed; all sessions and worker access revoked")
    return {"status": "updated", "requires_login": True}


@router.post("/api/auth/worker-token")
async def rotate_worker_token(request: Request):
    token = request.app.state.auth.rotate_worker_token(request.cookies.get(COOKIE, ""))
    return {"token": token}
