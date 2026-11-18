"""Persistent administrator credentials, revocable sessions and worker access."""

import hashlib
import hmac
import secrets
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path


class LoginThrottled(Exception):
    pass


class AuthService:
    session_seconds = 8 * 60 * 60
    password_iterations = 600_000

    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        # Create with private permissions before SQLite writes any credentials.
        path.touch(mode=0o600, exist_ok=True)
        path.chmod(0o600)
        with self._db() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS administrator (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    username TEXT NOT NULL,
                    password_hash TEXT NOT NULL,
                    revision TEXT NOT NULL,
                    worker_hash TEXT
                );
                CREATE TABLE IF NOT EXISTS sessions (
                    token_hash TEXT PRIMARY KEY,
                    csrf TEXT NOT NULL,
                    expires REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS login_attempts (
                    address TEXT NOT NULL,
                    attempted REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS login_attempt_time
                    ON login_attempts(attempted);
            """)

    @contextmanager
    def _db(self):
        db = sqlite3.connect(self.path, timeout=10)
        try:
            db.row_factory = sqlite3.Row
            with db:
                yield db
        finally:
            db.close()

    @staticmethod
    def digest(token: str) -> str:
        return hashlib.sha256(token.encode()).hexdigest()

    @classmethod
    def hash_password(cls, password: str) -> str:
        salt = secrets.token_hex(16)
        digest = hashlib.pbkdf2_hmac(
            "sha256", password.encode(), bytes.fromhex(salt), cls.password_iterations
        ).hex()
        return f"{salt}${digest}"

    @classmethod
    def verify_password(cls, password: str, encoded: str) -> bool:
        salt, expected = encoded.split("$", 1)
        actual = hashlib.pbkdf2_hmac(
            "sha256", password.encode(), bytes.fromhex(salt), cls.password_iterations
        ).hex()
        return hmac.compare_digest(actual, expected)

    def configured(self) -> bool:
        with self._db() as db:
            return db.execute("SELECT 1 FROM administrator").fetchone() is not None

    def set_credentials(self, username: str, password: str, *, replace=False):
        """Local recovery/bootstrap only. Web changes require the current password."""
        username = username.strip()
        if not 3 <= len(username) <= 64 or not 12 <= len(password) <= 256:
            raise ValueError("Username must be 3–64 characters and password 12–256 characters.")
        encoded = self.hash_password(password)
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            if db.execute("SELECT 1 FROM administrator").fetchone() and not replace:
                raise ValueError("An administrator already exists. Use --reset for local recovery.")
            db.execute(
                "INSERT INTO administrator VALUES (1, ?, ?, ?, NULL) "
                "ON CONFLICT(id) DO UPDATE SET username=excluded.username, "
                "password_hash=excluded.password_hash, revision=excluded.revision, worker_hash=NULL",
                (username, encoded, secrets.token_hex(16)),
            )
            db.execute("DELETE FROM sessions")
            db.execute("DELETE FROM login_attempts")

    def reserve_attempt(self, address: str):
        now = time.time()
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("DELETE FROM login_attempts WHERE attempted < ?", (now - 900,))
            total, per_address = db.execute(
                "SELECT count(*), coalesce(sum(address = ?), 0) FROM login_attempts", (address,)
            ).fetchone()
            if per_address >= 10 or total >= 100:
                raise LoginThrottled()
            db.execute("INSERT INTO login_attempts VALUES (?, ?)", (address, now))
        return now

    def login(self, username: str, password: str, address: str):
        now = self.reserve_attempt(address)
        with self._db() as db:
            admin = db.execute("SELECT * FROM administrator").fetchone()
        if admin is None:
            return None
        valid_password = self.verify_password(password, admin["password_hash"])
        valid_username = hmac.compare_digest(username.encode(), admin["username"].encode())
        if not (valid_password and valid_username):
            return None
        token, csrf = secrets.token_urlsafe(48), secrets.token_urlsafe(32)
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            # A password change racing with login must invalidate that login too.
            current = db.execute("SELECT revision FROM administrator").fetchone()
            if current["revision"] != admin["revision"]:
                return None
            db.execute("DELETE FROM sessions WHERE expires <= ?", (now,))
            db.execute("INSERT INTO sessions VALUES (?, ?, ?)",
                       (self.digest(token), csrf, now + self.session_seconds))
        return token, csrf

    def session(self, token: str):
        if not token or len(token) > 256:
            return None
        with self._db() as db:
            row = db.execute(
                "SELECT sessions.*, administrator.username FROM sessions "
                "CROSS JOIN administrator WHERE token_hash = ? AND expires > ?",
                (self.digest(token), time.time()),
            ).fetchone()
            return dict(row) if row else None

    def logout(self, token: str):
        with self._db() as db:
            db.execute("DELETE FROM sessions WHERE token_hash = ?", (self.digest(token),))

    def change_credentials(self, token: str, username: str, password: str, current_password: str):
        username = username.strip()
        if not 3 <= len(username) <= 64 or not 12 <= len(password) <= 256:
            raise ValueError("نام کاربری باید ۳ تا ۶۴ و رمز جدید ۱۲ تا ۲۵۶ نویسه باشد.")
        with self._db() as db:
            admin = db.execute("SELECT * FROM administrator").fetchone()
        if not self.verify_password(current_password, admin["password_hash"]):
            return False
        encoded = self.hash_password(password)
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            active = db.execute("SELECT 1 FROM sessions WHERE token_hash=? AND expires>?",
                                (self.digest(token), time.time())).fetchone()
            changed = db.execute("SELECT revision FROM administrator").fetchone()
            if not active or changed["revision"] != admin["revision"]:
                return False
            db.execute("UPDATE administrator SET username=?, password_hash=?, revision=?, worker_hash=NULL",
                       (username, encoded, secrets.token_hex(16)))
            db.execute("DELETE FROM sessions")
        return True

    def rotate_worker_token(self, session_token: str) -> str:
        token = secrets.token_urlsafe(48)
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            active = db.execute("SELECT 1 FROM sessions WHERE token_hash=? AND expires>?",
                                (self.digest(session_token), time.time())).fetchone()
            if not active:
                raise ValueError("نشست ورود منقضی شده است.")
            db.execute("UPDATE administrator SET worker_hash=?", (self.digest(token),))
        return token

    def valid_worker_token(self, token: str) -> bool:
        if not token or len(token) > 256:
            return False
        with self._db() as db:
            row = db.execute("SELECT worker_hash FROM administrator").fetchone()
        return bool(row and row[0] and hmac.compare_digest(self.digest(token), row[0]))
