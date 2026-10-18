"""
Notifikasi dan persetujuan registrasi lewat bot Telegram (hanya urllib dari pustaka standar).

Keamanan (perbaikan dari versi sebelumnya):
- Chat ID admin hanya terikat otomatis bila BELUM dikonfigurasi. Sebelumnya pesan dari siapa pun ke bot menimpa
  Chat ID admin sehingga orang asing bisa menerima notifikasi dan menyetujui akun.
- Tombol Setujui/Tolak hanya diterima dari chat yang sama dengan Chat ID admin terkonfigurasi.
- Token bot tidak pernah ikut tertulis di pesan error / log.
"""

from __future__ import annotations

import json
import logging
import threading
import urllib.request
from datetime import datetime
from typing import Optional, Tuple

from sqlalchemy.orm import Session

from app.core.config import settings as env_settings
from app.database.connection import session_scope
from app.database.models import UserAccount
from app.services.accounts import approve_account, delete_account, is_admin_account, md_escape
from app.services.settings import get_public_domain, get_setting, set_setting

logger = logging.getLogger(__name__)

API_BASE = "https://api.telegram.org"


def _api_post(bot_token: str, method: str, payload: dict, timeout: float = 8.0) -> dict:
    req = urllib.request.Request(
        f"{API_BASE}/bot{bot_token}/{method}",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def send_telegram_message(text: str, reply_markup: Optional[dict] = None,
                          db: Optional[Session] = None) -> Tuple[bool, str]:
    bot_token = get_setting("telegram_bot_token", "", db=db).strip()
    chat_id = get_setting("telegram_chat_id", "", db=db).strip()
    if not bot_token or not chat_id:
        return False, "Token Bot atau Chat ID Telegram belum dikonfigurasi di Pengaturan Sistem."

    payload = {"chat_id": chat_id, "text": text, "parse_mode": "Markdown"}
    if reply_markup:
        payload["reply_markup"] = reply_markup
    try:
        data = _api_post(bot_token, "sendMessage", payload)
        if data.get("ok"):
            return True, "Pesan Telegram berhasil terkirim."
        return False, f"Telegram error: {data.get('description', 'Unknown')}"
    except Exception as exc:
        # str(exc) bisa memuat URL berisi token bot; jangan dikembalikan ke klien/log apa adanya.
        logger.warning("Gagal mengirim pesan Telegram: %s", type(exc).__name__)
        return False, f"Gagal menghubungi Telegram ({type(exc).__name__})."


def notify_new_registration(user: UserAccount, db: Optional[Session] = None) -> Tuple[bool, str]:
    domain = get_public_domain(db)
    time_str = user.created_at.strftime("%Y-%m-%d %H:%M:%S UTC") if user.created_at else "Baru saja"
    text = (
        "🔔 *PENDAFTARAN KLIEN BARU*\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"👤 *Username:* `{user.username}`\n"
        f"🕒 *Waktu:* `{time_str}`\n\n"
        "Status: ⏳ *Menunggu Persetujuan Admin*"
        + (f"\nBuka Dashboard: {domain}/#settings" if domain else "")
    )
    reply_markup = {"inline_keyboard": [[
        {"text": "✅ Setujui Akun", "callback_data": f"approve_{user.id}"},
        {"text": "❌ Tolak", "callback_data": f"reject_{user.id}"},
    ]]}
    return send_telegram_message(text=text, reply_markup=reply_markup, db=db)


def _safe_call(bot_token: str, method: str, payload: dict) -> bool:
    try:
        return bool(_api_post(bot_token, method, payload, timeout=5.0).get("ok"))
    except Exception:
        return False


def _approval_card(target: UserAccount, approver: str, db: Session) -> str:
    domain = get_public_domain(db)
    lines = [
        "✅ *AKUN KLIEN BERHASIL DISETUJUI*",
        "━━━━━━━━━━━━━━━━━━━━",
        f"👤 *Username:* `{target.username}`",
        f"👮 *Disetujui Oleh:* {md_escape(approver)}",
        f"🕒 *Waktu:* `{datetime.now().strftime('%d-%m-%Y %H:%M:%S')}`",
    ]
    if domain:
        base = f"{domain}/api/download/agent?token={target.agent_token}"
        lines += ["📥 *File Agen Klien (token tertanam):*",
                  f"• [Unduh scpool_agent.bat]({base}&file_type=bat)",
                  f"• [Unduh scpool_agent.py]({base}&file_type=py)"]
    return "\n".join(lines)


def _handle_callback(db: Session, bot_token: str, authorized_chat: str, cb: dict) -> bool:
    message = cb.get("message") or {}
    chat_id = (message.get("chat") or {}).get("id")
    if str(chat_id) != authorized_chat:
        logger.warning("Callback Telegram dari chat tidak berwenang diabaikan.")
        return False

    action, _, raw_id = (cb.get("data") or "").partition("_")
    if action not in ("approve", "reject") or not raw_id.isdigit():
        return False
    target = db.query(UserAccount).filter(UserAccount.id == int(raw_id)).first()
    if not target or is_admin_account(target):
        _safe_call(bot_token, "answerCallbackQuery",
                   {"callback_query_id": cb.get("id"), "text": "Akun tidak ditemukan."})
        return False

    actor = (cb.get("from") or {}).get("username") or "admin"
    if action == "approve":
        approve_account(db, target)
        card = _approval_card(target, f"@{actor}", db)
        toast = f"✅ Akun {target.username} disetujui!"
    else:
        username = target.username
        delete_account(db, target, protected_pool_id=env_settings.local_pool_id)
        card = f"❌ *PENDAFTARAN KLIEN DITOLAK*\n👤 `{username}`\n👮 {md_escape('@' + actor)}"
        toast = f"❌ Pendaftaran {username} ditolak."

    _safe_call(bot_token, "answerCallbackQuery", {"callback_query_id": cb.get("id"), "text": toast})
    if not _safe_call(bot_token, "editMessageText", {
        "chat_id": chat_id, "message_id": message.get("message_id"), "text": card, "parse_mode": "Markdown",
    }):
        send_telegram_message(card, db=db)
    return True


def process_telegram_updates(db: Session, long_poll_seconds: int = 0) -> int:
    """Ambil pembaruan Telegram dan proses tombol persetujuan. Mengembalikan jumlah aksi yang diproses."""
    bot_token = get_setting("telegram_bot_token", "", db=db).strip()
    if not bot_token:
        return 0
    last = get_setting("telegram_last_update_id", "0", db=db)
    offset = int(last) if last.isdigit() else 0
    authorized_chat = get_setting("telegram_chat_id", "", db=db).strip()

    try:
        data = _api_post(
            bot_token, "getUpdates",
            {"offset": offset + 1, "timeout": long_poll_seconds, "allowed_updates": ["message", "callback_query"]},
            timeout=long_poll_seconds + 8.0,
        )
    except Exception as exc:
        logger.debug("getUpdates gagal: %s", type(exc).__name__)
        return 0
    if not data.get("ok"):
        return 0

    processed = 0
    new_offset = offset
    for upd in data.get("result", []):
        new_offset = max(new_offset, upd.get("update_id", 0))

        msg = upd.get("message")
        if msg and not authorized_chat and str(msg.get("text", "")).startswith("/start"):
            chat_id = (msg.get("chat") or {}).get("id")
            if chat_id:  # auto-bind hanya saat Chat ID masih kosong (pengaturan awal)
                set_setting("telegram_chat_id", str(chat_id), "Chat ID Admin Telegram", db=db)
                authorized_chat = str(chat_id)
                send_telegram_message("🎉 *SCPool Bot terhubung.* Notifikasi pendaftaran klien akan dikirim ke chat ini.",
                                      db=db)
            continue

        cb = upd.get("callback_query")
        if cb and authorized_chat:
            try:
                if _handle_callback(db, bot_token, authorized_chat, cb):
                    processed += 1
            except Exception:
                db.rollback()
                logger.exception("Gagal memproses callback Telegram")

    if new_offset > offset:
        set_setting("telegram_last_update_id", str(new_offset), db=db)
    return processed


class TelegramPoller:
    """Thread terpisah untuk long-polling Telegram (sebelumnya menumpang di loop pemindai serial dan menahannya)."""

    def __init__(self):
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="TelegramPoller", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=3.0)
        self._thread = None

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                with session_scope() as db:
                    has_token = bool(get_setting("telegram_bot_token", "", db=db).strip())
                    if has_token:
                        process_telegram_updates(db, long_poll_seconds=20)
                if not has_token:
                    self._stop.wait(10.0)
            except Exception:
                logger.exception("Telegram poller error")
                self._stop.wait(10.0)


telegram_poller = TelegramPoller()
