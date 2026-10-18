# Spesifikasi Agent Coding: SIM Inventory dan SMS Dashboard

Dokumen ini adalah instruksi kerja untuk agent coding. Sumber kebenaran fungsional adalah **Blueprint v1.1** (`blueprint-sim-inventory-v1.1.md`). Dokumen ini menerjemahkannya menjadi aturan kerja, batas wewenang, urutan pekerjaan, dan kriteria lulus yang dapat diuji.

**Bila dokumen ini bertentangan dengan blueprint, atau ada hal yang tidak tercakup, berhenti dan tanyakan ke manusia. Jangan menebak.**

## 1. Misi

Bangun aplikasi Windows yang mengelola modem pool (16–64 port, saat ini Foxcom 32 port) berisi SIM yang diganti manual. Aplikasi mendeteksi SIM terpasang atau dicabut, membaca ICCID, mencari nomor bila memungkinkan, membaca SMS masuk, mengelompokkan SMS per sender, memberi nomor urut ke SIM yang terdeteksi, dan menampilkan semuanya di dashboard web lokal. Tidak ada perangkat lunak pengelola modem lain yang dipakai; semua dibangun sendiri.

## 2. Aturan Keras (Invariant)

Aturan ini tidak boleh dilanggar oleh kode apa pun. Setiap aturan harus punya minimal satu pengujian otomatis (lihat bagian 9).

| ID | Aturan |
|---|---|
| I-1 | SIM baru tidak pernah mewarisi nomor telepon, metadata, atau SMS dari SIM sebelumnya pada slot yang sama. |
| I-2 | Nomor yang tidak ditemukan berstatus BELUM DIKETAHUI atau NUMBER_NOT_RETURNED. Nomor tidak pernah ditebak dari ICCID atau IMSI, dan tidak diambil dari sesi lain. |
| I-3 | Dua lapis identitas: ID port fisik (tetap) dan No. Urut (label tampilan). Kaitan SMS dan sesi hanya memakai ID port fisik dan sesi, tidak pernah No. Urut. |
| I-4 | SMS tidak dihapus dari modem atau kartu sebelum tersimpan dan tervalidasi di database. Penghapusan di kartu nonaktif secara default. |
| I-5 | Satu perintah AT per port pada satu waktu. Notifikasi tak terminta (URC) dipisahkan dari balasan perintah. |
| I-6 | Maksimal satu sesi SIM aktif per slot. |
| I-7 | Bila hubungan slot, SIM, dan SMS tidak dapat dipastikan, tandai PERLU DITINJAU. Jangan memilih jawaban yang paling mungkin. |
| I-8 | Tidak ada data hilang diam-diam. Kegagalan baca, decode, simpan, atau kaitan harus tercatat dan terlihat di dashboard. |
| I-9 | Deduplikasi SMS memakai kunci komposit (sesi SIM, sender mentah, waktu SMSC, indeks modem bila ada, hash isi). Isi pesan saja tidak cukup. |
| I-10 | Satu port error tidak boleh menghentikan port lain. |
| I-11 | Pesan yang sudah ada di kartu saat sesi dimulai ditandai PESAN BAWAAN dan tidak dihitung sebagai SMS baru. |
| I-12 | Isi SMS, nomor lengkap, dan ICCID lengkap tidak ditulis ke log umum, pesan commit, atau fixture uji. Dashboard hanya melayani localhost atau jaringan lokal yang dibatasi. |
| I-13 | Hanya perintah AT dalam daftar izin (lampiran A) yang boleh dikirim ke modem. Perintah di luar daftar memerlukan persetujuan manusia (bagian 3). |

## 3. Batas Wewenang

**Agent boleh, tanpa bertanya:**

- Menulis kode, pengujian, dan dokumentasi di dalam repositori.
- Menjalankan pengujian dengan modem simulasi (bagian 8).
- Membaca dan menganalisis keluaran yang ditempelkan manusia dari perangkat nyata.

