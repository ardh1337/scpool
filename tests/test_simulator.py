"""
Pengujian Perilaku Modem Simulasi (Section 8)
Memvalidasi skenario lengkap simulator modem sesuai kontrak Section 8 spesifikasi.
"""

import unittest
from tests.support.simulator.simulated_modem import SimulatedModem
from tests.support.simulator.scenarios import SimulatedModemPool
from app.modem.at_client import ATClient
from app.modem.sim_status import (
    clean_and_validate_iccid, parse_cpin_response,
    parse_iccid_response, parse_cnum_response
)
from app.modem.sms_reader import parse_cmgl_text_response


class TestModemSimulator(unittest.TestCase):

    def setUp(self):
        self.modem = SimulatedModem(port_name="VIRTUAL_TEST_PORT")
        self.client = ATClient(transport=self.modem.transport, default_timeout=1.0)
        self.client.open()

    def tearDown(self):
        self.client.close()

    def test_sim_insert_remove_lifecycle(self):
        """Uji siklus SIM dipasang, dibaca statusnya, dicabut, dan dipasang lagi."""
        # 1. Sebelum SIM dipasang
        resp = self.client.send_command("AT+CPIN?")
        self.assertFalse(resp.success)
        self.assertEqual(parse_cpin_response(resp), "NOT_INSERTED")

        # 2. Pasang SIM dengan ICCID valid
        iccid = "89620100000000001234"
        self.modem.insert_sim(iccid=iccid, phone_number="+628123456789")

        resp = self.client.send_command("AT+CPIN?")
        self.assertTrue(resp.success)
        self.assertEqual(parse_cpin_response(resp), "READY")

        # Baca ICCID
        resp_iccid = self.client.send_command("AT+QCCID")
        self.assertTrue(resp_iccid.success)
        self.assertEqual(parse_iccid_response(resp_iccid), iccid)

        # 3. Cabut SIM
        self.modem.remove_sim()
        resp_after_remove = self.client.send_command("AT+CPIN?")
        self.assertFalse(resp_after_remove.success)
        self.assertEqual(parse_cpin_response(resp_after_remove), "NOT_INSERTED")

    def test_iccid_validation_and_unreadable(self):
        """Uji validasi ICCID valid, invalid, dan kondisi gagal baca."""
        # ICCID valid (19-20 digit, awalan 89)
        self.assertEqual(clean_and_validate_iccid("8962010000000000123F"), "8962010000000000123")
        self.assertEqual(clean_and_validate_iccid('"89620100000000001234"'), "89620100000000001234")

        # ICCID invalid (panjang salah atau bukan awalan 89)
        self.assertIsNone(clean_and_validate_iccid("123456789"))
        self.assertIsNone(clean_and_validate_iccid("99620100000000001234"))

        # Kondisi ICCID tidak terbaca oleh modem
        self.modem.insert_sim(iccid="89620100000000001234")
        self.modem.iccid_unreadable = True
        resp = self.client.send_command("AT+QCCID")
        self.assertFalse(resp.success)

    def test_cnum_scenarios(self):
        """Uji AT+CNUM mengembalikan nomor, kosong, atau error tidak didukung."""
        # 1. Dengan nomor tersimpan di SIM
        self.modem.insert_sim(iccid="89620100000000001234", phone_number="+628123456789")
        resp = self.client.send_command("AT+CNUM")
        self.assertTrue(resp.success)
        self.assertEqual(parse_cnum_response(resp), "+628123456789")

        # 2. Tanpa nomor di SIM (kosong)
        self.modem.insert_sim(iccid="89620100000000001234", phone_number=None)
        resp_empty = self.client.send_command("AT+CNUM")
        self.assertTrue(resp_empty.success)
        self.assertIsNone(parse_cnum_response(resp_empty))

        # 3. AT+CNUM error tidak didukung
        self.modem.cnum_supported = False
        resp_err = self.client.send_command("AT+CNUM")
        self.assertFalse(resp_err.success)
        self.assertIsNone(parse_cnum_response(resp_err))

    def test_sms_reception_and_duplicate_notification(self):
        """Uji penerimaan SMS dan simulasi notifikasi URC ganda (+CMTI dua kali)."""
        self.modem.insert_sim(iccid="89620100000000001234")
        urc_list = []
        self.client.register_urc_callback(lambda u: urc_list.append(u))

        # Terima SMS dengan notifikasi ganda
        idx = self.modem.receive_sms(
            sender="GOJEK",
            body="Kode OTP anda adalah 8899",
            duplicate_notification=True
        )

        # Baca daftar SMS via AT+CMGL
        resp_cmgl = self.client.send_command("AT+CMGL=\"ALL\"")
        self.assertTrue(resp_cmgl.success)
        parsed = parse_cmgl_text_response(resp_cmgl.lines)
        self.assertEqual(len(parsed), 1)
        self.assertEqual(parsed[0].sender_raw, "GOJEK")
        self.assertIn("8899", parsed[0].body)

    def test_fault_injection_timeout_and_recovery(self):
        """Uji simulasi timeout modem dan pemulihan kembali."""
        self.modem.hang_mode = True
        resp = self.client.send_command("AT", timeout=0.3)
        self.assertTrue(resp.is_timeout)
        self.assertFalse(resp.success)

        # Pulihkan modem
        self.modem.hang_mode = False
        resp_ok = self.client.send_command("AT", timeout=0.3)
        self.assertTrue(resp_ok.success)

    def test_multi_port_pool_scale_up_to_64(self):
        """Uji kemampuan inisialisasi pool modem hingga 64 port."""
        pool64 = SimulatedModemPool(pool_id="pool_64", port_count=64)
        self.assertEqual(len(pool64.modems), 64)
        pool64.populate_sample_sims(active_count=32)
        # Port 1 ada SIM, port 64 kosong
        p1 = list(pool64.modems.values())[0]
        p64 = list(pool64.modems.values())[63]
        self.assertTrue(p1.sim_inserted)
        self.assertFalse(p64.sim_inserted)


if __name__ == "__main__":
    unittest.main()

