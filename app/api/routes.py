"""Router utama REST API SCPool: menggabungkan sub-router per domain."""

from fastapi import APIRouter

from app.api.admin import router as admin_router
from app.api.agent import router as agent_router
from app.api.auth import router as auth_router
from app.api.hardware import router as hardware_router
from app.api.monitoring import router as monitoring_router
from app.api.pools import router as pools_router

router = APIRouter(prefix="/api")
for sub in (monitoring_router, pools_router, hardware_router, auth_router, admin_router, agent_router):
    router.include_router(sub)

__all__ = ["router"]
