"""
Pengujian Unit Gateway WebSocket Remote Agent & Agent Status
"""

import pytest
import json
import asyncio
from unittest.mock import AsyncMock
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.main import app
from app.database.connection import get_db, Base
from app.database.models import UserAccount, ModemPool, Slot, SlotStatus, SMSMessage
from app.auth.security import hash_password, generate_agent_token
from app.services.agent_gateway import AgentGateway

TEST_DATABASE_URL = "sqlite:///:memory:"
engine = create_engine(
    TEST_DATABASE_URL, connect_args={"check_same_thread": False}, poolclass=StaticPool
)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


@pytest.fixture
def db_session():
    Base.metadata.create_all(bind=engine)
    db = TestingSessionLocal()
    db.add(UserAccount(
        username="test_admin", password=hash_password("admin-test-password"),
        role="superadmin", pool_id="foxcom_32", is_active=True,
    ))
    db.commit()
    try:
        yield db
    finally:
        db.close()
        Base.metadata.drop_all(bind=engine)


@pytest.fixture
def client(db_session):
    def override_get_db():
        try:
            yield db_session
        finally:
            pass

    app.dependency_overrides[get_db] = override_get_db
    c = TestClient(app)
    yield c
    app.dependency_overrides.clear()


def _owner(db_session, user_id, username):
    user = UserAccount(id=user_id, username=username, password="x", role="operator", is_active=True)
    db_session.add(user)
    db_session.commit()
    return user


@pytest.mark.anyio
async def test_agent_gateway_lifecycle(db_session):
    gw = AgentGateway()
    user = _owner(db_session, 99, "testuser")
    mock_ws = AsyncMock()

    assert gw.is_agent_online(99) is False

    await gw.register(99, mock_ws, user)
    assert gw.is_agent_online(99) is True
    assert gw.get_agent_info(99)["username"] == "testuser"

    await gw.handle_agent_message(99, json.dumps({"type": "ping"}), db=db_session)
    mock_ws.send_text.assert_called_with('{"type": "pong"}')

    gw.unregister(99)
    assert gw.is_agent_online(99) is False


@pytest.mark.anyio
async def test_agent_gateway_ports_and_sms(db_session):
    gw = AgentGateway()
    user = _owner(db_session, 100, "user_sms")
    await gw.register(100, AsyncMock(), user)

    await gw.handle_agent_message(100, json.dumps({
        "type": "ports_list", "ports": ["COM10", "COM11"], "version": "1.0.0"}), db=db_session)

    pools = db_session.query(ModemPool).filter(ModemPool.owner_id == 100).all()
    assert len(pools) == 1 and pools[0].name == "Modem Pool 1" and pools[0].port_count == 2
    slots = db_session.query(Slot).filter(Slot.pool_id == pools[0].id).order_by(Slot.slot_index).all()
    assert [s.physical_port_id for s in slots] == ["COM10", "COM11"]

    await gw.handle_agent_message(100, json.dumps({
        "type": "sms_received", "slot_index": 1, "sender": "BANK_BCA",
        "body": "Kode OTP rahasia anda 452189 berlaku 5 menit.", "index": 1}), db=db_session)

    msg = db_session.query(SMSMessage).first()
    assert msg is not None
    assert msg.sender_raw == "BANK_BCA"
    assert "452189" in msg.body


@pytest.mark.anyio
async def test_agent_detects_multiple_units_and_keeps_identity(db_session):
    """32 port + 16 port di dua kabel USB -> dua unit otomatis; deteksi ulang tidak menggeser port (I-3)."""
    gw = AgentGateway()
    user = _owner(db_session, 101, "multi")
    await gw.register(101, AsyncMock(), user)
    big = [f"COM{n}" for n in range(3, 35)]
    small = [f"COM{n}" for n in range(40, 56)]
    msg = {"type": "ports_list", "units": [{"key": "usb:1-4", "ports": big}, {"key": "usb:1-5", "ports": small}]}
    await gw.handle_agent_message(101, json.dumps(msg), db=db_session)

    pools = db_session.query(ModemPool).filter(ModemPool.owner_id == 101).order_by(ModemPool.created_at, ModemPool.id).all()
    assert sorted(p.port_count for p in pools) == [16, 32]
    assert {p.name for p in pools} == {"Modem Pool 1", "Modem Pool 2"}

    first_big = db_session.query(Slot).filter(Slot.physical_port_id == "COM3").one()
    pool_before, index_before = first_big.pool_id, first_big.slot_index

    # Kabel dicabut sebagian, urutan daftar berubah, dan port baru muncul di unit kecil.
    msg = {"type": "ports_list", "units": [
        {"key": "usb:1-5", "ports": ["COM99"] + small}, {"key": "usb:1-4", "ports": big[5:]}]}
    await gw.handle_agent_message(101, json.dumps(msg), db=db_session)
    db_session.expire_all()

    again = db_session.query(Slot).filter(Slot.physical_port_id == "COM3").one()
    assert (again.pool_id, again.slot_index) == (pool_before, index_before)
    assert again.status == SlotStatus.OFFLINE  # hilang dari mesin klien, riwayat tidak dihapus
    new = db_session.query(Slot).filter(Slot.physical_port_id == "COM99").one()
    assert new.slot_index == 17 and new.pool_id == db_session.query(Slot).filter(
        Slot.physical_port_id == "COM40").one().pool_id
    assert db_session.query(ModemPool).filter(ModemPool.owner_id == 101).count() == 2


