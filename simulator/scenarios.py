"""
Skenario Pengujian Modem Simulasi (Section 8)
Menyediakan pabrik pembuatan kumpulan modem simulasi (hingga 64 port)
untuk pengujian multi-pool, port error, dan simulasi swap SIM.
"""

from typing import Dict, List, Optional
from simulator.simulated_modem import SimulatedModem
from app.modem.transport import VirtualTransport


class SimulatedModemPool:
    """Mengelola satu set port virtual yang merepresentasikan satu unit sasis modem pool."""

    def __init__(self, pool_id: str = "pool_1", port_count: int = 32):
        self.pool_id = pool_id
        self.port_count = port_count
        self.modems: Dict[str, SimulatedModem] = {}
        self.transports: Dict[str, VirtualTransport] = {}

        for i in range(1, port_count + 1):
            port_name = f"VIRTUAL_{pool_id}_P{i:02d}"
            transport = VirtualTransport(port_name=port_name)
            modem = SimulatedModem(port_name=port_name, transport=transport)
            self.transports[port_name] = transport
            self.modems[port_name] = modem

    def populate_sample_sims(self, active_count: int = 24) -> None:
        """Mengisi sebagian slot dengan kartu SIM aktif sintetis."""
        ports = list(self.modems.keys())
        for idx in range(min(active_count, len(ports))):
            pname = ports[idx]
            iccid = f"896201000000000{idx+1:04d}F"
            phone = f"+628123456{idx+1:04d}" if idx % 2 == 0 else None
            initial_msgs = [
                {
                    "sender": "INFO-OPERATOR",
                    "body": f"Selamat datang di jaringan. ID SIM: {idx+1}",
                    "timestamp": "26/10/16,06:00:00+28"
                }
            ] if idx % 3 == 0 else None

            self.modems[pname].insert_sim(
                iccid=iccid,
                phone_number=phone,
                initial_messages=initial_msgs
            )

    def inject_port_error(self, port_index: int) -> None:
        """Membuat salah satu port error/putus komunikasi (I-10)."""
        ports = list(self.modems.keys())
        if 0 <= port_index < len(ports):
            pname = ports[port_index]
            self.modems[pname].hang_mode = True

    def perform_sim_swap(self, port_index: int, new_iccid: str, new_phone: Optional[str] = None) -> None:
        """Mensimulasikan pencabutan SIM lama dan pemasangan SIM baru (I-1)."""
        ports = list(self.modems.keys())
        if 0 <= port_index < len(ports):
            pname = ports[port_index]
            self.modems[pname].remove_sim()
            self.modems[pname].insert_sim(iccid=new_iccid, phone_number=new_phone)

