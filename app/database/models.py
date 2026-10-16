"""
Model Entitas Database Relasional
Menegakkan aturan keras (Invariants I-1 sampai I-13) pada tingkat skema tabel SQLite.
"""

from datetime import datetime, timezone
import enum
from typing import Optional
from sqlalchemy import (
    Column, Integer, String, Boolean, DateTime, ForeignKey,
    Text, Index, CheckConstraint, Enum as SQLEnum
)
from sqlalchemy.orm import relationship
from app.database.connection import Base


def utc_now() -> datetime:
    """Mengembalikan waktu UTC saat ini."""
    return datetime.now(timezone.utc)


class SlotStatus(str, enum.Enum):
    UNMAPPED = "UNMAPPED"
    VERIFYING = "VERIFYING"
    OFFLINE = "OFFLINE"
    EMPTY = "EMPTY"  # Tampil di UI sebagai KOSONG
    SIM_PRESENT = "SIM_PRESENT"
    READY = "READY"
    SIM_ERROR = "SIM_ERROR"
    PORT_ERROR = "PORT_ERROR"
    NEEDS_REVIEW = "NEEDS_REVIEW"


class NumberStatus(str, enum.Enum):
    DIKETAHUI = "DIKETAHUI"
    BELUM_DIKETAHUI = "BELUM DIKETAHUI"
    NUMBER_NOT_RETURNED = "NUMBER_NOT_RETURNED"


class NumberSource(str, enum.Enum):
    CNUM = "CNUM"
    USSD = "USSD"
    MANUAL = "MANUAL"


class SMSStatus(str, enum.Enum):
    RECEIVED = "RECEIVED"
    PESAN_BAWAAN = "PESAN BAWAAN"      # I-11: SMS yang sudah ada sebelum sesi aktif
    PERLU_DITINJAU = "PERLU DITINJAU"  # I-7: Hubungan tidak pasti


class ModemPool(Base):
    __tablename__ = "modem_pools"

    id = Column(String(50), primary_key=True)
    name = Column(String(100), nullable=False)
    port_count = Column(Integer, nullable=False, default=32)
    description = Column(String(255), nullable=True)
    created_at = Column(DateTime(timezone=True), default=utc_now, nullable=False)

    slots = relationship("Slot", back_populates="pool", cascade="all, delete-orphan")
    numberings = relationship("SlotNumbering", back_populates="pool", cascade="all, delete-orphan")


class Device(Base):
    __tablename__ = "devices"

    id = Column(String(100), primary_key=True)
    pool_id = Column(String(50), ForeignKey("modem_pools.id"), nullable=True)
    vid = Column(String(10), nullable=False)
    pid = Column(String(10), nullable=False)
    serial_number = Column(String(100), nullable=True)
    description = Column(String(255), nullable=True)
    created_at = Column(DateTime(timezone=True), default=utc_now, nullable=False)

    ports = relationship("Port", back_populates="device")


class Port(Base):
    """
    ID port fisik (misal COM11) adalah identitas tetap (I-3).
    """
    __tablename__ = "ports"

    id = Column(String(50), primary_key=True)  # contoh: "COM11"
    device_id = Column(String(100), ForeignKey("devices.id"), nullable=True)
    pool_id = Column(String(50), ForeignKey("modem_pools.id"), nullable=True)
    baudrate = Column(Integer, default=115200, nullable=False)
    is_active = Column(Boolean, default=True, nullable=False)
    last_error = Column(String(255), nullable=True)
    last_seen_at = Column(DateTime(timezone=True), default=utc_now, nullable=False)

    device = relationship("Device", back_populates="ports")
    slots = relationship("Slot", back_populates="port")


class Slot(Base):
    """
    Slot fisik pada sasis modem pool (1-32).
    """
    __tablename__ = "slots"

    id = Column(Integer, primary_key=True, autoincrement=True)
    pool_id = Column(String(50), ForeignKey("modem_pools.id"), nullable=False)
    slot_index = Column(Integer, nullable=False)  # 1..32 pada sasis
    physical_port_id = Column(String(50), ForeignKey("ports.id"), nullable=True, unique=True)
    status = Column(
        SQLEnum(SlotStatus, values_callable=lambda x: [e.value for e in x]),
        default=SlotStatus.EMPTY,
        nullable=False
    )
    updated_at = Column(DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False)

    pool = relationship("ModemPool", back_populates="slots")
    port = relationship("Port", back_populates="slots")
    sessions = relationship("SIMSession", back_populates="slot", cascade="all, delete-orphan")
    numberings = relationship("SlotNumbering", back_populates="slot")

    __table_args__ = (
        Index("uq_pool_slot_index", "pool_id", "slot_index", unique=True),
    )


