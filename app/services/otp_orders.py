"""
Pesanan nomor untuk aplikasi otomasi (HP): kunci satu nomor per layanan, tunggu OTP, lalu selesai atau batal.

Aturan:
- Satu nomor (ICCID) hanya dipegang satu pesanan aktif pada satu waktu, lintas akun.
- Nomor yang sudah menerima OTP untuk sebuah layanan tidak diberikan lagi untuk layanan yang sama.
- Hanya SMS yang masuk SETELAH pesanan dibuat, dari layanan itu, dan memuat kode yang dihitung sebagai OTP.
- Pesanan tanpa OTP lewat batas waktu menjadi EXPIRED (nomor bebas lagi); yang sudah dapat OTP menjadi DONE.
"""

from __future__ import annotations

import re
import threading
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Sequence

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.database.models import (
    NumberOrder, OrderStatus, SIMSession, Slot, SlotStatus, SMSMessage, SMSStatus,
)
from app.modem.otp import extract_otp

# Kata yang dicari di pengirim ATAU isi SMS. Layanan lain memakai namanya sendiri sebagai kata kunci.
SERVICES: Dict[str, Sequence[str]] = {
    "whatsapp": ("whatsapp",),
    "telegram": ("telegram",),
    "google": ("google", "g-"),
    "facebook": ("facebook", "fb-"),
    "instagram": ("instagram",),
    "tiktok": ("tiktok",),
    "twitter": ("twitter",),
    "microsoft": ("microsoft",),
    "shopee": ("shopee",),
    "tokopedia": ("tokopedia",),
    "lazada": ("lazada",),
    "gojek": ("gojek", "gopay"),
    "grab": ("grab",),
    "dana": ("dana",),
    "ovo": ("ovo",),
    "linkaja": ("linkaja",),
    "any": (),  # SMS apa pun yang memuat kode OTP
}
SERVICE_NAME = re.compile(r"^[a-z0-9][a-z0-9_-]{1,29}\Z")

DEFAULT_MINUTES = 10
MIN_MINUTES, MAX_MINUTES = 2, 30
SMSC_CLOCK_TOLERANCE = timedelta(minutes=2)
LOCKING = (OrderStatus.ACTIVE, OrderStatus.OTP_RECEIVED)
USED = (OrderStatus.OTP_RECEIVED, OrderStatus.DONE)

_reserve_lock = threading.Lock()  # pilih + kunci nomor harus atomik antar permintaan paralel (satu proses server)


class OrderError(Exception):
    def __init__(self, status_code: int, message: str):
        super().__init__(message)
        self.status_code = status_code
        self.message = message


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(value: Optional[datetime]) -> Optional[datetime]:
    """SQLite mengembalikan datetime tanpa zona; semua waktu di sini UTC."""
    return value.replace(tzinfo=timezone.utc) if value is not None and value.tzinfo is None else value


def clean_service(service: Optional[str]) -> str:
    name = (service or "").strip().lower()
    if not SERVICE_NAME.match(name):
        raise OrderError(400, "Nama layanan harus 2-30 karakter: huruf kecil, angka, - atau _ (mis. whatsapp).")
    return name


def sms_matches_service(service: str, sender: str, body: str) -> bool:
    keywords = SERVICES.get(service, (service,))
    if not keywords:
        return True
    text = f"{sender}\n{body}".lower()
    return any(k in text for k in keywords)


def expire_orders(db: Session) -> None:
    now = _now()
    stale = db.query(NumberOrder).filter(NumberOrder.status.in_(LOCKING), NumberOrder.expires_at < now).all()
    for order in stale:
        order.status = OrderStatus.DONE if order.status == OrderStatus.OTP_RECEIVED else OrderStatus.EXPIRED
        order.finished_at = now
    if stale:
        db.commit()


def _candidates(db: Session, pool_ids: List[str]):
    """Port READY dengan nomor yang sudah diketahui, beserta ICCID-nya, urut modem pool lalu nomor port."""
    rows = db.query(Slot, SIMSession).join(SIMSession, SIMSession.slot_id == Slot.id).filter(
        Slot.pool_id.in_(pool_ids), Slot.status == SlotStatus.READY, SIMSession.ended_at.is_(None),
    ).order_by(Slot.pool_id, Slot.slot_index).all()
    for slot, session in rows:
        sim = session.sim_card
        if sim and sim.phone_number and sim.iccid:
            yield slot, sim


