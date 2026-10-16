"""
Modul Penanganan USSD (Unstructured Supplementary Service Data)
Mengirimkan dial code USSD (misal *808# atau *123#) dan mengekstrak nomor telepon (Tahap M5).
"""

import re
import time
import threading
from typing import Optional, Tuple
from app.modem.at_client import ATClient, ATResponse


def decode_ussd_text(raw_text: str) -> str:
    """
    Mendekode teks balasan USSD:
    - Menangani teks langsung / ASCII.
    - Menangani teks berformat Hex UCS2 (UTF-16BE) yang sering dikirim oleh modem/jaringan.
    """
    cleaned = raw_text.strip().strip('"')
    # Cek apakah string adalah Hex UCS2 (semua hex dan panjang kelipatan 4)
    if len(cleaned) >= 8 and len(cleaned) % 4 == 0 and all(c in "0123456789ABCDEFabcdef" for c in cleaned):
        try:
            raw_bytes = bytes.fromhex(cleaned)
            decoded = raw_bytes.decode("utf-16-be", errors="ignore")
            if any(c.isalnum() for c in decoded):
                return decoded
        except Exception:
            pass

    return cleaned


def extract_phone_number_from_text(text: str) -> Optional[str]:
    """
    Mengekstrak nomor telepon Indonesia dari teks balasan USSD:
    - Format: 08xx atau 628xx atau +628xx (10-13 digit)
    """
    if not text:
        return None

    # Cari nomor yang diawali 08, 628, atau +628
    matches = re.findall(r'(?:\+?62|0)(8\d{8,11})\b', text)
    if matches:
        # Standarisasi ke format internasional +628xxx
        suffix = matches[0]
        return f"+62{suffix}"

    return None


def execute_ussd(
    client: ATClient,
    ussd_code: str,
    timeout: float = 12.0
) -> Tuple[bool, Optional[str], Optional[str]]:
    """
    Mengirim perintah USSD dan menunggu kedatangan URC +CUSD dari jaringan.
    Mengembalikan: (success: bool, raw_response_text, extracted_phone_number)
    """
    ussd_response = None
    event = threading.Event()

    def on_urc(line: str):
        nonlocal ussd_response
        if line.upper().startswith("+CUSD:"):
            ussd_response = line
            event.set()

    # Daftarkan sementara listener URC
    old_cb = client.urc_callback
    client.register_urc_callback(on_urc)

    try:
        # Kirim perintah USSD standar
        cmd = f'AT+CUSD=1,"{ussd_code}"'
        resp = client.send_command(cmd, timeout=8.0)

        # 1. Cek langsung di raw_response (dengan dukungan multi-baris re.DOTALL)
        source_text = resp.raw_response or ""
        match = re.search(r'\+CUSD:\s*\d+,\s*"([^"]*)"', source_text, re.IGNORECASE | re.DOTALL)
        if match:
            raw_str = match.group(1)
            decoded = decode_ussd_text(raw_str)
            phone = extract_phone_number_from_text(decoded)
            return True, decoded, phone

        # 2. Jika belum ada di raw_response, tunggu URC terpisah
        event.wait(timeout=timeout)
        if ussd_response:
            match = re.search(r'\+CUSD:\s*\d+,\s*"([^"]*)"', ussd_response, re.IGNORECASE | re.DOTALL)
            if match:
                raw_str = match.group(1)
                decoded = decode_ussd_text(raw_str)
                phone = extract_phone_number_from_text(decoded)
                return True, decoded, phone

        if not resp.success:
            err = resp.error_message or "Gagal menjalankan perintah USSD."
            return False, err, None

        return True, source_text, None

    finally:
        client.register_urc_callback(old_cb)

