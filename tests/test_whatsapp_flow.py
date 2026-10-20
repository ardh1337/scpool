"""Fokus WhatsApp: format OTP, pemisahan WhatsApp/WA Business, laporan hasil, blokir nomor, dan statistik."""

from datetime import datetime, timezone

import pytest

from app.database.connection import session_scope
from app.database.models import NumberOrder
from app.modem.otp import extract_otp
from app.services.agent_gateway import agent_gateway
from app.services.otp_orders import sms_matches_service
from tests.test_otp_orders import _order, _port_of, setup  # noqa: F401  (fixture dipakai ulang)

WA_SMS = [
    ("Kode WhatsApp Anda: 482-913. Jangan berikan kode ini ke siapa pun.", "482913"),
    ("<#> Kode WhatsApp Anda: 482-913\nAnda juga dapat mengetuk tautan ini untuk memverifikasi nomor telepon Anda: "
     "v.whatsapp.com/482913\nJangan bagikan kode ini dengan orang lain\n4sgLq1p5sV6", "482913"),
    ("<#> Your WhatsApp code: 482-913\nYou can also tap on this link to verify your phone: v.whatsapp.com/482913\n"
     "Don't share this code with others\n4sgLq1p5sV6", "482913"),
    ("Kode WhatsApp Business Anda: 120-334. Jangan berikan kode ini ke siapa pun.", "120334"),
    ("WhatsApp code 771-002", "771002"),
    ("Your WhatsApp Business code: 555 123", "555123"),
    ("Akun WhatsApp Anda sedang didaftarkan di perangkat baru. Jangan bagikan kode verifikasi Anda.", None),
    ("Your WhatsApp account is being registered on a new device. Do not share your verification code.", None),
]


@pytest.mark.parametrize("body,code", WA_SMS)
def test_whatsapp_sms_formats(body, code):
    assert extract_otp(body) == code


def test_whatsapp_and_business_are_separate_services():
    business = "Kode WhatsApp Business Anda: 120-334."
    personal = "Kode WhatsApp Anda: 482-913."
    assert sms_matches_service("whatsapp", "WhatsApp", personal)
    assert not sms_matches_service("whatsapp", "WhatsApp", business)
    assert sms_matches_service("whatsapp_business", "WhatsApp", business)
    assert not sms_matches_service("whatsapp_business", "WhatsApp", personal)


def _send(s, order, body):
    agent_gateway._on_sms_batch(s["owner"], {"port": _port_of(s, order), "messages": [
        {"index": 9, "sender": "WhatsApp", "body": body, "timestamp": datetime.now(timezone.utc).isoformat()}]}, None)


def _numbers_for(s, service="whatsapp"):
    """Ambil semua nomor yang masih bisa diberikan (lalu batalkan lagi tanpa alasan)."""
    got = []
    while True:
        r = _order(s, service)
        if r.status_code != 200:
            break
        got.append(r.json())
    for o in got:
        s["client"].post(f"/api/otp/orders/{o['order_id']}/cancel", headers=s["hdr"])
    return {o["phone_number"] for o in got}


def _cancel(s, order, reason):
    return s["client"].post(f"/api/otp/orders/{order['order_id']}/cancel", json={"reason": reason}, headers=s["hdr"])


@pytest.mark.parametrize("reason", ["banned", "registered_elsewhere", "under_review"])
def test_marked_results_are_flagged_but_number_stays_available(setup, reason):
    order = _order(setup).json()
    assert _cancel(setup, order, reason).json()["fail_reason"] == reason
    rows = {r["phone_number"]: r for r in
            setup["client"].get("/api/otp/stats", headers=setup["hdr"]).json()["numbers"]}
    row = rows[order["phone_number"]]
    assert (row["flags"], row["last_reason"], row["available"]) == ([reason], reason, True)
    assert order["phone_number"] in _numbers_for(setup)


def test_review_after_otp_is_flagged_not_locked(setup):
    """Ditinjau bisa muncul setelah OTP diisi: hasil gagal setelah OTP tidak membuat nomor dianggap terpakai."""
    order = _order(setup).json()
    _send(setup, order, "Kode WhatsApp Anda: 482-913.")
    setup["client"].get(f"/api/otp/orders/{order['order_id']}", headers=setup["hdr"])
    setup["client"].post(f"/api/otp/orders/{order['order_id']}/finish",
                         json={"result": "failed", "reason": "under_review"}, headers=setup["hdr"])
    assert order["phone_number"] in _numbers_for(setup)


def test_limited_number_rests(setup):
    order = _order(setup).json()
    _cancel(setup, order, "limited")
    assert order["phone_number"] not in _numbers_for(setup)


