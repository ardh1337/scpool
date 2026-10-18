"""Endpoint administrasi (khusus admin): akun pengguna, modem pool, persetujuan klien, dan pengaturan sistem."""

import re
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.auth.deps import require_admin
from app.auth.security import generate_agent_token, hash_password, password_problem
from app.core.config import USERNAME_PATTERN, VALID_ROLES, settings
from app.database.connection import get_db
from app.database.models import ModemPool, SystemSetting, UserAccount, UserSession
from app.services.accounts import (
    approve_account, create_operator_account, delete_account, is_admin_account,
)
from app.services.agent_gateway import agent_gateway
from app.services.settings import set_setting
from app.services.telegram_service import send_telegram_message

router = APIRouter()
MIN_PASSWORD = settings.password_min_length
_DOMAIN_PATTERN = re.compile(r'^https?://[A-Za-z0-9.-]{1,253}(:\d{1,5})?\Z')
SECRET_SETTING_KEYS = {"telegram_bot_token"}


def _get_user_or_404(db: Session, user_id: int) -> UserAccount:
    target = db.query(UserAccount).filter(UserAccount.id == user_id).first()
    if not target:
        raise HTTPException(404, "Pengguna tidak ditemukan.")
    return target


class UserCreateRequest(BaseModel):
    username: str = Field(max_length=50)
    password: str = Field(max_length=256)
    role: str = "operator"


class UserUpdateRequest(BaseModel):
    password: Optional[str] = Field(default=None, max_length=256)
    role: Optional[str] = None
    is_active: Optional[bool] = None


@router.get("/admin/users")
def list_admin_users(user: UserAccount = Depends(require_admin), db: Session = Depends(get_db)):
    users = db.query(UserAccount).order_by(UserAccount.id).all()
    unit_counts = dict(db.query(ModemPool.owner_id, func.count(ModemPool.id)).group_by(ModemPool.owner_id).all())
    return [
        {
            "id": u.id, "username": u.username, "role": u.role,
            "unit_count": unit_counts.get(u.id, 0),
            "agent_token": u.agent_token, "is_active": u.is_active,
            "agent_online": agent_gateway.is_agent_online(u.id),
            "created_at": u.created_at.isoformat() if u.created_at else None,
            "updated_at": u.updated_at.isoformat() if u.updated_at else None,
        }
        for u in users
    ]


@router.post("/admin/users")
def create_admin_user(payload: UserCreateRequest, user: UserAccount = Depends(require_admin),
                      db: Session = Depends(get_db)):
    uname = payload.username.strip().lower()
    if not USERNAME_PATTERN.match(uname):
        raise HTTPException(400, "Username harus 3-20 karakter, hanya huruf kecil, angka, dan garis bawah (_).")
    problem = password_problem(payload.password, uname)
    if problem:
        raise HTTPException(400, problem)
    if payload.role not in VALID_ROLES:
        raise HTTPException(400, f"Role harus salah satu dari: {', '.join(VALID_ROLES)}.")
    if db.query(UserAccount).filter(UserAccount.username == uname).first():
        raise HTTPException(400, f"Username '{uname}' sudah digunakan.")

    try:
        new_user = create_operator_account(db, uname, payload.password, payload.role, is_active=True)
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(400, f"Username '{uname}' sudah digunakan.")
    db.refresh(new_user)
    return {"success": True, "message": f"Pengguna {uname} berhasil dibuat.", "user_id": new_user.id,
            "agent_token": new_user.agent_token}


@router.put("/admin/users/{user_id}")
def update_admin_user(user_id: int, payload: UserUpdateRequest, user: UserAccount = Depends(require_admin),
                      db: Session = Depends(get_db)):
    target = _get_user_or_404(db, user_id)

    if payload.password is not None and payload.password.strip():
        problem = password_problem(payload.password, target.username)
        if problem:
            raise HTTPException(400, problem)
        target.password = hash_password(payload.password.strip())
        db.query(UserSession).filter(UserSession.user_id == target.id).delete(synchronize_session=False)

    if payload.role is not None:
        if payload.role not in VALID_ROLES:
            raise HTTPException(400, f"Role harus salah satu dari: {', '.join(VALID_ROLES)}.")
        if target.id == user.id and payload.role not in ("superadmin", "admin"):
            raise HTTPException(400, "Anda tidak dapat menurunkan peran akun Anda sendiri.")
        target.role = payload.role

    if payload.is_active is not None:
        if not payload.is_active and is_admin_account(target):
            raise HTTPException(400, "Akun Administrator tidak boleh dinonaktifkan.")
        target.is_active = payload.is_active
        if not payload.is_active:
            db.query(UserSession).filter(UserSession.user_id == target.id).delete(synchronize_session=False)

    target.updated_at = datetime.now(timezone.utc)
    db.commit()
    return {"success": True, "message": "Data pengguna berhasil diperbarui."}


