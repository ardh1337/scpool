"""
Aplikasi Utama FastAPI SIM Inventory & SMS Dashboard (Tahap M8)
Menjalankan server web lokal untuk dashboard Foxcom 32-Port.
"""

from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware

from app.database.models import init_db
from app.api.routes import router
from app.services.auto_detector import start_auto_detector, stop_auto_detector


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    start_auto_detector(interval=6.0)
    yield
    stop_auto_detector()


app = FastAPI(
    title="SCPool - SIM Inventory & SMS Dashboard",
    description="Sistem Pengelolaan Modem Pool Foxcom 32-Port & SMS Hub — Coded By Ardh1337",
    version="1.0.0",
    lifespan=lifespan
)

# CORS middleware untuk akses lokal aman dan domain tunnel terpercaya
app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"^https?://(localhost|127\.0\.0\.1|.*\.ardh1337\.cloud)(:\d+)?$",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.middleware("http")
async def add_signature_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Application"] = "SCPool"
    response.headers["X-Signature"] = "Coded By Ardh1337"
    return response


# Registrasi API endpoints
app.include_router(router)

WEB_DIR = Path(__file__).resolve().parent / "web"
WEB_DIR.mkdir(parents=True, exist_ok=True)

# Sajikan file web statis
app.mount("/static", StaticFiles(directory=str(WEB_DIR)), name="static")


@app.websocket("/ws/agent")
async def websocket_agent_endpoint(websocket: WebSocket, token: str = ""):
    """Endpoint WebSocket untuk koneksi dua arah dengan remote agent.exe."""
    from app.database.connection import get_db, SessionLocal
    from app.database.models import UserAccount
    from app.services.agent_gateway import agent_gateway

    if not token:
        token = websocket.query_params.get("token", "")

    if app.dependency_overrides.get(get_db):
        db_gen = app.dependency_overrides[get_db]()
        db = next(db_gen)
        should_close = False
    else:
        db = SessionLocal()
        should_close = True

    pool_id = None
    try:
        user = db.query(UserAccount).filter(UserAccount.agent_token == token).first() if token else None
        if not user or not user.is_active:
            await websocket.close(code=1008)
            return

        await websocket.accept()
        pool_id = user.pool_id or f"pool_{user.username}"
        await agent_gateway.register(pool_id, websocket, user)

        await websocket.send_text(
            '{"type":"welcome","pool_id":"' + pool_id + '","username":"' + user.username + '"}'
        )

        while True:
            msg_text = await websocket.receive_text()
            await agent_gateway.handle_agent_message(pool_id, msg_text, db=db)

    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        if pool_id:
            agent_gateway.unregister(pool_id)
        if should_close:
            db.close()



@app.get("/")
def serve_dashboard():
    index_path = WEB_DIR / "index.html"
    if index_path.exists():
        return FileResponse(str(index_path))
    return {"message": "SIM Inventory API aktif. File antarmuka web sedang disiapkan."}


@app.get("/download/agent")
def direct_download_agent(request: Request):
    from fastapi.responses import RedirectResponse
    query_str = f"?{request.url.query}" if request.url.query else ""
    return RedirectResponse(url=f"/api/download/agent{query_str}")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.main:app", host="127.0.0.1", port=8000, reload=True)
