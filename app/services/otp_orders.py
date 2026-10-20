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
from typing import Dict, List, Optional, Sequence, Tuple

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.database.models import (
    NumberOrder, OrderStatus, SIMCard, SIMSession, Slot, SlotStatus, SMSMessage, SMSStatus,
)
from app.modem.otp import extract_otp

# Kata yang dicari di pengirim ATAU isi SMS. Layanan lain memakai namanya sendiri sebagai kata kunci.
SERVICES: Dict[str, Sequence[str]] = {
    "whatsapp": ("whatsapp",),
    "whatsapp_business": ("whatsapp business",),
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
# Kata yang membatalkan kecocokan: SMS WhatsApp Business juga memuat kata "whatsapp".
EXCLUDE: Dict[str, Sequence[str]] = {"whatsapp": ("whatsapp business",)}
DISPLAY_NAMES = {"whatsapp": "WhatsApp", "whatsapp_business": "WhatsApp Business", "tiktok": "TikTok", "linkaja": "LinkAja", "dana": "DANA", "ovo": "OVO",
                 "any": "Semua (SMS apa pun)"}
SERVICE_NAME = re.compile(r"^[a-z0-9][a-z0-9_-]{1,29}\Z")

DEFAULT_MINUTES = 10
MIN_MINUTES, MAX_MINUTES = 2, 30
SMSC_CLOCK_TOLERANCE = timedelta(minutes=2)
LOCKING = (OrderStatus.ACTIVE, OrderStatus.OTP_RECEIVED)
USED = (OrderStatus.OTP_RECEIVED, OrderStatus.DONE)

# Alasan gagal yang dilaporkan aplikasi HP dan dampaknya ke pemberian nomor untuk layanan yang sama.
FAIL_REASONS = {
    "no_sms": "OTP tidak datang",
    "banned": "Tameng merah (nomor diblokir)",
    "registered_elsewhere": "Akun aktif di perangkat lain",
    "under_review": "Sedang ditinjau (banding)",
    "limited": "Dibatasi sementara (coba lagi nanti)",
    "wrong_code": "Kode ditolak",
    "other": "Lainnya",
}
# Hasil yang hanya DITANDAI di statistik; nomornya tetap boleh diberikan lagi (keputusan pemilik, 20 Okt 2026).
MARK_ONLY = ("banned", "registered_elsewhere", "under_review")
LIMITED_BLOCK = timedelta(hours=24)        # "limited": nomor istirahat sehari
NO_SMS_WINDOW = timedelta(hours=24)        # "no_sms" (termasuk EXPIRED) sebanyak NO_SMS_LIMIT kali dalam jendela ini
NO_SMS_LIMIT = 2                           # -> nomor istirahat sampai jendela lewat

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
    return any(k in text for k in keywords) and not any(k in text for k in EXCLUDE.get(service, ()))


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


def _is_no_sms(order: NumberOrder) -> bool:
    return order.status == OrderStatus.EXPIRED or order.fail_reason == "no_sms"


def blocked_numbers(db: Session, service: str) -> Dict[str, Tuple[str, Optional[datetime]]]:
    """ICCID yang tidak boleh diberikan untuk layanan ini -> (alasan, sampai kapan; None = selamanya)."""
    now = _now()
    blocked: Dict[str, Tuple[str, Optional[datetime]]] = {}
    # Terpakai = berhasil. Selesai dengan hasil gagal (mis. ditinjau setelah OTP diisi) tidak mengunci nomor.
    used = db.query(NumberOrder.iccid).filter(
        NumberOrder.service == service, NumberOrder.status.in_(USED),
        (NumberOrder.result.is_(None)) | (NumberOrder.result == "success")).distinct()
    for iccid, in used:
        blocked[iccid] = ("used", None)
    recent = db.query(NumberOrder).filter(
        NumberOrder.service == service, NumberOrder.created_at >= now - max(LIMITED_BLOCK, NO_SMS_WINDOW),
        NumberOrder.status.in_((OrderStatus.CANCELLED, OrderStatus.EXPIRED)),
    ).order_by(NumberOrder.created_at).all()
    no_sms: Dict[str, List[datetime]] = {}
    for order in recent:
        when = _aware(order.created_at)
        if order.fail_reason == "limited" and order.iccid not in blocked:
            blocked[order.iccid] = ("limited", when + LIMITED_BLOCK)
        elif _is_no_sms(order):
            no_sms.setdefault(order.iccid, []).append(when)
    for iccid, times in no_sms.items():
        times = [t for t in times if t >= now - NO_SMS_WINDOW]
        if len(times) >= NO_SMS_LIMIT and iccid not in blocked:
            blocked[iccid] = ("no_sms", times[-NO_SMS_LIMIT] + NO_SMS_WINDOW)
    return blocked


def free_numbers(db: Session, pool_ids: List[str], online_pool_ids: set, service: str):
    """(slot, sim) yang bisa diberikan untuk layanan ini sekarang: online, tidak dikunci, belum terpakai/diblokir."""
    locked = {row[0] for row in db.query(NumberOrder.iccid).filter(NumberOrder.status.in_(LOCKING)).all()}
    blocked = blocked_numbers(db, service)
    return [(slot, sim) for slot, sim in _candidates(db, [p for p in pool_ids if p in online_pool_ids])
            if sim.iccid not in locked and sim.iccid not in blocked]


def reserve_number(db: Session, user_id: int, pool_ids: List[str], online_pool_ids: set, service: str,
                   device: Optional[str], minutes: int) -> NumberOrder:
    if not pool_ids:
        raise OrderError(409, "Akun ini belum punya modem pool.")
    if not online_pool_ids:
        raise OrderError(409, "Agent modem pool sedang tidak terhubung, jadi OTP tidak bisa diterima.")
    with _reserve_lock:
        expire_orders(db)
        free = free_numbers(db, pool_ids, online_pool_ids, service)
        last_order = dict(db.query(NumberOrder.iccid, func.max(NumberOrder.created_at)).group_by(NumberOrder.iccid).all())
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
    # SMS harus milik KARTU pesanan ini (lewat sesi SIM-nya), bukan sekadar port: bila SIM dicabut dan diganti
    # selama pesanan, OTP untuk SIM pengganti tidak boleh terbaca sebagai OTP nomor ini.
    sessions = db.query(SIMSession.id).join(SIMCard, SIMCard.id == SIMSession.sim_card_id).filter(
        SIMSession.slot_id == order.slot_id, SIMCard.iccid == order.iccid)
    query = db.query(SMSMessage).filter(
        SMSMessage.slot_id == order.slot_id, SMSMessage.session_id.in_(sessions),
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


def clean_reason(reason: Optional[str]) -> Optional[str]:
    if reason is None or reason == "":
        return None
    if reason not in FAIL_REASONS:
        raise OrderError(400, "Alasan harus salah satu: " + ", ".join(FAIL_REASONS) + ".")
    return reason


def finish_order(db: Session, order: NumberOrder, result: Optional[str] = None,
                 reason: Optional[str] = None) -> NumberOrder:
    """Selesai. `result` "failed" + `reason` dipakai bila OTP sudah masuk tapi tetap gagal (mis. kode ditolak)."""
    if result not in (None, "success", "failed"):
        raise OrderError(400, "Hasil harus 'success' atau 'failed'.")
    reason = clean_reason(reason)
    if order.status in LOCKING:
        got_otp = order.status == OrderStatus.OTP_RECEIVED
        order.status = OrderStatus.DONE if got_otp else OrderStatus.CANCELLED
        order.result = result or ("success" if got_otp and not reason else "failed")
        order.fail_reason = reason if order.result == "failed" else None
        if order.result == "failed" and not order.fail_reason and not got_otp:
            order.fail_reason = "no_sms"
        order.finished_at = _now()
        db.commit()
    return order


def cancel_order(db: Session, order: NumberOrder, reason: Optional[str] = None) -> NumberOrder:
    reason = clean_reason(reason)
    if order.status == OrderStatus.OTP_RECEIVED:
        raise OrderError(409, "OTP sudah diterima; nomor tercatat dipakai. Pakai /finish untuk menyelesaikan.")
    if order.status == OrderStatus.ACTIVE:
        order.status, order.finished_at = OrderStatus.CANCELLED, _now()
        order.result, order.fail_reason = ("failed", reason) if reason else (None, None)
        db.commit()
    return order


def number_stats(db: Session, user_id: int, service: str) -> List[dict]:
    """Rekap per nomor untuk satu layanan: berapa kali berhasil/gagal dan apakah sedang bisa diberikan."""
    blocked = blocked_numbers(db, service)
    rows: Dict[str, dict] = {}
    for order in db.query(NumberOrder).filter(NumberOrder.user_id == user_id, NumberOrder.service == service) \
            .order_by(NumberOrder.created_at):
        row = rows.setdefault(order.iccid, {"phone_number": order.phone_number, "attempts": 0, "success": 0,
                                            "failed": 0, "reasons": {}, "flags": [], "last_result": None,
                                            "last_reason": None, "last_at": None})
        row["phone_number"] = order.phone_number
        row["attempts"] += 1
        if order.status in LOCKING:
            outcome = "active"
        elif order.result == "success" or (order.status == OrderStatus.DONE and order.result is None):
            outcome = "success"
        elif order.result == "failed" or order.status == OrderStatus.EXPIRED:
            outcome = "failed"
            reason = order.fail_reason or "no_sms"
            row["reasons"][reason] = row["reasons"].get(reason, 0) + 1
            if reason in MARK_ONLY and reason not in row["flags"]:
                row["flags"].append(reason)
        else:
            outcome = "cancelled"
        if outcome in ("success", "failed"):
            row[outcome] += 1
        row["last_result"], row["last_at"] = outcome, _aware(order.created_at).isoformat()
        row["last_reason"] = (order.fail_reason or "no_sms") if outcome == "failed" else None
    out = []
    for iccid, row in rows.items():
        reason, until = blocked.get(iccid, (None, None))
        out.append({**row, "available": iccid not in blocked, "blocked_reason": reason,
                    "blocked_until": until.isoformat() if until else None})
    return sorted(out, key=lambda r: r["last_at"] or "", reverse=True)


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
        "result": order.result,
        "fail_reason": order.fail_reason,
        "sms": {"id": sms.id, "sender": sms.sender_raw, "body": sms.body,
                "received_at": _aware(sms.received_at).isoformat()} if sms else None,
        "created_at": _aware(order.created_at).isoformat(),
        "expires_at": expires.isoformat(),
        "seconds_left": max(0, int((expires - _now()).total_seconds())) if order.status in LOCKING else 0,
    }

