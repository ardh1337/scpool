"""
SCPool Agent - jembatan antara modem pool di PC ini dan server SCPool.

Yang dilakukan agent:
  1. Mendeteksi port serial dan melaporkannya ke server.
  2. Memantau tiap port: ada/tidaknya SIM, ICCID, nomor (AT+CNUM) -> dilaporkan hanya saat berubah.
  3. Membaca SMS dari kartu SIM dan mengirimkannya ke server.
  4. Menjalankan perintah dari server: dial USSD dan perintah AT yang diizinkan (allowlist di bawah).

Agent tidak pernah menghapus SMS dari kartu dan menolak perintah AT di luar allowlist.
Berkas ini otomatis diisi alamat server & token saat diunduh dari dashboard.
"""

import argparse
import asyncio
import hashlib
import json
import os
import re
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


def _require(module: str, package: str):
    try:
        return __import__(module)
    except ImportError:
        print(f"[*] Memasang pustaka '{package}' (sekali saja)...")
        try:
            subprocess.check_call([sys.executable, "-m", "pip", "install", "--quiet", package])
            return __import__(module)
        except Exception as exc:
            print(f"[ERROR] Gagal memasang '{package}': {exc}\n        Jalankan manual: pip install {package}")
            sys.exit(1)


serial = _require("serial", "pyserial")
import serial.tools.list_ports  # noqa: E402
websockets = _require("websockets", "websockets")

# --- Diisi otomatis saat diunduh dari dashboard (jangan ubah format dua baris ini) ---
DEFAULT_SERVER = ""
DEFAULT_TOKEN = ""

VERSION = "2.1.0"
BAUDRATE = 115200
SCAN_INTERVAL = 8.0          # detik antar siklus pemindaian semua port
HEARTBEAT_INTERVAL = 60.0    # kirim ulang status port walau tidak berubah
SMS_READ_INTERVAL = 20.0     # detik antar pembacaan SMS per port READY
WATCH_INTERVAL = 3.0         # detik antar pembacaan SMS untuk port yang sedang menunggu OTP
MAX_WATCH_SECONDS = 1800.0
SMS_RESEND_INTERVAL = 300.0  # kirim ulang seluruh isi kartu walau tidak berubah (server melakukan dedup)
MAX_PARALLEL_PORTS = 8
PORT_WAIT = 6.0              # detik maksimal permintaan server menunggu port dipakai pemindai
MAX_WORKER_THREADS = 80      # 64 port + pemindai; tiap USSD memegang satu thread selama dial
CONFIG_FILE = Path(__file__).resolve().with_name("agent_config.json")

# Allowlist perintah AT (cerminan config server). Menghapus SMS (AT+CMGD), reset (ATZ/AT&F), dan
# perintah lain di luar daftar ini ditolak di sisi agent walaupun server memintanya.
_SAFE_EXACT = {
    "AT", "ATI", "AT+CGMI", "AT+CGMM", "AT+CGMR", "AT+CGSN", "AT+CPIN?", "AT+QCCID", "AT+CCID", "AT+CIMI",
    "AT+QSIMSTAT?", "AT+QSIMDET?", "AT+CNUM", "AT+COPS?", "AT+CREG?", "AT+CSQ", "AT+CMGF?", "AT+CPMS?",
    "AT+CNMI?", "AT+CUSD?", "AT+CUSD=?", "AT+CSCS?", "AT+CSCS=?", "AT+CMEE?",
}
_SAFE_PATTERNS = [re.compile(p, re.IGNORECASE) for p in (
    r"^AT\+CMGL(=(\d|\"[A-Z ]{1,20}\"))?\Z", r"^AT\+CMGR=\d+\Z",
    r'^AT\+CUSD=[012](,"[0-9*#+]{1,32}"(,\d{1,3})?)?\Z',
    r"^AT\+CMGF=[01]\Z", r'^AT\+CPMS="[A-Z]+","[A-Z]+","[A-Z]+"\Z', r"^AT\+CNMI=\d+,\d+,\d+,\d+,\d+\Z",
    r"^AT\+QSIMSTAT=[01]\Z", r"^AT\+QSIMDET=[01],[01]\Z", r'^AT\+CSCS="[A-Z]+"\Z', r"^AT\+CMEE=[012]\Z",
)]
USSD_CODE = re.compile(r"^[0-9*#+]{2,32}\Z")


