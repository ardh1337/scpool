"""Pengelompokan port per modem pool di agent (agent/agent_forwarder.py)."""

import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest

_spec = importlib.util.spec_from_file_location(
    "agent_forwarder", Path(__file__).resolve().parents[1] / "agent" / "agent_forwarder.py")
agent = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(agent)


def _port(device, location):
    return SimpleNamespace(device=device, location=location, vid=0x04E2)


def test_ports_without_pyserial_location_join_their_cable(monkeypatch):
    # Foxcom 32 port asli: pyserial 3.5 mengembalikan location=None untuk sebagian port di hub bertingkat.
    ports = [_port("COM3", "1-10.2.3.3.1:x.6"), _port("COM7", None), _port("COM22", None)]
    monkeypatch.setattr(agent.serial.tools.list_ports, "comports", lambda: ports)
    monkeypatch.setattr(agent, "windows_port_locations",
                        lambda: {"COM7": "1-10.2.3.3.1:x.0", "COM22": "1-10.2.3.3.7.1:x.2"})

    assert agent.get_local_units() == [("usb:1-10", ["COM7", "COM3", "COM22"])]


def test_location_lookup_failure_falls_back_to_default_group(monkeypatch):
    def boom():
        raise OSError("setupapi")

    monkeypatch.setattr(agent.serial.tools.list_ports, "comports", lambda: [_port("COM5", None)])
    monkeypatch.setattr(agent, "windows_port_locations", boom)

    assert agent.get_local_units() == [("default", ["COM5"])]


@pytest.mark.skipif(not agent.sys.platform.startswith("win"), reason="hanya Windows")
def test_windows_port_locations_runs():
    assert isinstance(agent.windows_port_locations(), dict)


class _FakeWS:
    def __init__(self):
        self.sent = []

    async def send(self, text):
        self.sent.append(text)


def _slow_ussd_session(monkeypatch, delay):
    import contextlib
    import time as _time

    monkeypatch.setattr(agent, "get_local_ports", lambda allowed=None: ["COM3", "COM4"])
    monkeypatch.setattr(agent, "open_port", lambda port: contextlib.nullcontext(port))
    monkeypatch.setattr(agent, "run_ussd", lambda ser, code, timeout: (_time.sleep(delay), (True, "+CUSD: 0", None))[1])
    return agent.Session(_FakeWS(), None)


def test_ussd_requests_on_different_ports_run_in_parallel(monkeypatch):
    import asyncio
    import time as _time

    session = _slow_ussd_session(monkeypatch, 0.5)

    async def scenario():
        start = _time.monotonic()
        for i, port in enumerate(["COM3", "COM4"]):
            await session.handle_server_message({"type": "ussd", "req_id": i, "port": port, "code": "*808#"})
        await asyncio.gather(*session.requests)
        return _time.monotonic() - start

    assert asyncio.run(scenario()) < 0.9
    assert len(session.ws.sent) == 2


def test_ussd_requests_on_same_port_still_take_turns(monkeypatch):
    import asyncio
    import time as _time

    session = _slow_ussd_session(monkeypatch, 0.3)

    async def scenario():
        start = _time.monotonic()
        for i in range(2):
            await session.handle_server_message({"type": "ussd", "req_id": i, "port": "COM3", "code": "*808#"})
        await asyncio.gather(*session.requests)
        return _time.monotonic() - start

    assert asyncio.run(scenario()) >= 0.6
