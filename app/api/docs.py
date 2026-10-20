"""
Dokumentasi API (Swagger) di /docs, hanya untuk pengguna yang sudah login.

Semua teks dokumentasi ada di file ini supaya mudah dibaca dan diubah tanpa menyentuh kode endpoint. Endpoint baru
wajib diberi entri di ENDPOINTS (dicek oleh tests/test_api_docs.py).
"""

import json
from typing import Dict, List, Tuple

from fastapi import Depends, FastAPI, Request
from fastapi.openapi.docs import get_swagger_ui_html
from fastapi.openapi.utils import get_openapi
from fastapi.responses import JSONResponse, PlainTextResponse, RedirectResponse

from app.api.docs_detail import API_KEY, DETAILS, FLOWS, GENERAL, POOL, PUBLIK, SAMPLE_KEY, TOKEN
from app.auth.deps import get_current_user_optional

INTRO = """
API untuk membaca SIM, SMS, dan menjalankan USSD di modem pool Anda.

**Cara mulai**

1. Mencoba langsung: login di dashboard, lalu buka `/docs` (Swagger). Tombol **Try it out** di sana memakai sesi
   login Anda.
2. Dari aplikasi lain (bot, sistem registrasi, dll.): panggil `POST /api/auth/login`, ambil nilai `token` dari
   balasannya, lalu kirim di setiap permintaan sebagai header `Authorization: Bearer <token>`.
   Di Swagger, token bisa dipasang lewat tombol **Authorize**.

**Istilah**

- **Modem pool**: satu alat modem (mis. Foxcom 32 port). Satu akun bisa punya beberapa.
- **Port / slot**: satu tempat SIM di modem pool, dinomori mulai 1 (`slot_index`).
- **pool_id**: kode modem pool. Lihat di `GET /api/pools`. Boleh dikosongkan bila akun hanya punya satu modem pool.
- **Agent**: program di komputer yang tercolok ke modem pool. Tanpa agent yang menyala, port tampil **OFFLINE**
  dan perintah ke modem (USSD, baca SMS) tidak bisa dijalankan.
"""

# Hanya tampil di Swagger; file unduhan tidak perlu menautkan dirinya sendiri.
DOWNLOADS = """
**Unduh dokumentasi:** [Panduan API (.md)](/docs/scpool-api.md) untuk dibaca atau dibagikan ·
[OpenAPI (.json)](/openapi.json?download=1) untuk diimpor ke Postman/Insomnia.
"""

