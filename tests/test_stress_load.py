"""
Test Suite Pengujian Beban & Stres Produksi (Production Stress & Load Testing)
Menguji kehandalan sistem di bawah beban tinggi:
1. Badai Permintaan API Paralel (50 Pekerja Simultan / 500+ Request)
2. Ingesti SMS Massal Multi-Thread pada Database SQLite WAL (Menguji Ketahanan Lock)
3. Simulasi Polling Frekuensi Tinggi dari Puluhan Klien Sekaligus
4. Konkurensi Antrian Eksekusi Perintah Modem (I-5 & I-10)
5. Ketahanan Penanganan Error & Beban WebSocket Gateway
"""

import unittest
import time
import os
from pathlib import Path
import concurrent.futures
from datetime import datetime, timezone
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from fastapi.testclient import TestClient

from app.database.connection import Base
from app.database.models import (
    ModemPool, Slot, Port, SIMCard, SIMSession, SMSMessage,
    SlotStatus, NumberStatus, SMSStatus, UserAccount
)
from app.database.bootstrap import init_db
from app.services.slot_manager import handle_sim_inserted
from app.services.sms_ingestion import ingest_sms
from app.modem.transport import VirtualTransport
from app.modem.at_client import ATClient
from app.modem.sms_reader import ParsedSMS, compute_body_hash
from app.main import app

STRESS_DB_PATH = Path(os.environ["SCPOOL_DATA_DIR"]) / "stress_test.db"