def reserve_number(db: Session, user_id: int, pool_ids: List[str], online_pool_ids: set, service: str,
                   device: Optional[str], minutes: int) -> NumberOrder:
    if not pool_ids:
        raise OrderError(409, "Akun ini belum punya modem pool.")
    if not online_pool_ids:
        raise OrderError(409, "Agent modem pool sedang tidak terhubung, jadi OTP tidak bisa diterima.")
    with _reserve_lock:
        expire_orders(db)
        locked = {row[0] for row in db.query(NumberOrder.iccid).filter(NumberOrder.status.in_(LOCKING)).all()}
        used = {row[0] for row in db.query(NumberOrder.iccid).filter(
            NumberOrder.service == service, NumberOrder.status.in_(USED)).all()}
        last_order = dict(db.query(NumberOrder.iccid, func.max(NumberOrder.created_at)).group_by(NumberOrder.iccid).all())

        free = [(slot, sim) for slot, sim in _candidates(db, [p for p in pool_ids if p in online_pool_ids])
                if sim.iccid not in locked and sim.iccid not in used]
        if not free:
            raise OrderError(409, f"Tidak ada nomor tersedia untuk layanan '{service}'. Semua nomor sedang dipakai, "
                                  "sudah pernah dipakai untuk layanan ini, atau belum terbaca nomornya.")
        # Nomor yang paling lama tidak dipesan didahulukan agar pemakaian merata.
        epoch = datetime.min.replace(tzinfo=timezone.utc)
        slot, sim = min(free, key=lambda pair: _aware(last_order.get(pair[1].iccid)) or epoch)

        now = _now()
        order = NumberOrder(user_id=user_id, slot_id=slot.id, pool_id=slot.pool_id, iccid=sim.iccid,
                            phone_number=sim.phone_number, service=service, device=device,
                            status=OrderStatus.ACTIVE, created_at=now, expires_at=now + timedelta(minutes=minutes))
        db.add(order)
        db.commit()
        return order


def refresh_order(db: Session, order: NumberOrder, after_sms_id: int = 0) -> NumberOrder:
    """Periksa SMS baru untuk pesanan ini. `after_sms_id` dipakai untuk menunggu OTP berikutnya (kirim ulang)."""
    expire_orders(db)
    db.refresh(order)
    if order.status not in LOCKING or order.slot_id is None:
        return order
    # received_at = waktu SMS tersimpan di server; SMS biasa yang tersimpan setelah pesanan dibuat memang baru.
    # Pembacaan PERTAMA sebuah SIM menandai semua isinya PESAN BAWAAN, termasuk OTP yang kebetulan masuk saat itu
    # (SIM baru terdeteksi lalu langsung dipesan). Untuk SMS itu waktu kirim SMSC yang menentukan, dengan toleransi
    # karena jam operator/modem bisa meleset; OTP lama yang masih tersimpan di kartu tetap tidak terbaca.
    since = _aware(order.created_at)
    query = db.query(SMSMessage).filter(
        SMSMessage.slot_id == order.slot_id,
        SMSMessage.status.in_((SMSStatus.RECEIVED, SMSStatus.PESAN_BAWAAN)),
        SMSMessage.received_at >= since, SMSMessage.id > after_sms_id,
    ).order_by(SMSMessage.id.desc()).limit(20)
    for sms in query.all():
        if sms.status == SMSStatus.PESAN_BAWAAN and _aware(sms.smsc_timestamp) < since - SMSC_CLOCK_TOLERANCE:
            continue
        if not sms_matches_service(order.service, sms.sender_raw, sms.body):
            continue
        code = extract_otp(sms.body)
        if code:
            order.status, order.otp_code, order.sms_id = OrderStatus.OTP_RECEIVED, code, sms.id
            db.commit()
            break
    return order


def finish_order(db: Session, order: NumberOrder) -> NumberOrder:
    if order.status in LOCKING:
        order.status = OrderStatus.DONE if order.status == OrderStatus.OTP_RECEIVED else OrderStatus.CANCELLED
        order.finished_at = _now()
        db.commit()
    return order


def cancel_order(db: Session, order: NumberOrder) -> NumberOrder:
    if order.status == OrderStatus.OTP_RECEIVED:
        raise OrderError(409, "OTP sudah diterima; nomor tercatat dipakai. Pakai /finish untuk menyelesaikan.")
    if order.status == OrderStatus.ACTIVE:
        order.status, order.finished_at = OrderStatus.CANCELLED, _now()
        db.commit()
    return order


def order_dict(db: Session, order: NumberOrder) -> dict:
    slot = db.get(Slot, order.slot_id) if order.slot_id else None
    sms = db.get(SMSMessage, order.sms_id) if order.sms_id else None
    expires = _aware(order.expires_at)
    phone = order.phone_number
    return {
        "order_id": order.id,
        "status": order.status.value,
        "service": order.service,
        "phone_number": phone,
        "phone_national": "0" + phone[3:] if phone.startswith("+62") else phone,
        "pool_id": order.pool_id,
        "slot_index": slot.slot_index if slot else None,
        "device": order.device,
        "otp": order.otp_code,
        "sms": {"id": sms.id, "sender": sms.sender_raw, "body": sms.body,
                "received_at": _aware(sms.received_at).isoformat()} if sms else None,
        "created_at": _aware(order.created_at).isoformat(),
        "expires_at": expires.isoformat(),
        "seconds_left": max(0, int((expires - _now()).total_seconds())) if order.status in LOCKING else 0,
    }

