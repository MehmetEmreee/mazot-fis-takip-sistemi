"""Gerçek kullanıcı düzeltmelerinde metin benzerliği yöntemlerini karşılaştırır."""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

from rapidfuzz import fuzz
from rapidfuzz.distance import JaroWinkler

import excel_ogrenme
from normalizasyon import bos_degeri_duzelt, firmayi_duzelt, plakayi_duzelt


def ham_deger(kayit: dict, alan: str) -> str:
    ham = kayit.get("_ham") or {}
    if alan == "firma":
        deger = ham.get("sayin_alani")
        if str(deger or "").strip().upper() in excel_ogrenme.GECERSIZ_METINLER:
            deger = None
        return firmayi_duzelt(deger)
    if alan == "plaka":
        return plakayi_duzelt(
            ham.get("arac_plakasi_alani"), bool(kayit.get("_ekipman_durumu"))
        )
    return bos_degeri_duzelt(ham.get("adi_soyadi_alani"))


def skorlar(sol: str, sag: str) -> dict[str, float]:
    a = excel_ogrenme.arama_anahtari(sol)
    b = excel_ogrenme.arama_anahtari(sag)
    mevcut = float(excel_ogrenme.benzerlik(sol, sag))
    jaro = JaroWinkler.normalized_similarity(a, b) * 100
    wratio = float(fuzz.WRatio(a, b))
    return {
        "mevcut": mevcut,
        "jaro_winkler": jaro,
        "wratio": wratio,
        "hibrit": 0.45 * jaro + 0.35 * wratio + 0.20 * mevcut,
    }


def main() -> None:
    kayitlar = json.loads(Path("sonuclar.json").read_text(encoding="utf-8"))
    adaylar = defaultdict(set)
    for kayit in kayitlar:
        if kayit.get("_onaylandi"):
            for alan in excel_ogrenme.ALAN_SUTUNLARI:
                deger = str(kayit.get(alan) or "").strip()
                if excel_ogrenme._anlamli(deger, alan):
                    adaylar[alan].add(deger)

    ornekler = []
    for kayit in kayitlar:
        if not (kayit.get("_onaylandi") and kayit.get("_duzeltildi")):
            continue
        for alan in excel_ogrenme.ALAN_SUTUNLARI:
            onceki = ham_deger(kayit, alan)
            dogru = str(kayit.get(alan) or "").strip()
            if (
                excel_ogrenme._anlamli(onceki, alan)
                and excel_ogrenme._anlamli(dogru, alan)
                and excel_ogrenme.arama_anahtari(onceki)
                != excel_ogrenme.arama_anahtari(dogru)
            ):
                ornekler.append((alan, onceki, dogru))

    yontemler = ("mevcut", "jaro_winkler", "wratio", "hibrit")
    sonuclar = {ad: {"dogru": 0, "toplam": 0, "ortalama_marj": 0.0} for ad in yontemler}
    for alan, onceki, dogru in ornekler:
        for yontem in yontemler:
            puanlar = sorted(
                ((skorlar(onceki, aday)[yontem], aday) for aday in adaylar[alan]),
                reverse=True,
            )
            dogru_anahtar = excel_ogrenme.arama_anahtari(dogru)
            dogru_puan = max(
                (puan for puan, aday in puanlar
                 if excel_ogrenme.arama_anahtari(aday) == dogru_anahtar),
                default=0,
            )
            yanlis_puan = max(
                (puan for puan, aday in puanlar
                 if excel_ogrenme.arama_anahtari(aday) != dogru_anahtar),
                default=0,
            )
            sonuc = sonuclar[yontem]
            sonuc["toplam"] += 1
            sonuc["dogru"] += int(dogru_puan > yanlis_puan)
            sonuc["ortalama_marj"] += dogru_puan - yanlis_puan

    print(f"Gercek yanlis->dogru ornek sayisi: {len(ornekler)}")
    for yontem, sonuc in sonuclar.items():
        toplam = sonuc["toplam"] or 1
        print(
            f"{yontem:15} dogru aday: {sonuc['dogru']:3}/{toplam} "
            f"(%{sonuc['dogru'] / toplam * 100:5.1f}) - "
            f"ortalama marj: {sonuc['ortalama_marj'] / toplam:5.1f}"
        )


if __name__ == "__main__":
    main()