def is_command_allowed(command: str) -> bool:
    cleaned = (command or "").strip().upper()
    if not cleaned or any(ch in cleaned for ch in "\r\n\x00\x1a\x1b"):
        return False
    if cleaned in _SAFE_EXACT:
        return True
    return any(p.match(cleaned) for p in _SAFE_PATTERNS)


# ----------------------------------------------------------------------------- port serial
_port_locks: Dict[str, threading.Lock] = {}
_port_locks_guard = threading.Lock()


def port_lock(port: str) -> threading.Lock:
    with _port_locks_guard:
        return _port_locks.setdefault(port.upper(), threading.Lock())


def natural_sort_key(s: str):
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", s)]


def usb_group_key(location: Optional[str]) -> str:
    """
    Kelompok fisik sebuah port: kabel USB yang masuk ke komputer (mis. "1-4" dari lokasi "1-4.7.2:x.0").
    Semua hub/chip di balik kabel yang sama dianggap satu modem pool; dua modem pool di kabel berbeda terpisah.
    Dua pool lewat satu hub eksternal tergabung di sini dan bisa dipisah dari panel.
    """
    root = (location or "").split(":")[0].split(".")[0].strip()
    return f"usb:{root}" if root else "default"


def windows_port_locations() -> Dict[str, str]:
    """
    Lokasi USB tiap port COM dalam format pyserial ("1-10.2.3:x.0").
    pyserial 3.5 membaca SPDRP_LOCATION_PATHS dengan buffer 250 karakter; pada hub bertingkat (Foxcom 32 port:
    4 tingkat hub + chip Exar 4 port) data itu lebih panjang sehingga sebagian port kembali tanpa lokasi dan
    terlempar ke kelompok "default", membuat satu modem pool terbaca dua.
    """
    if sys.platform != "win32":
        return {}
    import ctypes
    from ctypes import wintypes
    from serial.tools import list_ports_windows as w

    out: Dict[str, str] = {}
    guids = (w.GUID * 8)()
    count = wintypes.DWORD()
    if not w.SetupDiClassGuidsFromName("Ports", guids, ctypes.sizeof(guids), ctypes.byref(count)):
        return out
    for idx in range(count.value):
        h = w.SetupDiGetClassDevs(ctypes.byref(guids[idx]), None, None, w.DIGCF_PRESENT)
        dev = w.SP_DEVINFO_DATA()
        dev.cbSize = ctypes.sizeof(dev)
        i = 0
        while w.SetupDiEnumDeviceInfo(h, i, ctypes.byref(dev)):
            i += 1
            key = w.SetupDiOpenDevRegKey(h, ctypes.byref(dev), w.DICS_FLAG_GLOBAL, 0, w.DIREG_DEV, w.KEY_READ)
            name = ctypes.create_unicode_buffer(250)
            size = wintypes.ULONG(ctypes.sizeof(name))
            w.RegQueryValueEx(key, "PortName", None, None, ctypes.byref(name), ctypes.byref(size))
            w.RegCloseKey(key)
            paths = ctypes.create_unicode_buffer(4096)
            if not w.SetupDiGetDeviceRegistryProperty(h, ctypes.byref(dev), w.SPDRP_LOCATION_PATHS, None,
                                                      ctypes.byref(paths), ctypes.sizeof(paths) - 2, None):
                continue
            path = paths.value  # entri pertama multi-sz: PCIROOT(..)#..#USBROOT(n)#USB(p)#USB(p)..#USBMI(i)
            hops = re.findall(r"USBROOT\((\d+)\)|#USB\((\d+)\)", path)
            if not hops or not hops[0][0]:
                continue
            loc = str(int(hops[0][0]) + 1)
            for k, (_root, port) in enumerate(hops[1:]):
                loc += ("-" if k == 0 else ".") + port
            mi = re.search(r"#USBMI\((\d+)\)", path)
            out[name.value.upper()] = loc + (f":x.{int(mi.group(1))}" if mi else "")
        w.SetupDiDestroyDeviceInfoList(h)
    return out


