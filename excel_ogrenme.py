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
from functools import lru_cache
from pathlib import Path

from openpyxl import load_workbook
from rapidfuzz import fuzz

from normalizasyon import bos_degeri_duzelt, firmayi_duzelt, plakayi_duzelt


ROOT = Path(__file__).resolve().parent
REFERANS_FILE = ROOT / "excel_referans.json"
KULLANICI_FILE = ROOT / "kullanici_ogrenmeleri.json"
ALAN_SUTUNLARI = {"firma": 3, "plaka": 4, "ad_soyad": 5}
ESIKLER = {"firma": 68, "plaka": 72, "ad_soyad": 70}
GECERSIZ_METINLER = {"", "-", "—", "NONE", "NULL", "SAYIN", "SAYIN:", "SAYIN :"}


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
    except (OSError, json.JSONDecodeError, ValueError):
        return None


def _kullanici_yukle() -> dict:
    if not KULLANICI_FILE.exists():
        return {
            "surum": 2,
            "alanlar": {alan: [] for alan in ALAN_SUTUNLARI},
            "eslesmeler": {alan: [] for alan in ALAN_SUTUNLARI},
        }
    try:
        veri = json.loads(KULLANICI_FILE.read_text(encoding="utf-8"))
        if not isinstance(veri, dict):
            raise ValueError("gecersiz kullanici ogrenme dosyasi")
        veri.setdefault("alanlar", {})
        veri.setdefault("eslesmeler", {})
        return veri
    except (OSError, json.JSONDecodeError):
        return {
            "surum": 2,
            "alanlar": {alan: [] for alan in ALAN_SUTUNLARI},
            "eslesmeler": {alan: [] for alan in ALAN_SUTUNLARI},
        }


def _bilinen_gecerli_deger(veri: dict, alan: str, deger: str, esik: int = 2) -> bool:
    """Kullanıcının başka fişlerde defalarca onayladığı gerçek bir değer mi?

    Böyle bir değer OCR hatası sayılamaz; onu bir başka değere çeviren kural
    öğrenilmemeli ve uygulanmamalıdır (ör. gerçekten Selim İnşaat yazan fiş).
    """
    anahtar = arama_anahtari(deger)
    for oge in veri.get("alanlar", {}).get(alan, []):
        if arama_anahtari(oge.get("deger")) == anahtar:
            return int(oge.get("adet", 0)) >= esik
    return False


