from __future__ import annotations

from tests.conftest import PASSWORD, register


def test_auth_required_everywhere(anon):
    for method, url in [("get", "/api/companies"), ("get", "/api/tenders/1"), ("post", "/api/demo/seed"),
                        ("get", "/api/metrics"), ("get", "/api/auth/me"), ("post", "/api/telegram/link-code")]:
        assert getattr(anon, method)(url).status_code == 401, url
    assert anon.get("/api/health").status_code == 200
    assert anon.get("/api/auth/me", headers={"Authorization": "Bearer garbage"}).status_code == 401


def test_register_login_validation(anon):
    assert anon.post("/api/auth/register", json={"email": "bad", "password": PASSWORD}).status_code == 422
    assert anon.post("/api/auth/register", json={"email": "a@b.co", "password": "short1"}).status_code == 422
    assert anon.post("/api/auth/register", json={"email": "a@b.co", "password": "onlyletters"}).status_code == 422
    register(anon, "A@B.co")
    assert anon.post("/api/auth/register", json={"email": "a@b.co", "password": PASSWORD}).status_code == 409
    ok = anon.post("/api/auth/login", json={"email": " a@B.CO ", "password": PASSWORD})
    assert ok.status_code == 200
    me = anon.get("/api/auth/me", headers={"Authorization": f"Bearer {ok.json()['access_token']}"}).json()
    assert me["email"] == "a@b.co" and me["telegram_linked"] is False
    assert "password" not in str(me)


def test_login_throttle(anon):
    register(anon, "t@x.co")
    for _ in range(5):
        assert anon.post("/api/auth/login", json={"email": "t@x.co", "password": "wrong-pass1"}).status_code == 401
    assert anon.post("/api/auth/login", json={"email": "t@x.co", "password": PASSWORD}).status_code == 429


def test_registration_can_be_disabled(anon, monkeypatch):
    from app.config import get_settings
    monkeypatch.setattr(get_settings(), "allow_registration", False)
    assert anon.post("/api/auth/register", json={"email": "n@x.co", "password": PASSWORD}).status_code == 403


def test_expired_token_rejected(anon):
    import jwt
    from datetime import datetime, timedelta, timezone
    from app.config import get_settings
    register(anon, "e@x.co")
    old = jwt.encode({"sub": "1", "exp": datetime.now(timezone.utc) - timedelta(seconds=5)}, get_settings().secret_key, algorithm="HS256")
    assert anon.get("/api/auth/me", headers={"Authorization": f"Bearer {old}"}).status_code == 401
    forged = jwt.encode({"sub": "1", "exp": datetime.now(timezone.utc) + timedelta(hours=1)}, "other-secret-key-0123456789abcdef012", algorithm="HS256")
    assert anon.get("/api/auth/me", headers={"Authorization": f"Bearer {forged}"}).status_code == 401


def test_tenant_isolation(anon):
    a, b = register(anon, "a@x.co"), register(anon, "b@x.co")
    ids = anon.post("/api/demo/seed", headers=a).json()
    cid, tid = ids["company_id"], ids["tender_id"]
    for url in (f"/api/companies/{cid}", f"/api/companies/{cid}/alerts", f"/api/companies/{cid}/tenders", f"/api/tenders/{tid}",
                f"/api/tenders/{tid}/check", f"/api/tenders/{tid}/bid", f"/api/tenders/{tid}/generate/guarantee_letter"):
        assert anon.get(url, headers=b).status_code == 404, url
    assert anon.delete(f"/api/tenders/{tid}", headers=b).status_code == 404
    assert anon.post(f"/api/companies/{cid}/attributes", headers=b, json={"label": "xx", "value": 1}).status_code == 404
    assert anon.get("/api/companies", headers=b).json() == []
    assert anon.get(f"/api/tenders/{tid}", headers=a).status_code == 200
    doc = anon.get(f"/api/companies/{cid}", headers=a).json()["documents"][0]["id"]
    assert anon.delete(f"/api/documents/{doc}", headers=b).status_code == 404
    assert anon.get(f"/api/documents/{doc}/download", headers=b).status_code == 404


def test_roles_viewer_cannot_write_owner_manages_members(anon):
    owner, viewer = register(anon, "o@x.co"), register(anon, "v@x.co")
    cid = anon.post("/api/companies", headers=owner, json={"legal_name": "Alfa MChJ"}).json()["id"]
    assert anon.post(f"/api/companies/{cid}/members", headers=owner, json={"email": "ghost@x.co", "role": "viewer"}).status_code == 404
    assert anon.post(f"/api/companies/{cid}/members", headers=owner, json={"email": "v@x.co", "role": "owner"}).status_code == 422
    assert anon.post(f"/api/companies/{cid}/members", headers=owner, json={"email": "v@x.co", "role": "viewer"}).status_code == 201
    assert anon.get(f"/api/companies/{cid}", headers=viewer).status_code == 200
    assert anon.post(f"/api/companies/{cid}/attributes", headers=viewer, json={"label": "xx", "value": 1}).status_code == 403
    assert anon.post(f"/api/companies/{cid}/tenders", headers=viewer, data={"title": "t", "text": "kamida 3 yil"}).status_code == 403
    assert anon.post(f"/api/companies/{cid}/members", headers=viewer, json={"email": "o@x.co", "role": "lawyer"}).status_code == 403
    # promote to accountant: can write now, still cannot manage members
    anon.post(f"/api/companies/{cid}/members", headers=owner, json={"email": "v@x.co", "role": "accountant"})
    assert anon.post(f"/api/companies/{cid}/attributes", headers=viewer, json={"label": "xx", "value": 1}).status_code == 201
    assert anon.post(f"/api/companies/{cid}/members", headers=viewer, json={"email": "o@x.co", "role": "viewer"}).status_code == 403
    assert {m["role"] for m in anon.get(f"/api/companies/{cid}/members", headers=owner).json()} == {"owner", "accountant"}


def test_upload_size_limit_and_headers(client, monkeypatch):
    from app.config import get_settings
    monkeypatch.setattr(get_settings(), "max_upload_mb", 1)
    cid = client.post("/api/companies", json={"legal_name": "Alfa MChJ"}).json()["id"]
    big = b"x" * (1024 * 1024 + 10)
    assert client.post(f"/api/companies/{cid}/tenders", data={"title": "t"}, files={"file": ("a.txt", big)}).status_code == 413
    assert client.post(f"/api/companies/{cid}/documents", data={"kind": "license", "title": "t"}, files={"file": ("a.pdf", big)}).status_code == 413
    r = client.get("/api/companies")
    assert r.headers["cache-control"] == "no-store" and r.headers["x-frame-options"] == "DENY"
