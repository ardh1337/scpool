# SCPool Bot — Aplikasi Otomasi Smartphone Android

Aplikasi Android native (Kotlin) berukuran ringan (~3 MB) untuk otomasi registrasi akun dan verifikasi SMS OTP dari modem pool **SCPool**.

Aplikasi ini menggunakan layanan **Accessibility Service** dan **Foreground Service** resmi Android untuk:
1. Memesan nomor baru dari modem pool secara otomatis melalui REST API (`/api/otp/orders`).
2. Membuka aplikasi target (WhatsApp, Telegram, Shopee, Google, Gojek, Dana, TikTok, dll.).
3. Mengisikan nomor telepon secara otomatis ke formulir pendaftaran.
4. Menunggu SMS OTP masuk dari modem pool secara instan dengan *long-polling* berlatensi milidetik (`GET /api/otp/orders/{id}?wait=30`).
5. Mengisikan kode OTP yang diterima secara otomatis ke kolom verifikasi.
6. Memutar suara notifikasi, getar, serta menyalin nomor/OTP ke clipboard sebagai *fallback*.
7. Menyediakan *floating widget* (tombol melayang) di atas aplikasi target dengan hitung mundur dan status *realtime*.

---

## 1. Persiapan Kunci API (X-API-Key)

1. Buka dashboard SCPool di browser (`https://scpool.ardh1337.cloud`).
2. Masuk ke menu **Pengaturan** -> **Kunci API Otomasi (X-API-Key)**.
3. Klik **Buat Kunci Baru**, beri nama perangkat (misal: `HP-01`).
4. Salin kunci yang dihasilkan (format: `sk_...`).

---

## 2. Cara Menginstal & Mengaktifkan di Smartphone

### Langkah 1: Pasang File APK
- Unduh dan pasang file `app-debug.apk` pada smartphone Android Anda.

### Langkah 2: Aktifkan Layanan Aksesibilitas
1. Buka **Pengaturan (Settings)** smartphone Android Anda.
2. Cari menu **Aksesibilitas (Accessibility)** -> **Layanan Terinstal (Installed Apps)**.
3. Pilih **SCPool Otomasi OTP** dan geser sakelar ke **Aktif**.
4. Berikan izin saat sistem menampilkan peringatan konfirmasi.

### Langkah 3: Berikan Izin Tombol Melayang (Overlay)
1. Buka aplikasi **SCPool Bot**.
2. Jika diminta, berikan izin **Tampil di Atas Aplikasi Lain (Display over other apps)** agar widget kontrol melayang dapat tampil di atas WhatsApp/Telegram.

### Langkah 4: Konfigurasi Bot
1. Ketuk ikon **Pengaturan (Roda Gigi)** di pojok kanan atas aplikasi.
2. Masukkan konfigurasi berikut:
   - **URL Server**: `https://scpool.ardh1337.cloud`
   - **Kunci API**: Tempelkan kunci `sk_...` Anda.
   - **Nama Perangkat**: Misal `HP-01` atau `HP-02`.
   - Pastikan opsi **Auto-Input Nomor Telepon** dan **Auto-Input Kode OTP** aktif.
3. Ketuk **Simpan Pengaturan**.

---

## 3. Cara Menjalankan Otomasi

1. Pada halaman utama aplikasi, pilih **Layanan Target** (misal: `WhatsApp`).
2. Ketuk tombol besar **Ambil Nomor & Otomasi**.
3. **Alur Otomatis Berjalan**:
   - Bot memesan nomor dari SIM aktif di modem pool.
   - Nomor langsung disalin ke clipboard HP.
   - WhatsApp otomatis terbuka.
   - Layanan Aksesibilitas mengisi nomor telepon dan menekan "Lanjut".
   - Widget melayang menampilkan status "Menunggu SMS Masuk...".
   - Begitu SMS diterima oleh modem pool, kode OTP langsung terisi ke WhatsApp.
   - HP bergetar dan membunyikan notifikasi.
   - Ketuk **Selesai** pada widget melayang atau aplikasi utama.

---

## 4. Cara Build APK

### Opsi A: Otomatis via GitHub Actions (Rekomendasi)
Setiap kali perubahan di direktori `mobile/android` di-push ke GitHub, workflow `.github/workflows/build-android.yml` akan otomatis meng-compile file APK dan menyediakannya di tab **Actions** -> **Artifacts** (bisa langsung diunduh dari browser smartphone).

### Opsi B: Build Lokal via Android Studio
1. Buka Android Studio.
2. Pilih **Open** dan arahkan ke direktori `mobile/android`.
3. Tunggu Gradle selesai mengunduh dependensi.
4. Pilih menu **Build** -> **Build Bundle(s) / APK(s)** -> **Build APK(s)**.
5. File APK akan dihasilkan di `app/build/outputs/apk/debug/app-debug.apk`.

### Opsi C: Build Lokal via Terminal / Command Line
```powershell
cd mobile/android
.\gradlew.bat assembleDebug
```
File APK akan berada di `app\build\outputs\apk\debug\app-debug.apk`.

---

## 5. Struktur Modul Proyek

```
mobile/android/
├── app/
│   ├── build.gradle.kts          # Konfigurasi dependensi Android & Kotlin
│   └── src/main/
│       ├── AndroidManifest.xml   # Definisi izin OS, layanan & aktivitas
│       ├── res/                  # Desain antarmuka, warna emerald dark, XML
│       └── java/cloud/ardh1337/scpool/
│           ├── ScpoolApp.kt               # Entrypoint inisialisasi aplikasi
│           ├── api/ScpoolApiClient.kt     # Client OkHttp untuk endpoint /api/otp/*
│           ├── data/Models.kt             # Data model pesanan, status, & SMS
│           ├── service/
│           │   ├── AutomationEngine.kt          # State machine alur pemesanan & polling
│           │   ├── ScpoolAccessibilityService.kt# Otomasi input form & tombol aplikasi
│           │   └── FloatingOverlayService.kt    # Widget melayang di atas aplikasi target
│           ├── ui/
│           │   ├── MainActivity.kt        # Layar utama dashboard kontrol di smartphone
│           │   └── SettingsActivity.kt    # Layar konfigurasi server & kunci API
│           └── util/
│               ├── Prefs.kt               # Penyimpanan pengaturan lokal HP
│               └── SoundHelper.kt         # Kontrol getar, suara, & clipboard
├── gradle/wrapper/               # Gradle wrapper 8.5
├── build.gradle.kts              # Konfigurasi root build
└── settings.gradle.kts           # Registrasi modul :app
```

