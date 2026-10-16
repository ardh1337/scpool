"""
Layanan Penomoran Urut (Slot Numbering Service)
Menegakkan Invarian I-3 dan aturan penomoran:
- Dua lapis identitas: ID Port fisik (tetap) vs No. Urut (tampilan).
- Hanya slot dengan SIM terdeteksi yang dinomori (mulai dari 1 tanpa lompatan).
- Slot error tidak diberi nomor urut.
- Nomor urut dibekukan selama operasional; pengurutan ulang hanya lewat fungsi eksplisit.
"""

from datetime import datetime, timezone
from typing import List, Dict
from sqlalchemy.orm import Session
from app.database.models import Slot, SlotNumbering, SlotStatus, ModemPool


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
    Mendapatkan label tampilan untuk UI.
    Invarian I-3 & Bagian 6:
    - Nomor urut dibekukan saat SIM dicabut (tampil No. X (KOSONG)).
    - Slot tanpa nomor berstatus BELUM BERNOMOR atau KOSONG.
    """
    slot = db.query(Slot).filter(Slot.id == slot_id).first()
    if not slot:
        return "UNKNOWN"

    active_num = db.query(SlotNumbering).filter(
        SlotNumbering.slot_id == slot_id,
        SlotNumbering.is_active == True
    ).first()

    if active_num:
        if slot.status == SlotStatus.EMPTY:
            return f"No. {active_num.sequence_number} (KOSONG)"
        elif slot.status == SlotStatus.PORT_ERROR:
            return f"No. {active_num.sequence_number} (PORT_ERROR)"
        return f"No. {active_num.sequence_number}"
    elif slot.status == SlotStatus.EMPTY:
        return "KOSONG"
    elif slot.status == SlotStatus.PORT_ERROR:
        return "PORT_ERROR"
    else:
        return "BELUM BERNOMOR"