TAGS = [
    {"name": "Akun", "description": "Login, logout, daftar akun, dan ganti password."},
    {"name": "Modem Pool", "description": "Daftar modem pool milik Anda, ganti nama, pindah port, hapus."},
    {"name": "SIM & Port", "description": "Status setiap port dan SIM di dalamnya."},
    {"name": "SMS", "description": "SMS yang masuk ke SIM, termasuk kode OTP."},
    {"name": "USSD", "description": "Dial kode USSD (mis. cek nomor `*808#`) dan kode bawaan tiap operator."},
    {"name": "Otomasi HP", "description": "Untuk aplikasi di smartphone: ambil nomor, tunggu OTP, selesai. "
                                          "Memakai kunci API (`X-API-Key`)."},
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

    # Otomasi HP
    ("GET", "/api/otp/services"): (
        "Otomasi HP", "Daftar layanan",
        "Layanan yang dikenali beserta kata yang dicari di pengirim atau isi SMS. Layanan lain tetap bisa dipakai: "
        "namanya sendiri dijadikan kata kunci."),
    ("POST", "/api/otp/orders"): (
        "Otomasi HP", "Ambil nomor",
        "Mengunci satu nomor untuk satu layanan. Nomor yang diberikan: SIM aktif, agent-nya online, tidak sedang "
        "dikunci HP lain, dan belum pernah menerima OTP untuk layanan yang sama."),
    ("GET", "/api/otp/orders/{order_id}"): (
        "Otomasi HP", "Cek / tunggu OTP",
        "Status pesanan dan kode OTP. Isi `wait` (detik) agar server menunggu sampai OTP masuk, jadi HP tidak perlu "
        "bertanya berulang-ulang."),
    ("POST", "/api/otp/orders/{order_id}/finish"): (
        "Otomasi HP", "Selesai",
        "Melepas kunci nomor dan melaporkan hasilnya. Bila OTP sudah diterima, nomor tercatat sudah dipakai untuk "
        "layanan ini."),
    ("POST", "/api/otp/orders/{order_id}/cancel"): (
        "Otomasi HP", "Batal",
        "Melepas kunci nomor sebelum OTP masuk. Sertakan `reason` bila batal karena gagal (mis. nomor diblokir "
        "WhatsApp), supaya nomor itu tidak diberikan lagi ke HP lain."),
    ("GET", "/api/otp/orders"): (
        "Otomasi HP", "Riwayat pesanan", "Pesanan milik akun ini, yang terbaru di atas."),
    ("GET", "/api/otp/stats"): (
        "Otomasi HP", "Statistik per nomor",
        "Berapa kali tiap nomor berhasil atau gagal untuk satu layanan, alasan gagalnya, dan apakah nomor itu sedang "
        "bisa diberikan."),
    ("GET", "/api/otp/keys"): (
        "Otomasi HP", "Daftar kunci API", "Kunci API milik akun ini. Kunci lengkapnya tidak pernah ditampilkan lagi."),
    ("POST", "/api/otp/keys"): (
        "Otomasi HP", "Buat kunci API",
        "Membuat kunci untuk aplikasi HP. Kunci lengkap (`api_key`) **hanya ditampilkan sekali**; simpan baik-baik."),
    ("DELETE", "/api/otp/keys/{key_id}"): (
        "Otomasi HP", "Cabut kunci API", "Kunci langsung tidak bisa dipakai lagi. Pakai bila HP hilang atau kunci bocor."),

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


def _fields_table(fields) -> List[str]:
    return ["| Field | Arti |", "|---|---|", *[f"| `{name}` | {_cell(meaning)} |" for name, meaning in fields]]


def _apply_detail(operation: dict, detail: dict) -> None:
    """Tampilkan hak akses, arti field, catatan, serta contoh balasan dan error di Swagger."""
    if not detail:
        return
    extra = [f"**Akses:** {detail['access']}"]
    if detail.get("fields"):
        extra += ["", "**Arti field**", "", *_fields_table(detail["fields"])]
    if detail.get("notes"):
        extra += ["", f"**Catatan:** {detail['notes']}"]
    operation["description"] = operation.get("description", "") + "\n\n" + "\n".join(extra)

    body = operation.get("requestBody", {}).get("content", {}).get("application/json")
    if body is not None and "body" in detail:
        body["example"] = detail["body"]
    responses = operation.setdefault("responses", {})
    if "422" in responses:
        responses["422"]["description"] = "Format isian salah (mis. angka diisi teks). `detail` berisi field yang salah."
    if isinstance(detail.get("response"), (dict, list)):
        ok = responses.setdefault("200", {"description": "Berhasil"})
        ok["description"] = "Berhasil"
        ok.setdefault("content", {}).setdefault("application/json", {})["example"] = detail["response"]
    by_code: Dict[int, List[Tuple[str, str]]] = {}
    for code, message, meaning in detail.get("errors", []):
        by_code.setdefault(code, []).append((message, meaning))
    for code, items in by_code.items():
        lines = [f"- `{message}`" + (f": {meaning}" if meaning else "") for message, meaning in items]
        responses[str(code)] = {"description": "\n".join(lines),
                                "content": {"application/json": {"example": {"detail": items[0][0]}}}}


def build_openapi(app: FastAPI) -> dict:
    if app.openapi_schema:
        return app.openapi_schema
    schema = get_openapi(title="SCPool API", version=app.version, description=DOWNLOADS + INTRO + GENERAL,
                         routes=app.routes, tags=TAGS)
    for path, operations in schema["paths"].items():
        for method, operation in operations.items():
            entry = ENDPOINTS.get((method.upper(), path))
            if entry:
                tag, summary, description = entry
                operation.update(tags=[tag], summary=summary, description=description)
            _apply_detail(operation, DETAILS.get((method.upper(), path), {}))
    schema.setdefault("components", {})["securitySchemes"] = {
        "Token": {"type": "http", "scheme": "bearer", "description": "Token dari `POST /api/auth/login`."}}
    schema["security"] = [{"Token": []}]
    app.openapi_schema = schema
    return schema


_PLACE = {"path": "di URL", "query": "query (?nama=nilai)", "header": "header"}


def _cell(text) -> str:
    return " ".join(str(text if text is not None else "").split()).replace("|", r"\|")


def _body_fields(schema: dict, operation: dict) -> List[str]:
    content = operation.get("requestBody", {}).get("content", {}).get("application/json", {})
    ref = content.get("schema", {}).get("$ref", "")
    model = schema["components"]["schemas"].get(ref.rsplit("/", 1)[-1], {}) if ref else {}
    required = set(model.get("required", []))
    rows = []
    for name, prop in model.get("properties", {}).items():
        kind = prop.get("type") or "/".join(x["type"] for x in prop.get("anyOf", []) if x.get("type", "null") != "null")
        default = prop.get("default")
        note = "wajib" if name in required else "opsional" + (f", bawaan `{default}`" if default not in (None, "") else "")
        rows.append(f"| `{name}` | {kind} | {note} |")
    return rows


_PATH_EXAMPLES = {"{slot_index}": "1", "{pool_id}": POOL, "{user_id}": "2", "{operator_id}": "telkomsel",
                  "{order_id}": "41", "{key_id}": "3"}


def _curl(base_url: str, method: str, path: str, detail: dict) -> List[str]:
    url = path
    for placeholder, value in _PATH_EXAMPLES.items():
        url = url.replace(placeholder, value)
    lines = [f"curl -X {method} \"{base_url}{url}{detail.get('query', '')}\""]
    if detail.get("access") == API_KEY:
        lines.append(f"  -H \"X-API-Key: {SAMPLE_KEY}\"")
    elif detail.get("access") != PUBLIK:
        lines.append(f"  -H \"Authorization: Bearer {TOKEN}\"")
    if "body" in detail:
        lines += ["  -H \"Content-Type: application/json\"",
                  f"  -d '{json.dumps(detail['body'], ensure_ascii=False)}'"]
    return ["```bash", " \\\n".join(lines), "```"]


def to_markdown(schema: dict, base_url: str) -> str:
    """Panduan API lengkap tanpa Swagger. Endpoint dan parameternya diambil dari skema yang sama dengan /docs."""
    out = [f"# SCPool API (versi {schema['info']['version']})", "", f"Alamat server: `{base_url}`", "",
           INTRO.strip(), "", GENERAL.strip(), "", FLOWS.strip().replace("https://scpool.ardh1337.cloud", base_url),
           "", "# Daftar endpoint", ""]
    by_tag: Dict[str, List[Tuple[str, str, dict]]] = {}
    for path, operations in schema["paths"].items():
        for method, operation in operations.items():
            by_tag.setdefault((operation.get("tags") or ["Lainnya"])[0], []).append((method.upper(), path, operation))
    for tag in TAGS:
        items = by_tag.get(tag["name"])
        if not items:
            continue
        out += [f"## {tag['name']}", "", tag["description"], ""]
        for method, path, operation in items:
            detail = DETAILS.get((method, path), {})
            summary = ENDPOINTS.get((method, path), ("", path, ""))
            out += [f"### {summary[1]}", "", f"`{method} {path}`", "", summary[2], ""]
            if detail.get("access"):
                out += [f"**Akses:** {detail['access']}", ""]
            params = [p for p in operation.get("parameters", []) if p.get("in") != "cookie"]
            if params:
                out += ["**Parameter**", "", "| Parameter | Letak | Keterangan |", "|---|---|---|"]
                out += [f"| `{p['name']}` | {_PLACE.get(p['in'], p['in'])} | "
                        f"{'wajib' if p.get('required') else 'opsional'}"
                        f"{'. ' + _cell(p['description']) if p.get('description') else ''} |" for p in params]
                out.append("")
            fields = _body_fields(schema, operation)
            if fields:
                out += ["**Isi permintaan (JSON)**", "", "| Field | Tipe | Keterangan |", "|---|---|---|", *fields, ""]
            if detail:
                out += ["**Contoh permintaan**", "", *_curl(base_url, method, path, detail), ""]
            if isinstance(detail.get("response"), (dict, list)):
                out += ["**Contoh balasan** (`200`)", "", "```json",
                        json.dumps(detail["response"], indent=2, ensure_ascii=False), "```", ""]
            elif detail.get("response"):
                out += [f"**Balasan:** {detail['response']}", ""]
            if detail.get("fields"):
                out += ["**Arti field**", "", *_fields_table(detail["fields"]), ""]
            if detail.get("errors"):
                out += ["**Kemungkinan error**", "", "| Kode | Pesan (`detail`) | Arti |", "|---|---|---|"]
                out += [f"| `{code}` | {_cell(message)} | {_cell(meaning)} |" for code, message, meaning in detail["errors"]]
                out.append("")
            if detail.get("notes"):
                out += [f"**Catatan:** {detail['notes']}", ""]
            out += ["---", ""]
    return "\n".join(out) + "\n"


# Swagger UI dimuat dari CDN; CSP halaman ini saja yang dilonggarkan untuk CDN itu.
SWAGGER_CDN = "https://cdn.jsdelivr.net"
DOCS_CSP = (f"default-src 'self'; script-src 'self' 'unsafe-inline' {SWAGGER_CDN}; "
            f"style-src 'self' 'unsafe-inline' {SWAGGER_CDN}; img-src 'self' data:; connect-src 'self'; "
            "object-src 'none'; base-uri 'self'; frame-ancestors 'none'")


def install(app: FastAPI) -> None:
    app.openapi = lambda: build_openapi(app)

    @app.get("/openapi.json", include_in_schema=False)
    def openapi_json(download: bool = False, user=Depends(get_current_user_optional)):
        if user is None:
            return JSONResponse({"detail": "Login dulu untuk melihat dokumentasi API."}, status_code=401)
        headers = {"Content-Disposition": 'attachment; filename="scpool-openapi.json"'} if download else None
        return JSONResponse(build_openapi(app), headers=headers)

    @app.get("/docs/scpool-api.md", include_in_schema=False)
    def markdown_guide(request: Request, user=Depends(get_current_user_optional)):
        if user is None:
            return JSONResponse({"detail": "Login dulu untuk mengunduh dokumentasi API."}, status_code=401)
        return PlainTextResponse(to_markdown(build_openapi(app), str(request.base_url).rstrip("/")),
                                 media_type="text/markdown; charset=utf-8",
                                 headers={"Content-Disposition": 'attachment; filename="scpool-api.md"'})

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