**Agent harus bertanya dulu ke manusia sebelum:**

- Membuka port serial perangkat nyata, bahkan untuk perintah baca saja. Manusia yang menjalankan skrip probe dan melaporkan hasilnya, kecuali manusia secara eksplisit mengizinkan agent mengakses port.
- Mengirim perintah yang mengubah konfigurasi modem (misalnya pengaturan deteksi SIM, format SMS, notifikasi SMS). Nilai lama dicatat dulu sebelum diubah supaya bisa dikembalikan.
- Mengirim perintah di luar daftar izin, terutama yang merusak atau permanen (reset pabrik, penulisan NV, format penyimpanan, pembaruan firmware, power off modul).
- Mengirim SMS atau melakukan panggilan. Itu di luar cakupan proyek ini.
- Memasang driver, mengubah pengaturan sistem Windows, atau menghentikan layanan lain.
- Menghapus data (database, log, cadangan) atau mengaktifkan penghapusan SMS di kartu.
- Memutuskan pustaka pihak ketiga setelah uji awal (blueprint bagian 5A), atau menambah dependensi dengan lisensi baru.
- Mengubah aturan keras, kriteria selesai, atau skema database yang sudah dipakai data nyata.

## 4. Keputusan Teknis

**Ditetapkan oleh blueprint:**

- Python dengan PySerial untuk komunikasi serial. Lapisan AT dibangun sendiri di atasnya.
- Pustaka python-gsmmodem (fork Python 3) hanya referensi, dan dipakai sebagian bila lolos uji awal.
- FastAPI atau setara untuk backend; SQLite untuk prototipe satu komputer (PostgreSQL hanya bila perlu); dashboard web lokal dengan HTML, CSS, dan JavaScript.
- Bahasa antarmuka dashboard: Indonesia. Nama kode, tabel, dan komentar kode: Inggris (asumsi; tanyakan bila manusia ingin lain).

**Rekomendasi bawaan (boleh diubah dengan alasan tertulis):**

- Satu thread pekerja per port dengan antrean perintah sendiri, bukan satu loop untuk semua port.
- SQLite dalam mode WAL, dengan satu penulis terpusat untuk menghindari konflik penulisan dari banyak thread.
- Semua waktu disimpan dalam UTC; tampilan mengikuti zona waktu lokal.

**Belum ditetapkan (tanyakan ke manusia di M0):** versi Python, cara menjalankan di Windows (manual atau layanan), UI tanpa framework atau dengan framework, dan apakah satu atau beberapa modem pool aktif bersamaan.

## 5. Struktur Repositori

```
sim-inventory/
  app/
    main
    api/        (devices, slots, sims, sms, logs, review, pools)
    modem/      (com_scanner, at_client, sim_status, sms_reader, pdu)
    services/   (slot_manager, sim_change_detector, number_lookup,
                 sms_ingestion, sender_grouping, command_queue,
                 reconciler, numbering, pool_profiles, watchdog)
    database/   (models, repository, migrations)
    web/        (halaman dashboard, skrip, gaya)
  simulator/    (modem simulasi dan skenario)
  tools/        (skrip probe baca-saja untuk perangkat nyata)
  config/       (profil operator, profil modem pool, daftar izin AT)
  data/
  logs/
  tests/        (unit, skenario, kontrak simulator)
  docs/         (hasil probe, tabel kompatibilitas AT, keputusan)
  README
```

## 6. Model Domain Ringkas

Detail lengkap ada di blueprint bagian 6, 6A, dan 13. Ringkasan untuk implementasi:

**Entitas:** Device, Port, Slot, SIM Card, SIM Session, SMS Message, SMS Part, Sender Group, Operator Profile, Modem Pool Profile, Riwayat Penomoran, Event Log, Audit Akses.