class SlotNumbering(Base):
    """
    No. Urut (Label tampilan dinamis, I-3).
    Hanya SIM terdeteksi yang bernomor urut aktif.
    """
    __tablename__ = "slot_numberings"

    id = Column(Integer, primary_key=True, autoincrement=True)
    pool_id = Column(String(50), ForeignKey("modem_pools.id"), nullable=False)
    slot_id = Column(Integer, ForeignKey("slots.id"), nullable=False)
    sequence_number = Column(Integer, nullable=False)
    is_active = Column(Boolean, default=True, nullable=False)
    assigned_at = Column(DateTime(timezone=True), default=utc_now, nullable=False)
    revoked_at = Column(DateTime(timezone=True), nullable=True)

    pool = relationship("ModemPool", back_populates="numberings")
    slot = relationship("Slot", back_populates="numberings")

    __table_args__ = (
        # Nomor urut unik per profil modem pool pada satu waktu (I-3)
        Index(
            "uq_active_sequence_per_pool",
            "pool_id", "sequence_number",
            unique=True,
            sqlite_where=(is_active == True)
        ),
    )


class SIMCard(Base):
    """
    Data kartu SIM fisik.
    """
    __tablename__ = "sim_cards"

    id = Column(Integer, primary_key=True, autoincrement=True)
    iccid = Column(String(22), unique=True, nullable=False, index=True)
    imsi = Column(String(20), nullable=True)
    phone_number = Column(String(20), nullable=True)
    number_status = Column(
        SQLEnum(NumberStatus, values_callable=lambda x: [e.value for e in x]),
        default=NumberStatus.BELUM_DIKETAHUI,
        nullable=False
    )
    number_source = Column(
        SQLEnum(NumberSource, values_callable=lambda x: [e.value for e in x]),
        nullable=True
    )
    operator_name = Column(String(50), nullable=True)
    created_at = Column(DateTime(timezone=True), default=utc_now, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False)

    sessions = relationship("SIMSession", back_populates="sim_card")


class SIMSession(Base):
    """
    Siklus hidup SIM terpasang di slot hingga dicabut.
    Invarian I-6: Maksimal satu sesi SIM aktif per slot.
    """
    __tablename__ = "sim_sessions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    slot_id = Column(Integer, ForeignKey("slots.id"), nullable=False)
    sim_card_id = Column(Integer, ForeignKey("sim_cards.id"), nullable=True)
    started_at = Column(DateTime(timezone=True), default=utc_now, nullable=False)
    ended_at = Column(DateTime(timezone=True), nullable=True)  # NULL = aktif

    slot = relationship("Slot", back_populates="sessions")
    sim_card = relationship("SIMCard", back_populates="sessions")
    messages = relationship("SMSMessage", back_populates="session")

    __table_args__ = (
        # Penegakan I-6: Maksimal satu sesi SIM aktif per slot (ended_at IS NULL)
        Index(
            "uq_active_session_per_slot",
            "slot_id",
            unique=True,
            sqlite_where=(ended_at == None)
        ),
    )


class SMSMessage(Base):
    """
    Pesan SMS masuk.
    - I-4: Tidak dihapus sebelum tervalidasi di DB.
    - I-7: Hubungan tidak pasti wajib berstatus PERLU DITINJAU.
    - I-9: Deduplikasi dengan composite key.
    """
    __tablename__ = "sms_messages"

    id = Column(Integer, primary_key=True, autoincrement=True)
    session_id = Column(Integer, ForeignKey("sim_sessions.id"), nullable=True)
    slot_id = Column(Integer, ForeignKey("slots.id"), nullable=False)
    sender_raw = Column(String(50), nullable=False)
    sender_normalized = Column(String(50), nullable=False)
    smsc_timestamp = Column(DateTime(timezone=True), nullable=False)
    received_at = Column(DateTime(timezone=True), default=utc_now, nullable=False)
    body = Column(Text, nullable=False)
    body_hash = Column(String(64), nullable=False)  # SHA-256 dari teks mentah
    modem_index = Column(Integer, nullable=True)
    status = Column(
        SQLEnum(SMSStatus, values_callable=lambda x: [e.value for e in x]),
        default=SMSStatus.RECEIVED,
        nullable=False
    )
    is_multipart = Column(Boolean, default=False, nullable=False)
    part_count = Column(Integer, default=1, nullable=False)
    deleted_from_modem = Column(Boolean, default=False, nullable=False)  # I-4

    session = relationship("SIMSession", back_populates="messages")
    slot = relationship("Slot")
    parts = relationship("SMSPart", back_populates="sms_message", cascade="all, delete-orphan")

    __table_args__ = (
        # Penegakan I-7: SMS tanpa sesi wajib berstatus PERLU DITINJAU
        CheckConstraint(
            "(session_id IS NOT NULL) OR (status = 'PERLU DITINJAU')",
            name="chk_sms_session_or_review"
        ),
        # Penegakan I-9: Kunci komposit unik deduplikasi SMS
        Index(
            "uq_sms_deduplication",
            "session_id", "sender_raw", "smsc_timestamp", "modem_index", "body_hash",
            unique=True
        ),
    )


