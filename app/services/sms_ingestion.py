"""
Layanan Ingesti SMS Masuk
Menegakkan:
- I-4: SMS divalidasi tersimpan di DB sebelum aksi penghapusan di kartu dapat dijalankan.
- I-7: Hubungan tidak pasti berstatus PERLU DITINJAU.
- I-8: Tidak ada data hilang diam-diam (catat kegagalan ke EventLog).
- I-9: Deduplikasi SMS berbasis kunci komposit.
- I-11: Pesan awal di kartu ditandai PESAN BAWAAN.
"""

from datetime import datetime, timezone
from typing import Iterable, List, Optional, Tuple
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError

from app.database.models import (
    SMSMessage, SIMSession, Slot, SMSStatus, EventLog
)
from app.modem.sms_reader import ParsedSMS, compute_body_hash


def ingest_sms(
    db: Session,
    slot_id: int,
    parsed_sms: ParsedSMS,
    is_initial: bool = False
) -> Tuple[Optional[SMSMessage], bool]:
    """
    Menyimpan SMS yang diterima ke database.
    Mengembalikan: (SMSMessage, is_new: bool)
    """
    now = datetime.now(timezone.utc)

    active_session = db.query(SIMSession).filter(
        SIMSession.slot_id == slot_id,
        SIMSession.ended_at.is_(None)
    ).first()
    session_id = active_session.id if active_session else None
    body_hash = parsed_sms.body_hash or compute_body_hash(parsed_sms.body)

    # I-9: dedup dengan kunci komposit. session_id NULL tidak bisa dibandingkan dengan "=" di SQL, sehingga
    # versi lama menyimpan pesan tanpa sesi berulang kali di tiap polling; gunakan IS NULL secara eksplisit.
    session_match = (
        SMSMessage.session_id.is_(None) if session_id is None else SMSMessage.session_id == session_id
    )
    existing = db.query(SMSMessage).filter(
        session_match,
        SMSMessage.slot_id == slot_id,
        SMSMessage.sender_raw == parsed_sms.sender_raw,
        SMSMessage.smsc_timestamp == parsed_sms.smsc_timestamp,
        SMSMessage.modem_index == parsed_sms.index,
        SMSMessage.body_hash == body_hash
    ).first()
    if existing:
        return existing, False

    if active_session is None:
        status = SMSStatus.PERLU_DITINJAU  # I-7
        db.add(EventLog(
            timestamp=now, level="WARN", category="SMS", slot_id=slot_id,
            message="SMS diterima tanpa sesi SIM aktif yang jelas. Ditandai PERLU DITINJAU.",
            details=f"Waktu SMSC: {parsed_sms.smsc_timestamp}",  # I-8; pengirim/isi tidak dicatat (I-12)
        ))
    elif is_initial:
        status = SMSStatus.PESAN_BAWAAN  # I-11
    else:
        status = SMSStatus.RECEIVED

    sms = SMSMessage(
        session_id=session_id,
        slot_id=slot_id,
        sender_raw=parsed_sms.sender_raw,
        sender_normalized=parsed_sms.sender_normalized,
        smsc_timestamp=parsed_sms.smsc_timestamp,
        received_at=now,
        body=parsed_sms.body,
        body_hash=body_hash,
        modem_index=parsed_sms.index,
        status=status,
        is_multipart=parsed_sms.is_multipart,
        part_count=parsed_sms.total_parts,
        deleted_from_modem=False  # I-4: default tidak dihapus
    )

    try:
        db.add(sms)
        db.commit()
        db.refresh(sms)
        return sms, True
    except IntegrityError as err:
        db.rollback()
        db.add(EventLog(
            timestamp=now, level="ERROR", category="SMS", slot_id=slot_id,
            message="Gagal menyimpan SMS karena benturan integritas skema DB.",
            details=str(err.orig)[:200]  # I-8
        ))
        db.commit()
        return None, False


def can_delete_sms_from_modem(db: Session, sms_id: int) -> bool:
    """
    Invarian I-4: SMS tidak boleh dihapus dari modem sebelum tersimpan dan tervalidasi di DB.
    """
    sms = db.query(SMSMessage).filter(SMSMessage.id == sms_id).first()
    if not sms:
        return False
    # Validasi: pesan tersimpan dan memiliki ID primer yang valid
    return bool(sms.id and sms.body_hash)



def ingest_sms_batch(db: Session, slot: Slot, parsed_list: Iterable[ParsedSMS]) -> List[Tuple[SMSMessage, bool]]:
    """
    Simpan hasil pembacaan penuh kartu (AT+CMGL); dipakai bersama oleh jalur hardware lokal dan agent remote.
    Pembacaan pertama pada sesi SIM aktif menandai pesan yang sudah ada sebagai PESAN BAWAAN (I-11); pembacaan
    berikutnya hanya menambah pesan baru. Dedup (I-9) membuat pemanggilan berulang aman.
    """
    session = db.query(SIMSession).filter(
        SIMSession.slot_id == slot.id, SIMSession.ended_at.is_(None)
    ).first()
    is_initial = bool(session and session.initial_scan_at is None)

    saved: List[Tuple[SMSMessage, bool]] = []
    for parsed in parsed_list:
        sms_obj, is_new = ingest_sms(db=db, slot_id=slot.id, parsed_sms=parsed, is_initial=is_initial)
        if sms_obj:
            saved.append((sms_obj, is_new))

    if session is not None and is_initial:
        session.initial_scan_at = datetime.now(timezone.utc)
        db.commit()
    return saved
