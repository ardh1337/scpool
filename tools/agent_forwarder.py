"""
SCPool - Lightweight Remote Modem Pool Forwarder (agent.exe)
Coded By Ardh1337
Berjalan di komputer lokal tempat modem pool fisik dicolok.
Meneruskan I/O port serial ke Server SaaS secara aman via WebSocket.
Tidak memerlukan akses ke database pusat maupun source code dashboard.
"""

import sys
import os
import json
import time
import re
import asyncio
from pathlib import Path
from typing import List, Dict, Optional

try:
    import serial
    import serial.tools.list_ports
except ImportError:
    print("[ERROR] Module 'pyserial' belum terpasang. Jalankan: pip install pyserial")
    sys.exit(1)

try:
    import websockets
except ImportError:
    print("[ERROR] Module 'websockets' belum terpasang. Jalankan: pip install websockets")
    sys.exit(1)


CONFIG_FILE = Path("agent_config.json")
DEFAULT_SERVER = "wss://scpool.ardh1337.cloud/ws/agent"


def natural_sort_key(s: str):
    return [int(text) if text.isdigit() else text.lower() for text in re.split(r'(\d+)', s)]


def get_local_com_ports() -> List[str]:
    """Mendeteksi semua port serial COM yang aktif di sistem Windows."""
    ports = [p.device for p in serial.tools.list_ports.comports()]
    ports.sort(key=natural_sort_key)
    return ports


def load_or_prompt_config() -> dict:
    """Membaca konfigurasi atau meminta input awal jika belum ada file config."""
    if CONFIG_FILE.exists():
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            print(f"[WARN] Gagal membaca {CONFIG_FILE}: {e}")

    print("=" * 60)
    print("   SIM FLEET - MODEM POOL AGENT SETUP")
    print("=" * 60)
    server_input = input(f"Masukkan URL WebSocket Server [{DEFAULT_SERVER}]: ").strip()
    server_url = server_input if server_input else DEFAULT_SERVER

    agent_token = ""
    while not agent_token:
        agent_token = input("Masukkan Token Agent (tok_live_...): ").strip()

    cfg = {
        "server_url": server_url,
        "agent_token": agent_token
    }

    try:
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2)
        print(f"[OK] Konfigurasi disimpan ke {CONFIG_FILE.resolve()}")
    except Exception as e:
        print(f"[WARN] Gagal menyimpan file konfigurasi: {e}")

    return cfg


def execute_at_command(port_name: str, command: str, baudrate: int = 115200, timeout: float = 3.0) -> dict:
    """Membuka port serial, mengirimkan satu AT command, dan membaca responnya."""
    lines = []
    success = False
    error_msg = None

    try:
        with serial.Serial(port=port_name, baudrate=baudrate, timeout=timeout, write_timeout=timeout) as ser:
            ser.reset_input_buffer()
            ser.reset_output_buffer()

            cmd_bytes = command.encode("latin-1")
            if not cmd_bytes.endswith(b"\r") and not cmd_bytes.endswith(b"\r\n"):
                cmd_bytes += b"\r\n"

            ser.write(cmd_bytes)
            ser.flush()

            start_t = time.time()
            while time.time() - start_t < timeout:
                line_bytes = ser.readline()
                if not line_bytes:
                    break
                line_str = line_bytes.decode("latin-1", errors="replace").strip()
                if line_str:
                    lines.append(line_str)
                    if line_str in ["OK", "CONNECT"]:
                        success = True
                        break
                    elif "ERROR" in line_str or "+CME ERROR" in line_str or "+CMS ERROR" in line_str:
                        success = False
                        break

    except Exception as e:
        error_msg = str(e)
        success = False

    return {
        "success": success,
        "lines": lines,
        "error": error_msg
    }


async def run_agent():
    cfg = load_or_prompt_config()
    server_url = cfg.get("server_url", DEFAULT_SERVER)
    agent_token = cfg.get("agent_token", "")

    # Normalisasi WS URL jika user memasukkan HTTP
    if server_url.startswith("http://"):
        server_url = "ws://" + server_url[7:]
    elif server_url.startswith("https://"):
        server_url = "wss://" + server_url[8:]

    ws_target = f"{server_url}?token={agent_token}"

    print("-" * 60)
    print(f"Target Server : {server_url}")
    print(f"Agent Token   : {agent_token[:12]}... (tersimpan)")
    print("-" * 60)

    while True:
        try:
            detected_ports = get_local_com_ports()
            print(f"\n[INFO] Mendeteksi port serial lokal: {len(detected_ports)} port ditemukan")
            if detected_ports:
                print(f"       {', '.join(detected_ports[:8])}{' ...' if len(detected_ports) > 8 else ''}")

            print(f"[INFO] Menghubungi server pusat ({server_url})...")

            async with websockets.connect(ws_target, ping_interval=20, ping_timeout=20) as ws:
                print("[OK] Terhubung ke SIM Fleet Server!")

                # Kirim daftar port ke server
                init_payload = {
                    "type": "ports_list",
                    "ports": detected_ports,
                    "version": "1.0.0"
                }
                await ws.send(json.dumps(init_payload))

                # Loop penerimaan perintah dari server
                async for message in ws:
                    try:
                        data = json.loads(message)
                        msg_type = data.get("type")

                        if msg_type == "welcome":
                            pool_id = data.get("pool_id")
                            user = data.get("username")
                            print(f"[OK] Handshake sukses! Pool ID: {pool_id} (Pengguna: {user})")

                        elif msg_type == "cmd":
                            req_id = data.get("req_id")
                            port = data.get("port")
                            cmd = data.get("command")
                            cmd_timeout = data.get("timeout", 4.0)

                            # Jalankan AT command secara sinkron di threadpool agar tidak memblokir asyncio loop
                            res = await asyncio.to_thread(
                                execute_at_command,
                                port_name=port,
                                command=cmd,
                                timeout=cmd_timeout
                            )

                            response_payload = {
                                "type": "cmd_response",
                                "req_id": req_id,
                                "port": port,
                                "success": res["success"],
                                "lines": res["lines"],
                                "error": res["error"]
                            }
                            await ws.send(json.dumps(response_payload))

                    except Exception as err:
                        print(f"[WARN] Error saat memproses pesan: {err}")

        except (websockets.exceptions.ConnectionClosedError, websockets.exceptions.ConnectionClosedOK) as e:
            print(f"[DISCONNECT] Koneksi terputus: {e}. Mencoba menghubungkan kembali dalam 5 detik...")
            await asyncio.sleep(5)
        except Exception as e:
            print(f"[ERROR] Koneksi gagal: {e}. Mencoba menghubungkan kembali dalam 5 detik...")
            await asyncio.sleep(5)


def main():
    try:
        asyncio.run(run_agent())
    except KeyboardInterrupt:
        print("\n[INFO] Agen dihentikan oleh pengguna.")


if __name__ == "__main__":
    main()

