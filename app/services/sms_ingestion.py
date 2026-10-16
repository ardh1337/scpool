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
from typing import Optional, Tuple
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError

from app.database.models import (
    SMSMessage, SIMSession, Slot, SMSStatus, EventLog
)
from app.modem.sms_reader import ParsedSMS, compute_body_hash, mask_sensitive


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

    # 1. Cari sesi aktif untuk slot ini
    active_session = db.query(SIMSession).filter(
        SIMSession.slot_id == slot_id,
        SIMSession.ended_at.is_(None)
    ).first()

    # 2. Tentukan status pesan berdasarkan Invarian I-7 dan I-11
    if not active_session:
        # Invarian I-7: Hubungan tidak dapat dipastikan, tandai PERLU DITINJAU
        status = SMSStatus.PERLU_DITINJAU
        session_id = None
        # Invarian I-8: Catat kejadian anomali
        db.add(EventLog(
            timestamp=now,
            level="WARN",
            category="SMS",
            slot_id=slot_id,
            message="SMS diterima tanpa sesi SIM aktif yang jelas. Ditandai PERLU DITINJAU.",
            details=f"Sender: {parsed_sms.sender_raw}, Time: {parsed_sms.smsc_timestamp}"
        ))
    elif is_initial:
        # Invarian I-11: Pesan yang sudah ada saat sesi baru dimulai
        status = SMSStatus.PESAN_BAWAAN
        session_id = active_session.id
    else:
        status = SMSStatus.RECEIVED
        session_id = active_session.id

    body_hash = parsed_sms.body_hash or compute_body_hash(parsed_sms.body)

    # 3. Invarian I-9: Periksa deduplikasi dengan kunci komposit
    existing = db.query(SMSMessage).filter(
        SMSMessage.session_id == session_id,
        SMSMessage.sender_raw == parsed_sms.sender_raw,
        SMSMessage.smsc_timestamp == parsed_sms.smsc_timestamp,
        SMSMessage.modem_index == parsed_sms.index,
        SMSMessage.body_hash == body_hash
    ).first()

    if existing:
        return existing, False

    # 4. Simpan ke database
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
        deleted_from_modem=False  # I-4: Default tidak dihapus
    )

    try:
        db.add(sms)
        db.commit()
        db.refresh(sms)
        return sms, True
    except IntegrityError as err:
        db.rollback()
        # Invarian I-8: Catat kegagalan database
        db.add(EventLog(
            timestamp=now,
            level="ERROR",
            category="SMS",
            slot_id=slot_id,
            message="Gagal menyimpan SMS karena benturan integritas skema DB.",
            details=str(err)
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

