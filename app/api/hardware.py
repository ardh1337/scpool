"""
Kontrol hardware: dial USSD (satu slot / massal), sinkronisasi port, polling SMS, dan profil operator.

Modem bisa terhubung langsung ke server (hardware lokal) atau lewat agent remote. `_Dialer` memilih jalurnya
per pool sehingga endpoint yang sama bekerja untuk keduanya; sebelumnya endpoint selalu membuka COM lokal
sehingga tidak berfungsi sama sekali untuk klien remote.
"""

import asyncio
from datetime import datetime, timezone
from typing import List, Optional, Tuple

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.auth.deps import require_admin, require_pool_id, require_user
from app.core.config import MAX_PORTS_PER_POOL, settings
from app.database.connection import get_db
from app.database.models import NumberSource, OperatorProfile, SIMSession, Slot, SlotStatus, UserAccount
from app.modem.at_client import ATClient
from app.modem.port_lock import PortBusyError, port_access
from app.modem.transport import SerialTransport
from app.modem.ussd import execute_ussd, validate_ussd_code
from app.services.agent_gateway import agent_gateway
from app.services.hardware_sync import sync_modem_pool_hardware
from app.services.sim_numbers import apply_ussd_result
from app.services.sms_service import poll_all_active_slots_sms
from app.services.units import owner_of_pool

router = APIRouter()

DEFAULT_DIAL_CODE = "*808*7*8#"
BULK_CONCURRENCY = MAX_PORTS_PER_POOL  # tiap port modem sendiri; agent menggilir per port


def _dial_local(port: str, code: str) -> Tuple[bool, Optional[str], Optional[str]]:
    """Dial USSD pada modem yang terpasang di server (dijalankan di threadpool)."""
    try:
        with port_access(port, timeout=20.0):
            client = ATClient(transport=SerialTransport(port=port, baudrate=115200, timeout=1.0),
                              default_timeout=3.0)
            try:
                client.open()
                client.send_command('AT+CSCS="IRA"', timeout=1.0)
                return execute_ussd(client, ussd_code=code, timeout=12.0)
            finally:
                try:
                    client.close()
                except Exception:
                    pass
    except PortBusyError as exc:
        return False, str(exc), None
    except Exception as exc:
        return False, f"Gagal mengakses port {port}: {type(exc).__name__}", None


async def _dial(owner_id: Optional[int], pool_id: str, port: str,
                code: str) -> Tuple[bool, Optional[str], Optional[str]]:
    if owner_id is not None and agent_gateway.is_agent_online(owner_id):
        return await agent_gateway.request_ussd(owner_id, port, code)
    if settings.local_hardware and pool_id == settings.local_pool_id:
        return await run_in_threadpool(_dial_local, port, code)
    return False, "Agent untuk modem pool ini sedang offline.", None


def _store_number(db: Session, slot: Slot, success: bool, phone: Optional[str]) -> None:
    active = db.query(SIMSession).filter(SIMSession.slot_id == slot.id, SIMSession.ended_at.is_(None)).first()
    if active and active.sim_card:
        apply_ussd_result(db, active.sim_card, success, phone, source=NumberSource.USSD)
        db.commit()


def _checked_code(code: Optional[str]) -> str:
    try:
        return validate_ussd_code(code or DEFAULT_DIAL_CODE)
    except ValueError as exc:
        raise HTTPException(400, str(exc))


@router.post("/slots/{slot_index}/ussd")
async def dial_ussd_for_slot(slot_index: int, code: str = Query(DEFAULT_DIAL_CODE, description="Kode USSD"),
                             pool_id: Optional[str] = Query(None), user: UserAccount = Depends(require_user),
                             db: Session = Depends(get_db)):
    """Dial USSD pada satu slot (mis. untuk membaca nomor telepon SIM)."""
    ussd_code = _checked_code(code)
    slot = db.query(Slot).filter(Slot.slot_index == slot_index,
                                 Slot.pool_id == require_pool_id(db, user, pool_id)).first()
    if not slot or slot.status != SlotStatus.READY or not slot.physical_port_id:
        raise HTTPException(400, "Slot tidak siap atau SIM tidak terpasang.")

    success, text, phone = await _dial(owner_of_pool(db, slot.pool_id), slot.pool_id, slot.physical_port_id,
                                       ussd_code)
    if not success:
        return {"success": False, "message": text or "Gagal dial USSD."}
    _store_number(db, slot, True, phone)
    return {"success": True, "response_text": text, "phone_number": phone}


class BulkUSSDRequest(BaseModel):
    slot_indices: Optional[List[int]] = Field(default=None, max_length=64)
    code: Optional[str] = DEFAULT_DIAL_CODE
    pool_id: Optional[str] = None


