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

import hashlib
import json
import os
import re
import shutil
import sys
import uuid
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
# Yarım kalan aktarımın tekrarında fişleri ikinci kez yazmamak için defterde
# tutulan gizli kayıt sayfası (kaydetme ile birlikte atomik yazılır).
KIMLIK_SAYFASI = "_mazot_aktarim"
KIMLIK_BASLIK = ("islem_id", "kayit", "sayfa", "satir", "fis_no", "zaman")


class AktarimHatasi(RuntimeError):
    pass


def aktarim_oncesi_yedekle(xlsx_yolu: Path) -> Path:
    """Her hedef Excel için günde bir kez, yazma öncesi güvenli kopya alır.

    Farklı klasörlerdeki aynı adlı defterler birbirinin yedeği sayılmasın
    diye ada çözülmüş tam yolun kısa özeti eklenir.
    """
    yedek_klasoru = ROOT / "yedekler" / "excel_otomatik"
    yedek_klasoru.mkdir(parents=True, exist_ok=True)
    tarih = datetime.now().strftime("%Y-%m-%d")
    yol_ozeti = hashlib.sha256(
        os.path.normcase(str(xlsx_yolu.resolve())).encode("utf-8")
    ).hexdigest()[:10]
    hedef = yedek_klasoru / (
        f"{xlsx_yolu.stem}_{yol_ozeti}_{tarih}_aktarim_oncesi{xlsx_yolu.suffix}"
    )
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
            if yol.name.startswith(("~$", ".")):  # Excel kilit dosyası / yarım kayıt
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
        return [ad for ad in wb.sheetnames if ad != KIMLIK_SAYFASI]
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
    kayitlar: list[dict] | None = None, tumu: bool = False,
    data_file: Path | None = None,
) -> list[dict]:
    """Aktarılacak onaylı fişler.

    ``tumu=True`` verilirse daha önce Excel'e aktarılmış fişler de listeye
    girer; yanlış dosyaya aktarıldığında partinin tamamı başka bir deftere
    yeniden aktarılabilsin diye. Kayıtlar verilmezse ``data_file`` (yoksa
    aktif partinin DATA_FILE'ı) okunur; modül durumu değiştirilmez.
    """
    if kayitlar is None:
        kayitlar = yukle_json(Path(data_file or DATA_FILE), [])
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


def _litre_metni(metin: str) -> str:
    """Litrede tek ayraç (nokta ya da virgül) ondalıktır — doğrulama da
    öyle kabul eder; ikisi birlikteyse sondaki ondalık, öbürü binliktir."""
    metin = re.sub(r"\s+", "", metin)
    if "." in metin and "," in metin:
        ondalik = "," if metin.rfind(",") > metin.rfind(".") else "."
        binlik = "." if ondalik == "," else ","
        return metin.replace(binlik, "").replace(ondalik, ".")
    return metin.replace(",", ".")


def sayiya_cevir(deger, alan: str | None = None):
    """Hücreye yazılacak sayı. fis_no/km'de nokta binlik ayracıdır; litrede
    ondalık olabilir (12.5 litre 125 olmamalı)."""
    if deger is None or deger == "-":
        return None
    if isinstance(deger, (int, float)):
        return deger
    if alan == "litre":
        temiz = _litre_metni(str(deger).strip())
    else:
        # "300 638" gibi boşlukla ayrılmış binlikler de sayıdır (doğrulama da
        # boşlukları yok sayar); metin hücre defterde toplamı bozar.
        temiz = re.sub(r"\s+", "", str(deger)).replace(".", "").replace(",", ".")
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


def hucre_degeri(alan: str, deger):
    """Bir fiş alanının Excel hücresine yazılacak hali."""
    if alan == "tarih":
        return tarihi_cevir(deger)
    if alan in ("fis_no", "km", "litre"):
        return sayiya_cevir(deger, alan)
    return None if (deger is None or deger == "-") else str(deger)


def kayit_kimligi(kayit: dict) -> str:
    return str(kayit.get("_yukleme_id") or kayit.get("_dosya") or "")


