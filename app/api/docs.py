"""
Dokumentasi API (Swagger) di /docs, hanya untuk pengguna yang sudah login.

Semua teks dokumentasi ada di file ini supaya mudah dibaca dan diubah tanpa menyentuh kode endpoint. Endpoint baru
wajib diberi entri di ENDPOINTS (dicek oleh tests/test_api_docs.py).
"""

from typing import Dict, Tuple

from fastapi import Depends, FastAPI
from fastapi.openapi.docs import get_swagger_ui_html
from fastapi.openapi.utils import get_openapi
from fastapi.responses import JSONResponse, RedirectResponse

from app.auth.deps import get_current_user_optional

INTRO = """
API untuk membaca SIM, SMS, dan menjalankan USSD di modem pool Anda.

**Cara mulai**

1. Login di dashboard, lalu buka halaman ini. Tombol **Try it out** langsung memakai sesi login Anda.
2. Untuk aplikasi lain (bot, sistem registrasi, dll.): panggil `POST /api/auth/login`, ambil nilai `token` dari
   balasannya, lalu kirim di setiap permintaan sebagai header `Authorization: Bearer <token>`.
   Di halaman ini, token bisa dipasang lewat tombol **Authorize**.

**Istilah**

- **Modem pool**: satu alat modem (mis. Foxcom 32 port). Satu akun bisa punya beberapa.
- **Port / slot**: satu tempat SIM di modem pool, dinomori mulai 1 (`slot_index`).
- **pool_id**: kode modem pool. Lihat di `GET /api/pools`. Boleh dikosongkan bila akun hanya punya satu modem pool.
- **Agent**: program di komputer yang tercolok ke modem pool. Tanpa agent yang menyala, port tampil **OFFLINE**
  dan perintah ke modem (USSD, baca SMS) tidak bisa dijalankan.

**Status port**: `READY` (SIM aktif), `EMPTY` (tidak ada SIM), `SIM_ERROR` (SIM bermasalah),
`PORT_ERROR` (modem tidak merespons), `OFFLINE` (agent tidak terhubung).
"""

TAGS = [
    {"name": "Akun", "description": "Login, logout, daftar akun, dan ganti password."},
    {"name": "Modem Pool", "description": "Daftar modem pool milik Anda, ganti nama, pindah port, hapus."},
    {"name": "SIM & Port", "description": "Status setiap port dan SIM di dalamnya."},
    {"name": "SMS", "description": "SMS yang masuk ke SIM, termasuk kode OTP."},
    {"name": "USSD", "description": "Dial kode USSD (mis. cek nomor `*808#`) dan kode bawaan tiap operator."},
    {"name": "Agent", "description": "Status dan unduhan program agent untuk komputer modem pool."},
    {"name": "Admin", "description": "Khusus administrator: kelola pengguna dan pengaturan sistem."},
]

