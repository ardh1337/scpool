"""
Isi detail dokumentasi API: hak akses, contoh permintaan dan balasan, arti field, error, dan contoh alur.

Contoh balasan diambil dari balasan asli server (database uji + agent simulasi), lalu token dan ID diganti contoh.
Dipakai oleh app/api/docs.py untuk Swagger (/docs) dan Panduan API (.md).
"""

from typing import Dict, Tuple

PUBLIK, LOGIN, ADMIN = "Publik (tanpa login)", "Wajib login", "Khusus administrator"
API_KEY = "Kunci API (`X-API-Key`) atau login"

TOKEN = "Qov_NrV9aTJSWgvtm7VeQsdUYIwt6OD0i39KMKRpSPw"
POOL = "u2_5840651d"
SAMPLE_KEY = "sk_Xy3k9QaLm2Vt8RbN0cWq5ZpD7hJ4uE1fG6sK3oY9iA"

ORDER_ACTIVE = {"order_id": 41, "status": "ACTIVE", "service": "whatsapp", "phone_number": "+6281234567890",
                "phone_national": "081234567890", "pool_id": POOL, "slot_index": 5, "device": "HP-07", "otp": None,
                "sms": None, "created_at": "2026-10-19T02:41:00+00:00", "expires_at": "2026-10-19T02:51:00+00:00",
                "seconds_left": 600}
ORDER_OTP = {**ORDER_ACTIVE, "status": "OTP_RECEIVED", "otp": "482913", "seconds_left": 571,
             "sms": {"id": 918, "sender": "WhatsApp", "body": "Kode WhatsApp Anda: 482-913. Jangan bagikan kode ini.",
                     "received_at": "2026-10-19T02:41:29+00:00"}}

ERR_KEY = (401, "Kunci API salah, sudah dicabut, atau akunnya nonaktif.",
           "Periksa kunci di header `X-API-Key`, atau buat kunci baru.")
ERR_401 = (401, "Otentikasi diperlukan. Silakan login terlebih dahulu.", "Token tidak dikirim, salah, atau sudah kedaluwarsa.")
ERR_403_ADMIN = (403, "Akses ditolak. Fitur ini khusus Administrator.", "Akun Anda bukan administrator.")
ERR_404_POOL = (404, "Modem pool tidak ditemukan.", "`pool_id` salah atau bukan milik akun Anda.")
ERR_404_USER = (404, "Pengguna tidak ditemukan.", "`user_id` tidak ada.")
ERR_PASSWORD = (400, "(alasan penolakan password)",
                "Password terlalu pendek, terlalu umum, hanya angka, atau memuat username.")
ERR_USERNAME = (400, "Username harus 3-20 karakter, hanya huruf kecil, angka, dan garis bawah (_).",
                "Format username salah.")

GENERAL = f"""
## Dasar penggunaan

### Alamat dan format

- Semua endpoint diawali `/api`, misalnya `https://scpool.ardh1337.cloud/api/slots`.
- Kirim dan terima data dalam format JSON. Untuk `POST` dan `PUT` yang punya isi, tambahkan header
  `Content-Type: application/json`.
- Waktu memakai format ISO 8601, misalnya `2026-10-18T09:15:02`. Waktu tanpa zona waktu adalah UTC.

### Login dan token

1. Panggil `POST /api/auth/login` dengan username dan password.
2. Simpan nilai `token` dari balasannya.
3. Kirim token itu di **setiap** permintaan berikutnya:

```
Authorization: Bearer {TOKEN}
```

- Token berlaku **7 hari** sejak login. Setelah itu login ulang.
- Token batal bila Anda logout, atau bila password diganti (token lain milik akun yang sama ikut batal).
- Browser yang sudah login di dashboard otomatis memakai cookie, jadi tidak perlu header.
- **Token login berbeda dengan token agent** (`scpool_...`). Token agent hanya untuk program agent, tidak bisa
  dipakai memanggil API.

### Hak akses

Setiap endpoint ditandai salah satu:

- **{PUBLIK}**: boleh dipanggil tanpa token.
- **{LOGIN}**: butuh token. Anda hanya bisa melihat dan mengubah modem pool milik akun sendiri.
- **{ADMIN}**: butuh token akun administrator.
- **{API_KEY}**: untuk aplikasi otomasi. Kirim header `X-API-Key: sk_...`. Kunci API hanya berlaku di endpoint
  kelompok *Otomasi HP*; tidak bisa dipakai untuk endpoint lain.

### Memilih modem pool (`pool_id`)

Banyak endpoint menerima `pool_id` (lewat query `?pool_id=...` atau isi JSON).

- Kosongkan bila akun hanya punya satu modem pool: yang pertama otomatis dipakai.
- Bila punya lebih dari satu, ambil daftarnya dari `GET /api/pools` lalu kirim `pool_id` yang dimaksud.
- Administrator **wajib** mengisi `pool_id` untuk melihat modem pool klien. Tanpa itu, yang dipakai adalah pool
  bawaan admin (`foxcom_32`).

### Format error

Bila gagal, balasan selalu berisi `detail` dengan pesan dalam bahasa Indonesia:

```json
{{"detail": "Username atau password salah."}}
```

| Kode | Arti |
|---|---|
| `400` | Isian salah atau permintaan tidak bisa diproses. Baca pesan `detail`. |
| `401` | Belum login, atau token salah/kedaluwarsa. |
| `403` | Sudah login tapi tidak berhak (mis. bukan admin, akun belum disetujui). |
| `404` | Data tidak ditemukan atau bukan milik Anda. |
| `409` | Perintah ke modem tidak bisa dijalankan karena agent tidak terhubung. |
| `422` | Format isian tidak sesuai (mis. angka diisi teks). `detail` berisi daftar field yang salah. |
| `429` | Terlalu banyak percobaan. Tunggu sebentar lalu coba lagi. |

### Batas permintaan

- Login: maksimal 10 kali gagal per menit dari satu alamat IP.
- Pendaftaran akun dibatasi per alamat IP.

## Arti nilai status

### Status port (`status`)

| Nilai | Arti |
|---|---|
| `READY` | Ada SIM aktif dan terbaca. Hanya port ini yang menampilkan nomor dan ICCID. |
| `EMPTY` | Tidak ada SIM. |
| `SIM_ERROR` | Ada SIM tapi tidak bisa dibaca (rusak, terkunci PIN, belum aktif). |
| `PORT_ERROR` | Modem di port ini tidak merespons. |
| `OFFLINE` | Agent tidak terhubung, jadi kondisi port tidak diketahui. Nomor disembunyikan. |

### Status nomor (`number_status`)

| Nilai | Arti |
|---|---|
| `DIKETAHUI` | Nomor sudah terbaca. |
| `BELUM DIKETAHUI` | Belum terbaca. Server mencoba otomatis lewat USSD, atau dial manual. |
| `NUMBER_NOT_RETURNED` | USSD sudah dicoba tapi balasan operator tidak memuat nomor. Coba kode USSD lain. |

### Status SMS (`status`)

| Nilai | Arti |
|---|---|
| `RECEIVED` | SMS masuk biasa ke SIM yang sedang terpasang. |
| `PESAN BAWAAN` | SMS yang sudah ada di SIM sebelum SIM terdeteksi. Biasanya SMS lama, bukan OTP baru. |
| `PERLU DITINJAU` | Tidak pasti SMS ini milik SIM yang mana (mis. SIM baru saja ditukar). |
"""

