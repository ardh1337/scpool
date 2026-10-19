"""
API untuk aplikasi otomasi (HP): ambil nomor, tunggu OTP, selesai/batal. Juga pengelolaan kunci API.

Kunci API (`X-API-Key: sk_...`) hanya berlaku di endpoint /api/otp/orders dan /api/otp/services, supaya kunci yang
tertanam di HP tidak bisa dipakai mengganti password, menghapus pool, atau fungsi admin.
"""

import asyncio
import secrets
from datetime import datetime, timedelta, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.auth.deps import get_current_user_optional, is_admin, require_user
from app.auth.security import hash_session_token
from app.database.connection import get_db
from app.database.models import ApiKey, ModemPool, NumberOrder, OrderStatus, Slot, UserAccount
from app.services.agent_gateway import agent_gateway, sms_signal
from app.services.otp_orders import (
    DEFAULT_MINUTES, LOCKING, MAX_MINUTES, MIN_MINUTES, SERVICES, OrderError, cancel_order, clean_service,
    finish_order, order_dict, refresh_order, reserve_number,
)
from app.services.units import owned_pool_ids

router = APIRouter()

API_KEY_PREFIX = "sk_"
MAX_KEYS_PER_USER = 20
MAX_WAIT_SECONDS = 60


def _api_key_from(request: Request) -> Optional[str]:
    key = request.headers.get("X-API-Key", "").strip()
    if not key:
        auth = request.headers.get("Authorization", "")
        if auth.startswith(f"Bearer {API_KEY_PREFIX}"):
            key = auth[7:].strip()
    return key if key.startswith(API_KEY_PREFIX) else None


def require_automation_user(request: Request, db: Session = Depends(get_db),
                            session_user: Optional[UserAccount] = Depends(get_current_user_optional)) -> UserAccount:
    """Kunci API, atau sesi login biasa (agar bisa dicoba dari dashboard/Swagger)."""
    key = _api_key_from(request)
    if key:
        row = db.query(ApiKey).filter(ApiKey.key_hash == hash_session_token(key), ApiKey.revoked_at.is_(None)).first()
        user = db.get(UserAccount, row.user_id) if row else None
        if not user or not user.is_active:
            raise HTTPException(401, "Kunci API salah, sudah dicabut, atau akunnya nonaktif.")
        now = datetime.now(timezone.utc)
        last = row.last_used_at.replace(tzinfo=timezone.utc) if row.last_used_at and not row.last_used_at.tzinfo \
            else row.last_used_at
        if last is None or now - last > timedelta(minutes=1):
            row.last_used_at = now
            db.commit()
        return user
    if session_user:
        return session_user
    raise HTTPException(401, "Kirim kunci API di header X-API-Key, atau login terlebih dahulu.")


def _pool_ids(db: Session, user: UserAccount, pool_id: Optional[str]) -> List[str]:
    if pool_id:
        owner = db.query(ModemPool.owner_id).filter(ModemPool.id == pool_id).scalar()
        if owner is None or (owner != user.id and not is_admin(user)):
            raise HTTPException(404, "Modem pool tidak ditemukan.")
        return [pool_id]
    return owned_pool_ids(db, user.id)


def _own_order(db: Session, user: UserAccount, order_id: int) -> NumberOrder:
    order = db.get(NumberOrder, order_id)
    if not order or order.user_id != user.id:
        raise HTTPException(404, "Pesanan tidak ditemukan.")
    return order


def _owner_of(db: Session, order: NumberOrder) -> Optional[int]:
    return db.query(ModemPool.owner_id).filter(ModemPool.id == order.pool_id).scalar() if order.pool_id else None


def _port_of(db: Session, order: NumberOrder) -> Optional[str]:
    slot = db.get(Slot, order.slot_id) if order.slot_id else None
    return slot.physical_port_id if slot else None


async def _stop_watch(db: Session, order: NumberOrder) -> None:
    owner, port = _owner_of(db, order), _port_of(db, order)
    if owner is not None and port:
        await agent_gateway.watch_sms(owner, port, 0)


# ----------------------------------------------------------------------------- layanan & pesanan
@router.get("/otp/services")
def list_services(user: UserAccount = Depends(require_automation_user)):
    return [{"service": name, "keywords": list(words)} for name, words in SERVICES.items()]


class OrderRequest(BaseModel):
    service: str = Field(max_length=30)
    pool_id: Optional[str] = Field(default=None, max_length=50)
    device: Optional[str] = Field(default=None, max_length=50)
    minutes: int = Field(default=DEFAULT_MINUTES, ge=MIN_MINUTES, le=MAX_MINUTES)


@router.post("/otp/orders")
async def create_order(payload: OrderRequest, user: UserAccount = Depends(require_automation_user),
                       db: Session = Depends(get_db)):
    try:
        service = clean_service(payload.service)
        pool_ids = _pool_ids(db, user, payload.pool_id)
        owners = dict(db.query(ModemPool.id, ModemPool.owner_id).filter(ModemPool.id.in_(pool_ids)).all())
        online = {pid for pid, owner in owners.items() if owner is not None and agent_gateway.is_agent_online(owner)}
        device = (payload.device or "").strip() or None
        order = reserve_number(db, user.id, pool_ids, online, service, device, payload.minutes)
    except OrderError as exc:
        raise HTTPException(exc.status_code, exc.message)
    port = _port_of(db, order)
    owner = owners.get(order.pool_id)
    if owner is not None and port:
        await agent_gateway.watch_sms(owner, port, payload.minutes * 60)
    return order_dict(db, order)


