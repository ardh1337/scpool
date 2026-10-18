"""
Modem pool yang tercolok ke mesin server sendiri, tanpa agent terpisah.

Logika agent (agent/agent_forwarder.py: deteksi pool per kabel USB, pemindaian paralel, SMS, USSD paralel) dijalankan
di dalam proses server dan disambungkan langsung ke agent_gateway, tanpa WebSocket. Satu sumber kode untuk mode lokal
dan remote, jadi perbaikan agent otomatis berlaku di sini.

Pemiliknya adalah pemilik pool lokal (settings.local_pool_id, bawaan: admin).
"""

import asyncio
import importlib.util
import json
import logging
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from typing import Optional

from app.core.config import settings
from app.database.connection import session_scope
from app.database.models import UserAccount
from app.services.agent_gateway import agent_gateway
from app.services.units import owner_of_pool

logger = logging.getLogger(__name__)

AGENT_SCRIPT = Path(__file__).resolve().parents[2] / "agent" / "agent_forwarder.py"

_task: Optional[asyncio.Task] = None


def _load_agent():
    spec = importlib.util.spec_from_file_location("scpool_local_agent", AGENT_SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _ToAgent:
    """Sisi yang dipegang gateway: pesan server langsung diteruskan ke sesi agent."""

    def __init__(self, session):
        self.session = session
        self.tasks: set = set()

    async def send_text(self, text: str) -> None:
        task = asyncio.create_task(self.session.handle_server_message(json.loads(text)))
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)

    async def close(self, code: int = 1000) -> None:
        # Agent remote untuk akun yang sama menggantikan jembatan ini; hentikan supaya tidak berebut port COM.
        logger.warning("Agent remote mengambil alih akun pool lokal; pemindai lokal dihentikan.")
        if _task is not None:
            _task.cancel()


class _ToServer:
    """Sisi yang dipegang sesi agent: laporan agent langsung diproses gateway."""

    def __init__(self, owner_id: int):
        self.owner_id = owner_id

    async def send(self, text: str) -> None:
        await agent_gateway.handle_agent_message(self.owner_id, text)


def _local_owner() -> Optional[SimpleNamespace]:
    with session_scope() as db:
        owner_id = owner_of_pool(db, settings.local_pool_id)
        user = db.get(UserAccount, owner_id) if owner_id is not None else None
        return SimpleNamespace(id=user.id, username=user.username) if user else None


async def _run() -> None:
    owner = await asyncio.to_thread(_local_owner)
    if owner is None:
        logger.error("Pool lokal %s tidak punya pemilik; modem lokal tidak dipindai.", settings.local_pool_id)
        return
    agent = await asyncio.to_thread(_load_agent)
    # Tiap USSD memegang satu thread selama dial; bawaan asyncio (CPU+4) membuat 32 port mengantre.
    asyncio.get_running_loop().set_default_executor(ThreadPoolExecutor(max_workers=agent.MAX_WORKER_THREADS))

    session = agent.Session(_ToServer(owner.id), None)
    to_agent = _ToAgent(session)
    await agent_gateway.register(owner.id, to_agent, owner)
    logger.info("Modem lokal dipindai langsung oleh server (akun %s).", owner.username)
    try:
        await session.scan_loop()
    finally:
        for task in list(to_agent.tasks) + list(session.requests):
            task.cancel()
        agent_gateway.unregister(owner.id, to_agent)


def _done(task: asyncio.Task) -> None:
    if not task.cancelled() and task.exception():
        logger.error("Pemindai modem lokal berhenti", exc_info=task.exception())


def start() -> None:
    global _task
    if _task is None or _task.done():
        _task = asyncio.get_running_loop().create_task(_run())
        _task.add_done_callback(_done)


async def stop() -> None:
    global _task
    if _task is not None:
        _task.cancel()
        try:
            await _task
        except (asyncio.CancelledError, Exception):
            pass
        _task = None


def is_running() -> bool:
    return _task is not None and not _task.done()
