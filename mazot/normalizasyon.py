"""OCR sonuçlarını arayüz ve Excel için tek biçime getirir."""

from __future__ import annotations

import re
from datetime import date


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
    """Boş veya yalnızca rakamlardan oluşan anlamsız firma değerini düzeltir.

    Model bazen basılı 'Sayın:' etiketini veya 'null' metnini değer diye yazar;
    etiket atılır, geriye bir şey kalmazsa varsayılan firma kullanılır.
    """
    metin = "" if firma is None else str(firma).strip()
    metin = re.sub(r"^\s*SAY[Iİı]N\b\s*:?\s*", "", metin, flags=re.IGNORECASE).strip()
    if metin.casefold() in {"null", "none"}:
        metin = ""
    if not metin or metin == BOS_DEGER or metin.isdigit():
        return VARSAYILAN_FIRMA
    return metin


TARIH_DESENI = re.compile(r"^\s*(\d{1,2})\s*[./\-]\s*(\d{1,2})\s*[./\-]\s*(\d{4}|\d{2})\s*$")


def tarihi_duzelt(tarih) -> str:
    """Tarihi tek biçime getirir: GG.AA.YYYY (8/10/2026 → 08.10.2026,
    07-10-26 → 07.10.2026). Takvimde olmayan veya çözülemeyen değer olduğu
    gibi bırakılır; doğrulama onu ayrıca işaretler."""
    metin = bos_degeri_duzelt(tarih)
    m = TARIH_DESENI.match(metin)
    if not m:
        return metin
    gun, ay, yil = int(m[1]), int(m[2]), int(m[3])
    if yil < 100:
        yil += 2000
    try:
        return date(yil, ay, gun).strftime("%d.%m.%Y")
    except ValueError:
        return metin


def kilometreyi_duzelt(km) -> str:
    """Tek haneli OCR kilometrelerini okunamamış değer olarak işaretler."""
    metin = bos_degeri_duzelt(km)
    return BOS_DEGER if re.fullmatch(r"\d", metin) else metin


def fis_normalize_et(veri: dict) -> dict:
    """Bir fiş kaydını yerinde günceller ve aynı sözlüğü döndürür."""
    for alan in TABLO_ALANLARI:
        veri[alan] = bos_degeri_duzelt(veri.get(alan))
    veri["firma"] = firmayi_duzelt(
        None if veri.get("firma") == BOS_DEGER else veri.get("firma")
    )
    veri["km"] = kilometreyi_duzelt(veri.get("km"))
    veri["tarih"] = tarihi_duzelt(veri.get("tarih"))
    veri["plaka"] = plakayi_duzelt(
        veri.get("plaka"), bool(veri.get("_ekipman_durumu"))
    )
    return veri
