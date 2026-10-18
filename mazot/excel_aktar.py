r"""
Onaylanmış fişleri, kullanıcının arayüzden seçtiği Excel defterine aktarır.

Kullanım (arayüzden seçim yapmak yerine komut satırından):
    .\.venv\Scripts\python.exe excel_aktar.py "<xlsx yolu>" "<sayfa adı>"

Davranış:
- Seçilen sayfada veri içeren son satır bulunur, ARAYA BİR BOŞ SATIR
  bırakılarak yeni kayıtlar en sona eklenir.
- Sadece _onaylandi=true olan ve daha önce aktarılmamış kayıtlar yazılır;
  aktarılanlar sonuclar.json'da _excel_aktarildi=true olarak işaretlenir,
  böylece aynı fiş ikinci kez eklenmez.
- Hücre biçimi (font, hizalama, sayı formatı) bir üstteki dolu satırdan
  kopyalanır, defterin görünümü bozulmaz.

Sütun eşleştirmesi (NUSRET MAZOT defterindeki gerçek kullanıma göre):
  A TARİH · B FATURA NO · C FİRMA · D ARAÇ PLAKASI · E ADI SOYADI
  F ARAÇ KM · H ALINAN LİTRE
G, I, J, K, L, M (bakiye/fatura tutarı) boş bırakılır — o sütunlar deftere
ayrı bir süreçte, elle işleniyor.
"""

import json
import re
import shutil
import sys
from copy import copy
from datetime import datetime
from pathlib import Path

from openpyxl import load_workbook

from . import yollar

ROOT = yollar.VERI_DIZINI  # değişen veriler (data/)
DATA_FILE = ROOT / "sonuclar.json"
AYAR_FILE = ROOT / "output" / ".excel_ayar.json"

# Excel dosyası aranacak klasörler (arayüzdeki listeyi bunlar besler).
ARAMA_KLASORLERI = [ROOT / "output", ROOT, yollar.UYGULAMA_KOKU, Path.home() / "Documents"]
EXCEL_UZANTILARI = {".xlsx", ".xlsm"}

COL = {"tarih": 1, "fis_no": 2, "firma": 3, "plaka": 4, "ad_soyad": 5, "km": 6, "litre": 8}
SUTUN_ETIKETLERI = {
    "tarih": "A (TARİH)", "fis_no": "B (FATURA NO)", "firma": "C (FİRMA)",
    "plaka": "D (ARAÇ PLAKASI)", "ad_soyad": "E (ADI SOYADI)",
    "km": "F (ARAÇ KM)", "litre": "H (ALINAN LİTRE)",
}
# Son dolu satırı ararken bakılan sütunlar (yazdığımız sütunlar).
TARANAN_SUTUNLAR = (1, 2, 3, 4, 5, 6, 8)


def aktarim_oncesi_yedekle(xlsx_yolu: Path) -> Path:
    """Her hedef Excel için günde bir kez, yazma öncesi güvenli kopya alır."""
    yedek_klasoru = ROOT / "yedekler" / "excel_otomatik"
    yedek_klasoru.mkdir(parents=True, exist_ok=True)
    tarih = datetime.now().strftime("%Y-%m-%d")
    hedef = yedek_klasoru / f"{xlsx_yolu.stem}_{tarih}_aktarim_oncesi{xlsx_yolu.suffix}"
    if not hedef.exists():
        shutil.copy2(xlsx_yolu, hedef)
    return hedef


