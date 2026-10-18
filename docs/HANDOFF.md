# SCPool: catatan serah-terima

Dokumen ini merangkum seluruh pekerjaan sampai 18 Okt 2026 supaya sesi baru (Claude Code di VS Code) bisa melanjutkan tanpa konteks percakapan sebelumnya. Aturan kerja ada di `AGENTS.md`, spesifikasi dan invarian ada di `docs/agent-spec-sim-inventory.md`.

## 1. Tujuan produk

Dashboard web untuk mengelola modem pool Foxcom (8, 16, 32, atau 64 port) dan SIM card di dalamnya, sebagai pengganti SMart ACT Ultimate 2017. Fungsi utama:

- Inventaris SIM per port, dengan nomor urut hanya untuk SIM yang terdeteksi.
- Pembacaan SMS masuk dan deteksi OTP (Fast OTP).
- USSD, termasuk USSD massal.
- Multi-akun (multi-tenant). Setiap klien memasang agent Windows di komputernya. Agent membaca modem lewat port serial dan mengirim datanya ke server lewat WebSocket.

Pemilik proyek berbahasa Indonesia. Semua teks antarmuka dan balasan memakai bahasa Indonesia.

## 2. Arsitektur singkat

- **Server:** FastAPI + SQLAlchemy 2 + SQLite (WAL). Skema dimigrasi lewat `PRAGMA user_version`, versi saat ini 4 (`app/database/migrations.py`).
- **Agent:** `agent/agent_forwarder.py`, berjalan di Windows klien. Token agent sudah tertanam di file yang diunduh dari dashboard. Hanya perintah AT dalam allowlist yang boleh dikirim, dan SMS tidak pernah dihapus dari SIM.
- **Gateway:** `app/services/agent_gateway.py`, satu agent per pemilik akun (user id), endpoint `/ws/agent`.
- **Frontend:** vanilla JS tanpa build step (`app/web/app.js` sekitar 2800 baris, `style.css`, `index.html`). Handler inline dan fungsi global. Lihat bagian 6.
- **Deploy:** `deploy/ecosystem.config.js` (PM2) di belakang reverse proxy atau tunnel. Di server publik `SCPOOL_LOCAL_AUTOLOGIN=0` dan `SCPOOL_LOCAL_HARDWARE=0`.

Tabel utama (`app/database/models.py`): `ModemPool` (punya `owner_id` dan `group_key`), `Port`, `Slot`, `SlotNumbering`, `SIMCard`, `SIMSession`, `SMSMessage`, `SMSPart`, `EventLog`, `OperatorProfile`, `UserAccount`, `UserSession`, `SystemSetting`. Slot unik per (pool_id, slot_index) dan (pool_id, physical_port_id).

## 3. Modem pool otomatis (inti desain)

1. Registrasi hanya username dan password. Jumlah pool dan port tidak diisi manual.
2. Agent mengelompokkan port serial USB per kabel USB ke komputer (kunci `usb:<bus>-<port>`). Satu kelompok sama dengan satu modem pool fisik.
3. `app/services/units.py` membuat, mengganti nama, memindahkan port, dan menghapus unit. `sync_detected_ports` memakai aturan anchor/mayoritas supaya port yang sudah punya slot tidak pernah pindah (I-3).
4. Port yang hilang ditandai OFFLINE, tidak dihapus. Status slot: READY, EMPTY, SIM_ERROR, PORT_ERROR, OFFLINE.
5. Dua pool yang tergabung lewat hub USB dipisahkan manual dengan "Pindahkan Port". Riwayat SIM dan SMS ikut berpindah.
6. Batas: 16 unit per akun, 64 port per unit. Satu akun memakai satu agent.

## 4. Riwayat pekerjaan

| Fase | Isi | Hasil |
|---|---|---|
| 1 | Audit dan restruktur kode | Struktur paket seperti di README |
| 2 | Keamanan dan QC awal | Perbaikan dasar, suite pytest |
| 3 | Multi-unit, deteksi otomatis, nama pool bisa diganti | Skema v4, `units.py`, API `/pools` |
| 4 | Tampilan chassis | Layout 8/16/32/64 port, lihat bagian 5 |
| 5 | Audit keamanan menyeluruh | Bagian 7 |
| 6 | Uji E2E klien dari registrasi sampai logout | 141 pemeriksaan lulus, 5 bug diperbaiki, bagian 8 |