def test_two_no_sms_in_a_day_rests_the_number_but_one_does_not(setup):
    first = _order(setup).json()
    _cancel(setup, first, "no_sms")
    assert first["phone_number"] in _numbers_for(setup)
    again = next(o for o in (_order(setup).json(), _order(setup).json()) if o["phone_number"] == first["phone_number"])
    _cancel(setup, again, "no_sms")
    assert first["phone_number"] not in _numbers_for(setup)


def test_cancel_without_reason_does_not_count_against_the_number(setup):
    order = _order(setup).json()
    _cancel(setup, order, None)
    _cancel(setup, next(o for o in (_order(setup).json(), _order(setup).json())
                        if o["phone_number"] == order["phone_number"]), None)
    assert order["phone_number"] in _numbers_for(setup)


def test_unknown_reason_is_rejected(setup):
    order = _order(setup).json()
    r = _cancel(setup, order, "lupa")
    assert r.status_code == 400


def test_finish_reports_success_or_failure_after_otp(setup):
    order = _order(setup).json()
    _send(setup, order, "Kode WhatsApp Anda: 482-913.")
    assert setup["client"].get(f"/api/otp/orders/{order['order_id']}", headers=setup["hdr"]).json()["otp"] == "482913"
    done = setup["client"].post(f"/api/otp/orders/{order['order_id']}/finish",
                                json={"result": "failed", "reason": "wrong_code"}, headers=setup["hdr"]).json()
    assert (done["status"], done["result"], done["fail_reason"]) == ("DONE", "failed", "wrong_code")

    other = _order(setup).json()
    _send(setup, other, "Kode WhatsApp Anda: 111-222.")
    setup["client"].get(f"/api/otp/orders/{other['order_id']}", headers=setup["hdr"])
    ok = setup["client"].post(f"/api/otp/orders/{other['order_id']}/finish", headers=setup["hdr"]).json()
    assert ok["result"] == "success"


def test_stats_per_number(setup):
    order = _order(setup).json()
    _cancel(setup, order, "banned")
    other = _order(setup).json()
    _send(setup, other, "Kode WhatsApp Anda: 333-444.")
    setup["client"].get(f"/api/otp/orders/{other['order_id']}", headers=setup["hdr"])
    setup["client"].post(f"/api/otp/orders/{other['order_id']}/finish", headers=setup["hdr"])

    data = setup["client"].get("/api/otp/stats?service=whatsapp", headers=setup["hdr"]).json()
    rows = {r["phone_number"]: r for r in data["numbers"]}
    banned, used = rows[order["phone_number"]], rows[other["phone_number"]]
    assert (banned["failed"], banned["reasons"], banned["flags"], banned["available"]) == \
        (1, {"banned": 1}, ["banned"], True)
    assert (used["success"], used["available"], used["blocked_reason"]) == (1, False, "used")
    assert "banned" in data["reasons"]


def test_expired_order_counts_as_no_sms(setup):
    order = _order(setup).json()
    with session_scope() as db:
        db.get(NumberOrder, order["order_id"]).expires_at = datetime(2000, 1, 1, tzinfo=timezone.utc)
        db.commit()
    rows = {r["phone_number"]: r for r in
            setup["client"].get("/api/otp/stats", headers=setup["hdr"]).json()["numbers"]}
    assert rows[order["phone_number"]]["reasons"] == {"no_sms": 1}


def test_migration_adds_outcome_columns_to_existing_database(tmp_path):
    from sqlalchemy import create_engine, inspect, text
    from app.database.migrations import run_migrations

    engine = create_engine(f"sqlite:///{tmp_path / 'lama.db'}")
    run_migrations(engine)
    with engine.begin() as c:  # tiru database versi 4: tabel pesanan sudah ada, kolom hasil belum
        c.execute(text("INSERT INTO number_orders (user_id, iccid, phone_number, service, status, created_at, "
                       "expires_at) VALUES (1, '8962', '+62812', 'whatsapp', 'DONE', CURRENT_TIMESTAMP, "
                       "CURRENT_TIMESTAMP)"))
        c.execute(text("ALTER TABLE number_orders DROP COLUMN result"))
        c.execute(text("ALTER TABLE number_orders DROP COLUMN fail_reason"))
        c.execute(text("PRAGMA user_version = 4"))
    run_migrations(engine)
    columns = {col["name"] for col in inspect(engine).get_columns("number_orders")}
    assert {"result", "fail_reason"} <= columns
    with engine.begin() as c:
        assert c.execute(text("SELECT COUNT(*) FROM number_orders")).scalar() == 1
        assert c.execute(text("PRAGMA user_version")).scalar() == 5
