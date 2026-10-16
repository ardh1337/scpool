"""
Lapisan Abstraksi Transport Komunikasi Serial
Memungkinkan pergantian tanpa batas antara port serial nyata (PySerial)
dan modem simulasi in-memory untuk pengujian tanpa perangkat fisik (Section 7 & 8).
"""

from abc import ABC, abstractmethod
import queue
import time
import threading
from typing import Optional


class BaseTransport(ABC):
    """Antarmuka dasar komunikasi serial dua arah."""

    @abstractmethod
    def open(self) -> None:
        pass

    @abstractmethod
    def close(self) -> None:
        pass

    @abstractmethod
    def is_open(self) -> bool:
        pass

    @abstractmethod
    def write(self, data: bytes) -> int:
        pass

    @abstractmethod
    def readline(self, timeout: Optional[float] = None) -> bytes:
        pass

    @abstractmethod
    def flush(self) -> None:
        pass


class SerialTransport(BaseTransport):
    """Implementasi transport port serial nyata menggunakan PySerial."""

    def __init__(self, port: str, baudrate: int = 115200, timeout: float = 1.0):
        self.port_name = port
        self.baudrate = baudrate
        self.default_timeout = timeout
        self._serial = None

    def open(self) -> None:
        import serial
        if self._serial is None or not self._serial.is_open:
            self._serial = serial.Serial(
                port=self.port_name,
                baudrate=self.baudrate,
                timeout=self.default_timeout,
                write_timeout=self.default_timeout
            )

    def close(self) -> None:
        if self._serial and self._serial.is_open:
            self._serial.close()
        self._serial = None

    def is_open(self) -> bool:
        return bool(self._serial and self._serial.is_open)

    def write(self, data: bytes) -> int:
        if not self.is_open():
            raise IOError(f"Port {self.port_name} belum terbuka.")
        return self._serial.write(data)

    def readline(self, timeout: Optional[float] = None) -> bytes:
        if not self.is_open():
            raise IOError(f"Port {self.port_name} belum terbuka.")
        old_timeout = self._serial.timeout
        if timeout is not None:
            self._serial.timeout = timeout
        try:
            return self._serial.readline()
        finally:
            if timeout is not None:
                self._serial.timeout = old_timeout

    def flush(self) -> None:
        if self.is_open():
            self._serial.flush()


class VirtualTransport(BaseTransport):
    """
    Transport in-memory untuk simulator modem.
    Menghubungkan klien AT langsung ke engine simulator modem dengan stream byte akurat.
    """

    def __init__(self, port_name: str = "VIRTUAL_COM1", default_timeout: float = 1.0):
        self.port_name = port_name
        self.default_timeout = default_timeout
        self._is_open = False
        self._buffer = bytearray()
        self._cond = threading.Condition()
        self.on_write_callback = None

    def open(self) -> None:
        self._is_open = True

    def close(self) -> None:
        with self._cond:
            self._is_open = False
            self._cond.notify_all()

    def is_open(self) -> bool:
        return self._is_open

    def write(self, data: bytes) -> int:
        if not self._is_open:
            raise IOError(f"Virtual port {self.port_name} belum terbuka.")
        if self.on_write_callback:
            self.on_write_callback(data)
        return len(data)

    def feed_rx(self, data: bytes) -> None:
        """Dipanggil oleh engine simulator untuk mengirimkan byte ke klien."""
        with self._cond:
            self._buffer.extend(data)
            self._cond.notify_all()

    def readline(self, timeout: Optional[float] = None) -> bytes:
        if not self._is_open:
            raise IOError(f"Virtual port {self.port_name} belum terbuka.")
        t = timeout if timeout is not None else self.default_timeout
        start_time = time.time()

        with self._cond:
            while self._is_open:
                newline_pos = self._buffer.find(b"\n")
                if newline_pos != -1:
                    line = bytes(self._buffer[: newline_pos + 1])
                    del self._buffer[: newline_pos + 1]
                    return line

                elapsed = time.time() - start_time
                remaining = t - elapsed
                if remaining <= 0:
                    return b""

                self._cond.wait(timeout=remaining)

            return b""

    def flush(self) -> None:
        with self._cond:
            self._buffer.clear()
