"""
Skrip Probe Port Serial Perangkat Nyata (Read-Only)
Mengacu pada Tahap M1 dan Lampiran A:
- Memindai semua COM port USB di sistem Windows.
- Membaca VID:PID, nomor lokasi USB, dan identitas perangkat.
- Mengirim HANYA perintah baca aman dalam daftar izin (AT, ATI, AT+CGMI, AT+CPIN?).
- Melakukan masking pada data sensitif (ICCID / Nomor) agar tidak terekspos (I-12).
"""

import sys
import time
import subprocess
import re
from pathlib import Path
from typing import List, Dict, Optional

# Tambahkan direktori root proyek ke sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
import serial

from app.modem.at_client import ATClient, ATResponse
from app.modem.transport import SerialTransport
from app.modem.sms_reader import mask_sensitive


def list_usb_com_ports() -> List[Dict[str, str]]:
    """Memindai daftar port COM USB melalui WMI Windows."""
    ports = []
    try:
        cmd = [
            "powershell", "-NoProfile", "-Command",
            "Get-CimInstance Win32_PnPEntity | Where-Object { $_.Name -match 'COM\\d+' } | "
            "Select-Object Name, DeviceID, PNPDeviceID | ConvertTo-Json"
        ]
        res = subprocess.run(cmd, capture_output=True, text=True, check=True)
        import json
        data = json.loads(res.stdout)
        if isinstance(data, dict):
            data = [data]
        for item in data:
            name = item.get("Name", "")
            match = re.search(r'\((COM\d+)\)', name)
            com = match.group(1) if match else name
            ports.append({
                "port": com,
                "name": name,
                "device_id": item.get("DeviceID", ""),
                "pnp_id": item.get("PNPDeviceID", "")
            })
    except Exception as e:
        print(f"[!] Gagal memindai COM port melalui WMI: {e}", file=sys.stderr)
    return ports


def probe_single_port(port_name: str, baudrate: int = 115200, timeout: float = 1.0) -> Dict[str, str]:
    """
    Mengirimkan perintah baca aman ke satu port COM.
    Hanya membaca identitas modul (AT, ATI, AT+CPIN?).
    """
    info = {"port": port_name, "status": "OFFLINE", "module": "UNKNOWN", "sim": "UNKNOWN"}
    transport = SerialTransport(port=port_name, baudrate=baudrate, timeout=timeout)
    client = ATClient(transport=transport, default_timeout=timeout)

    try:
        client.open()
        # 1. Ping AT
        r_at = client.send_command("AT", timeout=0.8)
        if not r_at.success:
            info["status"] = "NO_RESPONSE"
            return info

        info["status"] = "ONLINE"

        # 2. Baca Identitas Modul (ATI)
        r_ati = client.send_command("ATI", timeout=1.0)
        if r_ati.success and r_ati.lines:
            clean_lines = [l for l in r_ati.lines if l != "OK"]
            info["module"] = " | ".join(clean_lines)[:60]

        # 3. Baca Status SIM (AT+CPIN?)
        r_cpin = client.send_command("AT+CPIN?", timeout=1.0)
        if r_cpin.success:
            info["sim"] = "READY" if any("READY" in l for l in r_cpin.lines) else "LOCKED"
            if info["sim"] == "READY":
                r_iccid = client.send_command("AT+QCCID", timeout=1.0)
                from app.modem.sim_status import parse_iccid_response
                iccid = parse_iccid_response(r_iccid)
                if iccid:
                    info["iccid"] = iccid
        else:
            err = (r_cpin.error_message or "").upper()
            if "NOT INSERTED" in err or "+CME ERROR: 10" in err:
                info["sim"] = "NOT_INSERTED"
            else:
                info["sim"] = "ERROR"

    except Exception as e:
        info["status"] = f"ERROR ({type(e).__name__})"
    finally:
        try:
            client.close()
        except Exception:
            pass

    return info


def run_probe_all():
    print("=" * 65)
    print("  SIM INVENTORY - USB COM PROBE TOOL (READ-ONLY)")
    print("=" * 65)

    ports = list_usb_com_ports()
    print(f"Ditemukan {len(ports)} port COM di sistem Windows.\n")

    if not ports:
        print("Tidak ada port COM yang terdeteksi.")
        return

    print(f"{'PORT':<8} | {'STATUS':<10} | {'SIM':<12} | {'ICCID (MASKED)':<22} | {'IDENTITAS MODUL'}")
    print("-" * 85)

    for p in ports:
        port_name = p["port"]
        res = probe_single_port(port_name)
        clean_text = re.sub(r'[^\x20-\x7E]', '', res['module'])
        safe_module = mask_sensitive(clean_text)
        raw_iccid = res.get("iccid", "-")
        safe_iccid = mask_sensitive(raw_iccid) if raw_iccid != "-" else "-"
        print(f"{res['port']:<8} | {res['status']:<10} | {res['sim']:<12} | {safe_iccid:<22} | {safe_module}")

    print("=" * 85)


if __name__ == "__main__":
    run_probe_all()