def get_local_units(allowed: Optional[List[str]] = None) -> List[Tuple[str, List[str]]]:
    """Port serial USB yang terdeteksi, dikelompokkan per modem pool dan diurutkan menurut posisi fisiknya."""
    wanted = {a.upper() for a in allowed} if allowed else None
    groups: Dict[str, List[Tuple[list, str]]] = {}
    infos = list(serial.tools.list_ports.comports())
    fallback: Dict[str, str] = {}
    if any(info.location is None and info.vid is not None for info in infos):
        try:
            fallback = windows_port_locations()
        except Exception as e:
            print(f"[!] Gagal membaca lokasi USB dari Windows: {e}")
    for info in infos:
        if wanted is not None:
            if info.device.upper() not in wanted:
                continue
        elif info.vid is None:  # bukan perangkat USB (COM1 bawaan, Bluetooth): bukan modem pool
            continue
        location = info.location or fallback.get(info.device.upper())
        order = natural_sort_key(location or "") + natural_sort_key(info.device)
        groups.setdefault(usb_group_key(location), []).append((order, info.device))
    units = [(key, [dev for _order, dev in sorted(items)]) for key, items in groups.items()]
    return sorted(units, key=lambda unit: natural_sort_key(unit[0]))


def get_local_ports(allowed: Optional[List[str]] = None) -> List[str]:
    return [port for _key, ports in get_local_units(allowed) for port in ports]


def run_at(ser, command: str, timeout: float = 3.0) -> Tuple[bool, List[str], Optional[str]]:
    """Kirim satu perintah AT; baca sampai OK / ERROR / timeout. Mengembalikan (sukses, baris, error)."""
    ser.reset_input_buffer()
    ser.write(command.encode("latin-1") + b"\r")
    ser.flush()
    lines: List[str] = []
    deadline = time.time() + timeout
    while time.time() < deadline:
        raw = ser.readline()
        if not raw:
            continue
        line = raw.decode("latin-1", errors="replace").strip()
        if not line or line.upper() == command.strip().upper():
            continue
        if line == "OK":
            return True, lines, None
        if line == "ERROR" or line.startswith(("+CME ERROR", "+CMS ERROR")):
            return False, lines, line
        lines.append(line)
    return False, lines, f"Timeout ({timeout}s) menunggu balasan '{command}'"


def open_port(port: str, timeout: float = 1.0):
    return serial.Serial(port=port, baudrate=BAUDRATE, timeout=timeout, write_timeout=timeout)


def run_ussd(ser, code: str, timeout: float) -> Tuple[bool, str, Optional[str]]:
    """Dial USSD; balasan +CUSD biasanya datang SETELAH 'OK' sehingga pembacaan dilanjutkan sampai kutip tertutup."""
    run_at(ser, "AT+CUSD=2", 2.0)
    deadline = time.time() + timeout
    ser.reset_input_buffer()
    ser.write(f'AT+CUSD=1,"{code}"\r'.encode("latin-1"))
    ser.flush()
    collected = ""
    in_cusd = False
    while time.time() < deadline:
        raw = ser.readline()
        if not raw:
            continue
        line = raw.decode("latin-1", errors="replace").strip("\r\n")
        stripped = line.strip()
        if not stripped:
            continue
        if not in_cusd:
            if stripped.upper().startswith("+CUSD:"):
                in_cusd, collected = True, stripped
            elif stripped == "ERROR" or stripped.startswith(("+CME ERROR", "+CMS ERROR")):
                return False, "", stripped
            else:
                continue
        else:
            collected += "\n" + line
        if collected.count('"') % 2 == 0:
            return True, collected, None
    return (True, collected, None) if collected else (False, "", f"Timeout ({timeout}s) menunggu balasan USSD")


def _parse_cpin(ok: bool, lines: List[str], error: Optional[str]) -> str:
    if ok:
        for line in lines:
            if "+CPIN:" in line:
                return "READY" if "READY" in line.upper() else "SIM_ERROR"
        return "SIM_ERROR"
    err = (error or "").upper()
    return "EMPTY" if ("NOT INSERTED" in err or "CME ERROR: 10" in err) else "PORT_ERROR"


def _parse_iccid(lines: List[str]) -> Optional[str]:
    for line in lines:
        match = re.search(r"89\d{17,18}", re.sub(r"[\"\s]", "", line))
        if match:
            return match.group(0)
    return None


