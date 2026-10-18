"""
Pemantau latar belakang modem pool LOKAL: mendeteksi SIM dicabut/dipasang/ditukar, mengambil nomor, dan membaca
SMS baru secara berkala.

Perbaikan dari versi sebelumnya:
- Hanya memindai pool lokal, dan hanya bila hardware lokal diaktifkan. Versi lama memindai SEMUA slot semua tenant
  lewat serial lokal; slot tenant remote (COM milik PC klien) otomatis jadi PORT_ERROR tiap 6 detik dan menimpa
  status yang dilaporkan agent.
- Membaca SMS otomatis (sebelumnya hanya jika tombol "Periksa SMS" ditekan) dengan interval sendiri.
- Pool yang agent-nya sedang online dilewati.
"""

import logging
import threading
import time
from typing import Optional

from app.core.config import settings
from app.database.connection import session_scope
from app.database.models import Slot
from app.services.agent_gateway import agent_gateway
from app.services.hardware_sync import sync_single_slot
from app.services.numbering import renumber_pool_slots
from app.services.sms_service import poll_all_active_slots_sms
from app.services.units import owner_of_pool

logger = logging.getLogger(__name__)

_thread: Optional[threading.Thread] = None
_stop_event = threading.Event()
_lock = threading.Lock()


def _scan_once(db) -> None:
    pool_id = settings.local_pool_id
    owner_id = owner_of_pool(db, pool_id)
    if owner_id is not None and agent_gateway.is_agent_online(owner_id):
        return
    changed = False
    for slot in db.query(Slot).filter(Slot.pool_id == pool_id).order_by(Slot.slot_index).all():
        if _stop_event.is_set():
            return
        result = sync_single_slot(db, slot, timeout=1.0, auto_fetch_number=True)
        changed = changed or result.get("session_action") in ("NEW_SESSION", "CLOSED_SESSION")
    if changed:
        renumber_pool_slots(db, pool_id)


def _loop(interval: float) -> None:
    next_sms_poll = 0.0
    while not _stop_event.is_set():
        try:
            with session_scope() as db:
                _scan_once(db)
                if time.time() >= next_sms_poll and not _stop_event.is_set():
                    poll_all_active_slots_sms(db, pool_id=settings.local_pool_id)
                    next_sms_poll = time.time() + settings.sms_poll_interval
        except Exception:
            logger.exception("Siklus pemindaian hardware gagal")
        _stop_event.wait(interval)


def start_auto_detector(interval: float = 6.0) -> None:
    global _thread
    with _lock:
        if _thread is not None and _thread.is_alive():
            return
        _stop_event.clear()
        _thread = threading.Thread(target=_loop, args=(interval,), name="AutoSIMDetectorThread", daemon=True)
        _thread.start()


def stop_auto_detector() -> None:
    global _thread
    with _lock:
        _stop_event.set()
        if _thread is not None and _thread.is_alive():
            _thread.join(timeout=5.0)
        _thread = None


def is_auto_detector_running() -> bool:
    return _thread is not None and _thread.is_alive()
