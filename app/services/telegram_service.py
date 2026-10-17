"""
SCPool - Layanan Notifikasi & Persetujuan Telegram (Coded By Ardh1337)
Menggunakan standard library Python (urllib.request) murni tanpa dependensi eksternal tambahan.
Mendukung pengiriman alert pendaftaran akun baru dengan tombol Inline Keyboard Setujui/Tolak.
"""

import json
import logging
import urllib.request
import urllib.parse
from datetime import datetime
from typing import Optional, Tuple
from sqlalchemy.orm import Session

from app.database.connection import SessionLocal
from app.database.models import SystemSetting, UserAccount, ModemPool

logger = logging.getLogger(__name__)


def get_setting(key: str, default: str = "", db: Optional[Session] = None) -> str:
    """Membaca konfigurasi sistem dari database."""
    should_close = False
    if db is None:
        db = SessionLocal()
        should_close = True
    try:
        row = db.query(SystemSetting).filter(SystemSetting.key == key).first()
        return row.value if (row and row.value is not None) else default
    finally:
        if should_close:
            db.close()


def set_setting(key: str, value: str, description: Optional[str] = None, db: Optional[Session] = None):
    """Menyimpan atau memperbarui konfigurasi sistem."""
    should_close = False
    if db is None:
        db = SessionLocal()
        should_close = True
    try:
        row = db.query(SystemSetting).filter(SystemSetting.key == key).first()
        if not row:
            row = SystemSetting(key=key, value=value, description=description)
            db.add(row)
        else:
            row.value = value
            if description:
                row.description = description
        db.commit()
    finally:
        if should_close:
            db.close()


def send_telegram_message(text: str, reply_markup: Optional[dict] = None, db: Optional[Session] = None) -> Tuple[bool, str]:
    """
    Mengirimkan pesan ke Telegram Admin menggunakan bot token & chat ID yang tersimpan.
    """
    bot_token = get_setting("telegram_bot_token", "", db=db).strip()
    chat_id = get_setting("telegram_chat_id", "", db=db).strip()

    if not bot_token or not chat_id:
        return False, "Token Bot atau Chat ID Telegram belum dikonfigurasi di Pengaturan Sistem."

    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "Markdown"
    }
    if reply_markup:
        payload["reply_markup"] = reply_markup

    try:
        req_data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=req_data,
            headers={"Content-Type": "application/json"},
            method="POST"
        )
        with urllib.request.urlopen(req, timeout=8.0) as resp:
            resp_data = json.loads(resp.read().decode("utf-8"))
            if resp_data.get("ok"):
                return True, "Pesan Telegram berhasil terkirim."
            return False, f"Telegram error: {resp_data.get('description', 'Unknown')}"
    except Exception as e:
        logger.warning(f"Gagal mengirim pesan Telegram: {e}")
        return False, str(e)


def notify_new_registration(user: UserAccount, pool: ModemPool, db: Optional[Session] = None) -> Tuple[bool, str]:
    """
    Kirim notifikasi pendaftaran akun operator baru ke admin Telegram beserta tombol persetujuan.
    """
    domain = get_setting("public_domain", "https://scpool.ardh1337.cloud", db=db).rstrip("/")
    time_str = user.created_at.strftime("%Y-%m-%d %H:%M:%S UTC") if user.created_at else "Baru saja"

    text = (
        "🔔 *PENDAFTARAN KLIEN BARU*\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"👤 *Username:* `{user.username}`\n"
        f"📦 *Modem Pool:* `{pool.name}` ({pool.port_count} Port)\n"
        f"🕒 *Waktu:* `{time_str}`\n"
        f"🔑 *Agent Token:* `{user.agent_token[:16]}...`\n\n"
        "Status: ⏳ *Menunggu Persetujuan Admin*\n"
        f"Buka Dashboard: {domain}/#settings"
    )

    reply_markup = {
        "inline_keyboard": [
            [
                {"text": "✅ Setujui Akun", "callback_data": f"approve_{user.id}"},
                {"text": "❌ Tolak", "callback_data": f"reject_{user.id}"}
            ]
        ]
    }

    return send_telegram_message(text=text, reply_markup=reply_markup, db=db)


