"""Tarih biçimi/aralığı, OCR yıl düzeltmesi ve 5-6 haneli fiş numarası."""

from __future__ import annotations

import copy
import io
import unittest
from contextlib import redirect_stdout
from datetime import date, timedelta

from mazot import arayuz
from mazot.dogrulama import kaydi_dogrula, tarih_sorunu, tarih_yilini_duzelt
from mazot.normalizasyon import fis_normalize_et, tarihi_duzelt
from tests.test_hata_duzeltmeleri import OcrTemel, jpeg

YUKLEME = "2026-10-18T10:00:00"


def kayit(**ek) -> dict:
    return {"fis_no": "100001", "tarih": "15.10.2026", "firma": "Selim İnşaat",
            "plaka": "34 ABC 123", "ad_soyad": "Ali Veli", "km": "12000",
            "litre": "50", "_yukleme_zamani": YUKLEME, **ek}


class TarihBicimiTesti(unittest.TestCase):
    def test_tek_bicim(self):
        for girdi, beklenen in (("8/10/2026", "08.10.2026"), ("07-10-2026", "07.10.2026"),
                                ("15/10/2026", "15.10.2026"), ("15.10.2026", "15.10.2026"),
                                ("7.10.26", "07.10.2026"), (" 1 / 9 / 2026 ", "01.09.2026")):
            self.assertEqual(tarihi_duzelt(girdi), beklenen, girdi)

    def test_cozulemeyen_oldugu_gibi_kalir(self):
        for girdi in ("10.109/2026", "31/02/2026", "dün"):
            self.assertEqual(tarihi_duzelt(girdi), girdi)
        self.assertEqual(tarihi_duzelt(None), "-")

    def test_normalizasyon_tarihi_birlestirir(self):
        self.assertEqual(fis_normalize_et({"tarih": "8/10/2026"})["tarih"], "08.10.2026")


class TarihAraligiTesti(unittest.TestCase):
    def test_yukleme_gunune_gore_aralik(self):
        self.assertIsNone(tarih_sorunu(kayit(tarih="18.10.2026")))
        self.assertIsNone(tarih_sorunu(kayit(tarih="18.07.2026")))
        self.assertIn("aydan eski", tarih_sorunu(kayit(tarih="17.07.2026")))
        self.assertIn("ileride", tarih_sorunu(kayit(tarih="19.10.2026")))
        self.assertIn("geçersiz", tarih_sorunu(kayit(tarih="31.02.2026")))

    def test_eski_parti_zamanla_gecersiz_olmaz(self):
        eski = kayit(tarih="20.08.2026", _yukleme_zamani="2026-09-01T09:00:00")
        self.assertIsNone(tarih_sorunu(eski, bugun=date(2027, 6, 1)))

    def test_ay_sonu(self):
        self.assertIsNone(tarih_sorunu(kayit(tarih="28.02.2026",
                                             _yukleme_zamani="2026-05-31T10:00:00")))

    def test_dogrulama_nedeni(self):
        sonuc = kaydi_dogrula(kayit(tarih="27.10.2026"))
        self.assertTrue(sonuc["_kontrol_gerekli"])
        self.assertIn("ileride", sonuc["_kontrol_nedeni"])
        self.assertFalse(kaydi_dogrula(kayit())["_kontrol_gerekli"])


