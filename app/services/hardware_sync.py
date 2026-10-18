"""
Sinkronisasi status modem pool LOKAL (serial port di mesin server) ke database.

Memindai tiap port, memperbarui status slot, mengelola siklus hidup sesi SIM (I-1, I-6) dan penomoran urut (I-3).
Modem yang terhubung lewat agent remote ditangani `agent_gateway`, bukan modul ini.
"""

import logging
from datetime import datetime, timezone
from typing import Dict, List

from sqlalchemy.orm import Session

from app.core.config import settings
from app.database.models import Slot, SIMSession, SlotStatus
from app.modem.at_client import ATClient
from app.modem.port_lock import PortBusyError, port_access
from app.modem.sim_status import parse_cnum_response, parse_cpin_response, parse_iccid_response
from app.modem.transport import SerialTransport
from app.modem.ussd import execute_ussd
from app.services.events import log_event
from app.services.numbering import get_display_label, renumber_pool_slots
from app.services.pool_init import ensure_pool_initialized
from app.services.sim_numbers import (
    apply_cnum_result, apply_ussd_result, fetch_backoff, needs_auto_fetch, ussd_code_for_sim,
)
from app.services.slot_manager import handle_sim_inserted, handle_sim_removed

logger = logging.getLogger(__name__)


def _mark_problem(db: Session, slot: Slot, status: SlotStatus, reason: str, now: datetime) -> None:
    """Tandai slot bermasalah dan catat kejadian hanya saat status BERUBAH (hindari banjir log tiap siklus)."""
    changed = slot.status != status
    slot.status = status
    slot.updated_at = now
    db.commit()
    if changed:
        log_event(db, "WARN" if status == SlotStatus.SIM_ERROR else "ERROR",
                  "PORT" if status == SlotStatus.PORT_ERROR else "SIM", reason,
                  slot_id=slot.id, port_id=slot.physical_port_id)


def _fetch_number(db: Session, client: ATClient, sim_card, iccid: str, res: Dict[str, str]) -> None:
    """Ambil nomor: AT+CNUM dulu (tanpa biaya jaringan), lalu USSD. Percobaan USSD diberi jeda eksponensial."""
    try:
        cnum = parse_cnum_response(client.send_command("AT+CNUM", timeout=1.5))
        if apply_cnum_result(db, sim_card, cnum):
            db.commit()
            res["phone_number"] = sim_card.phone_number
            return
    except Exception:
        logger.debug("AT+CNUM gagal pada %s", res.get("port"), exc_info=True)

    if not fetch_backoff.allowed(iccid):
        return
    ussd_code = ussd_code_for_sim(db, iccid, sim_card.operator_name)
    try:
        success, _text, phone = execute_ussd(client, ussd_code=ussd_code, timeout=12.0)
    except Exception as exc:
        fetch_backoff.record_failure(iccid)
        log_event(db, "WARN", "AT", "Dial USSD otomatis gagal.", port_id=res.get("port"), details=repr(exc),
                  dedupe_key=f"ussd:{iccid}", dedupe_seconds=900)
        return
    if success:
        fetch_backoff.record_success(iccid)
    else:
        fetch_backoff.record_failure(iccid)
    apply_ussd_result(db, sim_card, success, phone)
    db.commit()
    if phone:
        res["phone_number"] = phone


