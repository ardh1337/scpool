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

from app.main import app
from app.database.connection import get_db, Base
from app.database.models import UserAccount, ModemPool, Slot, SlotStatus, SMSMessage
from app.auth.security import hash_password, generate_agent_token
from app.services.agent_gateway import AgentGateway

TEST_DATABASE_URL = "sqlite:///:memory:"
engine = create_engine(TEST_DATABASE_URL, connect_args={"check_same_thread": False})
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


@pytest.fixture
def db_session():
    Base.metadata.create_all(bind=engine)
    db = TestingSessionLocal()
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


@pytest.mark.anyio
async def test_agent_gateway_lifecycle(db_session):
    gw = AgentGateway()
    pool_id = "pool_unit_test"
    user = UserAccount(id=99, username="testuser", role="operator", pool_id=pool_id, is_active=True)
    mock_ws = AsyncMock()

    assert gw.is_agent_online(pool_id) is False

    await gw.register(pool_id, mock_ws, user)
    assert gw.is_agent_online(pool_id) is True
    assert gw.get_agent_info(pool_id)["username"] == "testuser"

    # Handle ping
    await gw.handle_agent_message(pool_id, json.dumps({"type": "ping"}), db=db_session)
    mock_ws.send_text.assert_called_with('{"type": "pong"}')

    gw.unregister(pool_id)
    assert gw.is_agent_online(pool_id) is False


@pytest.mark.anyio
async def test_agent_gateway_ports_and_sms(db_session):
    gw = AgentGateway()
    pool_id = "pool_sms_test"
    pool = ModemPool(id=pool_id, name="Test Pool", port_count=2)
    db_session.add(pool)
    db_session.commit()

    user = UserAccount(id=100, username="user_sms", role="operator", pool_id=pool_id, is_active=True)
    mock_ws = AsyncMock()
    await gw.register(pool_id, mock_ws, user)

    # 1. Kirim ports_list
    ports_payload = json.dumps({
        "type": "ports_list",
        "ports": ["COM10", "COM11"],
        "version": "1.0.0"
    })
    await gw.handle_agent_message(pool_id, ports_payload, db=db_session)

    slots = db_session.query(Slot).filter(Slot.pool_id == pool_id).order_by(Slot.slot_index).all()
    assert len(slots) == 2
    assert slots[0].physical_port_id == "COM10"
    assert slots[1].physical_port_id == "COM11"

    # 2. Kirim sms_received
    sms_payload = json.dumps({
        "type": "sms_received",
        "slot_index": 1,
        "sender": "BANK_BCA",
        "body": "Kode OTP rahasia anda 452189 berlaku 5 menit.",
        "index": 1
    })
    await gw.handle_agent_message(pool_id, sms_payload, db=db_session)

    msg = db_session.query(SMSMessage).first()
    assert msg is not None
    assert msg.sender_raw == "BANK_BCA"
    assert "452189" in msg.body


def test_agent_status_endpoint(client, db_session):
    resp = client.get("/api/agent/status?pool_id=foxcom_32")
    assert resp.status_code == 200
    data = resp.json()
    assert data["pool_id"] == "foxcom_32"
    assert "is_online" in data
