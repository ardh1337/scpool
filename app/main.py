"""
Aplikasi Utama FastAPI SIM Inventory & SMS Dashboard (Tahap M8)
Menjalankan server web lokal untuk dashboard Foxcom 32-Port.
"""

from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware

from app.database.models import init_db
from app.api.routes import router


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


app = FastAPI(
    title="SIM Inventory & SMS Dashboard",
    description="Sistem Pengelolaan Modem Pool Foxcom 32-Port & SMS Hub",
    version="1.0.0",
    lifespan=lifespan
)

# CORS middleware untuk akses lokal aman
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Registrasi API endpoints
app.include_router(router)

WEB_DIR = Path(__file__).resolve().parent / "web"
WEB_DIR.mkdir(parents=True, exist_ok=True)

# Sajikan file web statis
app.mount("/static", StaticFiles(directory=str(WEB_DIR)), name="static")



@app.get("/")
def serve_dashboard():
    index_path = WEB_DIR / "index.html"
    if index_path.exists():
        return FileResponse(str(index_path))
    return {"message": "SIM Inventory API aktif. File antarmuka web sedang disiapkan."}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.main:app", host="127.0.0.1", port=8000, reload=True)
