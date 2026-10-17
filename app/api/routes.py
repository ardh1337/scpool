"""
API Endpoint FastAPI (Tahap M8)
Menyediakan REST API untuk dashboard web lokal:
- Manajemen dan monitoring 32 Slot Fisik
- Riwayat SMS masuk per slot dan feed gabungan
- Trigger sinkronisasi hardware & eksekusi USSD
"""

from datetime import datetime, timezone, timedelta
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query, Response, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session
from sqlalchemy import desc

from app.database.connection import get_db
from app.database.models import (
    ModemPool, Slot, Port, SIMCard, SIMSession, SMSMessage, SlotStatus, NumberStatus,
    OperatorProfile, UserAccount, UserSession
)
from app.auth.security import hash_password, verify_password, generate_token, generate_agent_token
from app.auth.deps import get_current_user_optional, require_user, require_admin
from app.services.hardware_sync import sync_single_slot, sync_modem_pool_hardware
from app.services.sms_service import poll_slot_sms, poll_all_active_slots_sms
from app.services.numbering import get_display_label
from app.modem.sms_reader import mask_sensitive
from app.modem.transport import SerialTransport
from app.modem.at_client import ATClient
from app.modem.ussd import execute_ussd

router = APIRouter(prefix="/api")


@router.get("/status")
def get_system_status(
    pool_id: Optional[str] = Query(None),
    user: Optional[UserAccount] = Depends(get_current_user_optional),
    db: Session = Depends(get_db)
):
    """Mengembalikan ringkasan status operasional modem pool berdasarkan hak akses pool."""
    target_pool_id = pool_id
    if user and user.role not in ["superadmin", "admin"]:
        target_pool_id = user.pool_id

    pool_query = db.query(ModemPool)
    if target_pool_id:
        pool = pool_query.filter(ModemPool.id == target_pool_id).first()
    else:
        pool = pool_query.first()

    slot_query = db.query(Slot)
    if pool:
        slot_query = slot_query.filter(Slot.pool_id == pool.id)

    total_slots = slot_query.count()
    ready_slots = slot_query.filter(Slot.status == SlotStatus.READY).count()
    empty_slots = slot_query.filter(Slot.status == SlotStatus.EMPTY).count()
    error_slots = slot_query.filter(Slot.status.in_([SlotStatus.SIM_ERROR, SlotStatus.PORT_ERROR])).count()

    sms_query = db.query(SMSMessage)
    if pool:
        sms_query = sms_query.join(Slot, SMSMessage.slot_id == Slot.id).filter(Slot.pool_id == pool.id)
    total_sms = sms_query.count()

    return {
        "pool_id": pool.id if pool else "foxcom_32",
        "pool_name": pool.name if pool else "Foxcom 32-Port Pool",
        "total_slots": total_slots,
        "ready_count": ready_slots,
        "empty_count": empty_slots,
        "error_count": error_slots,
        "total_sms": total_sms
    }


@router.get("/slots")
def list_slots(
    pool_id: Optional[str] = Query(None),
    user: Optional[UserAccount] = Depends(get_current_user_optional),
    db: Session = Depends(get_db)
):
    """Mengembalikan daftar slot fisik berdasarkan pool milik pengguna yang aktif."""
    target_pool_id = pool_id
    if user and user.role not in ["superadmin", "admin"]:
        target_pool_id = user.pool_id

    query = db.query(Slot)
    if target_pool_id:
        query = query.filter(Slot.pool_id == target_pool_id)
    else:
        # Fallback default ke pool pertama untuk kompatibilitas backward
        first_pool = db.query(ModemPool).first()
        if first_pool:
            query = query.filter(Slot.pool_id == first_pool.id)

    slots = query.order_by(Slot.slot_index).all()
    results = []

    for s in slots:
        phone_num = None
        num_status = "BELUM DIKETAHUI"
        iccid_val = None
        operator_name = None

        # Ambil sesi aktif jika slot READY
        active_sess = [sess for sess in s.sessions if sess.ended_at is None]
        if s.status == SlotStatus.READY and active_sess and active_sess[0].sim_card:
            sim = active_sess[0].sim_card
            phone_num = sim.phone_number
            num_status = sim.number_status.value
            iccid_val = sim.iccid
            operator_name = sim.operator_name or ("XL / AXIS" if phone_num and "838" in phone_num else None)

        sms_count = db.query(SMSMessage).filter(SMSMessage.slot_id == s.id).count()

        results.append({
            "id": s.id,
            "slot_index": s.slot_index,
            "port_name": s.physical_port_id,
            "status": s.status.value,
            "display_label": get_display_label(db, s.id),
            "phone_number": phone_num,
            "number_status": num_status,
            "operator_name": operator_name,
            "iccid": iccid_val,
            "iccid_masked": iccid_val,
            "sms_count": sms_count,
            "updated_at": s.updated_at.isoformat() if s.updated_at else None
        })

    return results


