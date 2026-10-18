"""Modem pool (unit) milik akun: daftar, ganti nama, pindahkan port antar unit, hapus unit kosong."""

from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.auth.deps import is_admin, require_user
from app.database.connection import get_db
from app.database.models import ModemPool, Slot, SlotStatus, UserAccount
from app.services.agent_gateway import agent_gateway
from app.services.units import (
    UnitError, delete_empty_unit, move_slots, owned_pools, rename_unit,
)

router = APIRouter()


def _visible_pools(db: Session, user: UserAccount) -> List[ModemPool]:
    if is_admin(user):
        return db.query(ModemPool).order_by(ModemPool.created_at, ModemPool.id).all()
    return owned_pools(db, user.id)


def _own_pool_or_404(db: Session, user: UserAccount, pool_id: str) -> ModemPool:
    pool = db.query(ModemPool).filter(ModemPool.id == pool_id).first()
    if not pool or not (is_admin(user) or pool.owner_id == user.id):
        raise HTTPException(404, "Modem pool tidak ditemukan.")
    return pool


def _pool_dict(pool: ModemPool, counts: dict, owners: Optional[dict] = None) -> dict:
    by_status = counts.get(pool.id, {})
    return {
        "id": pool.id,
        "name": pool.name,
        "port_count": sum(by_status.values()),
        "ready_count": by_status.get(SlotStatus.READY, 0),
        "offline_count": by_status.get(SlotStatus.OFFLINE, 0) + by_status.get(SlotStatus.UNMAPPED, 0),
        "auto_detected": bool(pool.group_key),
        "agent_online": bool(pool.owner_id and agent_gateway.is_agent_online(pool.owner_id)),
        "owner": owners.get(pool.owner_id) if owners else None,
    }


@router.get("/pools")
def list_pools(user: UserAccount = Depends(require_user), db: Session = Depends(get_db)):
    pools = _visible_pools(db, user)
    counts: dict = {}
    if pools:
        rows = db.query(Slot.pool_id, Slot.status, func.count(Slot.id)).filter(
            Slot.pool_id.in_([p.id for p in pools])).group_by(Slot.pool_id, Slot.status).all()
        for pool_id, status, n in rows:
            counts.setdefault(pool_id, {})[status] = n
    owners = dict(db.query(UserAccount.id, UserAccount.username).all()) if is_admin(user) else None
    return [_pool_dict(p, counts, owners) for p in pools]


class RenameRequest(BaseModel):
    name: str = Field(max_length=200)


@router.put("/pools/{pool_id}")
def rename_pool(pool_id: str, payload: RenameRequest, user: UserAccount = Depends(require_user),
                db: Session = Depends(get_db)):
    pool = _own_pool_or_404(db, user, pool_id)
    try:
        rename_unit(db, pool, payload.name)
    except UnitError as exc:
        raise HTTPException(400, str(exc))
    db.commit()
    return {"success": True, "id": pool.id, "name": pool.name}


class MovePortsRequest(BaseModel):
    slot_indices: List[int] = Field(min_length=1, max_length=64)
    target_pool_id: Optional[str] = Field(default=None, max_length=50)
    new_pool_name: Optional[str] = Field(default=None, max_length=200)


@router.post("/pools/{pool_id}/move-ports")
def move_ports(pool_id: str, payload: MovePortsRequest, user: UserAccount = Depends(require_user),
               db: Session = Depends(get_db)):
    """Pindahkan port ke unit lain atau ke unit baru (isi `target_pool_id` ATAU `new_pool_name`)."""
    source = _own_pool_or_404(db, user, pool_id)
    if source.owner_id is None:
        raise HTTPException(400, "Modem pool ini belum memiliki pemilik akun.")
    if bool(payload.target_pool_id) == bool(payload.new_pool_name):
        raise HTTPException(400, "Isi salah satu: target_pool_id atau new_pool_name.")
    try:
        target = _own_pool_or_404(db, user, payload.target_pool_id) if payload.target_pool_id else None
        if target is not None and target.owner_id != source.owner_id:
            raise HTTPException(404, "Modem pool tidak ditemukan.")
        result = move_slots(db, source.owner_id, source, payload.slot_indices, target=target,
                            new_name=payload.new_pool_name)
    except UnitError as exc:
        db.rollback()
        raise HTTPException(400, str(exc))
    db.commit()
    return {"success": True, "target_pool_id": result.id, "target_pool_name": result.name,
            "moved": len(set(payload.slot_indices))}


@router.delete("/pools/{pool_id}")
def delete_pool(pool_id: str, user: UserAccount = Depends(require_user), db: Session = Depends(get_db)):
    """Hapus unit yang sudah kosong (setelah semua port dipindahkan). Unit berisi port tidak bisa dihapus."""
    pool = _own_pool_or_404(db, user, pool_id)
    try:
        delete_empty_unit(db, pool.owner_id, pool)
    except UnitError as exc:
        raise HTTPException(400, str(exc))
    db.commit()
    return {"success": True}
