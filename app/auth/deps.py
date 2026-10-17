"""
Dependensi Otentikasi & Otorisasi Pengguna (FastAPI Dependency)
Mendukung Bearer Token, Cookie Session, dan Token Query Parameter secara murni.
"""

from datetime import datetime, timezone
from typing import Optional
from fastapi import Request, Depends, HTTPException, status
from sqlalchemy.orm import Session
from app.database.connection import get_db
from app.database.models import UserAccount, UserSession


def get_current_user_optional(request: Request, db: Session = Depends(get_db)) -> Optional[UserAccount]:
    """Mengambil akun pengguna saat ini jika token tersedia dan valid, tanpa memaksa error 401."""
    token: Optional[str] = None

    # 1. Dari Header Authorization: Bearer <token>
    auth_header = request.headers.get("Authorization")
    if auth_header and auth_header.startswith("Bearer "):
        token = auth_header[7:].strip()

    # 2. Dari Cookie session_token
    if not token:
        token = request.cookies.get("session_token")

    # 3. Dari Query Param token
    if not token:
        token = request.query_params.get("token")

    if not token:
        return None

    # Cari sesi aktif di database
    sess = db.query(UserSession).filter(UserSession.token == token).first()
    if not sess:
        return None

    # Cek masa berlaku jika diatur
    now = datetime.now(timezone.utc)
    if sess.expires_at:
        expires = sess.expires_at.replace(tzinfo=timezone.utc) if sess.expires_at.tzinfo is None else sess.expires_at
        if expires < now:
            db.delete(sess)
            db.commit()
            return None

    user = sess.user
    if not user or not user.is_active:
        return None

    return user


def require_user(user: Optional[UserAccount] = Depends(get_current_user_optional)) -> UserAccount:
    """Wajibkan login aktif untuk endpoint yang memerlukan otentikasi."""
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Otentikasi diperlukan. Silakan login terlebih dahulu."
        )
    return user


def require_admin(user: UserAccount = Depends(require_user)) -> UserAccount:
    """Wajibkan peran Super Administrator."""
    if user.role not in ["superadmin", "admin"]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Akses ditolak. Fitur ini khusus Administrator."
        )
    return user
