# SCPool

Dashboard inventaris SIM dan SMS/OTP untuk modem pool Foxcom 32-port. FastAPI + SQLite, mendukung banyak pool (multi-tenant) lewat agent Windows remote.

## Struktur
```
app/
  main.py            entry FastAPI, lifespan, /ws/agent
  core/              config (env SCPOOL_*), konstanta protokol
  database/          connection, models, migrations (user_version), bootstrap
  auth/              hashing, sesi, dependensi akses
  api/               router: auth, admin, monitoring, hardware, agent
  hardware/          profil pool, allowlist AT, profil operator
  modem/             transport serial, ATClient, port lock, USSD, SMS reader
  services/          logika bisnis (sync, ingest SMS, agent gateway, Telegram, ...)
  web/               dashboard (index.html, app.js, style.css)
agent/               agent_forwarder.py untuk komputer klien
scripts/             utilitas CLI; scripts/manual berisi diagnostik hardware
tests/               pytest (modem disimulasikan di tests/support/simulator)
docs/                spesifikasi dan invarian (I-1..I-13), hasil probe
deploy/              konfigurasi PM2
mobile/              aplikasi smartphone Android (SCPool Bot) untuk otomasi OTP & aksesibilitas
```

## Menjalankan
```
pip install -r requirements-dev.txt
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
pytest -q
```
Windows dengan modem terpasang: `run.bat`. Variabel lingkungan: lihat `.env.example`.

Saat pertama jalan, akun `admin` dibuat. Password dari `SCPOOL_ADMIN_PASSWORD`, atau acak di `data/initial_admin_password.txt`. Hapus file itu setelah dicatat.

## Deploy VPS
`pm2 start deploy/ecosystem.config.js` di belakang reverse proxy/tunnel. Pada server publik, `SCPOOL_LOCAL_AUTOLOGIN=0` dan `SCPOOL_LOCAL_HARDWARE=0` (sudah di ecosystem). Modem dibaca oleh agent klien lewat `/ws/agent`.

## Mode lokal (tanpa agent)
Bila server dijalankan di PC yang memegang modem pool, server membuka port COM sendiri, tanpa `.bat` agent dan tanpa
VPS. Logika agent (`agent/agent_forwarder.py`) berjalan di dalam proses server (`app/services/local_bridge.py`), jadi
deteksi pool, pemindaian paralel, SMS, dan USSD sama persis dengan agent. Modem lokal milik pemilik pool `foxcom_32`
(bawaan: admin).

1. Tutup agent `.bat` bila masih jalan (port COM hanya bisa dibuka satu program).
2. `run.bat` (di Windows `SCPOOL_LOCAL_HARDWARE` aktif secara bawaan).
3. Akses dari luar: Cloudflare Tunnel ke `http://127.0.0.1:8000`, mis.
   `cloudflared tunnel --url http://127.0.0.1:8000` atau tunnel bernama untuk domain sendiri.
   Pengunjung lewat tunnel tetap wajib login (auto-login hanya untuk `localhost` tanpa header proxy).

PC harus menyala terus dan database ada di PC itu (`data/`). Klien lain dengan modem di tempatnya sendiri tetap
memakai agent ke server VPS.

## Modem pool otomatis
Registrasi hanya username dan password. Jumlah modem pool dan port **tidak** diisi manual:

1. Agent membaca port serial USB di komputer klien dan mengelompokkannya per kabel USB yang masuk ke komputer
   (kunci `usb:<bus>-<port>`). Satu modem pool fisik = satu kelompok, berapa pun jumlah hub/chip di baliknya.
2. Server membuat satu unit per kelompok (`Modem Pool 1`, `Modem Pool 2`, ...) beserta slot sesuai jumlah port
   yang terdeteksi. Port baru yang dicolok kemudian masuk otomatis.
3. Nama tiap unit diganti kapan saja di **Pengaturan > Modem Pool Saya**.
4. Bila dua modem pool tergabung (mis. lewat satu hub USB eksternal), pisahkan dengan **Pindahkan Port**. Riwayat SIM
   dan SMS ikut berpindah, dan deteksi ulang tidak membatalkan pemindahan.

Port yang sudah punya slot tidak pernah berpindah slot atau unit karena deteksi ulang (I-3). Port yang hilang
ditandai OFFLINE, bukan dihapus. Batas: 16 unit per akun, 64 port per unit. Satu akun memakai satu agent;
untuk komputer berbeda gunakan akun berbeda.

## Agent klien
Akun yang sudah disetujui admin mengunduh agent dari dashboard (token sudah tertanam). Agent hanya mengirim perintah AT dalam allowlist dan tidak pernah menghapus SMS dari SIM.

## Keamanan

- Isi SMS, nama pengirim, nama modem pool dan nilai dari agent selalu diperlakukan tidak tepercaya. Di frontend, nilai di dalam handler inline memakai `jq()` (literal JSON yang di-escape), bukan `esc()` saja.
- Token sesi hanya disimpan sebagai hash SHA-256 di database. Setelah pembaruan ini semua pengguna perlu login ulang sekali.
- Password baru ditolak bila terlalu umum, hanya angka, atau memuat username.
- Domain publik harus berbentuk `http(s)://host[:port]`.
- Laju SMS real-time dari satu agent dibatasi 300/menit; kelebihannya dicatat sebagai event WARN.
- Semua regex validasi memakai `\Z` (bukan `$`) agar baris baru di ujung tidak lolos.
- Agent token sebaiknya tidak dikirim lewat query string (`?token=`) kecuali untuk unduhan pasca-registrasi; agent memakai header `Authorization`.
