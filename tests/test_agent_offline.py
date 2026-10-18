"""Saat agent mati, port tidak boleh tampil aktif dengan nomor lama (SIM bisa dicabut/ditukar tanpa terbaca)."""

import asyncio
import secrets

from app.database.bootstrap import init_db
from app.database.connection import session_scope
from app.database.models import NumberStatus, SIMCard, SIMSession, Slot, SlotStatus, UserAccount
from app.services.agent_gateway import AgentGateway
from app.services.units import create_unit

ICCID_A = "89620100000000001234"
ICCID_B = "89620100000000005678"


def _account_with_ready_sim():
    """Akun baru dengan satu port READY berisi SIM yang nomornya sudah diketahui."""
    init_db()
    gateway = AgentGateway(auto_fetch_numbers=False)
    port = f"COM{secrets.randbelow(10 ** 6)}"
    with session_scope() as db:
        user = UserAccount(username=f"klien_{secrets.token_hex(4)}", password="x", role="operator")
        db.add(user)
        db.flush()
        pool = create_unit(db, user.id, group_key="usb:1-10")
        db.add(Slot(pool_id=pool.id, slot_index=1, physical_port_id=port, status=SlotStatus.EMPTY))
        db.commit()
        owner_id = user.id
    gateway._on_slot_update(owner_id, {"port": port, "cpin": "READY", "iccid": ICCID_A}, None)
    with session_scope() as db:
        sim = db.query(SIMCard).filter(SIMCard.iccid == ICCID_A).one()
        sim.phone_number, sim.number_status = "+6281234567890", NumberStatus.DIKETAHUI
        db.commit()
    return gateway, owner_id, port


def _slot(port):
    with session_scope() as db:
        slot = db.query(Slot).filter(Slot.physical_port_id == port).one()
        active = db.query(SIMSession).filter(SIMSession.slot_id == slot.id, SIMSession.ended_at.is_(None)).first()
        return slot.status, (active.sim_card.iccid if active else None)


def _connect_and_drop(gateway, owner_id):
    class _WS:
        async def send_text(self, text):
            pass

    ws = _WS()
    user = type("U", (), {"id": owner_id, "username": "klien"})()
    asyncio.run(gateway.register(owner_id, ws, user))
    gateway.unregister(owner_id, ws)


def test_agent_disconnect_marks_ports_offline_but_keeps_sim_memory():
    gateway, owner_id, port = _account_with_ready_sim()

    _connect_and_drop(gateway, owner_id)

    status, iccid = _slot(port)
    assert status == SlotStatus.OFFLINE
    assert iccid == ICCID_A  # sesi tetap terbuka: SIM yang sama dikenali lagi tanpa dial USSD


def test_same_sim_after_reconnect_is_ready_again_without_new_session():
    gateway, owner_id, port = _account_with_ready_sim()
    _connect_and_drop(gateway, owner_id)

    gateway._on_ports_list(owner_id, {"units": [{"key": "usb:1-10", "ports": [port]}]}, None)
    gateway._on_slot_update(owner_id, {"port": port, "cpin": "READY", "iccid": ICCID_A}, None)

    assert _slot(port) == (SlotStatus.READY, ICCID_A)
    with session_scope() as db:
        assert db.query(SIMCard).filter(SIMCard.iccid == ICCID_A).one().phone_number == "+6281234567890"


def test_sim_removed_while_agent_was_off_closes_old_session():
    gateway, owner_id, port = _account_with_ready_sim()
    _connect_and_drop(gateway, owner_id)

    gateway._on_ports_list(owner_id, {"units": [{"key": "usb:1-10", "ports": [port]}]}, None)
    gateway._on_slot_update(owner_id, {"port": port, "cpin": "EMPTY"}, None)

    assert _slot(port) == (SlotStatus.EMPTY, None)


def test_sim_swapped_while_agent_was_off_starts_new_session():
    gateway, owner_id, port = _account_with_ready_sim()
    _connect_and_drop(gateway, owner_id)

    gateway._on_ports_list(owner_id, {"units": [{"key": "usb:1-10", "ports": [port]}]}, None)
    gateway._on_slot_update(owner_id, {"port": port, "cpin": "READY", "iccid": ICCID_B}, None)

    assert _slot(port) == (SlotStatus.READY, ICCID_B)
