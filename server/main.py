import datetime
import io
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import List, Optional
import zipfile

from fastapi import FastAPI, File, HTTPException, Query, Response, UploadFile, status
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel

from config.settings import get_settings
from core.exceptions import OTPNotFoundError, SessionNotFoundError
from core.telethon_patch import apply_telethon_patches
from server.account_service import AccountManagementService
from server.migration_manager import MigrationJobManager
from server.otp_service import TelethonOTPService
from server.proxy_service import ProxyService
from server.session_pool import TelethonSessionPool

# Ensure Telethon TL resilience patches are active
apply_telethon_patches()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("master_server")

settings = get_settings()
pool = TelethonSessionPool(settings)
otp_service = TelethonOTPService(pool)
migration_manager = MigrationJobManager(settings, otp_service)
account_service = AccountManagementService(pool, settings)
proxy_service = ProxyService(settings)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting Master Telegram Session Manager...")
    await pool.start()
    yield
    logger.info("Stopping Master Telegram Session Manager...")
    await pool.stop()


app = FastAPI(
    title="Telegram Session OTP Master Server & Admin Panel",
    description="Centralized Web Admin Panel & API for managing, monitoring, and migrating Telethon sessions.",
    version="1.1.0",
    lifespan=lifespan
)

# Path to Dashboard HTML
TEMPLATES_DIR = Path(__file__).parent / "templates"
INDEX_HTML_PATH = TEMPLATES_DIR / "index.html"


# ==========================================
# Pydantic Request/Response Models
# ==========================================
class OTPResponse(BaseModel):
    identifier: str
    code: str
    timestamp: float


class SessionsResponse(BaseModel):
    count: int
    sessions: list[str]


class SingleMigrateRequest(BaseModel):
    phone: str
    password: Optional[str] = None
    proxy: Optional[str] = None


class BatchMigrateRequest(BaseModel):
    delay: float = 15.0
    proxy: Optional[str] = None


class AddProxyRequest(BaseModel):
    name: str
    url: str
    is_default: bool = False


class TestProxyRequest(BaseModel):
    url: str


class UpdateProfileRequest(BaseModel):
    identifier: str
    first_name: str
    last_name: str = ""
    about: str = ""


class Set2FARequest(BaseModel):
    identifier: str
    new_password: str
    current_password: Optional[str] = None
    hint: str = ""


class BatchRandom2FARequest(BaseModel):
    identifiers: Optional[List[str]] = None
    length: int = 12
    delay: float = 2.0


class BatchNamesRequest(BaseModel):
    locale: str = "fa"
    identifiers: Optional[List[str]] = None
    delay: float = 1.5


class AccountActionRequest(BaseModel):
    identifier: str


# ==========================================
# Web Admin Panel Route
# ==========================================
@app.get("/", response_class=HTMLResponse, summary="Serve Admin Dashboard")
async def serve_admin_panel():
    if not INDEX_HTML_PATH.exists():
        raise HTTPException(status_code=404, detail="Admin panel template not found.")
    return HTMLResponse(content=INDEX_HTML_PATH.read_text(encoding="utf-8"))


# ==========================================
# Dashboard & Stats API
# ==========================================
@app.get("/api/stats", summary="Get system statistics")
async def get_stats():
    source_files = list(settings.source_sessions_dir.glob("*.session"))
    target_files = list(settings.target_sessions_dir.glob("*.session"))
    return {
        "status": "healthy",
        "source_sessions_count": len(source_files),
        "target_sessions_count": len(target_files),
        "active_clients_count": len(pool.list_sessions()),
        "migration_running": migration_manager.is_running
    }


@app.get("/health", summary="Check health and pool status")
async def health_check():
    return {
        "status": "healthy",
        "active_sessions_count": len(pool.list_sessions())
    }


