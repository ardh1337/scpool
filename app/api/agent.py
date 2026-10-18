"""Status koneksi agent remote dan distribusi skrip agent (token & server tertanam)."""

import json
import re
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import PlainTextResponse
from sqlalchemy.orm import Session

from app.auth.deps import get_current_user_optional, is_admin, require_user, resolve_pool_id
from app.auth.security import is_https_request
from app.database.connection import get_db
from app.database.models import UserAccount
from app.services.agent_gateway import agent_gateway
from app.services.settings import get_public_domain, ws_url_from_domain
from app.services.units import owner_of_pool

router = APIRouter()

AGENT_SCRIPT = Path(__file__).resolve().parents[2] / "agent" / "agent_forwarder.py"
_HOST_PATTERN = re.compile(r'^[A-Za-z0-9.-]{1,253}(:\d{1,5})?\Z')
_SERVER_LINE = re.compile(r'^DEFAULT_SERVER = ".*"$', re.MULTILINE)
_TOKEN_LINE = re.compile(r'^DEFAULT_TOKEN = ".*"$', re.MULTILINE)

# Baris pertama dilewati oleh `python -x`, jadi seluruh logika batch harus ada di baris ini dan diakhiri
# `exit /b` agar cmd tidak membaca sisa file (yang berisi kode Python).
BAT_HEADER = (
    '@echo off & title SCPool Modem Pool Bridge & chcp 65001 >nul & '
    'where python >nul 2>&1 || (echo Python 3 tidak ditemukan. Pasang dari https://www.python.org/ '
    'dan centang "Add Python to PATH". & pause & exit /b 1) & '
    'python -x "%~f0" %* & if errorlevel 1 pause & exit /b\r\n'
)


@router.get("/agent/status")
def get_agent_status(pool_id: Optional[str] = Query(None), user: UserAccount = Depends(require_user),
                     db: Session = Depends(get_db)):
    """Status agent milik akun. Admin yang memilih `pool_id` melihat agent pemilik pool itu."""
    target = resolve_pool_id(db, user, pool_id)
    owner_id = owner_of_pool(db, target) if (target and is_admin(user) and pool_id) else user.id
    return {
        "pool_id": target,
        "is_online": agent_gateway.is_agent_online(owner_id) if owner_id else False,
        "info": agent_gateway.get_agent_info(owner_id) if owner_id else None,
    }


def _request_base_url(request: Request) -> str:
    host = request.headers.get("x-forwarded-host") or request.headers.get("host") or "localhost"
    if not _HOST_PATTERN.match(host):
        host = "localhost"
    return f"{'https' if is_https_request(request) else 'http'}://{host}"


def render_agent_script(server_ws_url: str, token: str) -> str:
    """Sisipkan alamat server dan token ke skrip agent. json.dumps menjamin tanda kutip/backslash aman."""
    source = AGENT_SCRIPT.read_text(encoding="utf-8")
    source = _SERVER_LINE.sub(lambda _m: f"DEFAULT_SERVER = {json.dumps(server_ws_url)}", source, count=1)
    return _TOKEN_LINE.sub(lambda _m: f"DEFAULT_TOKEN = {json.dumps(token)}", source, count=1)


@router.get("/download/agent")
def download_agent_file(
    request: Request,
    file_type: str = Query("py", description="'py' (skrip Python) atau 'bat' (peluncur Windows satu file)"),
    token: Optional[str] = Query(None, description="Agent token untuk unduhan langsung pasca-registrasi"),
    user: Optional[UserAccount] = Depends(get_current_user_optional),
    db: Session = Depends(get_db),
):
    if token and token.strip():
        owner = db.query(UserAccount).filter(UserAccount.agent_token == token.strip()).first()
        if not owner or not owner.is_active:
            raise HTTPException(403, "Agent token tidak valid atau akun belum aktif.")
    elif user:
        owner = user
    else:
        raise HTTPException(401, "Otentikasi atau token diperlukan untuk mengunduh file agen.")

    if not AGENT_SCRIPT.exists():
        raise HTTPException(404, "File agent/agent_forwarder.py tidak ditemukan di server.")

    domain = get_public_domain(db, fallback=_request_base_url(request))
    script = render_agent_script(ws_url_from_domain(domain), owner.agent_token or "")

    if file_type in ("bat", "windows"):
        return PlainTextResponse(BAT_HEADER + script, media_type="application/x-bat",
                                 headers={"Content-Disposition": "attachment; filename=scpool_agent.bat",
                                          "Cache-Control": "no-store"})
    return PlainTextResponse(script, media_type="text/x-python",
                             headers={"Content-Disposition": "attachment; filename=scpool_agent.py",
                                      "Cache-Control": "no-store"})
