"""
Uji Sesi USSD Interaktif Bertahap (*808# -> 7 -> 1 -> 1)
"""

import sys
import time
import re
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.modem.transport import SerialTransport
from app.modem.at_client import ATClient
from app.modem.sms_reader import mask_sensitive


def test_steps(port="COM7"):
    transport = SerialTransport(port=port, baudrate=115200, timeout=1.0)
    client = ATClient(transport=transport, default_timeout=5.0)

    try:
        client.open()
        client.send_command('AT+CSCS="IRA"')

        print("=== LANGKAH 1: Dial *808# ===")
        r1 = client.send_command('AT+CUSD=1,"*808#"', timeout=8.0)
        print("Raw 1:\n", r1.raw_response.encode("ascii", errors="replace").decode("ascii"))

        time.sleep(2.0)

        print("\n=== LANGKAH 2: Balas '7' ===")
        r2 = client.send_command('AT+CUSD=1,"7"', timeout=8.0)
        print("Raw 2:\n", r2.raw_response.encode("ascii", errors="replace").decode("ascii"))

        time.sleep(2.0)

        print("\n=== LANGKAH 3: Balas '8' (Info) ===")
        r3 = client.send_command('AT+CUSD=1,"8"', timeout=8.0)
        print("Raw 3:\n", r3.raw_response.encode("ascii", errors="replace").decode("ascii"))

        time.sleep(2.0)

        print("\n=== LANGKAH 4: Balas '1' (Info Nomor) ===")
        r4 = client.send_command('AT+CUSD=1,"1"', timeout=8.0)
        print("Raw 4:\n", r4.raw_response.encode("ascii", errors="replace").decode("ascii"))

        # Tutup sesi
        client.send_command('AT+CUSD=2')

    finally:
        client.close()


if __name__ == "__main__":
    test_steps()

