"""Mazot fişi alanları için merkezi iş kuralı doğrulaması."""

from __future__ import annotations

import re
from datetime import date, datetime


BOS = {"", "-", "—", "none", "null"}


def bos_mu(deger) -> bool:
    return deger is None or str(deger).strip().lower() in BOS


def anlamli_metin_mi(deger) -> bool:
    if bos_mu(deger):
        return False
    metin = str(deger).strip()
    return len(metin) >= 3 and sum(c.isalpha() for c in metin) >= 2


# Fiş tarihi, fişin yüklendiği günden ileride ve ondan bu kadar ay eski olamaz.
TARIH_EN_FAZLA_AY = 3
FIS_NO_DESENI = re.compile(r"\d{5,6}")


def tarihi_coz(deger) -> date | None:
    if bos_mu(deger):
        return None
    m = re.fullmatch(r"\s*(\d{1,2})[./\-](\d{1,2})[./\-](\d{4})\s*", str(deger))
    if not m:
        return None
    try:
        return date(int(m[3]), int(m[2]), int(m[1]))
    except ValueError:
        return None


def _ay_once(gun: date, ay: int) -> date:
    yil, ay_no = divmod(gun.year * 12 + gun.month - 1 - ay, 12)
    ay_no += 1
    for son_gun in (gun.day, 30, 29, 28):  # 31 Mayıs - 3 ay → 28/29 Şubat
        try:
            return date(yil, ay_no, son_gun)
        except ValueError:
            continue
    raise ValueError(gun)


def tarih_araligi(veri: dict, bugun: date | None = None) -> tuple[date, date]:
    """Geçerli fiş tarihi aralığı. Referans fişin yüklendiği gündür (yoksa
    bugün): eski partiler zaman geçtikçe geçersiz sayılmaz."""
    referans = bugun or date.today()
    yukleme = veri.get("_yukleme_zamani")
    if yukleme:
        try:
            referans = datetime.fromisoformat(str(yukleme)).date()
        except ValueError:
            pass
    return _ay_once(referans, TARIH_EN_FAZLA_AY), referans


def tarih_sorunu(veri: dict, bugun: date | None = None) -> str | None:
    tarih = tarihi_coz(veri.get("tarih"))
    if tarih is None:
        return "tarih geçersiz (GG.AA.YYYY olmalı)"
    en_erken, en_gec = tarih_araligi(veri, bugun)
    if tarih > en_gec:
        return "tarih yükleme gününden ileride olamaz"
    if tarih < en_erken:
        return f"tarih {TARIH_EN_FAZLA_AY} aydan eski olamaz"
    return None


def tarih_gecerli_mi(deger, veri: dict | None = None) -> bool:
    return tarih_sorunu({**(veri or {}), "tarih": deger}) is None


def tarih_yilini_duzelt(veri: dict, bugun: date | None = None) -> bool:
    """OCR'ın en sık tarih hatası yıldır (2016/2020 → 2026; gün ve ay doğru).
    Tarih aralık dışındaysa ve yalnız yıl değiştirilince aralığa giren TEK bir
    aday varsa onu yazar ve _otomatik_duzeltmeler'e ekler. Gün/ay hataları
    birden çok aday verdiği için tahmin edilmez, doğrulama işaretler.
    Yalnız yeni okunan fişte çağrılır; onaylı kayıtlara dokunulmaz."""
    if veri.get("_onaylandi") or tarih_sorunu(veri, bugun) is None:
        return False
    tarih = tarihi_coz(veri.get("tarih"))
    if tarih is None:
        return False
    en_erken, en_gec = tarih_araligi(veri, bugun)
    adaylar = []
    for yil in {en_gec.year, en_erken.year}:
        try:
            aday = tarih.replace(year=yil)
        except ValueError:  # 29 Şubat
            continue
        if en_erken <= aday <= en_gec:
            adaylar.append(aday)
    if len(adaylar) != 1:
        return False
    eski = veri.get("tarih")
    veri["tarih"] = adaylar[0].strftime("%d.%m.%Y")
    veri.setdefault("_otomatik_duzeltmeler", []).append({
        "alan": "tarih", "eski": eski, "yeni": veri["tarih"],
        "neden": "OCR yılı yanlış okudu; tarih aralığına giren tek yıl",
    })
    veri["_otomatik_duzeltildi"] = True
    return True


def kilometre_gecerli_mi(deger) -> bool:
    if bos_mu(deger):
        return False
    metin = re.sub(r"\s+", "", str(deger))
    if not re.fullmatch(r"\d+", metin):
        return False
    return int(metin) > 0


def litre_gecerli_mi(deger) -> bool:
    if bos_mu(deger):
        return False
    metin = re.sub(r"\s+", "", str(deger)).replace(",", ".")
    if not re.fullmatch(r"\d+(?:\.\d+)?", metin):
        return False
    return float(metin) > 0


def plaka_gecerli_mi(deger) -> bool:
    """Standart Türk plakasını geniş ama güvenli bir formatla kontrol eder."""
    if bos_mu(deger):
        return False
    metin = re.sub(r"\s+", " ", str(deger).upper()).strip()
    eslesme = re.fullmatch(r"(\d{2})\s+([A-ZÇĞİÖŞÜ]{1,3})\s+(\d{2,4})", metin)
    if not eslesme:
        return False
    il_kodu = int(eslesme.group(1))
    return 1 <= il_kodu <= 81


def kaydi_dogrula(veri: dict) -> dict:
    """Kaydı yerinde doğrular, kontrol bayrağı ve açıklamalarını günceller."""
    if veri.get("_isleniyor"):
        veri["_kontrol_gerekli"] = True
        veri["_kontrol_nedeni"] = "OCR kuyruğunda"
        return veri

    nedenler: list[str] = []
    if veri.get("HATA"):
        nedenler.append("okuma hatası")
    if veri.get("_tekrar"):
        nedenler.append(veri.get("_tekrar_nedeni") or "tekrar fiş tespit edildi")

    fis_no = str(veri.get("fis_no") or "").strip()
    if fis_no.startswith("-"):
        nedenler.append("fiş no '-' ile başlayamaz")
    elif not FIS_NO_DESENI.fullmatch(fis_no):
        nedenler.append("fiş no 5 veya 6 rakam olmalı")

    sorun = tarih_sorunu(veri)
    if sorun:
        nedenler.append(sorun)

    if veri.get("_ekipman_durumu"):
        if not anlamli_metin_mi(veri.get("plaka")):
            nedenler.append("ekipman adı boş/geçersiz")
        nedenler.append("ekipman fişi (isim ve ekipman adı elle kontrol edilmeli)")
    elif not plaka_gecerli_mi(veri.get("plaka")):
        nedenler.append("plaka formatı geçersiz")

    if not anlamli_metin_mi(veri.get("firma")):
        nedenler.append("firma boş veya anlamsız")
    if not anlamli_metin_mi(veri.get("ad_soyad")):
        nedenler.append("ad soyad boş veya anlamsız")
    if not kilometre_gecerli_mi(veri.get("km")):
        nedenler.append("kilometre boş veya geçersiz")
    if not litre_gecerli_mi(veri.get("litre")):
        nedenler.append("litre boş veya geçersiz")

    # Sıralamayı koruyarak olası tekrarları kaldır.
    nedenler = list(dict.fromkeys(nedenler))
    veri["_kontrol_gerekli"] = bool(nedenler)
    veri["_kontrol_nedeni"] = "; ".join(nedenler)
    return veri