def _ayni_dosya_mi(a, b) -> bool:
    try:
        return os.path.normcase(str(Path(a).resolve())) == os.path.normcase(
            str(Path(b).resolve()))
    except OSError:
        return False


def _kimlik_sayfasi(wb, olustur: bool = False):
    """Defterdeki gizli aktarım kimliği sayfası. Aynı adlı ama bize ait
    olmayan bir sayfa varsa üzerine yazmamak için hata verir."""
    if KIMLIK_SAYFASI in wb.sheetnames:
        ws = wb[KIMLIK_SAYFASI]
        baslik = [ws.cell(row=1, column=i + 1).value for i in range(len(KIMLIK_BASLIK))]
        if baslik != list(KIMLIK_BASLIK):
            raise AktarimHatasi(
                f"Defterde '{KIMLIK_SAYFASI}' adlı başka bir sayfa var; aktarım "
                "kimlikleri yazılamadığı için işlem durduruldu."
            )
        return ws
    if not olustur:
        return None
    ws = wb.create_sheet(KIMLIK_SAYFASI)
    ws.sheet_state = "hidden"
    ws.append(list(KIMLIK_BASLIK))
    return ws


def _aktarildi_isaretle(kayit: dict, dosya, sayfa: str, satir: int,
                        aktaran: str | None = None) -> None:
    bekleyen = kayit.pop("_excel_bekleyen", None) or {}
    kayit["_excel_aktaran"] = aktaran or bekleyen.get("aktaran")
    kayit["_excel_aktarildi"] = True
    kayit["_excel_satir"] = satir
    kayit["_excel_dosya"] = str(dosya)
    kayit["_excel_sayfa"] = sayfa
    kayit["_excel_aktarim_zamani"] = datetime.now().isoformat(timespec="seconds")


def yarim_aktarimlari_uzlastir(kayitlar: list[dict]) -> list[dict]:
    """Excel'e yazılıp JSON'a işlenememiş (yarım kalmış) aktarımları tamamlar.

    Her bekleyen kayıt, hedef defterin kimlik sayfasında aranır: varsa
    defter kaydedilmiştir, kayıt aktarılmış işaretlenir (satır tekrar
    yazılmaz); yoksa defter hiç kaydedilmemiştir, bekleme kaldırılır.
    Tamamlanan kayıtları döndürür. Defter bulunamaz ya da satır sonradan
    değiştirilmişse AktarimHatasi fırlatır — tahmin yürütülüp ikinci kez
    yazılmaz.
    """
    gruplar: dict[str, list[dict]] = {}
    for kayit in kayitlar:
        bekleyen = kayit.get("_excel_bekleyen")
        if isinstance(bekleyen, dict) and bekleyen.get("dosya"):
            gruplar.setdefault(bekleyen["dosya"], []).append(kayit)
        elif bekleyen is not None:
            kayit.pop("_excel_bekleyen", None)

    tamamlanan = []
    for dosya, grup in gruplar.items():
        yol = Path(dosya)
        if not yol.exists():
            raise AktarimHatasi(
                f"Yarım kalan bir aktarımın defteri bulunamadı: {yol}. Defteri "
                "yerine koyup tekrar deneyin (fişlerin iki kez yazılmaması için)."
            )
        try:
            wb = load_workbook(yol)
        except Exception as exc:
            raise AktarimHatasi(
                f"Yarım kalan aktarımın defteri açılamadı ({exc}). Excel'de açıksa "
                "kapatıp tekrar deneyin."
            ) from exc
        try:
            ws_kimlik = _kimlik_sayfasi(wb)
            yazilan = {}
            if ws_kimlik is not None:
                for satir in ws_kimlik.iter_rows(min_row=2, values_only=True):
                    if satir and satir[0]:
                        yazilan[(str(satir[0]), str(satir[1]))] = satir
            for kayit in grup:
                bekleyen = kayit["_excel_bekleyen"]
                anahtar = (str(bekleyen.get("islem_id")), kayit_kimligi(kayit))
                if anahtar not in yazilan:
                    kayit.pop("_excel_bekleyen", None)
                    continue
                sayfa, satir = bekleyen.get("sayfa"), int(bekleyen.get("satir") or 0)
                if sayfa not in wb.sheetnames or satir < 1:
                    raise AktarimHatasi(
                        f"Yarım kalan aktarımın sayfası bulunamadı: '{sayfa}' ({yol.name})"
                    )
                ws = wb[sayfa]
                for alan, sutun in COL.items():
                    if ws.cell(row=satir, column=sutun).value != hucre_degeri(alan, kayit.get(alan)):
                        raise AktarimHatasi(
                            f"'{yol.name}' / '{sayfa}' satır {satir} yarım kalan "
                            "aktarımdan sonra değiştirilmiş; fişin iki kez yazılmaması "
                            "için işlem durduruldu. Satırı elle kontrol edin."
                        )
                _aktarildi_isaretle(kayit, dosya, sayfa, satir)
                tamamlanan.append(kayit)
        finally:
            wb.close()
    return tamamlanan


