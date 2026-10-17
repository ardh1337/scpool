"""
API Endpoint FastAPI (Tahap M8)
Menyediakan REST API untuk dashboard web lokal:
- Manajemen dan monitoring 32 Slot Fisik
- Riwayat SMS masuk per slot dan feed gabungan
- Trigger sinkronisasi hardware & eksekusi USSD
"""

from datetime import datetime, timezone
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session
from sqlalchemy import desc

from app.database.connection import get_db
from app.database.models import (
    ModemPool, Slot, Port, SIMCard, SIMSession, SMSMessage, SlotStatus, NumberStatus,
    OperatorProfile, UserAccount
)
from app.services.hardware_sync import sync_single_slot, sync_modem_pool_hardware
from app.services.sms_service import poll_slot_sms, poll_all_active_slots_sms
from app.services.numbering import get_display_label
from app.modem.sms_reader import mask_sensitive
from app.modem.transport import SerialTransport
from app.modem.at_client import ATClient
from app.modem.ussd import execute_ussd

router = APIRouter(prefix="/api")


@router.get("/status")
def get_system_status(db: Session = Depends(get_db)):
    """Mengembalikan ringkasan status operasional modem pool."""
    pool = db.query(ModemPool).first()
    total_slots = db.query(Slot).count()
    ready_slots = db.query(Slot).filter(Slot.status == SlotStatus.READY).count()
    empty_slots = db.query(Slot).filter(Slot.status == SlotStatus.EMPTY).count()
    error_slots = db.query(Slot).filter(Slot.status.in_([SlotStatus.SIM_ERROR, SlotStatus.PORT_ERROR])).count()
    total_sms = db.query(SMSMessage).count()

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
def list_slots(db: Session = Depends(get_db)):
    """Mengembalikan daftar 32 slot fisik lengkap dengan status dan informasi kartu."""
    slots = db.query(Slot).order_by(Slot.slot_index).all()
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
    limit: int = 50,
    db: Session = Depends(get_db)
):
    """Feed gabungan semua SMS masuk."""
    query = db.query(SMSMessage).join(Slot, SMSMessage.slot_id == Slot.id)

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

    user.password = new_pwd
    user.updated_at = datetime.now(timezone.utc)
    db.commit()

    return {
        "success": True,
        "message": "Password berhasil diperbarui.",
        "username": user.username,
        "password": user.password
    }