def _sync_open_port(db: Session, slot: Slot, client: ATClient, now: datetime, auto_fetch_number: bool,
                    res: Dict[str, str]) -> None:
    if not client.send_command("AT", timeout=0.8).success:
        _mark_problem(db, slot, SlotStatus.PORT_ERROR, "Modem tidak membalas perintah AT.", now)
        res["status"] = "PORT_ERROR"
        return

    cpin_status = parse_cpin_response(client.send_command("AT+CPIN?", timeout=1.0))

    if cpin_status == "READY":
        iccid = parse_iccid_response(client.send_command("AT+QCCID", timeout=1.0))
        if not iccid:
            _mark_problem(db, slot, SlotStatus.SIM_ERROR, "SIM siap tetapi ICCID tidak terbaca.", now)
            res["status"] = "SIM_ERROR"
            return
        res["iccid"] = iccid

        active = db.query(SIMSession).filter(
            SIMSession.slot_id == slot.id, SIMSession.ended_at.is_(None)
        ).first()
        if active and active.sim_card and active.sim_card.iccid == iccid:
            slot.status = SlotStatus.READY
            slot.updated_at = now
            res["status"], res["session_action"] = "READY", "EXISTING"
            sim_card = active.sim_card
        else:
            # I-1 & I-6: sesi baru murni untuk SIM ini.
            _, sim_card = handle_sim_inserted(db=db, slot_id=slot.id, iccid=iccid)
            res["status"], res["session_action"] = "READY", "NEW_SESSION"

        if auto_fetch_number and needs_auto_fetch(sim_card):
            _fetch_number(db, client, sim_card, iccid, res)

    elif cpin_status == "NOT_INSERTED":
        if slot.status != SlotStatus.EMPTY:
            handle_sim_removed(db=db, slot_id=slot.id)
            res["session_action"] = "CLOSED_SESSION"
        slot.status = SlotStatus.EMPTY
        res["status"] = "EMPTY"

    else:
        _mark_problem(db, slot, SlotStatus.SIM_ERROR, f"Status SIM tidak siap ({cpin_status}).", now)
        res["status"] = "SIM_ERROR"
        return

    slot.updated_at = now
    db.commit()


def sync_single_slot(db: Session, slot: Slot, timeout: float = 1.0,
                     auto_fetch_number: bool = False) -> Dict[str, str]:
    """Pindai satu slot lokal dan perbarui status/sesi di DB. Kegagalan satu port tidak menghentikan port lain (I-10)."""
    now = datetime.now(timezone.utc)
    port_name = slot.physical_port_id
    res = {"slot_index": slot.slot_index, "port": port_name, "status": "OFFLINE",
           "iccid": "-", "session_action": "NONE", "label": "-"}

    if not port_name:
        slot.status = SlotStatus.UNMAPPED
        db.commit()
        return res

    try:
        # Non-blocking: bila port sedang dipakai dial USSD/baca SMS, lewati siklus ini tanpa mengubah status.
        with port_access(port_name, blocking=False):
            client = ATClient(
                transport=SerialTransport(port=port_name, baudrate=115200, timeout=timeout),
                default_timeout=timeout,
            )
            try:
                client.open()
                _sync_open_port(db, slot, client, now, auto_fetch_number, res)
            finally:
                try:
                    client.close()
                except Exception:
                    pass
    except PortBusyError:
        res["status"] = slot.status.value
        res["session_action"] = "SKIPPED_BUSY"
    except Exception as exc:
        db.rollback()
        _mark_problem(db, slot, SlotStatus.PORT_ERROR, "Port serial tidak dapat dibuka atau dibaca.", now)
        logger.debug("Port %s error: %r", port_name, exc)
        res["status"] = "PORT_ERROR"

    res["label"] = get_display_label(db, slot.id)
    return res


def sync_modem_pool_hardware(db: Session, pool_id: str, auto_renumber: bool = True,
                             auto_fetch_number: bool = False) -> List[Dict[str, str]]:
    """Sinkronisasi penuh seluruh slot sebuah pool LOKAL dengan hardware nyata."""
    if pool_id == settings.local_pool_id:
        ensure_pool_initialized(db, pool_id=pool_id)

    slots = db.query(Slot).filter(Slot.pool_id == pool_id).order_by(Slot.slot_index).all()
    results = [sync_single_slot(db, slot, auto_fetch_number=auto_fetch_number) for slot in slots]

    if auto_renumber:
        renumber_pool_slots(db, pool_id=pool_id)
        by_index = {s.slot_index: s for s in slots}
        for r in results:
            slot_obj = by_index.get(r["slot_index"])
            if slot_obj:
                r["label"] = get_display_label(db, slot_obj.id)
    return results
