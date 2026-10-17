"""
Endpoint API Administrasi Sistem & Pengguna
Khusus untuk peran Superadministrator: manajemen akun operator, persetujuan klien, dan konfigurasi Telegram.
"""

from datetime import datetime, timezone
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database.connection import get_db
from app.database.models import UserAccount, UserSession, ModemPool, Slot, SlotStatus, SystemSetting
from app.auth.security import hash_password, generate_agent_token
from app.auth.deps import require_admin
from app.services.telegram_service import (
    send_telegram_message, set_setting
)

router = APIRouter()


class UserCreateRequest(BaseModel):
    username: str
    password: str
    role: str = "operator"
    pool_name: Optional[str] = None
    port_count: int = 32


class UserUpdateRequest(BaseModel):
    password: Optional[str] = None
    role: Optional[str] = None
    is_active: Optional[bool] = None
    pool_id: Optional[str] = None


@router.get("/admin/users")
def list_admin_users(
    user: UserAccount = Depends(require_admin),
    db: Session = Depends(get_db)
):
    """Mengembalikan daftar semua akun pengguna (khusus Superadmin)."""
    users = db.query(UserAccount).order_by(UserAccount.id).all()
    return [
        {
            "id": u.id,
            "username": u.username,
            "role": u.role,
            "pool_id": u.pool_id,
            "pool_name": u.pool.name if u.pool else (u.pool_id or "—"),
            "agent_token": u.agent_token,
            "is_active": u.is_active,
            "created_at": u.created_at.isoformat() if u.created_at else None,
            "updated_at": u.updated_at.isoformat() if u.updated_at else None
        }
        for u in users
    ]


@router.post("/admin/users")
def create_admin_user(
    payload: UserCreateRequest,
    user: UserAccount = Depends(require_admin),
    db: Session = Depends(get_db)
):
    """Membuat pengguna baru dan otomatis mengalokasikan Modem Pool baru."""
    uname = payload.username.strip().lower()
    if len(uname) < 3:
        raise HTTPException(status_code=400, detail="Username minimal 3 karakter.")
    if len(payload.password) < 4:
        raise HTTPException(status_code=400, detail="Password minimal 4 karakter.")

    if db.query(UserAccount).filter(UserAccount.username == uname).first():
        raise HTTPException(status_code=400, detail=f"Username '{uname}' sudah digunakan.")

    pool_id = f"pool_{uname}"
    existing_pool = db.query(ModemPool).filter(ModemPool.id == pool_id).first()
    if not existing_pool:
        pool_name = payload.pool_name.strip() if payload.pool_name else f"Modem Pool {uname.capitalize()}"
        new_pool = ModemPool(
            id=pool_id,
            name=pool_name,
            port_count=payload.port_count,
            description=f"Modem Pool milik {uname}"
        )
        db.add(new_pool)
        db.flush()

        for idx in range(1, payload.port_count + 1):
            db.add(Slot(pool_id=pool_id, slot_index=idx, status=SlotStatus.EMPTY))

    new_user = UserAccount(
        username=uname,
        password=hash_password(payload.password),
        role=payload.role,
        pool_id=pool_id,
        agent_token=generate_agent_token(),
        is_active=True
    )
    db.add(new_user)
    db.commit()
    db.refresh(new_user)

    return {
        "success": True,
        "message": f"Pengguna {uname} berhasil dibuat.",
        "user_id": new_user.id,
        "agent_token": new_user.agent_token,
        "pool_id": new_user.pool_id
    }


@router.put("/admin/users/{user_id}")
def update_admin_user(
    user_id: int,
    payload: UserUpdateRequest,
    user: UserAccount = Depends(require_admin),
    db: Session = Depends(get_db)
):
    """Memperbarui data pengguna, status aktif, atau password."""
    target = db.query(UserAccount).filter(UserAccount.id == user_id).first()
    if not target:
        raise HTTPException(status_code=404, detail="Pengguna tidak ditemukan.")

    if payload.password and len(payload.password.strip()) >= 4:
        target.password = hash_password(payload.password.strip())
    if payload.role:
        target.role = payload.role
    if payload.is_active is not None:
        if (target.role in ["superadmin", "admin"] or target.username.lower() == "admin") and not payload.is_active:
            raise HTTPException(status_code=400, detail="Akun Administrator tidak boleh dinonaktifkan.")
        target.is_active = payload.is_active
    if payload.pool_id:
        target.pool_id = payload.pool_id

    target.updated_at = datetime.now(timezone.utc)
    db.commit()

    return {"success": True, "message": "Data pengguna berhasil diperbarui."}


