import datetime
import io
import json
import logging
from contextlib import asynccontextmanager
from pathlib import Path
import re
from typing import List, Optional
import urllib.parse
import zipfile

from fastapi import FastAPI, File, Form, HTTPException, Query, Response, UploadFile, WebSocket, WebSocketDisconnect, status
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel

from config.settings import get_settings
from core.exceptions import OTPNotFoundError, SessionNotFoundError
from core.telethon_patch import apply_telethon_patches
from server.account_service import AccountManagementService
from server.chat_service import ChatService
from server.metadata_service import MetadataService
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
metadata_service = MetadataService(settings)
pool = TelethonSessionPool(settings, metadata_service)
otp_service = TelethonOTPService(pool)
migration_manager = MigrationJobManager(settings, otp_service, metadata_service)
account_service = AccountManagementService(pool, settings)
proxy_service = ProxyService(settings)
chat_service = ChatService(pool)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting Master Telegram Session Manager...")
    await pool.start()
    yield
    logger.info("Stopping Master Telegram Session Manager...")
    await migration_manager.stop()
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
    category: Optional[str] = None
    remark: Optional[str] = None


class BatchMigrateRequest(BaseModel):
    delay: float = 15.0
    proxy: Optional[str] = None
    skip_existing: bool = True
    category: Optional[str] = None
    remark: Optional[str] = None
    # Filters applied to SOURCE sessions before migration (based on their existing metadata)
    source_category: Optional[str] = None   # "__nocat__" for uncategorized
    source_remark: Optional[str] = None     # substring match (case-insensitive)


class UpdateSourceMetadataRequest(BaseModel):
    identifier: str
    category: Optional[str] = None
    remark: Optional[str] = None


class UpdateTargetMetadataRequest(BaseModel):
    filename: str
    category: Optional[str] = None
    remark: Optional[str] = None


class BatchUpdateSourceMetadataRequest(BaseModel):
    identifiers: Optional[List[str]] = None
    only_uncategorized: bool = False
    category: Optional[str] = None
    remark: Optional[str] = None


class BatchUpdateTargetMetadataRequest(BaseModel):
    filenames: Optional[List[str]] = None
    only_uncategorized: bool = False
    category: Optional[str] = None
    remark: Optional[str] = None


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


class SetStoredPasswordRequest(BaseModel):
    identifier: str
    password: str
    note: Optional[str] = ""


class Provide2FARequest(BaseModel):
    phone: str
    password: str
    save_to_storage: bool = True


class Skip2FARequest(BaseModel):
    phone: str


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
    sessions = pool.get_detailed_sessions()
    passwords = account_service.get_all_passwords()
    for s in sessions:
        phone = s.get("phone") or s.get("session_name") or ""
        clean_phone = phone.strip().replace(" ", "")
        pwd = None
        for k in [clean_phone, clean_phone.lstrip("+"), f"+{clean_phone.lstrip('+')}"]:
            if k in passwords:
                item = passwords[k]
                pwd = item.get("password") if isinstance(item, dict) else item
                if pwd:
                    break
        s["has_2fa"] = bool(pwd)
        s["saved_2fa"] = pwd or ""
    return sessions


@app.get("/api/categories", summary="Get distinct categories across sessions")
async def get_categories():
    return {
        "categories": metadata_service.get_all_categories()
    }


@app.post("/api/sessions/source/metadata", summary="Update category and remark for a source session")
async def update_source_metadata(req: UpdateSourceMetadataRequest):
    updated = metadata_service.set_source_metadata(
        identifier=req.identifier,
        category=req.category,
        remark=req.remark
    )
    return {"status": "updated", "identifier": req.identifier, "metadata": updated}


@app.post("/api/sessions/source/batch-metadata", summary="Batch update category and remark for source sessions")
async def batch_update_source_metadata(req: BatchUpdateSourceMetadataRequest):
    detailed = pool.get_detailed_sessions()
    target_ids = []

    if req.identifiers:
        target_ids = req.identifiers
    elif req.only_uncategorized:
        for s in detailed:
            cat = s.get("category", "")
            if not cat or cat.strip() == "":
                target_ids.append(s.get("phone") or s.get("session_name"))
    else:
        for s in detailed:
            target_ids.append(s.get("phone") or s.get("session_name"))

    updated_count = metadata_service.batch_set_source_metadata(
        identifiers=target_ids,
        category=req.category,
        remark=req.remark
    )
    return {
        "status": "success",
        "updated_count": updated_count,
        "total_targets": len(target_ids)
    }