@app.get("/api/config/telegram", summary="Get Telegram API ID/Hash for workers")
async def get_telegram_config():
    """Provides public Telegram API credentials to authorized workers for seamless setup."""
    return {
        "api_id": settings.api_id,
        "api_hash": settings.api_hash
    }


# ==========================================
# Source Sessions Management API
# ==========================================
@app.get("/sessions", response_model=SessionsResponse, summary="List available active sessions")
async def list_sessions():
    sessions = pool.list_sessions()
    return SessionsResponse(count=len(sessions), sessions=sessions)


@app.get("/api/sessions/source", summary="List detailed source sessions")
async def list_source_sessions():
    return pool.get_detailed_sessions()


@app.post("/api/sessions/upload", summary="Upload .session and .csv files")
async def upload_sessions(files: List[UploadFile] = File(...)):
    saved_count = 0
    csv_count = 0
    for file in files:
        if file.filename.endswith(".session"):
            dest_path = settings.source_sessions_dir / file.filename
            content = await file.read()
            dest_path.write_bytes(content)
            saved_count += 1
        elif file.filename.endswith(".csv"):
            content = await file.read()
            csv_text = content.decode("utf-8-sig", errors="ignore")
            csv_count += account_service.import_passwords_csv(csv_text)

    # Reload pool after upload
    await pool.reload()
    return {
        "status": "uploaded",
        "uploaded_count": saved_count,
        "csv_passwords_imported": csv_count,
        "total_active": len(pool.list_sessions())
    }


@app.delete("/api/sessions/source-all", summary="Delete all source session files")
async def delete_all_source_sessions():
    await pool.stop()
    session_files = list(settings.source_sessions_dir.glob("*.session*"))
    deleted_count = 0
    for f in session_files:
        try:
            f.unlink()
            deleted_count += 1
        except Exception as e:
            logger.warning(f"Error deleting session file {f.name}: {e}")

    await pool.reload()
    return {"status": "deleted", "deleted_count": deleted_count}


@app.delete("/api/sessions/source/{session_name}", summary="Delete source session file")
async def delete_source_session(session_name: str):
    client = pool.get_client(session_name)
    if client and client.is_connected():
        try:
            await client.disconnect()
        except Exception:
            pass

    session_file = settings.source_sessions_dir / f"{session_name}.session"
    if session_file.exists():
        session_file.unlink()
        journal = settings.source_sessions_dir / f"{session_name}.session-journal"
        if journal.exists():
            try:
                journal.unlink()
            except Exception:
                pass
        await pool.reload()
        return {"status": "deleted", "session": session_name}
    raise HTTPException(status_code=404, detail="Session file not found.")



@app.post("/sessions/reload", summary="Reload sessions from source folder")
@app.post("/api/sessions/reload", summary="Reload sessions from source folder")
async def reload_sessions():
    await pool.reload()
    return {
        "status": "reloaded",
        "active_sessions_count": len(pool.list_sessions())
    }


# ==========================================
# Target Sessions API (Created)
# ==========================================
@app.get("/api/sessions/target", summary="List generated target sessions")
async def list_target_sessions():
    import json
    target_files = list(settings.target_sessions_dir.glob("*.session"))
    meta_file = settings.base_dir / "storage" / "target_sessions_metadata.json"
    metadata_map = {}
    if meta_file.exists():
        try:
            metadata_map = json.loads(meta_file.read_text(encoding="utf-8"))
        except Exception:
            metadata_map = {}

    results = []
    for f in target_files:
        stats = f.stat()
        meta = metadata_map.get(f.name, {})
        created_via = meta.get("created_via", "direct")
        proxy_name = meta.get("proxy_name")
        if not proxy_name:
            proxy_name = "پروکسی" if created_via == "proxy" else "مستقیم (سرور اول)"
        proxy_ip = meta.get("proxy_ip") or meta.get("proxy") or ("آی‌پی سرور اول" if created_via == "direct" else "-")

        results.append({
            "filename": f.name,
            "phone": meta.get("phone", f.stem),
            "size_kb": round(stats.st_size / 1024, 2),
            "created_at": meta.get("created_at") or datetime.datetime.fromtimestamp(stats.st_mtime).strftime("%Y-%m-%d %H:%M:%S"),
            "created_via": created_via,
            "proxy_name": proxy_name,
            "proxy_ip": proxy_ip,
            "proxy_url": meta.get("proxy_url")
        })
    return sorted(results, key=lambda x: x["created_at"], reverse=True)