def _parse_cnum(lines: List[str]) -> Optional[str]:
    for line in lines:
        if "+CNUM:" in line:
            for value in re.findall(r'"([^"]*)"', line):
                digits = re.sub(r"[\s\-+]", "", value)
                if len(digits) >= 8 and digits.isdigit():
                    return value.strip()
    return None


def probe_port(port: str) -> Dict[str, Optional[str]]:
    """Baca status modem/SIM pada satu port. Tidak pernah melempar exception."""
    lock = port_lock(port)
    if not lock.acquire(blocking=False):
        return {"port": port, "cpin": "BUSY"}
    try:
        with open_port(port) as ser:
            if not run_at(ser, "AT", 1.0)[0]:
                return {"port": port, "cpin": "PORT_ERROR"}
            cpin = _parse_cpin(*run_at(ser, "AT+CPIN?", 1.5))
            result: Dict[str, Optional[str]] = {"port": port, "cpin": cpin}
            if cpin == "READY":
                ok, lines, _ = run_at(ser, "AT+QCCID", 1.5)
                iccid = _parse_iccid(lines) if ok else None
                if not iccid:
                    result["cpin"] = "SIM_ERROR"
                else:
                    result["iccid"] = iccid
                    ok, lines, _ = run_at(ser, "AT+CNUM", 2.0)
                    result["phone"] = _parse_cnum(lines) if ok else None
            return result
    except Exception as exc:
        return {"port": port, "cpin": "PORT_ERROR", "error": type(exc).__name__}
    finally:
        lock.release()


_CMGL_HEADER = re.compile(r'^\+CMGL:\s*(\d+),"([^"]*)","([^"]*)"(?:,[^,]*)?,"([^"]*)"', re.IGNORECASE)
_SMSC_TS = re.compile(r"(\d{2,4})[/.-](\d{2})[/.-](\d{2})[,\s]+(\d{2}):(\d{2}):(\d{2})([+-]\d{2})?")


def smsc_to_iso(ts: str) -> str:
    """'26/10/16,08:30:00+28' (offset dalam kelipatan 15 menit) -> ISO-8601 UTC."""
    match = _SMSC_TS.match(ts.strip())
    try:
        if match:
            yy, mm, dd, hh, mi, ss, tz = match.groups()
            year = int(yy) if len(yy) == 4 else 2000 + int(yy)
            base = datetime(year, int(mm), int(dd), int(hh), int(mi), int(ss))
            if tz:
                offset = timedelta(minutes=(1 if tz[0] == "+" else -1) * int(tz[1:]) * 15)
                base = base - offset
            return base.replace(tzinfo=timezone.utc).isoformat()
    except ValueError:
        pass
    return datetime.now(timezone.utc).isoformat()


def read_sms(port: str) -> Optional[List[dict]]:
    """Baca seluruh SMS pada kartu (tanpa menghapus). None bila port sibuk/gagal."""
    lock = port_lock(port)
    if not lock.acquire(blocking=False):
        return None
    try:
        with open_port(port) as ser:
            run_at(ser, 'AT+CSCS="IRA"', 1.0)
            run_at(ser, "AT+CMGF=1", 1.0)
            ok, lines, _ = run_at(ser, 'AT+CMGL="ALL"', 8.0)
            if not ok:
                return None
    except Exception:
        return None
    finally:
        lock.release()

    messages: List[dict] = []
    current: Optional[dict] = None
    for line in lines:
        header = _CMGL_HEADER.match(line)
        if header:
            if current:
                messages.append(current)
            current = {"index": int(header.group(1)), "sender": header.group(3),
                       "timestamp": smsc_to_iso(header.group(4)), "_body": []}
        elif current is not None:
            current["_body"].append(line)
    if current:
        messages.append(current)
    for m in messages:
        m["body"] = "\n".join(m.pop("_body"))
    return messages


# ----------------------------------------------------------------------------- jaringan
def normalize_ws_url(url: str) -> str:
    url = url.strip()
    if url.startswith("http://"):
        return "ws://" + url[7:]
    if url.startswith("https://"):
        return "wss://" + url[8:]
    return url if url.startswith(("ws://", "wss://")) else "wss://" + url


