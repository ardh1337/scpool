"""
Endpoint API Kontrol Hardware, Dial USSD, dan Sinkronisasi Port
Mengelola eksekusi USSD per slot, dial massal, sinkronisasi COM port fisik, dan profil operator.
"""

from datetime import datetime, timezone
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database.connection import get_db
from app.database.models import Slot, SlotStatus, NumberStatus, OperatorProfile, UserAccount
from app.auth.deps import require_user, require_admin
from app.services.hardware_sync import sync_modem_pool_hardware
from app.services.sms_service import poll_all_active_slots_sms
from app.modem.transport import SerialTransport
from app.modem.at_client import ATClient
from app.modem.ussd import execute_ussd

router = APIRouter()


@router.post("/slots/{slot_index}/ussd")
def dial_ussd_for_slot(
    slot_index: int,
    code: str = Query("*808*7*8#", description="Kode USSD dial"),
    user: UserAccount = Depends(require_user),
    db: Session = Depends(get_db)
):
    """Menjalankan dial USSD pada slot tertentu untuk mengekstrak nomor telepon."""
    query = db.query(Slot).filter(Slot.slot_index == slot_index)
    if user.role not in ["superadmin", "admin"]:
        query = query.filter(Slot.pool_id == user.pool_id)
    slot = query.first()
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
    user: UserAccount = Depends(require_user),
    db: Session = Depends(get_db)
):
    """Menjalankan dial USSD massal pada daftar slot fisik tertentu atau semua slot READY."""
    query = db.query(Slot).filter(Slot.status == SlotStatus.READY)
    if user.role not in ["superadmin", "admin"]:
        query = query.filter(Slot.pool_id == user.pool_id)
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
    user: UserAccount = Depends(require_admin),
    db: Session = Depends(get_db)
):
    """Memindai ulang 32 port hardware secara menyeluruh."""
    results = sync_modem_pool_hardware(
        db=db, pool_id="foxcom_32", auto_renumber=True, auto_fetch_number=auto_fetch
    )
    return {"success": True, "scanned_slots": len(results)}


@router.post("/poll-sms")
def trigger_poll_sms(
    user: UserAccount = Depends(require_user),
    db: Session = Depends(get_db)
):
    """Memindai SMS baru dari semua slot yang aktif."""
    new_count = poll_all_active_slots_sms(db=db, is_initial=False)
    return {"success": True, "new_messages": new_count}


@router.get("/operators")
def list_operator_profiles(
    user: UserAccount = Depends(require_user),
    db: Session = Depends(get_db)
):
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
    user: UserAccount = Depends(require_admin),
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

