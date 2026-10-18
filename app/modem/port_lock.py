"""
Kunci per-port serial untuk seluruh proses (I-5: satu perintah AT per port pada satu waktu).

ATClient hanya mengunci instansinya sendiri, sementara endpoint API, auto-detector, dan polling SMS masing-masing
membuat ATClient baru. Tanpa kunci lintas-instansi, dua pemakai yang membuka COM yang sama bersamaan membuat salah
satunya gagal (PermissionError di Windows) dan slot salah ditandai PORT_ERROR.
"""

from __future__ import annotations

import threading
from contextlib import contextmanager
from typing import Dict


class PortBusyError(RuntimeError):
    """Port sedang dipakai operasi lain."""


_guard = threading.Lock()
_locks: Dict[str, threading.RLock] = {}


def _lock_for(port_name: str) -> threading.RLock:
    key = port_name.strip().upper()
    with _guard:
        lock = _locks.get(key)
        if lock is None:
            lock = _locks[key] = threading.RLock()
        return lock


@contextmanager
def port_access(port_name: str, timeout: float = 20.0, blocking: bool = True):
    """Akses eksklusif ke port. `blocking=False` untuk pemindai latar belakang yang boleh melewati port sibuk."""
    lock = _lock_for(port_name)
    acquired = lock.acquire(timeout=timeout) if blocking else lock.acquire(blocking=False)
    if not acquired:
        raise PortBusyError(f"Port {port_name} sedang dipakai operasi lain.")
    try:
        yield
    finally:
        lock.release()
