"""
Unit modem pool per akun. Satu akun bisa punya banyak unit (mis. Foxcom 32 port + unit 16 port); jumlah port dan
unitnya ditentukan oleh yang dilaporkan agent, bukan oleh isian saat registrasi. Nama unit bebas diganti.
"""

from __future__ import annotations

import re
import secrets
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.config import MAX_PORTS_PER_POOL, MAX_UNIT_NAME, MAX_UNITS_PER_ACCOUNT
from app.database.models import ModemPool, Slot, SlotNumbering, SlotStatus

_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f]")


class UnitError(ValueError):
    """Pelanggaran aturan unit yang pesannya aman ditampilkan ke pengguna."""


def clean_unit_name(name: Optional[str]) -> str:
    cleaned = _CONTROL_CHARS.sub("", name or "").strip()
    if not cleaned:
        raise UnitError("Nama modem pool tidak boleh kosong.")
    if re.search(r"[<>]", cleaned):
        raise UnitError("Nama modem pool tidak boleh memuat karakter < atau >.")
    if len(cleaned) > MAX_UNIT_NAME:
        raise UnitError(f"Nama modem pool maksimal {MAX_UNIT_NAME} karakter.")
    return cleaned


def owned_pools(db: Session, owner_id: int) -> List[ModemPool]:
    return db.query(ModemPool).filter(ModemPool.owner_id == owner_id).order_by(
        ModemPool.created_at, ModemPool.id).all()


def owned_pool_ids(db: Session, owner_id: int) -> List[str]:
    return [p.id for p in owned_pools(db, owner_id)]


def owner_of_pool(db: Session, pool_id: str) -> Optional[int]:
    return db.query(ModemPool.owner_id).filter(ModemPool.id == pool_id).scalar()


def mark_slots_offline(db: Session, owner_id: Optional[int] = None) -> int:
    """
    Tanpa agent tidak ada yang membaca modem: SIM bisa saja sudah dicabut atau ditukar, jadi status lama tidak boleh
    tampil sebagai aktif. Sesi SIM sengaja dibiarkan terbuka agar SIM yang sama langsung dikenali (tanpa USSD ulang)
    saat agent kembali. owner_id None = semua akun (server baru menyala). Pemanggil yang commit.
    """
    query = db.query(Slot).filter(Slot.status != SlotStatus.OFFLINE)
    if owner_id is not None:
        query = query.filter(Slot.pool_id.in_(db.query(ModemPool.id).filter(ModemPool.owner_id == owner_id)))
    return query.update({Slot.status: SlotStatus.OFFLINE}, synchronize_session=False)


def create_unit(db: Session, owner_id: int, name: Optional[str] = None, group_key: Optional[str] = None) -> ModemPool:
    """Buat unit kosong. Nama bawaan "Modem Pool N" (N = urutan berikutnya) dan bisa diganti kapan saja."""
    count = db.query(func.count(ModemPool.id)).filter(ModemPool.owner_id == owner_id).scalar() or 0
    if count >= MAX_UNITS_PER_ACCOUNT:
        raise UnitError(f"Satu akun maksimal {MAX_UNITS_PER_ACCOUNT} modem pool.")
    pool_id = f"u{owner_id}_{secrets.token_hex(4)}"
    pool = ModemPool(id=pool_id, name=clean_unit_name(name) if name else f"Modem Pool {count + 1}",
                     port_count=0, owner_id=owner_id, group_key=group_key,
                     description="Terdeteksi otomatis dari agent")
    db.add(pool)
    db.flush()
    return pool


def _refresh_port_count(db: Session, pool: ModemPool) -> None:
    pool.port_count = db.query(func.count(Slot.id)).filter(Slot.pool_id == pool.id).scalar() or 0


def _next_slot_index(db: Session, pool_id: str) -> int:
    return (db.query(func.max(Slot.slot_index)).filter(Slot.pool_id == pool_id).scalar() or 0) + 1


