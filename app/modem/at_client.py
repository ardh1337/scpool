"""
Klien AT (AT Command Client)
Menegakkan:
- I-5: Satu perintah AT per port pada satu waktu. URC dipisahkan ke handler tersendiri.
- I-13: Perintah wajib tervalidasi terhadap allowlist resmi sebelum dikirim ke port.
"""

import threading
import time
from dataclasses import dataclass, field
from typing import List, Optional, Callable
import re

from app.modem.transport import BaseTransport
from app.hardware.at_allowlist import is_at_command_allowed


class ATError(Exception):
    """Kelas dasar error AT."""
    pass


class DisallowedATCommandError(ATError):
    """Dilempar saat perintah AT tidak ada dalam daftar izin (I-13)."""
    pass


class ATTimeoutError(ATError):
    """Dilempar saat modem tidak membalas dalam batas waktu yang ditentukan."""
    pass


@dataclass
class ATResponse:
    command: str
    success: bool
    lines: List[str] = field(default_factory=list)
    raw_response: str = ""
    error_message: Optional[str] = None
    is_timeout: bool = False


# Pola URC (Unsolicited Result Code) yang sering muncul dari modem GSM/LTE
URC_PATTERNS = [
    re.compile(r"^\+CMTI:\s*.*", re.IGNORECASE),       # Notifikasi SMS masuk
    re.compile(r"^\+CPIN:\s*.*", re.IGNORECASE),       # Status PIN SIM
    re.compile(r"^\+QSIMSTAT:\s*.*", re.IGNORECASE),   # Deteksi SIM Quectel
    re.compile(r"^\+CREG:\s*.*", re.IGNORECASE),       # Status registrasi jaringan
    re.compile(r"^\+CUSD:\s*.*", re.IGNORECASE),       # Balasan pesan USSD jaringan
    re.compile(r"^\^MODE:\s*.*", re.IGNORECASE),       # Indikator mode
    re.compile(r"^RING$", re.IGNORECASE),              # Panggilan masuk
    re.compile(r"^NO CARRIER$", re.IGNORECASE),
]


def is_urc_line(line: str, current_command: Optional[str] = None) -> bool:
    """
    Mendeteksi apakah baris keluaran serial merupakan notifikasi tak terminta (URC).
    Jika baris tersebut adalah respons langsung atas query aktif (misal +CPIN: untuk AT+CPIN?),
    maka baris tersebut bukan URC melainkan hasil balasan perintah.
    """
    stripped = line.strip()
    if not stripped:
        return False

    if current_command:
        cmd_clean = current_command.strip().upper()
        if cmd_clean.startswith("AT"):
            cmd_name = re.split(r"[=?\s]", cmd_clean[2:])[0]
            if cmd_name and stripped.upper().startswith(cmd_name + ":"):
                return False

    for pat in URC_PATTERNS:
        if pat.match(stripped):
            return True
    return False


