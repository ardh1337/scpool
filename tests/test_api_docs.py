"""Dokumentasi API (/docs): hanya untuk yang sudah login, dan setiap endpoint punya penjelasan."""

from fastapi.testclient import TestClient
from starlette.routing import Route

from app.api.docs import ENDPOINTS, build_openapi
from app.main import app


def _anonymous_client():
    # Auto-login lokal hanya untuk host localhost; host publik memaksa jalur login biasa.
    return TestClient(app, base_url="http://scpool.example.com")


def test_docs_require_login():
    with _anonymous_client() as client:
        assert client.get("/openapi.json").status_code == 401
        page = client.get("/docs", follow_redirects=False)
        assert page.status_code in (302, 307) and page.headers["location"] == "/"


def test_docs_page_loads_swagger_for_logged_in_user():
    with TestClient(app) as client:  # localhost + auto-login = admin lokal
        page = client.get("/docs")
        assert page.status_code == 200
        assert "swagger-ui-bundle.js" in page.text
        assert "https://cdn.jsdelivr.net" in page.headers["content-security-policy"]
        schema = client.get("/openapi.json").json()
        assert schema["paths"]["/api/ussd/bulk"]["post"]["summary"] == "Dial USSD di banyak port sekaligus"


def test_other_pages_keep_strict_csp():
    with TestClient(app) as client:
        assert "cdn.jsdelivr.net" not in client.get("/").headers["content-security-policy"]


def test_every_endpoint_is_documented():
    app.openapi_schema = None
    schema = build_openapi(app)
    documented = {(m.upper(), p) for p, ops in schema["paths"].items() for m in ops}
    missing = documented - set(ENDPOINTS)
    assert not missing, f"Endpoint tanpa penjelasan di app/api/docs.py: {sorted(missing)}"
    stale = set(ENDPOINTS) - documented
    assert not stale, f"Entri dokumentasi untuk endpoint yang sudah tidak ada: {sorted(stale)}"


def test_docs_downloads_for_logged_in_user():
    with TestClient(app) as client:
        guide = client.get("/docs/scpool-api.md")
        assert guide.status_code == 200
        assert 'filename="scpool-api.md"' in guide.headers["content-disposition"]
        assert "### Dial USSD di banyak port sekaligus" in guide.text
        assert "`POST /api/ussd/bulk`" in guide.text
        spec = client.get("/openapi.json?download=1")
        assert 'filename="scpool-openapi.json"' in spec.headers["content-disposition"]
        assert "/api/slots" in spec.json()["paths"]


def test_docs_downloads_require_login():
    with _anonymous_client() as client:
        assert client.get("/docs/scpool-api.md").status_code == 401
        assert client.get("/openapi.json?download=1").status_code == 401


def test_every_endpoint_has_detail():
    from app.api.docs_detail import DETAILS
    assert set(DETAILS) == set(ENDPOINTS), sorted(set(ENDPOINTS) ^ set(DETAILS))
    assert all("access" in d and "response" in d for d in DETAILS.values())


def test_docs_text_has_no_leftover_code():
    """Teks dokumentasi tidak boleh memuat potongan kode Python yang ikut tertulis (mis. 'repr(' atau '+ \"\"\"')."""
    from app.api.docs_detail import DETAILS
    for key, detail in DETAILS.items():
        notes = str(detail.get("notes", ""))
        assert "repr(" not in notes and '"""' not in notes, key
