"""
Shared pytest fixtures for tests that need a database session.

Centralizes the engine + dependency override so test_api.py and test_ui.py
don't each create their own competing engines and overwrite each other's
app.dependency_overrides setting.
"""
import os

# Must be set before any app module is imported so that EncryptedString /
# EncryptedDate TypeDecorators can call _get_fernet() during ORM operations.
# Value: base64.urlsafe_b64encode(b"test_key_test_key_test_key_test!")
os.environ.setdefault("PHI_ENCRYPTION_KEY", "dGVzdF9rZXlfdGVzdF9rZXlfdGVzdF9rZXlfdGVzdCE=")

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app

# One shared in-memory SQLite database for all API/UI tests.
# StaticPool forces every SQLAlchemy operation through the same connection
# so tables created by the fixture are visible to TestClient's sessions.
_engine = create_engine(
    "sqlite:///:memory:",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
_SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=_engine)


def _override_get_db():
    db = _SessionLocal()
    try:
        yield db
    finally:
        db.close()


# Register the override once at import time.
app.dependency_overrides[get_db] = _override_get_db


@pytest.fixture(autouse=True)
def reset_db():
    """Create all tables before each test; drop them after. Fast with SQLite."""
    Base.metadata.create_all(bind=_engine)
    yield
    Base.metadata.drop_all(bind=_engine)


@pytest.fixture
def db():
    """Direct DB session for seeding data in tests."""
    session = _SessionLocal()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def client():
    """FastAPI TestClient wired to the shared in-memory database."""
    return TestClient(app)
