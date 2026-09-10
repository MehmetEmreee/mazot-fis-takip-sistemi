r"""
Onaylanmış fişleri (arayuz.py'da onaylanan kayıtlar) NUSRET MAZOT 2026
defterinin "YİLMAZ MAZOT 2026" sayfasına aktarır.

Kullanım:
    .\.venv\Scripts\python.exe excel_aktar.py

Ne yapar:
- sonuclar.json'daki, _onaylandi=true VE daha önce aktarılmamış kayıtları bulur
- output/NUSRET MAZOT 2026_YENİ 2.xlsx dosyasındaki "YİLMAZ MAZOT 2026"
  sayfasına, mevcut verinin biçimini (font, hücre formatı) birebir
  koruyarak ekler
- İlk çalıştırmada: eski (elle girilmiş) veri ile yeni (OCR) veri arasına
  bir boşluk satırı bırakır. Sonraki çalıştırmalarda kaldığı yerden,
  ek boşluk bırakmadan devam eder (bunu output/.aktarim_durumu.json'da takip eder)
- Aktarılan kayıtları sonuclar.json'da _excel_aktarildi=true olarak işaretler
  (aynı fiş ikinci kez eklenmesin diye)

Sütun eşleştirmesi (defterdeki gerçek kullanıma göre doğrulandı):
  A TARİH        <- tarih
  B FATURA NO     <- fis_no
  C FİRMA         <- firma
  D ARAÇ PLAKASI  <- plaka (gerçek plaka VEYA ekipman adı)
  E ADI SOYADI    <- ad_soyad
  F ARAÇ KM       <- km
  H ALINAN LİTRE  <- litre  (G sütunu deftede kullanılmıyor, dokunulmuyor)
  G, I, J, K, L, M: boş bırakılıyor — bu sütunlar (bakiye/fatura tutarı)
  deftede ayrı bir süreçte, elle dolduruluyor; tek tek fiş satırlarında
  zaten boş (bkz. satır 3918-3933).
"""

import json
import re
import sys
from datetime import datetime
from pathlib import Path

from openpyxl import load_workbook
from openpyxl.utils import get_column_letter

ROOT = Path(__file__).resolve().parent
DATA_FILE = ROOT / "sonuclar.json"
OUTPUT_XLSX = ROOT / "output" / "NUSRET MAZOT 2026_YENİ 2.xlsx"
STATE_FILE = ROOT / "output" / ".aktarim_durumu.json"
SHEET_NAME = "YİLMAZ MAZOT 2026"

COL = {"tarih": 1, "fis_no": 2, "firma": 3, "plaka": 4, "ad_soyad": 5, "km": 6, "litre": 8}
SON_ALAN_SUTUNU = 8  # H: satırın "gerçek veri var mı" kontrolü için taranan son sütun


def yukle_json(yol: Path, varsayilan):
    if not yol.exists():
        return varsayilan
    return json.loads(yol.read_text(encoding="utf-8"))


def kaydet_json(yol: Path, veri) -> None:
    gecici = yol.with_suffix(".json.tmp")
    gecici.write_text(json.dumps(veri, ensure_ascii=False, indent=2), encoding="utf-8")
    gecici.replace(yol)


def son_veri_satirini_bul(ws) -> int:
    """FATURA NO / ARAÇ PLAKASI / ADI SOYADI (B, D, E) sütunlarından en az
    birine bakarak gerçek işlem satırlarının bittiği son satırı bulur.
    Sadece FİRMA (C) dolu olan tek başına satırlar (deftede satır 4767'de
    olduğu gibi unutulmuş etiketler) veya G,I,J,K,L,M'deki bakiye
    formülleri bu aramayı etkilemez."""
    son = 0
    for r in range(1, ws.max_row + 1):
        for c in (2, 4, 5):
            v = ws.cell(row=r, column=c).value
            if v is not None and str(v).strip() != "":
                son = r
                break
    return son


def tarihi_cevir(deger):
    if not deger or deger == "-":
        return None
    if isinstance(deger, datetime):
        return deger
    m = re.match(r"^(\d{1,2})[./](\d{1,2})[./](\d{4})$", str(deger).strip())
    if not m:
        return deger  # ayrıştıramazsak olduğu gibi bırak, elle düzeltilir
    gun, ay, yil = (int(x) for x in m.groups())
    try:
        return datetime(yil, ay, gun)
    except ValueError:
        return deger


