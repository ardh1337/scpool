"""
Modul Keamanan & Kriptografi Otentikasi
Menggunakan standar library Python murni (hashlib, hmac, secrets) tanpa dependensi eksternal.
"""

import hashlib
import hmac
import secrets


def hash_password(password: str) -> str:
    """Melakukan hashing password menggunakan PBKDF2-HMAC-SHA256 dengan salt acak 16 byte."""
    salt = secrets.token_bytes(16)
    key = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 100000)
    return f"pbkdf2${salt.hex()}${key.hex()}"


def verify_password(plain_password: str, stored_password: str) -> bool:
    """Memverifikasi kecocokan password terhadap hash PBKDF2 atau fallback teks biasa legacy."""
    if not stored_password:
        return False

    if stored_password.startswith("pbkdf2$"):
        parts = stored_password.split("$")
        if len(parts) != 3:
            return False
        try:
            salt = bytes.fromhex(parts[1])
            expected_key = bytes.fromhex(parts[2])
            actual_key = hashlib.pbkdf2_hmac("sha256", plain_password.encode("utf-8"), salt, 100000)
            return hmac.compare_digest(expected_key, actual_key)
        except Exception:
            return False

    # Fallback legacy: teks biasa (misal admin123 sebelum migrasi)
    return hmac.compare_digest(plain_password, stored_password)


def generate_token(nbytes: int = 32) -> str:
    """Menghasilkan session token acak yang aman."""
    return secrets.token_urlsafe(nbytes)


def generate_agent_token() -> str:
    """Menghasilkan token otentikasi unik untuk koneksi agent remote modem pool."""
    return "tok_live_" + secrets.token_hex(16)


def is_https_request(request) -> bool:
    """
    Mendeteksi apakah request berjalan di atas HTTPS (langsung atau via Cloudflare / Reverse Proxy).
    Mencegah cookie session terkirim tanpa enkripsi.
    """
    if getattr(request, "url", None) and request.url.scheme == "https":
        return True
    headers = getattr(request, "headers", {})
    if headers.get("x-forwarded-proto", "").lower() == "https":
        return True
    cf_visitor = headers.get("cf-visitor", "")
    if '"scheme":"https"' in cf_visitor.lower():
        return True
    return False


def get_client_ip(request) -> str:
    """Mendapatkan alamat IP asli klien dengan memeriksa header proxy terpercaya."""
    headers = getattr(request, "headers", {})
    cf_ip = headers.get("cf-connecting-ip")
    if cf_ip:
        return cf_ip.strip()
    x_real_ip = headers.get("x-real-ip")
    if x_real_ip:
        return x_real_ip.strip()
    x_forwarded_for = headers.get("x-forwarded-for")
    if x_forwarded_for:
        return x_forwarded_for.split(",")[0].strip()
    client = getattr(request, "client", None)
    return client.host if client else "127.0.0.1"


import time
from collections import defaultdict
from threading import Lock


class SimpleRateLimiter:
    """
    Rate limiter berbasis sliding window murni dalam memori (thread-safe, tanpa dependensi eksternal).
    Digunakan untuk memitigasi serangan brute-force dan registrasi massal.
    """
    def __init__(self):
        self._records = defaultdict(list)
        self._lock = Lock()

    def is_allowed(self, key: str, max_attempts: int, window_seconds: int) -> bool:
        now = time.time()
        with self._lock:
            cutoff = now - window_seconds
            valid_times = [t for t in self._records[key] if t > cutoff]
            if len(valid_times) >= max_attempts:
                self._records[key] = valid_times
                return False
            valid_times.append(now)
            self._records[key] = valid_times
            return True

    def reset(self, key: str):
        with self._lock:
            if key in self._records:
                del self._records[key]


auth_rate_limiter = SimpleRateLimiter()