@router.get("/slots/{slot_index}/sms")
def get_slot_sms(slot_index: int, db: Session = Depends(get_db)):
    """Mengambil riwayat SMS khusus untuk satu slot fisik tertentu."""
    slot = db.query(Slot).filter(Slot.slot_index == slot_index).first()
    if not slot:
        raise HTTPException(status_code=404, detail="Slot tidak ditemukan.")

    messages = db.query(SMSMessage).filter(
        SMSMessage.slot_id == slot.id
    ).order_by(desc(SMSMessage.received_at)).all()

    return [
        {
            "id": m.id,
            "sender": m.sender_raw,
            "body": m.body,
            "status": m.status.value,
            "smsc_time": m.smsc_timestamp.isoformat() if m.smsc_timestamp else None,
            "received_at": m.received_at.isoformat() if m.received_at else None
        }
        for m in messages
    ]


@router.get("/sms")
def list_all_sms(
    q: Optional[str] = Query(None, description="Pencarian kata kunci"),
    pool_id: Optional[str] = Query(None),
    limit: int = 50,
    user: Optional[UserAccount] = Depends(get_current_user_optional),
    db: Session = Depends(get_db)
):
    """Feed gabungan semua SMS masuk yang terisolasi per pool pengguna."""
    target_pool_id = pool_id
    if user and user.role not in ["superadmin", "admin"]:
        target_pool_id = user.pool_id

    query = db.query(SMSMessage).join(Slot, SMSMessage.slot_id == Slot.id)
    if target_pool_id:
        query = query.filter(Slot.pool_id == target_pool_id)

    if q:
        search_fmt = f"%{q}%"
        query = query.filter((SMSMessage.body.ilike(search_fmt)) | (SMSMessage.sender_raw.ilike(search_fmt)))

    messages = query.order_by(desc(SMSMessage.received_at)).limit(limit).all()

    return [
        {
            "id": m.id,
            "slot_index": m.slot.slot_index if m.slot else None,
            "port_name": m.slot.physical_port_id if m.slot else None,
            "sender": m.sender_raw,
            "body": m.body,
            "status": m.status.value,
            "smsc_time": m.smsc_timestamp.isoformat() if m.smsc_timestamp else None,
            "received_at": m.received_at.isoformat() if m.received_at else None
        }
        for m in messages
    ]


@router.post("/slots/{slot_index}/ussd")
def dial_ussd_for_slot(
    slot_index: int,
    code: str = Query("*808*7*8#", description="Kode USSD dial"),
    db: Session = Depends(get_db)
):
    """Menjalankan dial USSD pada slot tertentu untuk mengekstrak nomor telepon."""
    slot = db.query(Slot).filter(Slot.slot_index == slot_index).first()
    if not slot or slot.status != SlotStatus.READY or not slot.physical_port_id:
        raise HTTPException(status_code=400, detail="Slot tidak siap atau SIM tidak terpasang.")

    transport = SerialTransport(port=slot.physical_port_id, baudrate=115200, timeout=1.0)
    client = ATClient(transport=transport, default_timeout=3.0)

    try:
        client.open()
        client.send_command('AT+CSCS="IRA"', timeout=1.0)
        success, text, phone = execute_ussd(client, ussd_code=code, timeout=12.0)

        if not success:
            return {"success": False, "message": text or "Gagal dial USSD."}

        if phone:
            active_sess = [s for s in slot.sessions if s.ended_at is None]
            if active_sess and active_sess[0].sim_card:
                sim = active_sess[0].sim_card
                sim.phone_number = phone
                sim.number_status = NumberStatus.DIKETAHUI
                db.commit()

        return {
            "success": True,
            "response_text": text,
            "phone_number": phone
        }
    finally:
        client.close()


