"""
Pemeriksa Status SIM dan Parser ICCID
Mengimplementasikan validasi ICCID dan parsing status kartu (Section 7 & Invarian I-2).
"""

import re
from typing import Optional, Tuple
from app.modem.at_client import ATClient, ATResponse


def clean_and_validate_iccid(raw_iccid: str) -> Optional[str]:
    """
    Validasi ICCID:
    - 19–20 digit angka.
    - Dimulai dengan '89' (standar ITU-T E.118).
    - Membuang karakter pengisi seperti 'F', spasi, atau tanda kutip.
    """
    if not raw_iccid:
        return None

    # Buang tanda kutip, spasi, dan karakter non-alphanumeric
    cleaned = re.sub(r'["\s]', '', raw_iccid).upper()
    # Buang trailing 'F' yang biasa digunakan sebagai padding pada kartu SIM
    if cleaned.endswith('F'):
        cleaned = cleaned[:-1]

    # Ekstrak rangkaian digit yang diawali '89'
    match = re.search(r'89\d{17,18}', cleaned)
    if not match:
        return None

    candidate = match.group(0)
    # Panjang ICCID valid adalah 19 atau 20 digit
    if 19 <= len(candidate) <= 20:
        return candidate

    return None


def parse_cpin_response(response: ATResponse) -> str:
    """
    Menganalisis hasil AT+CPIN?.
    Mengembalikan status: 'READY', 'SIM PIN', 'SIM PUK', 'NOT INSERTED', 'ERROR'.
    """
    if not response.success:
        err = (response.error_message or "").upper()
        if "NOT INSERTED" in err or "+CME ERROR: 10" in err:
            return "NOT INSERTED"
        return "ERROR"

    for line in response.lines:
        if "+CPIN:" in line:
            parts = line.split(":", 1)
            if len(parts) > 1:
                return parts[1].strip()

    return "UNKNOWN"


def parse_iccid_response(response: ATResponse) -> Optional[str]:
    """
    Mengekstrak ICCID dari balasan AT+QCCID atau AT+CCID.
    """
    if not response.success:
        return None

    for line in response.lines:
        # Format umum: +QCCID: 8962... atau langsung 8962...
        if "+QCCID:" in line or "+CCID:" in line:
            parts = line.split(":", 1)
            if len(parts) > 1:
                validated = clean_and_validate_iccid(parts[1])
                if validated:
                    return validated
        else:
            validated = clean_and_validate_iccid(line)
            if validated:
                return validated

    return None


def parse_cnum_response(response: ATResponse) -> Optional[str]:
    """
    Mengekstrak nomor telepon dari AT+CNUM.
    Contoh balasan: +CNUM: "My Number","+62812345678",145
    Invarian I-2: Jika tidak ditemukan atau kosong, jangan pernah menebak.
    """
    if not response.success:
        return None

    for line in response.lines:
        if "+CNUM:" in line:
            # Mencari nomor di dalam tanda kutip kedua atau format angka internasional
            matches = re.findall(r'"([^"]*)"', line)
            for m in matches:
                digits_only = re.sub(r'[\s\-\+]', '', m)
                if len(digits_only) >= 8 and digits_only.isdigit():
                    return m.strip()

    return None
