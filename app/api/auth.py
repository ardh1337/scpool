"""
Endpoint API Otentikasi & Akun Pengguna
Menangani login, pendaftaran mandiri klien, profil pengguna saat ini, dan pembaruan kredensial.
"""

from datetime import datetime, timezone, timedelta
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Response, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database.connection import get_db
from app.database.models import UserAccount, UserSession, ModemPool, Slot, SlotStatus
from app.auth.security import (
    hash_password, verify_password, generate_token, generate_agent_token,
    is_https_request, get_client_ip, auth_rate_limiter
)
from app.auth.deps import (
    get_current_user_optional, require_admin, is_trusted_local_request
)
from app.services.telegram_service import (
    notify_new_registration, get_setting
)

router = APIRouter()


class CredentialsUpdate(BaseModel):
    username: Optional[str] = None
    password: str
    current_password: Optional[str] = None


@router.get("/auth/credentials")
def get_auth_credentials(
    user: UserAccount = Depends(require_admin),
    db: Session = Depends(get_db)
):
    """Mengambil informasi akun admin saat ini (khusus Superadmin terotentikasi)."""
    is_default = verify_password("admin123", user.password)
    return {
        "username": user.username,
        "password": "admin123 (Default)" if is_default else "•••••••• (Tersimpan Aman / PBKDF2)",
        "role": user.role,
        "is_default_password": is_default,
        "updated_at": user.updated_at.isoformat() if user.updated_at else None
    }


@router.put("/auth/credentials")
def update_auth_credentials(
    payload: CredentialsUpdate,
    user: UserAccount = Depends(require_admin),
    db: Session = Depends(get_db)
):
    """Memperbarui password atau username admin (khusus Superadmin terotentikasi)."""
    new_pwd = payload.password.strip()
    if len(new_pwd) < 4:
        raise HTTPException(status_code=400, detail="Password baru minimal 4 karakter.")

    if payload.username and payload.username.strip():
        user.username = payload.username.strip()

    user.password = hash_password(new_pwd)
    user.updated_at = datetime.now(timezone.utc)
    db.commit()

    return {
        "success": True,
        "message": "Password berhasil diperbarui.",
        "username": user.username,
        "password": user.password,
        "is_default_password": verify_password("admin123", user.password)
    }


class LoginRequest(BaseModel):
    username: str
    password: str


@router.post("/auth/login")
def login(
    payload: LoginRequest,
    request: Request,
    response: Response,
    db: Session = Depends(get_db)
):
    """Otentikasi login pengguna dengan mitigasi brute force dan cookie aman."""
    client_ip = get_client_ip(request)
    if not is_trusted_local_request(request):
        if not auth_rate_limiter.is_allowed(f"login:{client_ip}", max_attempts=10, window_seconds=60):
            raise HTTPException(
                status_code=429,
                detail="Terlalu banyak percobaan login. Silakan tunggu 1 menit sebelum mencoba lagi."
            )

    uname = payload.username.strip().lower()
    user = db.query(UserAccount).filter(UserAccount.username == uname).first()
    if not user or not verify_password(payload.password, user.password):
        raise HTTPException(status_code=401, detail="Username atau password salah.")

    if not user.is_active:
        raise HTTPException(status_code=403, detail="Akun dinonaktifkan oleh administrator.")

    auth_rate_limiter.reset(f"login:{client_ip}")

    # Otomatis tingkatkan password plaintext legacy ke PBKDF2 hash
    if not user.password.startswith("pbkdf2$"):
        user.password = hash_password(payload.password)
        db.commit()

    token = generate_token(32)
    session = UserSession(
        token=token,
        user_id=user.id,
        expires_at=datetime.now(timezone.utc) + timedelta(days=7)
    )
    db.add(session)
    db.commit()

    is_sec = is_https_request(request)
    response.set_cookie(
        key="session_token",
        value=token,
        max_age=7 * 86400,
        httponly=True,
        samesite="lax",
        secure=is_sec
    )

    return {
        "success": True,
        "token": token,
        "user": {
            "id": user.id,
            "username": user.username,
            "role": user.role,
            "pool_id": user.pool_id,
            "agent_token": user.agent_token,
            "is_default_password": verify_password("admin123", user.password)
        }
    }


class RegisterRequest(BaseModel):
    username: str
    password: str
    pool_name: Optional[str] = None
    port_count: int = 16