def sync_detected_ports(db: Session, owner_id: int, groups: Sequence[Tuple[str, Sequence[str]]]) -> Dict[str, int]:
    """
    Terapkan hasil deteksi agent: groups = [(group_key, [port, ...]), ...] dalam urutan fisik.

    - I-3: port yang sudah punya slot tidak pernah berpindah slot atau unit karena deteksi ulang. Hanya pengguna
      yang boleh memindahkannya.
    - Port baru masuk ke unit dengan group_key sama; bila belum ada, unit baru dibuat otomatis.
    - Port yang hilang dari mesin klien ditandai OFFLINE (bukan dihapus), agar riwayat SMS tetap utuh.
    Pemanggil yang commit. Mengembalikan ringkasan {"new_ports", "new_units", "offline"}.
    """
    pools = owned_pools(db, owner_id)
    pool_ids = [p.id for p in pools]
    slots = db.query(Slot).filter(Slot.pool_id.in_(pool_ids)).all() if pool_ids else []
    by_port: Dict[str, Slot] = {s.physical_port_id: s for s in slots if s.physical_port_id}
    pool_by_id = {p.id: p for p in pools}
    pool_by_key = {p.group_key: p for p in pools if p.group_key}
    free_by_pool: Dict[str, List[Slot]] = {}
    for s in sorted(slots, key=lambda x: x.slot_index):
        if not s.physical_port_id:
            free_by_pool.setdefault(s.pool_id, []).append(s)

    summary = {"new_ports": 0, "new_units": 0, "offline": 0}
    seen: set = set()
    touched: set = set()

    for key, ports in groups:
        # Port baru mengikuti unit tempat sebagian besar port sekelompoknya sudah berada. Ini menghormati
        # pemindahan manual (mis. dua unit digabung) dan membuat agent lama tanpa kunci kelompok tetap
        # menaruh port baru di unit yang sama.
        holders = [by_port[p].pool_id for p in ports if p in by_port]
        anchor = pool_by_id.get(max(set(holders), key=holders.count)) if holders else None
        for port in ports:
            if port in seen:
                continue
            seen.add(port)
            existing = by_port.get(port)
            if existing is not None:
                holder = pool_by_id.get(existing.pool_id)
                if holder is not None and holder.group_key is None and key not in pool_by_key:
                    holder.group_key = key  # unit lama (sebelum fitur ini) mulai ikut kunci kabelnya
                    pool_by_key[key] = holder
                if existing.status in (SlotStatus.UNMAPPED, SlotStatus.OFFLINE):
                    existing.status = SlotStatus.EMPTY
                continue

            pool = anchor or pool_by_key.get(key)
            if pool is None:
                pool = create_unit(db, owner_id, group_key=key)
                pool_by_key[key] = pool
                pool_by_id[pool.id] = pool
                summary["new_units"] += 1
            if db.query(func.count(Slot.id)).filter(Slot.pool_id == pool.id).scalar() >= MAX_PORTS_PER_POOL \
                    and not free_by_pool.get(pool.id):
                continue
            free = free_by_pool.get(pool.id)
            if free:
                slot = free.pop(0)
                slot.physical_port_id = port
            else:
                slot = Slot(pool_id=pool.id, slot_index=_next_slot_index(db, pool.id), physical_port_id=port,
                            status=SlotStatus.EMPTY)
                db.add(slot)
                db.flush()
            slot.status = SlotStatus.EMPTY
            by_port[port] = slot
            touched.add(pool.id)
            summary["new_ports"] += 1

    for port, slot in by_port.items():
        if port not in seen and slot.status not in (SlotStatus.UNMAPPED, SlotStatus.OFFLINE):
            slot.status = SlotStatus.OFFLINE
            summary["offline"] += 1

    db.flush()
    for pool_id in touched:
        _refresh_port_count(db, pool_by_id[pool_id])
    return summary


def rename_unit(db: Session, pool: ModemPool, name: str) -> ModemPool:
    pool.name = clean_unit_name(name)
    return pool


def move_slots(db: Session, owner_id: int, source: ModemPool, slot_indices: Iterable[int],
               target: Optional[ModemPool] = None, new_name: Optional[str] = None) -> ModemPool:
    """
    Pindahkan port (beserta seluruh riwayat SIM/SMS-nya) ke unit lain milik akun yang sama, atau ke unit baru.
    Dipakai bila pengelompokan otomatis salah, mis. dua modem pool lewat satu hub eksternal. Nomor slot di unit
    asal tidak digeser (identitas port tetap, I-3); port tiba di akhir unit tujuan.
    """
    if source.owner_id != owner_id or (target is not None and target.owner_id != owner_id):
        raise UnitError("Modem pool tidak ditemukan.")
    indices = sorted(set(int(i) for i in slot_indices))
    if not indices:
        raise UnitError("Pilih minimal satu port.")
    slots = db.query(Slot).filter(Slot.pool_id == source.id, Slot.slot_index.in_(indices)).order_by(
        Slot.slot_index).all()
    if len(slots) != len(indices):
        raise UnitError("Sebagian port tidak ditemukan di modem pool asal.")

    if target is None:
        target = create_unit(db, owner_id, name=new_name or None)
    elif target.id == source.id:
        raise UnitError("Modem pool tujuan sama dengan asal.")

    current = db.query(func.count(Slot.id)).filter(Slot.pool_id == target.id).scalar() or 0
    if current + len(slots) > MAX_PORTS_PER_POOL:
        raise UnitError(f"Satu modem pool maksimal {MAX_PORTS_PER_POOL} port.")

    slot_ids = [s.id for s in slots]
    db.query(SlotNumbering).filter(SlotNumbering.slot_id.in_(slot_ids)).delete(synchronize_session=False)
    next_index = _next_slot_index(db, target.id)
    for slot in slots:
        slot.pool_id = target.id
        slot.slot_index = next_index
        next_index += 1
    db.flush()
    _refresh_port_count(db, source)
    _refresh_port_count(db, target)
    return target


def delete_empty_unit(db: Session, owner_id: int, pool: ModemPool) -> None:
    if pool.owner_id != owner_id:
        raise UnitError("Modem pool tidak ditemukan.")
    if db.query(func.count(Slot.id)).filter(Slot.pool_id == pool.id).scalar():
        raise UnitError("Modem pool masih memiliki port. Pindahkan semua port ke unit lain dulu.")
    db.query(SlotNumbering).filter(SlotNumbering.pool_id == pool.id).delete(synchronize_session=False)
    db.delete(pool)
