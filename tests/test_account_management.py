from fastapi.testclient import TestClient
from server.account_service import AccountManagementService
from server.main import app

client = TestClient(app)


def test_generate_secure_password():
    pwd1 = AccountManagementService.generate_secure_password(length=14)
    assert len(pwd1) == 14
    assert any(c.isupper() for c in pwd1)
    assert any(c.islower() for c in pwd1)
    assert any(c.isdigit() for c in pwd1)

    pwd2 = AccountManagementService.generate_secure_password(length=12)
    assert pwd1 != pwd2


def test_passwords_api():
    response = client.get("/api/accounts/passwords")
    assert response.status_code == 200
    assert isinstance(response.json(), dict)


def test_passwords_export_csv_api():
    response = client.get("/api/accounts/passwords/export-csv")
    assert response.status_code == 200
    assert "text/csv" in response.headers["content-type"]
    assert "Phone / Identifier" in response.text
