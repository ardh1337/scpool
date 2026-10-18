"""Endpoint otentikasi: login, logout, registrasi mandiri klien, profil, dan pergantian kredensial."""

from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.auth.deps import get_current_user_optional, is_trusted_local_request, require_user, session_token
from app.auth.security import (
    auth_rate_limiter, burn_password_check, generate_token, get_client_ip, hash_password,
    hash_session_token, is_https_request, password_needs_rehash, password_problem, verify_password,
)
from app.core.config import ROLE_OPERATOR, USERNAME_PATTERN, settings
from app.database.connection import get_db
from app.database.models import UserAccount, UserSession
from app.services.accounts import create_operator_account
from app.services.settings import get_setting
from app.services.telegram_service import notify_new_registration

router = APIRouter()

MIN_PASSWORD = settings.password_min_length


def _user_payload(user: UserAccount) -> dict:
    return {
        "id": user.id,
        "username": user.username,
        "role": user.role,
        "pool_id": user.pool_id,
        "agent_token": user.agent_token,
    }


class LoginRequest(BaseModel):
    username: str = Field(max_length=50)
    password: str = Field(max_length=256)


@router.post("/auth/login")
def login(payload: LoginRequest, request: Request, response: Response, db: Session = Depends(get_db)):
    rate_key = f"login:{get_client_ip(request)}"
    if not is_trusted_local_request(request) and not auth_rate_limiter.is_allowed(rate_key, 10, 60):
        raise HTTPException(429, "Terlalu banyak percobaan login. Silakan tunggu 1 menit sebelum mencoba lagi.")

    uname = payload.username.strip().lower()
    user = db.query(UserAccount).filter(UserAccount.username == uname).first()
    if not user:
        burn_password_check(payload.password)  # samakan waktu respons agar username tidak bisa ditebak
        raise HTTPException(401, "Username atau password salah.")
    if not verify_password(payload.password, user.password):
        raise HTTPException(401, "Username atau password salah.")
    if not user.is_active:
        raise HTTPException(403, "Akun belum aktif atau dinonaktifkan oleh administrator.")

    auth_rate_limiter.reset(rate_key)
    if password_needs_rehash(user.password):
        user.password = hash_password(payload.password)

    db.query(UserSession).filter(
        UserSession.user_id == user.id, UserSession.expires_at < datetime.now(timezone.utc)
    ).delete(synchronize_session=False)  # bersihkan sesi kedaluwarsa milik user ini

    token = generate_token(32)
    max_age = settings.session_days * 86400
    db.add(UserSession(token=hash_session_token(token), user_id=user.id,
                       expires_at=datetime.now(timezone.utc) + timedelta(seconds=max_age)))
    db.commit()

    response.set_cookie("session_token", token, max_age=max_age, httponly=True, samesite="lax",
                        secure=is_https_request(request))
    return {"success": True, "token": token, "user": _user_payload(user)}


@router.post("/auth/logout")
def logout(request: Request, response: Response, db: Session = Depends(get_db)):
    token = request.cookies.get("session_token")
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        token = auth_header[7:].strip()
    if token and token != "guest":
        db.query(UserSession).filter(UserSession.token == hash_session_token(token)).delete(synchronize_session=False)
        db.commit()
    response.set_cookie("session_token", "guest", max_age=86400, httponly=True, samesite="lax",
                        secure=is_https_request(request))
    return {"success": True, "message": "Berhasil logout."}


class RegisterRequest(BaseModel):
    username: str = Field(max_length=50)
    password: str = Field(max_length=256)


