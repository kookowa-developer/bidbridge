from __future__ import annotations

import os

os.environ["BIDBRIDGE_DATABASE_URL"] = "sqlite://"
os.environ["BIDBRIDGE_SECRET_KEY"] = "test-secret-key-0123456789abcdef0123456789"
os.environ["BIDBRIDGE_OCR_PROVIDER"] = "none"

import pytest
from fastapi.testclient import TestClient

from app.db import engine, init_db, reset_schema_flag
from app.models import Base
from app.security import limiter

PASSWORD = "S3cret-pass"


def register(client: TestClient, email: str = "owner@example.com") -> dict[str, str]:
    r = client.post("/api/auth/register", json={"email": email, "password": PASSWORD, "full_name": "Test User"})
    assert r.status_code == 201, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


@pytest.fixture()
def anon() -> TestClient:
    Base.metadata.drop_all(engine)
    init_db()
    reset_schema_flag()
    limiter._fails.clear()
    from app.main import app

    app.dependency_overrides.clear()
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


@pytest.fixture()
def client(anon: TestClient) -> TestClient:
    """A client already authenticated as a fresh user."""
    anon.headers.update(register(anon))
    return anon
