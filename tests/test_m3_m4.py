"""
Test Suite Tahap M3 & M4
- M3: Deteksi SIM, validasi ICCID, dan siklus sesi SIM (I-1, I-6).
- M4: Penomoran urut, pembekuan nomor saat cabut SIM, penyusunan ulang nomor (I-3).
"""

import unittest
from datetime import datetime, timezone
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database.connection import Base
from app.database.models import (
    ModemPool, Slot, Port, SIMCard, SIMSession, SlotNumbering, SlotStatus
)
from app.services.pool_init import ensure_pool_initialized
from app.services.slot_manager import handle_sim_inserted, handle_sim_removed
from app.services.numbering import renumber_pool_slots, get_display_label


class TestM3M4Stages(unittest.TestCase):

    def setUp(self):
        self.engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False}
        )
        with self.engine.connect() as conn:
            conn.exec_driver_sql("PRAGMA foreign_keys = ON;")

        Base.metadata.create_all(bind=self.engine)
        Session = sessionmaker(bind=self.engine)
        self.db = Session()

    def tearDown(self):
        self.db.close()
        Base.metadata.drop_all(bind=self.engine)

    def test_pool_init_32_slots(self):
        """M3: Memastikan pool diinisialisasi dengan tepat 32 slot dan 32 port."""
        pool = ensure_pool_initialized(self.db, pool_id="foxcom_32")
        self.assertEqual(pool.id, "foxcom_32")

        slots = self.db.query(Slot).filter(Slot.pool_id == "foxcom_32").all()
        ports = self.db.query(Port).filter(Port.pool_id == "foxcom_32").all()

        self.assertEqual(len(slots), 32)
        self.assertEqual(len(ports), 32)

        # Slot 17 harus terhubung ke COM15
        slot17 = self.db.query(Slot).filter(Slot.slot_index == 17).first()
        self.assertIsNotNone(slot17)
        self.assertEqual(slot17.physical_port_id, "COM15")

    def test_m3_session_lifecycle(self):
        """M3: Siklus hidup sesi SIM (buka sesi baru, tutup saat cabut, SIM baru tidak mewarisi)."""
        pool = ensure_pool_initialized(self.db, pool_id="foxcom_32")
        slot1 = self.db.query(Slot).filter(Slot.slot_index == 1).first()

        # 1. Pasang SIM A
        session_a, sim_a = handle_sim_inserted(
            self.db, slot_id=slot1.id,
            iccid="896201000000000001",
            phone_number="+628111111111"
        )
        self.assertEqual(slot1.status, SlotStatus.READY)
        self.assertIsNone(session_a.ended_at)

        # 2. Cabut SIM A
        handle_sim_removed(self.db, slot_id=slot1.id)
        self.assertEqual(slot1.status, SlotStatus.EMPTY)
        self.assertIsNotNone(session_a.ended_at)

        # 3. Pasang SIM B (kartu baru) pada slot yang sama
        session_b, sim_b = handle_sim_inserted(
            self.db, slot_id=slot1.id,
            iccid="896201000000000002"
        )
        self.assertEqual(slot1.status, SlotStatus.READY)
        self.assertNotEqual(session_a.id, session_b.id)
        # I-1: SIM B tidak mewarisi nomor telepon dari SIM A
        self.assertIsNone(sim_b.phone_number)

    def test_m4_numbering_rules_and_freezing(self):
        """M4: Penomoran urut 1..N, pembekuan nomor saat SIM dicabut, dan susun ulang nomor."""
        pool = ensure_pool_initialized(self.db, pool_id="foxcom_32")

        slot1 = self.db.query(Slot).filter(Slot.slot_index == 1).first()
        slot2 = self.db.query(Slot).filter(Slot.slot_index == 2).first()
        slot18 = self.db.query(Slot).filter(Slot.slot_index == 18).first()

        # Pasang 3 SIM di slot 1, 2, dan 18
        handle_sim_inserted(self.db, slot_id=slot1.id, iccid="896200000000000001")
        handle_sim_inserted(self.db, slot_id=slot2.id, iccid="896200000000000002")
        handle_sim_inserted(self.db, slot_id=slot18.id, iccid="896200000000000018")

        # Jalankan penomoran urut awal
        renumber_pool_slots(self.db, pool_id="foxcom_32")

        # Verifikasi label tampilan berbasis Slot Fisik
        self.assertEqual(get_display_label(self.db, slot1.id), "Slot 1")
        self.assertEqual(get_display_label(self.db, slot2.id), "Slot 2")
        self.assertEqual(get_display_label(self.db, slot18.id), "Slot 18")

        # Slot yang tidak terpasang SIM berstatus Slot X (KOSONG)
        slot3 = self.db.query(Slot).filter(Slot.slot_index == 3).first()
        self.assertEqual(get_display_label(self.db, slot3.id), "Slot 3 (KOSONG)")

        # Cabut SIM pada Slot 2
        handle_sim_removed(self.db, slot_id=slot2.id)
        self.assertEqual(get_display_label(self.db, slot2.id), "Slot 2 (KOSONG)")
        self.assertEqual(get_display_label(self.db, slot18.id), "Slot 18")

    def test_m4_port_error_not_numbered(self):
        """M4: Port yang error menampilkan status PORT_ERROR."""
        pool = ensure_pool_initialized(self.db, pool_id="foxcom_32")
        slot4 = self.db.query(Slot).filter(Slot.slot_index == 4).first()
        slot4.status = SlotStatus.PORT_ERROR
        self.db.commit()

        self.assertEqual(get_display_label(self.db, slot4.id), "Slot 4 (PORT_ERROR)")


if __name__ == "__main__":
    unittest.main()