def sayiya_cevir(deger):
    if deger is None or deger == "-":
        return None
    if isinstance(deger, (int, float)):
        return deger
    temiz = str(deger).strip().replace(".", "").replace(",", ".")
    try:
        return int(temiz)
    except ValueError:
        try:
            return float(temiz)
        except ValueError:
            return deger  # sayı değilse olduğu gibi bırak


def hucre_stilini_kopyala(kaynak_hucre, hedef_hucre) -> None:
    hedef_hucre.font = kaynak_hucre.font.copy()
    hedef_hucre.fill = kaynak_hucre.fill.copy()
    hedef_hucre.border = kaynak_hucre.border.copy()
    hedef_hucre.alignment = kaynak_hucre.alignment.copy()
    hedef_hucre.number_format = kaynak_hucre.number_format


def aktar() -> dict:
    """Onaylı ve daha önce aktarılmamış fişleri deftere yazar. Hem CLI'den
    (main()) hem de arayuz.py'daki 'Excel'e Aktar' butonundan çağrılır —
    mantık tek bir yerde, iki tüketicisi var."""
    if not OUTPUT_XLSX.exists():
        return {"hata": f"{OUTPUT_XLSX} bulunamadı. Önce kaynak defterin bir "
                         f"kopyasını output/ klasörüne koy."}

    kayitlar = yukle_json(DATA_FILE, [])
    aktarilacaklar = [
        k for k in kayitlar
        if k.get("_onaylandi") and not k.get("_excel_aktarildi") and not k.get("HATA")
    ]

    if not aktarilacaklar:
        return {"aktarilan": 0, "detay": []}

    durum = yukle_json(STATE_FILE, {})
    wb = load_workbook(OUTPUT_XLSX)
    ws = wb[SHEET_NAME]

    if "son_satir" in durum:
        baslangic = durum["son_satir"] + 1
        stil_satiri = durum["son_satir"]
    else:
        son_veri = son_veri_satirini_bul(ws)
        baslangic = son_veri + 2  # bir satır boşluk bırak
        stil_satiri = son_veri

    detay = []
    satir = baslangic
    for kayit in aktarilacaklar:
        for alan, sutun in COL.items():
            hedef = ws.cell(row=satir, column=sutun)
            hucre_stilini_kopyala(ws.cell(row=stil_satiri, column=sutun), hedef)
            deger = kayit.get(alan)
            if alan == "tarih":
                hedef.value = tarihi_cevir(deger)
            elif alan in ("fis_no", "km", "litre"):
                hedef.value = sayiya_cevir(deger)
            else:
                hedef.value = None if (deger is None or deger == "-") else str(deger)

        kayit["_excel_aktarildi"] = True
        kayit["_excel_satir"] = satir
        kayit["_excel_aktarim_zamani"] = datetime.now().isoformat(timespec="seconds")
        detay.append({
            "satir": satir, "dosya": kayit.get("_dosya"), "fis_no": kayit.get("fis_no"),
            "firma": kayit.get("firma"), "plaka": kayit.get("plaka"),
        })
        satir += 1

    wb.save(OUTPUT_XLSX)
    kaydet_json(DATA_FILE, kayitlar)
    kaydet_json(STATE_FILE, {"son_satir": satir - 1})

    return {"aktarilan": len(aktarilacaklar), "detay": detay, "ilk_satir": baslangic, "son_satir": satir - 1}


def main():
    sonuc = aktar()
    if "hata" in sonuc:
        print(f"HATA: {sonuc['hata']}")
        sys.exit(1)
    if sonuc["aktarilan"] == 0:
        print("Aktarılacak yeni onaylı fiş yok.")
        return
    for d in sonuc["detay"]:
        print(f"  satır {d['satir']}: {d['dosya']} -> fiş no {d['fis_no']}, {d['firma']}, {d['plaka']}")
    print(f"\n{sonuc['aktarilan']} fiş '{OUTPUT_XLSX.name}' / '{SHEET_NAME}' sayfasına eklendi "
          f"(satır {sonuc['ilk_satir']}-{sonuc['son_satir']}).")


if __name__ == "__main__":
    main()