@app.post("/api/sessions/upload", summary="Upload .session, .zip and .csv files")
async def upload_sessions(
    files: List[UploadFile] = File(...),
    category: Optional[str] = Form(None),
    remark: Optional[str] = Form(None)
):
    saved_count = 0
    csv_count = 0
    uploaded_identifiers = []
    for file in files:
        if file.filename.endswith(".session"):
            dest_path = settings.source_sessions_dir / file.filename
            content = await file.read()
            dest_path.write_bytes(content)
            saved_count += 1
            uploaded_identifiers.append(file.filename)
            uploaded_identifiers.append(Path(file.filename).stem)
        elif file.filename.endswith(".zip"):
            content = await file.read()
            try:
                with zipfile.ZipFile(io.BytesIO(content)) as z:
                    for member in z.namelist():
                        if member.endswith(".session") and not member.startswith("__MACOSX"):
                            s_name = Path(member).name
                            (settings.source_sessions_dir / s_name).write_bytes(z.read(member))
                            saved_count += 1
                            uploaded_identifiers.append(s_name)
                            uploaded_identifiers.append(Path(s_name).stem)
            except Exception as e:
                logger.warning(f"Error extracting zip archive {file.filename}: {e}")
        elif file.filename.endswith(".csv"):
            content = await file.read()
            csv_text = content.decode("utf-8-sig", errors="ignore")
            csv_count += account_service.import_passwords_csv(csv_text)

    # Save category and remark for newly uploaded sessions if provided
    if (category or remark) and uploaded_identifiers:
        metadata_service.batch_set_source_metadata(
            identifiers=uploaded_identifiers,
            category=category,
            remark=remark
        )

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

        # Category and remark: fallback to source session if not set on target
        category = meta.get("category", "")
        remark = meta.get("remark", "")
        if not category or not remark:
            src_meta = metadata_service.get_source_metadata(meta.get("phone", f.stem))
            if not category:
                category = src_meta.get("category", "")
            if not remark:
                remark = src_meta.get("remark", "")

        results.append({
            "filename": f.name,
            "phone": meta.get("phone", f.stem),
            "size_kb": round(stats.st_size / 1024, 2),
            "created_at": meta.get("created_at") or datetime.datetime.fromtimestamp(stats.st_mtime).strftime("%Y-%m-%d %H:%M:%S"),
            "created_via": created_via,
            "proxy_name": proxy_name,
            "proxy_ip": proxy_ip,
            "proxy_url": meta.get("proxy_url"),
            "category": category,
            "remark": remark
        })
    return sorted(results, key=lambda x: x["created_at"], reverse=True)


@app.post("/api/sessions/target/metadata", summary="Update category and remark for a target session")
async def update_target_metadata(req: UpdateTargetMetadataRequest):
    updated = metadata_service.set_target_metadata(
        filename=req.filename,
        category=req.category,
        remark=req.remark
    )
    return {"status": "updated", "filename": req.filename, "metadata": updated}


@app.post("/api/sessions/target/batch-metadata", summary="Batch update category and remark for target sessions")
async def batch_update_target_metadata(req: BatchUpdateTargetMetadataRequest):
    target_files = list(settings.target_sessions_dir.glob("*.session"))
    target_fns = []

    if req.filenames:
        target_fns = req.filenames
    elif req.only_uncategorized:
        for f in target_files:
            meta = metadata_service.get_target_metadata(f.name)
            cat = meta.get("category", "")
            if not cat or cat.strip() == "":
                target_fns.append(f.name)
    else:
        target_fns = [f.name for f in target_files]

    updated_count = metadata_service.batch_set_target_metadata(
        filenames=target_fns,
        category=req.category,
        remark=req.remark
    )
    return {
        "status": "success",
        "updated_count": updated_count,
        "total_targets": len(target_fns)
    }


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


