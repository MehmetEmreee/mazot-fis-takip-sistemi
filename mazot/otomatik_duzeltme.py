"""Geçmiş kayıtlara dayalı OCR sonrası otomatik düzeltme (firma, plaka, ad-soyad).

excel_ogrenme.guvenli_otomatik_duzelt'in benzerlik ölçüsünü iyileştirir:
- WRatio yerine tam metin oranı: kısa 'ALİ', uzun 'ALİ RIZA KAYA' için 90 puan almaz.
- El yazısı OCR karışıklıkları (2/Z, 5/S, 6/G, 8/B, 0/O/D) karşılaştırmadan önce
  katlanır; plakada harf ve rakam bölümleri ayrı katlanır (34 K08 659 ~ 34 KDB 659).
- Aynı fişte geçmişte birlikte görülen plaka ve ad-soyad birbirini destekler.
Eşikler test seti dışındaki onaylı fişlerde ayarlanmıştır (tools/duzeltme_benchmark.py).
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field

from rapidfuzz import fuzz

from .excel_ogrenme import arama_anahtari

RAKAM_HARF = str.maketrans({"0": "O", "1": "I", "2": "Z", "4": "A", "5": "S",
                            "6": "G", "7": "T", "8": "B", "3": "B"})
HARF_RAKAM = str.maketrans({"O": "0", "D": "0", "Q": "0", "U": "0", "I": "1",
                            "L": "1", "Z": "2", "S": "5", "G": "6", "B": "8",
                            "T": "7", "A": "4"})
PLAKA_KALIBI = re.compile(r"(\d{2})([A-Z]{1,3})(\d{2,5})")


@dataclass
class Ayar:
    # 693 test dışı fişte 2 katlı taramayla seçildi (duzeltme_benchmark --izgara)
    esik: dict = field(default_factory=lambda: {"firma": 80, "plaka": 80, "ad_soyad": 75})
    fark: int = 3          # en iyi aday ile ikinci arasındaki en az puan farkı
    ortak_bonus: int = 10  # plaka-ad birlikteliği varsa eşikten düşülecek puan
    en_az_adet: int = 2    # adayın geçmişte en az kaç kez görülmüş olması


def _sade(deger) -> str:
    return arama_anahtari(deger).replace(" ", "")


def isim_benzerligi(a, b) -> float:
    sol, sag = _sade(a).translate(RAKAM_HARF), _sade(b).translate(RAKAM_HARF)
    if not sol or not sag:
        return 0.0
    return fuzz.ratio(sol, sag)


def plaka_benzerligi(okunan, aday) -> float:
    """Adayın plaka kalıbına göre okunan metni bölüm bölüm katlayıp karşılaştırır."""
    ok, ad = _sade(okunan), _sade(aday)
    if not ok or not ad:
        return 0.0
    m = PLAKA_KALIBI.fullmatch(ad)
    if m and len(ok) == len(ad):
        il, harf, sayi = (len(g) for g in m.groups())
        ok = (ok[:il].translate(HARF_RAKAM)
              + ok[il:il + harf].translate(RAKAM_HARF)
              + ok[il + harf:].translate(HARF_RAKAM))
        return fuzz.ratio(ok, ad)
    return isim_benzerligi(ok, ad)


BENZERLIK = {"firma": isim_benzerligi, "ad_soyad": isim_benzerligi,
             "plaka": plaka_benzerligi}


class Sozluk:
    """Geçmiş değerler (kanonik yazım + adet) ve plaka↔ad birlikteliği."""

    def __init__(self, alanlar: dict[str, list[dict]], ornekler: list[dict] = ()):
        self.degerler: dict[str, dict[str, dict]] = {}
        for alan, liste in alanlar.items():
            d = {}
            for oge in liste:
                anahtar = _sade(oge.get("deger"))
                if not anahtar:
                    continue
                mevcut = d.get(anahtar)
                adet = int(oge.get("adet", 0))
                if mevcut:
                    mevcut["adet"] += adet
                else:
                    d[anahtar] = {"deger": str(oge.get("deger")).strip(), "adet": adet,
                                  "kullanici_adet": int(oge.get("kullanici_adet", 0))}
            self.degerler[alan] = d
        self.birlikte: dict[tuple[str, str], Counter] = defaultdict(Counter)
        for ornek in ornekler:
            p, a = _sade(ornek.get("plaka")), _sade(ornek.get("ad_soyad"))
            if p and a:
                self.birlikte[("plaka", p)][a] += 1
                self.birlikte[("ad_soyad", a)][p] += 1

    def adaylar(self, alan: str, metin: str) -> list[tuple[float, str, dict]]:
        benzer = BENZERLIK[alan]
        puanli = [(benzer(metin, oge["deger"]), anahtar, oge)
                  for anahtar, oge in self.degerler.get(alan, {}).items()]
        puanli.sort(key=lambda x: (-x[0], -x[2]["kullanici_adet"], -x[2]["adet"]))
        return puanli[:5]

    def birlikte_mi(self, alan: str, anahtar: str, diger_alan: str, diger_anahtar: str) -> bool:
        return self.birlikte.get((alan, anahtar), Counter()).get(diger_anahtar, 0) > 0


def duzelt(kayit: dict, sozluk: Sozluk, ayar: Ayar | None = None,
           anlamli=None) -> list[dict]:
    """Kayıttaki firma/plaka/ad_soyad alanlarını yerinde düzeltir; değişiklikleri döndürür."""
    ayar = ayar or Ayar()
    degisiklikler = []
    adaylar = {}
    for alan in ("firma", "plaka", "ad_soyad"):
        mevcut = str(kayit.get(alan) or "").strip()
        if anlamli and not anlamli(mevcut, alan):
            continue
        if _sade(mevcut) in sozluk.degerler.get(alan, {}):
            # Geçmişte aynen var: yalnız yazımı kanonikleştir.
            adaylar[alan] = (100.0, _sade(mevcut), sozluk.degerler[alan][_sade(mevcut)], 100.0)
            continue
        liste = sozluk.adaylar(alan, mevcut)
        if not liste:
            continue
        ikinci = next((p for p, a, o in liste[1:] if o["deger"] != liste[0][2]["deger"]), 0)
        adaylar[alan] = (*liste[0], liste[0][0] - ikinci)

    for alan, (puan, anahtar, oge, fark) in adaylar.items():
        esik = ayar.esik[alan]
        diger = {"plaka": "ad_soyad", "ad_soyad": "plaka"}.get(alan)
        if diger and diger in adaylar and adaylar[diger][0] >= ayar.esik[diger] - ayar.ortak_bonus:
            if sozluk.birlikte_mi(alan, anahtar, diger, adaylar[diger][1]):
                esik -= ayar.ortak_bonus
        if puan < esik or fark < ayar.fark or oge["adet"] < ayar.en_az_adet:
            continue
        mevcut = str(kayit.get(alan) or "").strip()
        if oge["deger"] != mevcut:
            kayit[alan] = oge["deger"]
            degisiklikler.append({"alan": alan, "eski": mevcut, "yeni": oge["deger"],
                                  "benzerlik": round(puan), "neden": "geçmiş kayıt benzerliği"})
    return degisiklikler
