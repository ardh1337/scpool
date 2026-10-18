"""Status modem pool, daftar slot, feed SMS, dan log kejadian. Semua data dibatasi pada pool milik pengguna."""

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import desc, func
from sqlalchemy.orm import Session, joinedload

from app.auth.deps import is_admin, require_user, resolve_pool_id
from app.database.connection import get_db
from app.database.models import (
    EventLog, ModemPool, SIMSession, Slot, SlotStatus, SMSMessage, SMSStatus, UserAccount,
)
from app.services.agent_gateway import agent_gateway
from app.services.numbering import get_display_label

router = APIRouter()


def _mask_iccid(iccid: Optional[str]) -> Optional[str]:
    if not iccid or len(iccid) < 10:
        return iccid
    return iccid[:6] + "•" * (len(iccid) - 10) + iccid[-4:]


def _sms_dict(m: SMSMessage, with_slot: bool = False) -> dict:
    data = {
        "id": m.id,
        "sender": m.sender_raw,
        "body": m.body,
        "status": m.status.value,
        "smsc_time": m.smsc_timestamp.isoformat() if m.smsc_timestamp else None,
        "received_at": m.received_at.isoformat() if m.received_at else None,
    }
    if with_slot:
        data["slot_index"] = m.slot.slot_index if m.slot else None
        data["port_name"] = m.slot.physical_port_id if m.slot else None
    return data


def _get_pool_or_404(db: Session, pool_id: str) -> ModemPool:
    pool = db.query(ModemPool).filter(ModemPool.id == pool_id).first()
    if not pool:
        raise HTTPException(404, "Modem pool tidak ditemukan.")
    return pool


@router.get("/status")
def get_system_status(pool_id: Optional[str] = Query(None), user: UserAccount = Depends(require_user),
                      db: Session = Depends(get_db)):
    target = resolve_pool_id(db, user, pool_id)
    if target is None:
        return {"pool_id": None, "pool_name": None, "total_slots": 0, "ready_count": 0, "empty_count": 0,
                "error_count": 0, "offline_count": 0, "total_sms": 0, "needs_review_sms": 0,
                "agent_online": agent_gateway.is_agent_online(user.id)}
    pool = _get_pool_or_404(db, target)

    status_counts = dict(
        db.query(Slot.status, func.count(Slot.id)).filter(Slot.pool_id == pool.id).group_by(Slot.status).all())
    total_sms = db.query(func.count(SMSMessage.id)).join(Slot, SMSMessage.slot_id == Slot.id).filter(
        Slot.pool_id == pool.id).scalar() or 0
    needs_review = db.query(func.count(SMSMessage.id)).join(Slot, SMSMessage.slot_id == Slot.id).filter(
        Slot.pool_id == pool.id, SMSMessage.status == SMSStatus.PERLU_DITINJAU).scalar() or 0

    return {
        "pool_id": pool.id,
        "pool_name": pool.name,
        "total_slots": sum(status_counts.values()),
        "ready_count": status_counts.get(SlotStatus.READY, 0),
        "empty_count": status_counts.get(SlotStatus.EMPTY, 0),
        "error_count": status_counts.get(SlotStatus.SIM_ERROR, 0) + status_counts.get(SlotStatus.PORT_ERROR, 0),
        "offline_count": status_counts.get(SlotStatus.OFFLINE, 0) + status_counts.get(SlotStatus.UNMAPPED, 0),
        "total_sms": total_sms,
        "needs_review_sms": needs_review,
        "agent_online": bool(pool.owner_id and agent_gateway.is_agent_online(pool.owner_id)),
    }