@app.get("/api/download/target-all-zip", summary="Download target sessions in a zip archive with optional filters")
@app.get("/api/download/target-zip", summary="Download target sessions in a zip archive with optional filters")
async def download_all_target_zip(
    category: Optional[str] = Query(default=None, description="Filter by category (use '__nocat__' for uncategorized)"),
    remark: Optional[str] = Query(default=None, description="Filter by remark substring"),
    created_via: Optional[str] = Query(default=None, description="Filter by creation type: direct or proxy")
):
    target_files = list(settings.target_sessions_dir.glob("*.session"))
    if not target_files:
        raise HTTPException(status_code=404, detail="هیچ سشن مقصدی در سرور یافت نشد.")

    meta_file = settings.base_dir / "storage" / "target_sessions_metadata.json"
    metadata_map = {}
    if meta_file.exists():
        try:
            metadata_map = json.loads(meta_file.read_text(encoding="utf-8"))
        except Exception:
            metadata_map = {}

    cat_filter = category.strip() if category is not None and category.strip() != "" else None
    rem_filter = remark.strip().lower() if remark is not None and remark.strip() != "" else None
    via_filter = created_via.strip().lower() if created_via is not None and created_via.strip() != "" else None

    filtered_files = []
    for f in target_files:
        meta = metadata_map.get(f.name, {})
        # Extract category and remark with fallback to source session
        item_cat = (meta.get("category") or "").strip()
        item_rem = (meta.get("remark") or "").strip()
        if not item_cat or not item_rem:
            src_meta = metadata_service.get_source_metadata(meta.get("phone", f.stem))
            if not item_cat:
                item_cat = (src_meta.get("category") or "").strip()
            if not item_rem:
                item_rem = (src_meta.get("remark") or "").strip()

        item_via = (meta.get("created_via") or "direct").strip().lower()

        # Category filter check
        if cat_filter is not None:
            if cat_filter == "__nocat__":
                if item_cat != "":
                    continue
            else:
                if item_cat != cat_filter:
                    continue

        # Remark filter check (case-insensitive substring match)
        if rem_filter is not None:
            if rem_filter not in item_rem.lower():
                continue

        # Created via filter check
        if via_filter is not None:
            if item_via != via_filter:
                continue

        filtered_files.append(f)

    if not filtered_files:
        raise HTTPException(status_code=404, detail="هیچ سشنی مطابق با فیلترهای انتخابی یافت نشد.")

    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zip_file:
        for file_path in filtered_files:
            zip_file.write(file_path, arcname=file_path.name)

    zip_buffer.seek(0)

    # Build descriptive filename
    parts = ["telegram_sessions"]
    if cat_filter:
        if cat_filter == "__nocat__":
            parts.append("uncategorized")
        else:
            safe_cat = re.sub(r'[^\w\-]+', '_', cat_filter).strip("_")
            if safe_cat:
                parts.append(safe_cat)
    if rem_filter:
        safe_rem = re.sub(r'[^\w\-]+', '_', rem_filter).strip("_")
        if safe_rem:
            parts.append(safe_rem)
    if via_filter:
        parts.append(via_filter)

    parts.append(datetime.datetime.now().strftime("%Y%m%d_%H%M%S"))
    filename = "_".join(parts) + ".zip"
    ascii_filename = f"telegram_sessions_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.zip"
    encoded_filename = urllib.parse.quote(filename)

    return Response(
        content=zip_buffer.getvalue(),
        media_type="application/zip",
        headers={
            "Content-Disposition": f"attachment; filename=\"{ascii_filename}\"; filename*=UTF-8''{encoded_filename}"
        }
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
    if pwd:
        account_service.set_stored_password(req.phone, pwd, note="ثبت شده هنگام ساخت سشن تکی")
    else:
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
        proxy=effective_proxy,
        skip_existing=False,
        category=req.category,
        remark=req.remark
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

    # ── Filter active phones by source session metadata (category / remark) ─────
    src_cat_filter = req.source_category.strip() if req.source_category and req.source_category.strip() else None
    src_rem_filter = req.source_remark.strip().lower() if req.source_remark and req.source_remark.strip() else None

    if src_cat_filter or src_rem_filter:
        filtered_phones = []
        for ph in active_phones:
            src_meta = metadata_service.get_source_metadata(ph)
            item_cat = (src_meta.get("category") or "").strip()
            item_rem = (src_meta.get("remark") or "").strip().lower()

            # category filter
            if src_cat_filter is not None:
                if src_cat_filter == "__nocat__":
                    if item_cat != "":
                        continue
                else:
                    if item_cat != src_cat_filter:
                        continue

            # remark filter (case-insensitive substring)
            if src_rem_filter is not None:
                if src_rem_filter not in item_rem:
                    continue

            filtered_phones.append(ph)

        if not filtered_phones:
            raise HTTPException(
                status_code=400,
                detail="هیچ سشن سالم و فعالی با فیلترهای دسته‌بندی / ریمارک انتخاب‌شده یافت نشد."
            )
        active_phones = filtered_phones
        faulty_count = len(detailed_sessions) - len(active_phones)
    # ─────────────────────────────────────────────────────────────────────────────

    # Filter out already existing target sessions if skip_existing is True
    skipped_existing_count = 0
    if req.skip_existing:
        target_files = list(settings.target_sessions_dir.glob("*.session"))
        target_stems = {f.stem for f in target_files}
        meta_file = settings.base_dir / "storage" / "target_sessions_metadata.json"
        if meta_file.exists():
            try:
                import json
                meta_map = json.loads(meta_file.read_text(encoding="utf-8"))
                for item in meta_map.values():
                    if isinstance(item, dict) and "phone" in item:
                        p = item["phone"]
                        target_stems.add(p)
                        target_stems.add(p.lstrip("+"))
                        target_stems.add(f"+{p.lstrip('+')}")
            except Exception:
                pass

        new_active_phones = []
        for ph in active_phones:
            clean_p = ph.strip().replace(" ", "")
            p_no_plus = clean_p.lstrip("+")
            p_plus = f"+{p_no_plus}"
            if (
                clean_p in target_stems
                or p_no_plus in target_stems
                or p_plus in target_stems
                or (settings.target_sessions_dir / f"{p_no_plus}.session").exists()
                or (settings.target_sessions_dir / f"{p_plus}.session").exists()
            ):
                skipped_existing_count += 1
            else:
                new_active_phones.append(ph)

        if not new_active_phones:
            return {
                "status": "already_completed",
                "message": f"تمام {len(active_phones)} سشن سالم از قبل ساخته شده‌اند و سشن جدیدی برای ساخت وجود ندارد.",
                "total_accounts": 0,
                "skipped_existing_count": skipped_existing_count,
                "skipped_faulty_count": faulty_count
            }

        active_phones = new_active_phones

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
        proxy=effective_proxy,
        skip_existing=req.skip_existing,
        category=req.category,
        remark=req.remark
    )
    if not started:
        raise HTTPException(status_code=400, detail="Failed to start migration job.")
    return {
        "status": "started",
        "total_accounts": len(active_phones),
        "skipped_existing_count": skipped_existing_count,
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


@app.post("/api/migrate/provide-2fa", summary="Provide 2FA password in real-time during migration")
async def provide_migrate_2fa(req: Provide2FARequest):
    success = migration_manager.provide_2fa(phone=req.phone, password=req.password)
    if not success:
        raise HTTPException(
            status_code=400,
            detail="در حال حاضر هیچ فرآیندی منتظر دریافت رمز برای این شماره نیست."
        )
    if req.save_to_storage:
        account_service.set_stored_password(
            identifier=req.phone,
            password=req.password,
            note="ثبت خودکار هنگام ساخت سشن"
        )
    return {"status": "success", "phone": req.phone}


@app.post("/api/migrate/skip-2fa", summary="Skip waiting 2FA account during migration")
async def skip_migrate_2fa(req: Skip2FARequest):
    success = migration_manager.skip_2fa(phone=req.phone)
    if not success:
        raise HTTPException(
            status_code=400,
            detail="در حال حاضر هیچ فرآیندی منتظر دریافت رمز برای این شماره نیست."
        )
    return {"status": "skipped", "phone": req.phone}


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


@app.post("/api/accounts/passwords/set", summary="Set or update stored 2FA password in database")
async def set_stored_password(req: SetStoredPasswordRequest):
    return account_service.set_stored_password(
        identifier=req.identifier,
        password=req.password,
        note=req.note or "ثبت دستی از پنل"
    )


@app.delete("/api/accounts/passwords/{identifier}", summary="Delete stored 2FA password from database")
async def delete_stored_password(identifier: str):
    return account_service.delete_stored_password(identifier)



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


# ==========================================
# Chat Viewer API
# ==========================================

class SendMessageRequest(BaseModel):
    identifier: str
    peer: str
    text: str


@app.get("/api/chat/dialogs", summary="List dialogs (chats, groups, channels) for a session")
async def chat_dialogs(
    identifier: str = Query(..., description="Phone or session name"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    try:
        dialogs = await chat_service.get_dialogs(identifier, limit=limit, offset=offset)
        return {"dialogs": dialogs, "count": len(dialogs)}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/chat/messages", summary="Get message history for a peer")
async def chat_messages(
    identifier: str = Query(..., description="Phone or session name"),
    peer: str = Query(..., description="User ID, username, phone, or chat/channel ID"),
    limit: int = Query(50, ge=1, le=100),
    offset_id: int = Query(0, ge=0, description="Load messages older than this message ID"),
):
    try:
        messages, me_id = await chat_service.get_messages(
            identifier, peer, limit=limit, offset_id=offset_id
        )
        return {"messages": messages, "me_id": me_id}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/chat/peer-info", summary="Get info about a specific peer (user/group/channel)")
async def chat_peer_info(
    identifier: str = Query(...),
    peer: str = Query(...),
):
    try:
        info = await chat_service.get_peer_info(identifier, peer)
        return info
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/chat/send", summary="Send a message to a peer")
async def chat_send(req: SendMessageRequest):
    try:
        sent = await chat_service.send_message(req.identifier, req.peer, req.text)
        return {"status": "sent", "message": sent}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/chat/download", summary="Download media from a specific message")
async def chat_download_media(
    identifier: str = Query(..., description="Phone or session name"),
    peer: str = Query(..., description="Peer ID or username"),
    message_id: int = Query(..., description="Message ID containing the media"),
):
    try:
        data, filename, mime = await chat_service.download_media(identifier, peer, message_id)
        safe_filename = urllib.parse.quote(filename)
        return Response(
            content=data,
            media_type=mime,
            headers={
                "Content-Disposition": f"attachment; filename*=UTF-8''{safe_filename}",
                "Content-Length": str(len(data)),
            }
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.websocket("/ws/chat/{identifier}/{peer_id}")
async def chat_websocket(
    websocket: WebSocket,
    identifier: str,
    peer_id: str,
):
    """
    WebSocket endpoint for real-time message streaming.
    - identifier: phone or session name
    - peer_id: numeric peer ID (user_id / chat_id / channel_id)

    The client sends JSON pings: {"type": "ping"}
    The server pushes new messages as JSON: {"type": "new_message", "message": {...}}
    """
    await websocket.accept()
    logger.info(f"[WS] Chat opened: identifier={identifier} peer={peer_id}")

    queue: asyncio.Queue = chat_service.register_listener(identifier, peer_id)

    try:
        while True:
            # Wait for either a new message from Telegram OR a client message (ping/close)
            receive_task = asyncio.create_task(websocket.receive_text())
            queue_task = asyncio.create_task(queue.get())

            done, pending = await asyncio.wait(
                [receive_task, queue_task],
                return_when=asyncio.FIRST_COMPLETED,
            )

            # Cancel whichever didn't fire
            for t in pending:
                t.cancel()
                try:
                    await t
                except (asyncio.CancelledError, Exception):
                    pass

            for t in done:
                if t is receive_task:
                    try:
                        data = t.result()
                        payload = json.loads(data)
                        if payload.get("type") == "ping":
                            await websocket.send_json({"type": "pong"})
                    except (json.JSONDecodeError, WebSocketDisconnect):
                        return
                elif t is queue_task:
                    msg = t.result()
                    await websocket.send_json({"type": "new_message", "message": msg})

    except WebSocketDisconnect:
        logger.info(f"[WS] Chat closed: identifier={identifier} peer={peer_id}")
    except Exception as e:
        logger.error(f"[WS] Chat error: {e}")
        try:
            await websocket.close()
        except Exception:
            pass
    finally:
        chat_service.unregister_listener(identifier, peer_id)