@router.post("/auth/register")
def register_client(
    payload: RegisterRequest,
    request: Request,
    db: Session = Depends(get_db)
):
    """Pendaftaran mandiri akun klien baru dengan proteksi rate limit dan verifikasi Telegram / Admin."""
    client_ip = get_client_ip(request)
    if not is_trusted_local_request(request):
        if not auth_rate_limiter.is_allowed(f"register:{client_ip}", max_attempts=5, window_seconds=300):
            raise HTTPException(
                status_code=429,
                detail="Terlalu banyak percobaan pendaftaran. Silakan tunggu beberapa saat."
            )

    import re
    uname = payload.username.strip().lower()
    if not re.match(r'^[a-z0-9_]{3,20}$', uname):
        raise HTTPException(
            status_code=400,
            detail="Username harus 3-20 karakter, hanya huruf kecil, angka, dan garis bawah (_)."
        )
    if len(payload.password) < 4:
        raise HTTPException(status_code=400, detail="Password minimal 4 karakter.")

    if db.query(UserAccount).filter(UserAccount.username == uname).first():
        raise HTTPException(status_code=400, detail=f"Username '{uname}' sudah digunakan.")

    port_count = payload.port_count if payload.port_count in [8, 16, 24, 32, 64] else 16
    pool_id = f"pool_{uname}"
    existing_pool = db.query(ModemPool).filter(ModemPool.id == pool_id).first()
    if not existing_pool:
        pool_name = payload.pool_name.strip() if payload.pool_name else f"Modem Pool {uname.capitalize()}"
        new_pool = ModemPool(
            id=pool_id,
            name=pool_name,
            port_count=port_count,
            description=f"Modem Pool milik {uname}"
        )
        db.add(new_pool)
        db.flush()

        for idx in range(1, port_count + 1):
            db.add(Slot(pool_id=pool_id, slot_index=idx, status=SlotStatus.EMPTY))
    else:
        new_pool = existing_pool

    auto_approve_setting = get_setting("registration_auto_approve", "false", db=db).strip().lower()
    is_active = (auto_approve_setting == "true")

    new_user = UserAccount(
        username=uname,
        password=hash_password(payload.password),
        role="operator",
        pool_id=pool_id,
        agent_token=generate_agent_token(),
        is_active=is_active
    )
    db.add(new_user)
    db.commit()
    db.refresh(new_user)

    # Kirim notifikasi Telegram ke Admin jika akun menunggu persetujuan
    if not is_active:
        try:
            notify_new_registration(new_user, new_pool, db=db)
        except Exception:
            pass

    return {
        "success": True,
        "is_active": is_active,
        "username": new_user.username,
        "agent_token": new_user.agent_token,
        "pool_name": new_pool.name,
        "port_count": new_pool.port_count,
        "download_script": f"/api/download/agent?file_type=script&token={new_user.agent_token}",
        "download_bat": f"/api/download/agent?file_type=bat&token={new_user.agent_token}",
        "message": (
            f"Pendaftaran berhasil! Akun {uname} aktif dan siap digunakan."
            if is_active else
            f"Pendaftaran berhasil! Akun {uname} telah terdaftar dan menunggu persetujuan Administrator via Telegram."
        )
    }


@router.get("/auth/me")
def get_current_user_profile(user: Optional[UserAccount] = Depends(get_current_user_optional)):
    """Mengembalikan informasi sesi pengguna yang sedang login."""
    if not user:
        return {"logged_in": False}
    return {
        "logged_in": True,
        "id": user.id,
        "username": user.username,
        "role": user.role,
        "pool_id": user.pool_id,
        "agent_token": user.agent_token,
        "is_default_password": verify_password("admin123", user.password)
    }


@router.post("/auth/logout")
def logout(request: Request, response: Response, db: Session = Depends(get_db)):
    """Mengakhiri sesi pengguna aktif."""
    token = request.cookies.get("session_token")
    auth_header = request.headers.get("Authorization")
    if auth_header and auth_header.startswith("Bearer "):
        token = auth_header[7:].strip()

    if token and token != "guest":
        sess = db.query(UserSession).filter(UserSession.token == token).first()
        if sess:
            db.delete(sess)
            db.commit()

    is_sec = is_https_request(request)
    response.set_cookie(
        key="session_token",
        value="guest",
        max_age=86400,
        httponly=True,
        samesite="lax",
        secure=is_sec
    )
    return {"success": True, "message": "Berhasil logout."}