@router.get("/otp/orders/{order_id}")
async def get_order(order_id: int, wait: int = Query(0, ge=0, le=MAX_WAIT_SECONDS),
                    after: int = Query(0, ge=0), user: UserAccount = Depends(require_automation_user),
                    db: Session = Depends(get_db)):
    order = _own_order(db, user, order_id)
    deadline = asyncio.get_running_loop().time() + wait
    while True:
        ticket = sms_signal.ticket()
        db.commit()  # tutup transaksi yang mungkin terbuka agar SMS yang baru masuk dari agent ikut terbaca
        order = refresh_order(db, order, after)
        got_new = order.status == OrderStatus.OTP_RECEIVED and (order.sms_id or 0) > after
        if got_new or order.status not in LOCKING or asyncio.get_running_loop().time() >= deadline:
            return order_dict(db, order)
        # Bangun begitu SMS baru tersimpan; batas 2 detik tetap ada agar kedaluwarsa pesanan terdeteksi.
        remaining = deadline - asyncio.get_running_loop().time()
        await sms_signal.wait(ticket, min(2.0, max(remaining, 0.0)))


@router.post("/otp/orders/{order_id}/finish")
async def finish(order_id: int, user: UserAccount = Depends(require_automation_user), db: Session = Depends(get_db)):
    order = finish_order(db, _own_order(db, user, order_id))
    await _stop_watch(db, order)
    return order_dict(db, order)


@router.post("/otp/orders/{order_id}/cancel")
async def cancel(order_id: int, user: UserAccount = Depends(require_automation_user), db: Session = Depends(get_db)):
    try:
        order = cancel_order(db, _own_order(db, user, order_id))
    except OrderError as exc:
        raise HTTPException(exc.status_code, exc.message)
    await _stop_watch(db, order)
    return order_dict(db, order)


@router.get("/otp/orders")
def list_orders(status: Optional[str] = Query(None, description="ACTIVE, OTP_RECEIVED, DONE, CANCELLED, EXPIRED"),
                limit: int = Query(50, ge=1, le=500), user: UserAccount = Depends(require_automation_user),
                db: Session = Depends(get_db)):
    query = db.query(NumberOrder).filter(NumberOrder.user_id == user.id)
    if status:
        try:
            query = query.filter(NumberOrder.status == OrderStatus(status.upper()))
        except ValueError:
            raise HTTPException(400, "Status harus salah satu: ACTIVE, OTP_RECEIVED, DONE, CANCELLED, EXPIRED.")
    return [order_dict(db, o) for o in query.order_by(NumberOrder.id.desc()).limit(limit).all()]


# ----------------------------------------------------------------------------- kunci API (wajib login biasa)
def _key_dict(key: ApiKey) -> dict:
    def iso(value):
        return value.replace(tzinfo=timezone.utc).isoformat() if value and not value.tzinfo else (
            value.isoformat() if value else None)
    return {"id": key.id, "name": key.name, "prefix": key.prefix, "created_at": iso(key.created_at),
            "last_used_at": iso(key.last_used_at), "revoked": key.revoked_at is not None}


@router.get("/otp/keys")
def list_keys(user: UserAccount = Depends(require_user), db: Session = Depends(get_db)):
    keys = db.query(ApiKey).filter(ApiKey.user_id == user.id).order_by(ApiKey.id.desc()).all()
    return [_key_dict(k) for k in keys]


class KeyRequest(BaseModel):
    name: str = Field(min_length=1, max_length=50)


@router.post("/otp/keys")
def create_key(payload: KeyRequest, user: UserAccount = Depends(require_user), db: Session = Depends(get_db)):
    name = payload.name.strip()
    if not name or any(c in name for c in "<>"):
        raise HTTPException(400, "Nama kunci wajib diisi dan tidak boleh memuat < atau >.")
    active = db.query(ApiKey).filter(ApiKey.user_id == user.id, ApiKey.revoked_at.is_(None)).count()
    if active >= MAX_KEYS_PER_USER:
        raise HTTPException(400, f"Maksimal {MAX_KEYS_PER_USER} kunci aktif. Cabut kunci yang tidak dipakai.")
    secret = API_KEY_PREFIX + secrets.token_urlsafe(32)
    key = ApiKey(user_id=user.id, name=name, key_hash=hash_session_token(secret), prefix=secret[:10])
    db.add(key)
    db.commit()
    return {**_key_dict(key), "api_key": secret}


@router.delete("/otp/keys/{key_id}")
def revoke_key(key_id: int, user: UserAccount = Depends(require_user), db: Session = Depends(get_db)):
    key = db.get(ApiKey, key_id)
    if not key or key.user_id != user.id:
        raise HTTPException(404, "Kunci API tidak ditemukan.")
    if key.revoked_at is None:
        key.revoked_at = datetime.now(timezone.utc)
        db.commit()
    return {"success": True, "message": f"Kunci '{key.name}' dicabut dan tidak bisa dipakai lagi."}
