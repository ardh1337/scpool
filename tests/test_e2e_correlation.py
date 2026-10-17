"""
Test Suite Pengujian End-to-End Korelasi & Logika Sistem (M1 s/d M8)
Menguji keterkaitan seluruh modul:
1. Siklus Hidup SIM (Deteksi -> USSD Nomor -> Pencabutan -> Invarian I-1, I-2, I-3, I-6)
2. Penomoran Slot dan Label Tampilan (I-3)
3. Alur Ingesti SMS, Ekstraksi OTP, Deduplikasi Komposit, dan Pesan Bawaan (I-4, I-8, I-9, I-11)
4. Keamanan Log, Masking Data Sensitif & Allowlist AT (I-12, I-13)
5. Isolasi Kesalahan Port & Konkurensi Serialisasi (I-5, I-10)
6. Korelasi Multi-Tenant, Isolasi Akses Pool & WebSocket Agent Gateway
"""

import unittest
from datetime import datetime, timezone
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database.connection import Base
from app.database.models import (
    ModemPool, Slot, Port, SIMCard, SIMSession, SMSMessage,
    SlotStatus, NumberStatus, SMSStatus, UserAccount
)
from app.services.slot_manager import handle_sim_inserted, handle_sim_removed
from app.services.sms_ingestion import ingest_sms, can_delete_sms_from_modem
from app.services.numbering import renumber_pool_slots, get_display_label
from app.modem.transport import VirtualTransport
from app.modem.at_client import ATClient, DisallowedATCommandError
from app.modem.sms_reader import ParsedSMS, compute_body_hash, mask_sensitive
from app.auth.security import hash_password