def defteri_kaydet(wb, xlsx_yolu: Path) -> None:
    """Defteri yanındaki geçici dosyaya yazıp tek adımda yerine koyar; yarıda
    kesilen kayıt defteri bozmaz."""
    gecici = xlsx_yolu.with_name(f".{xlsx_yolu.stem}.{uuid.uuid4().hex[:8]}.tmp{xlsx_yolu.suffix}")
    try:
        wb.save(gecici)
        os.replace(gecici, xlsx_yolu)
    finally:
        gecici.unlink(missing_ok=True)


def aktar(xlsx_yolu: Path, sayfa: str, baslangic_satir: int | None = None,
          tumu: bool = False, data_file: Path | None = None,
          kullanici: str | None = None) -> dict:
    """Onaylı fişleri seçilen dosya/sayfaya ekler. baslangic_satir verilmezse
    son dolu satırın iki altından (bir boşluk bırakarak) başlar.

    ``data_file`` hangi partinin sonuclar.json'unun kullanılacağını belirtir
    (verilmezse DATA_FILE). Aktarım iki aşamalıdır: kayıtlar önce JSON'da
    işlem kimliğiyle "bekleyen" işaretlenir, sonra satırlar ve kimlikler
    deftere tek kayıtla yazılır, en son JSON tamamlanır. Arada kesilirse
    sonraki çağrı aynı fişleri ikinci kez yazmaz.
    """
    xlsx_yolu = Path(xlsx_yolu)
    data_file = Path(data_file or DATA_FILE)
    if not xlsx_yolu.exists():
        return {"hata": f"Dosya bulunamadı: {xlsx_yolu}"}
    if not yol_guvenli_mi(xlsx_yolu):
        return {"hata": "Bu konuma yazmaya izin yok (yalnızca proje ve Belgeler klasörü)."}
    if sayfa == KIMLIK_SAYFASI:
        return {"hata": f"'{sayfa}' sayfası programın iç kaydıdır, aktarım yapılamaz"}

    kayitlar = yukle_json(data_file, [])
    bekleme_vardi = any("_excel_bekleyen" in k for k in kayitlar)
    try:
        tamamlanan = yarim_aktarimlari_uzlastir(kayitlar)
    except AktarimHatasi as exc:
        return {"hata": str(exc)}
    if bekleme_vardi:
        try:
            kaydet_json(data_file, kayitlar)
        except OSError as exc:
            return {"hata": f"Kayıtlar güncellenemedi: {exc}"}

    # Yarım kalan işlem bu hedefe aitse onu tamamlamak, aynı fişleri
    # (tumu=True olsa bile) aynı yere yeniden yazmak demek değildir.
    tamamlanan_burada = {
        id(k) for k in tamamlanan
        if _ayni_dosya_mi(k.get("_excel_dosya"), xlsx_yolu) and k.get("_excel_sayfa") == sayfa
    }
    aktarilacaklar = [
        k for k in bekleyen_kayitlar(kayitlar, tumu=tumu) if id(k) not in tamamlanan_burada
    ]
    tamamlanan_ozet = {"tamamlanan": len(tamamlanan)} if tamamlanan else {}
    if not aktarilacaklar:
        return {"aktarilan": 0, "detay": [], **tamamlanan_ozet}

    try:
        yedek_yolu = aktarim_oncesi_yedekle(xlsx_yolu)
    except OSError as exc:
        return {"hata": f"Excel yedeği alınamadığı için aktarım durduruldu: {exc}"}

    try:
        wb = load_workbook(xlsx_yolu)
    except Exception as exc:
        return {"hata": f"Dosya açılamadı ({exc}). Excel'de açıksa kapatıp tekrar dene."}

    try:
        if sayfa not in wb.sheetnames:
            return {"hata": f"'{sayfa}' sayfası bulunamadı"}
        try:
            ws_kimlik = _kimlik_sayfasi(wb, olustur=True)
        except AktarimHatasi as exc:
            return {"hata": str(exc)}

        ws = wb[sayfa]
        son_veri = son_veri_satirini_bul(ws)
        baslangic = int(baslangic_satir) if baslangic_satir else son_veri + 2
        stil_satiri = son_veri if son_veri else 1
        islem_id = uuid.uuid4().hex
        zaman = datetime.now().isoformat(timespec="seconds")

        # 1) Niyet: JSON'a, defter yazılmadan önce işlenir.
        for sira, kayit in enumerate(aktarilacaklar):
            kayit["_excel_bekleyen"] = {
                "islem_id": islem_id, "dosya": str(xlsx_yolu),
                "sayfa": sayfa, "satir": baslangic + sira, "aktaran": kullanici,
            }
        try:
            kaydet_json(data_file, kayitlar)
        except OSError as exc:
            return {"hata": f"Kayıtlar güncellenemedi, Excel'e yazılmadı: {exc}"}

        # 2) Satırlar ve kimlikleri aynı kayıtla deftere.
        detay = []
        satir = baslangic
        for kayit in aktarilacaklar:
            for alan, sutun in COL.items():
                hedef = ws.cell(row=satir, column=sutun)
                hucre_stilini_kopyala(ws.cell(row=stil_satiri, column=sutun), hedef)
                hedef.value = hucre_degeri(alan, kayit.get(alan))
            ws_kimlik.append([
                islem_id, kayit_kimligi(kayit), sayfa, satir,
                str(kayit.get("fis_no") or ""), zaman,
            ])
            detay.append({
                "satir": satir, "dosya": kayit.get("_dosya"), "kayit": kayit_kimligi(kayit),
                "fis_no": kayit.get("fis_no"),
                "firma": kayit.get("firma"), "plaka": kayit.get("plaka"),
            })
            satir += 1

        try:
            defteri_kaydet(wb, xlsx_yolu)
        except Exception as exc:
            for kayit in aktarilacaklar:
                kayit.pop("_excel_bekleyen", None)
            try:
                kaydet_json(data_file, kayitlar)
            except OSError:
                pass  # kalan bekleme bir sonraki çağrıda defterde yok diye silinir
            if isinstance(exc, PermissionError):
                return {"hata": "Dosyaya yazılamadı — Excel'de açık olabilir, kapatıp tekrar dene."}
            return {"hata": f"Dosyaya yazılamadı: {exc}"}
    finally:
        wb.close()

    # 3) Tamamlama.
    for kayit in aktarilacaklar:
        bekleyen = kayit["_excel_bekleyen"]
        _aktarildi_isaretle(kayit, xlsx_yolu, sayfa, bekleyen["satir"], kullanici)
    try:
        kaydet_json(data_file, kayitlar)
    except OSError as exc:
        return {"hata": (
            f"Fişler Excel'e yazıldı ama kayıtlar güncellenemedi ({exc}). "
            "Aynı aktarımı tekrar başlatın; fişler ikinci kez eklenmez."
        )}
    try:
        kaydet_json(AYAR_FILE, {"dosya": str(xlsx_yolu), "sayfa": sayfa})
    except OSError:
        pass  # yalnızca son seçimi hatırlatır

    return {
        "aktarilan": len(aktarilacaklar), "detay": detay,
        "ilk_satir": baslangic, "son_satir": satir - 1,
        "dosya": str(xlsx_yolu), "sayfa": sayfa,
        "yedek": str(yedek_yolu), **tamamlanan_ozet,
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
