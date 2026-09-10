"""OCR sonuçlarını arayüz ve Excel için tek biçime getirir."""

from __future__ import annotations

import re


TABLO_ALANLARI = ("fis_no", "tarih", "firma", "plaka", "ad_soyad", "km", "litre")
BOS_DEGER = "-"
VARSAYILAN_FIRMA = "Selim İnşaat"


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


def firmayi_duzelt(firma) -> str:
    """Boş veya yalnızca rakamlardan oluşan anlamsız firma değerini düzeltir."""
    metin = "" if firma is None else str(firma).strip()
    if not metin or metin == BOS_DEGER or metin.isdigit():
        return VARSAYILAN_FIRMA
    return metin


def fis_normalize_et(veri: dict) -> dict:
    """Bir fiş kaydını yerinde günceller ve aynı sözlüğü döndürür."""
    for alan in TABLO_ALANLARI:
        veri[alan] = bos_degeri_duzelt(veri.get(alan))
    veri["firma"] = firmayi_duzelt(
        None if veri.get("firma") == BOS_DEGER else veri.get("firma")
    )
    veri["plaka"] = plakayi_duzelt(
        veri.get("plaka"), bool(veri.get("_ekipman_durumu"))
    )
    return veri
