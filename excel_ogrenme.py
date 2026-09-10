"""Geçmiş Excel kayıtlarından güvenli OCR düzeltme önerileri üretir.

Bu modül hiçbir alanı kendiliğinden değiştirmez. Excel'deki firma, plaka ve
ad-soyad değerlerini bir referans sözlüğüne dönüştürür; yeni OCR metniyle
yakın olan tek bir adayı, benzerlik yüzdesiyle birlikte arayüze verir.
"""

from __future__ import annotations

import json
import os
import re
import unicodedata
import uuid
from collections import Counter
from datetime import datetime
from difflib import SequenceMatcher
from functools import lru_cache
from pathlib import Path

from openpyxl import load_workbook


ROOT = Path(__file__).resolve().parent
REFERANS_FILE = ROOT / "excel_referans.json"
KULLANICI_FILE = ROOT / "kullanici_ogrenmeleri.json"
ALAN_SUTUNLARI = {"firma": 3, "plaka": 4, "ad_soyad": 5}
ESIKLER = {"firma": 68, "plaka": 72, "ad_soyad": 70}
GECERSIZ_METINLER = {"", "-", "—", "NONE", "NULL"}


@lru_cache(maxsize=4096)
def arama_anahtari(deger) -> str:
    """Türkçe karakter ve noktalama farklarını karşılaştırma için azaltır."""
    metin = str(deger or "").strip().upper().replace("İ", "I").replace("ı", "I")
    metin = "".join(
        karakter for karakter in unicodedata.normalize("NFKD", metin)
        if not unicodedata.combining(karakter)
    )
    return re.sub(r"[^A-Z0-9]+", " ", metin).strip()


def _anlamli(deger, alan: str) -> bool:
    metin = str(deger or "").strip()
    if metin.upper() in GECERSIZ_METINLER:
        return False
    if alan in {"firma", "ad_soyad"} and sum(c.isalpha() for c in metin) < 2:
        return False
    anahtar = arama_anahtari(metin)
    if alan == "firma" and any(kelime in anahtar for kelime in ("BAKIYE", "TOPLAM")):
        return False
    return len(anahtar) >= 2


def _basliklari_dogrula(ws) -> None:
    basliklar = [arama_anahtari(ws.cell(1, sutun).value) for sutun in (3, 4, 5)]
    if not ("FIRMA" in basliklar[0] and "PLAKA" in basliklar[1]
            and ("ADI" in basliklar[2] or "SOYADI" in basliklar[2])):
        raise ValueError(
            "Seçilen sayfanın C, D ve E sütunları FİRMA, ARAÇ PLAKASI ve "
            "ADI SOYADI düzeninde değil"
        )


def _kaynaklari_cikar(veri: dict | None) -> list[dict]:
    """Eski tek-kaynak şemasını da yeni biriktirmeli şemaya dönüştürür."""
    if not isinstance(veri, dict):
        return []
    if isinstance(veri.get("kaynaklar"), list):
        return [kaynak for kaynak in veri["kaynaklar"] if isinstance(kaynak, dict)]
    if isinstance(veri.get("alanlar"), dict):
        return [{
            "kaynak": veri.get("kaynak"),
            "kaynak_adi": veri.get("kaynak_adi"),
            "sayfa": veri.get("sayfa"),
            "ogrenme_zamani": veri.get("ogrenme_zamani"),
            "satir_sayisi": veri.get("satir_sayisi", 0),
            "alanlar": veri.get("alanlar", {}),
        }]
    return []


def _kaynak_anahtari(kaynak: dict) -> tuple[str, str]:
    yol = str(kaynak.get("kaynak") or kaynak.get("kaynak_adi") or "").casefold()
    sayfa = str(kaynak.get("sayfa") or "").strip().casefold()
    return yol, sayfa


