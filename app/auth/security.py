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
