"""
Konfigurasi terpusat SCPool.

Semua nilai yang sebelumnya tersebar sebagai literal (path Windows, domain, token, kata sandi bawaan)
dibaca dari variabel lingkungan `SCPOOL_*`. Nilai rahasia TIDAK PERNAH diberi default di kode.
Lihat `.env.example` untuk daftar lengkap.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Tuple

BASE_DIR = Path(__file__).resolve().parents[2]

ROLE_SUPERADMIN = "superadmin"
ROLE_ADMIN = "admin"
ROLE_OPERATOR = "operator"
ADMIN_ROLES = (ROLE_SUPERADMIN, ROLE_ADMIN)
VALID_ROLES = (ROLE_SUPERADMIN, ROLE_ADMIN, ROLE_OPERATOR)

USERNAME_PATTERN = re.compile(r"^[a-z0-9_]{3,20}\Z")
MAX_PORTS_PER_POOL = 64
MAX_UNITS_PER_ACCOUNT = 16
MAX_UNIT_NAME = 60


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


def _env_tuple(name: str, default: Tuple[str, ...]) -> Tuple[str, ...]:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return tuple(x.strip() for x in raw.split(",") if x.strip())


@dataclass(frozen=True)
class Settings:
    base_dir: Path = BASE_DIR
    data_dir: Path = field(default_factory=lambda: Path(os.environ.get("SCPOOL_DATA_DIR") or BASE_DIR / "data"))

    # Hardware lokal (serial port di mesin server). Default aktif hanya di Windows karena modem pool fisik
    # terhubung ke PC Windows; di VPS Linux modem datang lewat agent remote.
    local_hardware: bool = field(default_factory=lambda: _env_bool("SCPOOL_LOCAL_HARDWARE", os.name == "nt"))
    local_pool_id: str = field(default_factory=lambda: os.environ.get("SCPOOL_LOCAL_POOL_ID", "foxcom_32"))
    scan_interval: float = field(default_factory=lambda: _env_float("SCPOOL_SCAN_INTERVAL", 6.0))
    sms_poll_interval: float = field(default_factory=lambda: _env_float("SCPOOL_SMS_POLL_INTERVAL", 15.0))

    public_domain: str = field(default_factory=lambda: os.environ.get("SCPOOL_PUBLIC_DOMAIN", "").strip().rstrip("/"))
    extra_cors_origins: Tuple[str, ...] = field(default_factory=lambda: _env_tuple("SCPOOL_CORS_ORIGINS", ()))
    trusted_proxies: Tuple[str, ...] = field(
        default_factory=lambda: _env_tuple("SCPOOL_TRUSTED_PROXIES", ("127.0.0.1", "::1")))

    # Dashboard dibuka langsung di localhost (tanpa proxy) otomatis login sebagai admin. Matikan di server bersama.
    local_autologin: bool = field(default_factory=lambda: _env_bool("SCPOOL_LOCAL_AUTOLOGIN", True))
    session_days: int = field(default_factory=lambda: _env_int("SCPOOL_SESSION_DAYS", 7))
    password_min_length: int = 8

    # Bootstrap: dipakai hanya saat akun admin pertama dibuat.
    admin_password: Optional[str] = field(default_factory=lambda: os.environ.get("SCPOOL_ADMIN_PASSWORD") or None)
    telegram_bot_token: str = field(default_factory=lambda: os.environ.get("SCPOOL_TELEGRAM_BOT_TOKEN", "").strip())
    telegram_chat_id: str = field(default_factory=lambda: os.environ.get("SCPOOL_TELEGRAM_CHAT_ID", "").strip())

    log_level: str = field(default_factory=lambda: os.environ.get("SCPOOL_LOG_LEVEL", "INFO").upper())

    @property
    def db_path(self) -> Path:
        return self.data_dir / "sim_inventory.db"

    @property
    def cors_origin_regex(self) -> str:
        origins = [r"https?://(localhost|127\.0\.0\.1)(:\d+)?"]
        extra = ((self.public_domain,) if self.public_domain else ()) + self.extra_cors_origins
        origins += [re.escape(o.rstrip("/")) for o in extra]
        return "^(" + "|".join(origins) + ")$"


settings = Settings()
