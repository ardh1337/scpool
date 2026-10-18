"""
Koneksi database SQLite.

- WAL + busy_timeout supaya thread API, auto-detector, dan gateway agent tidak saling mengunci.
- Foreign key dipaksa aktif di setiap koneksi (PRAGMA foreign_keys = ON).
"""

from contextlib import contextmanager

from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker, declarative_base

from app.core.config import settings

settings.data_dir.mkdir(parents=True, exist_ok=True)
DB_DIR = settings.data_dir
DB_PATH = settings.db_path
DATABASE_URL = f"sqlite:///{DB_PATH.as_posix()}"

engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False, "timeout": 30.0},
    echo=False,
)


@event.listens_for(engine, "connect")
def _set_sqlite_pragma(dbapi_connection, connection_record):
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode = WAL;")
    cursor.execute("PRAGMA foreign_keys = ON;")
    cursor.execute("PRAGMA busy_timeout = 30000;")
    cursor.close()


SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def get_db():
    """Dependency FastAPI: satu sesi per request."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@contextmanager
def session_scope():
    """Sesi pendek untuk thread latar belakang / gateway (commit manual oleh pemanggil)."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
