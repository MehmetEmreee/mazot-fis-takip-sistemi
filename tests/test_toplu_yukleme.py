"""Toplu fiş ekleme (fotografi_kabul_et + gelen kutusu klasörü) testleri.

Ollama gerekmez: resmi_oku sahtesiyle çalışan tek test dışında OCR işçisi
başlatılmaz, kuyruk sadece kontrol edilir.
Çalıştırma: .venv/bin/python -m unittest test_toplu_yukleme
"""

from __future__ import annotations

import io
import json
import queue
import shutil
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import datetime
from pathlib import Path

from mazot import arayuz


def jpeg_baytlari(etiket: int) -> bytes:
    """Her etiket için farklı içerikli küçük bir JPEG benzeri bayt dizisi."""
    return b"\xff\xd8\xff\xe0" + b"JFIF" + etiket.to_bytes(4, "big") * 16 + b"\xff\xd9"


class TopluYuklemeTemel(unittest.TestCase):
    def setUp(self) -> None:
        self._gecici = tempfile.TemporaryDirectory()
        kok = Path(self._gecici.name)
        self._eski = {
            ad: getattr(arayuz, ad)
            for ad in ("FISLER_DIR", "DATA_FILE", "GELEN_DIR", "ESKI_GELEN_DIR", "OCR_QUEUE")
        }
        arayuz.FISLER_DIR = kok / "fisler"
        arayuz.FISLER_DIR.mkdir()
        arayuz.DATA_FILE = kok / "sonuclar.json"
        arayuz.GELEN_DIR = kok / "gelen_fisler"
        arayuz.ESKI_GELEN_DIR = None  # gerçek kök gelen kutusuna dokunma
        arayuz.OCR_QUEUE = queue.Queue()
        self._eski_hashler = dict(arayuz.IMAGE_HASHES)
        arayuz.IMAGE_HASHES.clear()
        self.kok = kok

    def tearDown(self) -> None:
        for ad, deger in self._eski.items():
            setattr(arayuz, ad, deger)
        arayuz.IMAGE_HASHES.clear()
        arayuz.IMAGE_HASHES.update(self._eski_hashler)
        self._gecici.cleanup()

    def kayitlar(self) -> list[dict]:
        return json.loads(arayuz.DATA_FILE.read_text(encoding="utf-8"))