# (METHOD, path) -> (kelompok, judul, penjelasan)
ENDPOINTS: Dict[Tuple[str, str], Tuple[str, str, str]] = {
    # Akun
    ("POST", "/api/auth/login"): (
        "Akun", "Login",
        "Masuk dengan username dan password. Balasan berisi `token` untuk dipakai di header "
        "`Authorization: Bearer <token>`. Salah password 10 kali dalam 1 menit akan diblokir sementara."),
    ("POST", "/api/auth/logout"): ("Akun", "Logout", "Keluar dan membatalkan token yang sedang dipakai."),
    ("POST", "/api/auth/register"): (
        "Akun", "Daftar akun baru",
        "Membuat akun baru. Akun baru belum bisa dipakai sampai disetujui administrator."),
    ("GET", "/api/auth/me"): (
        "Akun", "Akun yang sedang login",
        "Data akun yang sedang login. Bila belum login, balasannya `{\"logged_in\": false}`."),
    ("GET", "/api/auth/credentials"): (
        "Akun", "Lihat info login", "Username dan peran akun Anda. Password tidak pernah ditampilkan."),
    ("PUT", "/api/auth/credentials"): (
        "Akun", "Ganti username atau password",
        "Wajib mengisi password lama (`current_password`). Password yang terlalu umum, hanya angka, atau memuat "
        "username akan ditolak."),

    # Modem Pool
    ("GET", "/api/pools"): (
        "Modem Pool", "Daftar modem pool",
        "Semua modem pool milik Anda beserta jumlah port, jumlah SIM aktif, dan apakah agent-nya menyala."),
    ("PUT", "/api/pools/{pool_id}"): ("Modem Pool", "Ganti nama modem pool", "Nama tidak boleh memuat `<` atau `>`."),
    ("POST", "/api/pools/{pool_id}/move-ports"): (
        "Modem Pool", "Pindahkan port ke modem pool lain",
        "Dipakai bila dua modem pool terbaca jadi satu. Isi `target_pool_id` untuk memindah ke modem pool yang "
        "sudah ada, **atau** `new_pool_name` untuk membuat modem pool baru. Riwayat SIM dan SMS ikut pindah."),
    ("DELETE", "/api/pools/{pool_id}"): (
        "Modem Pool", "Hapus modem pool kosong",
        "Hanya bisa bila semua port-nya sudah dipindahkan. Modem pool yang masih berisi port ditolak."),

    # SIM & Port
    ("GET", "/api/status"): (
        "SIM & Port", "Ringkasan modem pool",
        "Jumlah port per status (aktif, kosong, error, offline), jumlah SMS, dan status agent."),
    ("GET", "/api/slots"): (
        "SIM & Port", "Daftar port dan SIM",
        "Setiap port beserta statusnya. Nomor telepon dan ICCID hanya diisi untuk port `READY`."),
    ("POST", "/api/sync"): (
        "SIM & Port", "Pindai ulang semua port",
        "Meminta agent membaca ulang semua port sekarang, tanpa menunggu jadwal pemindaian berikutnya."),

    # SMS
    ("GET", "/api/sms"): (
        "SMS", "Daftar SMS",
        "SMS terbaru di satu modem pool, yang paling baru di atas. Isi `q` untuk mencari kata di isi atau pengirim "
        "SMS. Pakai `limit` dan `offset` untuk halaman berikutnya."),
    ("GET", "/api/slots/{slot_index}/sms"): (
        "SMS", "SMS di satu port", "SMS yang diterima SIM di port tertentu, yang paling baru di atas."),
    ("POST", "/api/poll-sms"): (
        "SMS", "Periksa SMS baru sekarang",
        "Meminta agent membaca SMS dari semua SIM sekarang, tanpa menunggu jadwal berikutnya."),
    ("GET", "/api/events"): (
        "SIM & Port", "Log kejadian",
        "Catatan kejadian terbaru: SIM dipasang atau dicabut, port bermasalah, dan sebagainya."),

    # USSD
    ("POST", "/api/slots/{slot_index}/ussd"): (
        "USSD", "Dial USSD di satu port",
        "Menjalankan kode USSD (bawaan `*808*7*8#`) di port tertentu dan menampilkan balasan operator. "
        "Bila balasannya memuat nomor telepon, nomor SIM otomatis tersimpan. Butuh beberapa detik."),
    ("POST", "/api/ussd/bulk"): (
        "USSD", "Dial USSD di banyak port sekaligus",
        "Isi `slot_indices` dengan nomor port, atau kosongkan untuk semua SIM aktif. Semua port di-dial bersamaan; "
        "hasil tiap port dikembalikan dalam satu balasan."),
    ("GET", "/api/operators"): (
        "USSD", "Kode USSD tiap operator", "Kode USSD cek nomor yang dipakai untuk setiap operator."),
    ("PUT", "/api/operators/{operator_id}"): (
        "USSD", "Ganti kode USSD operator", "Khusus administrator. Mengubah kode USSD cek nomor untuk satu operator."),

    # Agent
    ("GET", "/api/agent/status"): (
        "Agent", "Status agent", "Apakah agent sedang terhubung, sejak kapan, dan port apa saja yang terbaca."),
    ("GET", "/api/download/agent"): (
        "Agent", "Unduh agent",
        "Mengunduh program agent yang sudah berisi alamat server dan token akun Anda. "
        "`file_type=bat` untuk Windows (cukup klik dua kali), `py` untuk skrip Python."),

    # Admin
    ("GET", "/api/admin/users"): ("Admin", "Daftar pengguna", "Semua akun beserta status aktif (sudah disetujui atau belum) dan agent-nya."),
    ("POST", "/api/admin/users"): ("Admin", "Buat pengguna", "Membuat akun baru yang langsung aktif."),
    ("PUT", "/api/admin/users/{user_id}"): (
        "Admin", "Ubah pengguna", "Ganti password, peran, atau aktif/nonaktifkan akun."),
    ("DELETE", "/api/admin/users/{user_id}"): (
        "Admin", "Hapus pengguna",
        "Menghapus akun beserta modem pool, SIM, dan SMS miliknya. Akun administrator tidak bisa dihapus."),
    ("POST", "/api/admin/users/{user_id}/token"): (
        "Admin", "Buat ulang token agent",
        "Token agent lama langsung tidak berlaku. Pengguna harus mengunduh ulang agent."),
    ("POST", "/api/admin/users/{user_id}/approve"): ("Admin", "Setujui pendaftaran", "Mengaktifkan akun yang baru mendaftar."),
    ("POST", "/api/admin/users/{user_id}/reject"): ("Admin", "Tolak pendaftaran", "Menolak dan menghapus akun yang baru mendaftar."),
    ("GET", "/api/admin/pools"): ("Admin", "Semua modem pool", "Modem pool semua pengguna beserta pemiliknya."),
    ("GET", "/api/admin/settings"): ("Admin", "Lihat pengaturan sistem", "Bot Telegram, domain publik, dan persetujuan otomatis."),
    ("POST", "/api/admin/settings"): ("Admin", "Ubah pengaturan sistem", "Hanya isian yang dikirim yang diubah."),
    ("POST", "/api/admin/telegram/test"): ("Admin", "Tes notifikasi Telegram", "Mengirim pesan percobaan ke bot Telegram."),
}