def log(message: str) -> None:
    print(f" [{time.strftime('%H:%M:%S')}] {message}", flush=True)


def _http_status(exc: Exception) -> Optional[int]:
    response = getattr(exc, "response", None)
    return getattr(response, "status_code", None) or getattr(exc, "status_code", None)


class AuthRejected(Exception):
    pass


class Session:
    """Satu koneksi ke server: pemindaian berkala + penanganan perintah."""

    def __init__(self, ws, allowed_ports: Optional[List[str]]):
        self.ws = ws
        self.allowed_ports = allowed_ports
        self.send_lock = asyncio.Lock()
        self.rescan = asyncio.Event()
        self.last_state: Dict[str, Tuple[str, float]] = {}
        self.sms_state: Dict[str, Tuple[str, float, str, float]] = {}
        self.known_units: List[Tuple[str, List[str]]] = []
        self.requests: set = set()
        self.watched: Dict[str, float] = {}  # port -> batas waktu baca cepat

    async def send(self, payload: dict) -> None:
        async with self.send_lock:
            await self.ws.send(json.dumps(payload))

    async def scan_loop(self) -> None:
        gate = asyncio.Semaphore(MAX_PARALLEL_PORTS)

        async def handle(port: str) -> None:
            async with gate:
                status = await asyncio.to_thread(probe_port, port)
                if status["cpin"] == "BUSY":
                    return
                await self.report_port(port, status)

        while True:
            units = await asyncio.to_thread(get_local_units, self.allowed_ports)
            ports = [port for _key, group in units for port in group]
            if units != self.known_units:
                self.known_units = units
                await self.send({"type": "ports_list", "version": VERSION,
                                 "units": [{"key": key, "ports": group} for key, group in units]})
                log(f"Port terdeteksi: {len(ports)} di {len(units)} modem pool")
            await asyncio.gather(*(handle(p) for p in ports))
            try:
                await asyncio.wait_for(self.rescan.wait(), timeout=SCAN_INTERVAL)
                self.rescan.clear()
                self.last_state.clear()
                self.sms_state.clear()
            except asyncio.TimeoutError:
                pass

    async def report_port(self, port: str, status: Dict[str, Optional[str]]) -> None:
        now = time.time()
        signature = json.dumps([status.get("cpin"), status.get("iccid"), status.get("phone")])
        previous = self.last_state.get(port)
        if previous is None or previous[0] != signature or now - previous[1] > HEARTBEAT_INTERVAL:
            self.last_state[port] = (signature, now)
            await self.send({"type": "slot_update", "port": port, "cpin": status["cpin"],
                             "iccid": status.get("iccid"), "phone": status.get("phone")})
            if previous is None or previous[0] != signature:
                log(f"[{port}] {status['cpin']}")
        if status["cpin"] != "READY":
            self.sms_state.pop(port, None)
            return
        await self.sync_sms(port, status.get("iccid") or "", SMS_READ_INTERVAL)

    async def sync_sms(self, port: str, iccid: str, interval: float) -> None:
        """Baca SMS di kartu bila pembacaan terakhir lebih lama dari `interval`, kirim ke server bila berubah."""
        now = time.time()
        prev = self.sms_state.get(port)  # (iccid, waktu baca, digest, waktu kirim terakhir)
        if prev and prev[0] == iccid and now - prev[1] < interval:
            return
        messages = await asyncio.to_thread(read_sms, port)
        if messages is None:
            return
        digest = hashlib.sha256(json.dumps(messages, sort_keys=True).encode()).hexdigest()
        changed = prev is None or prev[0] != iccid or prev[2] != digest
        last_sent = prev[3] if prev else 0.0
        if changed or now - last_sent >= SMS_RESEND_INTERVAL:
            await self.send({"type": "sms_batch", "port": port, "messages": messages})
            if changed:
                log(f"[{port}] {len(messages)} SMS di kartu dikirim ke server")
            last_sent = now
        self.sms_state[port] = (iccid, now, digest, last_sent)

    async def watch_loop(self) -> None:
        """Port yang sedang menunggu OTP (diminta server lewat watch_sms) dibaca jauh lebih sering."""
        while True:
            await asyncio.sleep(WATCH_INTERVAL)
            now = time.time()
            for port, until in list(self.watched.items()):
                if until < now:
                    self.watched.pop(port, None)
                    continue
                state = self.sms_state.get(port)
                if state:  # hanya port READY yang sudah pernah terbaca pemindai
                    await self.sync_sms(port, state[0], WATCH_INTERVAL)

    async def run(self) -> None:
        await asyncio.gather(self.scan_loop(), self.watch_loop())

    async def handle_server_message(self, payload: dict) -> None:
        kind = payload.get("type")
        if kind == "ping":
            await self.send({"type": "pong"})
        elif kind == "rescan":
            self.rescan.set()
        elif kind == "watch_sms":
            port, seconds = payload.get("port"), payload.get("seconds")
            if isinstance(port, str) and isinstance(seconds, (int, float)):
                if seconds > 0:
                    self.watched[port] = time.time() + min(float(seconds), MAX_WATCH_SECONDS)
                else:
                    self.watched.pop(port, None)
        elif kind in ("cmd", "ussd"):
            # Jangan di-await: satu USSD bisa 15 detik dan menahan pesan lain. Server meminta nomor 32 SIM sekaligus;
            # bila diproses berurutan, sisanya melewati batas tunggu server lalu kena jeda percobaan ulang.
            # Port yang sama tetap bergiliran lewat port_lock.
            task = asyncio.create_task(self._run_request(payload))
            self.requests.add(task)
            task.add_done_callback(self.requests.discard)

    async def _run_request(self, payload: dict) -> None:
        try:
            await self.handle_request(payload)
        except Exception as err:
            log(f"Gagal memproses permintaan server: {type(err).__name__}")

    async def handle_request(self, payload: dict) -> None:
        req_id, port = payload.get("req_id"), payload.get("port")
        timeout = min(max(float(payload.get("timeout") or 4.0), 1.0), 30.0)
        reply = {"type": "cmd_response", "req_id": req_id, "port": port, "success": False,
                 "lines": [], "raw": "", "error": None}

        known = await asyncio.to_thread(get_local_ports, self.allowed_ports)
        if port not in known:
            reply["error"] = f"Port {port} tidak ada di komputer ini."
        elif payload.get("type") == "ussd":
            code = str(payload.get("code") or "")
            if not USSD_CODE.match(code):
                reply["error"] = "Kode USSD tidak valid."
            else:
                log(f"[{port}] USSD {code}")
                ok, raw, error = await asyncio.to_thread(
                    self._with_port, port, lambda ser: run_ussd(ser, code, timeout), "")
                reply.update(success=ok, raw=raw, error=error)
        else:
            command = str(payload.get("command") or "")
            if not is_command_allowed(command):
                reply["error"] = "Perintah AT ditolak oleh allowlist agent."
                log(f"[{port}] perintah ditolak: {command[:40]!r}")
            else:
                log(f"[{port}] AT {command}")
                ok, lines, error = await asyncio.to_thread(
                    self._with_port, port, lambda ser: run_at(ser, command, timeout), [])
                reply.update(success=ok, lines=lines, raw="\n".join(lines), error=error)
        await self.send(reply)

    @staticmethod
    def _with_port(port: str, action, empty):
        """Jalankan aksi (ser) -> (ok, data, error) dengan kunci eksklusif pada port."""
        lock = port_lock(port)
        if not lock.acquire(timeout=PORT_WAIT):  # pemindai memegang port < 1-2 detik
            return False, empty, "Port sedang dipakai operasi lain."
        try:
            with open_port(port) as ser:
                return action(ser)
        except Exception as exc:
            return False, empty, f"Gagal mengakses port: {type(exc).__name__}"
        finally:
            lock.release()