@pytest.mark.anyio
async def test_new_port_joins_unit_of_its_group_even_without_group_key(db_session):
    gw = AgentGateway()
    await gw.register(105, AsyncMock(), _owner(db_session, 105, "legacy"))
    await gw.handle_agent_message(105, json.dumps(
        {"type": "ports_list", "units": [{"key": "usb:1-4", "ports": ["COM3", "COM4"]}]}), db=db_session)
    # agent lama tidak mengirim kunci kelompok; port baru harus ikut unit yang sama, bukan membuat unit baru
    await gw.handle_agent_message(105, json.dumps({"type": "ports_list", "ports": ["COM3", "COM4", "COM5"]}),
                                  db=db_session)
    pools = db_session.query(ModemPool).filter(ModemPool.owner_id == 105).all()
    assert [p.port_count for p in pools] == [3]


@pytest.mark.anyio
async def test_agent_cannot_touch_other_accounts_ports(db_session):
    """Nama port sama (COM3) di dua akun tidak boleh bercampur."""
    gw = AgentGateway()
    a, b = _owner(db_session, 110, "acc_a"), _owner(db_session, 111, "acc_b")
    await gw.register(110, AsyncMock(), a)
    await gw.register(111, AsyncMock(), b)
    for owner in (110, 111):
        await gw.handle_agent_message(owner, json.dumps({"type": "ports_list", "ports": ["COM3"]}), db=db_session)
    await gw.handle_agent_message(110, json.dumps({
        "type": "sms_received", "port": "COM3", "sender": "X", "body": "hanya milik A"}), db=db_session)

    msgs = db_session.query(SMSMessage).all()
    assert len(msgs) == 1
    owner_of_msg = db_session.query(ModemPool.owner_id).join(Slot, Slot.pool_id == ModemPool.id).filter(
        Slot.id == msgs[0].slot_id).scalar()
    assert owner_of_msg == 110


@pytest.mark.anyio
async def test_agent_input_limits(db_session):
    gw = AgentGateway()
    await gw.register(120, AsyncMock(), _owner(db_session, 120, "limits"))
    ports = [f"COM{n}" for n in range(1, 400)]
    await gw.handle_agent_message(120, json.dumps({"type": "ports_list", "units": [
        {"key": "bad key!", "ports": ports}, {"key": "k2", "ports": ["COM1", "../etc"]}]}), db=db_session)
    pools = db_session.query(ModemPool).filter(ModemPool.owner_id == 120).all()
    assert all(p.port_count <= 64 for p in pools)
    assert db_session.query(Slot).filter(Slot.physical_port_id == "../etc").count() == 0


def test_agent_status_endpoint(client, db_session):
    resp = client.get("/api/agent/status?pool_id=foxcom_32")
    assert resp.status_code == 200
    data = resp.json()
    assert data["pool_id"] == "foxcom_32"
    assert "is_online" in data


def test_download_agent_single_ready_file(client, db_session):
    # Buat user baru dengan token scpool
    user = UserAccount(
        username="ready_tester",
        password=hash_password("password123"),
        role="operator",
        agent_token="scpool_abcd1234efgh5678",
        is_active=True
    )
    db_session.add(user)
    db_session.commit()

    # 1. Download file .bat mandiri
    resp_bat = client.get(f"/api/download/agent?file_type=bat&token={user.agent_token}")
    assert resp_bat.status_code == 200
    assert "scpool_agent.bat" in resp_bat.headers.get("Content-Disposition", "")
    content_bat = resp_bat.text
    assert "@echo off" in content_bat
    assert 'DEFAULT_TOKEN = "scpool_abcd1234efgh5678"' in content_bat
    assert "SCPool Agent" in content_bat

    # 2. Download file .py mandiri
    resp_py = client.get(f"/api/download/agent?file_type=py&token={user.agent_token}")
    assert resp_py.status_code == 200
    assert "scpool_agent.py" in resp_py.headers.get("Content-Disposition", "")
    content_py = resp_py.text
    assert 'DEFAULT_TOKEN = "scpool_abcd1234efgh5678"' in content_py
    assert "SCPool Agent" in content_py

    # 3. Download dengan token tidak terdaftar
    resp_invalid = client.get("/api/download/agent?file_type=bat&token=scpool_invalidtoken999")
    assert resp_invalid.status_code == 403

