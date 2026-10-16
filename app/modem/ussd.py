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


def get_default_ussd_for_iccid(
    iccid: Optional[str],
    operator_name: Optional[str] = None,
    db: Optional[object] = None
) -> str:
    """
    Mengembalikan kode dial USSD yang sesuai berdasarkan profil operator tersimpan di DB
    atau fallback berdasarkan prefiks ICCID / nama operator.
    """
    clean_iccid = re.sub(r'\D', '', iccid) if iccid else ""
    op_clean = (operator_name or "").strip().upper()

    # 1. Coba cari profil tersimpan di DB
    if db:
        try:
            from app.database.models import OperatorProfile
            profiles = db.query(OperatorProfile).all()

            # Cocokkan berdasarkan prefiks ICCID
            if clean_iccid:
                for p in profiles:
                    if p.iccid_prefixes:
                        prefixes = [x.strip() for x in p.iccid_prefixes.split(",") if x.strip()]
                        if any(clean_iccid.startswith(pfx) for pfx in prefixes):
                            return p.ussd_code

            # Cocokkan berdasarkan nama operator
            if op_clean:
                for p in profiles:
                    if p.operator_patterns:
                        patterns = [x.strip().upper() for x in p.operator_patterns.split(",") if x.strip()]
                        if any(pat in op_clean for pat in patterns):
                            return p.ussd_code
        except Exception:
            pass

    # 2. Fallback default jika DB tidak ada / tidak cocok
    if clean_iccid.startswith("896211") or clean_iccid.startswith("896212") or "XL" in op_clean or "AXIS" in op_clean:
        return "*808*7*8#"
    elif clean_iccid.startswith("896210") or clean_iccid.startswith("896204") or "TELKOMSEL" in op_clean or "T-SEL" in op_clean:
        return "*808#"
    elif clean_iccid.startswith("896201") or clean_iccid.startswith("896221") or "INDOSAT" in op_clean or "IM3" in op_clean:
        return "*123*30#"
    elif clean_iccid.startswith("896289") or "TRI" in op_clean:
        return "*111*1#"
    elif clean_iccid.startswith("896209") or "SMARTFREN" in op_clean:
        return "*999#"

    return "*808*7*8#"


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

    # Batalkan sesi USSD aktif terlebih dahulu untuk menghindari +CME ERROR: 32
    try:
        client.send_command("AT+CUSD=2", timeout=2.0)
    except Exception:
        pass

    try:
        # Kirim perintah USSD standar
        cmd = f'AT+CUSD=1,"{ussd_code}"'
        resp = client.send_command(cmd, timeout=timeout)

        # 1. Cek langsung di raw_response (dengan dukungan multi-baris re.DOTALL)
        source_text = resp.raw_response or ""
        match = re.search(r'\+CUSD:\s*\d+,\s*"([^"]*)"', source_text, re.IGNORECASE | re.DOTALL)
        if match:
            raw_str = match.group(1)
            decoded = decode_ussd_text(raw_str)
            phone = extract_phone_number_from_text(decoded)
            return True, decoded, phone

        # Format cadangan jika tanda kutip terpotong atau format non-standar
        match_alt = re.search(r'\+CUSD:\s*\d+,\s*"(.*?)(?:"\s*,\s*\d+|"$|$)', source_text, re.IGNORECASE | re.DOTALL)
        if match_alt and match_alt.group(1):
            raw_str = match_alt.group(1)
            decoded = decode_ussd_text(raw_str)
            phone = extract_phone_number_from_text(decoded)
            if phone or len(decoded) > 5:
                return True, decoded, phone

        # 2. Jika belum ada di raw_response, tunggu URC terpisah
        event.wait(timeout=2.0)
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