def duzeltmelerden_ogren(
    kayit: dict,
    degisen_alanlar,
    onceki_degerler: dict | None = None,
    sadece_eslesme: bool = False,
) -> int:
    """Kullanıcının düzeltmesini hem doğru değer hem yanlış→doğru kuralı olarak öğrenir."""
    alanlar = set(degisen_alanlar) & set(ALAN_SUTUNLARI)
    if not alanlar:
        return 0
    veri = _kullanici_yukle()
    veri["surum"] = 2
    veri.setdefault("alanlar", {})
    veri.setdefault("eslesmeler", {})
    eklenen = 0
    degisti = False
    for alan in alanlar:
        alan_degisti = False
        deger = str(kayit.get(alan) or "").strip()
        if not _anlamli(deger, alan):
            continue
        if not sadece_eslesme:
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
            degisti = True
            alan_degisti = True

        onceki = str((onceki_degerler or {}).get(alan) or "").strip()
        if (
            _anlamli(onceki, alan)
            and arama_anahtari(onceki) != arama_anahtari(deger)
            and not _bilinen_gecerli_deger(veri, alan, onceki)
        ):
            eslesmeler = veri["eslesmeler"].setdefault(alan, [])
            eslesme = next(
                (
                    oge for oge in eslesmeler
                    if arama_anahtari(oge.get("yanlis")) == arama_anahtari(onceki)
                    and arama_anahtari(oge.get("dogru")) == arama_anahtari(deger)
                ),
                None,
            )
            if eslesme:
                eslesme["yanlis"] = onceki
                eslesme["dogru"] = deger
                eslesme["adet"] = int(eslesme.get("adet", 0)) + 1
            else:
                eslesmeler.append({"yanlis": onceki, "dogru": deger, "adet": 1})
            degisti = True
            alan_degisti = True

        if alan_degisti:
            eklenen += 1

    if degisti:
        veri["guncelleme_zamani"] = datetime.now().isoformat(timespec="seconds")
        gecici = KULLANICI_FILE.with_name(f".{KULLANICI_FILE.name}.{uuid.uuid4().hex}.tmp")
        gecici.write_text(json.dumps(veri, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(gecici, KULLANICI_FILE)
        birlesik_referans_yukle.cache_clear()
    return eklenen


def gecmis_duzeltmelerden_eslesme_ogren(kayitlar: list[dict]) -> int:
    """Onaylanmış eski kayıtların ham OCR sonucundan düzeltme eşleşmeleri çıkarır.

    Her kayıt dosya karması/yükleme kimliği ile yalnız bir kez işlenir. Böylece
    uygulama yeniden başlatıldığında öğrenme adetleri yapay biçimde artmaz.
    """
    veri = _kullanici_yukle()
    veri["surum"] = 2
    veri.setdefault("alanlar", {})
    veri.setdefault("eslesmeler", {})
    islenenler = set(veri.setdefault("eslesme_kaynaklari", []))
    yeni_kaynaklar = []
    eklenen = 0

    # Daha önce kaydedilmiş riskli kuralları (gerçek bir değeri başka bir
    # değere çeviren kurallar) temizle.
    temizlendi = False
    for alan, eslesmeler in veri["eslesmeler"].items():
        guvenli = [
            oge for oge in eslesmeler
            if not _bilinen_gecerli_deger(veri, alan, str(oge.get("yanlis") or ""))
        ]
        if len(guvenli) != len(eslesmeler):
            veri["eslesmeler"][alan] = guvenli
            temizlendi = True

    for kayit in kayitlar:
        if not (kayit.get("_onaylandi") and kayit.get("_duzeltildi")):
            continue
        kaynak = str(
            kayit.get("_dosya_hash") or kayit.get("_yukleme_id")
            or kayit.get("_dosya") or ""
        ).strip()
        if not kaynak or kaynak in islenenler:
            continue
        ham = kayit.get("_ham")
        if not isinstance(ham, dict):
            continue

        ham_firma = ham.get("sayin_alani")
        if str(ham_firma or "").strip().upper() in GECERSIZ_METINLER:
            ham_firma = None
        onceki = {
            "firma": firmayi_duzelt(ham_firma),
            "plaka": plakayi_duzelt(
                ham.get("arac_plakasi_alani"), bool(kayit.get("_ekipman_durumu"))
            ),
            "ad_soyad": bos_degeri_duzelt(ham.get("adi_soyadi_alani")),
        }
        degisen = {
            alan for alan in ALAN_SUTUNLARI
            if _anlamli(kayit.get(alan), alan)
            and _anlamli(onceki.get(alan), alan)
            and arama_anahtari(kayit.get(alan)) != arama_anahtari(onceki.get(alan))
        }
        for alan in degisen:
            yanlis = str(onceki[alan]).strip()
            dogru = str(kayit[alan]).strip()
            if _bilinen_gecerli_deger(veri, alan, yanlis):
                continue
            eslesmeler = veri["eslesmeler"].setdefault(alan, [])
            eslesme = next(
                (
                    oge for oge in eslesmeler
                    if arama_anahtari(oge.get("yanlis")) == arama_anahtari(yanlis)
                    and arama_anahtari(oge.get("dogru")) == arama_anahtari(dogru)
                ),
                None,
            )
            if eslesme:
                eslesme["adet"] = int(eslesme.get("adet", 0)) + 1
            else:
                eslesmeler.append({"yanlis": yanlis, "dogru": dogru, "adet": 1})
            eklenen += 1

        islenenler.add(kaynak)
        yeni_kaynaklar.append(kaynak)

    if yeni_kaynaklar or temizlendi:
        veri["eslesme_kaynaklari"] = sorted(islenenler)
        veri["guncelleme_zamani"] = datetime.now().isoformat(timespec="seconds")
        gecici = KULLANICI_FILE.with_name(f".{KULLANICI_FILE.name}.{uuid.uuid4().hex}.tmp")
        gecici.write_text(json.dumps(veri, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(gecici, KULLANICI_FILE)
        birlesik_referans_yukle.cache_clear()
    return eklenen


def _kesin_kullanici_eslesmesi(kullanici: dict, alan: str, mevcut: str) -> dict | None:
    """Aynı OCR yanlışı için en güçlü ve çelişkisiz kullanıcı düzeltmesini bulur."""
    anahtar = arama_anahtari(mevcut)
    adaylar = [
        oge for oge in kullanici.get("eslesmeler", {}).get(alan, [])
        if arama_anahtari(oge.get("yanlis")) == anahtar
    ]
    adaylar.sort(key=lambda oge: -int(oge.get("adet", 0)))
    if not adaylar:
        return None
    if _bilinen_gecerli_deger(kullanici, alan, mevcut):
        return None
    if len(adaylar) > 1:
        birinci = int(adaylar[0].get("adet", 0))
        ikinci = int(adaylar[1].get("adet", 0))
        # Aynı OCR metni için birden çok doğru öneriliyorsa ancak açık ara
        # üstün ve tekrarlanmış kural uygulanır.
        if birinci < 2 or birinci < ikinci * 2:
            return None
    return adaylar[0]


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
    return round(fuzz.WRatio(sol, sag))


def _fazladan_kelime_var_mi(mevcut: str, aday: str) -> bool:
    """Adayda, mevcut değerde hiç karşılığı olmayan fazladan sözcük var mı?

    'RT 650' ile 'RT 650 SARI VİNÇ' benzerlik puanında yüksek çıkar; oysa
    aradaki fark bir OCR hatası değil, fazladan yazılmış sözcüklerdir. Böyle
    adaylar otomatik uygulanmaz, yalnız öneri olarak gösterilir. Sözcük sayısı
    aynıysa fark harf düzeyindedir (ATV/DTV) ve bu kural devreye girmez;
    '34' gibi kısa il kodu eklemeleri de kuralın dışındadır.
    """
    sol = arama_anahtari(mevcut).split()
    sag = arama_anahtari(aday).split()
    if len(sol) == len(sag):
        return False
    uzun, kisa = (sol, sag) if len(sol) > len(sag) else (sag, sol)
    for kelime in uzun:
        if sum(c.isalpha() for c in kelime) < 3:
            continue
        if not any(benzerlik(kelime, diger) >= 80 for diger in kisa):
            return True
    return False


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


def guvenli_otomatik_duzelt(kayit: dict) -> int:
    """Geçmişe dayanarak yalnız çok yüksek güvenli metin düzeltmelerini uygular.

    Aynı arama anahtarına inen Türkçe karakter/case varyantları risksiz biçimde
    kanonikleştirilir. Yakın eşleşmelerde ise alan bazlı yüksek eşik ve geçmişte
    en az iki kullanım şartı aranır. Tüm değişiklikler denetim için kayda yazılır.
    """
    referans = birlesik_referans_yukle()
    if not referans:
        return 0

    degisiklikler = []
    kullanici = _kullanici_yukle()
    yakin_esikler = {"firma": 95, "plaka": 88, "ad_soyad": 95}
    for alan in ALAN_SUTUNLARI:
        mevcut = str(kayit.get(alan) or "").strip()
        if not _anlamli(mevcut, alan):
            continue

        kesin = _kesin_kullanici_eslesmesi(kullanici, alan, mevcut)
        if kesin:
            yeni = str(kesin.get("dogru") or "").strip()
            if yeni and yeni != mevcut:
                kayit[alan] = yeni
                degisiklikler.append({
                    "alan": alan, "eski": mevcut, "yeni": yeni,
                    "benzerlik": 100,
                    "neden": f"kullanıcı düzeltmesi ({int(kesin.get('adet', 0))} kez)",
                })
            continue

        adaylar = referans.get("alanlar", {}).get(alan, [])
        mevcut_anahtar = arama_anahtari(mevcut)

        ayni = [a for a in adaylar if arama_anahtari(a.get("deger")) == mevcut_anahtar]
        if ayni:
            aday = max(
                ayni,
                key=lambda a: (int(a.get("kullanici_adet", 0)), int(a.get("adet", 0))),
            )
            yeni = str(aday.get("deger") or "").strip()
            if yeni and yeni != mevcut:
                kayit[alan] = yeni
                degisiklikler.append({
                    "alan": alan, "eski": mevcut, "yeni": yeni,
                    "benzerlik": 100, "neden": "geçmişteki kanonik yazım",
                })
            continue

        puanli = sorted(
            ((benzerlik(mevcut, a.get("deger")), a) for a in adaylar),
            key=lambda x: (-x[0], -int(x[1].get("kullanici_adet", 0)), -int(x[1].get("adet", 0))),
        )
        if not puanli:
            continue
        puan, aday = puanli[0]
        ikinci = puanli[1][0] if len(puanli) > 1 else 0
        gecmis = int(aday.get("adet", 0))
        if puan < yakin_esikler[alan] or gecmis < 2 or puan - ikinci < 5:
            continue
        if _fazladan_kelime_var_mi(mevcut, aday.get("deger")):
            continue
        yeni = str(aday.get("deger") or "").strip()
        if yeni and yeni != mevcut:
            kayit[alan] = yeni
            degisiklikler.append({
                "alan": alan, "eski": mevcut, "yeni": yeni,
                "benzerlik": puan, "neden": f"geçmişte {gecmis} kullanım",
            })

    if degisiklikler:
        kayit["_otomatik_duzeltmeler"] = degisiklikler
        kayit["_otomatik_duzeltildi"] = True
    return len(degisiklikler)


def durum() -> dict:
    referans = birlesik_referans_yukle()
    if not referans:
        return {"hazir": False}
    kullanici = _kullanici_yukle()
    eslesme_sayisi = sum(
        len(liste) for liste in kullanici.get("eslesmeler", {}).values()
    )
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
        "kullanici_eslesme_sayisi": eslesme_sayisi,
        "deger_sayilari": {
            alan: len(degerler)
            for alan, degerler in referans.get("alanlar", {}).items()
        },
    }