@router.get("/slots")
def list_slots(pool_id: Optional[str] = Query(None), user: UserAccount = Depends(require_user),
               db: Session = Depends(get_db)):
    target_pool_id = resolve_pool_id(db, user, pool_id)
    if target_pool_id is None:
        return []
    _get_pool_or_404(db, target_pool_id)

    slots = db.query(Slot).options(
        joinedload(Slot.sessions).joinedload(SIMSession.sim_card)
    ).filter(Slot.pool_id == target_pool_id).order_by(Slot.slot_index).all()

    sms_counts = dict(
        db.query(SMSMessage.slot_id, func.count(SMSMessage.id))
        .join(Slot, SMSMessage.slot_id == Slot.id).filter(Slot.pool_id == target_pool_id)
        .group_by(SMSMessage.slot_id).all())

    results = []
    for s in slots:
        phone_num, iccid_val, operator_name = None, None, None
        num_status = "BELUM DIKETAHUI"
        active = next((x for x in s.sessions if x.ended_at is None), None)
        if s.status == SlotStatus.READY and active and active.sim_card:
            sim = active.sim_card
            phone_num, iccid_val, operator_name = sim.phone_number, sim.iccid, sim.operator_name
            num_status = sim.number_status.value

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
            "iccid_masked": _mask_iccid(iccid_val),
            "sms_count": sms_counts.get(s.id, 0),
            "updated_at": s.updated_at.isoformat() if s.updated_at else None,
        })
    return results


@router.get("/slots/{slot_index}/sms")
def get_slot_sms(slot_index: int, pool_id: Optional[str] = Query(None), limit: int = Query(200, ge=1, le=1000),
                 user: UserAccount = Depends(require_user), db: Session = Depends(get_db)):
    target_pool_id = resolve_pool_id(db, user, pool_id)
    slot = db.query(Slot).filter(Slot.slot_index == slot_index, Slot.pool_id == target_pool_id).first()
    if not slot:
        raise HTTPException(404, "Slot tidak ditemukan.")
    messages = db.query(SMSMessage).filter(SMSMessage.slot_id == slot.id).order_by(
        desc(SMSMessage.received_at), desc(SMSMessage.id)).limit(limit).all()
    return [_sms_dict(m) for m in messages]


@router.get("/sms")
def list_all_sms(q: Optional[str] = Query(None, max_length=100, description="Pencarian kata kunci"),
                 pool_id: Optional[str] = Query(None), limit: int = Query(50, ge=1, le=500),
                 offset: int = Query(0, ge=0), user: UserAccount = Depends(require_user),
                 db: Session = Depends(get_db)):
    target_pool_id = resolve_pool_id(db, user, pool_id)
    if target_pool_id is None:
        return []
    query = db.query(SMSMessage).options(joinedload(SMSMessage.slot)).join(
        Slot, SMSMessage.slot_id == Slot.id).filter(Slot.pool_id == target_pool_id)

    if q:
        escaped = q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        pattern = f"%{escaped}%"
        query = query.filter(SMSMessage.body.ilike(pattern, escape="\\") |
                             SMSMessage.sender_raw.ilike(pattern, escape="\\"))

    messages = query.order_by(desc(SMSMessage.received_at), desc(SMSMessage.id)).offset(offset).limit(limit).all()
    return [_sms_dict(m, with_slot=True) for m in messages]


@router.get("/events")
def list_events(pool_id: Optional[str] = Query(None), limit: int = Query(100, ge=1, le=500),
                user: UserAccount = Depends(require_user), db: Session = Depends(get_db)):
    """Log kejadian sistem (I-8: kegagalan harus terlihat). Dibatasi pada slot milik pool pengguna."""
    target_pool_id = resolve_pool_id(db, user, pool_id)
    if target_pool_id is None:
        return []
    slot_ids = [r[0] for r in db.query(Slot.id).filter(Slot.pool_id == target_pool_id).all()]
    query = db.query(EventLog)
    if is_admin(user) and not pool_id:
        query = query.filter((EventLog.slot_id.in_(slot_ids)) | (EventLog.slot_id.is_(None)))
    else:
        query = query.filter(EventLog.slot_id.in_(slot_ids))
    rows = query.order_by(desc(EventLog.timestamp), desc(EventLog.id)).limit(limit).all()
    return [
        {"id": e.id, "timestamp": e.timestamp.isoformat() if e.timestamp else None, "level": e.level,
         "category": e.category, "slot_id": e.slot_id, "port_id": e.port_id, "message": e.message}
        for e in rows
    ]
