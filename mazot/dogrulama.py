"""Mazot fişi alanları için merkezi iş kuralı doğrulaması."""

from __future__ import annotations

import re
from datetime import datetime


BOS = {"", "-", "—", "none", "null"}


def bos_mu(deger) -> bool:
    return deger is None or str(deger).strip().lower() in BOS


def anlamli_metin_mi(deger) -> bool:
    if bos_mu(deger):
        return False
    metin = str(deger).strip()
    return len(metin) >= 3 and sum(c.isalpha() for c in metin) >= 2


def tarih_gecerli_mi(deger) -> bool:
    if bos_mu(deger):
        return False
    metin = str(deger).strip().replace(".", "/").replace("-", "/")
    try:
        tarih = datetime.strptime(metin, "%d/%m/%Y")
    except ValueError:
        return False
    return tarih.year >= 2026


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
    elif not re.fullmatch(r"\d{6}", fis_no):
        nedenler.append("fiş no tam 6 rakam olmalı")

    if not tarih_gecerli_mi(veri.get("tarih")):
        nedenler.append("tarih geçersiz veya yıl 2026'dan küçük")

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
