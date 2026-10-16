"""
Skrip Sinkronisasi Hardware Nyata ke Database SQLite (Tahap M3 & M4)
Menjalankan sinkronisasi hardware seluruh 32 port modem pool Foxcom:
- Inisialisasi pool dan 32 slot di data/sim_inventory.db
- Deteksi SIM dan pembuatan record sesi SIM (I-1, I-6)
- Penomoran urut otomatis 1..N untuk SIM yang aktif (I-3)
- Menampilkan rekapitulasi status dalam format tabel yang rapi.
"""

import sys
import re
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.database.connection import SessionLocal
from app.database.models import init_db
from app.services.hardware_sync import sync_modem_pool_hardware
from app.modem.sms_reader import mask_sensitive


def main():
    print("=" * 85)
    print("  SIM INVENTORY - SINKRONISASI HARDWARE KE DATABASE (TAHAP M3 & M4)")
    print("=" * 85)
    print("[*] Menginisialisasi skema database di data/sim_inventory.db...")
    init_db()

    db = SessionLocal()
    try:
        print("[*] Memindai 32 slot port fisik dan mencatat sesi SIM...")
        results = sync_modem_pool_hardware(db=db, pool_id="foxcom_32", auto_renumber=True)

        print("\n" + "=" * 85)
        print(f"{'SLOT':<8} | {'PORT':<8} | {'NO. URUT':<18} | {'STATUS':<10} | {'ICCID (MASKED)':<22}")
        print("-" * 85)

        ready_count = 0
        for r in results:
            slot_label = f"Slot {r['slot_index']}"
            raw_iccid = r.get("iccid", "-")
            safe_iccid = mask_sensitive(raw_iccid) if raw_iccid != "-" else "-"
            if r["status"] == "READY":
                ready_count += 1
            print(f"{slot_label:<8} | {r['port']:<8} | {r['label']:<18} | {r['status']:<10} | {safe_iccid:<22}")

        print("=" * 85)
        print(f"[+] Selesai! Terdeteksi {ready_count} SIM aktif, bernomor urut rapi dari No. 1 s.d. No. {ready_count}.")
        print("[+] Sesi SIM telah tersimpan di database lokal SQLite.")

    finally:
        db.close()


if __name__ == "__main__":
    main()
