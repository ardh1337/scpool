"""USSD: kirim dial code, tunggu balasan jaringan (+CUSD), dan ekstrak nomor telepon."""

import re
import time
from typing import Optional, Tuple

from app.hardware.operators import FALLBACK_USSD_CODE, default_profiles, match_operator
from app.modem.at_client import ATClient

USSD_CODE_PATTERN = re.compile(r"^[0-9*#+]{2,32}\Z")

_CUSD_WITH_TEXT = re.compile(r'\+CUSD:\s*(\d+)\s*,\s*"(.*?)"\s*(?:,\s*\d+\s*)?$', re.IGNORECASE | re.DOTALL)
_CUSD_STATUS_ONLY = re.compile(r"\+CUSD:\s*(\d+)\s*$", re.IGNORECASE | re.MULTILINE)

_CUSD_STATUS_MESSAGES = {
    2: "Sesi USSD diakhiri tanpa pesan.",
    4: "Operasi USSD tidak didukung oleh jaringan.",
    5: "Jaringan tidak merespons permintaan USSD.",
}


def validate_ussd_code(code: Optional[str]) -> str:
    """Kode USSD hanya boleh terdiri dari 0-9 * # + agar tidak bisa menyisipkan perintah AT lain."""
    cleaned = (code or "").strip()
    if not USSD_CODE_PATTERN.match(cleaned):
        raise ValueError("Kode USSD tidak valid. Hanya angka, *, #, dan + (2-32 karakter) yang diizinkan.")
    return cleaned


def decode_ussd_text(raw_text: str) -> str:
    """Dekode teks USSD: teks biasa atau Hex UCS2 (UTF-16BE)."""
    cleaned = raw_text.strip().strip('"')
    if len(cleaned) >= 8 and len(cleaned) % 4 == 0 and all(c in "0123456789ABCDEFabcdef" for c in cleaned):
        try:
            decoded = bytes.fromhex(cleaned).decode("utf-16-be", errors="ignore")
            if any(c.isalnum() for c in decoded):
                return decoded
        except ValueError:
            pass
    return cleaned


def extract_phone_number_from_text(text: str) -> Optional[str]:
    """Ekstrak nomor Indonesia (08xx / 628xx / +628xx) dan standarkan ke +628xxx."""
    if not text:
        return None
    matches = re.findall(r"(?<!\d)(?:\+?62|0)(8\d{8,11})(?!\d)", text)
    return f"+62{matches[0]}" if matches else None


def get_default_ussd_for_iccid(iccid: Optional[str], operator_name: Optional[str] = None,
                               db: Optional[object] = None) -> str:
    """Kode USSD cek-nomor berdasarkan profil operator di DB; jatuh ke profil bawaan bila tidak cocok."""
    if db is not None:
        try:
            from app.database.models import OperatorProfile
            matched = match_operator(db.query(OperatorProfile).all(), iccid, operator_name)
            if matched:
                return matched.ussd_code
        except Exception:
            pass
    matched = match_operator(default_profiles(), iccid, operator_name)
    return matched.ussd_code if matched else FALLBACK_USSD_CODE


def _parse_cusd(text: str) -> Tuple[Optional[int], Optional[str]]:
    """(kode status n, teks pesan). Teks None bila modem hanya melaporkan status tanpa pesan."""
    match = _CUSD_WITH_TEXT.search(text)
    if match:
        return int(match.group(1)), decode_ussd_text(match.group(2))
    only = _CUSD_STATUS_ONLY.search(text)
    if only:
        return int(only.group(1)), None
    return None, None


def ussd_result_from_text(raw: str) -> Tuple[bool, Optional[str], Optional[str]]:
    """Interpretasi teks mentah yang memuat baris +CUSD (dipakai juga untuk balasan dari agent remote)."""
    status, message = _parse_cusd(raw or "")
    if message:
        return True, message, extract_phone_number_from_text(message)
    if status is not None:
        return False, _CUSD_STATUS_MESSAGES.get(status, f"Balasan USSD tanpa pesan (status {status})."), None
    return False, "Tidak ada balasan USSD dari jaringan sebelum waktu habis.", None


def execute_ussd(client: ATClient, ussd_code: str, timeout: float = 12.0) -> Tuple[bool, Optional[str], Optional[str]]:
    """
    Kirim USSD dan tunggu +CUSD, baik yang datang sebelum maupun sesudah "OK".
    Mengembalikan (berhasil, teks balasan / pesan error, nomor telepon atau None).
    """
    code = validate_ussd_code(ussd_code)
    deadline = time.time() + timeout

    try:  # bersihkan sesi USSD yang menggantung (hindari +CME ERROR: 32)
        client.send_command("AT+CUSD=2", timeout=2.0)
    except Exception:
        pass

    resp = client.send_command(f'AT+CUSD=1,"{code}"', timeout=timeout)
    status, message = _parse_cusd(resp.raw_response or "")

    if status is None and resp.success:
        remaining = deadline - time.time()
        if remaining > 0:
            late = client.wait_for_urc("+CUSD:", remaining)
            if late:
                status, message = _parse_cusd(late)

    if message:
        return True, message, extract_phone_number_from_text(message)
    if status is not None:
        return False, _CUSD_STATUS_MESSAGES.get(status, f"Balasan USSD tanpa pesan (status {status})."), None
    if not resp.success:
        return False, resp.error_message or "Gagal menjalankan perintah USSD.", None
    return False, "Tidak ada balasan USSD dari jaringan sebelum waktu habis.", None