@router.delete("/admin/users/{user_id}")
def delete_admin_user(
    user_id: int,
    user: UserAccount = Depends(require_admin),
    db: Session = Depends(get_db)
):
    """Menghapus akun pengguna operator beserta pool pribadinya secara aman."""
    target = db.query(UserAccount).filter(UserAccount.id == user_id).first()
    if not target:
        raise HTTPException(status_code=404, detail="Pengguna tidak ditemukan.")

    # 1. Proteksi ketat: Akun Administrator tidak boleh dihapus
    if target.role in ["superadmin", "admin"] or target.username.lower() == "admin":
        raise HTTPException(
            status_code=400,
            detail="Akun Administrator tidak boleh dihapus demi keamanan sistem."
        )

    # 2. Proteksi diri: Tidak boleh menghapus akun yang sedang digunakan login
    if target.id == user.id:
        raise HTTPException(
            status_code=400,
            detail="Anda tidak dapat menghapus akun Anda sendiri yang sedang digunakan untuk login."
        )

    target_pool_id = target.pool_id

    # 3. Hapus sesi aktif user
    db.query(UserSession).filter(UserSession.user_id == target.id).delete()

    # 4. Hapus user
    db.delete(target)
    db.commit()

    # 5. Bersihkan Modem Pool dan Slot pribadi operator jika tidak digunakan oleh akun lain
    if target_pool_id and target_pool_id != "foxcom_32":
        other_users = db.query(UserAccount).filter(UserAccount.pool_id == target_pool_id).count()
        if other_users == 0:
            pool_obj = db.query(ModemPool).filter(ModemPool.id == target_pool_id).first()
            if pool_obj:
                db.delete(pool_obj)
                db.commit()

    return {"success": True, "message": f"Pengguna {target.username} berhasil dihapus."}


@router.post("/admin/users/{user_id}/token")
def regenerate_admin_user_token(
    user_id: int,
    user: UserAccount = Depends(require_admin),
    db: Session = Depends(get_db)
):
    """Membuat ulang Agent Token baru untuk pengguna tertentu."""
    target = db.query(UserAccount).filter(UserAccount.id == user_id).first()
    if not target:
        raise HTTPException(status_code=404, detail="Pengguna tidak ditemukan.")

    target.agent_token = generate_agent_token()
    db.commit()
    return {"success": True, "agent_token": target.agent_token}


@router.get("/admin/pools")
def list_admin_pools(
    user: UserAccount = Depends(require_admin),
    db: Session = Depends(get_db)
):
    """Mengembalikan daftar seluruh modem pool yang terdaftar."""
    pools = db.query(ModemPool).order_by(ModemPool.id).all()
    return [
        {
            "id": p.id,
            "name": p.name,
            "port_count": p.port_count,
            "description": p.description,
            "slot_count": len(p.slots)
        }
        for p in pools
    ]


@router.post("/admin/users/{user_id}/approve")
def approve_user(
    user_id: int,
    user: UserAccount = Depends(require_admin),
    db: Session = Depends(get_db)
):
    """Menyetujui pendaftaran akun pengguna yang berstatus pending."""
    target = db.query(UserAccount).filter(UserAccount.id == user_id).first()
    if not target:
        raise HTTPException(status_code=404, detail="Pengguna tidak ditemukan.")

    target.is_active = True
    target.updated_at = datetime.now(timezone.utc)
    db.commit()

    try:
        send_telegram_message(
            f"✅ *Pendaftaran Disetujui!*\nAkun: `{target.username}`\nPool: `{target.pool_id}`\nOleh: `{user.username}`",
            db=db
        )
    except Exception:
        pass

    return {"success": True, "message": f"Akun {target.username} berhasil disetujui dan diaktifkan."}


