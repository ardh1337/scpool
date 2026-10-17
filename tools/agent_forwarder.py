"""
SCPool - Quantum Modem Engine // Client Hardware Bridge
Coded By Ardh1337
Menghubungkan modem pool fisik lokal langsung ke Server Pusat.
Tidak memerlukan konfigurasi rumit.
"""

import sys
import os
import json
import time
import re
import argparse
import asyncio
from pathlib import Path
from typing import List, Dict, Optional

try:
    import serial
    import serial.tools.list_ports
except ImportError:
    print("[ERROR] Pustaka 'pyserial' belum terpasang. Jalankan: pip install pyserial")
    sys.exit(1)

try:
    import websockets
except ImportError:
    print("[ERROR] Pustaka 'websockets' belum terpasang. Jalankan: pip install websockets")
    sys.exit(1)


# Konfigurasi Bawaan (Akan diisi otomatis saat file diunduh dari Panel)
DEFAULT_SERVER = "wss://scpool.ardh1337.cloud/ws/agent"
DEFAULT_TOKEN = ""

CONFIG_FILE = Path("agent_config.json")


def natural_sort_key(s: str):
    return [int(text) if text.isdigit() else text.lower() for text in re.split(r'(\d+)', s)]


def get_local_com_ports() -> List[str]:
    """Mendeteksi semua port serial COM yang aktif di sistem."""
    ports = [p.device for p in serial.tools.list_ports.comports()]
    ports.sort(key=natural_sort_key)
    return ports


def print_ai_banner():
    """Menampilkan banner terminal modern bergaya Neural Hardware Bridge."""
    os.system("cls" if os.name == "nt" else "clear")
    banner = r"""
================================================================================
   ███████╗ ██████╗██████╗  ██████╗  ██████╗ ██╗     
   ██╔════╝██╔════╝██╔══██╗██╔═══██╗██╔═══██╗██║     
   ███████╗██║     ██████╔╝██║   ██║██║   ██║██║     
   ╚════██║██║     ██╔═══╝ ██║   ██║██║   ██║██║     
   ███████║╚██████╗██║     ╚██████╔╝╚██████╔╝███████╗
   ╚══════╝ ╚═════╝╚═╝      ╚═════╝  ╚═════╝ ╚══════╝
      >> QUANTUM HARDWARE BRIDGE // CODED BY ARDH1337 <<
================================================================================
    """
    print(banner.strip())


def render_ports_table(ports: List[str]):
    """Menampilkan status visual port COM lokal secara rapi dan futuristik."""
    if not ports:
        print("\n [!] STATUS PORT: Tidak ada port serial COM yang terdeteksi.")
        print("     Pastikan kabel USB modem pool telah tersambung ke komputer.")
        print("     Sistem akan otomatis mendeteksi ketika modem dicolokkan...")
        return

    print(f"\n [●] PORT STATUS MONITOR: {len(ports)} Channels Active")
    print(" ┌" + "─" * 74 + "┐")

    # Format 4 kolom per baris
    cols = 4
    for i in range(0, len(ports), cols):
        row_ports = ports[i:i + cols]
        cells = []
        for p in row_ports:
            cells.append(f"[{p:>5}] READY".center(17))
        # Jika kolom baris terakhir kurang dari cols
        while len(cells) < cols:
            cells.append(" " * 17)
        row_str = " │ ".join(cells)
        print(f" │ {row_str} │")

    print(" └" + "─" * 74 + "┘")


def execute_at_command(port_name: str, command: str, baudrate: int = 115200, timeout: float = 3.0) -> dict:
    """Membuka port serial, mengirimkan AT command, dan membaca responnya."""
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


def normalize_ws_url(url: str) -> str:
    url = url.strip()
    if url.startswith("http://"):
        return "ws://" + url[7:]
    if url.startswith("https://"):
        return "wss://" + url[8:]
    if not (url.startswith("ws://") or url.startswith("wss://")):
        return "wss://" + url
    return url