@router.delete("/admin/users/{user_id}")
def delete_admin_user(user_id: int, user: UserAccount = Depends(require_admin), db: Session = Depends(get_db)):
    target = _get_user_or_404(db, user_id)
    if is_admin_account(target):
        raise HTTPException(400, "Akun Administrator tidak boleh dihapus demi keamanan sistem.")
    if target.id == user.id:
        raise HTTPException(400, "Anda tidak dapat menghapus akun Anda sendiri yang sedang digunakan untuk login.")

    username = target.username
    delete_account(db, target, protected_pool_id=settings.local_pool_id)
    return {"success": True, "message": f"Pengguna {username} berhasil dihapus."}


@router.post("/admin/users/{user_id}/token")
def regenerate_admin_user_token(user_id: int, user: UserAccount = Depends(require_admin),
                                db: Session = Depends(get_db)):
    target = _get_user_or_404(db, user_id)
    target.agent_token = generate_agent_token()
    db.commit()
    return {"success": True, "agent_token": target.agent_token}


@router.get("/admin/pools")
def list_admin_pools(user: UserAccount = Depends(require_admin), db: Session = Depends(get_db)):
    pools = db.query(ModemPool).order_by(ModemPool.id).all()
    owners = dict(db.query(UserAccount.id, UserAccount.username).all())
    return [
        {"id": p.id, "name": p.name, "port_count": p.port_count, "description": p.description,
         "owner": owners.get(p.owner_id), "slot_count": len(p.slots),
         "agent_online": bool(p.owner_id and agent_gateway.is_agent_online(p.owner_id))}
        for p in pools
    ]


@router.post("/admin/users/{user_id}/approve")
def approve_user(user_id: int, user: UserAccount = Depends(require_admin), db: Session = Depends(get_db)):
    target = _get_user_or_404(db, user_id)
    approve_account(db, target)
    try:
        send_telegram_message(
            f"✅ *Pendaftaran Disetujui!*\nAkun: `{target.username}`\nOleh: `{user.username}`",
            db=db)
    except Exception:
        pass
    return {"success": True, "message": f"Akun {target.username} berhasil disetujui dan diaktifkan."}


@router.post("/admin/users/{user_id}/reject")
def reject_user(user_id: int, user: UserAccount = Depends(require_admin), db: Session = Depends(get_db)):
    target = _get_user_or_404(db, user_id)
    if is_admin_account(target):
        raise HTTPException(400, "Akun Administrator tidak boleh ditolak.")
    username = target.username
    delete_account(db, target, protected_pool_id=settings.local_pool_id)
    return {"success": True, "message": f"Pendaftaran akun {username} telah ditolak dan dibersihkan."}


class SettingsUpdateRequest(BaseModel):
    telegram_bot_token: Optional[str] = Field(default=None, max_length=200)
    telegram_chat_id: Optional[str] = Field(default=None, max_length=50)
    public_domain: Optional[str] = Field(default=None, max_length=200)
    registration_auto_approve: Optional[str] = Field(default=None, max_length=10)


def _mask_secret(value: str) -> str:
    if not value:
        return ""
    head, sep, tail = value.partition(":")
    return f"{head}:***{tail[-4:]}" if sep and len(tail) >= 4 else "***"


@router.get("/admin/settings")
def get_system_settings(user: UserAccount = Depends(require_admin), db: Session = Depends(get_db)):
    result = {}
    for row in db.query(SystemSetting).all():
        if row.key == "telegram_last_update_id":
            continue
        value = row.value or ""
        result[row.key] = _mask_secret(value) if row.key in SECRET_SETTING_KEYS else value
    return result


@router.post("/admin/settings")
def update_system_settings(payload: SettingsUpdateRequest, user: UserAccount = Depends(require_admin),
                           db: Session = Depends(get_db)):
    if payload.telegram_bot_token is not None and "***" not in payload.telegram_bot_token:
        set_setting("telegram_bot_token", payload.telegram_bot_token.strip(), "Token Bot Telegram", db=db)
    if payload.telegram_chat_id is not None:
        set_setting("telegram_chat_id", payload.telegram_chat_id.strip(), "Chat ID Admin Telegram", db=db)
    if payload.public_domain is not None:
        domain = payload.public_domain.strip().rstrip("/")
        if domain and not _DOMAIN_PATTERN.match(domain):
            raise HTTPException(400, "Domain publik harus berbentuk http(s)://host[:port] tanpa path atau karakter khusus.")
        set_setting("public_domain", domain, "Domain publik akses remote", db=db)
    if payload.registration_auto_approve is not None:
        value = payload.registration_auto_approve.strip().lower()
        if value not in ("true", "false"):
            raise HTTPException(400, "registration_auto_approve harus 'true' atau 'false'.")
        set_setting("registration_auto_approve", value, "Auto approve registrasi", db=db)
    return {"success": True, "message": "Pengaturan sistem berhasil disimpan."}


@router.post("/admin/telegram/test")
def test_telegram_alert(user: UserAccount = Depends(require_admin), db: Session = Depends(get_db)):
    ok, msg = send_telegram_message(
        f"🤖 *Uji Coba Notifikasi SCPool Berhasil!*\nWaktu: `{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}`", db=db)
    if not ok:
        raise HTTPException(400, msg)
    return {"success": True, "message": msg}
