from server.account_service import AccountManagementService



def test_generate_secure_password():
    pwd1 = AccountManagementService.generate_secure_password(length=14)
    assert len(pwd1) == 14
    assert any(c.isupper() for c in pwd1)
    assert any(c.islower() for c in pwd1)
    assert any(c.isdigit() for c in pwd1)

    pwd2 = AccountManagementService.generate_secure_password(length=12)
    assert pwd1 != pwd2


def test_passwords_api(client):
    response = client.get("/api/accounts/passwords")
    assert response.status_code == 404


def test_passwords_export_csv_api(client):
    response = client.get("/api/accounts/passwords/export-csv")
    assert response.status_code in (404, 405)


def test_passwords_import_csv_api(client):
    sample_csv = "Phone / Identifier,2FA Password,Note\n+989123456789,TestSecret2FA!,ManualTest\n"
    response = client.post(
        "/api/accounts/passwords/import-csv",
        files={"file": ("test.csv", sample_csv, "text/csv")}
    )
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "success"
    assert data["imported_count"] >= 1

    res2 = client.get("/api/accounts/password-status", params={"identifier": "+989123456789"})
    assert res2.json() == {"has_2fa": True}
    assert "TestSecret2FA!" not in res2.text