@app.delete("/api/sessions/target-all", summary="Delete all generated target sessions")
async def delete_all_target_sessions():
    import json
    target_files = list(settings.target_sessions_dir.glob("*.session*"))
    deleted_count = 0
    for f in target_files:
        try:
            f.unlink()
            deleted_count += 1
        except Exception as e:
            logger.warning(f"Error deleting target file {f.name}: {e}")

    meta_file = settings.base_dir / "storage" / "target_sessions_metadata.json"
    if meta_file.exists():
        try:
            meta_file.write_text("{}", encoding="utf-8")
        except Exception:
            pass
    return {"status": "deleted", "deleted_count": deleted_count}


@app.delete("/api/sessions/target/{filename}", summary="Delete a generated target session")
async def delete_target_session(filename: str):
    import json
    file_path = settings.target_sessions_dir / filename
    if file_path.exists():
        file_path.unlink()
        journal = settings.target_sessions_dir / f"{filename}-journal"
        if journal.exists():
            try:
                journal.unlink()
            except Exception:
                pass
        meta_file = settings.base_dir / "storage" / "target_sessions_metadata.json"
        if meta_file.exists():
            try:
                m_data = json.loads(meta_file.read_text(encoding="utf-8"))
                if filename in m_data:
                    del m_data[filename]
                    meta_file.write_text(json.dumps(m_data, ensure_ascii=False, indent=2), encoding="utf-8")
            except Exception:
                pass
        return {"status": "deleted", "filename": filename}
    raise HTTPException(status_code=404, detail="File not found")


@app.get("/api/download/target/{filename}", summary="Download a generated target session")
async def download_target_session(filename: str):
    file_path = settings.target_sessions_dir / filename
    if not file_path.exists() or not file_path.is_file():
        raise HTTPException(status_code=404, detail="File not found")
    return FileResponse(
        path=file_path,
        filename=filename,
        media_type="application/octet-stream"
    )


@app.get("/api/download/target-all-zip", summary="Download all target sessions in a zip archive")
async def download_all_target_zip():
    target_files = list(settings.target_sessions_dir.glob("*.session"))
    if not target_files:
        raise HTTPException(status_code=404, detail="No target sessions found to download.")

    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zip_file:
        for file_path in target_files:
            zip_file.write(file_path, arcname=file_path.name)

    zip_buffer.seek(0)
    filename = f"telegram_sessions_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.zip"

    return Response(
        content=zip_buffer.getvalue(),
        media_type="application/zip",
        headers={"Content-Disposition": f"attachment; filename={filename}"}
    )


# ==========================================
# OTP Retrieval API
# ==========================================
@app.get("/otp/{identifier}", response_model=OTPResponse, summary="Retrieve latest login OTP")
@app.get("/api/otp/{identifier}", response_model=OTPResponse, summary="Retrieve latest login OTP")
async def get_otp(
    identifier: str,
    since: Optional[float] = Query(
        default=None,
        description="Filter messages received strictly on or after this UNIX timestamp"
    )
):
    try:
        result = await otp_service.get_latest_otp(identifier, since_timestamp=since)
        if not result:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"No recent OTP found for '{identifier}' after timestamp {since}"
            )
        code, ts = result
        return OTPResponse(identifier=identifier, code=code, timestamp=ts)

    except SessionNotFoundError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(e)
        )
    except OTPNotFoundError as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e)
        )


