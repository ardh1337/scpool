"""
Pembaca dan Pengurai Pesan SMS
Mendukung mode teks dan penguraian timestamp SMSC ke UTC, hashing pesan untuk I-9,
dan fungsi masking sensitif untuk I-12.
"""

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import re
from typing import List, Optional, Tuple


@dataclass
class ParsedSMS:
    index: int
    status_str: str          # misal: "REC UNREAD", "REC READ"
    sender_raw: str
    sender_normalized: str
    smsc_timestamp: datetime  # UTC
    body: str
    body_hash: str
    is_multipart: bool = False
    part_number: int = 1
    total_parts: int = 1
    ref_number: int = 0


def normalize_sender(sender: str) -> str:
    """
    Normalisasi konservatif nama pengirim / sender:
    - Menghapus spasi dan format penulisan umum.
    - Menjaga karakter alfanumerik asli.
    """
    cleaned = sender.strip().replace('"', '')
    if cleaned.startswith("+"):
        return "+" + re.sub(r'\D', '', cleaned[1:])
    if cleaned.isdigit():
        return cleaned
    return cleaned.upper()


def parse_smsc_timestamp(ts_str: str) -> datetime:
    """
    Mengurai timestamp standar GSM SMSC: 'yy/MM/dd,HH:mm:ss+zz' menjadi datetime UTC.
    Contoh: '26/10/16,08:30:00+28' (offset dalam kelipatan 15 menit).
    """
    cleaned = ts_str.strip('"\r\n ')
    try:
        # Pisahkan komponen tanggal+waktu dan timezone offset
        match = re.match(r'(\d{2})/(\d{2})/(\d{2}),(\d{2}):(\d{2}):(\d{2})([+-]\d{2})?', cleaned)
        if match:
            yy, mm, dd, hh, mi, ss, tz_offset = match.groups()
            year = 2000 + int(yy) if int(yy) < 70 else 1900 + int(yy)
            base_dt = datetime(year, int(mm), int(dd), int(hh), int(mi), int(ss))

            if tz_offset:
                # Format GSM timezone: unit 15 menit
                sign = 1 if tz_offset[0] == '+' else -1
                offset_quarters = int(tz_offset[1:])
                offset_seconds = sign * offset_quarters * 15 * 60
                tz = timezone(offset=datetime.fromtimestamp(0).tzinfo or timezone.utc)
                # Konversi langsung ke UTC
                utc_timestamp = base_dt.timestamp() - offset_seconds
                return datetime.fromtimestamp(utc_timestamp, timezone.utc)
            return base_dt.replace(tzinfo=timezone.utc)
    except Exception:
        pass

    return datetime.now(timezone.utc)


def compute_body_hash(body: str) -> str:
    """Menghitung SHA-256 hash dari teks pesan untuk kunci komposit I-9."""
    return hashlib.sha256(body.encode('utf-8')).hexdigest()


def mask_sensitive(text: str) -> str:
    """
    Masking data sensitif untuk log dan test fixtures (Invarian I-12):
    - Nomor telepon disensor (misal: +62812****789).
    - ICCID disensor (misal: 8962********1234).
    """
    if not text:
        return text

    # Sensor ICCID (19-20 digit diawali 89)
    def mask_iccid(m):
        val = m.group(0)
        return val[:4] + "*" * (len(val) - 8) + val[-4:]

    res = re.sub(r'\b89\d{17,18}\b', mask_iccid, text)

    # Sensor nomor telepon internasional / lokal
    def mask_phone(m):
        val = m.group(0)
        if len(val) >= 9:
            return val[:4] + "*" * (len(val) - 7) + val[-3:]
        return val

    res = re.sub(r'(\+?\d{9,15})', mask_phone, res)
    return res


def parse_cmgl_text_response(lines: List[str]) -> List[ParsedSMS]:
    """
    Pengurai hasil AT+CMGL pada mode teks (AT+CMGF=1).
    Format baris header:
    +CMGL: <index>,"<status>","<sender>",[alpha],"<timestamp>"
    Diikuti satu atau beberapa baris isi pesan hingga header berikutnya.
    """
    results: List[ParsedSMS] = []
    current_sms: Optional[dict] = None

    header_pattern = re.compile(
        r'^\+CMGL:\s*(\d+),"([^"]*)","([^"]*)"(?:,[^,]*)?,"([^"]*)"',
        re.IGNORECASE
    )

    for line in lines:
        match = header_pattern.match(line.strip())
        if match:
            # Simpan pesan sebelumnya jika ada
            if current_sms:
                full_body = "\n".join(current_sms["body_lines"])
                results.append(ParsedSMS(
                    index=current_sms["index"],
                    status_str=current_sms["status"],
                    sender_raw=current_sms["sender"],
                    sender_normalized=normalize_sender(current_sms["sender"]),
                    smsc_timestamp=parse_smsc_timestamp(current_sms["timestamp"]),
                    body=full_body,
                    body_hash=compute_body_hash(full_body)
                ))

            idx = int(match.group(1))
            status = match.group(2)
            sender = match.group(3)
            timestamp = match.group(4)
            current_sms = {
                "index": idx,
                "status": status,
                "sender": sender,
                "timestamp": timestamp,
                "body_lines": []
            }
        else:
            if current_sms is not None and line.strip() != "OK":
                current_sms["body_lines"].append(line)

    if current_sms:
        full_body = "\n".join(current_sms["body_lines"])
        results.append(ParsedSMS(
            index=current_sms["index"],
            status_str=current_sms["status"],
            sender_raw=current_sms["sender"],
            sender_normalized=normalize_sender(current_sms["sender"]),
            smsc_timestamp=parse_smsc_timestamp(current_sms["timestamp"]),
            body=full_body,
            body_hash=compute_body_hash(full_body)
        ))

    return results