class ATClient:
    """
    Klien komunikasi AT thread-safe untuk satu port serial.
    Menjamin eksekusi sekuensial satu perintah per port pada satu waktu (I-5).
    """

    def __init__(
        self,
        transport: BaseTransport,
        default_timeout: float = 3.0,
        urc_callback: Optional[Callable[[str], None]] = None,
        allow_destructive: bool = False
    ):
        self.transport = transport
        self.default_timeout = default_timeout
        self.urc_callback = urc_callback
        self.allow_destructive = allow_destructive
        self._lock = threading.Lock()  # Menjamin satu perintah per port pada satu waktu (I-5)
        self.urc_history: List[str] = []

    def open(self) -> None:
        self.transport.open()

    def close(self) -> None:
        self.transport.close()

    def is_open(self) -> bool:
        return self.transport.is_open()

    def register_urc_callback(self, callback: Callable[[str], None]) -> None:
        self.urc_callback = callback

    def wait_for_urc(self, prefix: str, timeout: float) -> Optional[str]:
        """
        Baca baris dari port sampai URC berawalan `prefix` (mis. "+CUSD:") tiba atau waktu habis.

        Dibutuhkan karena balasan USSD lazimnya datang SETELAH "OK" dari AT+CUSD; send_command() sudah berhenti
        membaca pada "OK" sehingga balasan itu tidak pernah terbaca. URC bertanda kutip yang mencakup beberapa
        baris digabung sampai kutipnya tertutup.
        """
        prefix_upper = prefix.upper()
        deadline = time.time() + max(timeout, 0.0)
        collected: Optional[str] = None
        with self._lock:
            if not self.transport.is_open():
                self.transport.open()
            while time.time() < deadline:
                raw = self.transport.readline(timeout=max(deadline - time.time(), 0.05))
                if not raw:
                    continue
                line = raw.decode("latin1", errors="replace").strip("\r\n")
                stripped = line.strip()
                if not stripped:
                    continue
                if collected is None:
                    if stripped.upper().startswith(prefix_upper):
                        collected = stripped
                    else:
                        self.urc_history.append(stripped)
                        continue
                else:
                    collected += "\n" + line
                if collected.count('"') % 2 == 0:
                    return collected
        return collected

    def send_command(
        self,
        command: str,
        timeout: Optional[float] = None,
        expected_prefix: Optional[str] = None
    ) -> ATResponse:
        """
        Mengirimkan perintah AT dan menunggu balasan final (OK, ERROR, +CME ERROR).
        Memfilter baris URC sehingga tidak tercampur dengan hasil balasan perintah (I-5).
        """
        cleaned_cmd = command.strip()

        if any(ch in cleaned_cmd for ch in "\r\n\x00\x1a\x1b"):
            raise DisallowedATCommandError("Perintah AT tidak boleh berisi karakter kontrol (CR/LF/NUL/Ctrl-Z).")

        # Invarian I-13: Periksa allowlist perintah AT
        if not is_at_command_allowed(cleaned_cmd, allow_delete=self.allow_destructive):
            raise DisallowedATCommandError(
                f"Perintah AT '{cleaned_cmd}' ditolak karena tidak ada dalam daftar izin (I-13)."
            )

        cmd_timeout = timeout if timeout is not None else self.default_timeout

        with self._lock:  # Invarian I-5: Penguncian eksklusif per port
            if not self.transport.is_open():
                self.transport.open()

            # Bersihkan buffer sebelum mengirim perintah baru
            self.transport.flush()

            full_cmd = f"{cleaned_cmd}\r\n".encode("latin1")
            self.transport.write(full_cmd)

            start_time = time.time()
            response_lines: List[str] = []
            raw_lines: List[str] = []
            success = False
            error_msg = None
            is_timeout = False

            while True:
                elapsed = time.time() - start_time
                remaining = cmd_timeout - elapsed
                if remaining <= 0:
                    is_timeout = True
                    error_msg = f"Timeout ({cmd_timeout}s) menunggu balasan untuk '{cleaned_cmd}'"
                    break

                line_bytes = self.transport.readline(timeout=remaining)
                if not line_bytes:
                    # Cek lagi batas waktu
                    if (time.time() - start_time) >= cmd_timeout:
                        is_timeout = True
                        error_msg = f"Timeout ({cmd_timeout}s) menunggu balasan untuk '{cleaned_cmd}'"
                        break
                    continue

                line_str = line_bytes.decode("latin1", errors="replace").strip("\r\n")
                raw_lines.append(line_str)
                trimmed = line_str.strip()

                if not trimmed:
                    continue

                # Echo perintah dari modem (bisa diabaikan jika persis sama)
                if trimmed.upper() == cleaned_cmd.upper():
                    continue

                # Invarian I-5: Pisahkan URC ke alur terpisah
                if is_urc_line(trimmed, current_command=cleaned_cmd):
                    self.urc_history.append(trimmed)
                    if self.urc_callback:
                        try:
                            self.urc_callback(trimmed)
                        except Exception:
                            pass
                    continue

                # Indikator akhir balasan sukses
                if trimmed == "OK":
                    success = True
                    break

                # Indikator akhir balasan error
                if trimmed == "ERROR" or trimmed.startswith("+CME ERROR:") or trimmed.startswith("+CMS ERROR:"):
                    success = False
                    error_msg = trimmed
                    break

                response_lines.append(trimmed)

            return ATResponse(
                command=cleaned_cmd,
                success=success,
                lines=response_lines,
                raw_response="\n".join(raw_lines),
                error_message=error_msg,
                is_timeout=is_timeout
            )
