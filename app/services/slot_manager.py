"""
Layanan Pengelola Slot dan Sesi SIM
Menegakkan:
- I-1: SIM baru tidak mewarisi nomor atau sesi sebelumnya pada slot yang sama.
- I-2: Nomor yang tidak ditemukan diset BELUM DIKETAHUI.
- I-6: Maksimal satu sesi SIM aktif per slot.
"""

from datetime import datetime, timezone
from typing import Optional, Tuple
from sqlalchemy.orm import Session
from app.database.models import (
    Slot, SIMCard, SIMSession, SlotStatus, NumberStatus, NumberSource
)


def handle_sim_inserted(
    db: Session,
    slot_id: int,
    iccid: str,
    phone_number: Optional[str] = None,
    number_source: Optional[NumberSource] = None
) -> Tuple[SIMSession, SIMCard]:
    """
    Dipanggil saat kartu SIM terdeteksi terpasang di slot.
    - Menutup sesi aktif lama jika ada (I-6).
    - Mendaftarkan atau memperbarui kartu SIM baru.
    - Membuat sesi SIM baru yang terisolasi tanpa mewarisi metadata lama (I-1).
    """
    now = datetime.now(timezone.utc)

    slot = db.query(Slot).filter(Slot.id == slot_id).first()
    if not slot:
        raise ValueError(f"Slot dengan ID {slot_id} tidak ditemukan.")

    # Invarian I-6: Pastikan sesi aktif lama pada slot ini ditutup
    active_session = db.query(SIMSession).filter(
        SIMSession.slot_id == slot_id,
        SIMSession.ended_at.is_(None)
    ).first()
    if active_session:
        active_session.ended_at = now

    # Registrasi kartu SIM
    sim_card = db.query(SIMCard).filter(SIMCard.iccid == iccid).first()
    if not sim_card:
        # Invarian I-2: Jika nomor tidak ditemukan, jangan ditebak
        num_status = NumberStatus.DIKETAHUI if phone_number else NumberStatus.BELUM_DIKETAHUI
        sim_card = SIMCard(
            iccid=iccid,
            phone_number=phone_number,
            number_status=num_status,
            number_source=number_source if phone_number else None,
            created_at=now,
            updated_at=now
        )
        db.add(sim_card)
        db.flush()
    else:
        # Update nomor hanya bila ada nomor valid baru yang ditemukan dari CNUM/USSD
        if phone_number and not sim_card.phone_number:
            sim_card.phone_number = phone_number
            sim_card.number_status = NumberStatus.DIKETAHUI
            sim_card.number_source = number_source
            sim_card.updated_at = now

    # Invarian I-1 & I-6: Buat sesi baru murni untuk SIM ini
    new_session = SIMSession(
        slot_id=slot_id,
        sim_card_id=sim_card.id,
        started_at=now,
        ended_at=None
    )
    db.add(new_session)

    slot.status = SlotStatus.READY
    slot.updated_at = now

    db.commit()
    db.refresh(new_session)
    db.refresh(sim_card)
    return new_session, sim_card


def handle_sim_removed(db: Session, slot_id: int) -> None:
    """
    Dipanggil saat kartu SIM dicabut dari slot.
    Menutup sesi aktif dan mengubah status slot menjadi EMPTY (KOSONG).
    """
    now = datetime.now(timezone.utc)
    slot = db.query(Slot).filter(Slot.id == slot_id).first()
    if not slot:
        return

    active_session = db.query(SIMSession).filter(
        SIMSession.slot_id == slot_id,
        SIMSession.ended_at.is_(None)
    ).first()
    if active_session:
        active_session.ended_at = now

    slot.status = SlotStatus.EMPTY
    slot.updated_at = now
    db.commit()
