"""
Skrip Uji Baca SMS Nyata dari Port COM (Tahap M6)
"""

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.modem.transport import SerialTransport
from app.modem.at_client import ATClient
from app.modem.sms_reader import parse_cmgl_text_response, mask_sensitive


def test_read_sms(port: str = "COM7"):
    transport = SerialTransport(port=port, baudrate=115200, timeout=1.0)
    client = ATClient(transport=transport, default_timeout=5.0)

    try:
        client.open()
        client.send_command('AT+CSCS="GSM"')
        client.send_command('AT+CMGF=1')
        res = client.send_command('AT+CMGL="ALL"', timeout=5.0)

        messages = parse_cmgl_text_response(res.lines)
        print(f"[{port}] Ditemukan {len(messages)} pesan di memori SIM.")
        for m in messages:
            safe_sender = mask_sensitive(m.sender_raw)
            safe_body = mask_sensitive(m.body)
            print(f"  - Indeks {m.index}: Dari={safe_sender} | Waktu={m.smsc_timestamp} | Isi={safe_body}")

    finally:
        client.close()


if __name__ == "__main__":
    test_read_sms("COM7")
    test_read_sms("COM19")