@router.post("/admin/users/{user_id}/reject")
def reject_user(
    user_id: int,
    user: UserAccount = Depends(require_admin),
    db: Session = Depends(get_db)
):
    target = db.query(UserAccount).filter(UserAccount.id == user_id).first()
    if not target:
        raise HTTPException(status_code=404, detail="Pengguna tidak ditemukan.")

    if target.role in ["superadmin", "admin"] or target.username.lower() == "admin":
        raise HTTPException(status_code=400, detail="Akun Administrator tidak boleh ditolak.")

    uname = target.username
    target_pool_id = target.pool_id

    # Hapus sesi jika ada
    db.query(UserSession).filter(UserSession.user_id == target.id).delete()

    # Hapus user pendaftaran
    db.delete(target)
    db.commit()

    # Bersihkan pool pribadi jika tidak digunakan oleh akun lain
    if target_pool_id and target_pool_id != "foxcom_32":
        other_users = db.query(UserAccount).filter(UserAccount.pool_id == target_pool_id).count()
        if other_users == 0:
            pool_obj = db.query(ModemPool).filter(ModemPool.id == target_pool_id).first()
            if pool_obj:
                db.delete(pool_obj)
                db.commit()

    return {"success": True, "message": f"Pendaftaran akun {uname} telah ditolak dan dibersihkan."}


class SettingsUpdateRequest(BaseModel):
    telegram_bot_token: Optional[str] = None
    telegram_chat_id: Optional[str] = None
    public_domain: Optional[str] = None
    registration_auto_approve: Optional[str] = None


@router.get("/admin/settings")
def get_system_settings(
    user: UserAccount = Depends(require_admin),
    db: Session = Depends(get_db)
):
    """Mengembalikan konfigurasi sistem untuk admin."""
    settings = db.query(SystemSetting).all()
    results = {}
    for s in settings:
        val = s.value or ""
        if s.key == "telegram_bot_token" and val:
            parts = val.split(":")
            if len(parts) == 2:
                val = f"{parts[0]}:***{parts[1][-4:] if len(parts[1]) >= 4 else '***'}"
            else:
                val = "***"
        results[s.key] = val
    return results


@router.post("/admin/settings")
def update_system_settings(
    payload: SettingsUpdateRequest,
    user: UserAccount = Depends(require_admin),
    db: Session = Depends(get_db)
):
    """Menyimpan pembaruan konfigurasi sistem."""
    if payload.telegram_bot_token is not None and not payload.telegram_bot_token.startswith("***"):
        set_setting("telegram_bot_token", payload.telegram_bot_token.strip(), "Token Bot Telegram", db=db)
    if payload.telegram_chat_id is not None:
        set_setting("telegram_chat_id", payload.telegram_chat_id.strip(), "Chat ID Admin Telegram", db=db)
    if payload.public_domain is not None:
        set_setting("public_domain", payload.public_domain.strip().rstrip("/"), "Domain publik akses remote", db=db)
    if payload.registration_auto_approve is not None:
        set_setting("registration_auto_approve", payload.registration_auto_approve.strip().lower(), "Auto approve registrasi", db=db)

    return {"success": True, "message": "Pengaturan sistem berhasil disimpan."}


@router.post("/admin/telegram/test")
def test_telegram_alert(
    user: UserAccount = Depends(require_admin),
    db: Session = Depends(get_db)
):
    """Menguji pengiriman notifikasi ke Telegram Admin."""
    time_now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    ok, msg = send_telegram_message(
        f"🤖 *Uji Coba Notifikasi SCPool Berhasil!*\n━━━━━━━━━━━━━━━━━━━━\nWaktu: `{time_now}`\nPengembang: `Coded By Ardh1337`\nBot Telegram siap menerima alert pendaftaran klien.",
        db=db
    )
    if not ok:
        raise HTTPException(status_code=400, detail=msg)
    return {"success": True, "message": msg}

