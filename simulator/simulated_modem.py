"""
Engine Simulator Modem AT (Section 8)
Mengemulasikan perilaku lengkap modem pool Foxcom untuk pengujian tanpa perangkat fisik:
- SIM swap (cabut, pasang, ganti kartu, status berkedip)
- Pembacaan ICCID valid, invalid, atau gagal baca
- AT+CNUM (ada nomor, kosong, error)
- Penerimaan SMS tunggal dan multipart, notifikasi ganda
- Pesan bawaan kartu (PESAN BAWAAN)
- Gangguan: timeout, respon terputus, port drop, recovery
"""

import threading
import time
from datetime import datetime, timezone
from typing import Dict, List, Optional
from app.modem.transport import VirtualTransport


class SimulatedSMS:
    def __init__(
        self,
        index: int,
        sender: str,
        body: str,
        timestamp_str: str,
        is_read: bool = False,
        is_initial: bool = False
    ):
        self.index = index
        self.sender = sender
        self.body = body
        self.timestamp_str = timestamp_str
        self.is_read = is_read
        self.is_initial = is_initial  # Ditandai sebagai PESAN BAWAAN jika ada saat SIM dipasang


class SimulatedModem:
    def __init__(self, port_name: str = "VIRTUAL_COM1", transport: Optional[VirtualTransport] = None):
        self.port_name = port_name
        self.transport = transport or VirtualTransport(port_name=port_name)
        self.transport.on_write_callback = self._handle_incoming_raw

        # Status Internal Modem
        self.is_powered = True
        self.sim_inserted = False
        self.sim_iccid: Optional[str] = None
        self.sim_phone_number: Optional[str] = None
        self.sim_error: bool = False
        self.iccid_unreadable: bool = False
        self.cnum_supported: bool = True

        # Pengaturan SMS
        self.sms_mode = 1  # 1 = Teks, 0 = PDU
        self.messages: Dict[int, SimulatedSMS] = {}
        self._next_sms_index = 1

        # Simulasi Gangguan (Fault Injection)
        self.hang_mode = False          # Timeout (tidak membalas)
        self.truncated_mode = False     # Mengirim balasan tidak lengkap
        self.port_disconnected = False  # Port putus mendadak

        self._lock = threading.Lock()

    # ------------------ Skenario Kontrol SIM ------------------ #

    def insert_sim(
        self,
        iccid: str,
        phone_number: Optional[str] = None,
        initial_messages: Optional[List[dict]] = None
    ) -> None:
        """Memasang kartu SIM ke dalam slot."""
        with self._lock:
            self.sim_inserted = True
            self.sim_iccid = iccid
            self.sim_phone_number = phone_number
            self.sim_error = False
            self.iccid_unreadable = False
            self.messages.clear()
            self._next_sms_index = 1

            if initial_messages:
                for item in initial_messages:
                    sms = SimulatedSMS(
                        index=self._next_sms_index,
                        sender=item.get("sender", "OPERATOR"),
                        body=item.get("body", "Pesan Selamat Datang"),
                        timestamp_str=item.get("timestamp", "26/10/16,08:00:00+28"),
                        is_read=True,
                        is_initial=True
                    )
                    self.messages[self._next_sms_index] = sms
                    self._next_sms_index += 1

            # Emit URC notifikasi SIM terdeteksi
            if not self.port_disconnected and self.transport.is_open():
                self.transport.feed_rx(b"+CPIN: READY\r\n")
                self.transport.feed_rx(b"+QSIMSTAT: 1,1\r\n")

    def remove_sim(self) -> None:
        """Mencabut kartu SIM dari slot."""
        with self._lock:
            self.sim_inserted = False
            self.sim_iccid = None
            self.sim_phone_number = None
            self.messages.clear()

            # Emit URC SIM dicabut
            if not self.port_disconnected and self.transport.is_open():
                self.transport.feed_rx(b"+QSIMSTAT: 1,0\r\n")
                self.transport.feed_rx(b"+CPIN: NOT INSERTED\r\n")

    def receive_sms(
        self,
        sender: str,
        body: str,
        timestamp_str: Optional[str] = None,
        duplicate_notification: bool = False
    ) -> int:
        """Menerima SMS baru secara dinamis."""
        with self._lock:
            if not self.sim_inserted:
                raise RuntimeError("Tidak dapat menerima SMS: SIM tidak terpasang.")

            now_str = timestamp_str or datetime.now(timezone.utc).strftime("%y/%m/%d,%H:%M:%S+00")
            idx = self._next_sms_index
            self._next_sms_index += 1

            sms = SimulatedSMS(
                index=idx,
                sender=sender,
                body=body,
                timestamp_str=now_str,
                is_read=False,
                is_initial=False
            )
            self.messages[idx] = sms

            # Kirim URC notifikasi pesan masuk
            urc = f"+CMTI: \"SM\",{idx}\r\n".encode("latin1")
            self.transport.feed_rx(urc)

            # Jika pengujian notifikasi ganda (duplicate notification)
            if duplicate_notification:
                self.transport.feed_rx(urc)

            return idx

    # ------------------ Pemrosesan Perintah AT ------------------ #

    def _handle_incoming_raw(self, data: bytes) -> None:
        """Callback saat klien menulis perintah ke virtual transport."""
        if self.hang_mode or self.port_disconnected:
            return  # Tidak merespons (simulasi timeout atau port putus)

        line = data.decode("latin1", errors="replace").strip()
        if not line:
            return

        cmd = line.upper()

        if self.truncated_mode:
            # Kirim balasan terpotong lalu berhenti
            self.transport.feed_rx(b"O")
            return

        with self._lock:
            self._dispatch_command(cmd, line)

    def _dispatch_command(self, cmd: str, raw_line: str) -> None:
        if cmd == "AT":
            self.transport.feed_rx(b"OK\r\n")

        elif cmd in ("ATI", "AT+CGMI", "AT+CGMM", "AT+CGMR", "AT+CGSN"):
            self.transport.feed_rx(b"Manufacturer: Foxcom\r\nModel: SIM-SIMULATOR-32\r\nRevision: 1.0\r\nOK\r\n")

        elif cmd == "AT+CPIN?":
            if not self.sim_inserted:
                self.transport.feed_rx(b"+CME ERROR: 10\r\n")  # SIM not inserted
            elif self.sim_error:
                self.transport.feed_rx(b"+CME ERROR: 13\r\n")  # SIM failure
            else:
                self.transport.feed_rx(b"+CPIN: READY\r\n\r\nOK\r\n")

        elif cmd in ("AT+QCCID", "AT+CCID"):
            if not self.sim_inserted:
                self.transport.feed_rx(b"+CME ERROR: 10\r\n")
            elif self.iccid_unreadable:
                self.transport.feed_rx(b"ERROR\r\n")
            else:
                self.transport.feed_rx(f"+QCCID: {self.sim_iccid}\r\n\r\nOK\r\n".encode("latin1"))

        elif cmd == "AT+CNUM":
            if not self.sim_inserted:
                self.transport.feed_rx(b"+CME ERROR: 10\r\n")
            elif not self.cnum_supported:
                self.transport.feed_rx(b"ERROR\r\n")
            elif self.sim_phone_number:
                self.transport.feed_rx(
                    f"+CNUM: \"Voice\",\"{self.sim_phone_number}\",145\r\n\r\nOK\r\n".encode("latin1")
                )
            else:
                # Kartu SIM tidak menyimpan nomor (kasus umum)
                self.transport.feed_rx(b"OK\r\n")

        elif cmd == "AT+CSQ":
            self.transport.feed_rx(b"+CSQ: 24,99\r\n\r\nOK\r\n")

        elif cmd == "AT+CREG?":
            self.transport.feed_rx(b"+CREG: 0,1\r\n\r\nOK\r\n")

        elif cmd == "AT+COPS?":
            self.transport.feed_rx(b"+COPS: 0,0,\"TELKOMSEL\"\r\n\r\nOK\r\n")

        elif cmd == "AT+CMGF?":
            self.transport.feed_rx(f"+CMGF: {self.sms_mode}\r\n\r\nOK\r\n".encode("latin1"))

        elif cmd.startswith("AT+CMGF="):
            mode = cmd.split("=")[1].strip()
            self.sms_mode = int(mode) if mode.isdigit() else 1
            self.transport.feed_rx(b"OK\r\n")

        elif cmd.startswith("AT+CPMS?"):
            count = len(self.messages)
            self.transport.feed_rx(f"+CPMS: \"SM\",{count},50,\"SM\",{count},50,\"SM\",{count},50\r\n\r\nOK\r\n".encode("latin1"))

        elif cmd.startswith("AT+CNMI="):
            self.transport.feed_rx(b"OK\r\n")

        elif cmd.startswith("AT+CMGL"):
            # Daftar semua pesan SMS
            for sms in self.messages.values():
                status_str = "REC READ" if sms.is_read else "REC UNREAD"
                header = f"+CMGL: {sms.index},\"{status_str}\",\"{sms.sender}\",,\"{sms.timestamp_str}\"\r\n"
                self.transport.feed_rx(header.encode("latin1"))
                self.transport.feed_rx(f"{sms.body}\r\n".encode("latin1"))
                sms.is_read = True
            self.transport.feed_rx(b"OK\r\n")

        elif cmd.startswith("AT+CMGR="):
            idx_str = cmd.split("=")[1].strip()
            if idx_str.isdigit() and int(idx_str) in self.messages:
                sms = self.messages[int(idx_str)]
                status_str = "REC READ" if sms.is_read else "REC UNREAD"
                header = f"+CMGR: \"{status_str}\",\"{sms.sender}\",,\"{sms.timestamp_str}\"\r\n"
                self.transport.feed_rx(header.encode("latin1"))
                self.transport.feed_rx(f"{sms.body}\r\n\r\nOK\r\n".encode("latin1"))
                sms.is_read = True
            else:
                self.transport.feed_rx(b"+CMS ERROR: 321\r\n")

        elif cmd.startswith("AT+CMGD="):
            idx_str = cmd.split("=")[1].split(",")[0].strip()
            if idx_str.isdigit() and int(idx_str) in self.messages:
                del self.messages[int(idx_str)]
                self.transport.feed_rx(b"OK\r\n")
            else:
                self.transport.feed_rx(b"+CMS ERROR: 321\r\n")

        else:
            # Perintah tidak dikenal
            self.transport.feed_rx(b"ERROR\r\n")

