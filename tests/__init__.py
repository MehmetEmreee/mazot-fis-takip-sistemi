"""Testler gerçek veri klasörüne (data/) asla yazmasın: her mazot içe
aktarımından önce veri dizini geçici bir klasöre yönlendirilir (alt süreçler
ortam değişkenini devralır)."""

import atexit
import os
import shutil
import tempfile

_VERI = tempfile.mkdtemp(prefix="mazot_test_veri_")
os.environ["MAZOT_VERI_DIZINI"] = _VERI
atexit.register(shutil.rmtree, _VERI, True)
