"""
Inisialisasi database saat startup: migrasi skema, seed profil operator, pool lokal, dan akun admin awal.

Kegagalan TIDAK ditelan diam-diam (versi lama menelan semua exception lalu startup "berhasil" tanpa
akun admin, tanpa profil operator, dan tanpa pengaturan).
"""

from __future__ import annotations

import logging
import os
from typing import Optional

from sqlalchemy.engine import Engine
from sqlalchemy.orm import sessionmaker

from app.auth.security import generate_agent_token, generate_password, hash_password
from app.core.config import ADMIN_ROLES, ROLE_SUPERADMIN, settings
from app.database.migrations import run_migrations
from app.database.models import ModemPool, OperatorProfile, SystemSetting, UserAccount
from app.hardware.operators import DEFAULT_OPERATOR_PROFILES

logger = logging.getLogger(__name__)


def _seed_settings(session) -> None:
    defaults = [
        ("telegram_bot_token", settings.telegram_bot_token, "Token Bot Telegram untuk notifikasi"),
        ("telegram_chat_id", settings.telegram_chat_id, "Chat ID Admin Telegram untuk persetujuan"),
        ("public_domain", settings.public_domain, "Domain publik akses remote"),
        ("registration_auto_approve", "false", "Otomatis setujui registrasi tanpa persetujuan manual (true/false)"),
    ]
    for key, value, desc in defaults:
        row = session.query(SystemSetting).filter(SystemSetting.key == key).first()
        if not row:
            session.add(SystemSetting(key=key, value=value, description=desc))
        elif not row.value and value:
            row.value = value


def _bootstrap_admin_password() -> str:
    """Password admin awal: env SCPOOL_ADMIN_PASSWORD, atau acak lalu ditulis ke file bermode 600."""
    if settings.admin_password:
        return settings.admin_password
    password = generate_password()
    path = settings.data_dir / "initial_admin_password.txt"
    try:
        path.write_text(f"username: admin\npassword: {password}\n", encoding="utf-8")
        os.chmod(path, 0o600)
        where = str(path)
    except OSError:
        where = "(gagal ditulis ke disk)"
    logger.warning("Akun admin dibuat dengan password acak. Lihat %s, segera ganti lalu hapus file tersebut.", where)
    return password


def _ensure_admin(session) -> None:
    """Pastikan ada minimal satu admin aktif. Admin yang diganti namanya tidak diduplikasi saat restart."""
    admins = session.query(UserAccount).filter(UserAccount.role.in_(ADMIN_ROLES)).order_by(UserAccount.id).all()
    if not admins:
        legacy = session.query(UserAccount).filter(UserAccount.username == "admin").first()
        if legacy is None:
            session.add(UserAccount(
                username="admin",
                password=hash_password(_bootstrap_admin_password()),
                role=ROLE_SUPERADMIN,
                pool_id=settings.local_pool_id,
                agent_token=generate_agent_token(),
                is_active=True,
            ))
            return
        legacy.role = ROLE_SUPERADMIN
        admins = [legacy]
    primary = admins[0]
    primary.is_active = True
    primary.agent_token = primary.agent_token or generate_agent_token()
    primary.pool_id = primary.pool_id or settings.local_pool_id


def init_db(engine_override: Optional[Engine] = None) -> None:
    from app.database.connection import engine as default_engine
    from app.services.pool_init import ensure_pool_initialized

    target = engine_override or default_engine
    run_migrations(target)

    session = sessionmaker(bind=target)()
    try:
        # Pool lokal harus ada sebelum akun admin yang merujuknya (FK).
        ensure_pool_initialized(session, pool_id=settings.local_pool_id)

        for profile in DEFAULT_OPERATOR_PROFILES:
            if not session.query(OperatorProfile).filter(OperatorProfile.id == profile["id"]).first():
                session.add(OperatorProfile(**profile))
        _seed_settings(session)
        _ensure_admin(session)
        session.flush()
        admin = session.query(UserAccount).filter(UserAccount.role.in_(ADMIN_ROLES)).order_by(UserAccount.id).first()
        local_pool = session.get(ModemPool, settings.local_pool_id)
        if admin and local_pool and local_pool.owner_id is None:
            local_pool.owner_id = admin.id
        session.commit()
    except Exception:
        session.rollback()
        logger.exception("Inisialisasi database gagal")
        raise
    finally:
        session.close()
