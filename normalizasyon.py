"""OCR sonuçlarını arayüz ve Excel için tek biçime getirir."""

from __future__ import annotations

import re


TABLO_ALANLARI = ("fis_no", "tarih", "firma", "plaka", "ad_soyad", "km", "litre")
BOS_DEGER = "-"


def bos_degeri_duzelt(deger) -> str:
    """None, boş metin ve yalnızca boşluk içeren değerleri '-' yapar."""
    if deger is None:
        return BOS_DEGER
    metin = str(deger).strip()
    return metin if metin else BOS_DEGER


def plakayi_duzelt(plaka, ekipman_durumu: bool = False) -> str:
    """İl kodu atlanan araç plakalarının başına İstanbul kodu olan 34'ü ekler.

    Ekipman adları (ör. ``KAZIK MAK.``) değiştirilmez. Zaten iki rakamlı bir
    il koduyla başlayan plakaya ikinci kez kod eklenmez.
    """
    metin = bos_degeri_duzelt(plaka)
    metin = re.sub(r"\s+", " ", metin)
    if ekipman_durumu or metin == BOS_DEGER:
        return metin
    if re.match(r"^\d{2}", metin):
        return metin
    if any(c.isalpha() for c in metin) and any(c.isdigit() for c in metin):
        return f"34 {metin}"
    return metin


def fis_normalize_et(veri: dict) -> dict:
    """Bir fiş kaydını yerinde günceller ve aynı sözlüğü döndürür."""
    for alan in TABLO_ALANLARI:
        veri[alan] = bos_degeri_duzelt(veri.get(alan))
    veri["plaka"] = plakayi_duzelt(
        veri.get("plaka"), bool(veri.get("_ekipman_durumu"))
    )
    return veri
