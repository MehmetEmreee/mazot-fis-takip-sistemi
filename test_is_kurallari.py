"""OCR normalizasyonu, öğrenme ve Excel aktarımı için güvenli entegrasyon testleri."""

from __future__ import annotations

import hashlib
import json
import shutil
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from openpyxl import load_workbook

import excel_aktar
import excel_ogrenme
import uygulama
from dogrulama import kaydi_dogrula
from normalizasyon import fis_normalize_et, kilometreyi_duzelt, plakayi_duzelt


ROOT = Path(__file__).resolve().parent
KAYNAK_EXCEL = Path.home() / "Documents" / "NUSRET MAZOT 2026_YENİ 2.xlsx"


def sha256(yol: Path) -> str:
    ozet = hashlib.sha256()
    with yol.open("rb") as dosya:
        for parca in iter(lambda: dosya.read(1024 * 1024), b""):
            ozet.update(parca)
    return ozet.hexdigest()


class NormalizasyonTesti(unittest.TestCase):
    def test_tek_haneli_kilometre_cizgi_olur(self):
        for deger in (0, 1, 7, 9, " 7 "):
            self.assertEqual(kilometreyi_duzelt(deger), "-")
        for deger in ("10", "123456", "-"):
            self.assertEqual(kilometreyi_duzelt(deger), deger)
        self.assertEqual(fis_normalize_et({"km": "1"})["km"], "-")

    def test_bosluk_firma_ve_plaka_kurallari(self):
        self.assertEqual(fis_normalize_et({"firma": "7"})["firma"], "Selim İnşaat")
        self.assertEqual(plakayi_duzelt("H2 1663"), "34 H2 1663")
        self.assertEqual(plakayi_duzelt("KAZIK MAK", ekipman_durumu=True), "KAZIK MAK")


class DogrulamaTesti(unittest.TestCase):
    def test_gecerli_arac_kaydi(self):
        kayit = kaydi_dogrula(fis_normalize_et({
            "fis_no": "001234", "tarih": "10/09/2026", "firma": "Selim İnşaat",
            "plaka": "34 ABC 123", "ad_soyad": "Ali Veli", "km": "123456",
            "litre": "250,5",
        }))
        self.assertFalse(kayit["_kontrol_gerekli"])

    def test_tum_kritik_hatalar_aciklanir(self):
        kayit = kaydi_dogrula(fis_normalize_et({
            "fis_no": "-12", "tarih": "01/01/2025", "firma": "1",
            "plaka": "34 A2 1", "ad_soyad": "-", "km": "7", "litre": "-",
        }))
        neden = kayit["_kontrol_nedeni"]
        for parca in ("fiş no", "tarih", "plaka", "ad soyad", "kilometre", "litre"):
            self.assertIn(parca, neden)