class SMSPart(Base):
    """
    Bagian pesan multipart / concantenated SMS (PDU).
    """
    __tablename__ = "sms_parts"

    id = Column(Integer, primary_key=True, autoincrement=True)
    sms_message_id = Column(Integer, ForeignKey("sms_messages.id"), nullable=True)
    part_index = Column(Integer, nullable=False)
    total_parts = Column(Integer, nullable=False)
    reference_number = Column(Integer, nullable=False)
    raw_pdu = Column(Text, nullable=True)
    part_body = Column(Text, nullable=False)
    received_at = Column(DateTime(timezone=True), default=utc_now, nullable=False)

    sms_message = relationship("SMSMessage", back_populates="parts")


class EventLog(Base):
    """
    Pencatatan kejadian sistem. I-12: Tidak ada nomor / ICCID lengkap di log umum.
    """
    __tablename__ = "event_logs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    timestamp = Column(DateTime(timezone=True), default=utc_now, nullable=False)
    level = Column(String(10), nullable=False)  # INFO, WARN, ERROR, CRITICAL
    category = Column(String(20), nullable=False)  # SIM, SMS, PORT, SYSTEM, AT
    slot_id = Column(Integer, nullable=True)
    port_id = Column(String(50), nullable=True)
    message = Column(String(255), nullable=False)
    details = Column(Text, nullable=True)


class OperatorProfile(Base):
    """
    Profil Operator Seluler (USSD & SMSC).
    Format dial USSD dan pola pengenalan nomor dapat diperbarui tanpa mengubah kode program (Bagian 4 Spec).
    """
    __tablename__ = "operator_profiles"

    id = Column(String(50), primary_key=True)  # contoh: "xl_axis", "telkomsel", "indosat", "tri", "smartfren"
    name = Column(String(100), nullable=False)
    ussd_code = Column(String(50), nullable=False)  # dial USSD untuk cek nomor
    iccid_prefixes = Column(String(255), nullable=True)  # koma dipisahkan, misal "896211,896212"
    operator_patterns = Column(String(255), nullable=True)  # koma dipisahkan, misal "XL,AXIS"
    description = Column(String(255), nullable=True)
    updated_at = Column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)


DEFAULT_OPERATOR_PROFILES = [
    {
        "id": "xl_axis",
        "name": "XL / AXIS",
        "ussd_code": "*808*7*8#",
        "iccid_prefixes": "896211,896212",
        "operator_patterns": "XL,AXIS",
        "description": "Dial langsung pengecekan nomor 4G untuk jaringan XL Axiata dan AXIS"
    },
    {
        "id": "telkomsel",
        "name": "Telkomsel",
        "ussd_code": "*808#",
        "iccid_prefixes": "896210,896204",
        "operator_patterns": "TELKOMSEL,T-SEL,SIMPATI,AS,LOOP",
        "description": "Dial cek nomor Telkomsel"
    },
    {
        "id": "indosat",
        "name": "Indosat Ooredoo",
        "ussd_code": "*123*30#",
        "iccid_prefixes": "896201,896221",
        "operator_patterns": "INDOSAT,IM3,MENTARI",
        "description": "Dial cek nomor Indosat Ooredoo Hutchison"
    },
    {
        "id": "tri",
        "name": "Tri (3)",
        "ussd_code": "*111*1#",
        "iccid_prefixes": "896289",
        "operator_patterns": "TRI,3",
        "description": "Dial cek nomor Tri"
    },
    {
        "id": "smartfren",
        "name": "Smartfren",
        "ussd_code": "*999#",
        "iccid_prefixes": "896209",
        "operator_patterns": "SMARTFREN,SMART",
        "description": "Dial cek nomor Smartfren"
    }
]


def init_db(engine_override=None):
    """Inisialisasi tabel di database dan seed data awal profil operator."""
    target_engine = engine_override or from_connection_engine()
    Base.metadata.create_all(bind=target_engine)

    from sqlalchemy.orm import sessionmaker
    SessionClass = sessionmaker(bind=target_engine)
    session = SessionClass()
    try:
        for prof in DEFAULT_OPERATOR_PROFILES:
            existing = session.query(OperatorProfile).filter(OperatorProfile.id == prof["id"]).first()
            if not existing:
                session.add(OperatorProfile(**prof))
        session.commit()
    except Exception:
        session.rollback()
    finally:
        session.close()


def from_connection_engine():
    from app.database.connection import engine
    return engine
