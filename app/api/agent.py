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
    active_token = target_token or "scpool_UNCONFIGURED"

    agent_path = Path(__file__).resolve().parent.parent.parent / "tools" / "agent_forwarder.py"
    if not agent_path.exists():
        raise HTTPException(status_code=404, detail="File tools/agent_forwarder.py tidak ditemukan di server.")

    raw_python_code = agent_path.read_text(encoding="utf-8")

    # Injeksi token dan domain server secara dinamis ke dalam file kode
    configured_code = raw_python_code.replace(
        'DEFAULT_SERVER = "wss://scpool.ardh1337.cloud/ws/agent"',
        f'DEFAULT_SERVER = "{ws_domain}"'
    )
    configured_code = configured_code.replace(
        'DEFAULT_TOKEN = ""',
        f'DEFAULT_TOKEN = "{active_token}"'
    )

    # 1. Unduhan format .bat (Single-File Launcher Windows Mandiri)
    if file_type in ["bat", "windows"]:
        bat_header = (
            "@echo off\r\n"
            "rem ============================================================================\r\n"
            "rem   SCPOOL // QUANTUM MODEM ENGINE - CLIENT HARDWARE BRIDGE\r\n"
            "rem   Coded By Ardh1337\r\n"
            "rem ============================================================================\r\n"
            "setlocal enabledelayedexpansion\r\n"
            "title SCPool Modem Pool Bridge\r\n\r\n"
            "where python >nul 2>&1\r\n"
            "if %ERRORLEVEL% neq 0 (\r\n"
            "    echo.\r\n"
            "    echo  [!] ERROR: Python 3 tidak ditemukan di sistem Windows ini.\r\n"
            "    echo      Silakan unduh dan pasang Python dari: https://www.python.org/\r\n"
            "    echo      (PENTING: Centang opsi 'Add Python to PATH' saat instalasi)\r\n"
            "    echo.\r\n"
            "    pause\r\n"
            "    exit /b 1\r\n"
            ")\r\n\r\n"
            "python -c \"import serial, websockets\" >nul 2>&1\r\n"
            "if %ERRORLEVEL% neq 0 (\r\n"
            "    echo.\r\n"
            "    echo  [*] Menyiapkan pustaka serial dan websockets (otomatis)...\r\n"
            "    python -m pip install pyserial websockets --quiet\r\n"
            ")\r\n\r\n"
            "python -x \"%~f0\" %*\r\n"
            "if %ERRORLEVEL% neq 0 (\r\n"
            "    echo.\r\n"
            "    echo  [!] Sesi bridge terhenti dengan kode keluar %ERRORLEVEL%.\r\n"
            "    pause\r\n"
            ")\r\n"
            "exit /b %ERRORLEVEL%\r\n"
            "# --- PYTHON QUANTUM BRIDGE ENGINE BELOW ---\r\n"
        )
        single_file_content = bat_header + configured_code
        return PlainTextResponse(
            single_file_content,
            media_type="application/x-bat",
            headers={"Content-Disposition": "attachment; filename=scpool_agent.bat"}
        )

    # 2. Unduhan format .py (Single Python File untuk Linux/Mac/Pengembang)
    return PlainTextResponse(
        configured_code,
        media_type="text/x-python",
        headers={"Content-Disposition": "attachment; filename=scpool_agent.py"}
    )