@router.post("/ussd/bulk")
async def dial_ussd_bulk(req: BulkUSSDRequest, user: UserAccount = Depends(require_user),
                         db: Session = Depends(get_db)):
    """Dial USSD paralel ke semua slot terpilih (atau semua slot READY) sekaligus."""
    ussd_code = _checked_code(req.code)
    pool_id = require_pool_id(db, user, req.pool_id)
    owner_id = owner_of_pool(db, pool_id)

    query = db.query(Slot).filter(Slot.pool_id == pool_id, Slot.status == SlotStatus.READY)
    if req.slot_indices:
        query = query.filter(Slot.slot_index.in_(req.slot_indices))
    slots = query.order_by(Slot.slot_index).all()

    gate = asyncio.Semaphore(BULK_CONCURRENCY)

    async def run(slot: Slot):
        if not slot.physical_port_id:
            return slot, False, "Port fisik tidak terdaftar", None
        async with gate:
            ok, text, phone = await _dial(owner_id, pool_id, slot.physical_port_id, ussd_code)
        return slot, ok, text, phone

    outcomes = await asyncio.gather(*(run(s) for s in slots))

    results = []
    for slot, ok, text, phone in outcomes:
        if ok:
            _store_number(db, slot, True, phone)
        entry = {"slot_index": slot.slot_index, "port_name": slot.physical_port_id, "success": ok}
        if ok:
            entry.update(phone_number=phone, response_text=text)
        else:
            entry["message"] = text
        results.append(entry)

    return {"success": True, "total_dialed": len(results), "results": results}


@router.post("/sync")
async def trigger_sync(pool_id: Optional[str] = Query(None),
                       auto_fetch: bool = Query(True, description="Ambil nomor USSD otomatis bila belum diketahui"),
                       user: UserAccount = Depends(require_admin), db: Session = Depends(get_db)):
    """Pindai ulang semua port: lewat agent bila online, atau langsung bila hardware lokal aktif."""
    target = require_pool_id(db, user, pool_id)
    owner_id = owner_of_pool(db, target)
    if owner_id is not None and agent_gateway.is_agent_online(owner_id):
        await agent_gateway.request_rescan(owner_id)
        return {"success": True, "mode": "agent", "message": "Permintaan pindai dikirim ke agent."}
    if settings.local_hardware and target == settings.local_pool_id:
        results = await run_in_threadpool(
            sync_modem_pool_hardware, db, target, True, auto_fetch)
        return {"success": True, "mode": "local", "scanned_slots": len(results)}
    raise HTTPException(409, "Tidak ada agent online dan hardware lokal tidak aktif untuk pool ini.")


@router.post("/poll-sms")
async def trigger_poll_sms(pool_id: Optional[str] = Query(None), user: UserAccount = Depends(require_user),
                           db: Session = Depends(get_db)):
    """Periksa SMS baru. Jalur agent mengirim ulang isi kartu; jalur lokal membaca langsung dari modem."""
    target = require_pool_id(db, user, pool_id)
    owner_id = owner_of_pool(db, target)
    if owner_id is not None and agent_gateway.is_agent_online(owner_id):
        await agent_gateway.request_rescan(owner_id)
        return {"success": True, "mode": "agent", "new_messages": 0,
                "message": "Permintaan baca SMS dikirim ke agent; hasil muncul beberapa detik lagi."}
    if settings.local_hardware and target == settings.local_pool_id:
        new_count = await run_in_threadpool(poll_all_active_slots_sms, db, target)
        return {"success": True, "mode": "local", "new_messages": new_count}
    raise HTTPException(409, "Tidak ada agent online dan hardware lokal tidak aktif untuk pool ini.")


@router.get("/operators")
def list_operator_profiles(user: UserAccount = Depends(require_user), db: Session = Depends(get_db)):
    profiles = db.query(OperatorProfile).order_by(OperatorProfile.name).all()
    return [
        {"id": p.id, "name": p.name, "ussd_code": p.ussd_code, "iccid_prefixes": p.iccid_prefixes,
         "operator_patterns": p.operator_patterns, "description": p.description,
         "updated_at": p.updated_at.isoformat() if p.updated_at else None}
        for p in profiles
    ]


class OperatorUSSDUpdate(BaseModel):
    ussd_code: str = Field(max_length=50)


@router.put("/operators/{operator_id}")
def update_operator_profile(operator_id: str, payload: Optional[OperatorUSSDUpdate] = None,
                            ussd_code: Optional[str] = Query(None, description="Kode USSD baru"),
                            user: UserAccount = Depends(require_admin), db: Session = Depends(get_db)):
    target_code = payload.ussd_code if payload and payload.ussd_code else ussd_code
    if not target_code:
        raise HTTPException(400, "Kode USSD wajib diisi.")
    new_code = _checked_code(target_code)  # kode ini nanti dikirim ke modem: harus lolos validasi

    profile = db.query(OperatorProfile).filter(OperatorProfile.id == operator_id).first()
    if not profile:
        raise HTTPException(404, "Profil operator tidak ditemukan.")
    profile.ussd_code = new_code
    profile.updated_at = datetime.now(timezone.utc)
    db.commit()
    return {"success": True, "id": profile.id, "name": profile.name, "ussd_code": profile.ussd_code}