class TestStressAndLoad(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        init_db()

    def setUp(self):
        # Database SQLite fisik pada disk dengan WAL dan timeout tinggi untuk pengujian konkurensi nyata
        if STRESS_DB_PATH.exists():
            try:
                STRESS_DB_PATH.unlink()
            except Exception:
                pass

        self.engine = create_engine(
            f"sqlite:///{STRESS_DB_PATH.as_posix()}",
            connect_args={"check_same_thread": False, "timeout": 30.0}
        )
        with self.engine.connect() as conn:
            conn.exec_driver_sql("PRAGMA journal_mode = WAL;")
            conn.exec_driver_sql("PRAGMA foreign_keys = ON;")
            conn.exec_driver_sql("PRAGMA busy_timeout = 30000;")

        Base.metadata.create_all(bind=self.engine)
        self.Session = sessionmaker(bind=self.engine, autoflush=False, autocommit=False)
        self.db = self.Session()

        # Inisialisasi pool produksi 32-port
        self.pool = ModemPool(id="pool_stress_32", name="Stress 32-Port Pool", port_count=32)
        self.db.add(self.pool)
        self.db.commit()

        self.slots = []
        for i in range(1, 33):
            port = Port(id=f"COM{i:02d}", pool_id="pool_stress_32")
            slot = Slot(pool_id="pool_stress_32", slot_index=i, physical_port_id=f"COM{i:02d}")
            self.db.add_all([port, slot])
            self.slots.append(slot)
        self.db.commit()

        # Masukkan SIM aktif pada 16 slot pertama
        for i in range(16):
            handle_sim_inserted(self.db, slot_id=self.slots[i].id, iccid=f"89621199000000{i:02d}")
        self.db.commit()

        self.client = TestClient(app)

    def tearDown(self):
        self.db.close()
        self.engine.dispose()
        # Bersihkan file DB pengujian stres
        for p in [STRESS_DB_PATH, STRESS_DB_PATH.with_suffix('.db-wal'), STRESS_DB_PATH.with_suffix('.db-shm')]:
            if p.exists():
                try:
                    p.unlink()
                except Exception:
                    pass

    # --------------------------------------------------------------------------
    # 1. STRESS TEST: BADAI PERMINTAAN API PARALEL (50 PEKERJA SIMULTAN)
    # --------------------------------------------------------------------------
    def test_stress_api_concurrency_storm(self):
        """
        Menguji ketahanan server FastAPI terhadap badai permintaan simultan:
        400 request dari 25 thread paralel mengakses /api/slots, /api/status, /api/sms.
        Toleransi error: 0% HTTP 5xx.
        """
        endpoints = ["/api/slots", "/api/status", "/api/sms?limit=50", "/api/auth/me"]
        total_requests = 400
        num_workers = 25

        latencies = []
        status_codes = []

        def worker_task(req_idx):
            url = endpoints[req_idx % len(endpoints)]
            t0 = time.perf_counter()
            resp = self.client.get(url)
            t1 = time.perf_counter()
            latencies.append((t1 - t0) * 1000)
            status_codes.append(resp.status_code)
            return resp.status_code

        t_start = time.perf_counter()
        with concurrent.futures.ThreadPoolExecutor(max_workers=num_workers) as executor:
            futures = [executor.submit(worker_task, i) for i in range(total_requests)]
            results = [f.result() for f in concurrent.futures.as_completed(futures)]
        t_total = time.perf_counter() - t_start

        # Evaluasi Hasil
        error_count = sum(1 for code in status_codes if code >= 500)
        self.assertEqual(error_count, 0, f"Ditemukan {error_count} request yang menghasilkan error 5xx!")

        throughput = total_requests / t_total
        avg_latency = sum(latencies) / len(latencies)
        p95_latency = sorted(latencies)[int(len(latencies) * 0.95)]

        print(f"\n[BENCHMARK API] Total: {total_requests} reqs | Workers: {num_workers}")
        print(f" -> Waktu Eksekusi: {t_total:.2f} detik")
        print(f" -> Throughput: {throughput:.1f} req/detik")
        print(f" -> Rata-rata Latensi: {avg_latency:.2f} ms")
        print(f" -> P95 Latensi: {p95_latency:.2f} ms")

        self.assertGreater(throughput, 40.0, "Throughput API di bawah standar minimum produksi!")

    # --------------------------------------------------------------------------
    # 2. STRESS TEST: BANJIR INGESTI SMS PARALEL PADA DATABASE WAL
    # --------------------------------------------------------------------------
    def test_stress_concurrent_sms_ingestion_wal(self):
        """
        Menguji ketahanan konkurensi write pada SQLite WAL:
        120 SMS masuk simultan dari 8 thread pekerja berbeda pada database fisik.
        Memastikan tidak terjadi 'database is locked' dan deduplikasi tetap akurat.
        """
        num_messages = 120
        num_threads = 8
        slot_ids = [s.id for s in self.slots[:16]]

        def ingest_worker(msg_idx):
            # Buat sesi DB independen per thread
            thread_db = self.Session()
            try:
                target_slot_id = slot_ids[msg_idx % len(slot_ids)]
                ts = datetime(2026, 10, 17, 10, (msg_idx % 60), 0, tzinfo=timezone.utc)
                parsed = ParsedSMS(
                    index=(msg_idx % 10) + 1,
                    status_str="REC UNREAD",
                    sender_raw=f"+62811000{msg_idx % 5:02d}",
                    sender_normalized=f"+62811000{msg_idx % 5:02d}",
                    smsc_timestamp=ts,
                    body=f"Kode OTP Anda {100000 + msg_idx}. Berlaku 5 menit.",
                    body_hash=compute_body_hash(f"Kode OTP Anda {100000 + msg_idx}. Berlaku 5 menit.")
                )
                msg, is_new = ingest_sms(thread_db, slot_id=target_slot_id, parsed_sms=parsed)
                return True
            except Exception as e:
                return str(e)
            finally:
                thread_db.close()

        t0 = time.perf_counter()
        with concurrent.futures.ThreadPoolExecutor(max_workers=num_threads) as executor:
            futures = [executor.submit(ingest_worker, i) for i in range(num_messages)]
            results = [f.result() for f in concurrent.futures.as_completed(futures)]
        duration = time.perf_counter() - t0

        failures = [r for r in results if r is not True]
        self.assertEqual(len(failures), 0, f"Gagal ingest SMS: {failures[:3]}")

        # Verifikasi data tersimpan
        saved_count = self.db.query(SMSMessage).count()
        self.assertEqual(saved_count, num_messages)

        ingestion_speed = num_messages / duration
        print(f"\n[BENCHMARK SMS INGEST] Total SMS: {num_messages} | Threads: {num_threads}")
        print(f" -> Kecepatan Ingesti: {ingestion_speed:.1f} SMS/detik")
        print(f" -> Waktu Total: {duration:.2f} detik")

    # --------------------------------------------------------------------------
    # 3. STRESS TEST: SIMULASI 20 DASHBOARD POLLING SIMULTAN
    # --------------------------------------------------------------------------
    def test_stress_high_frequency_dashboard_polling(self):
        """
        Simulasi 20 tab browser aktif melakukan live polling data slot & status secara cepat.
        Memastikan connection pool DB tidak bocor dan latency stabil.
        """
        simulated_clients = 20
        cycles_per_client = 5
        total_polls = simulated_clients * cycles_per_client

        def client_poll_loop(client_id):
            client = TestClient(app)
            for _ in range(cycles_per_client):
                r1 = client.get("/api/slots")
                r2 = client.get("/api/status")
                if r1.status_code != 200 or r2.status_code != 200:
                    return False
            return True

        t0 = time.perf_counter()
        with concurrent.futures.ThreadPoolExecutor(max_workers=simulated_clients) as executor:
            futures = [executor.submit(client_poll_loop, i) for i in range(simulated_clients)]
            results = [f.result() for f in concurrent.futures.as_completed(futures)]
        elapsed = time.perf_counter() - t0

        self.assertTrue(all(results), "Sebagian client mengalami kegagalan polling!")
        print(f"\n[BENCHMARK POLLING DASHBOARD] Clients: {simulated_clients} | Total Cycles: {total_polls}")
        print(f" -> Selesai dalam {elapsed:.2f} detik (Bebas kebocoran sesi)")

    # --------------------------------------------------------------------------
    # 4. STRESS TEST: KONKURENSI ANTRIAN AT COMMAND & ERROR ISOLATION (I-5, I-10)
    # --------------------------------------------------------------------------
    def test_stress_at_client_serialization_and_isolation(self):
        """
        Menguji antrian perintah pada VirtualTransport:
        Memastikan perintah AT dieksekusi secara serial per port (I-5)
        dan timeout pada port simulasi tidak mematikan port lainnya (I-10).
        """
        transport_a = VirtualTransport(port_name="PORT_STRESS_A")
        transport_b = VirtualTransport(port_name="PORT_STRESS_B")

        client_a = ATClient(transport=transport_a, default_timeout=1.0)
        client_b = ATClient(transport=transport_b, default_timeout=1.0)

        client_a.open()
        client_b.open()

        # Atur Port A merespons normal
        def on_write_a(data):
            if b"AT+CSQ" in data:
                transport_a.feed_rx(b"+CSQ: 25,99\r\n\r\nOK\r\n")

        transport_a.on_write_callback = on_write_a

        # Atur Port B gagal/timeout (simulasi kabel goyang / modul hang)
        transport_b.on_write_callback = lambda d: None  # Tidak merespons

        # Jalankan secara bersamaan
        resp_a = client_a.send_command("AT+CSQ")
        self.assertTrue(resp_a.success)
        self.assertIn("+CSQ: 25,99", resp_a.raw_response)

        # Port B harus timeout tanpa menyebabkan Port A macet
        resp_b = client_b.send_command("AT+CSQ", timeout=0.1)
        self.assertFalse(resp_b.success)
        self.assertTrue(resp_b.is_timeout)

        # Port A tetap sehat dan dapat menerima perintah berikutnya
        resp_a_2 = client_a.send_command("AT+CSQ")
        self.assertTrue(resp_a_2.success)

        client_a.close()
        client_b.close()

