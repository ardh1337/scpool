"""
API Endpoint FastAPI (Tahap M8)
Menyediakan REST API untuk dashboard web lokal:
- Manajemen dan monitoring 32 Slot Fisik
- Riwayat SMS masuk per slot dan feed gabungan
- Trigger sinkronisasi hardware & eksekusi USSD
"""

from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from sqlalchemy import desc

from app.database.connection import get_db
from app.database.models import (
    ModemPool, Slot, Port, SIMCard, SIMSession, SMSMessage, SlotStatus, NumberStatus
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
        iccid_masked = None
        operator_name = None

        # Ambil sesi aktif
        active_sess = [sess for sess in s.sessions if sess.ended_at is None]
        if active_sess and active_sess[0].sim_card:
            sim = active_sess[0].sim_card
            phone_num = sim.phone_number
            num_status = sim.number_status.value
            iccid_masked = mask_sensitive(sim.iccid)
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
            "iccid_masked": iccid_masked,
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


@router.post("/sync")
def trigger_sync(db: Session = Depends(get_db)):
    """Memindai ulang 32 port hardware secara menyeluruh."""
    results = sync_modem_pool_hardware(db=db, pool_id="foxcom_32", auto_renumber=True)
    return {"success": True, "scanned_slots": len(results)}


@router.post("/poll-sms")
def trigger_poll_sms(db: Session = Depends(get_db)):
    """Memindai SMS baru dari semua slot yang aktif."""
    new_count = poll_all_active_slots_sms(db=db, is_initial=False)
    return {"success": True, "new_messages": new_count}

