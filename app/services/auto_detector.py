"""
Layanan Pemantau Otomatis Deteksi SIM & Auto-Dial USSD (Tahap M5)
Memantau slot kosong dan slot yang belum memiliki nomor telepon secara berkala.
Saat kartu SIM baru terdeteksi di suatu port:
1. Otomatis mendaftarkan sesi SIM baru (I-1, I-6).
2. Otomatis menjalankan dial USSD untuk mengambil nomor telepon (I-2).
3. Memperbarui status kartu menjadi DIKETAHUI di database SQLite.
"""

import threading
import time
from typing import Optional
from sqlalchemy.orm import Session

from app.database.connection import SessionLocal
from app.database.models import Slot, SlotStatus, SIMSession, SIMCard
from app.services.hardware_sync import sync_single_slot

_thread: Optional[threading.Thread] = None
_stop_event = threading.Event()
_is_running = False
_lock = threading.Lock()


def _auto_detector_loop(interval: float = 6.0):
    global _is_running
    _is_running = True

    while not _stop_event.is_set():
        try:
            db: Session = SessionLocal()
            try:
                # Cari slot yang:
                # 1. KOSONG (mungkin baru dipasangi kartu SIM secara fisik)
                # 2. READY namun nomornya masih BELUM DIKETAHUI
                slots_to_check = db.query(Slot).filter(
                    (Slot.status == SlotStatus.EMPTY) |
                    (
                        (Slot.status == SlotStatus.READY) &
                        Slot.sessions.any(
                            (SIMSession.ended_at.is_(None)) &
                            SIMSession.sim_card.has(SIMCard.phone_number.is_(None))
                        )
                    )
                ).order_by(Slot.slot_index).all()

                for slot in slots_to_check:
                    if _stop_event.is_set():
                        break

                    # Jalankan sinkronisasi dengan auto-fetch nomor USSD
                    sync_single_slot(db, slot, timeout=1.0, auto_fetch_number=True)
                    # Berikan jeda kecil antar-port agar bus serial tenang
                    time.sleep(0.5)

            finally:
                db.close()

        except Exception:
            pass

        # Tunggu interval atau sampai sinyal stop diterima
        _stop_event.wait(interval)

    _is_running = False


def start_auto_detector(interval: float = 6.0):
    """Memulai pemantau latar belakang otomatis."""
    global _thread, _stop_event, _is_running
    with _lock:
        if _thread is not None and _thread.is_alive():
            return

        _stop_event.clear()
        _thread = threading.Thread(
            target=_auto_detector_loop,
            args=(interval,),
            name="AutoSIMDetectorThread",
            daemon=True
        )
        _thread.start()


def stop_auto_detector():
    """Menghentikan pemantau latar belakang otomatis."""
    global _thread, _stop_event
    with _lock:
        _stop_event.set()
        if _thread is not None and _thread.is_alive():
            _thread.join(timeout=3.0)
            _thread = None


def is_auto_detector_running() -> bool:
    """Status apakah auto detector sedang berjalan."""
    return _is_running and _thread is not None and _thread.is_alive()