class OtomatikYedekTesti(unittest.TestCase):
    def test_gunluk_json_yedegi_bir_kez_olusturulur(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as gecici:
            root = Path(gecici)
            (root / "sonuclar.json").write_text("[]", encoding="utf-8")
            ilk = uygulama.gunluk_veri_yedegi(root)
            ikinci = uygulama.gunluk_veri_yedegi(root)
            self.assertIsNotNone(ilk)
            self.assertEqual(ilk, ikinci)
            self.assertTrue(ilk.is_file())


class KullaniciOgrenmesiTesti(unittest.TestCase):
    def test_yalniz_degistirilen_metin_alanlari_ogrenilir(self):
        eski_dosya = excel_ogrenme.KULLANICI_FILE
        with tempfile.TemporaryDirectory(dir=ROOT) as gecici:
            try:
                excel_ogrenme.KULLANICI_FILE = Path(gecici) / "ogrenilen.json"
                excel_ogrenme.birlesik_referans_yukle.cache_clear()
                adet = excel_ogrenme.duzeltmelerden_ogren(
                    {"firma": "DOĞRU YENİ FİRMA", "km": "123456"},
                    {"firma", "km"},
                )
                self.assertEqual(adet, 1)
                oneri = excel_ogrenme.kayit_onerileri({
                    "firma": "DOGRU YENI FIRMAA", "plaka": "-", "ad_soyad": "-",
                })
                self.assertEqual(oneri["oneriler"][0]["onerilen"], "DOĞRU YENİ FİRMA")
                kayit = json.loads(excel_ogrenme.KULLANICI_FILE.read_text(encoding="utf-8"))
                self.assertNotIn("km", {
                    alan for alan, liste in kayit["alanlar"].items() if liste
                })
            finally:
                excel_ogrenme.KULLANICI_FILE = eski_dosya
                excel_ogrenme.birlesik_referans_yukle.cache_clear()


@unittest.skipUnless(KAYNAK_EXCEL.exists(), "Gerçek Excel örneği bu bilgisayarda yok")
class BirikimliExcelOgrenmesiTesti(unittest.TestCase):
    def test_farkli_sayfalar_birikir_ayni_sayfa_guncellenir(self):
        eski_referans = excel_ogrenme.REFERANS_FILE
        eski_kullanici = excel_ogrenme.KULLANICI_FILE
        with tempfile.TemporaryDirectory(dir=ROOT) as gecici:
            try:
                excel_ogrenme.REFERANS_FILE = Path(gecici) / "referans.json"
                excel_ogrenme.KULLANICI_FILE = Path(gecici) / "kullanici.json"
                excel_ogrenme.referans_yukle.cache_clear()
                excel_ogrenme.birlesik_referans_yukle.cache_clear()
                ilk = excel_ogrenme.referans_olustur(KAYNAK_EXCEL, "2026 MAZOT")
                ikinci = excel_ogrenme.referans_olustur(KAYNAK_EXCEL, "2025 MAZOT ")
                durum = excel_ogrenme.durum()
                self.assertEqual((ilk["toplam_kaynak"], ikinci["toplam_kaynak"]), (1, 2))
                self.assertEqual(durum["kaynak_sayisi"], 2)
                self.assertEqual(durum["satir_sayisi"], ilk["satir_sayisi"] + ikinci["satir_sayisi"])

                tekrar = excel_ogrenme.referans_olustur(KAYNAK_EXCEL, "2026 MAZOT")
                self.assertEqual(tekrar["toplam_kaynak"], 2)
                self.assertEqual(excel_ogrenme.durum()["kaynak_sayisi"], 2)
            finally:
                excel_ogrenme.REFERANS_FILE = eski_referans
                excel_ogrenme.KULLANICI_FILE = eski_kullanici
                excel_ogrenme.referans_yukle.cache_clear()
                excel_ogrenme.birlesik_referans_yukle.cache_clear()


@unittest.skipUnless(KAYNAK_EXCEL.exists(), "Gerçek Excel örneği bu bilgisayarda yok")
class ExcelAktarimTesti(unittest.TestCase):
    def test_dogru_satir_ve_sutunlara_yazar(self):
        kaynak_hash = sha256(KAYNAK_EXCEL)
        eski_root = excel_aktar.ROOT
        eski_data = excel_aktar.DATA_FILE
        eski_ayar = excel_aktar.AYAR_FILE
        with tempfile.TemporaryDirectory(dir=ROOT) as gecici:
            gecici = Path(gecici)
            kopya = gecici / "aktarim_testi.xlsx"
            data = gecici / "sonuclar.json"
            ayar = gecici / "ayar.json"
            shutil.copy2(KAYNAK_EXCEL, kopya)
            kayitlar = [
                {
                    "_dosya": "test-1.jpg", "_onaylandi": True,
                    "tarih": "10/09/2026", "fis_no": "009876",
                    "firma": "TEST FİRMA", "plaka": "34 ABC 123",
                    "ad_soyad": "TEST KİŞİ", "km": "123456", "litre": "42,5",
                },
                {
                    "_dosya": "test-2.jpg", "_onaylandi": True,
                    "tarih": "10/09/2026", "fis_no": "009877",
                    "firma": "TEST FİRMA", "plaka": "34 ABC 124",
                    "ad_soyad": "TEST KİŞİ 2", "km": "-", "litre": "50",
                },
            ]
            data.write_text(json.dumps(kayitlar, ensure_ascii=False), encoding="utf-8")
            try:
                excel_aktar.DATA_FILE = data
                excel_aktar.AYAR_FILE = ayar
                excel_aktar.ROOT = gecici
                sonuc = excel_aktar.aktar(kopya, "2026 MAZOT")
                self.assertNotIn("hata", sonuc)
                self.assertEqual((sonuc["aktarilan"], sonuc["ilk_satir"], sonuc["son_satir"]), (2, 1308, 1309))

                wb = load_workbook(kopya, data_only=False)
                ws = wb["2026 MAZOT"]
                self.assertTrue(all(ws.cell(1307, c).value is None for c in (1, 2, 3, 4, 5, 6, 8)))
                self.assertEqual(ws.cell(1308, 1).value, datetime(2026, 9, 10))
                self.assertEqual(ws.cell(1308, 2).value, 9876)
                self.assertEqual(ws.cell(1308, 3).value, "TEST FİRMA")
                self.assertEqual(ws.cell(1308, 4).value, "34 ABC 123")
                self.assertEqual(ws.cell(1308, 5).value, "TEST KİŞİ")
                self.assertEqual(ws.cell(1308, 6).value, 123456)
                self.assertEqual(ws.cell(1308, 8).value, 42.5)
                self.assertIsNone(ws.cell(1309, 6).value)
                self.assertTrue(all(ws.cell(1308, c).value is None for c in (7, 9, 10, 11, 12, 13)))
                wb.close()

                kaydedilen = json.loads(data.read_text(encoding="utf-8"))
                self.assertTrue(all(kayit.get("_excel_aktarildi") for kayit in kaydedilen))
                self.assertEqual([k["_excel_satir"] for k in kaydedilen], [1308, 1309])
                self.assertTrue(Path(sonuc["yedek"]).is_file())
            finally:
                excel_aktar.ROOT = eski_root
                excel_aktar.DATA_FILE = eski_data
                excel_aktar.AYAR_FILE = eski_ayar

        self.assertEqual(sha256(KAYNAK_EXCEL), kaynak_hash)


if __name__ == "__main__":
    unittest.main(verbosity=2)
