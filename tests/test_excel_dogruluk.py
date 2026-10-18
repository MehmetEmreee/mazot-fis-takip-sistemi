"""Excel doğruluk aracının ölçümü sentetik etiketli fişlerle doğrulanır."""

import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from tools import excel_dogruluk


class ExcelDogrulukTesti(unittest.TestCase):
    def test_beklenen_degerler(self):
        self.assertEqual(excel_dogruluk.beklenen("litre", "12.5"), 12.5)
        self.assertEqual(excel_dogruluk.beklenen("litre", "1.234,5"), 1234.5)
        self.assertEqual(excel_dogruluk.beklenen("litre", "50"), 50)
        self.assertEqual(excel_dogruluk.beklenen("km", "12.500"), 12500)
        self.assertEqual(excel_dogruluk.beklenen("tarih", "5.5.2026"), datetime(2026, 5, 5))
        self.assertIsNone(excel_dogruluk.beklenen("plaka", "-"))
        self.assertEqual(excel_dogruluk.beklenen("km", "300 638"), 300638)

    def test_simulasyon_tum_alanlari_dogru_yazar(self):
        kayitlar = [{"_dosya": f"{i}.jpg", "_onaylandi": True, "fis_no": f"{100000 + i}",
                     "tarih": "05.05.2026", "firma": "Selim İnşaat", "plaka": "34 ABC 12",
                     "ad_soyad": "Ali Veli", "km": km, "litre": l}
                    for i, (l, km) in enumerate([("12.5", "12.500"), ("12,5", "300 638"),
                                                 ("45", "12000"), ("1.234,5", "9")])]
        with tempfile.TemporaryDirectory() as gecici:
            (Path(gecici) / "sonuclar.json").write_text(json.dumps(kayitlar), encoding="utf-8")
            once = sorted(p.name for p in Path(gecici).rglob("*"))
            sonuc = excel_dogruluk.simule_olcum(
                excel_dogruluk.onayli_kayitlar(Path(gecici)), None, "2026 MAZOT")
            self.assertEqual(sorted(p.name for p in Path(gecici).rglob("*")), once)
        self.assertEqual(sonuc["tam_dogru_satir"], 100.0, sonuc["farklar"])
        self.assertEqual(sonuc["ornek"], 4)


if __name__ == "__main__":
    unittest.main()
