# Skrip Operasional SCPool

Jalankan dari root proyek, mis. `python scripts/probe_ports.py`.

- `probe_ports.py` — daftar port COM fisik (VID/PID, deskripsi). Hanya membaca daftar port, tidak membuka port.
- `sync_hardware_to_db.py` — sinkronisasi hardware lokal ke database tanpa dashboard. Membuka port serial sungguhan.

## manual/
Skrip diagnostik yang membuka port serial sungguhan dan butuh modem terhubung. Bukan bagian dari `pytest`.
Sesuai AGENTS.md, jalankan hanya dengan persetujuan pemilik hardware.

- `check_ussd.py`, `interactive_ussd.py`, `manual_ussd_direct.py` — uji USSD.
- `inspect_raw_sms.py`, `manual_read_sms.py`, `manual_atclient_cmgl.py` — baca SMS mentah (tidak menghapus apa pun dari SIM).
- `diagnose_slot1.py`, `manual_interactive_step.py`, `manual_timeout_glitch.py` — diagnosis komunikasi AT.

Agent untuk komputer klien ada di `agent/agent_forwarder.py` dan diunduh dari dashboard (`/api/download/agent`).