**Status slot:** UNMAPPED, VERIFYING, OFFLINE, EMPTY (tampil KOSONG), SIM_PRESENT, READY, SIM_ERROR, PORT_ERROR, NEEDS_REVIEW. Status nomor terpisah: DIKETAHUI, BELUM DIKETAHUI, NUMBER_NOT_RETURNED, dengan sumber CNUM, USSD, atau MANUAL.

**Constraint database yang harus ditegakkan di tingkat skema, bukan hanya kode:**

- Satu sesi aktif per slot.
- Sesi merujuk tepat satu slot dan satu kartu (atau identitas "tidak teridentifikasi").
- SMS merujuk sesi, atau berstatus PERLU DITINJAU.
- Kunci unik deduplikasi pada SMS.
- Nomor urut unik per profil modem pool pada satu waktu.

**Penomoran urut:** hanya SIM yang terdeteksi yang dinomori, dari 1 tanpa lompatan saat pemindaian awal. Nomor dibekukan selama sesi pemindaian. Slot yang SIM-nya dicabut tetap KOSONG dengan nomor dipertahankan sampai SIM dipasang lagi. Port yang belum bernomor berstatus BELUM BERNOMOR sampai "Susun Ulang Nomor" dijalankan. Port error tidak dinomori.

## 7. Kontrak Lapisan Modem

Deskripsi perilaku, bukan kode. Agent menentukan bentuk implementasinya.

- **Transport:** membuka dan menutup port, mengirim byte, membaca baris dengan batas waktu. Dapat diganti antara port nyata dan modem simulasi tanpa mengubah lapisan di atasnya.
- **Klien AT:** mengirim satu perintah, menunggu balasan akhir (OK atau ERROR), dengan batas waktu per perintah. Menerjemahkan balasan menjadi hasil terstruktur (sukses, error modem, timeout, balasan tak terduga). Menyimpan balasan mentah untuk diagnosis. Notifikasi tak terminta masuk ke jalur terpisah.
- **Pemeriksa SIM:** membaca status SIM, ICCID (dengan validasi: 19–20 digit, awalan 89, buang karakter pengisi, checksum bila ada), dan IMSI hanya bila diperlukan. Menerapkan jeda stabilisasi sebelum perubahan dianggap sah.
- **Pembaca SMS:** mode teks atau PDU sesuai hasil probe; mengumpulkan bagian multipart; menyimpan dua waktu (waktu SMSC dan waktu terima aplikasi); memproses jenis khusus (status report, kelas 0, SMS sistem operator) tanpa menghentikan alur.
- **Watchdog:** periksa respons modem berkala; setelah beberapa kegagalan berturut-turut bangun ulang sesi serial; bila tetap gagal tandai OFFLINE atau PORT_ERROR. Kehilangan registrasi jaringan tidak memicu restart.

## 8. Modem Simulasi (Wajib Dibuat Lebih Dulu)

Agent tidak dapat bergantung pada perangkat nyata untuk pengujian harian, jadi simulator adalah fondasi pengujian. Simulator harus dapat diprogram dengan skenario dan menghasilkan perilaku berikut:

- SIM terpasang, dicabut, dan dipasang lagi (termasuk SIM yang sama dan SIM berbeda), dengan status berkedip sesaat.
- ICCID valid, ICCID tidak valid, dan ICCID tidak terbaca.
- AT+CNUM mengembalikan nomor, kosong, atau error tidak didukung.
- SMS tunggal, SMS multipart lengkap, multipart tidak lengkap, bagian datang tidak berurutan, karakter non-Latin, PDU rusak.
- Notifikasi SMS yang sama datang dua kali, dan dua SMS berisi sama yang sah.
- Pesan yang sudah ada di kartu saat SIM dipasang.
- Modem tidak merespons, timeout, balasan terpotong, port terputus mendadak, dan pulih kembali.
- Banyak modem simulasi sekaligus (sampai 64) dengan sebagian error.
- Beberapa "modem pool" berbeda jumlah port dan posisi fisik.

## 9. Strategi Pengujian