async def connect_and_run(server_url: str, token: str, allowed_ports: Optional[List[str]]) -> None:
    """Satu sesi koneksi. Melempar AuthRejected bila token ditolak; exception lain berarti perlu reconnect."""
    server = normalize_ws_url(server_url)
    masked = f"{token[:10]}...{token[-4:]}" if len(token) > 14 else "***"
    log(f"Menghubungi {server} (token {masked})")

    try:
        connection = websockets.connect(f"{server}?token={token}", ping_interval=20, ping_timeout=20,
                                        max_size=2 ** 22)
        async with connection as ws:
            welcome = json.loads(await asyncio.wait_for(ws.recv(), timeout=10.0))
            if welcome.get("type") != "welcome":
                raise ConnectionError(f"Handshake tidak valid: {welcome}")
            log(f"Terhubung sebagai {welcome.get('username')}")

            session = Session(ws, allowed_ports)
            scanner = asyncio.create_task(session.run())
            try:
                async for message in ws:
                    try:
                        await session.handle_server_message(json.loads(message))
                    except Exception as err:
                        log(f"Gagal memproses pesan server: {type(err).__name__}")
                    if scanner.done():
                        scanner.result()  # munculkan error pemindai agar sesi di-restart
            finally:
                scanner.cancel()
                for task in list(session.requests):
                    task.cancel()
    except Exception as exc:
        if _http_status(exc) in (401, 403):
            raise AuthRejected() from exc
        code = getattr(getattr(exc, "rcvd", None), "code", None) or getattr(exc, "code", None)
        if code == 1008:
            raise AuthRejected() from exc
        raise


