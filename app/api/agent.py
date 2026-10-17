"""
Endpoint API Remote Agent Gateway & Distribusi Script
Menyediakan status koneksi websocket remote agent dan penyajian file client agent untuk operator.
"""

from typing import Optional
from pathlib import Path
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import PlainTextResponse, FileResponse
from sqlalchemy.orm import Session

from app.database.connection import get_db
from app.database.models import UserAccount
from app.auth.deps import require_user, get_current_user_optional
from app.services.telegram_service import get_setting

router = APIRouter()


@router.get("/agent/status")
def get_agent_status(
    pool_id: Optional[str] = Query(None),
    user: UserAccount = Depends(require_user)
):
    """Mengembalikan status online remote agent untuk pool tertentu."""
    from app.services.agent_gateway import agent_gateway
    target_pool = pool_id
    if user.role not in ["superadmin", "admin"]:
        target_pool = user.pool_id
    if not target_pool:
        target_pool = "foxcom_32"

    is_online = agent_gateway.is_agent_online(target_pool)
    info = agent_gateway.get_agent_info(target_pool)
    return {
        "pool_id": target_pool,
        "is_online": is_online,
        "info": info
    }


@router.get("/download/agent")
def download_agent_file(
    file_type: str = Query("script", description="Jenis file: 'script' (agent_forwarder.py) atau 'bat' (run_agent.bat)"),
    token: Optional[str] = Query(None, description="Agent token opsional untuk unduhan langsung pasca-registrasi"),
    user: Optional[UserAccount] = Depends(get_current_user_optional),
    db: Session = Depends(get_db)
):
    """Mengunduh file agent_forwarder.py atau script run_agent.bat yang sudah dikonfigurasi dengan token pengguna."""
    target_token = None
    if token and token.strip():
        matched_user = db.query(UserAccount).filter(UserAccount.agent_token == token.strip()).first()
        if not matched_user:
            raise HTTPException(status_code=403, detail="Agent token tidak valid.")
        target_token = matched_user.agent_token
    elif user:
        target_token = user.agent_token
    else:
        raise HTTPException(status_code=401, detail="Otentikasi atau token diperlukan untuk mengunduh file agen.")

    domain = get_setting("public_domain", "https://scpool.ardh1337.cloud", db=db).rstrip("/")
    ws_domain = domain.replace("https://", "wss://").replace("http://", "ws://") + "/ws/agent"
    active_token = target_token or "GANTI_DENGAN_TOKEN_ANDA"

    if file_type == "bat":
        bat_content = (
            "@echo off\r\n"
            "title SCPool - Remote Modem Agent\r\n"
            "echo ======================================================\r\n"
            "echo           SCPOOL REMOTE MODEM POOL AGENT               \r\n"
            "echo           Coded By Ardh1337                            \r\n"
            "echo ======================================================\r\n"
            "echo Menghubungkan modem pool lokal ke server SaaS...\r\n"
            f"python agent_forwarder.py --server {ws_domain} --token {active_token}\r\n"
            "if errorlevel 1 (\r\n"
            "    echo.\r\n"
            "    echo [ERROR] Agent terhenti atau python/pyserial belum terpasang.\r\n"
            "    echo Jalankan: pip install pyserial websockets\r\n"
            ")\r\n"
            "pause\r\n"
        )
        return PlainTextResponse(
            bat_content,
            media_type="application/x-bat",
            headers={"Content-Disposition": "attachment; filename=run_agent.bat"}
        )

    agent_path = Path(__file__).resolve().parent.parent.parent / "tools" / "agent_forwarder.py"
    if agent_path.exists():
        return FileResponse(
            str(agent_path),
            filename="agent_forwarder.py",
            media_type="text/x-python",
            headers={"Content-Disposition": "attachment; filename=agent_forwarder.py"}
        )
    raise HTTPException(status_code=404, detail="File agent_forwarder.py tidak ditemukan.")

