# Tools & Utilitas SCPool
*Coded By Ardh1337*

Direktori ini berisi utilitas operasional, forwarder agen remote, serta skrip diagnostik hardware serial modem untuk ekosistem SCPool.

## 1. Agen Klien Remote
* **`agent_forwarder.py`**: Skrip agen klien ringan berbasis WebSockets (`asyncio` + `pyserial`). Dijalankan di komputer klien yang terhubung langsung ke modem pool fisik untuk meneruskan komunikasi serial ke server SaaS / dashboard via tunnel. File ini juga dapat diunduh otomatis via endpoint `/download/agent`.

## 2. Utilitas Sinkronisasi & Deteksi Hardware
* **`probe_ports.py`**: Memindai seluruh port COM fisik di Windows (VID/PID, deskripsi port USB serial).
* **`sync_hardware_to_db.py`**: Menjalankan sinkronisasi hardware ke database lokal secara manual via CLI tanpa membuka browser.
* **`diagnose_slot1.py`**: Diagnostik mendalam status komunikasi AT pada Slot 1 / Port COM pertama.

## 3. Utilitas USSD & SMS
* **`check_ussd.py`**: Menguji dial USSD operator secara langsung dari CLI.
* **`interactive_ussd.py`**: Menjalankan sesi USSD interaktif (multi-step menu) pada port serial tertentu.
* **`inspect_raw_sms.py`**: Membaca dan menampilkan format raw SMS langsung dari storage SIM (AT+CMGL="ALL").

## 4. Testbench & Diagnostic Scripts
* **`test_atclient_cmgl.py`**: Verifikasi pembacaan respon AT+CMGL melalui ATClient.
* **`test_interactive_step.py`**: Uji coba eksekusi navigasi menu beruntun USSD.
* **`test_read_sms.py`**: Uji baca SMS teks sederhana.
* **`test_timeout_glitch.py`**: Uji coba penanganan timeout serial dan glitch pada koneksi kabel USB.
* **`test_ussd_direct.py`**: Uji coba eksekusi dial USSD langsung ke port modem.

