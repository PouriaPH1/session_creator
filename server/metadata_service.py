import datetime
import json
import logging
from pathlib import Path
from typing import Dict, List, Optional, Set

from config.settings import Settings

logger = logging.getLogger(__name__)


class MetadataService:
    """
    Manages custom categories, remarks, notes, and labels for both source and target sessions.
    Persists data in storage/source_sessions_metadata.json and storage/target_sessions_metadata.json.
    """

    def __init__(self, settings: Settings):
        self.settings = settings
        self.source_meta_file = self.settings.base_dir / "storage" / "source_sessions_metadata.json"
        self.target_meta_file = self.settings.base_dir / "storage" / "target_sessions_metadata.json"
        self.passwords_file = self.settings.base_dir / "storage" / "passwords.json"

    def _read_json(self, file_path: Path) -> dict:
        if file_path.exists():
            try:
                return json.loads(file_path.read_text(encoding="utf-8"))
            except Exception as e:
                logger.warning(f"Error reading JSON from {file_path}: {e}")
                return {}
        return {}

    def _write_json(self, file_path: Path, data: dict) -> None:
        try:
            file_path.parent.mkdir(parents=True, exist_ok=True)
            file_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception as e:
            logger.error(f"Error writing JSON to {file_path}: {e}")

    # ==========================================
    # Source Sessions Metadata
    # ==========================================
    def get_source_metadata(self, identifier: str) -> dict:
        """Retrieves category and remark for a source session with fallback to passwords.json note."""
        data = self._read_json(self.source_meta_file)
        clean = identifier.strip().replace(" ", "")
        candidates = [clean, clean.lstrip("+"), f"+{clean.lstrip('+')}"]

        for c in candidates:
            if c in data and isinstance(data[c], dict):
                cat = data[c].get("category", "")
                rem = data[c].get("remark", "")
                if cat or rem:
                    return {"category": cat, "remark": rem}

        # Fallback to note from passwords.json if present
        pwds = self._read_json(self.passwords_file)
        for c in candidates:
            if c in pwds:
                item = pwds[c]
                note = item.get("note", "") if isinstance(item, dict) else ""
                if note:
                    return {"category": "", "remark": note}

        return {"category": "", "remark": ""}

    def set_source_metadata(
        self,
        identifier: str,
        category: Optional[str] = None,
        remark: Optional[str] = None
    ) -> dict:
        """Updates category and remark for a single source session."""
        data = self._read_json(self.source_meta_file)
        clean = identifier.strip().replace(" ", "")
        clean_no_plus = clean.lstrip("+")
        key = clean if clean.startswith("+") else f"+{clean_no_plus}"

        existing = self.get_source_metadata(identifier)
        new_category = category.strip() if category is not None else existing.get("category", "")
        new_remark = remark.strip() if remark is not None else existing.get("remark", "")

        entry = {
            "category": new_category,
            "remark": new_remark,
            "updated_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        }
        data[key] = entry
        data[clean_no_plus] = entry
        self._write_json(self.source_meta_file, data)
        return entry

    def batch_set_source_metadata(
        self,
        identifiers: List[str],
        category: Optional[str] = None,
        remark: Optional[str] = None
    ) -> int:
        """Updates category and remark for multiple source sessions."""
        data = self._read_json(self.source_meta_file)
        now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        count = 0

        for ident in identifiers:
            clean = ident.strip().replace(" ", "")
            clean_no_plus = clean.lstrip("+")
            key = clean if clean.startswith("+") else f"+{clean_no_plus}"

            existing = self.get_source_metadata(ident)
            new_cat = category.strip() if category is not None else existing.get("category", "")
            new_rem = remark.strip() if remark is not None else existing.get("remark", "")

            entry = {
                "category": new_cat,
                "remark": new_rem,
                "updated_at": now_str
            }
            data[key] = entry
            data[clean_no_plus] = entry
            count += 1

        self._write_json(self.source_meta_file, data)
        return count

    # ==========================================
    # Target Sessions Metadata
    # ==========================================
    def get_target_metadata(self, filename: str) -> dict:
        data = self._read_json(self.target_meta_file)
        return data.get(filename, {})

    def set_target_metadata(
        self,
        filename: str,
        category: Optional[str] = None,
        remark: Optional[str] = None
    ) -> dict:
        """Updates category and remark for a created target session."""
        data = self._read_json(self.target_meta_file)
        entry = data.get(filename, {})
        if category is not None:
            entry["category"] = category.strip()
        if remark is not None:
            entry["remark"] = remark.strip()
        entry["updated_at"] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        data[filename] = entry
        self._write_json(self.target_meta_file, data)
        return entry

    def record_target_creation(
        self,
        filename: str,
        phone: str,
        created_via: str = "direct",
        proxy_name: str = "مستقیم (سرور اول)",
        proxy_ip: str = "آی‌پی سرور اول",
        proxy_url: Optional[str] = None,
        category: Optional[str] = None,
        remark: Optional[str] = None
    ) -> dict:
        data = self._read_json(self.target_meta_file)
        entry = {
            "phone": phone,
            "session_name": Path(filename).stem,
            "created_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "created_via": created_via,
            "proxy_name": proxy_name,
            "proxy_ip": proxy_ip,
            "proxy_url": proxy_url,
            "category": (category or "").strip(),
            "remark": (remark or "").strip()
        }
        data[filename] = entry
        self._write_json(self.target_meta_file, data)
        return entry

    # ==========================================
    # Distinct Categories for Filtering & Autocomplete
    # ==========================================
    def get_all_categories(self) -> List[str]:
        """Returns all distinct category names in use across source and target sessions."""
        categories: Set[str] = set()
        src_data = self._read_json(self.source_meta_file)
        for item in src_data.values():
            if isinstance(item, dict) and item.get("category"):
                val = item["category"].strip()
                if val:
                    categories.add(val)

        tgt_data = self._read_json(self.target_meta_file)
        for item in tgt_data.values():
            if isinstance(item, dict) and item.get("category"):
                val = item["category"].strip()
                if val:
                    categories.add(val)

        return sorted(list(categories))
