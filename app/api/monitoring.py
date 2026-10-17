"""
Endpoint API Monitoring Slot & Feed SMS
Menyediakan informasi status operasional 32 slot modem dan pesan SMS masuk secara terisolasi per pool.
"""

from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from sqlalchemy import desc

from app.database.connection import get_db
from app.database.models import ModemPool, Slot, SMSMessage, SlotStatus, UserAccount
from app.auth.deps import get_current_user_optional
from app.services.numbering import get_display_label

router = APIRouter()


@router.get("/status")
def get_system_status(
    pool_id: Optional[str] = Query(None),
    user: Optional[UserAccount] = Depends(get_current_user_optional),
    db: Session = Depends(get_db)
):
    """Mengembalikan ringkasan status operasional modem pool berdasarkan hak akses pool."""
    if not user:
        raise HTTPException(status_code=401, detail="Otentikasi diperlukan. Silakan login terlebih dahulu.")

    target_pool_id = pool_id
    if user.role not in ["superadmin", "admin"]:
        target_pool_id = user.pool_id
    elif not target_pool_id:
        first_pool = db.query(ModemPool).first()
        target_pool_id = first_pool.id if first_pool else "foxcom_32"

    pool = db.query(ModemPool).filter(ModemPool.id == target_pool_id).first()

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
        "pool_id": pool.id if pool else target_pool_id,
        "pool_name": pool.name if pool else "Modem Pool",
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
    if not user:
        raise HTTPException(status_code=401, detail="Otentikasi diperlukan. Silakan login terlebih dahulu.")

    target_pool_id = pool_id
    if user.role not in ["superadmin", "admin"]:
        target_pool_id = user.pool_id
    elif not target_pool_id:
        first_pool = db.query(ModemPool).first()
        target_pool_id = first_pool.id if first_pool else "foxcom_32"

    query = db.query(Slot).filter(Slot.pool_id == target_pool_id)
    slots = query.order_by(Slot.slot_index).all()
    results = []

    for s in slots:
        phone_num = None
        num_status = "BELUM DIKETAHUI"
        iccid_val = None
        operator_name = None

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
def get_slot_sms(
    slot_index: int,
    pool_id: Optional[str] = Query(None),
    user: Optional[UserAccount] = Depends(get_current_user_optional),
    db: Session = Depends(get_db)
):
    """Mengambil riwayat SMS khusus untuk satu slot fisik tertentu."""
    if not user:
        raise HTTPException(status_code=401, detail="Otentikasi diperlukan.")

    target_pool_id = pool_id
    if user.role not in ["superadmin", "admin"]:
        target_pool_id = user.pool_id
    elif not target_pool_id:
        target_pool_id = "foxcom_32"

    slot = db.query(Slot).filter(Slot.slot_index == slot_index, Slot.pool_id == target_pool_id).first()
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
    if not user:
        raise HTTPException(status_code=401, detail="Otentikasi diperlukan. Silakan login terlebih dahulu.")

    target_pool_id = pool_id
    if user.role not in ["superadmin", "admin"]:
        target_pool_id = user.pool_id
    elif not target_pool_id:
        target_pool_id = "foxcom_32"

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

