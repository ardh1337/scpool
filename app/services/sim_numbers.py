"""
Penetapan nomor telepon SIM dan kebijakan percobaan ulang pengambilan nomor otomatis.

I-2: nomor tidak pernah ditebak. Bila USSD berbalas tanpa nomor, status menjadi NUMBER_NOT_RETURNED dan
pengambilan otomatis berhenti; dial manual tetap bisa menimpa.
"""

from __future__ import annotations

import threading
import time
from datetime import datetime, timezone
from typing import Dict, Optional, Tuple

from sqlalchemy.orm import Session

from app.database.models import NumberSource, NumberStatus, OperatorProfile, SIMCard
from app.hardware.operators import default_profiles, match_operator


def operator_name_for_iccid(db: Session, iccid: Optional[str]) -> Optional[str]:
    profiles = db.query(OperatorProfile).all() or default_profiles()
    matched = match_operator(profiles, iccid, None)
    return matched.name if matched else None


def ussd_code_for_sim(db: Session, iccid: Optional[str], operator_name: Optional[str]) -> str:
    from app.modem.ussd import get_default_ussd_for_iccid
    return get_default_ussd_for_iccid(iccid, operator_name=operator_name, db=db)


def apply_ussd_result(db: Session, sim: SIMCard, success: bool, phone: Optional[str],
                      source: NumberSource = NumberSource.USSD) -> None:
    """Terapkan hasil USSD ke kartu SIM. Pemanggil yang commit."""
    now = datetime.now(timezone.utc)
    if success and phone:
        sim.phone_number = phone
        sim.number_status = NumberStatus.DIKETAHUI
        sim.number_source = source
        sim.updated_at = now
    elif success and sim.number_status != NumberStatus.DIKETAHUI:
        sim.number_status = NumberStatus.NUMBER_NOT_RETURNED
        sim.updated_at = now


def apply_cnum_result(db: Session, sim: SIMCard, phone: Optional[str]) -> bool:
    if not phone or sim.number_status == NumberStatus.DIKETAHUI:
        return False
    sim.phone_number = phone
    sim.number_status = NumberStatus.DIKETAHUI
    sim.number_source = NumberSource.CNUM
    sim.updated_at = datetime.now(timezone.utc)
    return True


def needs_auto_fetch(sim: Optional[SIMCard]) -> bool:
    return bool(sim) and sim.number_status == NumberStatus.BELUM_DIKETAHUI and not sim.phone_number


class FetchBackoff:
    """Jeda eksponensial per ICCID agar SIM yang gagal dial USSD tidak diserang ulang tiap siklus pemindaian."""

    def __init__(self, base: float = 60.0, cap: float = 900.0):
        self._base, self._cap = base, cap
        self._state: Dict[str, Tuple[float, float]] = {}
        self._lock = threading.Lock()

    def allowed(self, key: str) -> bool:
        with self._lock:
            entry = self._state.get(key)
            return entry is None or time.time() >= entry[0]

    def record_failure(self, key: str) -> None:
        with self._lock:
            delay = min(self._state.get(key, (0, self._base / 2))[1] * 2, self._cap)
            self._state[key] = (time.time() + delay, delay)

    def record_success(self, key: str) -> None:
        with self._lock:
            self._state.pop(key, None)

    def reset(self) -> None:
        with self._lock:
            self._state.clear()


fetch_backoff = FetchBackoff()
