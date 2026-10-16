"""
Layanan Pembaca dan Pengelola SMS Masuk (Tahap M6)
Menegakkan:
- I-4: Pesan divalidasi dan disimpan di DB (penghapusan kartu non-aktif secara default).
- I-9: Deduplikasi berbasis composite key (session_id, sender_raw, smsc_timestamp, index, body_hash).
- I-11: Pesan lama yang sudah ada di kartu saat sesi dimulai ditandai PESAN BAWAAN.
"""

from datetime import datetime, timezone
from typing import List, Tuple, Optional
from sqlalchemy.orm import Session

from app.database.models import Slot, SMSMessage, SMSStatus, SlotStatus
from app.modem.transport import SerialTransport
from app.modem.at_client import ATClient
from app.modem.sms_reader import parse_cmgl_text_response, ParsedSMS
from app.services.sms_ingestion import ingest_sms


def poll_slot_sms(
    db: Session,
    slot_id: int,
    is_initial_scan: bool = False
) -> List[Tuple[SMSMessage, bool]]:
    """
    Membaca pesan SMS dari kartu SIM pada slot tertentu dan menyimpannya ke DB.
    Mengembalikan daftar (SMSMessage, is_new: bool).
    """
    slot = db.query(Slot).filter(Slot.id == slot_id).first()
    if not slot or slot.status != SlotStatus.READY or not slot.physical_port_id:
        return []

    port_name = slot.physical_port_id
    transport = SerialTransport(port=port_name, baudrate=115200, timeout=1.0)
    client = ATClient(transport=transport, default_timeout=5.0)

    saved_messages = []

    try:
        client.open()
        # Set format SMS ke mode teks
        client.send_command('AT+CMGF=1', timeout=1.0)

        # Baca semua SMS dari penyimpanan memori kartu SIM
        res = client.send_command('AT+CMGL="ALL"', timeout=5.0)
        if not res.success:
            return []

        parsed_list = parse_cmgl_text_response(res.lines)

        for parsed in parsed_list:
            # Invarian I-11: Jika scan awal sesi baru, tandai sebagai PESAN BAWAAN
            sms_obj, is_new = ingest_sms(
                db=db,
                slot_id=slot.id,
                parsed_sms=parsed,
                is_initial=is_initial_scan
            )
            if sms_obj:
                saved_messages.append((sms_obj, is_new))

    except Exception:
        pass
    finally:
        try:
            client.close()
        except Exception:
            pass

    return saved_messages


def poll_all_active_slots_sms(db: Session, is_initial: bool = False) -> int:
    """Memindai SMS pada semua slot yang sedang aktif (READY)."""
    active_slots = db.query(Slot).filter(Slot.status == SlotStatus.READY).all()
    total_new = 0

    for slot in active_slots:
        results = poll_slot_sms(db=db, slot_id=slot.id, is_initial_scan=is_initial)
        new_in_slot = sum(1 for _, is_new in results if is_new)
        total_new += new_in_slot

    return total_new
