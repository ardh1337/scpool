"""
Layanan Penomoran Urut (Slot Numbering Service)
Menegakkan Invarian I-3 dan aturan penomoran:
- Dua lapis identitas: ID Port fisik (tetap) vs No. Urut (tampilan).
- Hanya slot dengan SIM terdeteksi yang dinomori (mulai dari 1 tanpa lompatan).
- Slot error tidak diberi nomor urut.
- Nomor urut dibekukan selama operasional; pengurutan ulang hanya lewat fungsi eksplisit.
"""

from datetime import datetime, timezone
from typing import List
from sqlalchemy.orm import Session
from app.database.models import Slot, SlotNumbering, SlotStatus


def renumber_pool_slots(db: Session, pool_id: str) -> List[SlotNumbering]:
    """
    Menyusun ulang penomoran urut untuk modem pool tertentu.
    Hanya slot yang statusnya READY / SIM_PRESENT yang diberi nomor urut 1..N.
    Slot EMPTY / PORT_ERROR dinonaktifkan nomor urutnya.
    """
    now = datetime.now(timezone.utc)

    # 1. Nonaktifkan nomor urut aktif sebelumnya untuk pool ini
    active_numbers = db.query(SlotNumbering).filter(
        SlotNumbering.pool_id == pool_id,
        SlotNumbering.is_active == True
    ).all()
    for num in active_numbers:
        num.is_active = False
        num.revoked_at = now

    db.flush()

    # 2. Ambil semua slot dalam pool, urutkan berdasarkan slot_index fisik
    slots = db.query(Slot).filter(Slot.pool_id == pool_id).order_by(Slot.slot_index).all()

    new_numberings: List[SlotNumbering] = []
    current_seq = 1

    for slot in slots:
        # Hanya slot dengan kartu SIM aktif (READY / SIM_PRESENT) yang dinomori
        if slot.status in (SlotStatus.READY, SlotStatus.SIM_PRESENT):
            num_entry = SlotNumbering(
                pool_id=pool_id,
                slot_id=slot.id,
                sequence_number=current_seq,
                is_active=True,
                assigned_at=now
            )
            db.add(num_entry)
            new_numberings.append(num_entry)
            current_seq += 1

    db.commit()
    return new_numberings


def get_display_label(db: Session, slot_id: int) -> str:
    """
    Mendapatkan label tampilan langsung berbasis Slot Fisik (Slot 1..32).
    """
    slot = db.query(Slot).filter(Slot.id == slot_id).first()
    if not slot:
        return "UNKNOWN"

    if slot.status == SlotStatus.EMPTY:
        return f"Slot {slot.slot_index} (KOSONG)"
    elif slot.status == SlotStatus.PORT_ERROR:
        return f"Slot {slot.slot_index} (PORT_ERROR)"
    return f"Slot {slot.slot_index}"

