import pytest
from pathlib import Path
from fastapi.testclient import TestClient

from config.settings import Settings
from server.metadata_service import MetadataService
from server.main import app, metadata_service


@pytest.fixture
def temp_settings(tmp_path):
    storage_dir = tmp_path / "storage"
    storage_dir.mkdir(parents=True, exist_ok=True)
    source_dir = tmp_path / "sessions"
    source_dir.mkdir(parents=True, exist_ok=True)
    target_dir = tmp_path / "target_sessions"
    target_dir.mkdir(parents=True, exist_ok=True)

    return Settings(
        base_dir=tmp_path,
        source_sessions_dir=source_dir,
        target_sessions_dir=target_dir,
        api_id=12345,
        api_hash="testhash"
    )


def test_metadata_service_crud(temp_settings):
    svc = MetadataService(temp_settings)

    # 1. Initially empty
    meta = svc.get_source_metadata("+989123456789")
    assert meta["category"] == ""
    assert meta["remark"] == ""

    # 2. Set source metadata
    res = svc.set_source_metadata("+989123456789", category="VIP Accounts", remark="Bought Oct 2026")
    assert res["category"] == "VIP Accounts"
    assert res["remark"] == "Bought Oct 2026"

    # Lookup without plus
    meta_no_plus = svc.get_source_metadata("989123456789")
    assert meta_no_plus["category"] == "VIP Accounts"
    assert meta_no_plus["remark"] == "Bought Oct 2026"

    # 3. Batch set source metadata
    count = svc.batch_set_source_metadata(["+1234567890", "+1987654321"], category="USA Accounts", remark="Batch 1")
    assert count == 2

    # 4. Target metadata recording
    tgt = svc.record_target_creation(
        filename="989123456789.session",
        phone="+989123456789",
        created_via="proxy",
        proxy_name="Proxy-US",
        proxy_ip="1.2.3.4",
        category="Migrated VIP",
        remark="Done smoothly"
    )
    assert tgt["category"] == "Migrated VIP"
    assert tgt["remark"] == "Done smoothly"
    assert tgt["created_via"] == "proxy"

    # 5. Distinct categories
    categories = svc.get_all_categories()
    assert "VIP Accounts" in categories
    assert "USA Accounts" in categories
    assert "Migrated VIP" in categories


def test_fallback_to_passwords_notes(temp_settings):
    # Pre-populate passwords.json with a note
    passwords_file = temp_settings.base_dir / "storage" / "passwords.json"
    import json
    passwords_file.write_text(json.dumps({
        "+14155552671": {
            "password": "secret_pwd",
            "note": "Imported from archive_2026.zip"
        }
    }), encoding="utf-8")

    svc = MetadataService(temp_settings)
    meta = svc.get_source_metadata("+14155552671")
    assert meta["remark"] == "Imported from archive_2026.zip"
    assert meta["category"] == ""


def test_api_categories_and_metadata_endpoints():
    client = TestClient(app)

    # 1. Update source metadata
    res = client.post("/api/sessions/source/metadata", json={
        "identifier": "+19998887766",
        "category": "TestCategory",
        "remark": "TestRemark"
    })
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "updated"
    assert data["metadata"]["category"] == "TestCategory"
    assert data["metadata"]["remark"] == "TestRemark"

    # 2. Get distinct categories
    cat_res = client.get("/api/categories")
    assert cat_res.status_code == 200
    cats = cat_res.json()["categories"]
    assert "TestCategory" in cats

    # 3. Update target metadata
    tgt_res = client.post("/api/sessions/target/metadata", json={
        "filename": "test_target.session",
        "category": "TargetCategory",
        "remark": "TargetRemark"
    })
    assert tgt_res.status_code == 200
    tgt_data = tgt_res.json()
    assert tgt_data["status"] == "updated"
    assert tgt_data["metadata"]["category"] == "TargetCategory"
