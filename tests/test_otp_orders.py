"""Pesanan nomor untuk otomasi HP: kunci nomor, tunggu OTP, selesai/batal, kunci API."""

import secrets
import threading
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app.auth.security import hash_password
from app.database.bootstrap import init_db
from app.database.connection import session_scope
from app.database.models import NumberOrder, NumberStatus, SIMCard, Slot, SlotStatus, UserAccount
from app.main import app
from app.services.agent_gateway import agent_gateway
from app.services.units import create_unit

PASSWORD = "Rahasia-Otomasi-77"


class _AgentWS:
    def __init__(self):
        self.sent = []

    async def send_text(self, text):
        self.sent.append(text)

    async def close(self, code=1000):
        pass


@pytest.fixture
def setup():
    """Akun dengan 2 SIM aktif bernomor, agent online, kunci API, dan klien HTTP publik (tanpa auto-login)."""
    init_db()
    username = f"hp{secrets.token_hex(3)}"
    with session_scope() as db:
        user = UserAccount(username=username, password=hash_password(PASSWORD), role="operator", is_active=True)
        db.add(user)
        db.flush()
        pool = create_unit(db, user.id, group_key="usb:1-10")
        ports = []
        for i in (1, 2):
            port = f"COM{secrets.randbelow(10 ** 6)}"
            db.add(Slot(pool_id=pool.id, slot_index=i, physical_port_id=port, status=SlotStatus.EMPTY))
            ports.append(port)
        db.commit()
        owner, pool_id = user.id, pool.id
    # Urutan dunia nyata: server menyala (semua port OFFLINE), agent tersambung, lalu melaporkan SIM.
    client = TestClient(app, base_url="https://scpool.example.com")
    client.__enter__()
    ws = _AgentWS()
    client.portal.call(agent_gateway.register, owner, ws, type("U", (), {"id": owner, "username": username})())
    iccids = [f"896210{secrets.randbelow(10 ** 13):013d}" for _ in ports]
    for port, iccid in zip(ports, iccids):
        agent_gateway._on_slot_update(owner, {"port": port, "cpin": "READY", "iccid": iccid}, None)
    with session_scope() as db:
        for n, iccid in enumerate(iccids):
            sim = db.query(SIMCard).filter(SIMCard.iccid == iccid).one()
            sim.phone_number, sim.number_status = f"+62812{secrets.randbelow(10 ** 7):07d}", NumberStatus.DIKETAHUI
        db.commit()
    token = client.post("/api/auth/login", json={"username": username, "password": PASSWORD}).json()["token"]
    client.cookies.clear()
    key = client.post("/api/otp/keys", json={"name": "HP 1"}, headers={"Authorization": f"Bearer {token}"}).json()
    yield {"client": client, "owner": owner, "ports": ports, "pool_id": pool_id, "ws": ws,
           "hdr": {"X-API-Key": key["api_key"]}, "token": token}
    agent_gateway.unregister(owner, ws)
    client.__exit__(None, None, None)


def _sms(s, port, sender, body):
    agent_gateway._on_sms_received(s["owner"], {"port": port, "sender": sender, "body": body,
                                                "timestamp": datetime.now(timezone.utc).isoformat()}, None)


def _order(s, service="whatsapp", **extra):
    return s["client"].post("/api/otp/orders", json={"service": service, **extra}, headers=s["hdr"])


def _port_of(s, order):
    return s["ports"][order["slot_index"] - 1]


def test_api_key_only_works_on_automation_endpoints(setup):
    c = setup["client"]
    assert c.get("/api/otp/services", headers=setup["hdr"]).status_code == 200
    assert c.get("/api/slots", headers=setup["hdr"]).status_code == 401
    assert c.put("/api/auth/credentials", json={"password": "x"}, headers=setup["hdr"]).status_code == 401
    assert c.get("/api/otp/services", headers={"X-API-Key": "sk_palsu"}).status_code == 401


def test_two_devices_get_different_numbers_and_third_waits(setup):
    first, second = _order(setup, device="HP-1").json(), _order(setup, device="HP-2").json()
    assert first["status"] == "ACTIVE" and second["status"] == "ACTIVE"
    assert first["phone_number"] != second["phone_number"]
    assert first["phone_national"].startswith("0812")
    third = _order(setup)
    assert third.status_code == 409 and "Tidak ada nomor tersedia" in third.json()["detail"]