## 5. Tampilan chassis (aturan yang harus dijaga)

- Ukuran pool hanya 8, 16, 32, 64. Maksimal 16 port per kolom, jadi tata letaknya 1×8, 1×16, 2×16, 4×16.
- Sampai sekitar 3 pool berdampingan, simetris dan adaptif. Pool 64+32 (6 kolom) turun ke baris kedua di layar sempit. Itu diketahui dan diterima.
- Fast OTP tetap ada, lebih kecil, dan harus muat bila ada lebih dari satu pool. Mode multi-pool memakai `.unified-pool-group` dengan label `.pool-chip`.
- Fungsi terkait di `app.js`: `chassisColumns`, `renderChassis`, `renderSlotBox`, `renderPools`, `visiblePools`, `updateUnifiedTable`. Id slot gabungan berbentuk `${pool_id}:P01` dan DOM id dibuat dengan `domId()`.

## 6. Konvensi frontend (penting, sumber bug sebelumnya)

- `esc()` untuk teks dan atribut HTML (sudah meng-escape `'`).
- `jq(v)` untuk nilai di dalam handler inline, misalnya `onclick="f(${jq(name)})"`. Jangan pakai `esc().replace(...)`.
- Isi SMS, pengirim, nama pool, dan nilai dari agent selalu dianggap tidak tepercaya.
- Tema tersimpan di `localStorage['scpool_theme']`. `clearSessionState()` membersihkan semua data di memori saat logout.
- Cache-buster skrip di `index.html` (`/static/app.js?v=6`). Naikkan angkanya setiap mengubah `app.js`.
- CSV ekspor: sel dikutip, sel berawalan `= + - @` diberi pengaman, kecuali nomor telepon `+\d{6,18}`.
- Slot OFFLINE dan PORT_ERROR tampil sebagai offline, SIM_ERROR sebagai error.

## 7. Keamanan: yang sudah dikerjakan

Temuan: 1 kritis (XSS tersimpan lewat isi SMS), 4 sedang, beberapa rendah. Semua sudah diperbaiki dan punya tes di `tests/test_security_hardening.py`.

- Token sesi disimpan sebagai hash SHA-256 (`hash_session_token`). Semua sesi lama batal sekali.
- Kebijakan password lewat `password_problem` (tolak umum, hanya angka, memuat username). Dipakai di registrasi, ganti password, dan admin.
- Regex validasi memakai `\Z`, bukan `$`. Pola CMGL di allowlist: `^AT\+CMGL(=(\d|\"[A-Z ]{1,20}\"))?\Z`.
- Allowlist AT diperketat (`app/hardware/at_allowlist.py`) dan tidak bisa dirantai.
- Batas SMS real-time 300/menit per pemilik, kelebihannya dicatat WARN. Rate limiter punya batas jumlah kunci.
- CSP, HSTS, CORS dibatasi ke localhost dan domain publik. Domain publik wajib `http(s)://host[:port]`.
- Nama pool menolak `<` dan `>`.
- Log tidak boleh memuat isi SMS atau nomor penuh (I-12).

## 8. Uji E2E klien dan bug yang ditemukan

Dijalankan lewat Chromium headless dan agent simulasi. Cakupan: registrasi, persetujuan admin, login, unduh agent, dashboard kosong, dua unit terdeteksi (32 dan 16 port), SMS langsung, Fast OTP, drawer slot, USSD massal lintas pool, halaman SMS, ganti nama dan pindah port, hapus pool kosong, tema, ganti password, agent putus dan tersambung lagi, port dicabut, ekspor CSV, tampilan HP, logout, integritas data.

Bug asli yang diperbaiki:

1. Tema tidak tersimpan dan mode otomatis tidak diterapkan di awal.
2. Port yang dicabut tampil "Kosong". Sekarang Offline.
3. Setelah logout, data dashboard lama masih terlihat di balik jendela login.
4. Halaman Pengaturan di HP melebar ke samping.
5. Nomor telepon di CSV diberi apostrof di depannya.

