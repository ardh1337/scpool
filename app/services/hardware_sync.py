"""
Sinkronisasi Status Perangkat Keras dan Manajemen Sesi SIM (Tahap M3 & M4)
Memindai status fisik seluruh port serial pada modem pool, memperbarui status slot,
mengelola siklus hidup sesi SIM di database (I-1, I-6), dan menerapkan penomoran urut (I-3).
"""

from datetime import datetime, timezone
from typing import Dict, List, Optional
from sqlalchemy.orm import Session

from app.database.models import (
    ModemPool, Slot, Port, SIMCard, SIMSession, SlotStatus, EventLog,
    NumberStatus, NumberSource
)
from app.modem.transport import SerialTransport
from app.modem.at_client import ATClient
from app.modem.sim_status import parse_cpin_response, parse_iccid_response
from app.modem.ussd import execute_ussd, get_default_ussd_for_iccid
from app.services.slot_manager import handle_sim_inserted, handle_sim_removed
from app.services.numbering import renumber_pool_slots, get_display_label
from app.services.pool_init import ensure_pool_initialized
from config.modem_pool_profile import FOXCOM_32_MAPPING, get_slot_for_port


def sync_single_slot(
    db: Session,
    slot: Slot,
    timeout: float = 1.0,
    auto_fetch_number: bool = False
) -> Dict[str, str]:
    """
    Memindai satu slot fisik tertentu dan memperbarui status sesi di DB.
    """
    now = datetime.now(timezone.utc)
    port_name = slot.physical_port_id
    res = {
        "slot_index": slot.slot_index,
        "port": port_name,
        "status": "OFFLINE",
        "iccid": "-",
        "session_action": "NONE",
        "label": "-"
    }

    if not port_name:
        slot.status = SlotStatus.UNMAPPED
        db.commit()
        return res

    transport = SerialTransport(port=port_name, baudrate=115200, timeout=timeout)
    client = ATClient(transport=transport, default_timeout=timeout)

    try:
        client.open()

        # 1. Ping AT
        r_at = client.send_command("AT", timeout=0.8)
        if not r_at.success:
            slot.status = SlotStatus.PORT_ERROR
            slot.updated_at = now
            db.commit()
            res["status"] = "PORT_ERROR"
            res["label"] = get_display_label(db, slot.id)
            return res

        # 2. Cek Status SIM
        r_cpin = client.send_command("AT+CPIN?", timeout=1.0)
        cpin_status = parse_cpin_response(r_cpin)

        if cpin_status == "READY":
            # Baca ICCID
            r_iccid = client.send_command("AT+QCCID", timeout=1.0)
            iccid = parse_iccid_response(r_iccid)

            if not iccid:
                # Gagal baca ICCID padahal SIM ready
                slot.status = SlotStatus.SIM_ERROR
                slot.updated_at = now
                db.commit()
                res["status"] = "SIM_ERROR"
                res["label"] = get_display_label(db, slot.id)
                return res

            res["iccid"] = iccid

            # Cek apakah slot ini sudah punya sesi aktif dengan kartu yang sama
            active_session = db.query(SIMSession).filter(
                SIMSession.slot_id == slot.id,
                SIMSession.ended_at.is_(None)
            ).first()

            sim_card = None
            if active_session and active_session.sim_card and active_session.sim_card.iccid == iccid:
                # Sesi aktif masih valid dan sama
                slot.status = SlotStatus.READY
                slot.updated_at = now
                res["status"] = "READY"
                res["session_action"] = "EXISTING"
                sim_card = active_session.sim_card
            else:
                # Invarian I-1 & I-6: Buka sesi baru untuk SIM ini
                new_session, sim_card = handle_sim_inserted(
                    db=db,
                    slot_id=slot.id,
                    iccid=iccid
                )
                res["status"] = "READY"
                res["session_action"] = "NEW_SESSION"

            # Auto dial USSD untuk mengambil nomor jika SIM belum memiliki nomor (I-2, I-5)
            if auto_fetch_number and sim_card and (not sim_card.phone_number or sim_card.number_status != NumberStatus.DIKETAHUI):
                ussd_code = get_default_ussd_for_iccid(iccid)
                success, text, phone = execute_ussd(client, ussd_code=ussd_code, timeout=12.0)
                if success and phone:
                    sim_card.phone_number = phone
                    sim_card.number_status = NumberStatus.DIKETAHUI
                    sim_card.number_source = NumberSource.USSD
                    db.commit()
                    res["phone_number"] = phone

        elif cpin_status == "NOT_INSERTED":
            # Kartu tidak terpasang
            if slot.status != SlotStatus.EMPTY:
                handle_sim_removed(db=db, slot_id=slot.id)
                res["session_action"] = "CLOSED_SESSION"
            slot.status = SlotStatus.EMPTY
            res["status"] = "EMPTY"

        else:
            # SIM error (PIN locked / error)
            slot.status = SlotStatus.SIM_ERROR
            res["status"] = "SIM_ERROR"

        slot.updated_at = now
        db.commit()

    except Exception as e:
        slot.status = SlotStatus.PORT_ERROR
        slot.updated_at = now
        db.commit()
        res["status"] = "PORT_ERROR"

    finally:
        try:
            client.close()
        except Exception:
            pass

    res["label"] = get_display_label(db, slot.id)
    return res


def sync_modem_pool_hardware(
    db: Session,
    pool_id: str = "foxcom_32",
    auto_renumber: bool = True,
    auto_fetch_number: bool = False
) -> List[Dict[str, str]]:
    """
    Melakukan sinkronisasi penuh seluruh 32 port modem pool dengan hardware nyata.
    - Menginisialisasi pool jika belum ada.
    - Memindai setiap port serial fisik.
    - Mengupdate sesi SIM dan status slot.
    - Menyusun ulang nomor urut jika auto_renumber=True.
    - Mengambil nomor via USSD secara otomatis jika auto_fetch_number=True.
    """
    ensure_pool_initialized(db, pool_id=pool_id)

    slots = db.query(Slot).filter(Slot.pool_id == pool_id).order_by(Slot.slot_index).all()
    results = []

    for slot in slots:
        slot_res = sync_single_slot(db, slot, auto_fetch_number=auto_fetch_number)
        results.append(slot_res)

    if auto_renumber:
        renumber_pool_slots(db, pool_id=pool_id)
        # Refresh label tampilan setelah renumber
        for r in results:
            slot_obj = next((s for s in slots if s.slot_index == r["slot_index"]), None)
            if slot_obj:
                r["label"] = get_display_label(db, slot_obj.id)

    return results