@router.post("/auth/register")
def register_client(payload: RegisterRequest, request: Request, db: Session = Depends(get_db)):
    if not is_trusted_local_request(request) and not auth_rate_limiter.is_allowed(
            f"register:{get_client_ip(request)}", 5, 300):
        raise HTTPException(429, "Terlalu banyak percobaan pendaftaran. Silakan tunggu beberapa saat.")

    uname = payload.username.strip().lower()
    if not USERNAME_PATTERN.match(uname):
        raise HTTPException(400, "Username harus 3-20 karakter, hanya huruf kecil, angka, dan garis bawah (_).")
    problem = password_problem(payload.password, uname)
    if problem:
        raise HTTPException(400, problem)
    if db.query(UserAccount).filter(UserAccount.username == uname).first():
        raise HTTPException(400, f"Username '{uname}' sudah digunakan.")

    is_active = get_setting("registration_auto_approve", "false", db=db).strip().lower() == "true"

    try:
        user = create_operator_account(db, uname, payload.password, ROLE_OPERATOR, is_active)
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(400, f"Username '{uname}' sudah digunakan.")
    db.refresh(user)

    if not is_active:
        try:
            notify_new_registration(user, db=db)
        except Exception:
            pass  # kegagalan Telegram tidak boleh menggagalkan pendaftaran

    # Token agen hanya diberikan untuk akun yang sudah aktif; akun pending menerimanya setelah disetujui.
    base = f"/api/download/agent?token={user.agent_token}" if is_active else None
    return {
        "success": True,
        "is_active": is_active,
        "username": user.username,
        "agent_token": user.agent_token if is_active else None,
        "download_script": f"{base}&file_type=py" if base else None,
        "download_bat": f"{base}&file_type=bat" if base else None,
        "message": (
            f"Pendaftaran berhasil! Akun {uname} aktif dan siap digunakan."
            if is_active else
            f"Pendaftaran berhasil! Akun {uname} menunggu persetujuan Administrator."
        ),
    }


@router.get("/auth/me")
def current_profile(user: Optional[UserAccount] = Depends(get_current_user_optional)):
    if not user:
        return {"logged_in": False}
    return {"logged_in": True, **_user_payload(user)}


@router.get("/auth/credentials")
def get_auth_credentials(user: UserAccount = Depends(require_user)):
    """Ringkasan kredensial akun sendiri. Hash/kata sandi tidak pernah dikembalikan."""
    return {
        "username": user.username,
        "role": user.role,
        "updated_at": user.updated_at.isoformat() if user.updated_at else None,
    }


class CredentialsUpdate(BaseModel):
    username: Optional[str] = Field(default=None, max_length=50)
    password: str = Field(max_length=256)
    current_password: Optional[str] = Field(default=None, max_length=256)


@router.put("/auth/credentials")
def update_auth_credentials(payload: CredentialsUpdate, request: Request,
                            user: UserAccount = Depends(require_user), db: Session = Depends(get_db)):
    """Ganti password/username akun yang sedang login. Wajib menyertakan password lama (kecuali login lokal)."""
    new_pwd = payload.password.strip()
    problem = password_problem(new_pwd, (payload.username or user.username or "").strip().lower())
    if problem:
        raise HTTPException(400, problem)

    if not is_trusted_local_request(request):
        if not payload.current_password or not verify_password(payload.current_password, user.password):
            raise HTTPException(403, "Password saat ini salah.")

    new_username = (payload.username or "").strip().lower()
    if new_username and new_username != user.username:
        if not USERNAME_PATTERN.match(new_username):
            raise HTTPException(400, "Username harus 3-20 karakter, hanya huruf kecil, angka, dan garis bawah (_).")
        if db.query(UserAccount).filter(UserAccount.username == new_username).first():
            raise HTTPException(400, f"Username '{new_username}' sudah digunakan.")
        user.username = new_username

    user.password = hash_password(new_pwd)
    user.updated_at = datetime.now(timezone.utc)
    current = session_token(request) or ""
    db.query(UserSession).filter(UserSession.user_id == user.id, UserSession.token != hash_session_token(current)
                                 ).delete(synchronize_session=False)  # cabut sesi lain
    db.commit()
    return {"success": True, "message": "Password berhasil diperbarui.", "username": user.username}
