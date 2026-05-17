from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, DeclarativeBase, Session

from app.config import settings


class Base(DeclarativeBase):
    pass


# Engine and session factory are created on first use, not at import time.
# This lets EDI builder/parser tests run without a database driver installed.
_engine = None
_SessionLocal = None


def get_engine():
    global _engine
    if _engine is None:
        _engine = create_engine(
            settings.database_url,
            pool_pre_ping=True,   # reconnects if connection dropped
            pool_size=10,         # max persistent connections
            max_overflow=20,      # extra connections allowed under load
            pool_recycle=3600,    # recycle connections every hour
        )
    return _engine


def _session_factory() -> sessionmaker:
    global _SessionLocal
    if _SessionLocal is None:
        _SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=get_engine())
    return _SessionLocal


def get_db() -> Session:
    """FastAPI dependency — yields a DB session and closes it when done."""
    db = _session_factory()()
    try:
        yield db
    finally:
        db.close()


def create_db_session() -> Session:
    """
    Returns a plain Session for use in Celery workers and scripts.
    Caller is responsible for calling db.close() when done.
    Use get_db() in FastAPI endpoints instead.
    """
    return _session_factory()()
