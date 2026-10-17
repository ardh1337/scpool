"""
Unit Test Otentikasi, Manajemen Pengguna & Isolasi Multi-Tenant
"""

import unittest
from fastapi.testclient import TestClient
from app.main import app
from app.database.models import init_db, UserAccount, ModemPool
from app.database.connection import SessionLocal


class TestAuthMultiTenant(unittest.TestCase):

    def setUp(self):
        init_db()
        db = SessionLocal()
        admin = db.query(UserAccount).filter(UserAccount.username == "admin").first()
        self._orig_admin_pwd = admin.password if admin else None
        if admin:
            admin.password = "admin123"
        old_budi = db.query(UserAccount).filter(UserAccount.username == "teman_budi").first()
        if old_budi:
            db.delete(old_budi)
        old_pool = db.query(ModemPool).filter(ModemPool.id == "pool_teman_budi").first()
        if old_pool:
            db.delete(old_pool)
        db.commit()
        db.close()
        self.client = TestClient(app)

    def tearDown(self):
        if self._orig_admin_pwd:
            db = SessionLocal()
            admin = db.query(UserAccount).filter(UserAccount.username == "admin").first()
            if admin:
                admin.password = self._orig_admin_pwd
                db.commit()
            db.close()

    def test_login_success_and_logout(self):
        """Memastikan login admin berhasil mendapatkan token dan bisa logout."""
        resp = self.client.post("/api/auth/login", json={"username": "admin", "password": "admin123"})
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data["success"])
        self.assertIn("token", data)
        self.assertEqual(data["user"]["username"], "admin")
        self.assertEqual(data["user"]["role"], "superadmin")

        token = data["token"]

        # Cek /api/auth/me dengan Bearer token
        me_resp = self.client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
        self.assertEqual(me_resp.status_code, 200)
        me_data = me_resp.json()
        self.assertTrue(me_data["logged_in"])
        self.assertEqual(me_data["username"], "admin")

        # Logout
        logout_resp = self.client.post("/api/auth/logout", headers={"Authorization": f"Bearer {token}"})
        self.assertEqual(logout_resp.status_code, 200)

        # Cek /api/auth/me setelah logout
        me_after = self.client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
        self.assertEqual(me_after.status_code, 200)
        self.assertFalse(me_after.json()["logged_in"])

    def test_login_failure(self):
        """Memastikan password salah menghasilkan HTTP 401."""
        resp = self.client.post("/api/auth/login", json={"username": "admin", "password": "wrongpassword!"})
        self.assertEqual(resp.status_code, 401)

    def test_admin_user_crud_and_pool_creation(self):
        """Memastikan admin bisa membuat akun klien baru beserta pool dan agent token unik."""
        # 1. Login sebagai admin
        login_resp = self.client.post("/api/auth/login", json={"username": "admin", "password": "admin123"})
        token = login_resp.json()["token"]
        headers = {"Authorization": f"Bearer {token}"}

        # 2. Buat akun teman baru
        new_user_payload = {
            "username": "teman_budi",
            "password": "password_budi_123",
            "role": "operator",
            "pool_name": "Foxcom Pool Budi",
            "port_count": 32
        }
        create_resp = self.client.post("/api/admin/users", json=new_user_payload, headers=headers)
        self.assertEqual(create_resp.status_code, 200)
        created = create_resp.json()
        self.assertTrue(created["success"])
        self.assertIn("scpool_", created["agent_token"])
        self.assertEqual(created["pool_id"], "pool_teman_budi")

        user_id = created["user_id"]

        # 3. List users
        users_resp = self.client.get("/api/admin/users", headers=headers)
        self.assertEqual(users_resp.status_code, 200)
        user_list = users_resp.json()
        budi_entry = next((u for u in user_list if u["username"] == "teman_budi"), None)
        self.assertIsNotNone(budi_entry)
        self.assertEqual(budi_entry["pool_id"], "pool_teman_budi")

        # 4. Login sebagai Budi
        budi_login = self.client.post("/api/auth/login", json={"username": "teman_budi", "password": "password_budi_123"})
        self.assertEqual(budi_login.status_code, 200)
        budi_token = budi_login.json()["token"]
        budi_headers = {"Authorization": f"Bearer {budi_token}"}

        # 5. Budi cek slots -> hanya melihat slot pool_teman_budi miliknya
        budi_slots = self.client.get("/api/slots", headers=budi_headers)
        self.assertEqual(budi_slots.status_code, 200)
        self.assertEqual(len(budi_slots.json()), 32)

        # 6. Budi tidak boleh mengakses menu admin
        budi_admin_access = self.client.get("/api/admin/users", headers=budi_headers)
        self.assertEqual(budi_admin_access.status_code, 403)

        # 7. Bersihkan: admin menghapus user teman_budi
        del_resp = self.client.delete(f"/api/admin/users/{user_id}", headers=headers)
        self.assertEqual(del_resp.status_code, 200)

        # 8. Verifikasi proteksi administrator: Admin tidak boleh dihapus (meskipun ID-nya bukan 1)
        admin_id = login_resp.json()["user"]["id"]
        del_admin_resp = self.client.delete(f"/api/admin/users/{admin_id}", headers=headers)
        self.assertEqual(del_admin_resp.status_code, 400)
        self.assertIn("Administrator", del_admin_resp.json()["detail"])


if __name__ == "__main__":
    unittest.main()
