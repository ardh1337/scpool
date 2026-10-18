"""
Pembacaan SMS dari modem lokal dan penyimpanannya ke DB.

- I-4: tidak ada penghapusan dari kartu.
- I-9 / I-11: dedup dan penandaan PESAN BAWAAN ditangani `ingest_sms_batch` (dipakai juga oleh jalur agent).
"""

import logging
from typing import List, Optional, Tuple

from sqlalchemy.orm import Session

from app.database.models import SMSMessage, Slot, SlotStatus
from app.modem.at_client import ATClient
from app.modem.port_lock import PortBusyError, port_access
from app.modem.sms_reader import parse_cmgl_text_response
from app.modem.transport import SerialTransport
from app.services.events import log_event
from app.services.sms_ingestion import ingest_sms_batch

logger = logging.getLogger(__name__)


def poll_slot_sms(db: Session, slot_id: int) -> List[Tuple[SMSMessage, bool]]:
    """Baca semua SMS pada kartu di slot lokal dan simpan ke DB. Mengembalikan [(SMS, is_new)]."""
    slot = db.query(Slot).filter(Slot.id == slot_id).first()
    if not slot or slot.status != SlotStatus.READY or not slot.physical_port_id:
        return []

    port_name = slot.physical_port_id
    try:
        with port_access(port_name, blocking=False):
            client = ATClient(
                transport=SerialTransport(port=port_name, baudrate=115200, timeout=1.0),
                default_timeout=5.0,
            )
            try:
                client.open()
                client.send_command('AT+CSCS="IRA"', timeout=1.0)
                client.send_command("AT+CMGF=1", timeout=1.0)
                res = client.send_command('AT+CMGL="ALL"', timeout=8.0)
                if not res.success:
                    log_event(db, "WARN", "SMS", "Pembacaan SMS (AT+CMGL) gagal.", slot_id=slot.id,
                              port_id=port_name, details=res.error_message,
                              dedupe_key=f"cmgl:{slot.id}", dedupe_seconds=600)
                    return []
                return ingest_sms_batch(db, slot, parse_cmgl_text_response(res.lines))
            finally:
                try:
                    client.close()
                except Exception:
                    pass
    except PortBusyError:
        return []  # dipakai operasi lain; siklus berikutnya mencoba lagi
    except Exception as exc:
        db.rollback()
        logger.warning("Polling SMS slot %s gagal: %s", slot.slot_index, type(exc).__name__)
        log_event(db, "ERROR", "SMS", "Polling SMS gagal.", slot_id=slot.id, port_id=port_name,
                  details=repr(exc), dedupe_key=f"poll:{slot.id}", dedupe_seconds=600)
        return []


def poll_all_active_slots_sms(db: Session, pool_id: Optional[str] = None) -> int:
    """Pindai SMS pada semua slot READY (opsional satu pool). Mengembalikan jumlah pesan baru."""
    query = db.query(Slot).filter(Slot.status == SlotStatus.READY)
    if pool_id:
        query = query.filter(Slot.pool_id == pool_id)
    total_new = 0
    for slot in query.all():
        total_new += sum(1 for _, is_new in poll_slot_sms(db, slot.id) if is_new)
    return total_new