- Setiap aturan keras (I-1 sampai I-13) dipetakan ke minimal satu pengujian yang diberi nama sesuai ID-nya, misalnya "I-4 pesan tidak dihapus sebelum tersimpan".
- Pengujian skenario memakai simulator untuk seluruh alur: deteksi SIM, sesi, penomoran, rekonsiliasi startup, SMS, deduplikasi.
- Pengujian khusus invarian pada penomoran: cabut SIM di tengah urutan, pasang lagi, Susun Ulang Nomor, ganti jenis modem pool.
- Pengujian perangkat nyata adalah skrip manual dengan daftar periksa. Hasilnya disimpan di `docs/` tanpa data pribadi, dan tidak dijalankan otomatis.
- Data uji seluruhnya sintetis. Tidak ada SMS, nomor, atau ICCID asli di repositori.

## 10. Rencana Kerja Bertahap

Setiap tahap punya **gerbang manusia**: agent berhenti, melaporkan, dan menunggu persetujuan sebelum lanjut. Tahap mengikuti Tahap 1–9 di blueprint bagian 17.

| Tahap | Pekerjaan | Lulus bila | Gerbang manusia |
|---|---|---|---|
| M0 | Kerangka proyek, skema database awal, modem simulasi, kerangka pengujian | Pengujian dasar berjalan; simulator menjalankan skenario bagian 8; semua pertanyaan "belum ditetapkan" di bagian 4 terjawab | Setujui keputusan teknis |
| M1 | Skrip probe baca-saja di `tools/` untuk daftar port, VID:PID, lokasi USB, dan identitas modul | Skrip hanya memakai perintah daftar izin; keluarannya tidak memuat data pribadi | Manusia menjalankan skrip pada satu slot dan menempelkan hasil |
| M2 | Analisis hasil probe; profil modem pool; pemetaan slot ke port AT; dasar pengurutan | Pemetaan terbukti dengan cabut/pasang SIM pada slot yang diuji; bertahan setelah restart | Manusia memverifikasi pemetaan fisik |
| M3 | Deteksi SIM, ICCID, sesi, dan status slot; uji pustaka (blueprint 5A) | Pengujian simulator lulus; ICCID terbaca benar pada satu slot nyata | Manusia melaporkan hasil uji satu slot; keputusan pustaka |
| M4 | Penomoran urut, KOSONG, PORT_ERROR, Susun Ulang Nomor, profil modem pool | Semua pengujian penomoran lulus; jumlah nomor sama dengan jumlah SIM terdeteksi | Persetujuan perilaku penomoran |
| M5 | Pencarian nomor (CNUM, USSD per profil operator, input manual) | Setiap hasil punya sumber yang benar; kegagalan berstatus BELUM DIKETAHUI | Manusia memverifikasi pada beberapa operator |
| M6 | Pembacaan SMS, multipart, deduplikasi, pesan bawaan, aturan baca-simpan-hapus | I-4, I-9, I-11 lulus di simulator; tidak ada pesan hilang pada skenario uji | Uji satu slot nyata dengan SMS uji dari manusia |
| M7 | Pengelompokan sender dan label | Aturan normalisasi konservatif lulus pengujian; sender asli tersimpan | - |
| M8 | API dan dashboard (halaman A sampai H blueprint bagian 12), peran pengguna, audit akses, masking | Semua halaman berfungsi dengan data simulasi; I-12 lulus | Manusia meninjau tampilan |
| M9 | Skala bertahap (1, 4, 8, 16, 32 slot), uji pemulihan, backup, retensi | Stabil di setiap tingkat selama periode pengamatan; rekonsiliasi benar setelah gangguan | Manusia menjalankan uji nyata dan menyetujui kenaikan tiap tingkat |

## 11. Format Laporan Agent

Setelah setiap tahap, laporkan secara singkat:

