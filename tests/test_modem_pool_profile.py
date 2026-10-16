"""
Pengujian Profil Pemetaan Modem Pool (Tahap M2)
Memastikan integritas pemetaan slot fisik 1-32 ke port COM serial.
"""

import unittest
from config.modem_pool_profile import (
    FOXCOM_32_MAPPING, PORT_TO_SLOT, SLOT_TO_PORT,
    get_slot_for_port, get_port_for_slot
)


class TestModemPoolProfile(unittest.TestCase):

    def test_total_slots_and_ports(self):
        """Memastikan tepat 32 slot fisik terpetakan."""
        self.assertEqual(len(FOXCOM_32_MAPPING), 32)
        self.assertEqual(len(PORT_TO_SLOT), 32)
        self.assertEqual(len(SLOT_TO_PORT), 32)

    def test_slot_index_range(self):
        """Memastikan nomor slot 1 sampai 32 berurutan tanpa lompatan."""
        slot_indices = sorted([m.slot_index for m in FOXCOM_32_MAPPING])
        self.assertEqual(slot_indices, list(range(1, 33)))

    def test_verified_slot_17(self):
        """Memastikan Slot 17 terpetakan ke COM15 sesuai hasil uji cabut fisik."""
        self.assertEqual(get_port_for_slot(17), "COM15")
        self.assertEqual(get_slot_for_port("COM15"), 17)

    def test_bidirectional_consistency(self):
        """Memastikan lookup dua arah port <-> slot konsisten."""
        for m in FOXCOM_32_MAPPING:
            self.assertEqual(get_slot_for_port(m.port_name), m.slot_index)
            self.assertEqual(get_port_for_slot(m.slot_index), m.port_name)


if __name__ == "__main__":
    unittest.main()

