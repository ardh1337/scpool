"""
Unit Test Endpoint API Dashboard (Tahap M8)
"""

import unittest
from fastapi.testclient import TestClient
from app.main import app
from app.database.bootstrap import init_db


class TestAPIEndpoints(unittest.TestCase):

    def setUp(self):
        init_db()
        self.client = TestClient(app)

    def test_status_endpoint(self):
        """Memastikan endpoint /api/status mengembalikan ringkasan pool."""
        resp = self.client.get("/api/status")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("total_slots", data)
        self.assertEqual(data["total_slots"], 32)
        self.assertIn("ready_count", data)

    def test_slots_endpoint(self):
        """Memastikan endpoint /api/slots mengembalikan tepat 32 slot berurutan."""
        resp = self.client.get("/api/slots")
        self.assertEqual(resp.status_code, 200)
        slots = resp.json()
        self.assertEqual(len(slots), 32)
        # Slot pertama adalah Slot 1
        self.assertEqual(slots[0]["slot_index"], 1)
        # Slot ke-17 adalah Slot 17
        self.assertEqual(slots[16]["slot_index"], 17)

    def test_sms_feed_endpoint(self):
        """Memastikan endpoint /api/sms berfungsi."""
        resp = self.client.get("/api/sms")
        self.assertEqual(resp.status_code, 200)
        self.assertIsInstance(resp.json(), list)

    def test_root_serves_html(self):
        """Memastikan root / menyajikan file index.html."""
        resp = self.client.get("/")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("SIM Inventory", resp.text)

    def test_operator_profiles_list_and_update(self):
        """Memastikan API profil operator dapat diambil dan diperbarui."""
        # 1. GET /api/operators
        resp = self.client.get("/api/operators")
        self.assertEqual(resp.status_code, 200)
        profiles = resp.json()
        self.assertIsInstance(profiles, list)
        self.assertGreaterEqual(len(profiles), 5)

        # Cari profil xl_axis
        xl_axis = next((p for p in profiles if p["id"] == "xl_axis"), None)
        self.assertIsNotNone(xl_axis)
        self.assertIn("*808", xl_axis["ussd_code"])

        # 2. PUT /api/operators/xl_axis via JSON body
        new_code = "*808*7*8#"
        update_resp = self.client.put("/api/operators/xl_axis", json={"ussd_code": new_code})
        self.assertEqual(update_resp.status_code, 200)
        updated = update_resp.json()
        self.assertTrue(updated["success"])
        self.assertEqual(updated["ussd_code"], new_code)

        # 3. PUT operator tidak valid -> 404
        not_found_resp = self.client.put("/api/operators/non_existent_provider", json={"ussd_code": "*123#"})
        self.assertEqual(not_found_resp.status_code, 404)

    def test_bulk_ussd_endpoint(self):
        """Memastikan endpoint /api/ussd/bulk merespon dengan benar."""
        resp = self.client.post("/api/ussd/bulk", json={"slot_indices": [999], "code": "*808#"})
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data["success"])
        self.assertEqual(data["total_dialed"], 0)
        self.assertEqual(data["results"], [])


    def test_auth_credentials_get_and_update(self):
        """Kredensial diambil tanpa membocorkan hash; password bisa diganti dan hash baru tersimpan."""
        from app.auth.security import verify_password
        from app.database.connection import SessionLocal
        from app.database.models import UserAccount

        resp = self.client.get("/api/auth/credentials")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("username", data)
        self.assertNotIn("password", data)

        db = SessionLocal()
        try:
            original_hash = db.query(UserAccount).filter(UserAccount.username == data["username"]).one().password
        finally:
            db.close()

        try:
            update_resp = self.client.put("/api/auth/credentials", json={"password": "newsecretpassword123"})
            self.assertEqual(update_resp.status_code, 200)
            body = update_resp.json()
            self.assertTrue(body["success"])
            self.assertNotIn("password", body)

            db = SessionLocal()
            try:
                stored = db.query(UserAccount).filter(UserAccount.username == body["username"]).one().password
            finally:
                db.close()
            self.assertTrue(stored.startswith("pbkdf2_sha256$"))
            self.assertTrue(verify_password("newsecretpassword123", stored))

            too_short = self.client.put("/api/auth/credentials", json={"password": "12"})
            self.assertEqual(too_short.status_code, 400)
        finally:
            db = SessionLocal()
            try:
                db.query(UserAccount).filter(UserAccount.username == data["username"]).one().password = original_hash
                db.commit()
            finally:
                db.close()

    def test_operator_ussd_code_rejects_injection(self):
        """Kode USSD yang akan dikirim ke modem tidak boleh memuat karakter di luar 0-9 * # +."""
        resp = self.client.put("/api/operators/xl_axis", json={"ussd_code": '*808#";AT+CFUN=1,1'})
        self.assertEqual(resp.status_code, 400)

    def test_requires_login_when_remote(self):
        """Akses dari luar (header proxy) tanpa token ditolak."""
        resp = self.client.get("/api/slots", headers={"cf-connecting-ip": "203.0.113.9"})
        self.assertEqual(resp.status_code, 401)

    def test_health(self):
        resp = self.client.get("/api/health")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["status"], "ok")

    def test_security_headers_and_docs_hidden(self):
        resp = self.client.get("/")
        self.assertIn("frame-ancestors 'none'", resp.headers["content-security-policy"])
        https = self.client.get("/", headers={"x-forwarded-proto": "https"})
        self.assertIn("strict-transport-security", https.headers)
        # /docs dan /openapi.json hanya untuk yang sudah login (tests/test_api_docs.py); ReDoc tidak dipakai.
        self.assertEqual(self.client.get("/redoc").status_code, 404)
        public = TestClient(app, base_url="http://scpool.example.com")
        self.assertEqual(public.get("/openapi.json").status_code, 401)
        self.assertNotEqual(public.get("/docs", follow_redirects=False).status_code, 200)

    def test_password_change_keeps_current_bearer_session(self):
        """Ganti password lewat Bearer: sesi sendiri tetap hidup, sesi lain dicabut."""
        from app.database.connection import SessionLocal
        from app.database.models import UserAccount, UserSession
        from app.auth.security import hash_password
        db = SessionLocal()
        db.add(UserAccount(username="pwchg", password=hash_password("Passw0rd-QA1"), role="operator", is_active=True))
        db.commit()
        db.close()
        remote = {"cf-connecting-ip": "10.9.9.9"}
        tok1 = self.client.post("/api/auth/login", json={"username": "pwchg", "password": "Passw0rd-QA1"},
                                headers=remote).json()["token"]
        tok2 = self.client.post("/api/auth/login", json={"username": "pwchg", "password": "Passw0rd-QA1"},
                                headers=remote).json()["token"]
        self.client.cookies.clear()
        h1 = {"Authorization": f"Bearer {tok1}", **remote}
        no_current = self.client.put("/api/auth/credentials", headers=h1, json={"password": "Passw0rd-QA2"})
        self.assertEqual(no_current.status_code, 403)
        ok = self.client.put("/api/auth/credentials", headers=h1,
                             json={"password": "Passw0rd-QA2", "current_password": "Passw0rd-QA1"})
        self.assertEqual(ok.status_code, 200)
        self.assertTrue(self.client.get("/api/auth/me", headers=h1).json()["logged_in"])
        self.assertFalse(self.client.get("/api/auth/me", headers={"Authorization": f"Bearer {tok2}", **remote}).json()["logged_in"])
        db = SessionLocal()
        user = db.query(UserAccount).filter(UserAccount.username == "pwchg").one()
        db.query(UserSession).filter(UserSession.user_id == user.id).delete()
        db.delete(user)
        db.commit()
        db.close()


if __name__ == "__main__":
    unittest.main()