def process_telegram_updates(db: Session) -> int:
    """
    Polling ringan untuk memproses tombol callback 'Setujui' atau 'Tolak' dari Telegram.
    Dijalankan secara berkala di background.
    """
    bot_token = get_setting("telegram_bot_token", "", db=db).strip()
    if not bot_token:
        return 0

    last_offset_str = get_setting("telegram_last_update_id", "0", db=db)
    offset = int(last_offset_str) if last_offset_str.isdigit() else 0

    url = f"https://api.telegram.org/bot{bot_token}/getUpdates?offset={offset + 1}&timeout=1"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "SCPool/1.0"})
        with urllib.request.urlopen(req, timeout=5.0) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            if not data.get("ok"):
                return 0

            updates = data.get("result", [])
            processed_count = 0

            for upd in updates:
                update_id = upd.get("update_id", 0)
                if update_id > offset:
                    offset = update_id

                # Tangani pesan teks masuk (seperti /start) untuk auto-binding Chat ID Admin
                msg = upd.get("message")
                if msg:
                    chat = msg.get("chat", {})
                    incoming_chat_id = chat.get("id")
                    from_user = msg.get("from", {}).get("username") or msg.get("from", {}).get("first_name", "Admin")
                    if incoming_chat_id:
                        set_setting("telegram_chat_id", str(incoming_chat_id), db=db)
                        welcome_reply = (
                            f"🎉 *SCPool Bot Berhasil Terhubung!*\n"
                            f"━━━━━━━━━━━━━━━━━━━━\n"
                            f"Halo @{from_user}, Chat ID Anda (`{incoming_chat_id}`) telah otomatis terhubung ke SCPool.\n"
                            f"Notifikasi pendaftaran akun klien baru & tombol persetujuan akan langsung dikirimkan ke sini.\n\n"
                            f"— *Coded By Ardh1337*"
                        )
                        send_telegram_message(text=welcome_reply, db=db)

                # Tangani Callback Query dari inline buttons
                cb = upd.get("callback_query")
                if cb:
                    cb_id = cb.get("id")
                    data_str = cb.get("data", "")
                    from_user = cb.get("from", {}).get("username", "Admin")

                    if data_str.startswith("approve_"):
                        try:
                            uid = int(data_str.split("_")[1])
                            target_user = db.query(UserAccount).filter(UserAccount.id == uid).first()
                            if target_user:
                                target_user.is_active = True
                                db.commit()
                                _answer_callback(bot_token, cb_id, f"✅ Akun {target_user.username} disetujui!")

                                pool = db.query(ModemPool).filter(ModemPool.id == target_user.pool_id).first()
                                pool_name = pool.name if pool else f"Modem Pool {target_user.username}"
                                port_count = pool.port_count if pool else 16
                                now_str = datetime.now().strftime("%d-%m-%Y %H:%M:%S")
                                domain = get_setting("public_domain", "https://scpool.ardh1337.cloud", db=db).rstrip("/")
                                ws_domain = domain.replace("https://", "wss://").replace("http://", "ws://") + "/ws/agent"

                                detail_card = (
                                    "✅ *AKUN KLIEN BERHASIL DISETUJUI*\n"
                                    "━━━━━━━━━━━━━━━━━━━━\n"
                                    f"👤 *Username:* `{target_user.username}`\n"
                                    f"📦 *Modem Pool:* `{pool_name}`\n"
                                    f"🔌 *Kapasitas Port:* `{port_count} Port`\n"
                                    f"🔑 *Agent Token:* `{target_user.agent_token}`\n"
                                    f"🕒 *Waktu Disetujui:* `{now_str}`\n"
                                    f"👮 *Disetujui Oleh:* `@{from_user}`\n"
                                    f"📌 *Status:* `AKTIF (Siap Digunakan)`\n"
                                    "━━━━━━━━━━━━━━━━━━━━\n"
                                    "📥 *Tautan File Agen Klien:*\n"
                                    f"• [Unduh run_agent.bat]({domain}/api/download/agent?file_type=bat&token={target_user.agent_token})\n"
                                    f"• [Unduh agent_forwarder.py]({domain}/api/download/agent?file_type=script&token={target_user.agent_token})\n\n"
                                    f"🌐 *Target WebSocket:* `{ws_domain}`\n"
                                    f"📊 *Dashboard:* {domain}/#settings\n\n"
                                    "— *Coded By Ardh1337*"
                                )

                                msg = cb.get("message")
                                if msg:
                                    chat_id = msg.get("chat", {}).get("id")
                                    msg_id = msg.get("message_id")
                                    # Hapus pesan notifikasi lama dengan tombol
                                    deleted = _delete_message(bot_token, chat_id, msg_id)
                                    if deleted:
                                        send_telegram_message(text=detail_card, db=db)
                                    else:
                                        _edit_message_text(bot_token, chat_id, msg_id, detail_card)
                                processed_count += 1
                        except Exception as e:
                            logger.error(f"Error approving user via Telegram: {e}")

                    elif data_str.startswith("reject_"):
                        try:
                            uid = int(data_str.split("_")[1])
                            target_user = db.query(UserAccount).filter(UserAccount.id == uid).first()
                            if target_user:
                                target_user.is_active = False
                                db.commit()
                                _answer_callback(bot_token, cb_id, f"❌ Pendaftaran {target_user.username} ditolak.")

                                pool = db.query(ModemPool).filter(ModemPool.id == target_user.pool_id).first()
                                pool_name = pool.name if pool else f"Modem Pool {target_user.username}"
                                now_str = datetime.now().strftime("%d-%m-%Y %H:%M:%S")

                                reject_card = (
                                    "❌ *PENDAFTARAN KLIEN DITOLAK*\n"
                                    "━━━━━━━━━━━━━━━━━━━━\n"
                                    f"👤 *Username:* `{target_user.username}`\n"
                                    f"📦 *Modem Pool:* `{pool_name}`\n"
                                    f"🕒 *Waktu:* `{now_str}`\n"
                                    f"👮 *Ditolak Oleh:* `@{from_user}`\n"
                                    "📌 *Status:* Dinonaktifkan."
                                )

                                msg = cb.get("message")
                                if msg:
                                    chat_id = msg.get("chat", {}).get("id")
                                    msg_id = msg.get("message_id")
                                    deleted = _delete_message(bot_token, chat_id, msg_id)
                                    if deleted:
                                        send_telegram_message(text=reject_card, db=db)
                                    else:
                                        _edit_message_text(bot_token, chat_id, msg_id, reject_card)
                                processed_count += 1
                        except Exception as e:
                            logger.error(f"Error rejecting user via Telegram: {e}")

            if offset > int(last_offset_str):
                set_setting("telegram_last_update_id", str(offset), db=db)

            return processed_count
    except Exception:
        return 0