class FotografiKabulEtTesti(TopluYuklemeTemel):
    def test_kabul_eder_ve_kuyruga_alir(self) -> None:
        sonuc = arayuz.fotografi_kabul_et(
            jpeg_baytlari(1), ".jpg", orijinal_ad="IMG_0001.JPG", kaynak="toplu"
        )
        self.assertTrue(sonuc["ok"])
        hedef = arayuz.temp_dir() / sonuc["filename"]
        self.assertTrue(hedef.is_file())
        self.assertEqual(arayuz.OCR_QUEUE.qsize(), 1)
        yol, _ = arayuz.OCR_QUEUE.get_nowait()
        self.assertEqual(yol, hedef)

        kayitlar = self.kayitlar()
        self.assertEqual(len(kayitlar), 1)
        self.assertTrue(kayitlar[0]["_isleniyor"])
        self.assertEqual(kayitlar[0]["_orijinal_ad"], "IMG_0001.JPG")
        self.assertEqual(kayitlar[0]["_kaynak"], "toplu")

    def test_ozdes_fotografi_reddeder(self) -> None:
        ilk = arayuz.fotografi_kabul_et(jpeg_baytlari(2), ".jpg")
        ikinci = arayuz.fotografi_kabul_et(jpeg_baytlari(2), ".jpg", orijinal_ad="kopya.jpg")
        self.assertTrue(ilk["ok"])
        self.assertFalse(ikinci["ok"])
        self.assertTrue(ikinci["duplicate"])
        self.assertEqual(ikinci["existing_filename"], ilk["filename"])
        self.assertEqual(len(self.kayitlar()), 1)
        self.assertEqual(arayuz.OCR_QUEUE.qsize(), 1)

    def test_imzaya_gore_uzanti_ve_desteklenmeyen_tur(self) -> None:
        png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
        sonuc = arayuz.fotografi_kabul_et(png, ".jpg")
        self.assertTrue(sonuc["filename"].endswith(".png"))
        with self.assertRaises(ValueError):
            arayuz.fotografi_kabul_et(b"heic-icerik", ".heic")
        with self.assertRaises(ValueError):
            arayuz.fotografi_kabul_et(b"", ".jpg")

    def test_orijinal_ad_temizlenir(self) -> None:
        self.assertEqual(
            arayuz.orijinal_adi_temizle("C%3A%5CFotolar%5CIMG_5.jpg", url_cozumle=True),
            "IMG_5.jpg",
        )
        self.assertEqual(arayuz.orijinal_adi_temizle("../../etc/passwd"), "passwd")
        uzun = arayuz.orijinal_adi_temizle("a" * 300 + ".jpeg")
        self.assertLessEqual(len(uzun), arayuz.ORIJINAL_AD_SINIRI)
        self.assertTrue(uzun.endswith(".jpeg"))
        self.assertIsNone(arayuz.orijinal_adi_temizle(""))

    def test_uzanti_belirle(self) -> None:
        self.assertEqual(arayuz.uzanti_belirle("image/jpeg"), ".jpg")
        self.assertEqual(arayuz.uzanti_belirle("image/png; charset=x"), ".png")
        self.assertEqual(arayuz.uzanti_belirle("application/octet-stream", "x.webp"), ".webp")
        self.assertEqual(arayuz.uzanti_belirle(None), ".jpg")
        self.assertEqual(arayuz.uzanti_belirle("image/heic", "x.heic"), ".heic")


