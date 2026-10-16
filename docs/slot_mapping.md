# Dokumentasi Pemetaan Fisik Slot ke Port COM (Tahap M2)

## Metodologi Verifikasi
1. **Pemeriksaan Hierarki USB Hardware**:
   - Controller USB UART: Exar XR21V1414 (8 chip x 4 UART = 32 Port)
   - 2 Unit USB 2.0 Hub terintegrasi:
     - `Hub_#0009` (5 Chip downstream): Port 1 s.d. 5
     - `Hub_#0010` (3 Chip downstream): Port 1 s.d. 3 (tersambung ke Port 7 Hub_#0009)
2. **Uji Cabut SIM Fisik**:
   - Manusia mencabut kartu SIM pada **Slot Fisik No. 17**.
   - Port yang langsung beralih status dari `READY` menjadi `NOT_INSERTED` adalah **COM15**.
   - Terbukti secara perangkat keras bahwa `COM15` adalah interface pertama (`MI_00`) pada Chip 5 (`Hub_#0009.Port_#0005`, `d&8458c67&0`).

## Tabel Pemetaan Terverifikasi

| Slot Fisik | Port COM | USB Hub & Port | Interface | Parent ID Prefix | Status Deteksi Awal |
|---|---|---|---|---|---|
| **Slot 1** | COM7 | Hub 9, Port 1 | MI_00 | `d&7066c26&0` | NOT_INSERTED |
| **Slot 2** | COM19 | Hub 9, Port 1 | MI_02 | `d&7066c26&0` | NOT_INSERTED |
| **Slot 3** | COM4 | Hub 9, Port 1 | MI_04 | `d&7066c26&0` | NOT_INSERTED |
| **Slot 4** | COM3 | Hub 9, Port 1 | MI_06 | `d&7066c26&0` | NOT_INSERTED |
| **Slot 5** | COM9 | Hub 9, Port 2 | MI_00 | `d&2fe78588&0` | NOT_INSERTED |
| **Slot 6** | COM11 | Hub 9, Port 2 | MI_02 | `d&2fe78588&0` | NOT_INSERTED |
| **Slot 7** | COM5 | Hub 9, Port 2 | MI_04 | `d&2fe78588&0` | NOT_INSERTED |
| **Slot 8** | COM6 | Hub 9, Port 2 | MI_06 | `d&2fe78588&0` | NOT_INSERTED |
| **Slot 9** | COM12 | Hub 9, Port 3 | MI_00 | `d&1d2dd4e3&0` | NOT_INSERTED |
| **Slot 10** | COM20 | Hub 9, Port 3 | MI_02 | `d&1d2dd4e3&0` | NOT_INSERTED |
| **Slot 11** | COM8 | Hub 9, Port 3 | MI_04 | `d&1d2dd4e3&0` | NOT_INSERTED |
| **Slot 12** | COM10 | Hub 9, Port 3 | MI_06 | `d&1d2dd4e3&0` | NOT_INSERTED |
| **Slot 13** | COM13 | Hub 9, Port 4 | MI_00 | `d&a74243e&0` | NOT_INSERTED |
| **Slot 14** | COM17 | Hub 9, Port 4 | MI_02 | `d&a74243e&0` | NOT_INSERTED |
| **Slot 15** | COM14 | Hub 9, Port 4 | MI_04 | `d&a74243e&0` | NOT_INSERTED |
| **Slot 16** | COM18 | Hub 9, Port 4 | MI_06 | `d&a74243e&0` | NOT_INSERTED |
| **Slot 17** | **COM15** | Hub 9, Port 5 | MI_00 | `d&8458c67&0` | **VERIFIED (Cabut SIM)** |
| **Slot 18** | COM16 | Hub 9, Port 5 | MI_02 | `d&8458c67&0` | READY |
| **Slot 19** | COM21 | Hub 9, Port 5 | MI_04 | `d&8458c67&0` | READY |
| **Slot 20** | COM23 | Hub 9, Port 5 | MI_06 | `d&8458c67&0` | READY |
| **Slot 21** | COM25 | Hub 10, Port 1 | MI_00 | `e&49a5f69&0` | READY |
| **Slot 22** | COM22 | Hub 10, Port 1 | MI_02 | `e&49a5f69&0` | READY |
| **Slot 23** | COM24 | Hub 10, Port 1 | MI_04 | `e&49a5f69&0` | READY |
| **Slot 24** | COM26 | Hub 10, Port 1 | MI_06 | `e&49a5f69&0` | READY |
| **Slot 25** | COM30 | Hub 10, Port 2 | MI_00 | `e&2d7b78cb&0` | READY |
| **Slot 26** | COM27 | Hub 10, Port 2 | MI_02 | `e&2d7b78cb&0` | READY |
| **Slot 27** | COM28 | Hub 10, Port 2 | MI_04 | `e&2d7b78cb&0` | READY |
| **Slot 28** | COM29 | Hub 10, Port 2 | MI_06 | `e&2d7b78cb&0` | READY |
| **Slot 29** | COM31 | Hub 10, Port 3 | MI_00 | `e&1ac1c826&0` | READY |
| **Slot 30** | COM33 | Hub 10, Port 3 | MI_02 | `e&1ac1c826&0` | READY |
| **Slot 31** | COM32 | Hub 10, Port 3 | MI_04 | `e&1ac1c826&0` | READY |
| **Slot 32** | COM34 | Hub 10, Port 3 | MI_06 | `e&1ac1c826&0` | READY |

