"""Mode lokal: server memindai modem sendiri lewat logika agent, tanpa agent .bat dan tanpa WebSocket."""

import asyncio
import contextlib
import importlib.util
import time

from app.core.config import settings
from app.database.bootstrap import init_db
from app.database.connection import session_scope
from app.database.models import SIMCard, Slot, SlotStatus
from app.services import local_bridge
from app.services.agent_gateway import agent_gateway
from app.services.units import owner_of_pool

ICCID = "89620100000000001234"


def _fake_agent(monkeypatch):
    spec = importlib.util.spec_from_file_location("scpool_test_local_agent", local_bridge.AGENT_SCRIPT)
    agent = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(agent)
    monkeypatch.setattr(agent, "get_local_units", lambda allowed=None: [("usb:1-10", ["COM7"])])
    monkeypatch.setattr(agent, "get_local_ports", lambda allowed=None: ["COM7"])
    monkeypatch.setattr(agent, "probe_port", lambda port: {"port": port, "cpin": "READY", "iccid": ICCID})
    monkeypatch.setattr(agent, "read_sms", lambda port: [])
    monkeypatch.setattr(agent, "open_port", lambda port: contextlib.nullcontext(port))
    monkeypatch.setattr(agent, "run_ussd",
                        lambda ser, code, timeout: (True, '+CUSD: 0,"Nomor Anda 081234567890",15', None))
    monkeypatch.setattr(local_bridge, "_load_agent", lambda: agent)


def _phone_on_com7():
    with session_scope() as db:
        sim = db.query(SIMCard).filter(SIMCard.iccid == ICCID).first()
        slot = db.query(Slot).filter(Slot.pool_id == settings.local_pool_id, Slot.physical_port_id == "COM7").first()
        return (sim.phone_number if sim else None), (slot.status if slot else None)


def test_local_bridge_reads_sim_and_number_without_agent(monkeypatch):
    init_db()
    _fake_agent(monkeypatch)
    with session_scope() as db:
        owner_id = owner_of_pool(db, settings.local_pool_id)

    async def scenario():
        local_bridge.start()
        deadline = time.time() + 10
        result = (None, None)
        while time.time() < deadline:
            await asyncio.sleep(0.2)
            result = await asyncio.to_thread(_phone_on_com7)
            if result[0]:
                break
        online = agent_gateway.is_agent_online(owner_id)
        await local_bridge.stop()
        return result, online, agent_gateway.is_agent_online(owner_id)

    (phone, status), online_while_running, online_after_stop = asyncio.run(scenario())

    assert phone == "+6281234567890"
    assert status == SlotStatus.READY
    assert online_while_running
    assert not online_after_stop
