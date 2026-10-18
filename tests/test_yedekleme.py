"""Artımlı tam yedek: alma, doğrulama, eksiksiz geri yükleme ve saklama."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from mazot import yedekleme


def agac_ozeti(kok: Path) -> dict[str, str]:
    return {
        y.relative_to(kok).as_posix(): hashlib.sha256(y.read_bytes()).hexdigest()
        for y in sorted(kok.rglob("*"))
        if y.is_file() and yedekleme._yedeklenecek_mi(y.relative_to(kok))
    }


class YedeklemeTesti(unittest.TestCase):
    def setUp(self) -> None:
        self._gecici = tempfile.TemporaryDirectory()
        self.kok = Path(self._gecici.name).resolve() / "data"
        k = self.kok
        (k / "fisler" / "Selim İnşaat").mkdir(parents=True)
        (k / "fisler" / "Selim İnşaat" / "100001.jpg").write_bytes(os.urandom(5000))
        (k / "arsiv" / "parti_1" / "fisler").mkdir(parents=True)
        (k / "arsiv" / "parti_1" / "fisler" / "100000.jpg").write_bytes(os.urandom(7000))
        (k / "arsiv" / "parti_1" / "sonuclar.json").write_text('[{"_dosya": "100000.jpg"}]', encoding="utf-8")
        (k / "sonuclar.json").write_text('[{"_dosya": "100001.jpg"}]', encoding="utf-8")
        (k / "denetim").mkdir()
        (k / "denetim" / "2026-10.jsonl").write_text('{"islem": "onayla"}\n', encoding="utf-8")
        (k / "gelen_fisler" / "_islendi").mkdir(parents=True)
        (k / "gelen_fisler" / "_islendi" / "eski.jpg").write_bytes(b"islenmis")
        (k / "gelen_fisler" / "bekleyen.jpg").write_bytes(b"bekleyen")
        (k / "yedekler" / "excel_otomatik").mkdir(parents=True)
        self.ilk = agac_ozeti(k)

    def tearDown(self) -> None:
        self._gecici.cleanup()

    def test_eksiksiz_geri_yukleme(self):
        anlik = yedekleme.anlik_yedek_al(self.kok)
        self.assertEqual(anlik["dosya_sayisi"], len(self.ilk))
        self.assertNotIn("gelen_fisler/_islendi/eski.jpg",
                         [d["yol"] for d in yedekleme._manifest(self.kok, None)["dosyalar"]])
        # Felaket: görsel silindi, kayıt bozuldu, arşiv gitti, yabancı dosya geldi.
        (self.kok / "fisler" / "Selim İnşaat" / "100001.jpg").unlink()
        (self.kok / "sonuclar.json").write_text("bozuk", encoding="utf-8")
        shutil.rmtree(self.kok / "arsiv")
        (self.kok / "yeni.json").write_text("{}", encoding="utf-8")

        rapor = yedekleme.geri_yukle(self.kok, anlik["kimlik"])
        self.assertEqual(agac_ozeti(self.kok), self.ilk)
        self.assertEqual(rapor["geri_yuklenen"], len(self.ilk))
        # Mevcut veri silinmedi, kenara alındı; işlenmiş gelen kopyaları yerinde.
        kenar = Path(rapor["onceki_veri"])
        self.assertEqual((kenar / "sonuclar.json").read_text(encoding="utf-8"), "bozuk")
        self.assertTrue((kenar / "yeni.json").exists())
        self.assertTrue((self.kok / "gelen_fisler" / "_islendi" / "eski.jpg").exists())

    def test_artimli_yedek_ayni_dosyayi_tekrar_saklamaz(self):
        yedekleme.anlik_yedek_al(self.kok, "a")
        ikinci = yedekleme.anlik_yedek_al(self.kok, "b")
        self.assertEqual((ikinci["yeni_nesne"], ikinci["yeni_bayt"], ikinci["okunan_dosya"]), (0, 0, 0))
        (self.kok / "sonuclar.json").write_text("[]", encoding="utf-8")
        ucuncu = yedekleme.anlik_yedek_al(self.kok, "c")
        self.assertEqual(ucuncu["yeni_nesne"], 1)

    def test_bozuk_yedek_geri_yuklenmez(self):
        anlik = yedekleme.anlik_yedek_al(self.kok)
        nesne = next(p for p in (self.kok / "yedekler" / "tam" / "nesneler").rglob("*")
                     if p.is_file())
        nesne.write_bytes(b"bit rot")
        self.assertFalse(yedekleme.yedegi_dogrula(self.kok)["saglam"])
        (self.kok / "sonuclar.json").write_text("guncel", encoding="utf-8")
        with self.assertRaises(yedekleme.YedekHatasi):
            yedekleme.geri_yukle(self.kok, anlik["kimlik"])
        self.assertEqual((self.kok / "sonuclar.json").read_text(encoding="utf-8"), "guncel")

    def test_yarida_kalan_geri_yukleme_mevcut_veriyi_geri_koyar(self):
        anlik = yedekleme.anlik_yedek_al(self.kok)
        (self.kok / "sonuclar.json").write_text("guncel", encoding="utf-8")
        once = agac_ozeti(self.kok)
        gercek = shutil.copyfile
        sayac = {"n": 0}

        def bozuk(*a, **k):
            sayac["n"] += 1
            if sayac["n"] == 3:
                raise OSError("disk dolu")
            return gercek(*a, **k)

        with mock.patch.object(yedekleme.shutil, "copyfile", bozuk):
            with self.assertRaises(OSError):
                yedekleme.geri_yukle(self.kok, anlik["kimlik"])
        self.assertEqual(agac_ozeti(self.kok), once)

    def test_saklama_kurali_ve_cop_toplama(self):
        for i in range(5):
            (self.kok / "gecici.json").write_text(str(i), encoding="utf-8")
            yedekleme.anlik_yedek_al(self.kok, f"s{i}")
        sonuc = yedekleme.eski_anliklari_temizle(self.kok, tutulacak=2)
        self.assertEqual(len(sonuc["silinen_anlik"]), 3)
        self.assertEqual(sonuc["silinen_nesne"], 3)  # artık kimsenin kullanmadığı içerikler
        for anlik in yedekleme.anliklari_listele(self.kok):
            self.assertTrue(yedekleme.yedegi_dogrula(self.kok, anlik["kimlik"])["saglam"])

    def test_gunluk_yedek_gunde_bir_kez_ve_eski_excel_yedekleri_temizlenir(self):
        eski = self.kok / "yedekler" / "excel_otomatik" / "d_2026-01-01_aktarim_oncesi.xlsx"
        eski.write_bytes(b"x")
        os.utime(eski, (time.time() - 200 * 86400,) * 2)
        self.assertIsNotNone(yedekleme.gunluk_yedek(self.kok))
        self.assertIsNone(yedekleme.gunluk_yedek(self.kok))
        self.assertFalse(eski.exists())


if __name__ == "__main__":
    unittest.main()
