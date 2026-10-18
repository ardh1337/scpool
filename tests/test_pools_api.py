"""API unit modem pool: daftar, ganti nama, pindah port, hapus unit kosong, dan isolasi antar akun."""

import unittest
from datetime import datetime, timezone

from fastapi.testclient import TestClient

from app.auth.security import hash_password
from app.database.bootstrap import init_db
from app.database.connection import SessionLocal
from app.database.models import ModemPool, SMSMessage, SMSStatus, Slot, SlotNumbering, UserAccount, UserSession
from app.main import app
from app.services.accounts import purge_pool
from app.services.units import sync_detected_ports

REMOTE = {"cf-connecting-ip": "10.20.30.40"}
PASSWORD = "Passw0rd-QA1"


def _purge_user(db, username):
    user = db.query(UserAccount).filter(UserAccount.username == username).first()
    if not user:
        return
    for pool in db.query(ModemPool).filter(ModemPool.owner_id == user.id).all():
        purge_pool(db, pool.id)
    db.query(UserSession).filter(UserSession.user_id == user.id).delete()
    db.delete(user)
    db.commit()


class TestPoolsAPI(unittest.TestCase):

    def setUp(self):
        init_db()
        self.client = TestClient(app)
        self.db = SessionLocal()
        for name in ("pool_alice", "pool_bob"):
            _purge_user(self.db, name)
        self.ids = {}
        for name in ("pool_alice", "pool_bob"):
            user = UserAccount(username=name, password=hash_password(PASSWORD), role="operator", is_active=True)
            self.db.add(user)
            self.db.commit()
            self.ids[name] = user.id
        sync_detected_ports(self.db, self.ids["pool_alice"], [
            ("usb:1-4", [f"COM{n}" for n in range(3, 35)]), ("usb:1-5", [f"COM{n}" for n in range(40, 56)])])
        sync_detected_ports(self.db, self.ids["pool_bob"], [("usb:1-4", ["COM3", "COM4"])])
        self.db.commit()
        self.alice = self._login("pool_alice")
        self.bob = self._login("pool_bob")

    def tearDown(self):
        for name in ("pool_alice", "pool_bob"):
            _purge_user(self.db, name)
        self.db.close()

    def _login(self, username):
        r = self.client.post("/api/auth/login", json={"username": username, "password": PASSWORD}, headers=REMOTE)
        return {"Authorization": f"Bearer {r.json()['token']}", **REMOTE}

    def test_list_shows_detected_sizes(self):
        pools = self.client.get("/api/pools", headers=self.alice).json()
        self.assertEqual(sorted(p["port_count"] for p in pools), [16, 32])
        self.assertEqual([p["port_count"] for p in self.client.get("/api/pools", headers=self.bob).json()], [2])

    def test_rename_and_validation(self):
        big = next(p for p in self.client.get("/api/pools", headers=self.alice).json() if p["port_count"] == 32)
        r = self.client.put(f"/api/pools/{big['id']}", json={"name": "  Kantor Pusat  "}, headers=self.alice)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["name"], "Kantor Pusat")
        for bad in ("", "   ", "x" * 61):
            self.assertEqual(self.client.put(f"/api/pools/{big['id']}", json={"name": bad},
                                             headers=self.alice).status_code, 400)
        r = self.client.put(f"/api/pools/{big['id']}", json={"name": "Gudang\u0007 A"}, headers=self.alice)
        self.assertEqual(r.json()["name"], "Gudang A")  # karakter kontrol dibuang
        # nama tampil di status unit tersebut
        st = self.client.get(f"/api/status?pool_id={big['id']}", headers=self.alice).json()
        self.assertEqual((st["pool_name"], st["total_slots"]), ("Gudang A", 32))

    def test_other_account_cannot_see_or_change(self):
        alice_pool = self.client.get("/api/pools", headers=self.alice).json()[0]["id"]
        self.assertEqual(self.client.put(f"/api/pools/{alice_pool}", json={"name": "hack"}, headers=self.bob).status_code, 404)
        self.assertEqual(self.client.delete(f"/api/pools/{alice_pool}", headers=self.bob).status_code, 404)
        self.assertEqual(self.client.post(f"/api/pools/{alice_pool}/move-ports", headers=self.bob,
                                          json={"slot_indices": [1], "new_pool_name": "x"}).status_code, 404)
        for path in ("slots", "status", "sms", "events"):
            self.assertEqual(self.client.get(f"/api/{path}?pool_id={alice_pool}", headers=self.bob).status_code, 404)
        self.assertEqual(self.client.post(f"/api/slots/1/ussd?pool_id={alice_pool}", headers=self.bob).status_code, 404)

    def test_move_ports_keeps_history_and_blocks_cross_account_targets(self):
        pools = self.client.get("/api/pools", headers=self.alice).json()
        small = next(p for p in pools if p["port_count"] == 16)
        big = next(p for p in pools if p["port_count"] == 32)
        bob_pool = self.client.get("/api/pools", headers=self.bob).json()[0]["id"]

        slot = self.db.query(Slot).filter(Slot.pool_id == small["id"], Slot.slot_index == 3).one()
        self.db.add(SMSMessage(slot_id=slot.id, sender_raw="BANK", sender_normalized="BANK", body="riwayat",
                               body_hash="h" * 8,
                               smsc_timestamp=datetime.now(timezone.utc), status=SMSStatus.PERLU_DITINJAU))
        self.db.commit()
        slot_id, port = slot.id, slot.physical_port_id

        r = self.client.post(f"/api/pools/{small['id']}/move-ports", headers=self.alice,
                             json={"slot_indices": [3], "target_pool_id": bob_pool})
        self.assertEqual(r.status_code, 404)

        r = self.client.post(f"/api/pools/{small['id']}/move-ports", headers=self.alice,
                             json={"slot_indices": [3], "target_pool_id": big["id"]})
        self.assertEqual(r.status_code, 200, r.text)
        self.db.expire_all()
        moved = self.db.get(Slot, slot_id)
        self.assertEqual((moved.pool_id, moved.slot_index, moved.physical_port_id), (big["id"], 33, port))
        self.assertEqual(self.db.query(SMSMessage).filter(SMSMessage.slot_id == slot_id).count(), 1)
        counts = {p["id"]: p["port_count"] for p in self.client.get("/api/pools", headers=self.alice).json()}
        self.assertEqual((counts[small["id"]], counts[big["id"]]), (15, 33))
        # sumber tidak digeser (I-3): slot 3 hilang, slot lain tetap
        idx = [s["slot_index"] for s in self.client.get(f"/api/slots?pool_id={small['id']}", headers=self.alice).json()]
        self.assertNotIn(3, idx)
        self.assertIn(4, idx)

    def test_move_to_new_unit_then_delete_empty_one(self):
        small = next(p for p in self.client.get("/api/pools", headers=self.alice).json() if p["port_count"] == 16)
        both = {"slot_indices": [1], "target_pool_id": "x", "new_pool_name": "y"}
        self.assertEqual(self.client.post(f"/api/pools/{small['id']}/move-ports", headers=self.alice,
                                          json=both).status_code, 400)
        r = self.client.post(f"/api/pools/{small['id']}/move-ports", headers=self.alice,
                             json={"slot_indices": list(range(1, 17)), "new_pool_name": "Cadangan"})
        self.assertEqual(r.status_code, 200, r.text)
        new_id = r.json()["target_pool_id"]
        self.assertEqual(self.client.delete(f"/api/pools/{new_id}", headers=self.alice).status_code, 400)  # berisi
        self.assertEqual(self.client.delete(f"/api/pools/{small['id']}", headers=self.alice).status_code, 200)
        names = sorted(p["name"] for p in self.client.get("/api/pools", headers=self.alice).json())
        self.assertIn("Cadangan", names)
        self.assertEqual(len(names), 2)

    def test_redetect_does_not_undo_manual_move(self):
        small = next(p for p in self.client.get("/api/pools", headers=self.alice).json() if p["port_count"] == 16)
        big = next(p for p in self.client.get("/api/pools", headers=self.alice).json() if p["port_count"] == 32)
        self.client.post(f"/api/pools/{small['id']}/move-ports", headers=self.alice,
                         json={"slot_indices": [1, 2], "target_pool_id": big["id"]})
        sync_detected_ports(self.db, self.ids["pool_alice"], [
            ("usb:1-4", [f"COM{n}" for n in range(3, 35)]), ("usb:1-5", [f"COM{n}" for n in range(40, 56)])])
        self.db.commit()
        counts = {p["id"]: p["port_count"] for p in self.client.get("/api/pools", headers=self.alice).json()}
        self.assertEqual((counts[small["id"]], counts[big["id"]]), (14, 34))

    def test_slot_numbering_rows_cleared_when_moved(self):
        pool = self.client.get("/api/pools", headers=self.alice).json()[0]
        slot = self.db.query(Slot).filter(Slot.pool_id == pool["id"]).order_by(Slot.slot_index).first()
        self.db.add(SlotNumbering(pool_id=pool["id"], slot_id=slot.id, sequence_number=1, is_active=True))
        self.db.commit()
        self.client.post(f"/api/pools/{pool['id']}/move-ports", headers=self.alice,
                         json={"slot_indices": [slot.slot_index], "new_pool_name": "Baru"})
        self.assertEqual(self.db.query(SlotNumbering).filter(SlotNumbering.slot_id == slot.id).count(), 0)

    def test_empty_account_gets_empty_views_not_errors(self):
        carol = UserAccount(username="pool_carol", password=hash_password(PASSWORD), role="operator", is_active=True)
        self.db.add(carol)
        self.db.commit()
        try:
            h = self._login("pool_carol")
            for path in ("slots", "sms", "events", "pools"):
                r = self.client.get(f"/api/{path}", headers=h)
                self.assertEqual((r.status_code, r.json()), (200, []), path)
            st = self.client.get("/api/status", headers=h).json()
            self.assertEqual((st["pool_id"], st["total_slots"]), (None, 0))
            self.assertEqual(self.client.post("/api/poll-sms", headers=h).status_code, 404)
        finally:
            _purge_user(self.db, "pool_carol")


if __name__ == "__main__":
    unittest.main()
