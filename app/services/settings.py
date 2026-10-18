"""Akses konfigurasi dinamis (tabel system_settings) yang bisa diubah admin dari dashboard."""

from typing import Optional

from sqlalchemy.orm import Session

from app.core.config import settings as env_settings
from app.database.connection import SessionLocal
from app.database.models import SystemSetting


def get_setting(key: str, default: str = "", db: Optional[Session] = None) -> str:
    owns = db is None
    db = db or SessionLocal()
    try:
        row = db.query(SystemSetting).filter(SystemSetting.key == key).first()
        return row.value if (row and row.value is not None) else default
    finally:
        if owns:
            db.close()


def set_setting(key: str, value: str, description: Optional[str] = None, db: Optional[Session] = None) -> None:
    owns = db is None
    db = db or SessionLocal()
    try:
        row = db.query(SystemSetting).filter(SystemSetting.key == key).first()
        if not row:
            db.add(SystemSetting(key=key, value=value, description=description))
        else:
            row.value = value
            if description:
                row.description = description
        db.commit()
    finally:
        if owns:
            db.close()


def get_public_domain(db: Optional[Session] = None, fallback: str = "") -> str:
    """Domain publik: pengaturan dashboard > env SCPOOL_PUBLIC_DOMAIN > fallback (mis. base URL request)."""
    value = get_setting("public_domain", "", db=db).strip().rstrip("/")
    return value or env_settings.public_domain or fallback.rstrip("/")


def ws_url_from_domain(domain: str) -> str:
    if domain.startswith("https://"):
        return "wss://" + domain[len("https://"):] + "/ws/agent"
    if domain.startswith("http://"):
        return "ws://" + domain[len("http://"):] + "/ws/agent"
    return "wss://" + domain + "/ws/agent"
