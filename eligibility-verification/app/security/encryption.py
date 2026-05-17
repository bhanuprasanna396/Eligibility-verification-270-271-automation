"""
Transparent AES encryption for PHI columns via SQLAlchemy TypeDecorators.

Fernet (AES-128-CBC + HMAC-SHA256) is used so that:
  - Each encryption call produces a unique ciphertext (random IV), so two
    identical values stored at different times look different on disk.
  - Ciphertext is authenticated; tampering is detected on decryption.

Set PHI_ENCRYPTION_KEY to a URL-safe base64-encoded 32-byte key:
    python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
"""
import os
from datetime import date

import sqlalchemy as sa
from cryptography.fernet import Fernet

_fernet: Fernet | None = None


def _get_fernet() -> Fernet:
    global _fernet
    if _fernet is None:
        # Check os.environ first (set by tests / explicit export), then fall
        # back to settings so pydantic-settings can load it from .env automatically.
        key = os.environ.get("PHI_ENCRYPTION_KEY", "")
        if not key:
            from app.config import settings  # late import — avoids circular dep
            key = settings.phi_encryption_key
        if not key:
            raise RuntimeError(
                "PHI_ENCRYPTION_KEY is not set. Add it to .env or export it. "
                "Generate one with: python -c \"from cryptography.fernet import Fernet; "
                "print(Fernet.generate_key().decode())\""
            )
        _fernet = Fernet(key.encode())
    return _fernet


class EncryptedString(sa.TypeDecorator):
    """Stores text as Fernet-encrypted ciphertext."""

    impl = sa.Text
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        return _get_fernet().encrypt(value.encode()).decode()

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        return _get_fernet().decrypt(value.encode()).decode()


class EncryptedDate(sa.TypeDecorator):
    """Stores date values as Fernet-encrypted ISO-format strings."""

    impl = sa.Text
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        iso = value.isoformat() if isinstance(value, date) else str(value)
        return _get_fernet().encrypt(iso.encode()).decode()

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        decrypted = _get_fernet().decrypt(value.encode()).decode()
        return date.fromisoformat(decrypted)