def test_otp_from_service_after_order_is_returned_and_others_ignored(setup):
    order = _order(setup).json()
    port = _port_of(setup, order)
    assert '"watch_sms"' in setup["ws"].sent[-1]  # agent diminta membaca port ini lebih sering

    _sms(setup, port, "Telkomsel", "Kode promo 123456 berlaku hari ini, verifikasi di aplikasi")
    pending = setup["client"].get(f"/api/otp/orders/{order['order_id']}", headers=setup["hdr"]).json()
    assert pending["status"] == "ACTIVE" and pending["otp"] is None

    _sms(setup, port, "WhatsApp", "Kode WhatsApp Anda: 482-913. Jangan bagikan kode ini.")
    got = setup["client"].get(f"/api/otp/orders/{order['order_id']}", headers=setup["hdr"]).json()
    assert got["status"] == "OTP_RECEIVED" and got["otp"] == "482913"
    assert got["sms"]["sender"] == "WhatsApp"


def test_sms_received_before_the_order_is_not_an_otp(setup):
    with session_scope() as db:
        slot = db.query(Slot).filter(Slot.physical_port_id == setup["ports"][0]).one()
    _sms(setup, setup["ports"][0], "WhatsApp", "Kode WhatsApp Anda: 111-222.")
    _sms(setup, setup["ports"][1], "WhatsApp", "Kode WhatsApp Anda: 111-222.")
    order = _order(setup).json()
    got = setup["client"].get(f"/api/otp/orders/{order['order_id']}", headers=setup["hdr"]).json()
    assert got["status"] == "ACTIVE" and slot.id


def test_wait_returns_as_soon_as_otp_arrives(setup):
    order = _order(setup).json()
    port = _port_of(setup, order)
    threading.Timer(1.0, _sms, args=(setup, port, "WhatsApp", "Kode WhatsApp Anda: 765-432.")).start()
    started = datetime.now()
    got = setup["client"].get(f"/api/otp/orders/{order['order_id']}?wait=10", headers=setup["hdr"]).json()
    assert got["otp"] == "765432"
    assert (datetime.now() - started).total_seconds() < 5


def test_finished_number_is_not_reused_for_same_service_but_ok_for_another(setup):
    a, b = _order(setup).json(), _order(setup).json()
    _sms(setup, _port_of(setup, a), "WhatsApp", "Kode WhatsApp Anda: 482-913.")
    setup["client"].get(f"/api/otp/orders/{a['order_id']}", headers=setup["hdr"])
    done = setup["client"].post(f"/api/otp/orders/{a['order_id']}/finish", headers=setup["hdr"]).json()
    assert done["status"] == "DONE"
    cancelled = setup["client"].post(f"/api/otp/orders/{b['order_id']}/cancel", headers=setup["hdr"]).json()
    assert cancelled["status"] == "CANCELLED"

    again = _order(setup).json()
    assert again["phone_number"] == b["phone_number"]  # yang dibatalkan boleh dipakai lagi
    assert _order(setup).status_code == 409             # yang sudah dapat OTP tidak diberikan lagi
    assert _order(setup, service="telegram").json()["phone_number"] == a["phone_number"]


def test_cancel_after_otp_is_refused(setup):
    order = _order(setup).json()
    _sms(setup, _port_of(setup, order), "WhatsApp", "Kode WhatsApp Anda: 482-913.")
    setup["client"].get(f"/api/otp/orders/{order['order_id']}", headers=setup["hdr"])
    assert setup["client"].post(f"/api/otp/orders/{order['order_id']}/cancel", headers=setup["hdr"]).status_code == 409


def test_expired_order_frees_the_number(setup):
    order = _order(setup).json()
    with session_scope() as db:
        db.get(NumberOrder, order["order_id"]).expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        db.commit()
    got = setup["client"].get(f"/api/otp/orders/{order['order_id']}", headers=setup["hdr"]).json()
    assert got["status"] == "EXPIRED" and got["seconds_left"] == 0
    numbers = {_order(setup).json()["phone_number"], _order(setup).json()["phone_number"]}
    assert order["phone_number"] in numbers and len(numbers) == 2  # nomor yang kedaluwarsa bebas lagi


def test_no_numbers_when_agent_offline(setup):
    agent_gateway.unregister(setup["owner"], setup["ws"])
    resp = _order(setup)
    assert resp.status_code == 409 and "Agent" in resp.json()["detail"]


def test_revoked_key_stops_working(setup):
    c, auth = setup["client"], {"Authorization": f"Bearer {setup['token']}"}
    key_id = c.get("/api/otp/keys", headers=auth).json()[0]["id"]
    assert c.delete(f"/api/otp/keys/{key_id}", headers=auth).status_code == 200
    assert c.get("/api/otp/services", headers=setup["hdr"]).status_code == 401


def test_orders_are_private_per_account(setup):
    order = _order(setup).json()
    other = setup["client"].get(f"/api/otp/orders/{order['order_id']}", headers={"X-API-Key": "sk_" + "x" * 43})
    assert other.status_code == 401