class BulkUSSDRequest(BaseModel):
    slot_indices: Optional[List[int]] = None
    code: Optional[str] = "*808*7*8#"


@router.post("/ussd/bulk")
def dial_ussd_bulk(
    req: BulkUSSDRequest,
    db: Session = Depends(get_db)
):
    """Menjalankan dial USSD massal pada daftar slot fisik tertentu atau semua slot READY."""
    query = db.query(Slot).filter(Slot.status == SlotStatus.READY)
    if req.slot_indices:
        query = query.filter(Slot.slot_index.in_(req.slot_indices))

    slots = query.order_by(Slot.slot_index).all()
    results = []

    for slot in slots:
        if not slot.physical_port_id:
            results.append({
                "slot_index": slot.slot_index,
                "port_name": None,
                "success": False,
                "message": "Port fisik tidak terdaftar"
            })
            continue

        transport = SerialTransport(port=slot.physical_port_id, baudrate=115200, timeout=1.0)
        client = ATClient(transport=transport, default_timeout=3.0)
        try:
            client.open()
            client.send_command('AT+CSCS="IRA"', timeout=1.0)
            code_to_use = req.code or "*808*7*8#"
            success, text, phone = execute_ussd(client, ussd_code=code_to_use, timeout=12.0)
            if success and phone:
                active_sess = [s for s in slot.sessions if s.ended_at is None]
                if active_sess and active_sess[0].sim_card:
                    sim = active_sess[0].sim_card
                    sim.phone_number = phone
                    sim.number_status = NumberStatus.DIKETAHUI
                    db.commit()
            results.append({
                "slot_index": slot.slot_index,
                "port_name": slot.physical_port_id,
                "success": success,
                "phone_number": phone,
                "response_text": text
            })
        except Exception as e:
            results.append({
                "slot_index": slot.slot_index,
                "port_name": slot.physical_port_id,
                "success": False,
                "message": str(e)
            })
        finally:
            try:
                client.close()
            except Exception:
                pass

    return {
        "success": True,
        "total_dialed": len(results),
        "results": results
    }


@router.post("/sync")
def trigger_sync(
    auto_fetch: bool = Query(True, description="Ambil nomor USSD otomatis jika belum diketahui"),
    db: Session = Depends(get_db)
):
    """Memindai ulang 32 port hardware secara menyeluruh."""
    results = sync_modem_pool_hardware(
        db=db, pool_id="foxcom_32", auto_renumber=True, auto_fetch_number=auto_fetch
    )
    return {"success": True, "scanned_slots": len(results)}


@router.post("/poll-sms")
def trigger_poll_sms(db: Session = Depends(get_db)):
    """Memindai SMS baru dari semua slot yang aktif."""
    new_count = poll_all_active_slots_sms(db=db, is_initial=False)
    return {"success": True, "new_messages": new_count}


@router.get("/operators")
def list_operator_profiles(db: Session = Depends(get_db)):
    """Mengembalikan daftar konfigurasi kode USSD per operator."""
    profiles = db.query(OperatorProfile).order_by(OperatorProfile.name).all()
    return [
        {
            "id": p.id,
            "name": p.name,
            "ussd_code": p.ussd_code,
            "iccid_prefixes": p.iccid_prefixes,
            "operator_patterns": p.operator_patterns,
            "description": p.description,
            "updated_at": p.updated_at.isoformat() if p.updated_at else None
        }
        for p in profiles
    ]


class OperatorUSSDUpdate(BaseModel):
    ussd_code: str


