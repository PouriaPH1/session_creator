import os
import sys
import glob
import json
import csv
import zipfile
import shutil
from pathlib import Path
from datetime import datetime

# Set encoding for console output
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

BASE_DIR = Path(__file__).resolve().parent
SESSIONS_ZIP_DIR = BASE_DIR / "sessions"
EXTRACTED_DIR = BASE_DIR / "extracted_sessions"
SESSIONS_EXTRACTED_DIR = SESSIONS_ZIP_DIR / "extracted"
SOURCE_SESSIONS_DIR = BASE_DIR / "storage" / "source_sessions"
PASSWORDS_FILE = BASE_DIR / "storage" / "passwords.json"

EXTRACTED_DIR.mkdir(parents=True, exist_ok=True)
SESSIONS_EXTRACTED_DIR.mkdir(parents=True, exist_ok=True)
SOURCE_SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
(BASE_DIR / "storage").mkdir(parents=True, exist_ok=True)

# List of accounts data
accounts = []

zip_files = sorted(list(SESSIONS_ZIP_DIR.glob("*.zip")))
print(f"Found {len(zip_files)} zip archives in {SESSIONS_ZIP_DIR}:")
for z in zip_files:
    print(f"  - {z.name}")

now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

for zpath in zip_files:
    zip_name = zpath.name
    with zipfile.ZipFile(zpath, "r") as zf:
        namelist = zf.namelist()
        
        # Find all JSON metadata files
        json_entries = [n for n in namelist if n.startswith("data/") and n.endswith(".json")]
        
        for j_entry in json_entries:
            try:
                raw_json = zf.read(j_entry).decode("utf-8")
                data = json.loads(raw_json)
            except Exception as e:
                print(f"Error parsing JSON {j_entry} in {zip_name}: {e}")
                continue
            
            phone = data.get("phone", "").strip()
            if not phone:
                phone = Path(j_entry).stem
            
            clean_phone = phone.strip().replace(" ", "")
            raw_phone_digits = clean_phone.lstrip("+")
            
            # Extract 2FA Password
            password = data.get("password", "")
            
            # Cross-check with password.txt in tdata if available
            txt_matches = [
                n for n in namelist 
                if n.endswith("password.txt") and raw_phone_digits in n.replace("+", "")
            ]
            if txt_matches:
                try:
                    txt_pwd = zf.read(txt_matches[0]).decode("utf-8", errors="ignore").strip()
                    if txt_pwd and not password:
                        password = txt_pwd
                except Exception:
                    pass
            
            # Locate .session file inside zip
            session_candidates = [
                n for n in namelist 
                if n.endswith(".session") and raw_phone_digits in n.replace("+", "")
            ]
            # Prefer telethon/<phone>.session
            telethon_candidates = [n for n in session_candidates if n.startswith("telethon/")]
            session_entry = telethon_candidates[0] if telethon_candidates else (session_candidates[0] if session_candidates else None)
            
            session_filename = f"{clean_phone}.session"
            
            if session_entry:
                session_bytes = zf.read(session_entry)
                # Write to extracted_sessions
                (EXTRACTED_DIR / session_filename).write_bytes(session_bytes)
                # Write to sessions/extracted
                (SESSIONS_EXTRACTED_DIR / session_filename).write_bytes(session_bytes)
                # Write to storage/source_sessions (master server pool)
                (SOURCE_SESSIONS_DIR / session_filename).write_bytes(session_bytes)
            else:
                print(f"Warning: No .session found for {clean_phone} in {zip_name}")
            
            account_info = {
                "phone": clean_phone,
                "password_2fa": password,
                "first_name": data.get("first_name", ""),
                "last_name": data.get("last_name", ""),
                "user_id": data.get("user_id", ""),
                "country": data.get("alpha_2", ""),
                "device_model": data.get("device_model", ""),
                "app_version": data.get("app_version", ""),
                "system_version": data.get("system_version", ""),
                "status": data.get("status", ""),
                "session_file": session_filename,
                "archive": zip_name
            }
            accounts.append(account_info)

