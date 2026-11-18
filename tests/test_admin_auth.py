import asyncio
import sqlite3
import time

import pytest
from fastapi import FastAPI, WebSocket
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from server.auth import AdminAuthMiddleware, COOKIE, router as auth_router
from server.auth_service import AuthService
from server.main import app


@pytest.mark.parametrize("method,path", [
    ("GET", "/api/sessions/source"), ("GET", "/api/accounts/passwords"),
    ("GET", "/api/accounts/passwords/export-csv"), ("GET", "/api/download/target-all-zip"),
    ("GET", "/api/config/telegram"), ("GET", "/otp/15550000000"),
    ("GET", "/api/otp/15550000000"), ("GET", "/sessions"),
    ("GET", "/openapi.json"), ("POST", "/api/accounts/terminate-sessions"),
    ("DELETE", "/api/sessions/source-all"), ("POST", "/api/sessions/upload"),
    ("GET", "/api/chat/messages"), ("POST", "/api/auth/worker-token"),
])
def test_all_sensitive_routes_require_authentication(auth_service, method, path):
    http = TestClient(app, base_url="https://testserver")
    response = http.request(method, path)
    assert response.status_code == 401
    assert response.headers["cache-control"] == "no-store"


def test_public_health_and_protected_panel(auth_service):
    http = TestClient(app, base_url="https://testserver")
    assert http.get("/health").json() == {"status": "healthy"}
    assert http.get("/", follow_redirects=False).headers["location"] == "/login"
    assert http.get("/login").status_code == 200
    assert http.get("/docs", follow_redirects=False).status_code == 303


def test_login_cookie_csrf_and_logout(client, auth_service):
    token = client.cookies.get(COOKIE)
    info = client.get("/api/auth/me").json()
    assert info["username"] == "administrator"
    assert client.post("/api/auth/logout", headers={"X-CSRF-Token": "wrong"}).status_code == 403
    assert client.get("/api/stats", headers={"Origin": "https://evil.example"}).status_code == 403
    assert client.post("/api/auth/logout", headers={"X-CSRF-Token": info["csrf_token"]}).status_code == 200
    assert client.get("/api/stats").status_code == 401
    assert auth_service.session(token) is None


def test_cookie_flags_and_login_csrf(auth_service):
    http = TestClient(app, base_url="https://testserver")
    payload = {"username": "administrator", "password": "Strong-test-password-123"}
    assert http.post("/api/auth/login", json=payload, headers={"Origin": "https://evil.example"}).status_code == 403
    response = http.post("/api/auth/login", json=payload)
    cookie = response.headers["set-cookie"].lower()
    assert "httponly" in cookie and "secure" in cookie and "samesite=strict" in cookie
    assert "password" not in response.json()


def test_credentials_change_revokes_other_sessions_and_worker(client, auth_service):
    other = TestClient(app, base_url="https://testserver")
    other.post("/api/auth/login", json={"username": "administrator", "password": "Strong-test-password-123"})
    csrf = client.get("/api/auth/me").json()["csrf_token"]
    headers = {"X-CSRF-Token": csrf}
    worker = client.post("/api/auth/worker-token", headers=headers).json()["token"]
    body = {"username": "new-admin", "password": "New-long-password-456", "current_password": "wrong"}
    assert client.post("/api/auth/credentials", json=body, headers=headers).status_code == 403
    assert other.get("/api/auth/me").status_code == 200
    body["current_password"] = "Strong-test-password-123"
    assert client.post("/api/auth/credentials", json=body, headers=headers).status_code == 200
    assert client.get("/api/auth/me").status_code == 401
    assert other.get("/api/auth/me").status_code == 401
    assert not auth_service.valid_worker_token(worker)
    assert other.post("/api/auth/login", json={"username": "administrator", "password": "Strong-test-password-123"}).status_code == 401
    assert other.post("/api/auth/login", json={"username": "new-admin", "password": "New-long-password-456"}).status_code == 200


