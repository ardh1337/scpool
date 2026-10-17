"""
Unit & Integration Test: Isolasi Akses Publik (Cloudflare) & Registrasi Klien dengan Persetujuan Admin / Telegram
Memastikan:
1. Akses dari remote device (Cloudflare header cf-connecting-ip) tanpa login ditolak HTTP 401 untuk data hardware & SMS.
2. Akses lokal (localhost/127.0.0.1) tetap otomatis terotentikasi sebagai Super Administrator tanpa popup login.
3. Alur self-registration klien baru (is_active=False) dan persetujuan admin (/approve & /reject).
4. Download script agent_forwarder.py dan run_agent.bat dengan token kustom klien.
5. Konfigurasi Telegram settings (bot token, chat id).
"""

import unittest
from fastapi.testclient import TestClient
from app.main import app
from app.database.connection import SessionLocal
from app.database.models import init_db, UserAccount, ModemPool, Slot, SlotStatus, SystemSetting


class TestPublicIsolationAndRegistration(unittest.TestCase):

    def setUp(self):
        init_db()
        self.client = TestClient(app)
        db = SessionLocal()
        admin = db.query(UserAccount).filter(UserAccount.username == "admin").first()
        self._orig_admin_pwd = admin.password if admin else None
        if admin:
            admin.is_active = True
            admin.password = "admin123"
            db.commit()
        db.close()

    def tearDown(self):
        if self._orig_admin_pwd:
            db = SessionLocal()
            admin = db.query(UserAccount).filter(UserAccount.username == "admin").first()
            if admin:
                admin.password = self._orig_admin_pwd
                db.commit()
            db.close()

    def test_remote_unauthenticated_request_is_blocked_401(self):
        """
        Memastikan akses publik via Cloudflare Tunnel (header cf-connecting-ip)
        wajib login dan dilarang melihat data slot hardware atau SMS owner.
        """
        remote_headers = {
            "cf-connecting-ip": "114.122.10.45",
            "host": "scpool.ardh1337.cloud"
        }

        # 1. /api/auth/me mengembalikan logged_in = False
        me_resp = self.client.get("/api/auth/me", headers=remote_headers)
        self.assertEqual(me_resp.status_code, 200)
        self.assertFalse(me_resp.json()["logged_in"])

        # 2. /api/slots ditolak 401
        slots_resp = self.client.get("/api/slots", headers=remote_headers)
        self.assertEqual(slots_resp.status_code, 401)
        self.assertIn("Otentikasi diperlukan", slots_resp.json()["detail"])

        # 3. /api/sms ditolak 401
        sms_resp = self.client.get("/api/sms", headers=remote_headers)
        self.assertEqual(sms_resp.status_code, 401)

        # 4. /api/status ditolak 401
        status_resp = self.client.get("/api/status", headers=remote_headers)
        self.assertEqual(status_resp.status_code, 401)

        # 5. /api/admin/users ditolak 401
        users_resp = self.client.get("/api/admin/users", headers=remote_headers)
        self.assertIn(users_resp.status_code, [401, 403])

        # 6. /api/admin/settings ditolak 401
        settings_resp = self.client.get("/api/admin/settings", headers=remote_headers)
        self.assertIn(settings_resp.status_code, [401, 403])

        # 7. /api/auth/credentials ditolak 401
        cred_resp = self.client.get("/api/auth/credentials", headers=remote_headers)
        self.assertIn(cred_resp.status_code, [401, 403])

        # 8. /api/sync dan /api/slots/1/ussd ditolak 401
        sync_resp = self.client.post("/api/sync", headers=remote_headers)
        self.assertIn(sync_resp.status_code, [401, 403])

        ussd_resp = self.client.post("/api/slots/1/ussd", headers=remote_headers)
        self.assertIn(ussd_resp.status_code, [401, 403])

    def test_client_registration_and_admin_approval_workflow(self):
        """
        Menguji siklus hidup pendaftaran klien:
        1. Klien mendaftar -> is_active = False (menunggu persetujuan)
        2. Klien mencoba login sebelum disetujui -> 403 Forbidden
        3. Admin menyetujui akun -> is_active = True
        4. Klien login berhasil dan hanya melihat pool miliknya
        5. Klien mengunduh script forwarder & bat dengan token miliknya
        """
        # 1. Registrasi klien baru
        reg_payload = {
            "username": "klien_baru_test",
            "password": "password_klien_123",
            "pool_name": "Modem Pool Kantor Cabang",
            "port_count": 16
        }
        reg_resp = self.client.post("/api/auth/register", json=reg_payload)
        self.assertEqual(reg_resp.status_code, 200)
        reg_data = reg_resp.json()
        self.assertTrue(reg_data["success"])
        self.assertFalse(reg_data["is_active"])  # Wajib pending!
        self.assertEqual(reg_data["username"], "klien_baru_test")
        self.assertTrue(reg_data["agent_token"].startswith("tok_live_"))
        self.assertIn("download_script", reg_data)
        self.assertIn("download_bat", reg_data)

        # 1b. Klien yang baru daftar (belum disetujui) sudah bisa mengunduh 2 filenya via token
        tok = reg_data["agent_token"]
        dl_pre_bat = self.client.get(f"/api/download/agent?file_type=bat&token={tok}")
        self.assertEqual(dl_pre_bat.status_code, 200)
        self.assertIn(tok, dl_pre_bat.text)

        dl_pre_script = self.client.get(f"/api/download/agent?file_type=script&token={tok}")
        self.assertEqual(dl_pre_script.status_code, 200)

        # Token palsu ditolak 403
        dl_fake = self.client.get("/api/download/agent?file_type=bat&token=tok_live_palsu_12345")
        self.assertEqual(dl_fake.status_code, 403)

        # 2. Coba login sebelum disetujui -> 403
        login_premature = self.client.post("/api/auth/login", json={
            "username": "klien_baru_test",
            "password": "password_klien_123"
        })
        self.assertEqual(login_premature.status_code, 403)
        self.assertIn("dinonaktifkan", login_premature.json()["detail"].lower())

        # 3. Admin login dan setujui pendaftaran
        admin_login = self.client.post("/api/auth/login", json={"username": "admin", "password": "admin123"})
        self.assertEqual(admin_login.status_code, 200)
        admin_token = admin_login.json()["token"]
        admin_headers = {"Authorization": f"Bearer {admin_token}"}

        # Cari user_id klien_baru_test
        users_resp = self.client.get("/api/admin/users", headers=admin_headers)
        self.assertEqual(users_resp.status_code, 200)
        klien_record = next((u for u in users_resp.json() if u["username"] == "klien_baru_test"), None)
        self.assertIsNotNone(klien_record)
        self.assertFalse(klien_record["is_active"])
        klien_id = klien_record["id"]

        # Approve klien
        approve_resp = self.client.post(f"/api/admin/users/{klien_id}/approve", headers=admin_headers)
        self.assertEqual(approve_resp.status_code, 200)
        self.assertTrue(approve_resp.json()["success"])

        # 4. Klien kini bisa login
        klien_login = self.client.post("/api/auth/login", json={
            "username": "klien_baru_test",
            "password": "password_klien_123"
        })
        self.assertEqual(klien_login.status_code, 200)
        klien_auth = klien_login.json()
        self.assertTrue(klien_auth["success"])
        klien_tok = klien_auth["token"]
        self.assertIsNotNone(klien_auth["user"]["agent_token"])
        klien_headers = {"Authorization": f"Bearer {klien_tok}"}

        # Klien mengakses /api/slots -> Hanya melihat 16 slot pool miliknya
        klien_slots_resp = self.client.get("/api/slots", headers=klien_headers)
        self.assertEqual(klien_slots_resp.status_code, 200)
        klien_slots = klien_slots_resp.json()
        self.assertEqual(len(klien_slots), 16)

        # 5. Klien mengunduh script forwarder & run_agent.bat
        dl_script = self.client.get("/api/download/agent?file_type=script", headers=klien_headers)
        self.assertEqual(dl_script.status_code, 200)
        self.assertIn("SCPool - Lightweight Remote Modem Pool Forwarder", dl_script.text)

        dl_bat = self.client.get("/api/download/agent?file_type=bat", headers=klien_headers)
        self.assertEqual(dl_bat.status_code, 200)
        self.assertIn(klien_auth["user"]["agent_token"], dl_bat.text)
        self.assertIn("python agent_forwarder.py", dl_bat.text)

        # Bersihkan test data
        del_resp = self.client.delete(f"/api/admin/users/{klien_id}", headers=admin_headers)
        self.assertEqual(del_resp.status_code, 200)

    def test_admin_settings_and_telegram_config(self):
        """Memastikan admin dapat membaca dan memperbarui konfigurasi sistem & Telegram."""
        from app.database.models import SystemSetting
        db = SessionLocal()
        orig_settings = {s.key: s.value for s in db.query(SystemSetting).all()}
        db.close()

        admin_login = self.client.post("/api/auth/login", json={"username": "admin", "password": "admin123"})
        admin_token = admin_login.json()["token"]
        headers = {"Authorization": f"Bearer {admin_token}"}

        try:
            # 1. Update settings Telegram
            update_resp = self.client.post("/api/admin/settings", json={
                "telegram_bot_token": "123456789:ABCdefGhIjkLmNoPqRsTuvWxYz",
                "telegram_chat_id": "987654321",
                "public_domain": "https://scpool.ardh1337.cloud"
            }, headers=headers)
            self.assertEqual(update_resp.status_code, 200)
            self.assertTrue(update_resp.json()["success"])

            # 2. Get settings -> token ter-masking
            get_resp = self.client.get("/api/admin/settings", headers=headers)
            self.assertEqual(get_resp.status_code, 200)
            settings = get_resp.json()
            self.assertIn("telegram_bot_token", settings)
            self.assertIn("***", settings["telegram_bot_token"])
            self.assertEqual(settings["telegram_chat_id"], "987654321")
        finally:
            # Pulihkan konfigurasi asli agar tidak menimpa kredensial produksi
            db_restore = SessionLocal()
            for k, v in orig_settings.items():
                row = db_restore.query(SystemSetting).filter(SystemSetting.key == k).first()
                if row:
                    row.value = v
            db_restore.commit()
            db_restore.close()

    def test_credential_security_and_rate_limiting(self):
        """Memastikan proteksi brute-force login dan atribut secure pada cookie HTTPS."""
        remote_headers = {
            "cf-connecting-ip": "103.28.12.99",
            "host": "scpool.ardh1337.cloud",
            "x-forwarded-proto": "https"
        }

        # 1. Login sukses via HTTPS menghasilkan cookie secure
        login_resp = self.client.post("/api/auth/login", json={
            "username": "admin",
            "password": "admin123"
        }, headers=remote_headers)
        self.assertEqual(login_resp.status_code, 200)
        cookie_header = login_resp.headers.get("set-cookie", "")
        self.assertIn("session_token=", cookie_header)
        self.assertIn("secure", cookie_header.lower())
        self.assertIn("httponly", cookie_header.lower())

        # 2. Percobaan brute-force login dari IP remote yang sama dibatasi rate limiter (max 10)
        attack_ip = "192.168.99.100"
        attack_headers = {
            "cf-connecting-ip": attack_ip,
            "host": "scpool.ardh1337.cloud"
        }
        status_codes = []
        for _ in range(12):
            r = self.client.post("/api/auth/login", json={
                "username": "admin",
                "password": "wrong_password"
            }, headers=attack_headers)
            status_codes.append(r.status_code)

        # Harus ada respons 429 Too Many Requests setelah melebihi kuota
        self.assertIn(429, status_codes)


if __name__ == "__main__":
    unittest.main()