# ==========================================
# Web Migration Execution API
# ==========================================
@app.post("/api/migrate/single", summary="Trigger migration for a single phone")
async def migrate_single(req: SingleMigrateRequest):
    if migration_manager.is_running:
        raise HTTPException(status_code=400, detail="A migration job is already running.")

    # Validate that session is authorized and active
    detailed = pool.get_detailed_sessions()
    target_info = next(
        (s for s in detailed if s.get("phone") in (req.phone, req.phone.lstrip("+"), f"+{req.phone.lstrip('+')}") or s.get("session_name") == req.phone),
        None
    )
    if target_info and not target_info.get("is_authorized"):
        raise HTTPException(
            status_code=400,
            detail=f"سشن {req.phone} غیرمجاز، دی‌اکتیو یا دارای مشکل است و امکان ساخت سشن از آن وجود ندارد."
        )

    effective_proxy = req.proxy or proxy_service.get_default_proxy()
    pwd = req.password
    if not pwd:
        stored = account_service.get_all_passwords()
        for k in [req.phone, req.phone.lstrip("+"), f"+{req.phone.lstrip('+')}"]:
            if k in stored:
                item = stored[k]
                pwd = item.get("password") if isinstance(item, dict) else item
                if pwd:
                    break

    passwords = {req.phone: pwd} if pwd else {}
    started = await migration_manager.start_batch_migration(
        [req.phone],
        passwords,
        delay=0.0,
        proxy=effective_proxy
    )
    if not started:
        raise HTTPException(status_code=400, detail="Failed to start migration job.")
    return {"status": "started", "phone": req.phone, "proxy_used": effective_proxy}


@app.post("/api/migrate/batch", summary="Trigger migration for all source sessions")
async def migrate_batch(req: BatchMigrateRequest):
    if migration_manager.is_running:
        raise HTTPException(status_code=400, detail="A migration job is already running.")

    detailed_sessions = pool.get_detailed_sessions()
    active_phones = [
        s["phone"] for s in detailed_sessions
        if s.get("is_authorized") and s.get("status") == "active" and s.get("phone") not in ("N/A", "Deactivated", "Error", "Unknown")
    ]
    faulty_count = len([s for s in detailed_sessions if not s.get("is_authorized") or s.get("status") != "active"])

    if not active_phones:
        raise HTTPException(status_code=400, detail="هیچ سشن سالم و فعالی برای ساخت سشن یافت نشد.")

    effective_proxy = req.proxy or proxy_service.get_default_proxy()

    # Pass 2FA passwords from account service to migration worker
    passwords_dict = {}
    stored = account_service.get_all_passwords()
    for ph, info in stored.items():
        if isinstance(info, dict) and "password" in info:
            passwords_dict[ph] = info["password"]
            passwords_dict[ph.lstrip("+")] = info["password"]
            passwords_dict[f"+{ph.lstrip('+')}"] = info["password"]
        elif isinstance(info, str):
            passwords_dict[ph] = info
            passwords_dict[ph.lstrip("+")] = info
            passwords_dict[f"+{ph.lstrip('+')}"] = info

    started = await migration_manager.start_batch_migration(
        active_phones,
        two_fa_passwords=passwords_dict,
        delay=req.delay,
        proxy=effective_proxy
    )
    if not started:
        raise HTTPException(status_code=400, detail="Failed to start migration job.")
    return {
        "status": "started",
        "total_accounts": len(active_phones),
        "skipped_faulty_count": faulty_count,
        "delay": req.delay,
        "proxy_used": effective_proxy
    }


@app.get("/api/migrate/status", summary="Get status of current/last migration job")
async def migrate_status():
    return migration_manager.get_status()


@app.post("/api/migrate/reset", summary="Force reset stuck migration job")
async def reset_migration():
    migration_manager.reset()
    return {"status": "reset"}


# ==========================================
# Proxy Management API
# ==========================================
@app.get("/api/proxies", summary="List configured proxies")
async def list_proxies():
    return proxy_service.get_all()


