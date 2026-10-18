"""
Skrip Interaktif USSD XL untuk Menelusuri Menu dan Menemukan Nomor Telepon
"""

import sys
import re
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.modem.transport import SerialTransport
from app.modem.at_client import ATClient
from app.modem.sms_reader import mask_sensitive


def test_xl_ussd_navigation(port: str = "COM7"):
    transport = SerialTransport(port=port, baudrate=115200, timeout=1.0)
    client = ATClient(transport=transport, default_timeout=5.0)

    try:
        client.open()
        print(f"[*] Membuka port {port}...")

        # 1. Dial awal *808#
        print("[*] Dial *808#...")
        resp1 = client.send_command('AT+CUSD=1,"*808#"', timeout=5.0)
        print("Raw 1:\n", resp1.raw_response)

        # Beri jeda 1 detik
        time.sleep(1.0)

        # 2. Kirim pilihan '7' (MyInfo)
        print("\n[*] Mengirim opsi '7' (MyInfo)...")
        resp2 = client.send_command('AT+CUSD=1,"7"', timeout=5.0)
        print("Raw 2:\n", resp2.raw_response)

        time.sleep(1.0)

        # 3. Kirim opsi '1' (Cek Profil / Info Nomor jika ada)
        print("\n[*] Mengirim opsi '1'...")
        resp3 = client.send_command('AT+CUSD=1,"1"', timeout=5.0)
        print("Raw 3:\n", resp3.raw_response)

        # Tutup sesi USSD
        client.send_command('AT+CUSD=2', timeout=2.0)

    finally:
        client.close()


if __name__ == "__main__":
    test_xl_ussd_navigation()

