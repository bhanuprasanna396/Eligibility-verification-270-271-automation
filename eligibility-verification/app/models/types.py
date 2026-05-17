"""
Custom SQLAlchemy column types used across models.
"""
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


class JSONBCompatible(sa.TypeDecorator):
    """
    Uses PostgreSQL JSONB in production, falls back to JSON for SQLite
    (used in tests). The application-level behavior is identical.
    """
    impl = sa.JSON
    cache_ok = True

    def load_dialect_impl(self, dialect):
        if dialect.name == "postgresql":
            return dialect.type_descriptor(postgresql.JSONB())
        return dialect.type_descriptor(sa.JSON())
