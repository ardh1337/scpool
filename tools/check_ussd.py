"""
Skrip Uji Cek USSD untuk Mengambil Nomor Telepon (Tahap M5)
Menjalankan dial USSD (misal *808# atau *123#) pada port tertentu,
membaca respon jaringan, dan mengekstrak nomor telepon jika ada.
"""

import sys
import argparse
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.modem.transport import SerialTransport
from app.modem.at_client import ATClient
from app.modem.ussd import execute_ussd
from app.modem.sms_reader import mask_sensitive
from app.database.connection import SessionLocal
from app.database.models import Slot, SIMCard, NumberStatus, NumberSource


def test_ussd_on_port(port_name: str, ussd_code: str = "*808#", update_db: bool = True):
    print("=" * 70)
    print(f"  PENGUJIAN USSD PADA PORT {port_name} (KODE: {ussd_code})")
    print("=" * 70)

    transport = SerialTransport(port=port_name, baudrate=115200, timeout=1.0)
    client = ATClient(transport=transport, default_timeout=2.0)

    try:
        client.open()
        # Set charset ke IRA agar karakter teks terbaca standar ASCII
        client.send_command('AT+CSCS="IRA"', timeout=1.0)

        print(f"[*] Mengirim kode USSD '{ussd_code}' ke jaringan seluler...")
        success, text, phone = execute_ussd(client, ussd_code=ussd_code, timeout=12.0)

        if not success:
            print(f"[-] Gagal menjalankan USSD: {text}")
            return

        print(f"[+] Balasan Jaringan:")
        print("-" * 70)
        # Sanitasi dan sensor
        display_text = text or "(Balasan kosong)"
        clean_text = display_text.encode("ascii", errors="replace").decode("ascii")
        print(mask_sensitive(clean_text))
        print("-" * 70)

        if phone:
            safe_phone = mask_sensitive(phone)
            print(f"[+] NOMOR BERHASIL DITEMUKAN: {safe_phone}")

            if update_db:
                db = SessionLocal()
                try:
                    slot = db.query(Slot).filter(Slot.physical_port_id == port_name).first()
                    if slot and slot.sessions:
                        active_session = [s for s in slot.sessions if s.ended_at is None]
                        if active_session and active_session[0].sim_card:
                            sim = active_session[0].sim_card
                            sim.phone_number = phone
                            sim.number_status = NumberStatus.DIKETAHUI
                            sim.number_source = NumberSource.USSD
                            db.commit()
                            print(f"[+] Nomor berhasil disimpan ke database untuk Slot {slot.slot_index}.")
                finally:
                    db.close()
        else:
            print("[-] Nomor telepon tidak ditemukan secara otomatis dalam teks balasan.")

    finally:
        client.close()
        print("=" * 70)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Uji USSD modem pool")
    parser.add_argument("--port", default="COM7", help="Port COM yang diuji (default: COM7 / Slot 1)")
    parser.add_argument("--code", default="*808#", help="Kode USSD (default: *808#)")
    args = parser.parse_args()

    test_ussd_on_port(port_name=args.port, ussd_code=args.code)