class GelenKlasoruTesti(TopluYuklemeTemel):
    def setUp(self) -> None:
        super().setUp()
        arayuz.GELEN_DIR.mkdir()
        self.durum: dict = {}

    def tara(self) -> dict:
        with redirect_stdout(io.StringIO()):
            return arayuz.gelen_klasoru_tara(self.durum)

    def test_ilk_taramada_bekler_ikincide_aktarir(self) -> None:
        alt = arayuz.GELEN_DIR / "telefon" / "2026"
        alt.mkdir(parents=True)
        (arayuz.GELEN_DIR / "b.jpg").write_bytes(jpeg_baytlari(10))
        (alt / "a.JPG").write_bytes(jpeg_baytlari(11))

        ilk = self.tara()
        self.assertEqual(ilk["aktarilan"], 0)
        self.assertEqual(self.durum["bekleyen"], 2)
        self.assertEqual(arayuz.OCR_QUEUE.qsize(), 0)

        ikinci = self.tara()
        self.assertEqual(ikinci["aktarilan"], 2)
        self.assertEqual(self.durum["bekleyen"], 0)
        self.assertEqual(self.durum["son_aktarilan"], 2)

        gun = datetime.now().strftime("%Y-%m-%d")
        islendi = arayuz.GELEN_DIR / "_islendi" / gun
        self.assertEqual(sorted(p.name for p in islendi.iterdir()), ["a.JPG", "b.jpg"])
        self.assertFalse((arayuz.GELEN_DIR / "b.jpg").exists())

        kayitlar = self.kayitlar()
        # Ada (göreli yola) göre sıralı aktarım: önce b.jpg, sonra telefon/2026/a.JPG
        self.assertEqual([k["_orijinal_ad"] for k in kayitlar], ["b.jpg", "a.JPG"])
        self.assertTrue(all(k["_kaynak"] == "klasor" for k in kayitlar))

        # İşlenen klasör yeniden taranmaz.
        self.assertEqual(self.tara()["aktarilan"], 0)
        self.assertEqual(len(self.kayitlar()), 2)

    def test_eski_kok_gelen_kutusu_da_taranir(self) -> None:
        eski = self.kok / "eski_gelen"
        eski.mkdir()
        arayuz.ESKI_GELEN_DIR = eski
        (eski / "localsend.jpg").write_bytes(jpeg_baytlari(50))
        self.tara()
        self.assertEqual(self.tara()["aktarilan"], 1)
        self.assertFalse((eski / "localsend.jpg").exists())
        gun = datetime.now().strftime("%Y-%m-%d")
        self.assertTrue((arayuz.GELEN_DIR / "_islendi" / gun / "localsend.jpg").is_file())

    def test_degisen_dosya_beklenir(self) -> None:
        yol = arayuz.GELEN_DIR / "yaziliyor.jpg"
        yol.write_bytes(jpeg_baytlari(20))
        self.tara()
        yol.write_bytes(jpeg_baytlari(20) + jpeg_baytlari(21))  # dosya büyüdü
        self.assertEqual(self.tara()["aktarilan"], 0)
        self.assertEqual(self.tara()["aktarilan"], 1)

    def test_tekrar_fotograf_tekrar_klasorune(self) -> None:
        arayuz.fotografi_kabul_et(jpeg_baytlari(30), ".jpg")
        (arayuz.GELEN_DIR / "ayni.jpg").write_bytes(jpeg_baytlari(30))
        self.tara()
        ozet = self.tara()
        self.assertEqual(ozet["tekrar"], 1)
        self.assertTrue((arayuz.GELEN_DIR / "_tekrar" / "ayni.jpg").is_file())
        self.assertEqual(len(self.kayitlar()), 1)

    def test_ayni_adli_hedef_ezilmez(self) -> None:
        tekrar = arayuz.GELEN_DIR / "_tekrar"
        tekrar.mkdir()
        (tekrar / "x.jpg").write_bytes(b"eski")
        arayuz.fotografi_kabul_et(jpeg_baytlari(40), ".jpg")
        (arayuz.GELEN_DIR / "x.jpg").write_bytes(jpeg_baytlari(40))
        self.tara()
        self.tara()
        self.assertEqual((tekrar / "x.jpg").read_bytes(), b"eski")
        self.assertTrue((tekrar / "x_2.jpg").is_file())

    def test_yarim_ve_gizli_dosyalar_yok_sayilir(self) -> None:
        for ad in ("foto.jpg.part", "foto.jpg.crdownload", "foto.tmp", "~foto.jpg",
                   ".gizli.jpg", "notlar.txt"):
            (arayuz.GELEN_DIR / ad).write_bytes(jpeg_baytlari(50))
        gizli_klasor = arayuz.GELEN_DIR / ".sync"
        gizli_klasor.mkdir()
        (gizli_klasor / "f.jpg").write_bytes(jpeg_baytlari(51))
        self.tara()
        ozet = self.tara()
        self.assertEqual(sum(ozet.values()), 0)
        self.assertEqual(self.durum["bekleyen"], 0)
        self.assertFalse(arayuz.DATA_FILE.exists())

    def test_kesik_jpeg_beklenir_tamamlaninca_aktarilir(self) -> None:
        yol = arayuz.GELEN_DIR / "localsend.jpg"
        tam = jpeg_baytlari(80)
        yol.write_bytes(tam[:-10])  # sonu (FF D9) henüz gelmemiş
        self.tara()
        self.assertEqual(sum(self.tara().values()), 0)
        self.assertEqual(sum(self.tara().values()), 0)
        self.assertTrue(yol.exists())
        self.assertEqual(self.durum["bekleyen"], 1)
        self.assertEqual(arayuz.OCR_QUEUE.qsize(), 0)

        yol.write_bytes(tam + b"\x00" * 8)  # sıfır dolgu kabul edilir
        self.tara()
        self.assertEqual(self.tara()["aktarilan"], 1)
        self.assertFalse(yol.exists())

    def test_hep_kesik_kalan_jpeg_sure_dolunca_hataliya(self) -> None:
        eski = arayuz.GELEN_EKSIK_BEKLEME_SN
        arayuz.GELEN_EKSIK_BEKLEME_SN = 0
        try:
            yol = arayuz.GELEN_DIR / "yarim.jpg"
            yol.write_bytes(jpeg_baytlari(81)[:-10])
            self.tara()                      # ilk görülüş
            self.assertEqual(sum(self.tara().values()), 0)  # eksik, süre başlar
            ozet = self.tara()               # süre doldu
        finally:
            arayuz.GELEN_EKSIK_BEKLEME_SN = eski
        self.assertEqual(ozet["hatali"], 1)
        self.assertTrue((arayuz.GELEN_DIR / "_hatali" / "yarim.jpg").is_file())

    def test_acilamayan_heic_sure_dolunca_hataliya(self) -> None:
        eski = arayuz.GELEN_EKSIK_BEKLEME_SN
        arayuz.GELEN_EKSIK_BEKLEME_SN = 0
        try:
            (arayuz.GELEN_DIR / "IMG_1.HEIC").write_bytes(b"\x00\x00\x00\x18ftypheic" + b"\x00" * 40)
            self.tara()
            self.tara()                      # açılamıyor: yarım sayılır, süre başlar
            ozet = self.tara()
        finally:
            arayuz.GELEN_EKSIK_BEKLEME_SN = eski
        self.assertEqual(ozet["hatali"], 1)
        self.assertTrue((arayuz.GELEN_DIR / "_hatali" / "IMG_1.HEIC").is_file())
        self.assertFalse(arayuz.DATA_FILE.exists())

    def test_heic_jpege_cevrilip_alinir(self) -> None:
        try:
            import pillow_heif  # noqa: F401
        except ImportError:
            self.skipTest("pillow-heif kurulu değil")
        import io
        from PIL import Image

        tampon = io.BytesIO()
        Image.new("RGB", (640, 480), (200, 30, 30)).save(tampon, "HEIF")
        veri = tampon.getvalue()
        yarim = arayuz.GELEN_DIR / "IMG_2.HEIC"
        yarim.write_bytes(veri[: len(veri) // 2])
        self.tara()
        self.assertEqual(sum(self.tara().values()), 0)  # yarım: bekler
        yarim.write_bytes(veri)
        self.tara()
        self.assertEqual(self.tara()["aktarilan"], 1)
        kayit = json.loads(arayuz.DATA_FILE.read_text(encoding="utf-8"))[-1]
        self.assertTrue(kayit["_dosya"].endswith(".jpg"))

    def test_png_verify_ile_kontrol_edilir(self) -> None:
        from PIL import Image

        tampon = io.BytesIO()
        Image.new("RGB", (8, 8), "white").save(tampon, "PNG")
        (arayuz.GELEN_DIR / "tam.png").write_bytes(tampon.getvalue())
        (arayuz.GELEN_DIR / "kesik.png").write_bytes(tampon.getvalue()[:20])
        self.tara()
        ozet = self.tara()
        self.assertEqual(ozet["aktarilan"], 1)
        self.assertTrue((arayuz.GELEN_DIR / "kesik.png").exists())

    def test_hatali_dosya_hatali_klasorune(self) -> None:
        eski_sinir = arayuz.MAX_UPLOAD_BYTES
        arayuz.MAX_UPLOAD_BYTES = 10
        try:
            (arayuz.GELEN_DIR / "buyuk.jpg").write_bytes(jpeg_baytlari(60))
            self.tara()
            ozet = self.tara()
        finally:
            arayuz.MAX_UPLOAD_BYTES = eski_sinir
        self.assertEqual(ozet["hatali"], 1)
        self.assertTrue((arayuz.GELEN_DIR / "_hatali" / "buyuk.jpg").is_file())

    def test_kendi_alt_klasorleri_taranmaz(self) -> None:
        for alt in ("_islendi", "_tekrar", "_hatali"):
            (arayuz.GELEN_DIR / alt).mkdir()
            (arayuz.GELEN_DIR / alt / f"{alt}.jpg").write_bytes(jpeg_baytlari(len(alt)))
        self.tara()
        self.assertEqual(sum(self.tara().values()), 0)


class OcrIscisiAlanKorumaTesti(TopluYuklemeTemel):
    def test_isci_orijinal_ad_ve_kaynagi_korur(self) -> None:
        sonuc = arayuz.fotografi_kabul_et(
            jpeg_baytlari(70), ".jpg", orijinal_ad="IMG_70.jpg", kaynak="klasor"
        )
        eski_oku = arayuz.resmi_oku

        def sahte_oku(yol):
            raise RuntimeError("Ollama yok")

        arayuz.resmi_oku = sahte_oku
        try:
            import threading
            isci = threading.Thread(target=arayuz.ocr_worker, daemon=True)
            isci.start()
            arayuz.OCR_QUEUE.join()
        finally:
            arayuz.resmi_oku = eski_oku
        kayit = next(k for k in self.kayitlar() if k["_dosya"] == sonuc["filename"])
        self.assertFalse(kayit["_isleniyor"])
        self.assertIn("HATA", kayit)
        self.assertEqual(kayit["_orijinal_ad"], "IMG_70.jpg")
        self.assertEqual(kayit["_kaynak"], "klasor")


if __name__ == "__main__":
    unittest.main()


class VeriTasimaTesti(unittest.TestCase):
    """yollar.eski_veriyi_tasi: kökteki eski veri data/ altına bir kez taşınır."""

    def setUp(self) -> None:
        from mazot import yollar

        self.yollar = yollar
        self._gecici = tempfile.TemporaryDirectory()
        self.kok = Path(self._gecici.name)
        self.veri = self.kok / "data"
        (self.kok / "sonuclar.json").write_text("[]", encoding="utf-8")
        (self.kok / "fisler").mkdir()
        (self.kok / "fisler" / "a.jpg").write_bytes(b"x")

    def tearDown(self) -> None:
        self._gecici.cleanup()

    def test_tasir_ve_kayit_tutar(self) -> None:
        tasinan = self.yollar.eski_veriyi_tasi(self.kok, self.veri)
        self.assertEqual(sorted(tasinan), ["fisler", "sonuclar.json"])
        self.assertTrue((self.veri / "fisler" / "a.jpg").is_file())
        self.assertFalse((self.kok / "sonuclar.json").exists())
        kayit = json.loads((self.veri / "tasima_kaydi.json").read_text(encoding="utf-8"))
        self.assertEqual(sorted(kayit[0]["tasinan"]), ["fisler", "sonuclar.json"])
        # İkinci açılışta yapacak iş yok.
        self.assertEqual(self.yollar.eski_veriyi_tasi(self.kok, self.veri), [])

    def test_hedefte_varsa_kaynaga_dokunmaz(self) -> None:
        self.veri.mkdir()
        (self.veri / "sonuclar.json").write_text('[{"yeni": 1}]', encoding="utf-8")
        self.yollar.eski_veriyi_tasi(self.kok, self.veri)
        self.assertEqual((self.kok / "sonuclar.json").read_text(encoding="utf-8"), "[]")
        self.assertIn("yeni", (self.veri / "sonuclar.json").read_text(encoding="utf-8"))

    def test_hata_olursa_tasinanlar_geri_alinir(self) -> None:
        from unittest import mock

        gercek = shutil.move

        def bozuk(kaynak, hedef):
            # sonuclar.json önce taşınır; fisler/ kilitliyse o da geri alınmalı.
            if Path(kaynak).name == "fisler" and Path(hedef).parent.name == "data":
                raise PermissionError("dosya açık")
            return gercek(kaynak, hedef)

        with mock.patch.object(self.yollar.shutil, "move", side_effect=bozuk):
            with self.assertRaises(self.yollar.TasimaHatasi):
                self.yollar.eski_veriyi_tasi(self.kok, self.veri)
        self.assertTrue((self.kok / "fisler" / "a.jpg").is_file())
        self.assertTrue((self.kok / "sonuclar.json").is_file())
        self.assertFalse((self.veri / "sonuclar.json").exists())
