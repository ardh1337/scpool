"""
Daftar Izin Perintah AT (AT Command Allowlist)
Mengacu pada Lampiran A agent-spec-sim-inventory.md dan Invarian I-13.
Perintah di luar daftar ini ditolak oleh Klien AT kecuali diizinkan secara eksplisit oleh operator.
"""

from typing import Set
import re

# Perintah baca aman (Read-Only)
READ_ONLY_PREFIXES: Set[str] = {
    "AT",
    "ATI",
    "AT+CGMI",
    "AT+CGMM",
    "AT+CGMR",
    "AT+CGSN",
    "AT+CPIN?",
    "AT+QCCID",
    "AT+CCID",
    "AT+CIMI",
    "AT+QSIMSTAT?",
    "AT+QSIMDET?",
    "AT+CNUM",
    "AT+COPS?",
    "AT+CREG?",
    "AT+CSQ",
    "AT+CMGF?",
    "AT+CPMS?",
    "AT+CNMI?",
}

# Perintah baca pesan berparameter (AT+CMGL=..., AT+CMGR=...)
SMS_READ_PATTERNS = [
    re.compile(r"^AT\+CMGL(=.*)?$", re.IGNORECASE),
    re.compile(r"^AT\+CMGR=\d+$", re.IGNORECASE),
    re.compile(r"^AT\+CUSD=\d+(,.*)?$", re.IGNORECASE),
]

# Perintah konfigurasi aman yang diizinkan untuk inisialisasi pembacaan SMS
CONFIG_ALLOWLIST_PATTERNS = [
    re.compile(r"^AT\+CMGF=[01]$", re.IGNORECASE),               # Mode PDU (0) atau Teks (1)
    re.compile(r"^AT\+CPMS=\"[A-Z]+\",\"[A-Z]+\",\"[A-Z]+\"$", re.IGNORECASE),  # Pilih storage
    re.compile(r"^AT\+CNMI=\d+,\d+,\d+,\d+,\d+$", re.IGNORECASE), # Notifikasi SMS
    re.compile(r"^AT\+QSIMSTAT=[01]$", re.IGNORECASE),           # SIM status reporting
    re.compile(r"^AT\+QSIMDET=[01],[01]$", re.IGNORECASE),       # SIM detect config
]

# Perintah hapus pesan (Hanya diizinkan jika diverifikasi sudah tersimpan di DB, default non-aktif)
SMS_DELETE_PATTERN = re.compile(r"^AT\+CMGD=\d+(,\d+)?$", re.IGNORECASE)

# Perintah berbahaya yang mutlak dilarang (Blocklist keras)
STRICT_BANNED_PREFIXES = [
    "AT&F",
    "ATZ",
    "AT+CFUN=0",
    "AT+CFUN=1,1",
    "AT+QPOWD",
]


def is_at_command_allowed(command: str, allow_delete: bool = False) -> bool:
    """
    Validasi perintah AT terhadap allowlist resmi.
    Mengembalikan True jika perintah diizinkan, False jika ditolak.
    """
    cleaned = command.strip().upper()
    if not cleaned:
        return False

    for banned in STRICT_BANNED_PREFIXES:
        if cleaned.startswith(banned):
            return False

    if cleaned in READ_ONLY_PREFIXES:
        return True

    for pattern in SMS_READ_PATTERNS:
        if pattern.match(cleaned):
            return True

    for pattern in CONFIG_ALLOWLIST_PATTERNS:
        if pattern.match(cleaned):
            return True

    if allow_delete and SMS_DELETE_PATTERN.match(cleaned):
        return True

    return False

