"""
Test Suite Aturan Keras (Invariants I-1 s.d. I-13)
Memverifikasi kepatuhan seluruh aturan keras spesifikasi (agent-spec-sim-inventory.md Bagian 2).
"""

import unittest
from datetime import datetime, timezone
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.exc import IntegrityError

from app.database.connection import Base
from app.database.models import (
    ModemPool, Slot, Port, SIMCard, SIMSession, SMSMessage, EventLog,
    SlotStatus, NumberStatus, SMSStatus
)
from app.services.slot_manager import handle_sim_inserted, handle_sim_removed
from app.services.sms_ingestion import ingest_sms, can_delete_sms_from_modem
from app.services.numbering import renumber_pool_slots, get_display_label
from app.modem.transport import VirtualTransport
from app.modem.at_client import ATClient, DisallowedATCommandError
from app.modem.sms_reader import ParsedSMS, compute_body_hash, mask_sensitive
from simulator.simulated_modem import SimulatedModem


class TestInvariants(unittest.TestCase):

    def setUp(self):
        # Database in-memory SQLite untuk pengujian cepat & terisolasi
        self.engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False}
        )
        # Aktifkan Foreign Keys di SQLite in-memory
        with self.engine.connect() as conn:
            conn.exec_driver_sql("PRAGMA foreign_keys = ON;")

        Base.metadata.create_all(bind=self.engine)
        Session = sessionmaker(bind=self.engine)
        self.db = Session()

        # Siapkan satu pool dan satu slot fisik
        self.pool = ModemPool(id="pool_test", name="Test Pool", port_count=32)
        self.port1 = Port(id="COM101", pool_id="pool_test")
        self.port2 = Port(id="COM102", pool_id="pool_test")
        self.db.add_all([self.pool, self.port1, self.port2])
        self.db.commit()

        self.slot1 = Slot(pool_id="pool_test", slot_index=1, physical_port_id="COM101")
        self.slot2 = Slot(pool_id="pool_test", slot_index=2, physical_port_id="COM102")
        self.db.add_all([self.slot1, self.slot2])
        self.db.commit()

    def tearDown(self):
        self.db.close()
        Base.metadata.drop_all(bind=self.engine)

    def test_I01_sim_swap_no_inheritance(self):
        """I-1: SIM baru tidak pernah mewarisi nomor telepon, metadata, atau SMS dari SIM sebelumnya."""
        # 1. Pasang SIM A dengan nomor telepon tertentu
        session_a, sim_a = handle_sim_inserted(
            self.db, slot_id=self.slot1.id,
            iccid="896201000000000001",
            phone_number="+628111111111"
        )
        # Masukkan SMS untuk SIM A
        sms_a = ParsedSMS(
            index=1, status_str="REC UNREAD", sender_raw="BANK-A",
            sender_normalized="BANK-A", smsc_timestamp=datetime.now(timezone.utc),
            body="OTP SIM A: 1234", body_hash=compute_body_hash("OTP SIM A: 1234")
        )
        ingest_sms(self.db, slot_id=self.slot1.id, parsed_sms=sms_a)

        # 2. Cabut SIM A
        handle_sim_removed(self.db, slot_id=self.slot1.id)

        # 3. Pasang SIM B (kartu baru tanpa nomor) pada slot yang sama
        session_b, sim_b = handle_sim_inserted(
            self.db, slot_id=self.slot1.id,
            iccid="896201000000000002",
            phone_number=None  # Nomor belum diketahui
        )

        # Verifikasi: SIM B tidak mewarisi nomor telepon dari SIM A
        self.assertNotEqual(session_a.id, session_b.id)
        self.assertIsNone(sim_b.phone_number)
        self.assertEqual(sim_b.number_status, NumberStatus.BELUM_DIKETAHUI)

        # Verifikasi: SMS dari sesi SIM A tidak terkait dengan sesi SIM B
        msgs_session_b = self.db.query(SMSMessage).filter(SMSMessage.session_id == session_b.id).all()
        self.assertEqual(len(msgs_session_b), 0)

    def test_I02_unknown_number_not_guessed(self):
        """I-2: Nomor yang tidak ditemukan berstatus BELUM DIKETAHUI dan tidak pernah ditebak."""
        session, sim = handle_sim_inserted(
            self.db, slot_id=self.slot1.id,
            iccid="896201000000000099",
            phone_number=None
        )
        self.assertEqual(sim.number_status, NumberStatus.BELUM_DIKETAHUI)
        self.assertIsNone(sim.phone_number)

    def test_I03_identity_separation_physical_port_vs_seq_num(self):
        """I-3: Dua lapis identitas: Port fisik (tetap) dan No. Urut (tampilan)."""
        session, sim = handle_sim_inserted(
            self.db, slot_id=self.slot1.id,
            iccid="896201000000000003",
            phone_number="+62812345678"
        )
        # Penomoran urut
        renumber_pool_slots(self.db, pool_id="pool_test")
        label = get_display_label(self.db, slot_id=self.slot1.id)
        self.assertEqual(label, "No. 1")

        # SMS hanya terkait ke session_id dan slot_id, bukan sequence number
        sms = ParsedSMS(
            index=1, status_str="REC UNREAD", sender_raw="TEST",
            sender_normalized="TEST", smsc_timestamp=datetime.now(timezone.utc),
            body="Hello", body_hash=compute_body_hash("Hello")
        )
        saved_sms, is_new = ingest_sms(self.db, slot_id=self.slot1.id, parsed_sms=sms)
        self.assertEqual(saved_sms.slot_id, self.slot1.id)
        self.assertEqual(saved_sms.session_id, session.id)

    def test_I04_sms_not_deleted_before_db_validated(self):
        """I-4: SMS tidak dihapus dari modem sebelum tersimpan dan tervalidasi di database."""
        # Cek ID yang belum ada di DB
        self.assertFalse(can_delete_sms_from_modem(self.db, sms_id=99999))

        # Masukkan SMS ke DB
        session, _ = handle_sim_inserted(self.db, slot_id=self.slot1.id, iccid="896201000000000004")
        sms = ParsedSMS(
            index=1, status_str="REC UNREAD", sender_raw="OPS",
            sender_normalized="OPS", smsc_timestamp=datetime.now(timezone.utc),
            body="Important Message", body_hash=compute_body_hash("Important Message")
        )
        saved, _ = ingest_sms(self.db, slot_id=self.slot1.id, parsed_sms=sms)

        # Setelah tervalidasi di DB, baru diperbolehkan jika fitur hapus diaktifkan
        self.assertTrue(can_delete_sms_from_modem(self.db, sms_id=saved.id))
        self.assertFalse(saved.deleted_from_modem)

    def test_I05_single_at_command_and_urc_separation(self):
        """I-5: Satu perintah AT per port pada satu waktu. URC dipisahkan dari balasan perintah."""
        transport = VirtualTransport(port_name="VIRTUAL_TEST")
        client = ATClient(transport=transport, default_timeout=2.0)
        client.open()

        urc_received = []
        client.register_urc_callback(lambda u: urc_received.append(u))

        # Simulasikan modem yang menyelipkan URC di tengah eksekusi perintah AT+CSQ
        def mock_modem_behavior(data):
            # Kirim URC notifikasi SMS masuk
            transport.feed_rx(b"+CMTI: \"SM\",5\r\n")
            # Lalu kirim balasan perintah sebenarnya
            transport.feed_rx(b"+CSQ: 20,99\r\n\r\nOK\r\n")

        transport.on_write_callback = mock_modem_behavior

        resp = client.send_command("AT+CSQ")

        self.assertTrue(resp.success)
        self.assertIn("+CSQ: 20,99", resp.lines)
        # URC tidak boleh tercampur di resp.lines
        self.assertNotIn("+CMTI: \"SM\",5", resp.lines)
        # URC harus masuk ke daftar URC
        self.assertEqual(len(urc_received), 1)
        self.assertEqual(urc_received[0], "+CMTI: \"SM\",5")

    def test_I06_max_one_active_session_per_slot(self):
        """I-6: Maksimal satu sesi SIM aktif per slot (ditegakkan di DB)."""
        now = datetime.now(timezone.utc)
        s1 = SIMSession(slot_id=self.slot1.id, started_at=now, ended_at=None)
        self.db.add(s1)
        self.db.commit()

        # Mencoba menambahkan sesi kedua yang juga aktif (ended_at = None) pada slot yang sama
        s2 = SIMSession(slot_id=self.slot1.id, started_at=now, ended_at=None)
        self.db.add(s2)
        with self.assertRaises(IntegrityError):
            self.db.commit()
        self.db.rollback()

    def test_I07_ambiguous_relations_marked_perlu_ditinjau(self):
        """I-7: Bila hubungan slot, SIM, dan SMS tidak pasti, tandai PERLU DITINJAU."""
        # SMS masuk ke slot1 saat tidak ada sesi SIM aktif
        sms = ParsedSMS(
            index=1, status_str="REC UNREAD", sender_raw="GHOST",
            sender_normalized="GHOST", smsc_timestamp=datetime.now(timezone.utc),
            body="Pesan tanpa sesi", body_hash=compute_body_hash("Pesan tanpa sesi")
        )
        saved, is_new = ingest_sms(self.db, slot_id=self.slot1.id, parsed_sms=sms)
        self.assertEqual(saved.status, SMSStatus.PERLU_DITINJAU)
        self.assertIsNone(saved.session_id)

    def test_I08_no_silent_data_loss(self):
        """I-8: Tidak ada data hilang diam-diam. Kegagalan atau anomali tercatat di EventLog."""
        sms = ParsedSMS(
            index=1, status_str="REC UNREAD", sender_raw="ANOMALY",
            sender_normalized="ANOMALY", smsc_timestamp=datetime.now(timezone.utc),
            body="Anomali data", body_hash=compute_body_hash("Anomali data")
        )
        ingest_sms(self.db, slot_id=self.slot1.id, parsed_sms=sms)

        # Pastikan ada log yang tercatat
        logs = self.db.query(EventLog).filter(EventLog.slot_id == self.slot1.id).all()
        self.assertGreaterEqual(len(logs), 1)
        self.assertEqual(logs[0].level, "WARN")

    def test_I09_sms_composite_deduplication(self):
        """I-9: Deduplikasi SMS memakai kunci komposit (sesi, sender mentah, timestamp SMSC, index, hash)."""
        session, _ = handle_sim_inserted(self.db, slot_id=self.slot1.id, iccid="896201000000000009")
        ts = datetime(2026, 10, 16, 8, 0, 0, tzinfo=timezone.utc)
        sms1 = ParsedSMS(
            index=3, status_str="REC READ", sender_raw="PROMO",
            sender_normalized="PROMO", smsc_timestamp=ts,
            body="Diskon 50%", body_hash=compute_body_hash("Diskon 50%")
        )

        saved1, is_new1 = ingest_sms(self.db, slot_id=self.slot1.id, parsed_sms=sms1)
        self.assertTrue(is_new1)

        # Ingest SMS yang sama kedua kalinya
        saved2, is_new2 = ingest_sms(self.db, slot_id=self.slot1.id, parsed_sms=sms1)
        self.assertFalse(is_new2)
        self.assertEqual(saved1.id, saved2.id)

    def test_I10_isolated_port_error(self):
        """I-10: Satu port error tidak boleh menghentikan port lain."""
        sim_modem1 = SimulatedModem(port_name="V1")
        sim_modem2 = SimulatedModem(port_name="V2")

        client1 = ATClient(transport=sim_modem1.transport, default_timeout=0.5)
        client2 = ATClient(transport=sim_modem2.transport, default_timeout=0.5)

        # Port 1 diset hang/timeout
        sim_modem1.hang_mode = True

        resp1 = client1.send_command("AT")
        self.assertTrue(resp1.is_timeout)
        self.assertFalse(resp1.success)

        # Port 2 harus tetap berjalan normal dan sukses
        resp2 = client2.send_command("AT")
        self.assertTrue(resp2.success)

    def test_I11_existing_sms_marked_pesan_bawaan(self):
        """I-11: Pesan yang sudah ada di kartu saat sesi dimulai ditandai PESAN BAWAAN."""
        session, _ = handle_sim_inserted(self.db, slot_id=self.slot1.id, iccid="896201000000000011")
        old_sms = ParsedSMS(
            index=1, status_str="REC READ", sender_raw="TELCO",
            sender_normalized="TELCO", smsc_timestamp=datetime.now(timezone.utc),
            body="SMS lama sebelum swap", body_hash=compute_body_hash("SMS lama sebelum swap")
        )

        saved, is_new = ingest_sms(self.db, slot_id=self.slot1.id, parsed_sms=old_sms, is_initial=True)
        self.assertEqual(saved.status, SMSStatus.PESAN_BAWAAN)

    def test_I12_sensitive_data_masked(self):
        """I-12: Isi SMS, nomor lengkap, dan ICCID lengkap tidak ditulis polos ke log umum."""
        raw_log = "Error reading SIM 89620100000000001234 for phone +628123456789"
        masked = mask_sensitive(raw_log)
        self.assertNotIn("89620100000000001234", masked)
        self.assertNotIn("+628123456789", masked)
        self.assertIn("8962", masked)
        self.assertIn("****", masked)

    def test_I13_at_command_allowlist_enforced(self):
        """I-13: Hanya perintah AT dalam daftar izin yang boleh dikirim."""
        transport = VirtualTransport(port_name="VIRTUAL_TEST")
        client = ATClient(transport=transport)

        # Perintah terlarang
        banned_commands = ["AT&F", "ATZ", "AT+CFUN=0", "AT+FOOBAR", "AT+QPOWD"]
        for cmd in banned_commands:
            with self.assertRaises(DisallowedATCommandError):
                client.send_command(cmd)


if __name__ == "__main__":
    unittest.main()