def test_worker_token_scope_and_rotation(client, auth_service, monkeypatch):
    from server.main import pool
    monkeypatch.setattr(pool, "list_sessions", lambda: [])
    csrf = client.get("/api/auth/me").json()["csrf_token"]
    first = client.post("/api/auth/worker-token", headers={"X-CSRF-Token": csrf}).json()["token"]
    worker = TestClient(app, base_url="https://testserver", headers={"Authorization": f"Bearer {first}"})
    assert worker.get("/sessions").status_code == 200
    assert worker.get("/api/config/telegram").status_code == 200
    assert worker.get("/otp/nonexistent").status_code == 404
    for route in ("/api/accounts/passwords", "/api/download/target-all-zip", "/api/auth/me", "/openapi.json"):
        assert worker.get(route).status_code == 401
    assert worker.post("/api/accounts/terminate-sessions").status_code == 401
    second = client.post("/api/auth/worker-token", headers={"X-CSRF-Token": csrf}).json()["token"]
    assert second != first
    assert worker.get("/sessions").status_code == 401


def test_rate_limit_is_persistent(auth_service):
    http = TestClient(app, base_url="https://testserver")
    for _ in range(10):
        assert http.post("/api/auth/login", json={"username": "administrator", "password": "incorrect"}).status_code == 401
    app.state.auth = AuthService(auth_service.path)
    response = http.post("/api/auth/login", json={"username": "administrator", "password": "Strong-test-password-123"})
    assert response.status_code == 429 and response.headers["retry-after"] == "900"


def test_session_expiry_and_hashed_storage(client, auth_service):
    token = client.cookies.get(COOKIE)
    with sqlite3.connect(auth_service.path) as db:
        stored_hash = db.execute("SELECT password_hash FROM administrator").fetchone()[0]
        assert stored_hash != "Strong-test-password-123"
        assert db.execute("SELECT token_hash FROM sessions").fetchone()[0] != token
        db.execute("UPDATE sessions SET expires=?", (time.time() - 1,))
    assert client.get("/api/auth/me").status_code == 401


def test_unconfigured_auth_fails_closed(tmp_path, monkeypatch):
    monkeypatch.setitem(app.state._state, "auth", AuthService(tmp_path / "unconfigured.sqlite3"))
    http = TestClient(app, base_url="https://testserver")
    assert http.get("/api/stats").status_code == 401
    assert http.post("/api/auth/login", json={"username": "admin", "password": "some-password"}).status_code == 503


def test_remote_plain_http_blocked_and_forwarded_headers_ignored(auth_service):
    http = TestClient(app, base_url="http://testserver")
    assert http.get("/login").status_code == 426
    assert http.get("/api/stats", headers={"X-Forwarded-Proto": "https", "X-Forwarded-For": "127.0.0.1"}).status_code == 426


def test_websocket_authentication_and_origin(client, auth_service):
    anonymous = TestClient(app, base_url="https://testserver")
    with pytest.raises(WebSocketDisconnect):
        with anonymous.websocket_connect("wss://testserver/ws/chat/unknown/123"):
            pytest.fail("Unauthenticated WebSocket was accepted")
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("wss://testserver/ws/chat/unknown/123", headers={"Origin": "https://evil.example"}):
            pytest.fail("Cross-origin WebSocket was accepted")


def test_open_websocket_closes_after_logout(auth_service):
    isolated_app = FastAPI()
    isolated_app.state.auth = auth_service
    isolated_app.state.auth_allow_http = False
    isolated_app.add_middleware(AdminAuthMiddleware)
    isolated_app.include_router(auth_router)

    @isolated_app.websocket("/ws/echo")
    async def echo(socket: WebSocket):
        await socket.accept()
        while True:
            await socket.send_text(await socket.receive_text())

    client = TestClient(isolated_app, base_url="https://testserver")
    logged_in = client.post("/api/auth/login", json={
        "username": "administrator", "password": "Strong-test-password-123"
    })
    with client.websocket_connect("wss://testserver/ws/echo", headers={"Origin": "https://testserver"}) as socket:
        socket.send_text("authorized")
        assert socket.receive_text() == "authorized"
        response = client.post("/api/auth/logout", headers={"X-CSRF-Token": logged_in.json()["csrf_token"]})
        assert response.status_code == 200
        with pytest.raises(WebSocketDisconnect):
            socket.receive_text()


def test_worker_rejects_remote_plain_http():
    from core.exceptions import MasterServerError
    from worker.otp_client import HttpOTPClient
    with pytest.raises(MasterServerError):
        HttpOTPClient("http://45.141.118.92:8000", api_token="secret-token")
    assert HttpOTPClient("https://panel.example", api_token="secret-token").headers == {
        "Authorization": "Bearer secret-token"
    }
    assert HttpOTPClient("http://127.0.0.1:8000").base_url == "http://127.0.0.1:8000"
