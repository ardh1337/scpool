"""
Profil Pemetaan Hardware Modem Pool Foxcom 32-Port (Tahap M2)
Memetakan posisi fisik Slot (1 s.d. 32) pada sasis ke port serial COM
berdasarkan topologi bus USB Controller Exar XR21V1414.

Terverifikasi melalui uji cabut SIM fisik pada Slot 17 (COM15, Chip 5 MI_00).
"""

from typing import Dict, List, Optional
from dataclasses import dataclass


@dataclass(frozen=True)
class SlotMapping:
    slot_index: int       # 1 s.d. 32 pada sasis fisik
    port_name: str        # COM3 s.d. COM34
    parent_hub: str       # Hub 9 (Chip 1-5) atau Hub 10 (Chip 6-8)
    hub_port: int         # 1 s.d. 5
    interface_mi: str     # MI_00, MI_02, MI_04, MI_06
    parent_id_prefix: str


# Pemetaan 32-Slot Foxcom terverifikasi
FOXCOM_32_MAPPING: List[SlotMapping] = [
    # Chip 1 (Hub 9, Port 1) -> Slot 1-4
    SlotMapping(1, "COM7", "Hub_#0009", 1, "MI_00", "d&7066c26&0"),
    SlotMapping(2, "COM19", "Hub_#0009", 1, "MI_02", "d&7066c26&0"),
    SlotMapping(3, "COM4", "Hub_#0009", 1, "MI_04", "d&7066c26&0"),
    SlotMapping(4, "COM3", "Hub_#0009", 1, "MI_06", "d&7066c26&0"),

    # Chip 2 (Hub 9, Port 2) -> Slot 5-8
    SlotMapping(5, "COM9", "Hub_#0009", 2, "MI_00", "d&2fe78588&0"),
    SlotMapping(6, "COM11", "Hub_#0009", 2, "MI_02", "d&2fe78588&0"),
    SlotMapping(7, "COM5", "Hub_#0009", 2, "MI_04", "d&2fe78588&0"),
    SlotMapping(8, "COM6", "Hub_#0009", 2, "MI_06", "d&2fe78588&0"),

    # Chip 3 (Hub 9, Port 3) -> Slot 9-12
    SlotMapping(9, "COM12", "Hub_#0009", 3, "MI_00", "d&1d2dd4e3&0"),
    SlotMapping(10, "COM20", "Hub_#0009", 3, "MI_02", "d&1d2dd4e3&0"),
    SlotMapping(11, "COM8", "Hub_#0009", 3, "MI_04", "d&1d2dd4e3&0"),
    SlotMapping(12, "COM10", "Hub_#0009", 3, "MI_06", "d&1d2dd4e3&0"),

    # Chip 4 (Hub 9, Port 4) -> Slot 13-16
    SlotMapping(13, "COM13", "Hub_#0009", 4, "MI_00", "d&a74243e&0"),
    SlotMapping(14, "COM17", "Hub_#0009", 4, "MI_02", "d&a74243e&0"),
    SlotMapping(15, "COM14", "Hub_#0009", 4, "MI_04", "d&a74243e&0"),
    SlotMapping(16, "COM18", "Hub_#0009", 4, "MI_06", "d&a74243e&0"),

    # Chip 5 (Hub 9, Port 5) -> Slot 17-20 (Slot 17 terbukti = COM15 via cabut fisik)
    SlotMapping(17, "COM15", "Hub_#0009", 5, "MI_00", "d&8458c67&0"),
    SlotMapping(18, "COM16", "Hub_#0009", 5, "MI_02", "d&8458c67&0"),
    SlotMapping(19, "COM21", "Hub_#0009", 5, "MI_04", "d&8458c67&0"),
    SlotMapping(20, "COM23", "Hub_#0009", 5, "MI_06", "d&8458c67&0"),

    # Chip 6 (Hub 10, Port 1) -> Slot 21-24
    SlotMapping(21, "COM25", "Hub_#0010", 1, "MI_00", "e&49a5f69&0"),
    SlotMapping(22, "COM22", "Hub_#0010", 1, "MI_02", "e&49a5f69&0"),
    SlotMapping(23, "COM24", "Hub_#0010", 1, "MI_04", "e&49a5f69&0"),
    SlotMapping(24, "COM26", "Hub_#0010", 1, "MI_06", "e&49a5f69&0"),

    # Chip 7 (Hub 10, Port 2) -> Slot 25-28
    SlotMapping(25, "COM30", "Hub_#0010", 2, "MI_00", "e&2d7b78cb&0"),
    SlotMapping(26, "COM27", "Hub_#0010", 2, "MI_02", "e&2d7b78cb&0"),
    SlotMapping(27, "COM28", "Hub_#0010", 2, "MI_04", "e&2d7b78cb&0"),
    SlotMapping(28, "COM29", "Hub_#0010", 2, "MI_06", "e&2d7b78cb&0"),

    # Chip 8 (Hub 10, Port 3) -> Slot 29-32
    SlotMapping(29, "COM31", "Hub_#0010", 3, "MI_00", "e&1ac1c826&0"),
    SlotMapping(30, "COM33", "Hub_#0010", 3, "MI_02", "e&1ac1c826&0"),
    SlotMapping(31, "COM32", "Hub_#0010", 3, "MI_04", "e&1ac1c826&0"),
    SlotMapping(32, "COM34", "Hub_#0010", 3, "MI_06", "e&1ac1c826&0"),
]

# Indeks lookup cepat
PORT_TO_SLOT: Dict[str, int] = {m.port_name: m.slot_index for m in FOXCOM_32_MAPPING}
SLOT_TO_PORT: Dict[int, str] = {m.slot_index: m.port_name for m in FOXCOM_32_MAPPING}


def get_slot_for_port(port_name: str) -> Optional[int]:
    """Mengembalikan nomor slot fisik (1-32) untuk nama port COM tertentu."""
    return PORT_TO_SLOT.get(port_name.upper())


def get_port_for_slot(slot_index: int) -> Optional[str]:
    """Mengembalikan nama port COM untuk nomor slot fisik tertentu."""
    return SLOT_TO_PORT.get(slot_index)