class YilDuzeltmeTesti(unittest.TestCase):
    def test_yanlis_yil_duzeltilir(self):
        for ocr in ("12.10.2016", "07.10.2020", "19.09.2006"):
            veri = kayit(tarih=ocr)
            self.assertTrue(tarih_yilini_duzelt(veri))
            self.assertEqual(veri["tarih"], ocr[:6] + "2026")
            self.assertEqual(veri["_otomatik_duzeltmeler"][-1]["eski"], ocr)

    def test_yil_basinda_onceki_yil(self):
        veri = kayit(tarih="20.12.2016", _yukleme_zamani="2027-01-10T10:00:00")
        self.assertTrue(tarih_yilini_duzelt(veri))
        self.assertEqual(veri["tarih"], "20.12.2026")

    def test_gun_ay_hatasi_tahmin_edilmez(self):
        veri = kayit(tarih="28.10.2026")  # yükleme 18.10: gün/ay hatası, yıl değil
        self.assertFalse(tarih_yilini_duzelt(veri))
        self.assertEqual(veri["tarih"], "28.10.2026")

    def test_onayli_kayda_dokunulmaz(self):
        veri = kayit(tarih="12.10.2016", _onaylandi=True)
        self.assertFalse(tarih_yilini_duzelt(veri))
        self.assertEqual(veri["tarih"], "12.10.2016")


class FisNoTesti(unittest.TestCase):
    def test_bes_ve_alti_hane_gecerli(self):
        for fis_no in ("12345", "123456"):
            self.assertNotIn("fiş no", kaydi_dogrula(kayit(fis_no=fis_no))["_kontrol_nedeni"])
        for fis_no in ("1234", "1234567", "12a45"):
            self.assertIn("5 veya 6 rakam", kaydi_dogrula(kayit(fis_no=fis_no))["_kontrol_nedeni"])


class OcrHattiTesti(OcrTemel):
    # Yükleme şimdi yapılır; fiş 5 gün önceki, OCR yılı 2016 okumuş.
    GERCEK = date.today() - timedelta(days=5)

    def sahte_ocr(self, image_path):
        sonuc = super().sahte_ocr(image_path)
        sonuc.update({"tarih": self.GERCEK.strftime("%d/%m/") + "2016", "fis_no": "12345"})
        return sonuc

    def test_okumada_yil_duzeltilir_bes_haneli_tekrar_yakalanir(self):
        with redirect_stdout(io.StringIO()):
            arayuz.fotografi_kabul_et(jpeg(1), ".jpg")
            arayuz.fotografi_kabul_et(jpeg(2), ".jpg")
            self.isciyi_baslat()
            self.kuyrugu_bekle()
        kayitlar = self.oku()
        self.assertEqual({k["tarih"] for k in kayitlar}, {self.GERCEK.strftime("%d.%m.%Y")})
        self.assertTrue(kayitlar[0]["_otomatik_duzeltildi"])
        self.assertTrue(any(k.get("_tekrar") for k in kayitlar))  # 5 haneli tekrar


class OnayliKayitKorumaTesti(OcrTemel):
    def test_yukle_kaydet_onayli_degerleri_degistirmez(self):
        onayli = [
            kayit(_dosya="a.jpg", tarih="07/10/2026", _onaylandi=True, fis_no="12345"),
            kayit(_dosya="b.jpg", tarih="12/10/2016", _onaylandi=True),  # yanlış onaylanmış
            kayit(_dosya="c.jpg", tarih="8/10/2026", _onaylandi=True,
                  _excel_aktarildi=True, _excel_satir=10),
        ]
        self.yaz(self.data, onayli)
        once = copy.deepcopy(onayli)
        with arayuz.DATA_LOCK:
            arayuz.save_receipts(arayuz.load_receipts())
        sonra = self.oku()
        for o, s in zip(once, sonra):
            for alan in o:
                if alan == "tarih":
                    self.assertEqual(s["tarih"], tarihi_duzelt(o["tarih"]))
                else:
                    self.assertEqual(s[alan], o[alan], alan)
        self.assertEqual([s["tarih"] for s in sonra], ["07.10.2026", "12.10.2016", "08.10.2026"])
        # Yanlış yıl onaylıda düzeltilmez, yalnız uyarı.
        self.assertIn("aydan eski", sonra[1]["_kontrol_nedeni"])
        self.assertFalse(sonra[0]["_kontrol_gerekli"])


if __name__ == "__main__":
    unittest.main()
