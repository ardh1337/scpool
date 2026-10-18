"""
Skrip setup instan untuk konfigurasi Bot Telegram SCPool di VPS/Lokal.
Coded By Ardh1337
"""

import sys
from pathlib import Path

# Tambahkan root directory ke sys.path
BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

from app.database.bootstrap import init_db
from app.services.settings import set_setting, get_setting
from app.services.telegram_service import send_telegram_message

def main():
    print("=" * 60)
    print("   SCPOOL - TELEGRAM BOT CONFIGURATION SETUP")
    print("=" * 60)

    # Inisialisasi database jika belum
    init_db()

    token = "8838231096:AAGyboJQed986oQvlY5Gv7ecf8fXBG3kUdY"
    chat_id = "1033965745"

    set_setting("telegram_bot_token", token, "Token Bot Telegram")
    set_setting("telegram_chat_id", chat_id, "Chat ID Admin Telegram")

    print("[OK] Token Bot Telegram berhasil disimpan.")
    print("[OK] Chat ID Admin (1033965745) berhasil disimpan.")
    print("\n[*] Mengirim pesan uji coba ke Telegram Anda...")

    ok, msg = send_telegram_message(
        "🎉 *SCPool Bot Berhasil Terhubung di VPS!*\n\n"
        "Token Bot `@SCPool_bot` dan Chat ID Anda telah aktif.\n"
        "Notifikasi pendaftaran klien baru akan masuk ke sini.\n\n"
        "— _Coded By Ardh1337_"
    )

    if ok:
        print("[SUCCESS] Pesan uji coba berhasil terkirim ke Telegram!")
    else:
        print(f"[WARN] Pengiriman pesan uji coba: {msg}")

    print("=" * 60)

if __name__ == "__main__":
    main()
