"""Regresi keamanan: anchor regex, kebijakan password, hash sesi, validasi domain, dan escape frontend."""

import re
import unittest
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.auth.security import hash_password, hash_session_token, password_problem
from app.database.bootstrap import init_db
from app.database.connection import SessionLocal
from app.database.models import UserAccount, UserSession
from app.main import app
from app.services.accounts import purge_pool
from app.services.units import sync_detected_ports
from app.core.config import USERNAME_PATTERN
from app.core.protocol import GROUP_KEY_PATTERN, PORT_NAME_PATTERN
from app.hardware.at_allowlist import is_at_command_allowed
from app.modem.ussd import validate_ussd_code


@pytest.mark.parametrize("cmd", ["AT+CMGL=4;+CMGD=1,4", 'AT+CMGL="ALL";+CMGD=1', "AT+CMGL=4\nAT+CFUN=0", "AT+CMGR=1;+CFUN=0"])
def test_allowlist_rejects_trailing_newline_and_chaining(cmd):
    assert not is_at_command_allowed(cmd)


@pytest.mark.parametrize("cmd", ['AT+CMGL="ALL"', "AT+CMGL=4", "AT+CMGL", "AT+CMGR=3"])
def test_allowlist_still_accepts_reads(cmd):
    assert is_at_command_allowed(cmd)


def test_trailing_newline_rejected_by_all_patterns():
    assert not PORT_NAME_PATTERN.match("COM3\n")
    assert not GROUP_KEY_PATTERN.match("usb:1-4\n")
    assert not USERNAME_PATTERN.match("abc\n")
    assert validate_ussd_code("*123#\n") == "*123#"
    with pytest.raises(ValueError):
        validate_ussd_code("*123#\nAT+CFUN=0")


@pytest.mark.parametrize("pw", ["12345678", "password", "aaaaaaaaaa", "abababab", "short", "Budi12345"])
def test_weak_passwords_rejected(pw):
    assert password_problem(pw, "budi")


def test_good_password_accepted():
    assert password_problem("Kopi-Hitam-42", "budi") is None


def test_session_token_hash_is_stable_and_not_plain():
    t = "abc"
    assert hash_session_token(t) == hash_session_token(t) != t and len(hash_session_token(t)) == 64


def test_frontend_never_puts_raw_values_in_inline_js_strings():
    js = (Path(__file__).resolve().parents[1] / "app" / "web" / "app.js").read_text(encoding="utf-8")
    assert re.search(r"const esc=.*&#39;", js), "esc harus meng-escape tanda kutip tunggal"
    offenders = [l.strip()[:120] for l in js.splitlines() if "onclick=" in l and re.search(r"'\$\{esc\(", l)]
    assert not offenders, offenders
    assert "replace(/'/g" not in js



class TestApiHardening(unittest.TestCase):
    NAME = "hard_user"
    REMOTE = {"cf-connecting-ip": "10.77.0.9"}

    def setUp(self):
        init_db()
        self.client = TestClient(app)
        self.db = SessionLocal()
        self._cleanup()
        self.db.add(UserAccount(username=self.NAME, password=hash_password("Kopi-Hitam-42"), role="operator",
                                is_active=True))
        self.db.commit()

    def tearDown(self):
        self._cleanup()
        self.db.close()

    def _cleanup(self):
        user = self.db.query(UserAccount).filter(UserAccount.username == self.NAME).first()
        if user:
            self.db.query(UserSession).filter(UserSession.user_id == user.id).delete()
            self.db.delete(user)
            self.db.commit()

    def _login(self):
        r = self.client.post("/api/auth/login", json={"username": self.NAME, "password": "Kopi-Hitam-42"},
                             headers=self.REMOTE)
        return r.json()["token"]

    def test_session_token_stored_as_hash(self):
        token = self._login()
        rows = [row.token for row in self.db.query(UserSession).all()]
        self.assertNotIn(token, rows)
        self.assertIn(hash_session_token(token), rows)
        ok = self.client.get("/api/pools", headers={"Authorization": f"Bearer {token}", **self.REMOTE})
        self.assertEqual(ok.status_code, 200)

    def test_register_rejects_weak_password(self):
        r = self.client.post("/api/auth/register", json={"username": "hard_weak", "password": "12345678"},
                             headers={"cf-connecting-ip": "10.77.0.10"})
        self.assertEqual(r.status_code, 400)

    def test_pool_name_rejects_markup(self):
        token = self._login()
        h = {"Authorization": f"Bearer {token}", **self.REMOTE}
        user = self.db.query(UserAccount).filter(UserAccount.username == self.NAME).first()
        sync_detected_ports(self.db, user.id, [("usb:1-4", ["COM3"])])
        self.db.commit()
        pool = self.client.get("/api/pools", headers=h).json()[0]["id"]
        r = self.client.put(f"/api/pools/{pool}", headers=h, json={"name": "<img src=x onerror=alert(1)>"})
        self.assertEqual(r.status_code, 400)
        purge_pool(self.db, pool)
        self.db.commit()


class TestPublicDomain(unittest.TestCase):
    def test_pattern(self):
        from app.api.admin import _DOMAIN_PATTERN
        for ok in ("https://scpool.example.com", "http://10.0.0.5:8000"):
            self.assertTrue(_DOMAIN_PATTERN.match(ok))
        for bad in ("javascript:alert(1)", "https://a.com/path", "https://a.com\n", 'https://a.com"; x', "https://a b.com"):
            self.assertFalse(_DOMAIN_PATTERN.match(bad), bad)


class TestFrontendRegressions(unittest.TestCase):
    JS = (Path(__file__).resolve().parents[1] / "app" / "web" / "app.js").read_text(encoding="utf-8")

    def test_offline_port_is_not_shown_as_empty(self):
        self.assertIn("s.status === 'OFFLINE'", self.JS)

    def test_logged_out_state_renders_into_view_and_clears_data(self):
        body = self.JS.split("function renderLoggedOutState(){", 1)[1].split("\n}\n", 1)[0]
        self.assertIn("clearSessionState()", body)
        self.assertNotIn("$('#mc')", body)
        self.assertIn("poolsList = [];", self.JS.split("function clearSessionState(){", 1)[1].split("\n}\n", 1)[0])

    def test_theme_choice_persisted_and_not_reset_on_start(self):
        self.assertIn("localStorage.setItem('scpool_theme'", self.JS)
        init = self.JS.split("async function checkAuthAndInit(){", 1)[1][:120]
        self.assertIn("setThemeMode(currentThemeMode)", init)
