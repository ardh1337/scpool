"""
Router Utama REST API SCPool (Coded By Ardh1337)
Mengagregasikan modul-modul router terisolasi:
- monitoring.py : Status sistem, monitoring slot fisik, dan feed SMS
- hardware.py   : Eksekusi dial USSD, sinkronisasi port fisik, profil operator
- auth.py       : Kredensial, login, registrasi mandiri klien, dan sesi
- admin.py      : Manajemen akun pengguna, modem pool, pengaturan Telegram
- agent.py      : Remote agent websocket gateway status dan distribusi script
"""

from fastapi import APIRouter

from app.api.monitoring import router as monitoring_router
from app.api.hardware import router as hardware_router
from app.api.auth import router as auth_router
from app.api.admin import router as admin_router
from app.api.agent import router as agent_router

router = APIRouter(prefix="/api")

# Registrasi sub-router modular
router.include_router(monitoring_router)
router.include_router(hardware_router)
router.include_router(auth_router)
router.include_router(admin_router)
router.include_router(agent_router)

__all__ = ["router"]
