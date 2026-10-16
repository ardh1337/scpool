"""
Unit Test Endpoint API Dashboard (Tahap M8)
"""

import unittest
from fastapi.testclient import TestClient
from app.main import app
from app.database.models import init_db


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


if __name__ == "__main__":
    unittest.main()
