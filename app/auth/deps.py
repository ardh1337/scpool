"""
Dependensi otentikasi & otorisasi FastAPI.

Sumber token sesi: header `Authorization: Bearer` atau cookie `session_token`. Token lewat query string sengaja
TIDAK didukung (bocor ke log akses, riwayat browser, dan header Referer).
"""

from datetime import datetime, timezone
from typing import Optional

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.core.config import ADMIN_ROLES, settings
from app.auth.security import hash_session_token
from app.database.connection import get_db
from app.database.models import UserAccount, UserSession
from app.services.units import owned_pool_ids

_PROXY_HEADERS = ("cf-connecting-ip", "cf-ray", "cf-visitor", "x-forwarded-for", "x-real-ip", "forwarded")
_LOCAL_HOSTS = {"localhost", "127.0.0.1", "[::1]", "testclient", "testserver"}
_LOCAL_PEERS = {"127.0.0.1", "::1", "testclient", "testserver"}


def _host_without_port(host_header: str) -> str:
    host = host_header.strip().lower()
    if host.startswith("["):  # IPv6 literal: [::1]:8000
        return host.split("]")[0] + "]"
    return host.rsplit(":", 1)[0] if ":" in host else host


def is_trusted_local_request(request: Request) -> bool:
    """True hanya untuk akses langsung dari mesin host (bukan lewat tunnel/proxy publik) dan hanya bila mode lokal diaktifkan."""
    if not settings.local_autologin:
        return False
    if any(h in request.headers for h in _PROXY_HEADERS):
        return False
    if _host_without_port(request.headers.get("host", "")) not in _LOCAL_HOSTS:
        return False
    peer = request.client.host if request.client else None
    return peer in _LOCAL_PEERS


def session_token(request: Request) -> Optional[str]:
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        return auth_header[7:].strip() or None
    return request.cookies.get("session_token")


def get_current_user_optional(request: Request, db: Session = Depends(get_db)) -> Optional[UserAccount]:
    """Akun saat ini bila token valid; None bila tidak ada (tanpa memaksa 401)."""
    token = session_token(request)

    if not token:
        # Pemilik mesin yang membuka dashboard langsung di localhost otomatis menjadi admin lokal.
        if settings.local_autologin and is_trusted_local_request(request):
            return db.query(UserAccount).filter(
                UserAccount.role.in_(ADMIN_ROLES), UserAccount.is_active.is_(True)
            ).order_by(UserAccount.id).first()
        return None

    if token == "guest":
        return None

    sess = db.query(UserSession).filter(UserSession.token == hash_session_token(token)).first()
    if not sess:
        return None

    if sess.expires_at:
        expires = sess.expires_at if sess.expires_at.tzinfo else sess.expires_at.replace(tzinfo=timezone.utc)
        if expires < datetime.now(timezone.utc):
            db.delete(sess)
            db.commit()
            return None

    user = sess.user
    return user if user and user.is_active else None


def require_user(user: Optional[UserAccount] = Depends(get_current_user_optional)) -> UserAccount:
    if not user:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Otentikasi diperlukan. Silakan login terlebih dahulu.")
    return user


def require_admin(user: UserAccount = Depends(require_user)) -> UserAccount:
    if user.role not in ADMIN_ROLES:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Akses ditolak. Fitur ini khusus Administrator.")
    return user


def is_admin(user: UserAccount) -> bool:
    return user.role in ADMIN_ROLES


def resolve_pool_id(db: Session, user: UserAccount, requested: Optional[str] = None) -> Optional[str]:
    """
    Modem pool yang dipakai request ini.

    Operator hanya boleh memakai unit miliknya: `requested` yang bukan miliknya ditolak 404 (tanpa membedakan
    "tidak ada" dan "milik orang lain"); tanpa `requested`, dipakai unit pertamanya. Mengembalikan None bila akun
    belum punya unit (agent belum pernah terhubung). Admin boleh memilih pool mana pun.
    """
    if is_admin(user):
        return requested or user.pool_id or settings.local_pool_id
    owned = owned_pool_ids(db, user.id)
    if requested:
        if requested not in owned:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Modem pool tidak ditemukan.")
        return requested
    return owned[0] if owned else None


def require_pool_id(db: Session, user: UserAccount, requested: Optional[str] = None) -> str:
    pool_id = resolve_pool_id(db, user, requested)
    if not pool_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND,
                            "Belum ada modem pool. Jalankan agent di komputer yang terhubung ke modem pool.")
    return pool_id