def _kaynaklari_birlestir(kaynaklar: list[dict]) -> dict | None:
    if not kaynaklar:
        return None
    alanlar = {}
    for alan in ALAN_SUTUNLARI:
        gruplar: dict[str, Counter] = {}
        for kaynak in kaynaklar:
            for oge in kaynak.get("alanlar", {}).get(alan, []):
                deger = str(oge.get("deger") or "").strip()
                if not deger:
                    continue
                adet = int(oge.get("adet", 0))
                gruplar.setdefault(arama_anahtari(deger), Counter())[deger] += adet
        alanlar[alan] = [
            {"deger": varyantlar.most_common(1)[0][0], "adet": sum(varyantlar.values())}
            for varyantlar in gruplar.values()
        ]
        alanlar[alan].sort(key=lambda oge: (-oge["adet"], oge["deger"]))

    kaynak_sayisi = len(kaynaklar)
    sayfalar = [str(k.get("sayfa") or "-") for k in kaynaklar]
    return {
        "surum": 2,
        "kaynak_adi": (
            str(kaynaklar[0].get("kaynak_adi") or "Excel")
            if kaynak_sayisi == 1 else f"{kaynak_sayisi} Excel sayfası"
        ),
        "sayfa": sayfalar[0] if kaynak_sayisi == 1 else ", ".join(sayfalar),
        "ogrenme_zamani": max(
            (str(k.get("ogrenme_zamani") or "") for k in kaynaklar), default=""
        ),
        "satir_sayisi": sum(int(k.get("satir_sayisi", 0)) for k in kaynaklar),
        "kaynak_sayisi": kaynak_sayisi,
        "kaynaklar": kaynaklar,
        "alanlar": alanlar,
    }


