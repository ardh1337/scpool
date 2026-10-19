"""Operasi akun & modem pool yang dipakai bersama oleh API admin dan persetujuan via Telegram."""

from __future__ import annotations

import re
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.auth.security import generate_agent_token, hash_password
from app.core.config import ADMIN_ROLES, ROLE_OPERATOR
from app.database.models import (
    ApiKey, Device, EventLog, ModemPool, NumberOrder, Port, SIMCard, SIMSession, Slot, SlotNumbering, SMSMessage,
    SMSPart, UserAccount, UserSession,
)


def is_admin_account(user: UserAccount) -> bool:
    return user.role in ADMIN_ROLES or (user.username or "").lower() == "admin"


def create_operator_account(db: Session, username: str, password: str, role: str, is_active: bool) -> UserAccount:
    """
    Akun baru. Tidak ada modem pool di sini: unit dan jumlah port terbentuk otomatis begitu agent pertama kali
    terhubung (services/units.py). Pemanggil yang commit.
    """
    user = UserAccount(
        username=username, password=hash_password(password), role=role or ROLE_OPERATOR,
        agent_token=generate_agent_token(), is_active=is_active,
    )
    db.add(user)
    return user


def approve_account(db: Session, user: UserAccount) -> None:
    user.is_active = True
    user.updated_at = datetime.now(timezone.utc)
    db.commit()


def purge_pool(db: Session, pool_id: str) -> None:
    """
    Hapus pool beserta seluruh data turunannya (slot, sesi, SMS, event, port, SIM yatim) dalam urutan yang
    memenuhi foreign key. Versi lama hanya menghapus ModemPool sehingga gagal (IntegrityError) begitu ada
    SMS/sesi tersimpan. Pemanggil memastikan tidak ada akun yang masih memakai pool ini.
    """
    slot_ids = [row[0] for row in db.query(Slot.id).filter(Slot.pool_id == pool_id).all()]
    if slot_ids:
        sim_ids = {row[0] for row in db.query(SIMSession.sim_card_id).filter(
            SIMSession.slot_id.in_(slot_ids), SIMSession.sim_card_id.isnot(None)).all()}
        sms_ids = [row[0] for row in db.query(SMSMessage.id).filter(SMSMessage.slot_id.in_(slot_ids)).all()]
        if sms_ids:
            db.query(SMSPart).filter(SMSPart.sms_message_id.in_(sms_ids)).delete(synchronize_session=False)
        db.query(SMSMessage).filter(SMSMessage.slot_id.in_(slot_ids)).delete(synchronize_session=False)
        db.query(EventLog).filter(EventLog.slot_id.in_(slot_ids)).delete(synchronize_session=False)
        db.query(SlotNumbering).filter(SlotNumbering.slot_id.in_(slot_ids)).delete(synchronize_session=False)
        db.query(SIMSession).filter(SIMSession.slot_id.in_(slot_ids)).delete(synchronize_session=False)
        db.query(Slot).filter(Slot.id.in_(slot_ids)).delete(synchronize_session=False)
        db.flush()
        for sim_id in sim_ids:  # SIM yang tidak lagi punya sesi di mana pun ikut dibersihkan
            if not db.query(SIMSession).filter(SIMSession.sim_card_id == sim_id).first():
                db.query(SIMCard).filter(SIMCard.id == sim_id).delete(synchronize_session=False)
    db.query(SlotNumbering).filter(SlotNumbering.pool_id == pool_id).delete(synchronize_session=False)
    db.query(Port).filter(Port.pool_id == pool_id).delete(synchronize_session=False)
    db.query(Device).filter(Device.pool_id == pool_id).delete(synchronize_session=False)
    db.query(ModemPool).filter(ModemPool.id == pool_id).delete(synchronize_session=False)


def delete_account(db: Session, target: UserAccount, protected_pool_id: str) -> None:
    """Hapus akun beserta seluruh unit miliknya (dan pool lama bila tak dipakai akun lain). Satu transaksi atomik."""
    legacy_pool_id = target.pool_id
    owned = [p.id for p in db.query(ModemPool).filter(ModemPool.owner_id == target.id).all()]
    db.query(UserSession).filter(UserSession.user_id == target.id).delete(synchronize_session=False)
    db.query(ApiKey).filter(ApiKey.user_id == target.id).delete(synchronize_session=False)
    db.query(NumberOrder).filter(NumberOrder.user_id == target.id).delete(synchronize_session=False)
    db.delete(target)
    db.flush()
    for pool_id in owned:
        if pool_id != protected_pool_id:
            purge_pool(db, pool_id)
    if legacy_pool_id and legacy_pool_id not in owned and legacy_pool_id != protected_pool_id:
        if db.query(UserAccount).filter(UserAccount.pool_id == legacy_pool_id).count() == 0:
            purge_pool(db, legacy_pool_id)
    db.commit()


_MD_SPECIALS = re.compile(r"([_*`\[\]])")


def md_escape(text: str) -> str:
    """Escape karakter Markdown (Telegram legacy) pada teks buatan pengguna agar notifikasi tidak gagal terkirim."""
    return _MD_SPECIALS.sub(r"\\\1", text or "")
