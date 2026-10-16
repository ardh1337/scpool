"""
Inisialisasi Modem Pool dan Pemetaan 32 Slot (Tahap M3 & M4)
Menyiapkan record database untuk modem pool Foxcom 32-Port, 32 Port fisik, dan 32 Slot fisik.
"""

from datetime import datetime, timezone
from sqlalchemy.orm import Session
from app.database.models import ModemPool, Device, Port, Slot, SlotStatus, init_db
from config.modem_pool_profile import FOXCOM_32_MAPPING


def ensure_pool_initialized(db: Session, pool_id: str = "foxcom_32", pool_name: str = "Foxcom 32-Port Pool") -> ModemPool:
    """Memastikan entitas pool, port, dan slot terdaftar di database."""
    now = datetime.now(timezone.utc)

    # 1. Pastikan ModemPool ada
    pool = db.query(ModemPool).filter(ModemPool.id == pool_id).first()
    if not pool:
        pool = ModemPool(
            id=pool_id,
            name=pool_name,
            port_count=32,
            description="Modem Pool Foxcom 32-Port dengan modul Quectel M26",
            created_at=now
        )
        db.add(pool)
        db.flush()

    # 2. Pastikan Device controller terdaftar
    device_id = "EXAR_XR21V1414_FOXCOM32"
    device = db.query(Device).filter(Device.id == device_id).first()
    if not device:
        device = Device(
            id=device_id,
            pool_id=pool.id,
            vid="04E2",
            pid="1414",
            description="Exar XR21V1414 USB UART Quad Controllers",
            created_at=now
        )
        db.add(device)
        db.flush()

    # 3. Sinkronisasi 32 Port dan 32 Slot
    for mapping in FOXCOM_32_MAPPING:
        # Cek Port
        port = db.query(Port).filter(Port.id == mapping.port_name).first()
        if not port:
            port = Port(
                id=mapping.port_name,
                device_id=device.id,
                pool_id=pool.id,
                baudrate=115200,
                is_active=True,
                last_seen_at=now
            )
            db.add(port)
            db.flush()

        # Cek Slot
        slot = db.query(Slot).filter(
            Slot.pool_id == pool.id,
            Slot.slot_index == mapping.slot_index
        ).first()
        if not slot:
            slot = Slot(
                pool_id=pool.id,
                slot_index=mapping.slot_index,
                physical_port_id=mapping.port_name,
                status=SlotStatus.EMPTY,
                updated_at=now
            )
            db.add(slot)

    db.commit()
    db.refresh(pool)
    return pool
