"""
Pencatatan kejadian sistem ke tabel event_logs (I-8: tidak ada data hilang diam-diam).

I-12: teks log umum tidak boleh memuat isi SMS, nomor lengkap, atau ICCID lengkap; semua teks bebas dilewatkan
`mask_sensitive` sebelum disimpan.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Dict, Optional

from sqlalchemy.orm import Session

from app.database.models import EventLog
from app.modem.sms_reader import mask_sensitive

logger = logging.getLogger(__name__)

_recent: Dict[str, float] = {}
_recent_lock = threading.Lock()


def _suppressed(key: Optional[str], window: float) -> bool:
    if not key or window <= 0:
        return False
    now = time.time()
    with _recent_lock:
        last = _recent.get(key)
        if last is not None and now - last < window:
            return True
        _recent[key] = now
        if len(_recent) > 2048:
            for k in [k for k, t in _recent.items() if now - t > 3600]:
                _recent.pop(k, None)
    return False


def log_event(db: Session, level: str, category: str, message: str, *, slot_id: Optional[int] = None,
              port_id: Optional[str] = None, details: Optional[str] = None,
              dedupe_key: Optional[str] = None, dedupe_seconds: float = 0.0) -> None:
    """Simpan EventLog tanpa pernah menggagalkan alur utama. Kejadian berulang dapat diredam via dedupe_key."""
    if _suppressed(dedupe_key, dedupe_seconds):
        return
    try:
        db.add(EventLog(
            level=level[:10], category=category[:20], slot_id=slot_id, port_id=port_id,
            message=mask_sensitive(message)[:255],
            details=mask_sensitive(details) if details else None,
        ))
        db.commit()
    except Exception:
        db.rollback()
        logger.exception("Gagal menyimpan EventLog")
