"""
Migrasi skema SQLite yang idempoten dan non-destruktif.

Database produksi sudah berisi data (SMS, SIM, akun). `create_all` tidak mengubah tabel yang sudah ada,
jadi perubahan skema ditangani di sini. Setiap langkah memeriksa kondisi aktual skema lebih dulu, sehingga
aman dijalankan berkali-kali pada database baru maupun lama. Versi skema disimpan di `PRAGMA user_version`.
"""

from __future__ import annotations

import logging

from sqlalchemy.engine import Engine
from sqlalchemy.schema import CreateIndex, CreateTable

from app.database.connection import Base
from app.database import models  # noqa: F401  (mendaftarkan semua tabel ke Base.metadata)

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 5

# Kolom baru bertipe NOT NULL butuh nilai bawaan saat ALTER TABLE ADD COLUMN.
_COLUMN_DEFAULTS = {("user_accounts", "is_active"): "1"}


def _table_exists(cur, name: str) -> bool:
    return cur.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone() is not None


def _columns(cur, table: str) -> set:
    return {row[1] for row in cur.execute(f'PRAGMA table_info("{table}")').fetchall()}


def _add_missing_columns(cur, engine: Engine) -> None:
    for table in Base.metadata.sorted_tables:
        if not _table_exists(cur, table.name):
            continue
        existing = _columns(cur, table.name)
        for col in table.columns:
            if col.name in existing:
                continue
            ddl_type = col.type.compile(dialect=engine.dialect)
            default = _COLUMN_DEFAULTS.get((table.name, col.name))
            clause = f" DEFAULT {default}" if default is not None else ""
            cur.execute(f'ALTER TABLE "{table.name}" ADD COLUMN "{col.name}" {ddl_type}{clause}')
            logger.info("Migrasi: kolom %s.%s ditambahkan", table.name, col.name)


def _slots_needs_rebuild(cur) -> bool:
    """Skema lama: slots.physical_port_id berstatus FK ke ports.id dan UNIQUE global."""
    for row in cur.execute("PRAGMA foreign_key_list(slots)").fetchall():
        if row[2] == "ports":
            return True
    for idx in cur.execute("PRAGMA index_list(slots)").fetchall():
        if idx[2] and idx[3] == "u":
            cols = [r[2] for r in cur.execute(f"PRAGMA index_info('{idx[1]}')").fetchall()]
            if cols == ["physical_port_id"]:
                return True
    return False


def _rebuild_slots(cur, engine: Engine) -> None:
    """
    Bangun ulang tabel slots (prosedur resmi SQLite: buat baru -> salin -> drop -> rename) agar FK/UNIQUE global
    pada physical_port_id hilang tanpa kehilangan data. Pemanggil sudah mematikan foreign_keys.
    """
    from sqlalchemy import MetaData
    slots = Base.metadata.tables["slots"]
    tmp_meta = MetaData()
    for fk in slots.foreign_keys:
        fk.column.table.to_metadata(tmp_meta)
    tmp = slots.to_metadata(tmp_meta, name="slots__new")
    for idx in list(tmp.indexes):
        tmp.indexes.discard(idx)

    old_cols = _columns(cur, "slots")
    col_list = ", ".join(f'"{c.name}"' for c in slots.columns if c.name in old_cols)

    cur.execute("BEGIN")
    try:
        cur.execute("DROP TABLE IF EXISTS slots__new")
        cur.execute(str(CreateTable(tmp).compile(dialect=engine.dialect)))
        cur.execute(f"INSERT INTO slots__new ({col_list}) SELECT {col_list} FROM slots")
        cur.execute("DROP TABLE slots")
        cur.execute("ALTER TABLE slots__new RENAME TO slots")
        for idx in slots.indexes:
            cur.execute(str(CreateIndex(idx).compile(dialect=engine.dialect)))
        violations = cur.execute("PRAGMA foreign_key_check").fetchall()
        if violations:
            raise RuntimeError(f"Migrasi slots menimbulkan pelanggaran FK: {violations[:5]}")
        cur.execute("COMMIT")
    except Exception:
        cur.execute("ROLLBACK")
        raise
    logger.info("Migrasi: tabel slots dibangun ulang (physical_port_id tidak lagi FK/UNIQUE global)")


def _ensure_indexes(cur, engine: Engine) -> None:
    existing = {r[0] for r in cur.execute("SELECT name FROM sqlite_master WHERE type='index'").fetchall()}
    for table in Base.metadata.sorted_tables:
        if not _table_exists(cur, table.name):
            continue
        for idx in table.indexes:
            if idx.name in existing:
                continue
            try:
                cur.execute(str(CreateIndex(idx).compile(dialect=engine.dialect)))
                logger.info("Migrasi: indeks %s dibuat", idx.name)
            except Exception as exc:  # data lama melanggar keunikan -> jangan hentikan startup
                logger.error("Migrasi: indeks %s gagal dibuat: %s", idx.name, exc)


def run_migrations(engine: Engine) -> None:
    raw = engine.raw_connection()
    try:
        # Atribut harus diset pada koneksi sqlite3 aslinya (bukan pembungkus pool) agar tiap pernyataan langsung
        # tersimpan; tanpa ini UPDATE dibuka dalam transaksi implisit yang dibatalkan saat koneksi ditutup.
        raw.driver_connection.isolation_level = None
        cur = raw.cursor()
        fresh = not _table_exists(cur, "user_accounts") and not _table_exists(cur, "slots")
        version = cur.execute("PRAGMA user_version").fetchone()[0]

        Base.metadata.create_all(bind=engine)
        if fresh:
            cur.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
            return
        if version >= SCHEMA_VERSION:
            return

        cur.execute("PRAGMA foreign_keys = OFF")
        try:
            _add_missing_columns(cur, engine)
            if _table_exists(cur, "slots") and _slots_needs_rebuild(cur):
                _rebuild_slots(cur, engine)
            _ensure_indexes(cur, engine)
            # Sesi lama dianggap sudah melewati scan awal agar label pesan lama tidak berubah. Hanya berjalan sekali
            # (dijaga PRAGMA user_version), jadi tidak menyentuh sesi baru.
            cur.execute("UPDATE sim_sessions SET initial_scan_at = started_at WHERE initial_scan_at IS NULL")
            # Pool lama dimiliki akun yang menunjuknya lewat user_accounts.pool_id.
            if _table_exists(cur, "user_accounts"):
                cur.execute(
                    "UPDATE modem_pools SET owner_id = (SELECT MIN(u.id) FROM user_accounts u "
                    "WHERE u.pool_id = modem_pools.id) WHERE owner_id IS NULL")
            cur.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        finally:
            cur.execute("PRAGMA foreign_keys = ON")
    finally:
        raw.close()
