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


def _auto_detector_loop(interval: float = 4.0):
    global _is_running
    _is_running = True

    while not _stop_event.is_set():
        try:
            db: Session = SessionLocal()
            try:
                # Periksa seluruh 32 slot untuk mendeteksi:
                # 1. SIM dicabut: status READY -> EMPTY, sesi ditutup, nomor langsung hilang
                # 2. SIM dipasang: status EMPTY -> READY, sesi dibuka, auto-dial USSD aktif
                # 3. SIM tertukar: sesi lama ditutup, sesi baru dibuka
                slots = db.query(Slot).order_by(Slot.slot_index).all()

                for slot in slots:
                    if _stop_event.is_set():
                        break

                    sync_single_slot(db, slot, timeout=1.0, auto_fetch_number=True)

                # Proses tombol callback persetujuan Telegram (jika ada pendaftaran baru)
                try:
                    from app.services.telegram_service import process_telegram_updates
                    process_telegram_updates(db)
                except Exception:
                    pass

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