def referans_olustur(excel_yolu: Path, sayfa: str) -> dict:
    """Seçilen sayfadaki metin alanlarını sık kullanım bilgisiyle kaydeder."""
    excel_yolu = Path(excel_yolu).resolve()
    if not excel_yolu.is_file():
        raise ValueError(f"Excel dosyası bulunamadı: {excel_yolu}")

    wb = load_workbook(excel_yolu, read_only=True, data_only=True)
    try:
        if sayfa not in wb.sheetnames:
            raise ValueError(f"'{sayfa}' sayfası bulunamadı")
        ws = wb[sayfa]
        _basliklari_dogrula(ws)
        sayaclar = {alan: Counter() for alan in ALAN_SUTUNLARI}
        satir_sayisi = 0
        for satir in ws.iter_rows(min_row=2, values_only=True):
            satirda_deger = False
            for alan, sutun in ALAN_SUTUNLARI.items():
                deger = satir[sutun - 1] if len(satir) >= sutun else None
                if _anlamli(deger, alan):
                    sayaclar[alan][str(deger).strip()] += 1
                    satirda_deger = True
            satir_sayisi += int(satirda_deger)

        alanlar = {}
        for alan, sayac in sayaclar.items():
            # Yazım/case varyantlarını aynı anahtarda birleştir; en sık görülen
            # biçimi kullanıcıya önerilecek kanonik değer yap.
            gruplar: dict[str, Counter] = {}
            for deger, adet in sayac.items():
                gruplar.setdefault(arama_anahtari(deger), Counter())[deger] += adet
            alanlar[alan] = [
                {"deger": varyantlar.most_common(1)[0][0], "adet": sum(varyantlar.values())}
                for varyantlar in gruplar.values()
            ]
            alanlar[alan].sort(key=lambda kayit: (-kayit["adet"], kayit["deger"]))

        yeni_kaynak = {
            "kaynak": str(excel_yolu),
            "kaynak_adi": excel_yolu.name,
            "sayfa": sayfa,
            "ogrenme_zamani": datetime.now().isoformat(timespec="seconds"),
            "satir_sayisi": satir_sayisi,
            "alanlar": alanlar,
        }
        mevcut_veri = None
        if REFERANS_FILE.exists():
            try:
                mevcut_veri = json.loads(REFERANS_FILE.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                mevcut_veri = None
        kaynaklar = _kaynaklari_cikar(mevcut_veri)
        yeni_anahtar = _kaynak_anahtari(yeni_kaynak)
        kaynaklar = [
            kaynak for kaynak in kaynaklar
            if _kaynak_anahtari(kaynak) != yeni_anahtar
        ]
        kaynaklar.append(yeni_kaynak)
        referans = {
            "surum": 2,
            "guncelleme_zamani": datetime.now().isoformat(timespec="seconds"),
            "kaynaklar": kaynaklar,
        }
        gecici = REFERANS_FILE.with_name(f".{REFERANS_FILE.name}.{uuid.uuid4().hex}.tmp")
        gecici.write_text(json.dumps(referans, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(gecici, REFERANS_FILE)
        referans_yukle.cache_clear()
        birlesik_referans_yukle.cache_clear()
        return {**yeni_kaynak, "toplam_kaynak": len(kaynaklar)}
    finally:
        wb.close()


@lru_cache(maxsize=1)
def referans_yukle() -> dict | None:
    if not REFERANS_FILE.exists():
        return None
    try:
        veri = json.loads(REFERANS_FILE.read_text(encoding="utf-8"))
        return _kaynaklari_birlestir(_kaynaklari_cikar(veri))
    except (OSError, json.JSONDecodeError):
        return None


def _kullanici_yukle() -> dict:
    if not KULLANICI_FILE.exists():
        return {"surum": 1, "alanlar": {alan: [] for alan in ALAN_SUTUNLARI}}
    try:
        veri = json.loads(KULLANICI_FILE.read_text(encoding="utf-8"))
        return veri if isinstance(veri, dict) else {"surum": 1, "alanlar": {}}
    except (OSError, json.JSONDecodeError):
        return {"surum": 1, "alanlar": {}}


def duzeltmelerden_ogren(kayit: dict, degisen_alanlar) -> int:
    """Kullanıcının gerçekten değiştirdiği metin değerlerini yerel sözlüğe ekler."""
    alanlar = set(degisen_alanlar) & set(ALAN_SUTUNLARI)
    if not alanlar:
        return 0
    veri = _kullanici_yukle()
    veri.setdefault("alanlar", {})
    eklenen = 0
    for alan in alanlar:
        deger = str(kayit.get(alan) or "").strip()
        if not _anlamli(deger, alan):
            continue
        liste = veri["alanlar"].setdefault(alan, [])
        anahtar = arama_anahtari(deger)
        mevcut = next(
            (oge for oge in liste if arama_anahtari(oge.get("deger")) == anahtar),
            None,
        )
        if mevcut:
            mevcut["deger"] = deger
            mevcut["adet"] = int(mevcut.get("adet", 0)) + 1
        else:
            liste.append({"deger": deger, "adet": 1})
        eklenen += 1

    if eklenen:
        veri["guncelleme_zamani"] = datetime.now().isoformat(timespec="seconds")
        gecici = KULLANICI_FILE.with_name(f".{KULLANICI_FILE.name}.{uuid.uuid4().hex}.tmp")
        gecici.write_text(json.dumps(veri, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(gecici, KULLANICI_FILE)
        birlesik_referans_yukle.cache_clear()
    return eklenen


@lru_cache(maxsize=1)
def birlesik_referans_yukle() -> dict | None:
    """Excel geçmişini, daha öncelikli kullanıcı düzeltmeleriyle birleştirir."""
    excel = referans_yukle()
    kullanici = _kullanici_yukle()
    kullanici_sayisi = sum(
        int(oge.get("adet", 0))
        for liste in kullanici.get("alanlar", {}).values()
        for oge in liste
    )
    if not excel and not kullanici_sayisi:
        return None

    sonuc = dict(excel or {})
    sonuc["alanlar"] = {}
    for alan in ALAN_SUTUNLARI:
        birlesik: dict[str, dict] = {}
        for oge in (excel or {}).get("alanlar", {}).get(alan, []):
            anahtar = arama_anahtari(oge.get("deger"))
            birlesik[anahtar] = {
                "deger": oge.get("deger"), "adet": int(oge.get("adet", 0)),
                "kullanici_adet": 0,
            }
        for oge in kullanici.get("alanlar", {}).get(alan, []):
            anahtar = arama_anahtari(oge.get("deger"))
            hedef = birlesik.setdefault(anahtar, {"deger": oge.get("deger"), "adet": 0})
            hedef["deger"] = oge.get("deger")  # Kullanıcının yazımı kanonik olsun.
            hedef["kullanici_adet"] = int(oge.get("adet", 0))
            hedef["adet"] = int(hedef.get("adet", 0)) + hedef["kullanici_adet"]
        sonuc["alanlar"][alan] = list(birlesik.values())
    sonuc["kullanici_duzeltme_sayisi"] = kullanici_sayisi
    if kullanici_sayisi:
        sonuc["kaynak_adi"] = (
            f"{sonuc.get('kaynak_adi', 'Yerel sözlük')} + kullanıcı düzeltmeleri"
        )
    return sonuc


def benzerlik(a, b) -> int:
    sol, sag = arama_anahtari(a), arama_anahtari(b)
    if not sol or not sag:
        return 0
    bitisik = SequenceMatcher(None, sol.replace(" ", ""), sag.replace(" ", "")).ratio()
    kelimeler = SequenceMatcher(None, " ".join(sorted(sol.split())), " ".join(sorted(sag.split()))).ratio()
    return round(max(bitisik, kelimeler) * 100)


def kayit_onerileri(kayit: dict) -> dict | None:
    """Kaydın üç metin alanı için en güçlü, yeterince güvenli adayları döndürür."""
    referans = birlesik_referans_yukle()
    if not referans:
        return None

    oneriler = []
    for alan, esik in ESIKLER.items():
        mevcut = str(kayit.get(alan) or "").strip()
        if not _anlamli(mevcut, alan):
            continue
        adaylar = referans.get("alanlar", {}).get(alan, [])
        mevcut_anahtar = arama_anahtari(mevcut)
        # Zaten geçmişte aynen bulunan geçerli bir değer için gereksiz öneri verme.
        if any(arama_anahtari(aday.get("deger")) == mevcut_anahtar for aday in adaylar):
            continue
        puanli = sorted(
            ((benzerlik(mevcut, aday.get("deger")), aday) for aday in adaylar),
            key=lambda oge: (
                -oge[0], -int(oge[1].get("kullanici_adet", 0)),
                -int(oge[1].get("adet", 0)),
            ),
        )
        if not puanli or puanli[0][0] < esik:
            continue
        puan, aday = puanli[0]
        ikinci_puan = puanli[1][0] if len(puanli) > 1 else 0
        # Orta güvenli iki aday birbirine çok yakınsa yanlış yönlendirme yapma.
        if puan < 85 and puan - ikinci_puan < 5:
            continue
        oneriler.append({
            "alan": alan,
            "mevcut": mevcut,
            "onerilen": aday["deger"],
            "benzerlik": puan,
            "gecmis_adet": aday.get("adet", 0),
        })

    if not oneriler:
        return None
    return {
        "kaynak": referans.get("kaynak_adi"),
        "sayfa": referans.get("sayfa"),
        "oneriler": oneriler,
    }


def durum() -> dict:
    referans = birlesik_referans_yukle()
    if not referans:
        return {"hazir": False}
    return {
        "hazir": True,
        "kaynak": referans.get("kaynak_adi"),
        "sayfa": referans.get("sayfa"),
        "satir_sayisi": referans.get("satir_sayisi", 0),
        "kaynak_sayisi": referans.get("kaynak_sayisi", 1),
        "kaynaklar": [
            {"dosya": k.get("kaynak_adi"), "sayfa": k.get("sayfa"),
             "satir_sayisi": k.get("satir_sayisi", 0)}
            for k in referans.get("kaynaklar", [])
        ],
        "ogrenme_zamani": referans.get("ogrenme_zamani"),
        "kullanici_duzeltme_sayisi": referans.get("kullanici_duzeltme_sayisi", 0),
        "deger_sayilari": {
            alan: len(degerler)
            for alan, degerler in referans.get("alanlar", {}).items()
        },
    }