FLOWS = f"""
## Contoh alur

### 1. Ambil daftar nomor di modem pool

```bash
# Login
curl -X POST https://scpool.ardh1337.cloud/api/auth/login \\
  -H "Content-Type: application/json" \\
  -d '{{"username":"klien01","password":"Rahasia-Klien-77"}}'

# Daftar port; ambil yang status = READY dan phone_number tidak kosong
curl https://scpool.ardh1337.cloud/api/slots -H "Authorization: Bearer {TOKEN}"
```

Contoh dengan Python:

```python
import requests

BASE = "https://scpool.ardh1337.cloud"
token = requests.post(f"{{BASE}}/api/auth/login",
                      json={{"username": "klien01", "password": "Rahasia-Klien-77"}}).json()["token"]
auth = {{"Authorization": f"Bearer {{token}}"}}

for pool in requests.get(f"{{BASE}}/api/pools", headers=auth).json():
    slots = requests.get(f"{{BASE}}/api/slots", headers=auth, params={{"pool_id": pool["id"]}}).json()
    for s in slots:
        if s["status"] == "READY" and s["phone_number"]:
            print(pool["name"], s["slot_index"], s["phone_number"], s["operator_name"])
```

### 2. Tunggu OTP masuk ke SIM tertentu

1. Cari port milik nomor yang dipakai: `GET /api/slots`, cocokkan `phone_number`, catat `slot_index`.
2. Catat `id` SMS terbaru di port itu: `GET /api/slots/{{slot_index}}/sms?limit=1`.
3. Minta OTP ke layanan tujuan (WhatsApp, bank, dll.).
4. Ulangi `GET /api/slots/{{slot_index}}/sms?limit=5` tiap 3-5 detik sampai muncul SMS dengan `id` lebih besar.
5. Ambil kodenya dari `body` (mis. angka 4-8 digit).

Catatan: agent membaca SMS dari SIM kira-kira tiap 20 detik. Panggil `POST /api/poll-sms` untuk meminta
pembacaan segera.

### 3. Isi nomor yang belum terbaca

1. `GET /api/slots`: cari port `READY` dengan `number_status` = `BELUM DIKETAHUI`.
2. Lihat kode USSD cek nomor tiap operator di `GET /api/operators` (mis. `*808#` untuk Telkomsel).
3. `POST /api/ussd/bulk` dengan `slot_indices` port-port satu operator dan `code` operator itu. Ulangi per operator.
4. Nomor yang terbaca langsung tersimpan; panggil lagi `GET /api/slots` untuk melihatnya.
"""

