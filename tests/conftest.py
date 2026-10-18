"""
Konfigurasi pytest. Environment HARUS diset sebelum modul `app` pertama kali diimpor:
database uji berada di direktori sementara sehingga tes tidak pernah menyentuh data asli.
"""

import os
import tempfile

_TMP = tempfile.mkdtemp(prefix="scpool-test-")
os.environ["SCPOOL_DATA_DIR"] = _TMP
os.environ["SCPOOL_ADMIN_PASSWORD"] = "admin-test-password"
os.environ["SCPOOL_LOCAL_HARDWARE"] = "0"
os.environ["SCPOOL_LOCAL_AUTOLOGIN"] = "1"
os.environ.setdefault("SCPOOL_TELEGRAM_BOT_TOKEN", "")