## 9. Hasil uji terakhir

- `pytest -q`: 119 lulus (dicek ulang di Windows, 18 Okt 2026).
- `qa/client_e2e.py`: 141 pemeriksaan, 0 gagal.
- `qa/chassis.py`: 53 pemeriksaan tata letak lulus, tanpa error JS.
- `qa/sec.py`: 119 pemeriksaan, 3 "gagal" yang ternyata ekspektasi skrip (bukan bug).
- `qa/xss2.py`: tanpa temuan.

## 10. Skrip QA (folder `qa/`)

**Catatan:** folder `qa/` belum ada di repo ini. Skrip-skrip di bawah dibuat di sesi Claude web dan belum disalin ke sini.

Skrip ini ditulis di Linux dan masih memakai path `/tmp/...` (server di port 8765, data di `/tmp/qa_data`, pid di `/tmp/qa.pid`). Di Windows ubah path-nya dulu atau jalankan lewat WSL. Server uji dijalankan dengan:

```
SCPOOL_DATA_DIR=/tmp/qa_data SCPOOL_ADMIN_PASSWORD='AdminQA-12345' SCPOOL_LOCAL_AUTOLOGIN=0 SCPOOL_LOCAL_HARDWARE=0 python3 -m uvicorn app.main:app --host 127.0.0.1 --port 8765
```

- `client_e2e.py` menjalankan seluruh perjalanan klien (butuh Playwright dengan Chromium, jangan jalankan `playwright install` di mesin yang sudah punya browser).
- `simagent.py` adalah agent simulasi interaktif yang meniru modem.
- `run_e2e.sh` menyalakan ulang server bersih lalu menjalankan E2E.
- Jangan memakai `pkill -f` atau `pgrep -f` di lingkungan Claude. Pakai file pid.

## 11. Belum terverifikasi dan risiko yang diketahui

- **Belum pernah diuji dengan modem Foxcom asli.** Semua uji memakai agent simulasi. Pengelompokan per USB root di Windows bergantung pada informasi port dari OS dan harus dicoba di perangkat nyata. Ini prioritas berikutnya.
- Panel admin hanya diuji lewat jalur keamanan, belum sebagai perjalanan penuh.
- Notifikasi Telegram belum diuji karena butuh token bot asli dan akses ke `api.telegram.org`. Alurnya: isi token di pengaturan admin, kirim `/start` ke bot (Chat ID terikat otomatis), daftarkan akun baru, kartu persetujuan harus masuk dengan tombol Setujui/Tolak.
- CSP masih memakai `unsafe-inline` karena handler inline di frontend. Memperbaikinya berarti memindahkan semua handler ke `addEventListener`.
- Token agent juga diterima lewat query string, yang bisa muncul di log proxy. Token agent disimpan polos di database dan tidak kedaluwarsa.
- Registrasi membocorkan apakah sebuah username sudah ada.
- `SCPOOL_LOCAL_AUTOLOGIN` aktif secara default untuk akses lokal. Matikan di server publik.
- Tampilan di perangkat HP sungguhan belum dicoba, hanya viewport emulasi.

## 12. Langkah berikutnya yang disarankan

1. Uji agent dengan modem Foxcom sungguhan di Windows. Sebelum membuka port serial, ikuti aturan di `AGENTS.md` (tanya pemilik dulu).
2. Uji Telegram di mesin yang punya internet.
3. Putar token agent menjadi bisa kedaluwarsa atau di-hash, dan hentikan penerimaan token lewat query string.
4. Hilangkan `unsafe-inline` dari CSP.
5. Uji perjalanan penuh panel admin dengan pola yang sama seperti `qa/client_e2e.py`.

## 13. Catatan keamanan operasional

Token bot Telegram yang pernah ditempel di percakapan sebelumnya harus dicabut lewat BotFather (`/revoke`). Jangan menaruh token apa pun di repositori. Aplikasi tidak membaca file `.env`, variabel lingkungan harus diset di proses atau PM2.