# String biasa (bukan f-string): contoh Kotlin memakai kurung kurawal.
FLOWS += """
### 4. Otomasi HP: ambil nomor dan OTP

Persiapan sekali: login di dashboard, buat kunci API (`POST /api/otp/keys`, atau menu Pengaturan > Kunci API),
lalu tanam kuncinya di aplikasi HP. Satu kunci per HP memudahkan pencabutan bila HP hilang.

Alur setiap kali HP butuh nomor:

1. **Ambil nomor**: `POST /api/otp/orders` dengan `{"service": "whatsapp", "device": "HP-07"}`.
   Simpan `order_id`, isi nomor (`phone_national`) di aplikasi tujuan, lalu minta OTP.
2. **Tunggu OTP**: `GET /api/otp/orders/{order_id}?wait=30`. Ulangi sampai `status` = `OTP_RECEIVED`,
   atau sampai `status` menjadi `EXPIRED` (nomor tidak menerima OTP; ambil nomor lain).
3. **Pakai** nilai `otp`.
4. **Selesai**: `POST /api/otp/orders/{order_id}/finish`. Bila batal sebelum OTP masuk:
   `POST /api/otp/orders/{order_id}/cancel`.

Butuh kode kedua (kirim ulang)? Panggil `GET /api/otp/orders/{order_id}?wait=30&after=<sms.id sebelumnya>`.

Contoh Kotlin (Android, OkHttp + org.json), jalankan di luar main thread:

```kotlin
val base = "https://scpool.ardh1337.cloud"
val apiKey = "sk_..."  // kunci API untuk HP ini
val http = OkHttpClient.Builder().readTimeout(70, TimeUnit.SECONDS).build()
val json = "application/json".toMediaType()

fun call(method: String, path: String, body: JSONObject? = null): JSONObject {
    val req = Request.Builder().url(base + path).header("X-API-Key", apiKey)
        .method(method, body?.toString()?.toRequestBody(json)).build()
    http.newCall(req).execute().use { res ->
        val data = JSONObject(res.body!!.string())
        if (!res.isSuccessful) throw IllegalStateException(data.optString("detail"))
        return data
    }
}

val order = call("POST", "/api/otp/orders", JSONObject().put("service", "whatsapp").put("device", "HP-07"))
val id = order.getInt("order_id")
val nomor = order.getString("phone_national")      // isi ke aplikasi tujuan, lalu minta OTP
var otp: String? = null
while (otp == null) {
    val st = call("GET", "/api/otp/orders/$id?wait=30")
    when (st.getString("status")) {
        "OTP_RECEIVED" -> otp = st.getString("otp")
        "EXPIRED", "CANCELLED" -> error("Nomor tidak menerima OTP, ambil nomor lain")
    }
}
// ... pakai otp ...
call("POST", "/api/otp/orders/$id/finish")
```

Contoh `curl`:

```bash
KEY="sk_..."
curl -X POST "https://scpool.ardh1337.cloud/api/otp/orders" -H "X-API-Key: $KEY" \
  -H "Content-Type: application/json" -d '{"service":"whatsapp","device":"HP-07"}'
curl "https://scpool.ardh1337.cloud/api/otp/orders/41?wait=30" -H "X-API-Key: $KEY"
curl -X POST "https://scpool.ardh1337.cloud/api/otp/orders/41/finish" -H "X-API-Key: $KEY"
```
"""

