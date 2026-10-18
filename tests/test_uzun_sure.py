"""Uzun süreli çalışma testinin kısa sürümü (tam koşu: python -m tools.uzun_sure_testi)."""

import tempfile
import unittest
from pathlib import Path

from tools import uzun_sure_testi


class KisaSoakTesti(unittest.TestCase):
    def test_bes_gunluk_akis_kontrolleri_gecer(self):
        with tempfile.TemporaryDirectory() as gecici:
            rapor = uzun_sure_testi.calistir(5, 25, Path(gecici) / "data", gorsel_kb=8,
                                             hata_orani=0.1, yazdir=False)
        self.assertEqual(rapor["toplam_aktarilan"], 125)
        basarisiz = {k: v for k, v in rapor["kontroller"].items() if v is False}
        self.assertEqual(basarisiz, {})


if __name__ == "__main__":
    unittest.main()