def _answer_callback(bot_token: str, callback_query_id: str, text: str):
    """Memberi respon pop-up notifikasi ke Telegram user setelah klik tombol inline."""
    try:
        url = f"https://api.telegram.org/bot{bot_token}/answerCallbackQuery"
        payload = json.dumps({"callback_query_id": callback_query_id, "text": text}).encode("utf-8")
        req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"}, method="POST")
        urllib.request.urlopen(req, timeout=3.0)
    except Exception:
        pass


def _delete_message(bot_token: str, chat_id: int, message_id: int) -> bool:
    """Menghapus pesan sebelumnya di Telegram agar digantikan pesan detail baru."""
    try:
        url = f"https://api.telegram.org/bot{bot_token}/deleteMessage"
        payload = json.dumps({"chat_id": chat_id, "message_id": message_id}).encode("utf-8")
        req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=3.0) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return bool(data.get("ok"))
    except Exception:
        return False


def _edit_message_text(bot_token: str, chat_id: int, message_id: int, text: str):
    """Memperbarui teks pesan di Telegram jika penghapusan pesan tidak dimungkinkan."""
    try:
        url = f"https://api.telegram.org/bot{bot_token}/editMessageText"
        payload = json.dumps({
            "chat_id": chat_id,
            "message_id": message_id,
            "text": text,
            "parse_mode": "Markdown"
        }).encode("utf-8")
        req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"}, method="POST")
        urllib.request.urlopen(req, timeout=3.0)
    except Exception:
        pass