# (METHOD, path) -> detail. Kunci yang tersedia: access, query, body, response, fields, errors, notes.
Detail = Dict[str, object]
DETAILS: Dict[Tuple[str, str], Detail] = {
    ("POST", "/api/auth/login"): {
        "access": PUBLIK,
        "body": {"username": "klien01", "password": "Rahasia-Klien-77"},
        "response": {"success": True, "token": TOKEN,
                     "user": {"id": 2, "username": "klien01", "role": "operator", "pool_id": None,
                              "agent_token": "scpool_86d6be0119d16fa6fbfe43a54abee4eb"}},
        "fields": [("token", "Token login. Kirim sebagai `Authorization: Bearer <token>`. Berlaku 7 hari."),
                   ("user.role", "`superadmin`, `admin`, atau `operator` (klien biasa)."),
                   ("user.agent_token", "Token untuk program agent, bukan untuk API.")],
        "errors": [(401, "Username atau password salah.", "Periksa lagi username dan password."),
                   (403, "Akun belum aktif atau dinonaktifkan oleh administrator.", "Tunggu persetujuan admin."),
                   (429, "Terlalu banyak percobaan login. Silakan tunggu 1 menit sebelum mencoba lagi.",
                    "Terlalu banyak salah password.")],
    },
    ("POST", "/api/auth/logout"): {
        "access": LOGIN,
        "response": {"success": True, "message": "Berhasil logout."},
        "notes": "Token yang dipakai langsung tidak berlaku.",
    },
    ("POST", "/api/auth/register"): {
        "access": PUBLIK,
        "body": {"username": "klien01", "password": "Rahasia-Klien-77"},
        "response": {"success": True, "is_active": False, "username": "klien01", "agent_token": None,
                     "download_script": None, "download_bat": None,
                     "message": "Pendaftaran berhasil! Akun klien01 menunggu persetujuan Administrator."},
        "fields": [("is_active", "`false` = menunggu persetujuan admin. Bila persetujuan otomatis aktif, `true` dan "
                                 "`agent_token` serta tautan unduh agent langsung terisi.")],
        "errors": [ERR_USERNAME, ERR_PASSWORD,
                   (400, "Username 'klien01' sudah digunakan.", "Pilih username lain."),
                   (429, "Terlalu banyak percobaan pendaftaran. Silakan tunggu beberapa saat.", "")],
        "notes": "Username: 3-20 karakter, huruf kecil, angka, dan garis bawah (_).",
    },
    ("GET", "/api/auth/me"): {
        "access": PUBLIK,
        "response": {"logged_in": True, "id": 2, "username": "klien01", "role": "operator", "pool_id": None,
                     "agent_token": "scpool_86d6be0119d16fa6fbfe43a54abee4eb"},
        "notes": "Tanpa token, balasannya `{\"logged_in\": false}` (bukan error). Cocok untuk mengecek token masih "
                 "berlaku.",
    },
    ("GET", "/api/auth/credentials"): {
        "access": LOGIN,
        "response": {"username": "klien01", "role": "operator", "updated_at": "2026-10-18T15:10:06.415226"},
        "errors": [ERR_401],
    },
    ("PUT", "/api/auth/credentials"): {
        "access": LOGIN,
        "body": {"current_password": "Rahasia-Klien-77", "password": "Rahasia-Baru-88"},
        "response": {"success": True, "message": "Password berhasil diperbarui.", "username": "klien01"},
        "fields": [("current_password", "Password saat ini. Wajib."),
                   ("password", "Password baru."),
                   ("username", "Opsional, bila ingin ganti username sekaligus.")],
        "errors": [(403, "Password saat ini salah.", "`current_password` keliru."), ERR_PASSWORD, ERR_USERNAME],
        "notes": "Token lain milik akun ini (perangkat lain) ikut batal. Token yang sedang dipakai tetap berlaku.",
    },

    ("GET", "/api/pools"): {
        "access": LOGIN,
        "response": [{"id": POOL, "name": "Modem Pool 1", "port_count": 32, "ready_count": 30, "offline_count": 0,
                      "auto_detected": True, "agent_online": True, "owner": None}],
        "fields": [("id", "`pool_id` untuk endpoint lain."),
                   ("port_count", "Jumlah port."), ("ready_count", "Jumlah port dengan SIM aktif."),
                   ("offline_count", "Jumlah port yang offline."),
                   ("agent_online", "`true` bila agent untuk modem pool ini sedang terhubung."),
                   ("owner", "Nama pemilik. Hanya diisi untuk administrator.")],
        "errors": [ERR_401],
    },
    ("PUT", "/api/pools/{pool_id}"): {
        "access": LOGIN,
        "body": {"name": "Foxcom Kantor"},
        "response": {"success": True, "id": POOL, "name": "Foxcom Kantor"},
        "errors": [ERR_404_POOL, (400, "Nama modem pool tidak boleh memuat karakter < atau >.", "")],
    },
    ("POST", "/api/pools/{pool_id}/move-ports"): {
        "access": LOGIN,
        "body": {"slot_indices": [17, 18, 19, 20], "new_pool_name": "Pool Cadangan"},
        "response": {"success": True, "target_pool_id": "u2_653ef3d6", "target_pool_name": "Pool Cadangan",
                     "moved": 4},
        "fields": [("slot_indices", "Nomor port yang dipindah (di modem pool asal)."),
                   ("target_pool_id", "Isi untuk memindah ke modem pool yang sudah ada."),
                   ("new_pool_name", "Isi untuk membuat modem pool baru. Pilih salah satu dengan `target_pool_id`."),
                   ("moved", "Jumlah port yang dipindah.")],
        "errors": [ERR_404_POOL, (400, "Isi salah satu: target_pool_id atau new_pool_name.", ""),
                   (400, "Satu modem pool maksimal 64 port.", "")],
        "notes": "Port yang dipindah ditaruh di urutan akhir modem pool tujuan.",
    },
    ("DELETE", "/api/pools/{pool_id}"): {
        "access": LOGIN,
        "response": {"success": True},
        "errors": [ERR_404_POOL,
                   (400, "Modem pool masih memiliki port. Pindahkan semua port ke unit lain dulu.", "")],
    },

    ("GET", "/api/status"): {
        "access": LOGIN,
        "query": "?pool_id=" + POOL,
        "response": {"pool_id": POOL, "pool_name": "Modem Pool 1", "total_slots": 32, "ready_count": 28,
                     "empty_count": 2, "error_count": 1, "offline_count": 1, "total_sms": 412,
                     "needs_review_sms": 0, "agent_online": True},
        "fields": [("error_count", "Jumlah port `SIM_ERROR` + `PORT_ERROR`."),
                   ("needs_review_sms", "Jumlah SMS berstatus `PERLU DITINJAU`.")],
        "errors": [ERR_401, ERR_404_POOL],
    },
    ("GET", "/api/slots"): {
        "access": LOGIN,
        "query": "?pool_id=" + POOL,
        "response": [
            {"id": 33, "slot_index": 1, "port_name": "COM7", "status": "READY", "display_label": "Slot 1",
             "phone_number": "+6281234567890", "number_status": "DIKETAHUI", "operator_name": "Telkomsel",
             "iccid": "89621000000000012345", "iccid_masked": "896210••••••••••2345", "sms_count": 1,
             "updated_at": "2026-10-18T15:10:06.508362"},
            {"id": 35, "slot_index": 3, "port_name": "COM4", "status": "EMPTY", "display_label": "Slot 3",
             "phone_number": None, "number_status": "BELUM DIKETAHUI", "operator_name": None, "iccid": None,
             "iccid_masked": None, "sms_count": 0, "updated_at": "2026-10-18T15:10:06.524115"}],
        "fields": [("slot_index", "Nomor port di modem pool, mulai 1. Dipakai di endpoint per port."),
                   ("port_name", "Nama port COM di komputer agent."),
                   ("status", "Lihat tabel status port."),
                   ("phone_number", "Nomor dalam format +62. `null` bila port tidak `READY` atau nomor belum terbaca."),
                   ("number_status", "Lihat tabel status nomor."),
                   ("operator_name", "Operator, ditebak dari ICCID."),
                   ("iccid", "Nomor seri kartu SIM (unik per kartu)."),
                   ("sms_count", "Jumlah SMS tersimpan untuk port ini.")],
        "errors": [ERR_401, ERR_404_POOL],
        "notes": "Semua port ikut dikirim, termasuk yang kosong. Saring sendiri bila hanya butuh SIM aktif.",
    },
    ("POST", "/api/sync"): {
        "access": ADMIN,
        "query": "?pool_id=" + POOL,
        "response": {"success": True, "mode": "agent", "message": "Permintaan pindai dikirim ke agent."},
        "errors": [ERR_403_ADMIN, (409, "Tidak ada agent online dan hardware lokal tidak aktif untuk pool ini.",
                                   "Agent modem pool itu sedang mati.")],
    },
    ("GET", "/api/events"): {
        "access": LOGIN,
        "query": "?limit=50",
        "response": [{"id": 2, "timestamp": "2026-10-18T15:10:06.532685", "level": "WARN", "category": "SIM",
                      "slot_id": 36, "port_id": "COM3", "message": "Agent melaporkan SIM_ERROR."}],
        "fields": [("level", "`INFO`, `WARN`, atau `ERROR`."),
                   ("category", "Sumber kejadian, mis. `SIM`, `PORT`, `SMS`.")],
        "errors": [ERR_401, ERR_404_POOL],
    },

    ("GET", "/api/sms"): {
        "access": LOGIN,
        "query": "?q=whatsapp&limit=20&offset=0",
        "response": [{"id": 1, "sender": "WhatsApp", "body": "Kode WhatsApp Anda: 482-913. Jangan bagikan kode ini.",
                      "status": "RECEIVED", "smsc_time": "2026-10-18T09:15:02",
                      "received_at": "2026-10-18T15:10:06.536127", "slot_index": 1, "port_name": "COM7"}],
        "fields": [("q", "Opsional. Cari kata di isi atau nama pengirim."),
                   ("limit", "Jumlah SMS per halaman, 1-500 (bawaan 50)."),
                   ("offset", "Lewati sejumlah SMS, untuk halaman berikutnya."),
                   ("smsc_time", "Waktu SMS dikirim menurut operator."),
                   ("received_at", "Waktu SMS tersimpan di server."),
                   ("status", "Lihat tabel status SMS.")],
        "errors": [ERR_401, ERR_404_POOL],
    },
    ("GET", "/api/slots/{slot_index}/sms"): {
        "access": LOGIN,
        "query": "?limit=5",
        "response": [{"id": 1, "sender": "WhatsApp", "body": "Kode WhatsApp Anda: 482-913. Jangan bagikan kode ini.",
                      "status": "RECEIVED", "smsc_time": "2026-10-18T09:15:02",
                      "received_at": "2026-10-18T15:10:06.536127"}],
        "fields": [("limit", "Jumlah SMS, 1-1000 (bawaan 200)."),
                   ("pool_id", "Wajib bila akun punya lebih dari satu modem pool.")],
        "errors": [ERR_401, (404, "Slot tidak ditemukan.", "`slot_index` tidak ada di modem pool itu.")],
        "notes": "Berisi semua SMS yang pernah masuk ke port ini, termasuk dari SIM sebelumnya bila SIM pernah "
                 "ditukar. Untuk OTP, pakai SMS terbaru saja.",
    },
    ("POST", "/api/poll-sms"): {
        "access": LOGIN,
        "query": "?pool_id=" + POOL,
        "response": {"success": True, "mode": "agent", "new_messages": 0,
                     "message": "Permintaan baca SMS dikirim ke agent; hasil muncul beberapa detik lagi."},
        "errors": [ERR_401, (409, "Tidak ada agent online dan hardware lokal tidak aktif untuk pool ini.",
                             "Agent sedang mati.")],
        "notes": "Tidak menunggu SMS terbaca. Panggil endpoint SMS lagi beberapa detik kemudian.",
    },

    ("POST", "/api/slots/{slot_index}/ussd"): {
        "access": LOGIN,
        "query": "?code=*808%23",
        "response": {"success": True, "response_text": "Nomor Anda adalah 6281234567890. Info paket: *363#",
                     "phone_number": "+6281234567890"},
        "fields": [("code", "Kode USSD. Tanda `#` ditulis `%23` di URL. Bawaan `*808*7*8#`."),
                   ("response_text", "Balasan operator apa adanya."),
                   ("phone_number", "Nomor yang ditemukan di balasan, atau `null`. Bila ada, otomatis tersimpan.")],
        "errors": [ERR_401, (400, "Slot tidak siap atau SIM tidak terpasang.", "Port bukan `READY`."),
                   (400, "Kode USSD tidak valid. Hanya angka, *, #, dan + (2-32 karakter) yang diizinkan.", "")],
        "notes": "Bila dial gagal (mis. operator tidak membalas), kode tetap `200` tapi balasannya "
                 "`{\"success\": false, \"message\": \"<alasan>\"}`. Satu dial butuh 2-15 detik.",
    },
    ("POST", "/api/ussd/bulk"): {
        "access": LOGIN,
        "body": {"slot_indices": [1, 2], "code": "*808#", "pool_id": POOL},
        "response": {"success": True, "total_dialed": 2, "results": [
            {"slot_index": 1, "port_name": "COM7", "success": True, "phone_number": "+6281234567890",
             "response_text": "Nomor Anda adalah 6281234567890. Info paket: *363#"},
            {"slot_index": 2, "port_name": "COM19", "success": False,
             "message": "Tidak ada balasan USSD dari jaringan sebelum waktu habis."}]},
        "fields": [("slot_indices", "Nomor port. Kosongkan (`null`) untuk semua port `READY`."),
                   ("code", "Kode USSD, bawaan `*808*7*8#`."),
                   ("results[].success", "Hasil per port. Bila `false`, alasannya di `message`.")],
        "errors": [ERR_401, ERR_404_POOL, (400, "Kode USSD tidak valid. Hanya angka, *, #, dan + (2-32 karakter) yang diizinkan.", "")],
        "notes": "Semua port di-dial bersamaan; balasan datang setelah port terlama selesai (biasanya 5-15 detik). "
                 "Port yang tidak `READY` dilewati.",
    },
    ("GET", "/api/operators"): {
        "access": LOGIN,
        "response": [{"id": "telkomsel", "name": "Telkomsel", "ussd_code": "*808#",
                      "iccid_prefixes": "896210,896204", "operator_patterns": "TELKOMSEL,T-SEL",
                      "description": "Dial cek nomor Telkomsel", "updated_at": "2026-10-18T15:10:06.116127"}],
        "fields": [("ussd_code", "Kode USSD cek nomor untuk operator ini."),
                   ("iccid_prefixes", "Awalan ICCID untuk mengenali operator.")],
        "errors": [ERR_401],
    },
    ("PUT", "/api/operators/{operator_id}"): {
        "access": ADMIN,
        "query": "?ussd_code=*808%23",
        "response": {"success": True, "id": "telkomsel", "name": "Telkomsel", "ussd_code": "*808#"},
        "errors": [ERR_403_ADMIN, (400, "Kode USSD wajib diisi.", ""),
                   (404, "Profil operator tidak ditemukan.", "")],
    },

    ("GET", "/api/otp/services"): {
        "access": API_KEY,
        "response": [{"service": "whatsapp", "keywords": ["whatsapp"]},
                     {"service": "google", "keywords": ["google", "g-"]},
                     {"service": "any", "keywords": []}],
        "fields": [("service", "Nama yang dikirim saat ambil nomor."),
                   ("keywords", "Kata yang dicari di pengirim atau isi SMS. `any` = SMS apa pun yang memuat kode.")],
        "errors": [ERR_KEY],
    },
    ("POST", "/api/otp/orders"): {
        "access": API_KEY,
        "body": {"service": "whatsapp", "device": "HP-07", "minutes": 10},
        "response": ORDER_ACTIVE,
        "fields": [("service", "Wajib. Nama layanan, huruf kecil (lihat `GET /api/otp/services`)."),
                   ("device", "Opsional. Nama HP, untuk dikenali di riwayat."),
                   ("minutes", "Opsional. Lama kunci nomor, 2-30 menit (bawaan 10). Lewat dari itu tanpa OTP, "
                               "pesanan menjadi `EXPIRED`."),
                   ("pool_id", "Opsional. Batasi ke satu modem pool."),
                   ("order_id", "Simpan untuk langkah berikutnya."),
                   ("phone_number / phone_national", "Nomor dalam format `+62...` dan `08...`."),
                   ("seconds_left", "Sisa waktu kunci nomor, dalam detik.")],
        "errors": [ERR_KEY,
                   (400, "Nama layanan harus 2-30 karakter: huruf kecil, angka, - atau _ (mis. whatsapp).", ""),
                   (409, "Tidak ada nomor tersedia untuk layanan 'whatsapp'. ...",
                    "Semua nomor sedang dipakai HP lain, sudah pernah dipakai untuk layanan ini, atau belum terbaca "
                    "nomornya. Coba lagi nanti atau tambah SIM."),
                   (409, "Agent modem pool sedang tidak terhubung, jadi OTP tidak bisa diterima.", ""),
                   (409, "Akun ini belum punya modem pool.", "")],
        "notes": "Agent otomatis membaca SMS port itu tiap ~1 detik selama pesanan aktif (agent versi baru), dan server "
                 "langsung meneruskan OTP ke HP yang sedang menunggu (`wait`).",
    },
    ("GET", "/api/otp/orders/{order_id}"): {
        "access": API_KEY,
        "query": "?wait=30",
        "response": ORDER_OTP,
        "fields": [("wait", "Opsional. Tunggu sampai OTP masuk, maksimal 60 detik. `0` = langsung jawab."),
                   ("after", "Opsional. `sms.id` OTP sebelumnya; server menunggu OTP yang lebih baru "
                             "(dipakai saat HP meminta kirim ulang kode)."),
                   ("status", "`ACTIVE` (menunggu), `OTP_RECEIVED`, `DONE`, `CANCELLED`, `EXPIRED`."),
                   ("otp", "Kode OTP yang sudah diekstrak, tanpa tanda `-` atau spasi."),
                   ("sms", "SMS asal OTP, untuk diperiksa bila perlu.")],
        "errors": [ERR_KEY, (404, "Pesanan tidak ditemukan.", "Bukan milik akun ini atau `order_id` salah.")],
        "notes": "Bila `wait` habis tanpa OTP, balasannya tetap `200` dengan `status` `ACTIVE`; panggil lagi. "
                 "Hanya SMS dari layanan itu yang masuk setelah pesanan dibuat yang dihitung sebagai OTP.",
    },
    ("POST", "/api/otp/orders/{order_id}/finish"): {
        "access": API_KEY,
        "response": {**ORDER_OTP, "status": "DONE", "seconds_left": 0},
        "errors": [ERR_KEY, (404, "Pesanan tidak ditemukan.", "")],
        "notes": "Panggil setelah HP selesai memakai OTP. Bila OTP belum masuk, sama dengan batal.",
    },
    ("POST", "/api/otp/orders/{order_id}/cancel"): {
        "access": API_KEY,
        "response": {**ORDER_ACTIVE, "status": "CANCELLED", "seconds_left": 0},
        "errors": [ERR_KEY, (404, "Pesanan tidak ditemukan.", ""),
                   (409, "OTP sudah diterima; nomor tercatat dipakai. Pakai /finish untuk menyelesaikan.", "")],
    },
    ("GET", "/api/otp/orders"): {
        "access": API_KEY,
        "query": "?status=ACTIVE&limit=20",
        "response": [ORDER_ACTIVE],
        "fields": [("status", "Opsional. Saring per status."), ("limit", "1-500, bawaan 50.")],
        "errors": [ERR_KEY, (400, "Status harus salah satu: ACTIVE, OTP_RECEIVED, DONE, CANCELLED, EXPIRED.", "")],
    },
    ("GET", "/api/otp/keys"): {
        "access": LOGIN,
        "response": [{"id": 3, "name": "HP-07", "prefix": "sk_Xy3k9Qa", "created_at": "2026-10-19T02:10:00+00:00",
                      "last_used_at": "2026-10-19T02:41:12+00:00", "revoked": False}],
        "fields": [("prefix", "Awal kunci, untuk mengenali kunci mana yang dipakai HP mana.")],
        "errors": [ERR_401],
    },
    ("POST", "/api/otp/keys"): {
        "access": LOGIN,
        "body": {"name": "HP-07"},
        "response": {"id": 3, "name": "HP-07", "prefix": "sk_Xy3k9Qa", "created_at": "2026-10-19T02:10:00+00:00",
                     "last_used_at": None, "revoked": False, "api_key": SAMPLE_KEY},
        "fields": [("api_key", "Kunci lengkap. Hanya muncul sekali di sini.")],
        "errors": [ERR_401, (400, "Maksimal 20 kunci aktif. Cabut kunci yang tidak dipakai.", "")],
        "notes": "Wajib login biasa (bukan kunci API), supaya kunci yang bocor tidak bisa membuat kunci baru.",
    },
    ("DELETE", "/api/otp/keys/{key_id}"): {
        "access": LOGIN,
        "response": {"success": True, "message": "Kunci 'HP-07' dicabut dan tidak bisa dipakai lagi."},
        "errors": [ERR_401, (404, "Kunci API tidak ditemukan.", "")],
    },
    ("GET", "/api/agent/status"): {
        "access": LOGIN,
        "response": {"pool_id": POOL, "is_online": True,
                     "info": {"online": True, "connected_at": "2026-10-18T15:10:06.484033+00:00", "port_count": 32,
                              "ports": ["COM7", "COM19", "COM4", "COM3"], "username": "klien01", "version": "1.2"}},
        "fields": [("info", "`null` bila agent tidak terhubung."), ("info.ports", "Port COM yang terbaca agent.")],
        "errors": [ERR_401],
    },
    ("GET", "/api/download/agent"): {
        "access": LOGIN + " (atau `token` agent di query, khusus setelah daftar)",
        "query": "?file_type=bat",
        "response": "(file scpool_agent.bat)",
        "errors": [(401, "Otentikasi atau token diperlukan untuk mengunduh file agen.", ""),
                   (403, "Agent token tidak valid atau akun belum aktif.", "")],
    },

    ("GET", "/api/admin/users"): {
        "access": ADMIN,
        "response": [{"id": 2, "username": "klien01", "role": "operator", "unit_count": 1,
                      "agent_token": "scpool_86d6be0119d16fa6fbfe43a54abee4eb", "is_active": True,
                      "agent_online": True, "created_at": "2026-10-18T15:10:06.393704",
                      "updated_at": "2026-10-18T15:10:06.393708"}],
        "fields": [("is_active", "`false` = belum disetujui atau dinonaktifkan."),
                   ("unit_count", "Jumlah modem pool milik akun.")],
        "errors": [ERR_401, ERR_403_ADMIN],
    },
    ("POST", "/api/admin/users"): {
        "access": ADMIN,
        "body": {"username": "operator02", "password": "Operator-Kuat-55", "role": "operator"},
        "response": {"success": True, "message": "Pengguna operator02 berhasil dibuat.", "user_id": 3,
                     "agent_token": "scpool_073654ef647ccfa3d0d04f4fe6aa0045"},
        "fields": [("role", "`operator` (klien), `admin`, atau `superadmin`.")],
        "errors": [ERR_403_ADMIN, ERR_USERNAME, ERR_PASSWORD,
                   (400, "Username 'operator02' sudah digunakan.", "")],
    },
    ("PUT", "/api/admin/users/{user_id}"): {
        "access": ADMIN,
        "body": {"is_active": False},
        "response": {"success": True, "message": "Data pengguna berhasil diperbarui."},
        "fields": [("password", "Opsional. Password baru."), ("role", "Opsional."),
                   ("is_active", "Opsional. `false` untuk menonaktifkan akun.")],
        "errors": [ERR_403_ADMIN, ERR_404_USER, ERR_PASSWORD,
                   (400, "Akun Administrator tidak boleh dinonaktifkan.", ""),
                   (400, "Anda tidak dapat menurunkan peran akun Anda sendiri.", "")],
    },
    ("DELETE", "/api/admin/users/{user_id}"): {
        "access": ADMIN,
        "response": {"success": True, "message": "Pengguna klien01 berhasil dihapus."},
        "errors": [ERR_403_ADMIN, ERR_404_USER,
                   (400, "Akun Administrator tidak boleh dihapus demi keamanan sistem.", "")],
        "notes": "Tidak bisa dibatalkan. Modem pool, SIM, dan riwayat SMS akun ikut terhapus.",
    },
    ("POST", "/api/admin/users/{user_id}/token"): {
        "access": ADMIN,
        "response": {"success": True, "agent_token": "scpool_45eabd18dcdee186b5d9043cb87d4ce5"},
        "errors": [ERR_403_ADMIN, ERR_404_USER],
    },
    ("POST", "/api/admin/users/{user_id}/approve"): {
        "access": ADMIN,
        "response": {"success": True, "message": "Akun klien01 berhasil disetujui dan diaktifkan."},
        "errors": [ERR_403_ADMIN, ERR_404_USER],
    },
    ("POST", "/api/admin/users/{user_id}/reject"): {
        "access": ADMIN,
        "response": {"success": True, "message": "Pendaftaran akun klien01 telah ditolak dan dibersihkan."},
        "errors": [ERR_403_ADMIN, ERR_404_USER, (400, "Akun Administrator tidak boleh ditolak.", "")],
    },
    ("GET", "/api/admin/pools"): {
        "access": ADMIN,
        "response": [{"id": POOL, "name": "Foxcom Kantor", "port_count": 32, "description": "Terdeteksi otomatis dari agent",
                      "owner": "klien01", "slot_count": 32, "agent_online": True}],
        "errors": [ERR_403_ADMIN],
    },
    ("GET", "/api/admin/settings"): {
        "access": ADMIN,
        "response": {"telegram_bot_token": "", "telegram_chat_id": "", "public_domain": "https://scpool.ardh1337.cloud",
                     "registration_auto_approve": "false"},
        "errors": [ERR_403_ADMIN],
    },
    ("POST", "/api/admin/settings"): {
        "access": ADMIN,
        "body": {"public_domain": "https://scpool.ardh1337.cloud", "registration_auto_approve": "false"},
        "response": {"success": True, "message": "Pengaturan sistem berhasil disimpan."},
        "fields": [("public_domain", "Alamat publik server, wajib diawali `http://` atau `https://`."),
                   ("registration_auto_approve", "`\"true\"` agar akun baru langsung aktif tanpa persetujuan."),
                   ("telegram_bot_token / telegram_chat_id", "Untuk notifikasi pendaftaran ke Telegram.")],
        "errors": [ERR_403_ADMIN,
                   (400, "Domain publik harus berbentuk http(s)://host[:port] tanpa path atau karakter khusus.", ""),
                   (400, "registration_auto_approve harus 'true' atau 'false'.", "")],
    },
    ("POST", "/api/admin/telegram/test"): {
        "access": ADMIN,
        "response": {"success": True, "message": "Pesan Telegram berhasil terkirim."},
        "errors": [ERR_403_ADMIN,
                   (400, "Token Bot atau Chat ID Telegram belum dikonfigurasi di Pengaturan Sistem.", "")],
    },
}
