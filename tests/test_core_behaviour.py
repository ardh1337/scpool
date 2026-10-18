"""Perilaku inti: allowlist AT, validasi USSD, migrasi skema, gateway agent."""

import sqlite3
import pytest
from sqlalchemy import create_engine, inspect, text

from app.hardware.at_allowlist import is_at_command_allowed as is_command_allowed
from app.modem.ussd import validate_ussd_code
from app.database.migrations import run_migrations


@pytest.mark.parametrize("cmd", [
    "AT", "AT+CPIN?", "AT+CMGL=\"ALL\"", "AT+CUSD=1,\"*808#\",15",
])
def test_allowlist_accepts(cmd):
    assert is_command_allowed(cmd)


@pytest.mark.parametrize("cmd", [
    "AT+CMGD=1", "AT+CFUN=1,1", "AT+CUSD=1,\"*1#\";AT+CFUN=1", "AT\r+CMGD=1,4", "AT+CUSD=1,\"abc\"",
])
def test_allowlist_rejects(cmd):
    assert not is_command_allowed(cmd)


@pytest.mark.parametrize("code", ["*808#", "*123*1#", "+62812"])
def test_ussd_code_ok(code):
    validate_ussd_code(code)


@pytest.mark.parametrize("code", ["", '*1#";AT', "a" * 40])
def test_ussd_code_bad(code):
    with pytest.raises(Exception):
        validate_ussd_code(code)


def test_migration_from_legacy_slots(tmp_path):
    path = tmp_path / "old.db"
    con = sqlite3.connect(path)
    con.executescript("""
        CREATE TABLE modem_pools (id VARCHAR(50) PRIMARY KEY, name TEXT, port_count INTEGER, description TEXT, created_at DATETIME);
        CREATE TABLE ports (id INTEGER PRIMARY KEY, device_id VARCHAR(50) UNIQUE, pool_id VARCHAR(50), baudrate INTEGER,
                            is_active BOOLEAN, last_error TEXT, last_seen_at DATETIME);
        CREATE TABLE slots (
            id INTEGER NOT NULL, pool_id VARCHAR(50) NOT NULL, slot_index INTEGER NOT NULL,
            physical_port_id VARCHAR(50), status VARCHAR(12) NOT NULL, updated_at DATETIME NOT NULL,
            PRIMARY KEY (id), FOREIGN KEY(pool_id) REFERENCES modem_pools (id),
            UNIQUE (physical_port_id), FOREIGN KEY(physical_port_id) REFERENCES ports (id));
        INSERT INTO modem_pools VALUES ('a','A',1,'',CURRENT_TIMESTAMP),('b','B',1,'',CURRENT_TIMESTAMP);
        CREATE TABLE user_accounts (id INTEGER PRIMARY KEY, username TEXT, password TEXT, role TEXT, pool_id TEXT,
            agent_token TEXT, is_active BOOLEAN DEFAULT 1, created_at DATETIME, updated_at DATETIME);
        INSERT INTO user_accounts VALUES (7,'alice','x','operator','a',NULL,1,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP);
        INSERT INTO ports VALUES (1,'COM3','a',115200,1,NULL,NULL);
        INSERT INTO slots VALUES (1,'a',1,1,'READY',CURRENT_TIMESTAMP);
    """)
    con.commit()
    con.close()

    engine = create_engine(f"sqlite:///{path}")
    run_migrations(engine)
    run_migrations(engine)  # idempoten

    with engine.begin() as c:
        # port yang sama harus boleh dipakai pool lain
        c.execute(text("INSERT INTO slots (pool_id, slot_index, physical_port_id, status, updated_at) VALUES ('b',1,'1','EMPTY',CURRENT_TIMESTAMP)"))
        assert c.execute(text("SELECT COUNT(*) FROM slots")).scalar() == 2
        assert c.execute(text("PRAGMA user_version")).scalar() >= 4
        owners = dict(c.execute(text("SELECT id, owner_id FROM modem_pools")).fetchall())
        assert owners == {"a": 7, "b": None}  # pool lama dimiliki akun yang menunjuknya
    assert "initial_scan_at" in {c["name"] for c in inspect(engine).get_columns("sim_sessions")} \
        or "sim_sessions" not in inspect(engine).get_table_names()