async def connect_and_run(server_url: str, token: str) -> bool:
    """
    Mencoba melakukan koneksi dan handshake dengan server.
    Mengembalikan True jika sesi berjalan sukses (atau terputus wajar).
    Mengembalikan False jika otentikasi ditolak (error 1008 atau token invalid).
    """
    server_clean = normalize_ws_url(server_url)
    ws_target = f"{server_clean}?token={token}"

    print(f"\n [LINK] Menghubungi Gateway: {server_clean}")
    masked_token = f"{token[:10]}...{token[-4:]}" if len(token) > 14 else token
    print(f" [AUTH] Mengirim Token   : {masked_token}")

    try:
        async with websockets.connect(ws_target, ping_interval=20, ping_timeout=20) as ws:
            # Tunggu respon pesan handshake pertama dari server
            first_msg = await asyncio.wait_for(ws.recv(), timeout=10.0)
            data = json.loads(first_msg)

            if data.get("type") != "welcome":
                print(f" [!] Handshake ditolak oleh server: {first_msg}")
                return False

            pool_id = data.get("pool_id", "Unknown")
            username = data.get("username", "Unknown")

            # Cetak banner dan status koneksi
            print_ai_banner()
            print(f" [●] GATEWAY STATUS : CONNECTED & SYNCHRONIZED")
            print(f" [●] OPERATOR NODE  : {username} (Cluster: {pool_id})")
            print(f" [●] GATEWAY TARGET : {server_clean}")
            print(f" [●] SECURITY TOKEN : {masked_token} [VALIDATED]")

            # Deteksi awal port lokal
            current_ports = get_local_com_ports()
            render_ports_table(current_ports)

            # Kirim daftar port ke server
            await ws.send(json.dumps({
                "type": "ports_list",
                "ports": current_ports,
                "version": "2.0.0"
            }))

            print("\n [*] Telemetri hardware aktif. Menunggu instruksi dari Server...")
            print(" [i] Tekan Ctrl+C di terminal ini untuk menghentikan agen.\n")

            last_ports = list(current_ports)

            # Loop pemrosesan pesan dari server
            async for message in ws:
                try:
                    payload = json.loads(message)
                    msg_type = payload.get("type")

                    if msg_type == "cmd":
                        req_id = payload.get("req_id")
                        port = payload.get("port")
                        cmd = payload.get("command")
                        cmd_timeout = payload.get("timeout", 4.0)

                        timestamp = time.strftime("%H:%M:%S")
                        print(f" [{timestamp}] >> [{port}] AT CMD: {cmd}")

                        res = await asyncio.to_thread(
                            execute_at_command,
                            port_name=port,
                            command=cmd,
                            timeout=cmd_timeout
                        )

                        resp_status = "OK" if res["success"] else f"FAIL ({res.get('error') or 'ERROR'})"
                        print(f" [{timestamp}] << [{port}] STATUS: {resp_status}")

                        response_payload = {
                            "type": "cmd_response",
                            "req_id": req_id,
                            "port": port,
                            "success": res["success"],
                            "lines": res["lines"],
                            "error": res["error"]
                        }
                        await ws.send(json.dumps(response_payload))

                    elif msg_type == "ping":
                        await ws.send(json.dumps({"type": "pong"}))

                except Exception as err:
                    print(f" [WARN] Gagal memproses paket: {err}")

            return True

    except websockets.exceptions.InvalidStatusCode as e:
        if e.status_code == 403 or e.status_code == 401:
            print(f"\n [!] AUTENTIKASI DITOLAK: Token tidak valid (HTTP {e.status_code}).")
            return False
        print(f"\n [!] KONEKSI GAGAL: Server merespon HTTP {e.status_code}.")
        return True

    except websockets.exceptions.ConnectionClosedError as e:
        if e.code == 1008:
            print("\n [!] AUTENTIKASI DITOLAK: Token agen salah atau akun dinonaktifkan (Code 1008).")
            return False
        print(f"\n [DISCONNECT] Koneksi terputus: {e}. Menghubungkan ulang...")
        return True

    except Exception as e:
        print(f"\n [!] Gagal terhubung ke Gateway: {e}")
        return True


async def main_loop():
    parser = argparse.ArgumentParser(description="SCPool Quantum Modem Bridge")
    parser.add_argument("--server", help="URL WebSocket Server", default="")
    parser.add_argument("--token", help="Token Agen (scpool_...)", default="")
    args = parser.parse_args()

    print_ai_banner()

    # Prioritas Server URL: CLI args -> DEFAULT_SERVER
    server_url = (args.server or DEFAULT_SERVER).strip()

    # Prioritas Token: CLI args -> DEFAULT_TOKEN (yang di-embed saat diunduh) -> File Config
    token = (args.token or DEFAULT_TOKEN).strip()
    if not token and CONFIG_FILE.exists():
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                saved = json.load(f)
                token = saved.get("agent_token", "").strip()
                if not server_url and saved.get("server_url"):
                    server_url = saved.get("server_url").strip()
        except Exception:
            pass

    # Loop interaktif: jika token belum ada atau salah, minta input ulang tanpa menyimpan yang salah
    while True:
        if not token:
            print("\n" + "-" * 60)
            print(" [SETUP] Masukkan Kredensial Autentikasi")
            print("-" * 60)
            server_input = input(f" URL Gateway Server [{server_url}]: ").strip()
            if server_input:
                server_url = server_input

            while not token:
                token = input(" Masukkan Token Agen (scpool_...): ").strip()
                if not token:
                    print(" [!] Token tidak boleh kosong.")

        # Lakukan koneksi
        success = await connect_and_run(server_url, token)

        if not success:
            # Token salah / ditolak -> Hapus config lama & reset token agar meminta input ulang
            if CONFIG_FILE.exists():
                try:
                    CONFIG_FILE.unlink()
                except Exception:
                    pass
            print(" [RETRY] Silakan periksa token Anda pada dashboard lalu masukkan kembali.")
            token = ""
            await asyncio.sleep(1)
            continue

        # Jika sukses terhubung dan token bukan dari DEFAULT_TOKEN yang di-embed, simpan ke file
        if token and token != DEFAULT_TOKEN:
            try:
                with open(CONFIG_FILE, "w", encoding="utf-8") as f:
                    json.dump({"server_url": server_url, "agent_token": token}, f, indent=2)
            except Exception:
                pass

        # Jika koneksi terputus normal, tunggu 5 detik sebelum reconnect otomatis
        print("\n [*] Menghubungkan ulang ke Gateway dalam 5 detik...")
        await asyncio.sleep(5)


def main():
    try:
        asyncio.run(main_loop())
    except KeyboardInterrupt:
        print("\n\n [INFO] Gateway dihentikan oleh pengguna. Sampai jumpa!")
        sys.exit(0)


if __name__ == "__main__":
    main()