@app.post("/api/proxies", summary="Add a new proxy")
async def add_proxy(req: AddProxyRequest):
    return proxy_service.add_proxy(name=req.name, url=req.url, is_default=req.is_default)


@app.delete("/api/proxies/{proxy_id}", summary="Delete a proxy")
async def delete_proxy(proxy_id: str):
    if not proxy_service.delete_proxy(proxy_id):
        raise HTTPException(status_code=404, detail="Proxy not found")
    return {"status": "deleted", "id": proxy_id}


@app.post("/api/proxies/{proxy_id}/default", summary="Set proxy as default")
async def set_default_proxy(proxy_id: str):
    if not proxy_service.set_default(proxy_id):
        raise HTTPException(status_code=404, detail="Proxy not found")
    return {"status": "updated", "id": proxy_id}


@app.post("/api/proxies/test", summary="Test a proxy connection and get IP/latency")
async def test_proxy(req: TestProxyRequest):
    return await proxy_service.test_proxy(req.url)


# ==========================================
# Account Management API
# ==========================================
@app.post("/api/accounts/profile", summary="Update profile name and bio")
async def update_profile(req: UpdateProfileRequest):
    try:
        return await account_service.update_profile(
            identifier=req.identifier,
            first_name=req.first_name,
            last_name=req.last_name,
            about=req.about
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/accounts/avatar", summary="Upload profile avatar")
async def upload_avatar(
    identifier: str = Query(..., description="Phone or session name"),
    file: UploadFile = File(...)
):
    try:
        content = await file.read()
        return await account_service.upload_avatar(
            identifier=identifier,
            photo_bytes=content,
            filename=file.filename or "avatar.jpg"
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/accounts/2fa", summary="Set or update 2FA password")
async def set_2fa(req: Set2FARequest):
    try:
        return await account_service.set_2fa_password(
            identifier=req.identifier,
            new_password=req.new_password,
            current_password=req.current_password,
            hint=req.hint
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/accounts/2fa/batch-random", summary="Assign random 2FA passwords to accounts")
async def batch_random_2fa(req: BatchRandom2FARequest):
    try:
        results = await account_service.batch_set_random_2fa(
            identifiers=req.identifiers,
            length=req.length,
            delay=req.delay
        )
        return {
            "status": "completed",
            "total": len(results),
            "results": results
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/accounts/passwords", summary="List stored 2FA passwords")
async def list_passwords():
    return account_service.get_all_passwords()


@app.get("/api/accounts/passwords/export-csv", summary="Export passwords as CSV")
async def export_passwords_csv():
    csv_data = account_service.export_passwords_csv()
    filename = f"telegram_2fa_passwords_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
    return Response(
        content=csv_data,
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"}
    )


@app.post("/api/accounts/passwords/import-csv", summary="Import 2FA passwords from CSV")
async def import_passwords_csv(file: UploadFile = File(...)):
    try:
        content = await file.read()
        csv_text = content.decode("utf-8-sig", errors="ignore")
        count = account_service.import_passwords_csv(csv_text)
        return {
            "status": "success",
            "imported_count": count,
            "total_passwords": len(account_service.get_all_passwords())
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))



@app.post("/api/accounts/batch-names", summary="Assign random names to accounts")
async def batch_names(req: BatchNamesRequest):
    try:
        results = await account_service.batch_set_random_names(
            locale=req.locale,
            identifiers=req.identifiers,
            delay=req.delay
        )
        return {
            "status": "completed",
            "total": len(results),
            "results": results
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/accounts/terminate-sessions", summary="Terminate other sessions")
async def terminate_other_sessions(req: AccountActionRequest):
    try:
        return await account_service.terminate_other_sessions(req.identifier)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/accounts/check-spam", summary="Check account restriction via @SpamBot")
async def check_spambot(req: AccountActionRequest):
    try:
        return await account_service.check_spambot(req.identifier)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

