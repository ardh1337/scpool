"""
Primitif keamanan: hash password, token, deteksi HTTPS/IP klien, dan rate limiter.
Hanya memakai pustaka standar Python.
"""

from __future__ import annotations

import hashlib
import hmac
import ipaddress
import secrets
import time
from collections import defaultdict
from threading import Lock
from typing import Optional

from app.core.config import settings

PBKDF2_ITERATIONS = 260_000
_LEGACY_ITERATIONS = 100_000
_HASH_PREFIX = "pbkdf2_sha256"


def hash_password(password: str) -> str:
    """PBKDF2-HMAC-SHA256 dengan salt acak; jumlah iterasi ikut tersimpan agar bisa dinaikkan nanti."""
    salt = secrets.token_bytes(16)
    key = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, PBKDF2_ITERATIONS)
    return f"{_HASH_PREFIX}${PBKDF2_ITERATIONS}${salt.hex()}${key.hex()}"


def _parse_hash(stored: str) -> Optional[tuple]:
    parts = stored.split("$")
    try:
        if parts[0] == _HASH_PREFIX and len(parts) == 4:
            return int(parts[1]), bytes.fromhex(parts[2]), bytes.fromhex(parts[3])
        if parts[0] == "pbkdf2" and len(parts) == 3:  # format lama: tanpa jumlah iterasi
            return _LEGACY_ITERATIONS, bytes.fromhex(parts[1]), bytes.fromhex(parts[2])
    except ValueError:
        return None
    return None


def verify_password(plain_password: str, stored_password: str) -> bool:
    """Verifikasi hash PBKDF2 (format baru & lama) atau teks biasa warisan (di-upgrade otomatis saat login)."""
    if not stored_password:
        return False
    if stored_password.startswith(("pbkdf2$", _HASH_PREFIX + "$")):
        parsed = _parse_hash(stored_password)
        if not parsed:
            return False
        iterations, salt, expected = parsed
        actual = hashlib.pbkdf2_hmac("sha256", plain_password.encode("utf-8"), salt, iterations)
        return hmac.compare_digest(expected, actual)
    return hmac.compare_digest(plain_password.encode("utf-8"), stored_password.encode("utf-8"))


def password_needs_rehash(stored_password: str) -> bool:
    if not stored_password.startswith(_HASH_PREFIX + "$"):
        return True
    parsed = _parse_hash(stored_password)
    return not parsed or parsed[0] < PBKDF2_ITERATIONS


# Hash tiruan untuk menyamakan waktu respons saat username tidak ada (cegah enumerasi user).
_DUMMY_HASH = hash_password(secrets.token_hex(8))


def burn_password_check(plain_password: str) -> None:
    verify_password(plain_password, _DUMMY_HASH)


_COMMON_PASSWORDS = {
    "password", "password1", "password123", "passw0rd", "12345678", "123456789", "1234567890", "qwerty123",
    "qwertyuiop", "11111111", "00000000", "abc12345", "admin123", "admin1234", "administrator", "iloveyou",
    "letmein123", "welcome123", "indonesia", "indonesia123", "bismillah", "sayangku", "rahasia123", "scpool123",
}


def password_problem(password: str, username: str = "") -> Optional[str]:
    """Alasan password ditolak (kata sandi lemah), atau None bila layak dipakai."""
    pw = password.strip()
    if len(pw) < settings.password_min_length:
        return f"Password minimal {settings.password_min_length} karakter."
    low = pw.lower()
    if low in _COMMON_PASSWORDS or pw.isdigit() or len(set(low)) <= 2:
        return "Password terlalu mudah ditebak. Gunakan kombinasi huruf, angka, atau kata yang tidak umum."
    if username and len(username) >= 3 and username.lower() in low:
        return "Password tidak boleh memuat username."
    return None


def hash_session_token(token: str) -> str:
    """Hanya hash SHA-256 token sesi yang disimpan di DB, jadi kebocoran DB tidak bisa dipakai membajak sesi."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def generate_token(nbytes: int = 32) -> str:
    return secrets.token_urlsafe(nbytes)


def generate_agent_token() -> str:
    return "scpool_" + secrets.token_hex(16)


def generate_password(nbytes: int = 12) -> str:
    return secrets.token_urlsafe(nbytes)


def is_https_request(request) -> bool:
    """HTTPS langsung atau lewat reverse proxy/Cloudflare. Mencegah cookie sesi terkirim tanpa enkripsi."""
    if getattr(request, "url", None) and request.url.scheme == "https":
        return True
    headers = getattr(request, "headers", {})
    if headers.get("x-forwarded-proto", "").lower() == "https":
        return True
    return '"scheme":"https"' in headers.get("cf-visitor", "").lower()


def _is_trusted_proxy(host: Optional[str]) -> bool:
    if not host:
        return False
    if host in {"testclient", "testserver"}:
        return True
    try:
        addr = ipaddress.ip_address(host)
    except ValueError:
        return False
    for entry in settings.trusted_proxies:
        try:
            if "/" in entry:
                if addr in ipaddress.ip_network(entry, strict=False):
                    return True
            elif addr == ipaddress.ip_address(entry):
                return True
        except ValueError:
            continue
    return False


def get_client_ip(request) -> str:
    """
    IP klien sebenarnya. Header proxy (CF-Connecting-IP, X-Real-IP, X-Forwarded-For) hanya dipercaya bila koneksi
    datang dari proxy tepercaya (default: loopback tempat cloudflared berjalan). Tanpa pemeriksaan ini penyerang
    bisa memalsukan header untuk menghindari rate limit.
    """
    client = getattr(request, "client", None)
    peer = client.host if client else "127.0.0.1"
    if not _is_trusted_proxy(peer):
        return peer
    headers = getattr(request, "headers", {})
    for name in ("cf-connecting-ip", "x-real-ip"):
        value = headers.get(name)
        if value:
            return value.strip()
    forwarded = headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return peer


class SimpleRateLimiter:
    """Sliding window dalam memori (thread-safe) untuk brute-force login dan registrasi massal."""

    MAX_KEYS = 10_000

    def __init__(self):
        self._records = defaultdict(list)
        self._lock = Lock()

    def is_allowed(self, key: str, max_attempts: int, window_seconds: int) -> bool:
        now = time.time()
        with self._lock:
            if len(self._records) > self.MAX_KEYS:
                self._records = defaultdict(list, {k: v for k, v in self._records.items() if v and v[-1] > now - 3600})
            valid = [t for t in self._records[key] if t > now - window_seconds]
            if len(valid) >= max_attempts:
                self._records[key] = valid
                return False
            valid.append(now)
            self._records[key] = valid
            return True

    def reset(self, key: str) -> None:
        with self._lock:
            self._records.pop(key, None)


auth_rate_limiter = SimpleRateLimiter()
