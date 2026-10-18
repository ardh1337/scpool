"""
Aplikasi FastAPI SCPool - SIM Inventory & SMS Dashboard.

Menyajikan REST API, dashboard statis, dan gateway WebSocket untuk agent remote.
"""

import json
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from app.api.routes import router
from app.auth.security import auth_rate_limiter, get_client_ip, is_https_request
from app.core.config import settings
from app.database.bootstrap import init_db
from app.database.connection import SessionLocal, get_db, session_scope
from app.database.models import UserAccount
from app.services.agent_gateway import agent_gateway
from app.services import local_bridge
from app.services.units import mark_slots_offline
from app.services.telegram_service import telegram_poller

logging.basicConfig(level=getattr(logging, settings.log_level, logging.INFO),
                    format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("scpool")

APP_VERSION = "1.1.0"
WEB_DIR = Path(__file__).resolve().parent / "web"
WS_AUTH_FAILURES_PER_MINUTE = 20


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    with session_scope() as db:  # belum ada agent yang tersambung: status lama belum tentu benar
        mark_slots_offline(db)
        db.commit()
    if settings.local_hardware:
        local_bridge.start()
    else:
        logger.info("Hardware lokal nonaktif (SCPOOL_LOCAL_HARDWARE); modem dilayani lewat agent remote.")
    telegram_poller.start()
    try:
        yield
    finally:
        telegram_poller.stop()
        await local_bridge.stop()


# 'unsafe-inline' dibutuhkan karena dashboard memakai handler onclick/style inline; sisanya dikunci.
CSP = ("default-src 'self'; script-src 'self' 'unsafe-inline'; "
       "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; font-src https://fonts.gstatic.com; "
       "img-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'self'; "
       "form-action 'self'; frame-ancestors 'none'")

app = FastAPI(
    title="SCPool - SIM Inventory & SMS Dashboard",
    description="Sistem pengelolaan modem pool Foxcom 32-port & SMS hub",
    version=APP_VERSION,
    lifespan=lifespan,
    docs_url=None, redoc_url=None, openapi_url=None,
)

app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=settings.cors_origin_regex,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "same-origin")
    response.headers.setdefault("Content-Security-Policy", CSP)
    if is_https_request(request):
        response.headers.setdefault("Strict-Transport-Security", "max-age=31536000")
    if request.url.path.startswith("/api/"):
        response.headers.setdefault("Cache-Control", "no-store")
    return response


app.include_router(router)
WEB_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/static", StaticFiles(directory=str(WEB_DIR)), name="static")


def _open_ws_session():
    """Sesi DB untuk autentikasi WebSocket. Menghormati dependency override (dipakai pengujian)."""
    override = app.dependency_overrides.get(get_db)
    if override:
        gen = override()
        return next(gen), False
    return SessionLocal(), True


def _ws_token(websocket: WebSocket) -> str:
    auth = websocket.headers.get("authorization", "")
    if auth.startswith("Bearer "):
        return auth[7:].strip()
    return websocket.query_params.get("token", "")


@app.websocket("/ws/agent")
async def websocket_agent_endpoint(websocket: WebSocket):
    """Koneksi dua arah dengan agent remote; diautentikasi lewat agent token."""
    token = _ws_token(websocket)
    client_ip = get_client_ip(websocket)

    db, owns_session = _open_ws_session()
    try:
        user = db.query(UserAccount).filter(UserAccount.agent_token == token).first() if token else None
        if not (user and user.is_active):
            within_limit = auth_rate_limiter.is_allowed(f"ws-auth:{client_ip}", WS_AUTH_FAILURES_PER_MINUTE, 60)
            await websocket.close(code=1008 if within_limit else 1013)
            return
        identity = SimpleNamespace(id=user.id, username=user.username)
        owner_id = user.id
    finally:
        if owns_session:
            db.close()

    # Produksi: sesi DB dibuat per pesan di dalam gateway. Hanya pengujian (dependency override) berbagi satu sesi.
    shared_db = None if owns_session else db

    await websocket.accept()
    await agent_gateway.register(owner_id, websocket, identity)
    try:
        await websocket.send_text(json.dumps({"type": "welcome", "username": identity.username}))
        while True:
            text = await websocket.receive_text()
            await agent_gateway.handle_agent_message(owner_id, text, db=shared_db)
    except WebSocketDisconnect:
        pass
    except Exception:
        logger.exception("Koneksi agent akun %s berakhir karena error", owner_id)
    finally:
        agent_gateway.unregister(owner_id, websocket)


@app.get("/api/health", include_in_schema=False)
def health():
    return {"status": "ok", "version": APP_VERSION, "local_hardware": settings.local_hardware,
            "detector_running": local_bridge.is_running()}


@app.get("/")
def serve_dashboard():
    index_path = WEB_DIR / "index.html"
    if index_path.exists():
        return FileResponse(str(index_path), headers={"Cache-Control": "no-cache"})
    return JSONResponse({"message": "SIM Inventory API aktif. File antarmuka web belum tersedia."})


@app.get("/download/agent", include_in_schema=False)
def direct_download_agent(request: Request):
    query = f"?{request.url.query}" if request.url.query else ""
    return RedirectResponse(url=f"/api/download/agent{query}")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.main:app", host="127.0.0.1", port=8000)