@router.put("/operators/{operator_id}")
def update_operator_profile(
    operator_id: str,
    payload: Optional[OperatorUSSDUpdate] = None,
    ussd_code: Optional[str] = Query(None, description="Kode USSD baru"),
    db: Session = Depends(get_db)
):
    """Memperbarui kode dial USSD untuk operator tertentu."""
    target_code = (payload.ussd_code if payload and payload.ussd_code else ussd_code)
    if not target_code:
        raise HTTPException(status_code=400, detail="Kode USSD wajib diisi.")

    profile = db.query(OperatorProfile).filter(OperatorProfile.id == operator_id).first()
    if not profile:
        raise HTTPException(status_code=404, detail="Profil operator tidak ditemukan.")

    profile.ussd_code = target_code.strip()
    profile.updated_at = datetime.now(timezone.utc)
    db.commit()

    return {
        "success": True,
        "id": profile.id,
        "name": profile.name,
        "ussd_code": profile.ussd_code
    }


class CredentialsUpdate(BaseModel):
    username: Optional[str] = None
    password: str
    current_password: Optional[str] = None


@router.get("/auth/credentials")
def get_auth_credentials(db: Session = Depends(get_db)):
    """Mengambil informasi akun admin saat ini."""
    user = db.query(UserAccount).first()
    if not user:
        user = UserAccount(username="admin", password="admin123", role="superadmin")
        db.add(user)
        db.commit()
        db.refresh(user)

    return {
        "username": user.username,
        "password": user.password,
        "role": user.role,
        "updated_at": user.updated_at.isoformat() if user.updated_at else None
    }


@router.put("/auth/credentials")
def update_auth_credentials(payload: CredentialsUpdate, db: Session = Depends(get_db)):
    """Memperbarui password atau username admin."""
    user = db.query(UserAccount).first()
    if not user:
        user = UserAccount(username="admin", password="admin123", role="superadmin")
        db.add(user)

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
        "password": user.password
    }


class LoginRequest(BaseModel):
    username: str
    password: str


class UserCreateRequest(BaseModel):
    username: str
    password: str
    role: str = "operator"  # "superadmin" atau "operator"
    pool_name: Optional[str] = None
    port_count: int = 32


class UserUpdateRequest(BaseModel):
    password: Optional[str] = None
    role: Optional[str] = None
    is_active: Optional[bool] = None
    pool_id: Optional[str] = None


@router.post("/auth/login")
def login(payload: LoginRequest, response: Response, db: Session = Depends(get_db)):
    """Otentikasi login pengguna dan penerbitan session token."""
    uname = payload.username.strip().lower()
    user = db.query(UserAccount).filter(UserAccount.username == uname).first()
    if not user or not verify_password(payload.password, user.password):
        raise HTTPException(status_code=401, detail="Username atau password salah.")

    if not user.is_active:
        raise HTTPException(status_code=403, detail="Akun dinonaktifkan oleh administrator.")

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

    response.set_cookie(
        key="session_token",
        value=token,
        max_age=7 * 86400,
        httponly=True,
        samesite="lax"
    )

    return {
        "success": True,
        "token": token,
        "user": {
            "id": user.id,
            "username": user.username,
            "role": user.role,
            "pool_id": user.pool_id,
            "agent_token": user.agent_token if user.role in ["superadmin", "admin"] else None
        }
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
        "agent_token": user.agent_token
    }


@router.post("/auth/logout")
def logout(request: Request, response: Response, db: Session = Depends(get_db)):
    """Mengakhiri sesi pengguna aktif."""
    token = request.cookies.get("session_token")
    auth_header = request.headers.get("Authorization")
    if auth_header and auth_header.startswith("Bearer "):
        token = auth_header[7:].strip()

    if token:
        sess = db.query(UserSession).filter(UserSession.token == token).first()
        if sess:
            db.delete(sess)
            db.commit()

    response.delete_cookie("session_token")
    return {"success": True, "message": "Berhasil logout."}


