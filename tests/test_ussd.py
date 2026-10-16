"""
Unit Test Modul USSD dan Ekstraksi Nomor Telepon (Tahap M5)
"""

import unittest
from app.modem.ussd import decode_ussd_text, extract_phone_number_from_text


class TestUSSDModule(unittest.TestCase):

    def test_extract_phone_number_standard(self):
        """Memastikan nomor telepon Indonesia berhasil diekstrak dari respon USSD."""
        sample_text = (
            "Status Kesiapan 4G Anda di nomor 6283866029217 :\n"
            "Handset 4G : YA\n"
            "Kartu 4G : YA\n"
            "Paket 4G : YA\n"
            "ICCID : 8962115963047226794"
        )
        phone = extract_phone_number_from_text(sample_text)
        self.assertEqual(phone, "+6283866029217")

    def test_extract_phone_number_with_leading_zero(self):
        """Mengekstrak nomor dengan awalan 08."""
        sample = "Info Akun: 081912345678 aktif sampai 2026"
        phone = extract_phone_number_from_text(sample)
        self.assertEqual(phone, "+6281912345678")

    def test_decode_ussd_plain_text(self):
        """Memastikan teks ASCII biasa tidak rusak saat didekode."""
        text = "Pulsa Anda Rp 5.000 aktif s.d 30 hari"
        self.assertEqual(decode_ussd_text(text), text)

    def test_decode_ussd_ucs2_hex(self):
        """Memastikan teks format UCS2 hex dapat didekode."""
        # "Pulsa" in UTF-16BE hex
        hex_str = "00500075006C00730061"
        decoded = decode_ussd_text(hex_str)
        self.assertEqual(decoded, "Pulsa")

    def test_get_default_ussd_for_iccid(self):
        """Memastikan pemilihan kode USSD berdasarkan profil ICCID operator."""
        from app.modem.ussd import get_default_ussd_for_iccid
        self.assertEqual(get_default_ussd_for_iccid("8962115963047226794"), "*808*7*8#")
        self.assertEqual(get_default_ussd_for_iccid("8962100000000000000"), "*808#")
        self.assertEqual(get_default_ussd_for_iccid("8962010000000000000"), "*123*30#")
        self.assertEqual(get_default_ussd_for_iccid("8962890000000000000"), "*111*1#")
        self.assertEqual(get_default_ussd_for_iccid("8962090000000000000"), "*999#")


if __name__ == "__main__":
    unittest.main()