1. Apa yang selesai dan pengujian apa yang lulus (sebut ID aturan keras yang tercakup).
2. Aturan keras atau bagian blueprint yang tersentuh.
3. Asumsi yang diambil (setiap asumsi harus ditandai, bukan disembunyikan).
4. Hal yang perlu dilakukan atau dilaporkan manusia di gerbang.
5. Pertanyaan terbuka.

Commit kecil dan fokus, dengan pesan yang tidak memuat data pribadi. Jangan melakukan perubahan besar di luar tahap yang sedang dikerjakan.

## 12. Definisi Selesai

Sama dengan blueprint bagian 18. Ringkasnya: pemetaan terverifikasi dan bertahan setelah restart; tidak ada pewarisan nomor atau SMS pada pengujian cabut/pasang berulang; jumlah SMS tersimpan sama persis dengan jumlah diterima pada uji termasuk pesan panjang dan pesan identik; nomor gagal tidak ditebak; error dan akses isi SMS tercatat; stabil pada kapasitas target dan pulih otomatis setelah gangguan.

## 13. Pertanyaan Terbuka untuk Manusia (Awal)

1. Agent berjalan di mana, dan apakah ia boleh mengakses port serial komputer Windows langsung, atau semua hasil perangkat nyata ditempelkan manusia?
2. Versi Python dan cara menjalankan aplikasi (manual, tugas terjadwal, atau layanan Windows)?
3. Merek dan model modul dalam modem pool Foxcom (bila sudah diketahui)?
4. Apakah hanya satu modem pool yang aktif pada satu waktu?
5. Apakah label "No. Urut" disetujui, atau ingin nama lain?

## Lampiran A. Daftar Izin Perintah AT (Awal)

Daftar ini adalah titik mulai dan harus **diverifikasi terhadap manual AT resmi** untuk modul dan firmware yang dipakai sebelum dipercaya. Dukungan setiap perintah dicatat per model di `docs/` setelah diuji.

**Baca saja (boleh dikirim setelah port nyata diizinkan manusia):**

- Identitas: AT, ATI, AT+CGMI, AT+CGMM, AT+CGMR, AT+CGSN (IMEI).
- SIM: AT+CPIN?, AT+QCCID atau AT+CCID, AT+CIMI (hanya bila diperlukan), AT+QSIMSTAT?, AT+QSIMDET?.
- Nomor: AT+CNUM (dukungan belum terbukti).
- Jaringan: AT+COPS?, AT+CREG?, AT+CSQ.
- SMS (status): AT+CMGF?, AT+CPMS?, AT+CNMI?.

**Baca SMS (perhatian):** membaca pesan dapat mengubah status baca di kartu (misalnya pesan "belum dibaca" menjadi "sudah dibaca"). Gunakan opsi yang tidak mengubah status bila modem mendukungnya, dan catat perilaku sebenarnya saat uji. Perintah daftar atau baca pesan (AT+CMGL, AT+CMGR) hanya dijalankan sesuai rancangan baca-simpan-hapus (aturan I-4).

**Mengubah konfigurasi (perlu persetujuan manusia, nilai lama dicatat dulu):** pengaturan deteksi SIM (AT+QSIMDET=, AT+QSIMSTAT=), format SMS (AT+CMGF=), penyimpanan SMS (AT+CPMS=), notifikasi SMS (AT+CNMI=).

**Dilarang tanpa persetujuan eksplisit dan tertulis:** reset pabrik atau profil (AT&F, ATZ), penulisan atau penghapusan NV, format penyimpanan pengguna, operasi sistem berkas modul, pembaruan firmware, power off modul, perubahan konfigurasi USB, serta pengiriman SMS dan panggilan suara.

## Lampiran B. Urutan Prioritas Bila Ada Konflik

1. Aturan keras (bagian 2).
2. Blueprint v1.1.
3. Dokumen ini.
4. Rekomendasi bawaan (bagian 4).
5. Preferensi gaya kode agent.

Bila dua sumber di atasnya bertentangan, berhenti dan tanyakan ke manusia.