@router.get("/admin/users")
def list_admin_users(
    user: Optional[UserAccount] = Depends(get_current_user_optional),
    db: Session = Depends(get_db)
):
    """Mengembalikan daftar semua akun pengguna (khusus Superadmin)."""
    if user and user.role not in ["superadmin", "admin"]:
        raise HTTPException(status_code=403, detail="Akses ditolak.")

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
    user: Optional[UserAccount] = Depends(get_current_user_optional),
    db: Session = Depends(get_db)
):
    """Membuat pengguna baru dan otomatis mengalokasikan Modem Pool baru."""
    if user and user.role not in ["superadmin", "admin"]:
        raise HTTPException(status_code=403, detail="Akses ditolak.")

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
    user: Optional[UserAccount] = Depends(get_current_user_optional),
    db: Session = Depends(get_db)
):
    """Memperbarui data pengguna, status aktif, atau password."""
    if user and user.role not in ["superadmin", "admin"]:
        raise HTTPException(status_code=403, detail="Akses ditolak.")

    target = db.query(UserAccount).filter(UserAccount.id == user_id).first()
    if not target:
        raise HTTPException(status_code=404, detail="Pengguna tidak ditemukan.")

    if payload.password and len(payload.password.strip()) >= 4:
        target.password = hash_password(payload.password.strip())
    if payload.role:
        target.role = payload.role
    if payload.is_active is not None:
        if target.id == 1 and not payload.is_active:
            raise HTTPException(status_code=400, detail="Administrator utama tidak boleh dinonaktifkan.")
        target.is_active = payload.is_active
    if payload.pool_id:
        target.pool_id = payload.pool_id

    target.updated_at = datetime.now(timezone.utc)
    db.commit()

    return {"success": True, "message": "Data pengguna berhasil diperbarui."}


@router.delete("/admin/users/{user_id}")
def delete_admin_user(
    user_id: int,
    user: Optional[UserAccount] = Depends(get_current_user_optional),
    db: Session = Depends(get_db)
):
    """Menghapus akun pengguna."""
    if user and user.role not in ["superadmin", "admin"]:
        raise HTTPException(status_code=403, detail="Akses ditolak.")
    if user_id == 1:
        raise HTTPException(status_code=400, detail="Administrator utama tidak boleh dihapus.")

    target = db.query(UserAccount).filter(UserAccount.id == user_id).first()
    if not target:
        raise HTTPException(status_code=404, detail="Pengguna tidak ditemukan.")

    db.delete(target)
    db.commit()
    return {"success": True, "message": "Pengguna berhasil dihapus."}


@router.post("/admin/users/{user_id}/token")
def regenerate_admin_user_token(
    user_id: int,
    user: Optional[UserAccount] = Depends(get_current_user_optional),
    db: Session = Depends(get_db)
):
    """Membuat ulang Agent Token baru untuk pengguna tertentu."""
    if user and user.role not in ["superadmin", "admin"]:
        raise HTTPException(status_code=403, detail="Akses ditolak.")

    target = db.query(UserAccount).filter(UserAccount.id == user_id).first()
    if not target:
        raise HTTPException(status_code=404, detail="Pengguna tidak ditemukan.")

    target.agent_token = generate_agent_token()
    db.commit()
    return {"success": True, "agent_token": target.agent_token}


@router.get("/admin/pools")
def list_admin_pools(
    user: Optional[UserAccount] = Depends(get_current_user_optional),
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


@router.get("/agent/status")
def get_agent_status(
    pool_id: Optional[str] = Query(None),
    user: Optional[UserAccount] = Depends(get_current_user_optional)
):
    """Mengembalikan status online remote agent untuk pool tertentu."""
    from app.services.agent_gateway import agent_gateway
    target_pool = pool_id
    if user and user.role not in ["superadmin", "admin"]:
        target_pool = user.pool_id
    if not target_pool:
        target_pool = "foxcom_32"

    is_online = agent_gateway.is_agent_online(target_pool)
    info = agent_gateway.get_agent_info(target_pool)
    return {
        "pool_id": target_pool,
        "is_online": is_online,
        "info": info
    }



