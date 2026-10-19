"""
Ekstraksi kode OTP dari isi SMS. Logikanya sama dengan extractOTP() di app/web/app.js agar kode yang dikirim API
sama dengan yang tampil di dashboard (Fast OTP). Ubah keduanya bersamaan.
"""

import re
from typing import Optional

_KEYWORD = re.compile(r"\b(otp|kode(?!\s*promo|\s*voucher)|code(?!\s*promo|\s*voucher)|verifikasi|verification|"
                      r"passcode|one-time|rahasia|pin|konfirmasi|confirmation|security\s+code)\b", re.I)
_GOOGLE = re.compile(r"\b(?:G-|c-)([0-9]{6})\b", re.I)
_CODE_BEFORE = re.compile(r"\b([0-9]{3}[-\s]?[0-9]{3}|[0-9]{4,8})\b\s*(?:adalah|is\s+(?:your\s+)?[\w\s]{0,25}?"
                          r"(?:code|kode|otp|pin|passcode|verifikasi|verification|password))", re.I)
_CODE_AFTER = re.compile(r"(?:otp|kode|code|pin|passcode|verifikasi|verification|security\s+code)[^\d\n:=]{0,35}"
                         r"[:=is|adalah\-]?\s*([0-9]{3}[-\s]?[0-9]{3}|[0-9]{4,8})\b", re.I)
_STRONG_KEYWORD = re.compile(r"\b(otp|verifikasi|verification|rahasia)\b", re.I)
_YEAR = re.compile(r"^(?:199\d|20[2-3]\d)$")


def _clean(code: str) -> Optional[str]:
    code = re.sub(r"[-\s]", "", code)
    return None if _YEAR.match(code) else code


def extract_otp(text: Optional[str]) -> Optional[str]:
    if not text or not _KEYWORD.search(text):
        return None
    match = _GOOGLE.search(text)
    if match:
        return match.group(1)
    for pattern in (_CODE_BEFORE, _CODE_AFTER):
        match = pattern.search(text)
        if match and _clean(match.group(1)):
            return _clean(match.group(1))
    if _STRONG_KEYWORD.search(text):
        for code in re.findall(r"\b[0-9]{4,8}\b", text):
            if _YEAR.match(code) or re.match(r"^\d000+$", code) or code.startswith(("08", "62")):
                continue
            escaped = re.escape(code)
            if re.search(r"(?:rp\.?|idr)\s*" + escaped, text, re.I):
                continue
            if re.search(escaped + r"\s*(?:gb|mb|kb|hr|hari|bln|bulan|thn|tahun|menit|mnt|detik|sec)", text, re.I):
                continue
            if re.search(r"(?:sn|ref|trx|id|no)\s*[:=.]?\s*" + escaped, text, re.I):
                continue
            return code
    return None