print(f"\nSuccessfully extracted {len(accounts)} accounts!")

# 1. Generate comprehensive CSV (sessions_info.csv)
csv_comprehensive_path1 = EXTRACTED_DIR / "sessions_info.csv"
csv_comprehensive_path2 = SESSIONS_EXTRACTED_DIR / "sessions_info.csv"
csv_comprehensive_path3 = SESSIONS_ZIP_DIR / "sessions_info.csv"

fieldnames_full = [
    "phone", "password_2fa", "first_name", "last_name", "user_id",
    "country", "device_model", "app_version", "system_version",
    "status", "session_file", "archive"
]

for out_path in [csv_comprehensive_path1, csv_comprehensive_path2, csv_comprehensive_path3]:
    with open(out_path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames_full)
        writer.writeheader()
        writer.writerows(accounts)

# 2. Generate standard panel 2FA Passwords CSV (passwords_2fa.csv)
csv_panel_path1 = EXTRACTED_DIR / "passwords_2fa.csv"
csv_panel_path2 = SESSIONS_EXTRACTED_DIR / "passwords_2fa.csv"
csv_panel_path3 = SESSIONS_ZIP_DIR / "passwords_2fa.csv"

fieldnames_panel = ["Phone / Identifier", "2FA Password", "Note", "Updated At"]

for out_path in [csv_panel_path1, csv_panel_path2, csv_panel_path3]:
    with open(out_path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow(fieldnames_panel)
        for acc in accounts:
            writer.writerow([
                acc["phone"],
                acc["password_2fa"],
                f"Imported from {acc['archive']}",
                now_str
            ])

# 3. Generate phones.txt for CLI/worker usage (phone:password)
txt_path1 = EXTRACTED_DIR / "phones.txt"
txt_path2 = SESSIONS_EXTRACTED_DIR / "phones.txt"
txt_path3 = SESSIONS_ZIP_DIR / "phones.txt"

for out_path in [txt_path1, txt_path2, txt_path3]:
    with open(out_path, "w", encoding="utf-8") as f:
        for acc in accounts:
            f.write(f"{acc['phone']}:{acc['password_2fa']}\n")

# 4. Update storage/passwords.json for web panel auto-integration
stored_passwords = {}
if PASSWORDS_FILE.exists():
    try:
        stored_passwords = json.loads(PASSWORDS_FILE.read_text(encoding="utf-8"))
    except Exception:
        stored_passwords = {}

for acc in accounts:
    stored_passwords[acc["phone"]] = {
        "password": acc["password_2fa"],
        "note": f"Imported from {acc['archive']}",
        "updated_at": now_str
    }
    # Also index without '+' for flexible matching
    raw_key = acc["phone"].lstrip("+")
    stored_passwords[raw_key] = {
        "password": acc["password_2fa"],
        "note": f"Imported from {acc['archive']}",
        "updated_at": now_str
    }

PASSWORDS_FILE.write_text(json.dumps(stored_passwords, ensure_ascii=False, indent=2), encoding="utf-8")
print(f"Updated {PASSWORDS_FILE} with {len(accounts)} 2FA passwords.")

print("\nSummary of files created:")
print(f"1. Extracted sessions directory: {EXTRACTED_DIR} ({len(list(EXTRACTED_DIR.glob('*.session')))} .session files)")
print(f"2. Sessions extracted directory: {SESSIONS_EXTRACTED_DIR}")
print(f"3. Storage source sessions: {SOURCE_SESSIONS_DIR} ({len(list(SOURCE_SESSIONS_DIR.glob('*.session')))} .session files)")
print(f"4. Comprehensive CSV: {csv_comprehensive_path1}")
print(f"5. Panel Passwords CSV: {csv_panel_path1}")
print(f"6. Worker phones.txt: {txt_path1}")
