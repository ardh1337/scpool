"""
Daftar Izin Perintah AT (AT Command Allowlist)
Mengacu pada Lampiran A docs/agent-spec-sim-inventory.md dan Invarian I-13.
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
    "AT+CUSD?",
    "AT+CUSD=?",
    "AT+CSCS?",
    "AT+CSCS=?",
    "AT+CMEE?",
}

# Perintah baca pesan berparameter (AT+CMGL=..., AT+CMGR=...)
SMS_READ_PATTERNS = [
    re.compile(r"^AT\+CMGL(=(\d|\"[A-Z ]{1,20}\"))?\Z", re.IGNORECASE),
    re.compile(r"^AT\+CMGR=\d+\Z", re.IGNORECASE),
    # Kode USSD hanya boleh berisi 0-9 * # + (mencegah penyisipan perintah AT lain lewat tanda kutip/;)
    re.compile(r'^AT\+CUSD=[012](,"[0-9*#+]{1,32}"(,\d{1,3})?)?\Z', re.IGNORECASE),
]

# Perintah konfigurasi aman yang diizinkan untuk inisialisasi pembacaan SMS
CONFIG_ALLOWLIST_PATTERNS = [
    re.compile(r"^AT\+CMGF=[01]\Z", re.IGNORECASE),               # Mode PDU (0) atau Teks (1)
    re.compile(r"^AT\+CPMS=\"[A-Z]+\",\"[A-Z]+\",\"[A-Z]+\"\Z", re.IGNORECASE),  # Pilih storage
    re.compile(r"^AT\+CNMI=\d+,\d+,\d+,\d+,\d+\Z", re.IGNORECASE), # Notifikasi SMS
    re.compile(r"^AT\+QSIMSTAT=[01]\Z", re.IGNORECASE),           # SIM status reporting
    re.compile(r"^AT\+QSIMDET=[01],[01]\Z", re.IGNORECASE),       # SIM detect config
    re.compile(r"^AT\+CSCS=\"[A-Z]+\"\Z", re.IGNORECASE),         # TE Character Set (GSM / IRA)
    re.compile(r"^AT\+CMEE=[012]\Z", re.IGNORECASE),              # Report mobile equipment error (0=numeric, 1=numeric, 2=verbose)
]

# Perintah hapus pesan (Hanya diizinkan jika diverifikasi sudah tersimpan di DB, default non-aktif)
SMS_DELETE_PATTERN = re.compile(r"^AT\+CMGD=\d+(,\d+)?\Z", re.IGNORECASE)

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
    # Satu baris saja: CR/LF/NUL/Ctrl-Z di tengah perintah dapat menyelundupkan perintah kedua.
    if any(ch in cleaned for ch in "\r\n\x00\x1a\x1b"):
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