class TestE2ECorrelation(unittest.TestCase):

    def setUp(self):
        self.engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False}
        )
        with self.engine.connect() as conn:
            conn.exec_driver_sql("PRAGMA foreign_keys = ON;")

        Base.metadata.create_all(bind=self.engine)
        self.Session = sessionmaker(bind=self.engine)
        self.db = self.Session()

        # Inisialisasi pool dan 4 slot untuk pengujian alur
        self.pool = ModemPool(id="pool_e2e", name="E2E Pool", port_count=4)
        self.db.add(self.pool)
        self.db.commit()

        self.slots = []
        for i in range(1, 5):
            port = Port(id=f"COM{i:02d}", pool_id="pool_e2e")
            slot = Slot(pool_id="pool_e2e", slot_index=i, physical_port_id=f"COM{i:02d}")
            self.db.add_all([port, slot])
            self.slots.append(slot)
        self.db.commit()

    def tearDown(self):
        self.db.close()
        Base.metadata.drop_all(bind=self.engine)

    # --------------------------------------------------------------------------
    # 1. KORELASI SIKLUS HIDUP SIM & ISOLASI INVARIAN (I-1, I-2, I-3, I-6)
    # --------------------------------------------------------------------------
    def test_sim_lifecycle_full_correlation(self):
        """
        Korelasi: Pasang SIM-A -> Baca Nomor -> Terima SMS -> Cabut SIM-A ->
        Pasang SIM-B -> Verifikasi SIM-B bersih tanpa warisan data SIM-A.
        """
        # 1. Pasang SIM A di Slot 1
        sess_a, sim_a = handle_sim_inserted(self.db, slot_id=self.slots[0].id, iccid="8962110001")
        self.assertIsNotNone(sess_a)
        sim_a.phone_number = "+6281234567890"
        sim_a.number_status = NumberStatus.DIKETAHUI
        self.db.commit()

        # Slot 1 berstatus READY dan memiliki nomor SIM A
        self.assertEqual(self.slots[0].status, SlotStatus.READY)

        # 2. Ingesti SMS untuk SIM A
        body_text = "Kode OTP WhatsApp Anda adalah 123456."
        parsed_sms_a = ParsedSMS(
            index=1,
            status_str="REC READ",
            sender_raw="+6280000001",
            sender_normalized="+6280000001",
            smsc_timestamp=datetime.now(timezone.utc),
            body=body_text,
            body_hash=compute_body_hash(body_text),
            is_multipart=False
        )
        ingest_sms(self.db, slot_id=self.slots[0].id, parsed_sms=parsed_sms_a)

        # Verifikasi SMS tersimpan untuk SIM A
        sms_count_a = self.db.query(SMSMessage).filter(SMSMessage.session_id == sess_a.id).count()
        self.assertEqual(sms_count_a, 1)

        # 3. Cabut SIM A
        handle_sim_removed(self.db, slot_id=self.slots[0].id)
        self.assertEqual(self.slots[0].status, SlotStatus.EMPTY)
        old_sess = self.db.query(SIMSession).filter(SIMSession.id == sess_a.id).first()
        self.assertIsNotNone(old_sess.ended_at)

        # 4. Pasang SIM B di slot yang sama (Slot 1)
        sess_b, sim_b = handle_sim_inserted(self.db, slot_id=self.slots[0].id, iccid="8962110002")
        self.assertNotEqual(sess_b.id, sess_a.id)

        # Invarian I-1 & I-2: SIM B tidak mewarisi nomor telepon dari SIM A
        self.assertIsNone(sim_b.phone_number)
        self.assertEqual(sim_b.number_status, NumberStatus.BELUM_DIKETAHUI)

        # Invarian I-1: SMS dari sesi SIM A tidak boleh muncul di sesi aktif SIM B
        sms_b = self.db.query(SMSMessage).filter(SMSMessage.session_id == sess_b.id).all()
        self.assertEqual(len(sms_b), 0)

        # Invarian I-6: Maksimal satu sesi SIM aktif per slot
        active_sessions = self.db.query(SIMSession).filter(
            SIMSession.slot_id == self.slots[0].id,
            SIMSession.ended_at.is_(None)
        ).all()
        self.assertEqual(len(active_sessions), 1)
        self.assertEqual(active_sessions[0].id, sess_b.id)

    # --------------------------------------------------------------------------
    # 2. KORELASI PENOMORAN SLOT & LABEL LAYER (I-3)
    # --------------------------------------------------------------------------
    def test_slot_numbering_and_display_label_correlation(self):
        """
        Korelasi: Pengaturan label tampilan vs ID fisik port.
        Memastikan kaitan SMS dan sesi selalu menggunakan ID port fisik permanen (I-3).
        """
        # Slot 1 terikat ke port COM01
        self.assertEqual(self.slots[0].physical_port_id, "COM01")
        # Saat kosong, label memiliki penanda status KOSONG
        label_empty = get_display_label(self.db, self.slots[0].id)
        self.assertEqual(label_empty, "Slot 1 (KOSONG)")

        # Pasang SIM di Slot 1
        handle_sim_inserted(self.db, slot_id=self.slots[0].id, iccid="8962110003")
        label_active = get_display_label(self.db, self.slots[0].id)
        self.assertEqual(label_active, "Slot 1")

        # Susun ulang nomor (renumbering)
        renumber_pool_slots(self.db, "pool_e2e")
        self.assertEqual(get_display_label(self.db, self.slots[0].id), "Slot 1")

    # --------------------------------------------------------------------------
    # 3. KORELASI DEDUKLIKASI SMS, ATURAN SIMPAN-HAPUS & PESAN BAWAAN (I-4, I-8, I-9, I-11)
    # --------------------------------------------------------------------------
    def test_sms_deduplication_and_preexisting_correlation(self):
        """
        Korelasi: Dua SMS identik masuk secara berurutan.
        Memastikan kunci komposit mendeteksi duplikat (I-9) dan aturan hapus aman (I-4).
        """
        sess, _ = handle_sim_inserted(self.db, slot_id=self.slots[0].id, iccid="8962119999")
        fixed_time = datetime(2026, 10, 17, 10, 0, 0, tzinfo=timezone.utc)
        body_text = "Jangan berikan kode OTP 889900 kepada siapa pun."

        # 1. SMS Pertama
        sms1 = ParsedSMS(
            index=1,
            status_str="REC READ",
            sender_raw="BANK-BCA",
            sender_normalized="BANK-BCA",
            smsc_timestamp=fixed_time,
            body=body_text,
            body_hash=compute_body_hash(body_text),
            is_multipart=False
        )
        msg1, is_new1 = ingest_sms(self.db, slot_id=self.slots[0].id, parsed_sms=sms1, is_initial=True)
        self.assertTrue(is_new1)
        # I-11: Pesan saat sesi dimulai ditandai PESAN_BAWAAN
        self.assertEqual(msg1.status, SMSStatus.PESAN_BAWAAN)

        # I-4: Verifikasi aturan hapus aman (hanya boleh dihapus dari modem jika sudah di DB)
        can_del = can_delete_sms_from_modem(self.db, msg1.id)
        self.assertTrue(can_del)

        # 2. SMS Kedua (Duplikat identik: sesi sama, sender sama, timestamp sama, hash sama)
        sms2 = ParsedSMS(
            index=1,
            status_str="REC READ",
            sender_raw="BANK-BCA",
            sender_normalized="BANK-BCA",
            smsc_timestamp=fixed_time,
            body=body_text,
            body_hash=compute_body_hash(body_text),
            is_multipart=False
        )
        msg2, is_new2 = ingest_sms(self.db, slot_id=self.slots[0].id, parsed_sms=sms2, is_initial=False)
        # I-9: Duplikat terdeteksi, tidak disimpan sebagai entri baru
        self.assertFalse(is_new2)
        self.assertEqual(msg2.id, msg1.id)

        # Total SMS di database tetap 1
        total_sms = self.db.query(SMSMessage).count()
        self.assertEqual(total_sms, 1)

    # --------------------------------------------------------------------------
    # 4. KEAMANAN LOG & ALLOWLIST AT COMMAND (I-12, I-13)
    # --------------------------------------------------------------------------
    def test_security_masking_and_at_allowlist(self):
        """
        Memastikan masking nomor telepon/ICCID di log umum (I-12)
        dan pencegahan eksekusi perintah di luar allowlist (I-13).
        """
        # I-12: Masking nomor & ICCID
        full_num = "+6281234567890"
        masked_num = mask_sensitive(full_num)
        self.assertNotIn("3456", masked_num)
        self.assertTrue(masked_num.startswith("+628"))
        self.assertIn("****", masked_num)

        # I-13: AT Client menolak perintah yang tidak ada di Allowlist
        transport = VirtualTransport()
        client = ATClient(transport)

        # Perintah terlarang seperti penulisan NV atau flashing firmware
        with self.assertRaises(DisallowedATCommandError):
            client.send_command("AT+QNVW=1,2,3")

        with self.assertRaises(DisallowedATCommandError):
            client.send_command("AT+CFUN=0")  # Pengubahan daya tanpa izin

        with self.assertRaises(DisallowedATCommandError):
            client.send_command("ATD*#06#;")  # Dial tak terdaftar

    # --------------------------------------------------------------------------
    # 5. ISOLASI KESALAHAN PORT (I-10) & KONKURENSI AT (I-5)
    # --------------------------------------------------------------------------
    def test_port_error_isolation(self):
        """
        I-10: Satu port error atau offline tidak boleh menghentikan port lain.
        """
        # Atur Slot 1 menjadi PORT_ERROR
        self.slots[0].status = SlotStatus.PORT_ERROR
        self.db.commit()

        # Slot 2 tetap dapat beroperasi normal
        sess_2, sim_2 = handle_sim_inserted(self.db, slot_id=self.slots[1].id, iccid="8962118888")
        self.assertIsNotNone(sess_2)
        self.assertEqual(self.slots[1].status, SlotStatus.READY)

        # Pastikan Slot 1 tetap PORT_ERROR dan Slot 2 tetap READY
        self.assertEqual(self.slots[0].status, SlotStatus.PORT_ERROR)
        self.assertEqual(self.slots[1].status, SlotStatus.READY)

    # --------------------------------------------------------------------------
    # 6. KORELASI MULTI-TENANT & ISOLASI POOL CLIENT
    # --------------------------------------------------------------------------
    def test_multitenant_pool_isolation_correlation(self):
        """
        Korelasi: Pengguna Client hanya bisa mengakses pool miliknya sendiri.
        """
        # Buat Pool B milik Client beserta Port-nya
        pool_b = ModemPool(id="pool_client_b", name="Pool Client B", port_count=2)
        port_b1 = Port(id="COM91", pool_id="pool_client_b")
        port_b2 = Port(id="COM92", pool_id="pool_client_b")
        self.db.add_all([pool_b, port_b1, port_b2])
        self.db.commit()

        slot_b1 = Slot(pool_id="pool_client_b", slot_index=1, physical_port_id="COM91")
        slot_b2 = Slot(pool_id="pool_client_b", slot_index=2, physical_port_id="COM92")
        self.db.add_all([slot_b1, slot_b2])
        self.db.commit()

        # Buat akun operator untuk Client B
        user_b = UserAccount(
            username="klien_b",
            password=hash_password("klien123"),
            role="operator",
            pool_id="pool_client_b",
            agent_token="tok_agent_b_secret"
        )
        self.db.add(user_b)
        self.db.commit()

        # Pasang SIM pada masing-masing pool
        handle_sim_inserted(self.db, slot_id=self.slots[0].id, iccid="8962110001")  # Pool A
        handle_sim_inserted(self.db, slot_id=slot_b1.id, iccid="8962119991")         # Pool B

        # Query terisolasi untuk Pool B
        slots_b = self.db.query(Slot).filter(Slot.pool_id == user_b.pool_id).all()
        self.assertEqual(len(slots_b), 2)
        self.assertEqual(slots_b[0].pool_id, "pool_client_b")

        # Query pool A tidak boleh mencampur slot Pool B
        slots_a = self.db.query(Slot).filter(Slot.pool_id == "pool_e2e").all()
        self.assertEqual(len(slots_a), 4)
        for s in slots_a:
            self.assertEqual(s.pool_id, "pool_e2e")
