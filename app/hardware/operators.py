"""
Profil operator seluler Indonesia: data bawaan dan pencocokan ICCID/nama operator.
Murni logika (tanpa DB) supaya bisa dipakai lapisan modem, service, dan API.
"""

from __future__ import annotations

import re
from types import SimpleNamespace
from typing import Iterable, List, Optional

DEFAULT_OPERATOR_PROFILES = [
    {
        "id": "xl_axis", "name": "XL / AXIS", "ussd_code": "*808*7*8#",
        "iccid_prefixes": "896211,896212", "operator_patterns": "XL,AXIS",
        "description": "Dial langsung pengecekan nomor 4G untuk jaringan XL Axiata dan AXIS",
    },
    {
        "id": "telkomsel", "name": "Telkomsel", "ussd_code": "*808#",
        "iccid_prefixes": "896210,896204", "operator_patterns": "TELKOMSEL,T-SEL,SIMPATI,AS,LOOP",
        "description": "Dial cek nomor Telkomsel",
    },
    {
        "id": "indosat", "name": "Indosat Ooredoo", "ussd_code": "*123*30#",
        "iccid_prefixes": "896201,896221", "operator_patterns": "INDOSAT,IM3,MENTARI",
        "description": "Dial cek nomor Indosat Ooredoo Hutchison",
    },
    {
        "id": "tri", "name": "Tri (3)", "ussd_code": "*111*1#",
        "iccid_prefixes": "896289", "operator_patterns": "TRI,3",
        "description": "Dial cek nomor Tri",
    },
    {
        "id": "smartfren", "name": "Smartfren", "ussd_code": "*999#",
        "iccid_prefixes": "896209", "operator_patterns": "SMARTFREN,SMART",
        "description": "Dial cek nomor Smartfren",
    },
]

FALLBACK_USSD_CODE = "*808*7*8#"


def default_profiles() -> List[SimpleNamespace]:
    return [SimpleNamespace(**p) for p in DEFAULT_OPERATOR_PROFILES]


def _split(value: Optional[str]) -> List[str]:
    return [x.strip() for x in (value or "").split(",") if x.strip()]


def match_operator(profiles: Iterable, iccid: Optional[str] = None, operator_name: Optional[str] = None):
    """Cocokkan prefiks ICCID dulu (paling andal), lalu nama operator. Mengembalikan profil atau None."""
    profiles = list(profiles)
    clean_iccid = re.sub(r"\D", "", iccid) if iccid else ""
    if clean_iccid:
        for p in profiles:
            if any(clean_iccid.startswith(prefix) for prefix in _split(p.iccid_prefixes)):
                return p
    name = (operator_name or "").strip().upper()
    if name:
        for p in profiles:
            if any(pattern.upper() in name for pattern in _split(p.operator_patterns)):
                return p
    return None