def build_openapi(app: FastAPI) -> dict:
    if app.openapi_schema:
        return app.openapi_schema
    schema = get_openapi(title="SCPool API", version=app.version, description=INTRO, routes=app.routes, tags=TAGS)
    for path, operations in schema["paths"].items():
        for method, operation in operations.items():
            entry = ENDPOINTS.get((method.upper(), path))
            if entry:
                tag, summary, description = entry
                operation.update(tags=[tag], summary=summary, description=description)
    schema.setdefault("components", {})["securitySchemes"] = {
        "Token": {"type": "http", "scheme": "bearer", "description": "Token dari `POST /api/auth/login`."}}
    schema["security"] = [{"Token": []}]
    app.openapi_schema = schema
    return schema


# Swagger UI dimuat dari CDN; CSP halaman ini saja yang dilonggarkan untuk CDN itu.
SWAGGER_CDN = "https://cdn.jsdelivr.net"
DOCS_CSP = (f"default-src 'self'; script-src 'self' 'unsafe-inline' {SWAGGER_CDN}; "
            f"style-src 'self' 'unsafe-inline' {SWAGGER_CDN}; img-src 'self' data:; connect-src 'self'; "
            "object-src 'none'; base-uri 'self'; frame-ancestors 'none'")


def install(app: FastAPI) -> None:
    app.openapi = lambda: build_openapi(app)

    @app.get("/openapi.json", include_in_schema=False)
    def openapi_json(user=Depends(get_current_user_optional)):
        if user is None:
            return JSONResponse({"detail": "Login dulu untuk melihat dokumentasi API."}, status_code=401)
        return build_openapi(app)

    @app.get("/docs", include_in_schema=False)
    def swagger_ui(user=Depends(get_current_user_optional)):
        if user is None:
            return RedirectResponse("/")
        page = get_swagger_ui_html(
            openapi_url="/openapi.json", title="SCPool API - Dokumentasi", swagger_favicon_url="data:,",
            swagger_js_url=f"{SWAGGER_CDN}/npm/swagger-ui-dist@5.17.14/swagger-ui-bundle.js",
            swagger_css_url=f"{SWAGGER_CDN}/npm/swagger-ui-dist@5.17.14/swagger-ui.css",
            swagger_ui_parameters={"docExpansion": "none", "defaultModelsExpandDepth": 0,
                                   "persistAuthorization": True, "tryItOutEnabled": False},
        )
        page.headers["Content-Security-Policy"] = DOCS_CSP
        return page