def yukle_json(yol: Path, varsayilan):
    if not yol.exists():
        return varsayilan
    try:
        return json.loads(yol.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return varsayilan


def kaydet_json(yol: Path, veri) -> None:
    yol.parent.mkdir(parents=True, exist_ok=True)
    gecici = yol.with_suffix(yol.suffix + ".tmp")
    gecici.write_text(json.dumps(veri, ensure_ascii=False, indent=2), encoding="utf-8")
    gecici.replace(yol)


def yol_guvenli_mi(yol: Path) -> bool:
    """Tarayıcıdan gelen dosya yolunun izin verilen klasörlerin içinde
    kaldığını doğrular — rastgele bir sistem dosyasına yazılmasını engeller."""
    try:
        cozulmus = yol.resolve()
    except OSError:
        return False
    if cozulmus.suffix.lower() not in EXCEL_UZANTILARI:
        return False
    for klasor in ARAMA_KLASORLERI:
        try:
            cozulmus.relative_to(klasor.resolve())
            return True
        except ValueError:
            continue
    return False


def excel_dosyalarini_bul() -> list[dict]:
    """Arayüzdeki dosya listesini üretir (alt klasörlere inmez)."""
    bulunanlar = {}
    for klasor in ARAMA_KLASORLERI:
        if not klasor.exists():
            continue
        for yol in sorted(klasor.glob("*")):
            if yol.suffix.lower() not in EXCEL_UZANTILARI:
                continue
            if yol.name.startswith("~$"):  # Excel'in açıkken oluşturduğu kilit dosyası
                continue
            cozulmus = str(yol.resolve())
            if cozulmus in bulunanlar:
                continue
            bulunanlar[cozulmus] = {
                "yol": cozulmus,
                "ad": yol.name,
                "klasor": str(klasor),
                "boyut_kb": round(yol.stat().st_size / 1024),
            }
    return list(bulunanlar.values())


def sayfa_adlari(xlsx_yolu: Path) -> list[str]:
    wb = load_workbook(xlsx_yolu, read_only=True)
    try:
        return list(wb.sheetnames)
    finally:
        wb.close()


def _hucre_dolu_mu(deger) -> bool:
    if deger is None:
        return False
    metin = str(deger).strip()
    if not metin:
        return False
    # Boş satırlara önceden çekilmiş bakiye formüllerini "veri" sayma.
    return not metin.startswith("=")


def son_veri_satirini_bul(ws) -> int:
    """Yazdığımız sütunlarda (A-F, H) gerçek işlem içeren son satır.

    En az İKİ dolu hücre şartı var: gerçek bir fiş satırında tarih, fatura
    no, firma, plaka... hep birlikte dolar. Tek bir hücresi dolu satırlar
    (deftere unutulmuş 'SELİM İNŞAAT' etiketi ya da 'ARA TOPLAM' gibi)
    işlem sayılmaz, yoksa yeni kayıtlar defterin çok altına düşerdi.
    """
    son = 0
    for r in range(1, ws.max_row + 1):
        dolu = sum(
            1 for c in TARANAN_SUTUNLAR
            if _hucre_dolu_mu(ws.cell(row=r, column=c).value)
        )
        if dolu >= 2:
            son = r
    return son


def sayfa_onizleme(xlsx_yolu: Path, sayfa: str) -> dict:
    """Arayüzde gösterilecek bilgi: başlıklar, son dolu satır, önerilen
    başlangıç satırı ve son birkaç satırın özeti."""
    wb = load_workbook(xlsx_yolu)
    try:
        if sayfa not in wb.sheetnames:
            raise ValueError(f"'{sayfa}' sayfası bulunamadı")
        ws = wb[sayfa]
        basliklar = [
            (str(ws.cell(row=1, column=c).value).strip()
             if ws.cell(row=1, column=c).value is not None else "")
            for c in range(1, 14)
        ]
        son = son_veri_satirini_bul(ws)
        ornekler = []
        for r in range(max(1, son - 2), son + 1):
            ornekler.append({
                "satir": r,
                "degerler": [
                    ("" if ws.cell(row=r, column=c).value is None
                     else str(ws.cell(row=r, column=c).value))
                    for c in TARANAN_SUTUNLAR
                ],
            })
        return {
            "basliklar": basliklar,
            "son_veri_satiri": son,
            "onerilen_baslangic": son + 2,  # bir satır boşluk bırak
            "ornek_satirlar": ornekler,
            "sutun_eslestirmesi": SUTUN_ETIKETLERI,
        }
    finally:
        wb.close()


def bekleyen_kayitlar(
    kayitlar: list[dict] | None = None, tumu: bool = False
) -> list[dict]:
    """Aktarılacak onaylı fişler.

    ``tumu=True`` verilirse daha önce Excel'e aktarılmış fişler de listeye
    girer; yanlış dosyaya aktarıldığında partinin tamamı başka bir deftere
    yeniden aktarılabilsin diye.
    """
    kayitlar = yukle_json(DATA_FILE, []) if kayitlar is None else kayitlar
    return [
        k for k in kayitlar
        if k.get("_onaylandi") and (tumu or not k.get("_excel_aktarildi"))
        and not k.get("HATA") and not k.get("_silindi")
    ]


def tarihi_cevir(deger):
    if not deger or deger == "-":
        return None
    if isinstance(deger, datetime):
        return deger
    m = re.match(r"^(\d{1,2})[./](\d{1,2})[./](\d{4})$", str(deger).strip())
    if not m:
        return deger
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
            return deger


def hucre_stilini_kopyala(kaynak_hucre, hedef_hucre) -> None:
    hedef_hucre.font = copy(kaynak_hucre.font)
    hedef_hucre.fill = copy(kaynak_hucre.fill)
    hedef_hucre.border = copy(kaynak_hucre.border)
    hedef_hucre.alignment = copy(kaynak_hucre.alignment)
    hedef_hucre.number_format = kaynak_hucre.number_format


def aktar(xlsx_yolu: Path, sayfa: str, baslangic_satir: int | None = None,
          tumu: bool = False) -> dict:
    """Onaylı fişleri seçilen dosya/sayfaya ekler. baslangic_satir verilmezse
    son dolu satırın iki altından (bir boşluk bırakarak) başlar."""
    xlsx_yolu = Path(xlsx_yolu)
    if not xlsx_yolu.exists():
        return {"hata": f"Dosya bulunamadı: {xlsx_yolu}"}
    if not yol_guvenli_mi(xlsx_yolu):
        return {"hata": "Bu konuma yazmaya izin yok (yalnızca proje ve Belgeler klasörü)."}

    kayitlar = yukle_json(DATA_FILE, [])
    aktarilacaklar = bekleyen_kayitlar(kayitlar, tumu=tumu)
    if not aktarilacaklar:
        return {"aktarilan": 0, "detay": []}

    try:
        yedek_yolu = aktarim_oncesi_yedekle(xlsx_yolu)
    except OSError as exc:
        return {"hata": f"Excel yedeği alınamadığı için aktarım durduruldu: {exc}"}

    try:
        wb = load_workbook(xlsx_yolu)
    except Exception as exc:
        return {"hata": f"Dosya açılamadı ({exc}). Excel'de açıksa kapatıp tekrar dene."}

    if sayfa not in wb.sheetnames:
        wb.close()
        return {"hata": f"'{sayfa}' sayfası bulunamadı"}

    ws = wb[sayfa]
    son_veri = son_veri_satirini_bul(ws)
    baslangic = int(baslangic_satir) if baslangic_satir else son_veri + 2
    stil_satiri = son_veri if son_veri else 1

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
        kayit["_excel_dosya"] = str(xlsx_yolu)
        kayit["_excel_sayfa"] = sayfa
        kayit["_excel_aktarim_zamani"] = datetime.now().isoformat(timespec="seconds")
        detay.append({
            "satir": satir, "dosya": kayit.get("_dosya"), "fis_no": kayit.get("fis_no"),
            "firma": kayit.get("firma"), "plaka": kayit.get("plaka"),
        })
        satir += 1

    try:
        wb.save(xlsx_yolu)
    except PermissionError:
        wb.close()
        return {"hata": "Dosyaya yazılamadı — Excel'de açık olabilir, kapatıp tekrar dene."}
    finally:
        wb.close()

    kaydet_json(DATA_FILE, kayitlar)
    kaydet_json(AYAR_FILE, {"dosya": str(xlsx_yolu), "sayfa": sayfa})

    return {
        "aktarilan": len(aktarilacaklar), "detay": detay,
        "ilk_satir": baslangic, "son_satir": satir - 1,
        "dosya": str(xlsx_yolu), "sayfa": sayfa,
        "yedek": str(yedek_yolu),
    }


def son_ayar() -> dict:
    return yukle_json(AYAR_FILE, {})


def main():
    if len(sys.argv) < 3:
        ayar = son_ayar()
        if ayar.get("dosya") and ayar.get("sayfa"):
            print(f"Kullanım: python -m mazot.excel_aktar \"<xlsx yolu>\" \"<sayfa adı>\"")
            print(f"Son kullanılan: \"{ayar['dosya']}\" / \"{ayar['sayfa']}\"")
        else:
            print('Kullanım: python -m mazot.excel_aktar "<xlsx yolu>" "<sayfa adı>"')
            print("\nBulunan Excel dosyaları:")
            for d in excel_dosyalarini_bul():
                print(f"  {d['yol']}")
        sys.exit(1)

    sonuc = aktar(Path(sys.argv[1]), sys.argv[2],
                  int(sys.argv[3]) if len(sys.argv) > 3 else None)
    if "hata" in sonuc:
        print(f"HATA: {sonuc['hata']}")
        sys.exit(1)
    if sonuc["aktarilan"] == 0:
        print("Aktarılacak yeni onaylı fiş yok.")
        return
    for d in sonuc["detay"]:
        print(f"  satır {d['satir']}: {d['dosya']} -> fiş no {d['fis_no']}, {d['firma']}, {d['plaka']}")
    print(f"\n{sonuc['aktarilan']} fiş '{Path(sonuc['dosya']).name}' / '{sonuc['sayfa']}' "
          f"sayfasına eklendi (satır {sonuc['ilk_satir']}-{sonuc['son_satir']}).")


if __name__ == "__main__":
    main()