def load_saved_token() -> Tuple[str, str]:
    if CONFIG_FILE.exists():
        try:
            saved = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
            return str(saved.get("server_url", "")), str(saved.get("agent_token", ""))
        except Exception:
            pass
    return "", ""


def save_token(server_url: str, token: str) -> None:
    try:
        CONFIG_FILE.write_text(json.dumps({"server_url": server_url, "agent_token": token}, indent=2), encoding="utf-8")
        try:
            os.chmod(CONFIG_FILE, 0o600)
        except OSError:
            pass
    except OSError:
        pass


async def main_loop() -> None:
    # Bawaan asyncio.to_thread = min(32, CPU+4) thread; PC 4 core hanya 8, sehingga USSD 32 port kembali mengantre.
    asyncio.get_running_loop().set_default_executor(ThreadPoolExecutor(max_workers=MAX_WORKER_THREADS))
    parser = argparse.ArgumentParser(description="SCPool Agent")
    parser.add_argument("--server", default="", help="URL WebSocket server (wss://host/ws/agent)")
    parser.add_argument("--token", default="", help="Agent token (scpool_...)")
    parser.add_argument("--ports", default="", help="Batasi ke port tertentu, pisahkan koma (mis. COM3,COM4)")
    args = parser.parse_args()

    saved_server, saved_token = load_saved_token()
    server_url = (args.server or DEFAULT_SERVER or saved_server).strip()
    token = (args.token or DEFAULT_TOKEN or saved_token).strip()
    allowed = [p.strip() for p in args.ports.split(",") if p.strip()] or None

    print(f"\n SCPool Agent v{VERSION}\n Tekan Ctrl+C untuk berhenti.\n")
    backoff = 2.0
    while True:
        if not server_url:
            server_url = input(" URL server (mis. wss://domain-anda/ws/agent): ").strip()
        if not token:
            token = input(" Agent token (scpool_...): ").strip()
            if not token:
                continue
        try:
            started = time.time()
            await connect_and_run(server_url, token, allowed)
            log("Koneksi ditutup server.")
            backoff = 2.0 if time.time() - started > 30 else min(backoff * 2, 60.0)
        except AuthRejected:
            print("\n [!] Token ditolak server (salah, akun belum disetujui, atau dinonaktifkan).")
            if CONFIG_FILE.exists():
                CONFIG_FILE.unlink(missing_ok=True)
            if token == DEFAULT_TOKEN and DEFAULT_TOKEN:
                print("     Token tertanam di berkas ini sudah tidak berlaku. Unduh ulang agent dari dashboard.")
                await asyncio.sleep(30)
            token = ""
            continue
        except Exception as exc:
            log(f"Koneksi gagal: {type(exc).__name__}: {exc}")
            backoff = min(backoff * 2, 60.0)
        else:
            if token and token != DEFAULT_TOKEN:
                save_token(server_url, token)
        log(f"Menghubungkan ulang dalam {backoff:.0f} detik...")
        await asyncio.sleep(backoff)


def main() -> None:
    try:
        asyncio.run(main_loop())
    except KeyboardInterrupt:
        print("\n Agent dihentikan.")
        sys.exit(0)


if __name__ == "__main__":
    main()
