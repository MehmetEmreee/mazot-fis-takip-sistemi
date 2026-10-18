r"""Fiş görsellerini OCR sonuçlarıyla yan yana kontrol etmek için yerel arayüz.

Kullanım:
    .\.venv\Scripts\python.exe arayuz.py

Arayüz varsayılan olarak http://127.0.0.1:8765 adresinde açılır.
"""

from __future__ import annotations

import copy
import hashlib
import io
import json
import mimetypes
import os
import queue
import re
import shutil
import socket
import subprocess
import sys
import threading
import time
import uuid
import webbrowser
from datetime import datetime
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote, urlparse

from .extract_json import resmi_oku  # noqa: E402
from .dogrulama import FIS_NO_DESENI, kaydi_dogrula, tarih_yilini_duzelt  # noqa: E402
from .normalizasyon import fis_normalize_et  # noqa: E402
from . import excel_aktar  # noqa: E402
from . import excel_ogrenme  # noqa: E402
from . import alan_yeniden_okuma  # noqa: E402
from . import parti_yonetimi  # noqa: E402
from . import yollar  # noqa: E402
from . import denetim  # noqa: E402
from . import erisim  # noqa: E402

try:  # iPhone HEIC fotoğraflarını Pillow ile açabilmek için (pillow-heif)
    from pillow_heif import register_heif_opener

    register_heif_opener()
except ImportError:
    pass

ROOT = yollar.VERI_DIZINI  # değişen veriler (data/)
UI_DIR = yollar.UI_DIZINI
DATA_FILE = ROOT / "sonuclar.json"
FISLER_DIR = ROOT / "fisler"
# Toplu ekleme "gelen kutusu": buraya atılan/senkronlanan fotoğraflar
# otomatik olarak OCR kuyruğuna alınır (uygulama.yollari_ayarla veri dizinine bağlar).
GELEN_DIR = ROOT / "gelen_fisler"
# Veri data/ altına taşınmadan önceki gelen kutusu. LocalSend gibi araçlar
# hâlâ buraya kaydediyorsa bu klasör de taranır (yoksa yok sayılır).
ESKI_GELEN_DIR: Path | None = yollar.UYGULAMA_KOKU / "gelen_fisler"
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}
EDITABLE_FIELDS = ("fis_no", "tarih", "firma", "plaka", "ad_soyad", "km", "litre")
MAX_UPLOAD_BYTES = 30 * 1024 * 1024  # 30MB güvenlik sınırı
DATA_LOCK = threading.Lock()
OCR_QUEUE: queue.Queue[tuple[Path, str]] = queue.Queue()
IMAGE_HASHES: dict[str, str] = {}

GELEN_ISLENDI = "_islendi"
GELEN_TEKRAR = "_tekrar"
GELEN_HATALI = "_hatali"
GELEN_ALT_KLASORLER = {GELEN_ISLENDI, GELEN_TEKRAR, GELEN_HATALI}
# Henüz yazılmakta olan / yarım inmiş dosyaların adlarında görülen ekler.
GELEN_YARIM_EKLER = (".tmp", ".part", ".crdownload", ".partial", ".download")
GELEN_TARAMA_ARALIGI_SN = 3.0
# LocalSend gibi uygulamalar dosyayı yerinde yazar; yarım görsel bu süre
# boyunca hiç değişmezse bozuk sayılıp _hatali/'ya taşınır.
GELEN_EKSIK_BEKLEME_SN = 120.0
# iPhone fotoğrafları: sadece Pillow açabiliyorsa JPEG'e çevrilip alınır.
HEIC_UZANTILARI = {".heic", ".heif"}
HEIF_MARKALARI = {b"heic", b"heix", b"heim", b"heis", b"hevc", b"hevx", b"mif1", b"msf1"}
GELEN_UZANTILARI = IMAGE_EXTENSIONS | HEIC_UZANTILARI
# Klasör izleyicisinin son durumu; /api/gelen buradan okur.
GELEN_DURUM: dict = {}
# Açılıştaki parti kurtarmasının sonucu; çakışma varsa arayüzde uyarı olur.
PARTI_KURTARMA: dict | None = None

# Uç noktaların gerektirdiği yetki (bkz. erisim.ROLLER).
GET_YETKILERI = {
    "/api/partiler": "goruntule", "/api/gelen": "goruntule",
    "/api/receipts": "goruntule", "/api/image": "goruntule",
    "/api/durum": "goruntule", "/api/denetim": "goruntule",
    "/api/excel/files": "aktar", "/api/excel/sheets": "aktar",
    "/api/excel/preview": "aktar",
}
POST_YETKILERI = {
    "/api/partiler/yeni": "aktar", "/api/save": "duzenle", "/api/upload": "yukle",
    "/api/gelen/ac": "duzenle", "/api/delete": "duzenle", "/api/retry": "duzenle",
    "/api/export-excel": "aktar", "/api/excel/upload": "aktar",
    "/api/excel/learn": "aktar",
}

ORIJINAL_AD_SINIRI = 120
KAYNAKLAR = {"telefon", "toplu", "klasor"}
CONTENT_TYPE_UZANTILARI = {
    "image/jpeg": ".jpg",
    "image/jpg": ".jpg",
    "image/pjpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "image/bmp": ".bmp",
    "image/x-bmp": ".bmp",
    "image/x-ms-bmp": ".bmp",
}


def lan_ip() -> str:
    """Bu makinenin yerel ağdaki IP adresini bulur (internete paket
    göndermeden — sadece işletim sistemine hangi arayüzün kullanılacağını
    sorar)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


def load_receipts_from(data_file: Path) -> list[dict]:
    if not data_file.exists():
        return []
    try:
        data = json.loads(data_file.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        raise ValueError(f"sonuclar.json okunamadı: {exc}") from exc
    if not isinstance(data, list):
        raise ValueError("sonuclar.json bir liste içermeli")
    return [kaydi_dogrula(fis_normalize_et(receipt)) for receipt in data]


def load_receipts() -> list[dict]:
    return load_receipts_from(DATA_FILE)


def save_receipts(receipts: list[dict]) -> None:
    receipts = [kaydi_dogrula(fis_normalize_et(receipt)) for receipt in receipts]
    temporary = DATA_FILE.with_name(f".{DATA_FILE.name}.{uuid.uuid4().hex}.tmp")
    temporary.write_text(
        json.dumps(receipts, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    try:
        for attempt in range(6):
            try:
                os.replace(temporary, DATA_FILE)
                return
            except PermissionError:
                if attempt == 5:
                    raise
                time.sleep(0.05 * (attempt + 1))
    finally:
        if temporary.exists():
            temporary.unlink(missing_ok=True)


def temp_dir() -> Path:
    """Yeni yüklenen, henüz onaylanmamış fişlerin geçici olarak durduğu
    klasör. FISLER_DIR --fisler-dir ile değişebildiği için sabit bir
    değişken yerine her seferinde hesaplanır."""
    d = FISLER_DIR / "temp"
    d.mkdir(parents=True, exist_ok=True)
    return d


def silinenler_dir() -> Path:
    d = FISLER_DIR / "silinenler"
    d.mkdir(parents=True, exist_ok=True)
    return d


def firma_klasor_adi(firma) -> str:
    """Firma adını Windows'ta geçerli bir klasör adına çevirir."""
    metin = str(firma or "").strip() or "DİĞER"
    metin = re.sub(r'[<>:"/\\|?*]', "", metin)
    metin = re.sub(r"\s+", " ", metin).strip(" .")
    return metin or "DİĞER"


def firma_klasorune_tasi(receipt: dict) -> None:
    """Onaylanan bir fişi, güncel firma adına göre fisler/<firma>/ altına
    taşır. Fiş zaten doğru klasördeyse bir şey yapmaz; firma sonradan
    düzeltilip yeniden onaylanırsa doğru klasöre yeniden taşır."""
    dosya = receipt.get("_dosya")
    mevcut = find_image(dosya) if dosya else None
    if not mevcut:
        return

    hedef_klasor = FISLER_DIR / firma_klasor_adi(receipt.get("firma"))
    hedef_klasor.mkdir(parents=True, exist_ok=True)
    hedef = hedef_klasor / mevcut.name

    if mevcut.resolve() == hedef.resolve():
        return
    if hedef.exists():
        hedef = benzersiz_hedef(hedef_klasor, mevcut.stem, mevcut.suffix.lower())
        receipt["_dosya"] = hedef.name
    mevcut.rename(hedef)


def guvenli_fis_no(fis_no) -> str | None:
    """fis_no'yu dosya adı olarak kullanılabilecek hale getirir. Okunamamış
    (None, '-', boş) veya harf/rakam dışında hiçbir şey içermeyen değerler
    için None döner — bu durumda zaman damgalı ad korunur."""
    if not fis_no or not isinstance(fis_no, str):
        return None
    temiz = re.sub(r"[^A-Za-z0-9_-]+", "", fis_no.strip())
    return temiz or None


def benzersiz_hedef(klasor: Path, taban: str, ext: str) -> Path:
    """Aynı fis_no ile ikinci bir fiş gelirse dosyanın üzerine yazmak yerine
    _2, _3 ... ekleyerek benzersiz bir ad üretir."""
    aday = klasor / f"{taban}{ext}"
    sayac = 2
    while aday.exists():
        aday = klasor / f"{taban}_{sayac}{ext}"
        sayac += 1
    return aday


def kullanilan_dosya_adlari(kayitlar: list[dict] | None = None) -> set[str]:
    """Kayıtlarda ve görsel klasörlerinde geçen tüm dosya adları.

    Onaylanan fişler firma klasörüne taşındığı için tek bir klasöre bakmak
    yetmez; aynı fiş numarasına sahip ikinci fiş aynı adı almamalı.
    """
    adlar = {str(k.get("_dosya")) for k in (kayitlar or []) if k.get("_dosya")}
    if FISLER_DIR.exists():
        adlar.update(
            yol.name for yol in FISLER_DIR.rglob("*")
            if yol.is_file() and yol.suffix.lower() in IMAGE_EXTENSIONS
        )
    return adlar


def benzersiz_kayit_adi(klasor: Path, taban: str, ext: str,
                        kayitlar: list[dict] | None = None) -> Path:
    """Hiçbir kayıtta ve hiçbir klasörde kullanılmayan bir dosya adı üretir."""
    kullanilan = kullanilan_dosya_adlari(kayitlar)
    aday = klasor / f"{taban}{ext}"
    sayac = 2
    while aday.exists() or aday.name in kullanilan:
        aday = klasor / f"{taban}_{sayac}{ext}"
        sayac += 1
    return aday


def dosya_hashi(path: Path) -> str:
    ozet = hashlib.sha256()
    with path.open("rb") as file:
        for parca in iter(lambda: file.read(1024 * 1024), b""):
            ozet.update(parca)
    return ozet.hexdigest()


def hash_indeksini_hazirla() -> None:
    """Mevcut fişleri bir kez tarayıp özdeş fotoğraf indeksini oluşturur."""
    IMAGE_HASHES.clear()
    for path in FISLER_DIR.rglob("*"):
        if path.is_file() and path.suffix.lower() in IMAGE_EXTENSIONS:
            # Geri dönüşüm klasöründeki silinmiş fişler yeni yüklemeyi engellemesin.
            if "silinenler" in {part.lower() for part in path.parts}:
                continue
            try:
                IMAGE_HASHES.setdefault(dosya_hashi(path), path.name)
            except OSError:
                continue


def orijinal_adi_temizle(ad, url_cozumle: bool = False) -> str | None:
    """X-Orijinal-Ad başlığından/dosya adından güvenli, kısa bir ad çıkarır.
    Klasör kısmı atılır; sadece kayıtta bilgi olarak saklanır. HTTP başlığı
    URL-kodlu geldiği için handler url_cozumle=True ile çağırır."""
    if not ad or not isinstance(ad, str):
        return None
    metin = unquote(ad) if url_cozumle else ad
    metin = metin.replace("\\", "/")
    metin = Path(metin).name.strip()
    metin = re.sub(r"[\x00-\x1f\x7f]", "", metin)
    if not metin or metin in (".", ".."):
        return None
    if len(metin) > ORIJINAL_AD_SINIRI:
        uzanti = Path(metin).suffix[:10]
        metin = metin[: ORIJINAL_AD_SINIRI - len(uzanti)] + uzanti
    return metin


def uzanti_belirle(content_type: str | None, orijinal_ad: str | None = None) -> str | None:
    """Content-Type'tan uzantı seçer; bilinmiyorsa orijinal adın uzantısına
    bakar. Hiçbir ipucu yoksa eski davranış gibi .jpg varsayılır."""
    tur = (content_type or "").split(";")[0].strip().lower()
    if tur in CONTENT_TYPE_UZANTILARI:
        return CONTENT_TYPE_UZANTILARI[tur]
    if orijinal_ad:
        uzanti = Path(orijinal_ad).suffix.lower()
        if uzanti:
            return uzanti
    if tur in ("", "application/octet-stream"):
        return ".jpg"
    return None


def imza_uzantisi(data: bytes) -> str | None:
    """Dosyanın ilk baytlarından gerçek görsel türünü bulur (uzantı yanlış
    olsa bile doğru uzantıyla kaydetmek için)."""
    if data[:3] == b"\xff\xd8\xff":
        return ".jpg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return ".png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return ".webp"
    if data[:2] == b"BM":
        return ".bmp"
    if data[4:8] == b"ftyp" and data[8:12] in HEIF_MARKALARI:
        return ".heic"
    return None


def fotografi_kabul_et(data: bytes, ext: str | None, orijinal_ad: str | None = None,
                       kaynak: str = "telefon", kim: dict | None = None) -> dict:
    """Bir fiş fotoğrafını temp/ klasörüne kaydeder, "işleniyor" kaydını
    sonuclar.json'a ekler ve OCR kuyruğuna koyar.

    Dönüş: {"ok": True, "filename", "receipt", "queue_size"} veya özdeş
    fotoğraf için {"ok": False, "duplicate": True, "existing_filename"}.
    Boş/çok büyük/desteklenmeyen dosyada ValueError, diske yazılamazsa
    OSError fırlatır. Telefon sayfası, masaüstü toplu ekleme ve gelen kutusu
    klasörü aynı yolu kullanır.
    """
    if not data:
        raise ValueError("Boş yükleme")
    if len(data) > MAX_UPLOAD_BYTES:
        raise ValueError("Dosya çok büyük (30MB sınırı)")
    ext = (ext or "").lower()
    if ext in (".jpe", ".jfif"):
        ext = ".jpg"
    ext = imza_uzantisi(data) or ext
    if ext in HEIC_UZANTILARI:
        # Tarayıcılar HEIC gösteremez; arşivde ve arayüzde JPEG olarak tut.
        data, ext = heic_jpege_cevir(data), ".jpg"
    if ext not in IMAGE_EXTENSIONS:
        raise ValueError(
            f"Desteklenmeyen dosya türü ({ext or 'bilinmiyor'}); "
            "JPG, PNG, WEBP veya BMP olmalı"
        )
    orijinal_ad = orijinal_adi_temizle(orijinal_ad)
    kaynak = kaynak if kaynak in KAYNAKLAR else "telefon"

    ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:-3]
    upload_id = uuid.uuid4().hex
    filename = f"fis_{ts}_{upload_id[:6]}{ext}"
    image_hash = hashlib.sha256(data).hexdigest()
    kayit = {
        "_dosya": filename,
        "_isleniyor": True,
        "_yukleme_id": upload_id,
        "_dosya_hash": image_hash,
        "_yukleme_zamani": datetime.now().isoformat(timespec="seconds"),
        "_kaynak": kaynak,
        "_yukleyen": (kim or {}).get("ad") or kaynak,
        "_kontrol_gerekli": True,
        "_kontrol_nedeni": "OCR kuyruğunda",
    }
    if orijinal_ad:
        kayit["_orijinal_ad"] = orijinal_ad
    record = fis_normalize_et(kayit)

    # Tekrar kontrolü, görselin yazılması, kaydın eklenmesi ve kuyruğa alma
    # tek kritik bölümde: arada parti kapanırsa görsel ile kayıt farklı
    # partilere düşerdi. Parti kapatma da aynı kilidi tutar.
    with DATA_LOCK:
        onceki_dosya = IMAGE_HASHES.get(image_hash)
        if onceki_dosya:
            return {"ok": False, "duplicate": True, "existing_filename": onceki_dosya}
        dest = temp_dir() / filename
        IMAGE_HASHES[image_hash] = filename
        try:
            dest.write_bytes(data)
            receipts = load_receipts()
            receipts.append(record)
            save_receipts(receipts)
        except (OSError, ValueError):
            # Kayıt yazılamadıysa yarım kalmış dosyayı ve hash'i geri al.
            if IMAGE_HASHES.get(image_hash) == filename:
                IMAGE_HASHES.pop(image_hash, None)
            dest.unlink(missing_ok=True)
            raise
        OCR_QUEUE.put((dest, image_hash))
        queue_size = OCR_QUEUE.qsize()

    try:
        denetim.kaydet("yukle", kim or {"ad": kaynak}, dosya=filename, kayit=upload_id,
                       kaynak=kaynak, orijinal_ad=orijinal_ad)
    except OSError as exc:  # fotoğraf kabul edildi; kaybetmektense uyar
        print(f"Denetim kaydı yazılamadı (yükleme {filename}): {exc}")
    return {
        "ok": True,
        "filename": filename,
        "receipt": record,
        "queue_size": queue_size,
    }


def _ocr_sonrasi_duzelt(record: dict, image_path: Path) -> None:
    """Öğrenme ve alan yeniden okuma isteğe bağlı iyileştirmelerdir; biri
    hata verirse OCR sonucu korunur ve kayıt kontrole işaretlenir."""
    ham_kayit = copy.deepcopy(record)
    for ad, adim in (
        ("Excel öğrenmesi", lambda: excel_ogrenme.guvenli_otomatik_duzelt(record)),
        # Geçmişte karşılığı olmayan alanı kırpıp alan adıyla yeniden okut.
        ("alan yeniden okuma",
         lambda: alan_yeniden_okuma.yeniden_oku_ve_duzelt(record, ham_kayit, image_path)),
    ):
        try:
            adim()
        except Exception as exc:
            print(f"OCR sonrası {ad} atlandı ({image_path.name}): {exc}")
            record.setdefault("_uyarilar", []).append(f"{ad} yapılamadı: {exc}")


def fisi_isle(image_path: Path, image_hash: str) -> None:
    """Kuyruktaki tek fişi okur, fiş no ile adlandırır ve kaydını günceller.

    Kayıt, kuyruğa alındığı andaki dosya adıyla (original_name) bulunur.
    Yeniden adlandırma ile JSON yazımı arasında süreç kesilirse başlangıçtaki
    yarim_ocr_kayitlarini_kurtar() görseli kayıttaki _dosya_hash ile bulur.
    """
    original_name = image_path.name
    try:
        record = resmi_oku(image_path)
    except Exception as exc:
        record = fis_normalize_et({
            "_dosya": original_name,
            "HATA": str(exc),
            "_kontrol_gerekli": True,
            "_kontrol_nedeni": "işleme hatası",
        })

    if not record.get("HATA"):
        _ocr_sonrasi_duzelt(record, image_path)
        kaydi_dogrula(fis_normalize_et(record))

    record["_isleniyor"] = False
    record["_dosya_hash"] = image_hash
    record["_dosya"] = original_name
    with DATA_LOCK:
        receipts = load_receipts()
        index = next(
            (i for i, item in enumerate(receipts)
             if item.get("_dosya") == original_name),
            None,
        )

        # OCR başarılıysa fiş no ile adlandırma kuralını koru (hâlâ temp/'te).
        fis_no = guvenli_fis_no(record.get("fis_no")) if not record.get("HATA") else None
        if fis_no and image_path.name != f"{fis_no}{image_path.suffix.lower()}":
            try:
                yeni_hedef = benzersiz_kayit_adi(
                    image_path.parent, fis_no, image_path.suffix.lower(),
                    [k for k in receipts if k.get("_dosya") != original_name],
                )
                image_path.rename(yeni_hedef)
                record["_dosya"] = yeni_hedef.name
            except OSError:
                record["_dosya"] = original_name

        if index is None:
            receipts.append(record)
            index = len(receipts) - 1
        else:
            onceki = receipts[index]
            record["_yukleme_zamani"] = onceki.get("_yukleme_zamani")
            record["_yukleme_id"] = onceki.get("_yukleme_id")
            for anahtar in ("_orijinal_ad", "_kaynak", "_yukleyen", "_deneme_sayisi"):
                if onceki.get(anahtar):
                    record[anahtar] = onceki[anahtar]
            receipts[index] = record

        # Tarih aralığı fişin yüklendiği güne göredir; yükleme zamanı artık kayıtta.
        if not record.get("HATA") and tarih_yilini_duzelt(record):
            kaydi_dogrula(record)

        IMAGE_HASHES[image_hash] = record.get("_dosya", original_name)
        fis_no = str(record.get("fis_no") or "").strip()
        if FIS_NO_DESENI.fullmatch(fis_no):
            diger = next(
                (item for i, item in enumerate(receipts)
                 if i != index and not item.get("_isleniyor")
                 and not item.get("_silindi")
                 and str(item.get("fis_no") or "").strip() == fis_no),
                None,
            )
            if diger:
                record["_tekrar"] = True
                record["_tekrar_nedeni"] = (
                    f"Aynı fiş no daha önce yüklendi: {diger.get('_dosya', '-')}"
                )
        save_receipts(receipts)
    try:
        denetim.kaydet("ocr_hatasi" if record.get("HATA") else "ocr", {"ad": "ocr", "rol": "sistem"},
                       dosya=record.get("_dosya"), kayit=record.get("_yukleme_id"),
                       onceki_dosya=original_name if record.get("_dosya") != original_name else None,
                       hata=record.get("HATA"), fis_no=record.get("fis_no"))
    except OSError as exc:
        print(f"Denetim kaydı yazılamadı (OCR {original_name}): {exc}")


def _islem_hatasini_kaydet(image_path: Path, image_hash: str, exc: Exception) -> None:
    """fisi_isle beklenmedik hata verdiyse kaydı 'işleniyor'da bırakmaz."""
    with DATA_LOCK:
        receipts = load_receipts()
        mevcut = image_path if image_path.exists() else None
        if mevcut is None and IMAGE_HASHES.get(image_hash):
            mevcut = find_image(IMAGE_HASHES[image_hash])
        for receipt in receipts:
            if receipt.get("_isleniyor") and (
                receipt.get("_dosya") == image_path.name
                or (image_hash and receipt.get("_dosya_hash") == image_hash)
            ):
                if mevcut is not None:
                    receipt["_dosya"] = mevcut.name
                receipt["_isleniyor"] = False
                receipt["HATA"] = f"İşleme hatası: {exc}"
                break
        save_receipts(receipts)


def ocr_worker(kuyruk: queue.Queue | None = None) -> None:
    """Yüklenen görselleri arka planda, GPU'yu yormadan tek tek işler.

    Bir fişteki hiçbir hata (bozuk kuyruk öğesi dahil) işçiyi durdurmaz;
    kuyruktaki sonraki fişe geçilir. İşçi başlatıldığı kuyruğa bağlı kalır.
    """
    kuyruk = kuyruk or OCR_QUEUE
    while True:
        is_ = kuyruk.get()
        try:
            image_path, image_hash = is_
            image_path = Path(image_path)
            fisi_isle(image_path, image_hash)
        except Exception as exc:
            print(f"OCR işçisi: {is_!r} işlenemedi: {exc}")
            try:
                _islem_hatasini_kaydet(Path(is_[0]), is_[1], exc)
            except Exception as kayit_hatasi:
                # Kayıtta _dosya_hash var; yeniden başlatmada kurtarılır.
                print(f"OCR işçisi: hata kaydı da yazılamadı: {kayit_hatasi}")
        finally:
            kuyruk.task_done()


def yarim_ocr_kayitlarini_kurtar() -> list[tuple[Path, str]]:
    """Başlangıçta 'işleniyor' kalmış kayıtları ve kayıtsız yüklemeleri
    görselleriyle uzlaştırır.

    Görsel kayıttaki adla bulunamazsa (ör. fiş no ile yeniden adlandırıldıktan
    sonra süreç kesildi) _dosya_hash ile aranır ve kayıt yeni ada bağlanır.
    Hiç bulunamayan kayıtlar sonsuza dek 'işleniyor' kalmasın diye hata olarak
    işaretlenir. Yeniden kuyruğa alınacak işleri döndürür.
    """
    isler: list[tuple[Path, str]] = []
    with DATA_LOCK:
        receipts = load_receipts()
        kullanilan = {r.get("_dosya") for r in receipts}
        degisti = False
        for receipt in receipts:
            if not receipt.get("_isleniyor") or receipt.get("_silindi"):
                continue
            ad = receipt.get("_dosya") or ""
            image_path = find_image(ad) if ad else None
            image_hash = receipt.get("_dosya_hash")
            if image_path is None and image_hash and IMAGE_HASHES.get(image_hash):
                aday = IMAGE_HASHES[image_hash]
                if aday not in kullanilan:
                    image_path = find_image(aday)
                if image_path is not None:
                    receipt["_dosya"] = image_path.name
                    kullanilan.add(image_path.name)
                    degisti = True
            if image_path is not None:
                isler.append((image_path, image_hash or dosya_hashi(image_path)))
                continue
            receipt["_isleniyor"] = False
            receipt["HATA"] = "Görsel bulunamadı (işlem yarıda kesilmiş olabilir)"
            degisti = True

        # Görsel yazılıp kayıt eklenemeden kesilen yüklemeler: kayıtsız görsel
        # hem kaybolur hem de hash indeksi aynı fotoğrafın yeniden yüklenmesini
        # engellerdi. Yeni bir 'işleniyor' kaydıyla kuyruğa geri alınır.
        temp = FISLER_DIR / "temp"
        yetimler = sorted(
            yol for yol in (temp.iterdir() if temp.is_dir() else [])
            if yol.is_file() and yol.suffix.lower() in IMAGE_EXTENSIONS
            and yol.name not in kullanilan
        )
        for yol in yetimler:
            image_hash = dosya_hashi(yol)
            receipts.append(fis_normalize_et({
                "_dosya": yol.name,
                "_isleniyor": True,
                "_yukleme_id": uuid.uuid4().hex,
                "_dosya_hash": image_hash,
                "_yukleme_zamani": datetime.fromtimestamp(
                    yol.stat().st_mtime).isoformat(timespec="seconds"),
                "_kaynak": "kurtarilan",
            }))
            kullanilan.add(yol.name)
            isler.append((yol, image_hash))
            degisti = True
        if degisti:
            save_receipts(receipts)

    # Yarıda kesilmiş atomik yazımlardan kalan geçici dosyalar.
    for gecici in DATA_FILE.parent.glob(f".{DATA_FILE.name}.*.tmp"):
        gecici.unlink(missing_ok=True)
    return isler


def _gelen_yarim_mi(ad: str) -> bool:
    """Gizli dosyalar ve indirme/kopyalama sırasında oluşan geçici adlar."""
    kucuk = ad.lower()
    return (
        kucuk.startswith(".") or kucuk.startswith("~")
        or kucuk.endswith(GELEN_YARIM_EKLER)
    )


def gelen_dosyalarini_listele(kok: Path | None = None) -> list[Path]:
    """Gelen kutusundaki aktarılmayı bekleyen görselleri ada göre sıralı
    döndürür (_islendi/_tekrar/_hatali ve gizli klasörler hariç)."""
    kok = kok or GELEN_DIR
    if not kok.is_dir():
        return []
    bulunan: list[Path] = []
    for klasor, alt_klasorler, dosyalar in os.walk(kok):
        klasor_yolu = Path(klasor)
        alt_klasorler[:] = [
            ad for ad in alt_klasorler
            if not ad.startswith(".")
            and not (klasor_yolu == kok and ad.lower() in GELEN_ALT_KLASORLER)
        ]
        for ad in dosyalar:
            if _gelen_yarim_mi(ad):
                continue
            if Path(ad).suffix.lower() not in GELEN_UZANTILARI:
                continue
            bulunan.append(klasor_yolu / ad)
    bulunan.sort(key=lambda yol: yol.relative_to(kok).as_posix().lower())
    return bulunan


def goruntu_tam_mi(data: bytes) -> bool:
    """Aktarımı bitmemiş (sonu kesik) görselleri yakalar. JPEG'de dosya
    EOI işaretiyle (FF D9, sonrasında sıfır dolgu olabilir) bitmeli; diğer
    türlerde Pillow'un verify() kontrolünden geçmeli."""
    tur = imza_uzantisi(data)
    if tur == ".jpg":
        return data.rstrip(b"\x00").endswith(b"\xff\xd9")
    try:
        from PIL import Image

        with Image.open(io.BytesIO(data)) as resim:
            if tur == ".heic":
                # HEIF'te verify() yalnız başlığa bakar; yarım dosyayı ancak
                # tam çözme yakalar.
                resim.load()
            else:
                resim.verify()
        return True
    except Exception:
        return False


def heic_jpege_cevir(data: bytes) -> bytes:
    """HEIC/HEIF'i Pillow açabiliyorsa JPEG'e çevirir; açamıyorsa ValueError.
    (pillow-heif kurulu değilse iPhone fotoğrafları açılamaz.)"""
    try:
        from PIL import Image, ImageOps

        with Image.open(io.BytesIO(data)) as resim:
            resim = ImageOps.exif_transpose(resim).convert("RGB")
            cikti = io.BytesIO()
            resim.save(cikti, "JPEG", quality=95)
            return cikti.getvalue()
    except Exception as exc:
        raise ValueError(
            "HEIC/HEIF fotoğrafı açılamadı (desteklenmiyor); telefonda "
            "'En Uyumlu' (JPG) biçimini seç veya JPG olarak gönder"
        ) from exc


def _gelen_tasi(yol: Path, alt_klasor: str) -> Path:
    """Kullanıcının dosyasını silmeden gelen kutusunun alt klasörüne taşır."""
    hedef_klasor = GELEN_DIR / alt_klasor
    if alt_klasor == GELEN_ISLENDI:
        hedef_klasor = hedef_klasor / datetime.now().strftime("%Y-%m-%d")
    hedef_klasor.mkdir(parents=True, exist_ok=True)
    hedef = hedef_klasor / yol.name
    if hedef.exists():
        hedef = benzersiz_hedef(hedef_klasor, yol.stem, yol.suffix)
    shutil.move(str(yol), str(hedef))
    return hedef


def gelen_kokleri() -> list[Path]:
    """Taranacak gelen kutuları: güncel klasör + varsa eski kök klasörü."""
    kokler = [GELEN_DIR]
    if (ESKI_GELEN_DIR is not None and ESKI_GELEN_DIR.is_dir()
            and ESKI_GELEN_DIR.resolve() != GELEN_DIR.resolve()):
        kokler.append(ESKI_GELEN_DIR)
    return kokler


def gelen_klasoru_tara(durum: dict) -> dict:
    """Gelen kutusunu bir kez tarar.

    Bir dosya ancak iki ardışık taramada boyutu ve değişiklik zamanı aynı
    kaldıysa aktarılır (senkron istemcisi veya kopyalama hâlâ yazıyor
    olabilir). `durum` taramalar arasında saklanan sözlüktür; testler için
    dışarıdan verilir. Bu geçişin özetini döndürür.
    """
    GELEN_DIR.mkdir(parents=True, exist_ok=True)
    gorulen: dict[str, tuple[int, int]] = durum.setdefault("gorulen", {})
    tasinacak: dict[str, str] = durum.setdefault("tasinacak", {})
    eksik: dict[str, tuple[tuple[int, int], float]] = durum.setdefault("eksik", {})
    bugun = datetime.now().strftime("%Y-%m-%d")
    if durum.get("gun") != bugun:
        durum["gun"] = bugun
        durum["son_aktarilan"] = 0
    ozet = {"aktarilan": 0, "tekrar": 0, "hatali": 0}
    islenen: set[str] = set()

    # Önceki geçişte kabul edilip taşınamayan dosyalar (ör. Windows kilidi).
    for yol_metni, alt_klasor in list(tasinacak.items()):
        yol = Path(yol_metni)
        if not yol.exists():
            tasinacak.pop(yol_metni, None)
            continue
        try:
            _gelen_tasi(yol, alt_klasor)
            tasinacak.pop(yol_metni, None)
        except OSError:
            pass

    simdiki: dict[str, tuple[int, int]] = {}
    hazir: list[Path] = []
    for yol in [y for kok in gelen_kokleri() for y in gelen_dosyalarini_listele(kok)]:
        anahtar = str(yol)
        if anahtar in tasinacak:
            continue
        try:
            bilgi = yol.stat()
        except OSError:
            continue
        imza = (bilgi.st_size, bilgi.st_mtime_ns)
        simdiki[anahtar] = imza
        if bilgi.st_size > 0 and gorulen.get(anahtar) == imza:
            hazir.append(yol)
    durum["gorulen"] = gorulen = simdiki
    for anahtar in list(eksik):
        if anahtar not in simdiki:
            eksik.pop(anahtar, None)

    for yol in hazir:
        anahtar = str(yol)
        try:
            if yol.stat().st_size > MAX_UPLOAD_BYTES:
                raise ValueError("Dosya çok büyük (30MB sınırı)")
            data = yol.read_bytes()
        except PermissionError:
            # Başka bir program hâlâ tutuyor; iki sağlam taramayı yeniden bekle.
            gorulen.pop(anahtar, None)
            continue
        except FileNotFoundError:
            gorulen.pop(anahtar, None)
            continue
        except (OSError, ValueError) as exc:
            sonuc = {"hata": str(exc)}
        else:
            uzanti = yol.suffix.lower()
            if not goruntu_tam_mi(data):
                # Büyük ihtimalle hâlâ aktarılıyor: dokunma, sonraki taramada
                # yeniden bak. Uzun süre hiç değişmeden yarım kalırsa bozuktur.
                imza = simdiki[anahtar]
                onceki = eksik.get(anahtar)
                if onceki is None or onceki[0] != imza:
                    eksik[anahtar] = (imza, time.monotonic())
                    continue
                if time.monotonic() - onceki[1] < GELEN_EKSIK_BEKLEME_SN:
                    continue
                sonuc = {"hata": "Görsel eksik/bozuk (aktarım tamamlanmamış olabilir)"}
            else:
                try:
                    if uzanti in HEIC_UZANTILARI:
                        data, uzanti = heic_jpege_cevir(data), ".jpg"
                    sonuc = fotografi_kabul_et(
                        data, uzanti, orijinal_ad=yol.name, kaynak="klasor"
                    )
                except ValueError as exc:
                    sonuc = {"hata": str(exc)}
                except OSError as exc:
                    # Sorun bizim tarafta (disk/kayıt); dosyaya dokunma, sonra dene.
                    print(f"Gelen kutusu: {yol.name} kaydedilemedi: {exc}")
                    gorulen.pop(anahtar, None)
                    continue
        eksik.pop(anahtar, None)

        if sonuc.get("ok"):
            alt_klasor, sayac = GELEN_ISLENDI, "aktarilan"
        elif sonuc.get("duplicate"):
            alt_klasor, sayac = GELEN_TEKRAR, "tekrar"
        else:
            alt_klasor, sayac = GELEN_HATALI, "hatali"
            print(f"Gelen kutusu: {yol.name} aktarılamadı: {sonuc.get('hata')}")
        ozet[sayac] += 1
        islenen.add(anahtar)
        gorulen.pop(anahtar, None)
        try:
            _gelen_tasi(yol, alt_klasor)
        except OSError:
            tasinacak[anahtar] = alt_klasor

    durum["son_aktarilan"] = durum.get("son_aktarilan", 0) + ozet["aktarilan"]
    durum["bekleyen"] = len([y for y in simdiki if y not in islenen])
    if any(ozet.values()):
        print(
            f"Gelen kutusu: {ozet['aktarilan']} fotoğraf kuyruğa alındı"
            + (f", {ozet['tekrar']} tekrar" if ozet["tekrar"] else "")
            + (f", {ozet['hatali']} hatalı" if ozet["hatali"] else "")
        )
    return ozet


def gelen_izleyici() -> None:
    """Gelen kutusunu birkaç saniyede bir tarayan arka plan iş parçacığı."""
    while True:
        try:
            gelen_klasoru_tara(GELEN_DURUM)
        except Exception as exc:  # izleyici hiçbir hatada durmamalı
            print(f"Gelen kutusu taranamadı: {exc}")
        time.sleep(GELEN_TARAMA_ARALIGI_SN)


def klasoru_ac(yol: Path) -> None:
    """Klasörü işletim sisteminin dosya yöneticisinde açar."""
    if sys.platform.startswith("win"):
        os.startfile(str(yol))  # type: ignore[attr-defined]
    elif sys.platform == "darwin":
        subprocess.Popen(["open", str(yol)])
    else:
        subprocess.Popen(["xdg-open", str(yol)])


def find_image(filename: str, images_dir: Path | None = None) -> Path | None:
    """Seçilen partinin görsel klasöründe adı tam eşleşen dosyayı döndürür."""
    safe_name = Path(filename).name
    if not safe_name or safe_name != filename:
        return None

    images_dir = images_dir or FISLER_DIR
    direct = images_dir / safe_name
    if direct.is_file() and direct.suffix.lower() in IMAGE_EXTENSIONS:
        return direct

    if not images_dir.is_dir():
        return None
    for path in images_dir.rglob(safe_name):
        if path.is_file() and path.suffix.lower() in IMAGE_EXTENSIONS:
            return path
    return None


class IslemCakismasi(Exception):
    """Kayıt şu an başka bir işlem (OCR) tarafından güncelleniyor."""


def _cerez(ad: str, deger: str, sure_sn: int) -> str:
    return f"{ad}={deger}; Path=/; HttpOnly; SameSite=Lax; Max-Age={sure_sn}"


def durum_ozeti() -> dict:
    """Arayüzün göstereceği bekleyen/başarısız işler özeti."""
    with DATA_LOCK:
        kayitlar = [k for k in load_receipts() if not k.get("_silindi")]
    hatali_gelen = GELEN_DIR / GELEN_HATALI
    return {
        "kuyruk": OCR_QUEUE.qsize(),
        "isleniyor": sum(bool(k.get("_isleniyor")) for k in kayitlar),
        "ocr_hatali": [k.get("_dosya") for k in kayitlar if k.get("HATA")],
        "excel_yarim": [k.get("_dosya") for k in kayitlar if k.get("_excel_bekleyen")],
        "gelen_hatali": sum(1 for p in hatali_gelen.rglob("*") if p.is_file())
        if hatali_gelen.is_dir() else 0,
        "gelen_klasoru": str(hatali_gelen),
        "parti_uyarisi": PARTI_KURTARMA if (PARTI_KURTARMA or {}).get("durum") == "cakisma" else None,
    }


class ReceiptHandler(SimpleHTTPRequestHandler):
    server_version = "FisKontrol/1.0"

    def log_message(self, format: str, *args) -> None:
        print(f"[{self.log_date_time_string()}] {format % args}")

    def send_json(self, payload, status=HTTPStatus.OK, cerez: str | None = None) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        if cerez:
            self.send_header("Set-Cookie", cerez)
        self.end_headers()
        self.wfile.write(body)

    def yonlendir(self, hedef: str, cerez: str | None = None) -> None:
        self.send_response(HTTPStatus.FOUND)
        self.send_header("Location", hedef)
        if cerez:
            self.send_header("Set-Cookie", cerez)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def istemci(self) -> str:
        return str(self.client_address[0])

    def kim(self) -> dict | None:
        if not hasattr(self, "_kim"):
            try:
                self._kim = erisim.kimlik(self.headers.get("Cookie"), self.istemci())
            except erisim.ErisimHatasi as exc:
                print(f"Erişim: {exc}")
                self._kim = None
        return self._kim

    def yetkili(self, yetki: str | None) -> bool:
        """Yetki yoksa 401/403 gönderir ve False döner."""
        if yetki is None:
            return True
        kim = self.kim()
        if erisim.yetkili_mi(kim, yetki):
            return True
        if kim is None:
            self.send_json({"error": "Giriş gerekli", "giris_gerekli": True},
                           HTTPStatus.UNAUTHORIZED)
        else:
            self.send_json({"error": "Bu işlem için yetkiniz yok"}, HTTPStatus.FORBIDDEN)
        return False

    def denetle(self, islem: str, **ayrinti) -> None:
        denetim.kaydet(islem, self.kim(), istemci=self.istemci(), **ayrinti)

    def read_json(self) -> dict:
        length = int(self.headers.get("Content-Length", "0"))
        return json.loads(self.rfile.read(length).decode("utf-8"))

    def do_GET(self) -> None:
        parsed = urlparse(self.path)

        if parsed.path == "/api/oturum":
            kim = self.kim()
            yanit = {"kim": kim, "kullanici_modu": erisim.kullanici_var_mi()}
            if erisim.yetkili_mi(kim, "aktar"):
                yanit["telefon_baglantisi"] = f"/cek?anahtar={erisim.yukleme_anahtari()}"
            self.send_json(yanit)
            return
        if parsed.path == "/giris":
            self.path = "/giris.html"
            super().do_GET()
            return
        if parsed.path == "/cek":
            anahtar = parse_qs(parsed.query).get("anahtar", [""])[0]
            if anahtar and anahtar == erisim.yukleme_anahtari():
                self.yonlendir("/cek", _cerez(erisim.YUKLEME_COOKIE, anahtar, 365 * 86400))
                return
            if not erisim.yetkili_mi(self.kim(), "yukle"):
                self.yonlendir("/giris?sonra=/cek")
                return
        if parsed.path in ("/", "/index.html"):
            kim = self.kim()
            if kim is None:
                self.yonlendir("/giris")
                return
            if not erisim.yetkili_mi(kim, "goruntule"):
                self.yonlendir("/cek")
                return
        yetki = GET_YETKILERI.get(parsed.path)
        if parsed.path == "/api/receipts" and erisim.yetkili_mi(self.kim(), "yukle"):
            yetki = None  # telefon yalnız kendi yüklemelerini görür (aşağıda)
        if parsed.path.startswith("/api/") and yetki is None and parsed.path not in GET_YETKILERI:
            yetki = "goruntule"
        if not self.yetkili(yetki):
            return

        if parsed.path == "/api/durum":
            self.send_json(durum_ozeti())
            return

        if parsed.path == "/api/denetim":
            query = parse_qs(parsed.query)
            aranan = query.get("dosya", [None])[0]
            if not aranan and not self.yetkili("yonet"):
                return
            self.send_json({"olaylar": denetim.oku(aranan, limit=500 if not aranan else 100)})
            return

        if parsed.path == "/api/partiler":
            self.send_json({"partiler": parti_yonetimi.partileri_listele()})
            return

        if parsed.path == "/api/gelen":
            if "bekleyen" in GELEN_DURUM:
                bekleyen = GELEN_DURUM["bekleyen"]
            else:
                bekleyen = len(gelen_dosyalarini_listele())
            bugun = datetime.now().strftime("%Y-%m-%d")
            self.send_json({
                "klasor": str(GELEN_DIR),
                "bekleyen": bekleyen,
                "son_aktarilan": (
                    GELEN_DURUM.get("son_aktarilan", 0)
                    if GELEN_DURUM.get("gun") == bugun else 0
                ),
            })
            return

        if parsed.path == "/api/receipts":
            try:
                parti_id = parse_qs(parsed.query).get("parti", ["aktif"])[0]
                data_file, _, salt_okunur = parti_yonetimi.parti_yollari(parti_id)
                with DATA_LOCK:
                    receipts = [r for r in load_receipts_from(data_file) if not r.get("_silindi")]
                    if not erisim.yetkili_mi(self.kim(), "goruntule"):
                        kim = self.kim() or {}
                        receipts = [] if salt_okunur else [
                            r for r in receipts if r.get("_yukleyen") == kim.get("ad")]
                    for receipt in receipts:
                        receipt["_excel_onerileri"] = excel_ogrenme.kayit_onerileri(receipt)
                    modified = (
                        datetime.fromtimestamp(data_file.stat().st_mtime).isoformat()
                        if data_file.exists()
                        else None
                    )
                self.send_json({
                    "receipts": receipts, "modified": modified,
                    "parti": parti_id, "salt_okunur": salt_okunur,
                })
            except (ValueError, LookupError) as exc:
                self.send_json({"error": str(exc)}, HTTPStatus.INTERNAL_SERVER_ERROR)
            return

        if parsed.path == "/api/image":
            query = parse_qs(parsed.query)
            filename = query.get("file", [""])[0]
            parti_id = query.get("parti", ["aktif"])[0]
            try:
                _, images_dir, _ = parti_yonetimi.parti_yollari(parti_id)
            except LookupError:
                self.send_json({"error": "Parti bulunamadı"}, HTTPStatus.NOT_FOUND)
                return
            image_path = find_image(unquote(filename), images_dir)
            if not image_path:
                self.send_json({"error": "Görsel bulunamadı"}, HTTPStatus.NOT_FOUND)
                return
            content = image_path.read_bytes()
            self.send_response(HTTPStatus.OK)
            self.send_header(
                "Content-Type", mimetypes.guess_type(image_path.name)[0] or "image/jpeg"
            )
            self.send_header("Content-Length", str(len(content)))
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            self.wfile.write(content)
            return

        if parsed.path == "/api/excel/files":
            try:
                parti_id = parse_qs(parsed.query).get("parti", ["aktif"])[0]
                data_file, _, _ = parti_yonetimi.parti_yollari(parti_id)
                self.send_json({
                    "dosyalar": excel_aktar.excel_dosyalarini_bul(),
                    "son_ayar": excel_aktar.son_ayar(),
                    "bekleyen": len(excel_aktar.bekleyen_kayitlar(data_file=data_file)),
                    "ogrenme": excel_ogrenme.durum(),
                })
            except Exception as exc:
                self.send_json({"error": str(exc)}, HTTPStatus.INTERNAL_SERVER_ERROR)
            return

        if parsed.path == "/api/excel/sheets":
            dosya = unquote(parse_qs(parsed.query).get("dosya", [""])[0])
            try:
                yol = Path(dosya)
                if not excel_aktar.yol_guvenli_mi(yol):
                    raise ValueError("Bu dosyaya erişim izni yok")
                self.send_json({"sayfalar": excel_aktar.sayfa_adlari(yol)})
            except Exception as exc:
                self.send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
            return

        if parsed.path == "/api/excel/preview":
            query = parse_qs(parsed.query)
            dosya = unquote(query.get("dosya", [""])[0])
            sayfa = unquote(query.get("sayfa", [""])[0])
            parti_id = query.get("parti", ["aktif"])[0]
            try:
                yol = Path(dosya)
                if not excel_aktar.yol_guvenli_mi(yol):
                    raise ValueError("Bu dosyaya erişim izni yok")
                data_file, _, _ = parti_yonetimi.parti_yollari(parti_id)
                tumu = query.get("tumu", ["0"])[0] in ("1", "true", "True")
                onizleme = excel_aktar.sayfa_onizleme(yol, sayfa)
                kayitlar = excel_aktar.yukle_json(data_file, [])
                onizleme["bekleyen"] = len(excel_aktar.bekleyen_kayitlar(kayitlar, tumu=tumu))
                onizleme["aktarilmis"] = len(
                    [k for k in excel_aktar.bekleyen_kayitlar(kayitlar, tumu=True)
                     if k.get("_excel_aktarildi")]
                )
                self.send_json(onizleme)
            except Exception as exc:
                self.send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
            return

        if parsed.path == "/":
            self.path = "/index.html"
        elif parsed.path == "/cek":
            self.path = "/cek.html"
        elif parsed.path.startswith("/ui/"):
            self.path = parsed.path.removeprefix("/ui")
        super().do_GET()

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        if path == "/api/giris":
            self.handle_login()
            return
        if path == "/api/cikis":
            self.handle_logout()
            return
        if not self.yetkili(POST_YETKILERI.get(path, "yonet")):
            return
        if path == "/api/retry":
            self.handle_retry()
        elif path == "/api/partiler/yeni":
            self.handle_new_batch()
        elif path == "/api/save":
            self.handle_save()
        elif path == "/api/upload":
            self.handle_upload()
        elif path == "/api/gelen/ac":
            self.handle_gelen_ac()
        elif path == "/api/delete":
            self.handle_delete()
        elif path == "/api/export-excel":
            self.handle_export_excel()
        elif path == "/api/excel/upload":
            self.handle_excel_upload()
        elif path == "/api/excel/learn":
            self.handle_excel_learn()
        else:
            self.send_error(HTTPStatus.NOT_FOUND)

    def handle_login(self) -> None:
        try:
            payload = self.read_json()
            ad = str(payload.get("ad") or "")
            anahtar = erisim.giris_yap(ad, payload.get("parola"), self.istemci())
        except (erisim.ErisimHatasi, json.JSONDecodeError) as exc:
            try:
                denetim.kaydet("giris_basarisiz", {"ad": locals().get("ad") or "?"},
                               istemci=self.istemci(), hata=str(exc))
            except OSError:
                pass
            self.send_json({"error": str(exc)}, HTTPStatus.UNAUTHORIZED)
            return
        self._kim = erisim.kimlik(f"{erisim.OTURUM_COOKIE}={anahtar}", self.istemci())
        self.denetle("giris")
        self.send_json({"ok": True, "kim": self._kim},
                       cerez=_cerez(erisim.OTURUM_COOKIE, anahtar, erisim.OTURUM_SURESI_SN))

    def handle_logout(self) -> None:
        oturum = erisim.cerezleri_ayristir(self.headers.get("Cookie")).get(erisim.OTURUM_COOKIE)
        if self.kim():
            try:
                self.denetle("cikis")
            except OSError:
                pass
        erisim.cikis_yap(oturum)
        self.send_json({"ok": True}, cerez=_cerez(erisim.OTURUM_COOKIE, "", 0))

    def handle_retry(self) -> None:
        """OCR'ı başarısız olmuş fişi güvenle yeniden kuyruğa alır."""
        try:
            payload = self.read_json()
            filename = payload.get("filename")
            if not isinstance(filename, str) or not filename:
                raise ValueError("Geçersiz dosya adı")
            with DATA_LOCK:
                receipts = load_receipts()
                eslesen = [r for r in receipts
                           if r.get("_dosya") == filename and not r.get("_silindi")]
                if len(eslesen) != 1:
                    raise LookupError(f"Kayıt bulunamadı: {filename}")
                receipt = eslesen[0]
                if receipt.get("_isleniyor"):
                    raise IslemCakismasi("Bu fiş zaten işleniyor")
                if not receipt.get("HATA"):
                    raise IslemCakismasi("Bu fişte tekrar denenecek bir hata yok")
                image_path = find_image(filename)
                if image_path is None:
                    raise LookupError("Fişin görseli bulunamadı; tekrar denenemez")
                image_hash = receipt.get("_dosya_hash") or dosya_hashi(image_path)
                onceki_hata = receipt.pop("HATA")
                receipt["_isleniyor"] = True
                receipt["_dosya_hash"] = image_hash
                receipt["_deneme_sayisi"] = int(receipt.get("_deneme_sayisi") or 0) + 1
                save_receipts(receipts)
                OCR_QUEUE.put((image_path, image_hash))
            self.denetle("tekrar_dene", dosya=filename, kayit=receipt.get("_yukleme_id"),
                         onceki_hata=onceki_hata)
            self.send_json({"ok": True, "queue_size": OCR_QUEUE.qsize()})
        except IslemCakismasi as exc:
            self.send_json({"error": str(exc)}, HTTPStatus.CONFLICT)
        except LookupError as exc:
            self.send_json({"error": str(exc)}, HTTPStatus.NOT_FOUND)
        except (ValueError, json.JSONDecodeError, OSError) as exc:
            self.send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)

    def handle_new_batch(self) -> None:
        """Aktif partiyi Excel'e dokunmadan arşivler ve temiz parti açar."""
        try:
            payload = self.read_json()
            ad = payload.get("ad")
            if not isinstance(ad, str) or not ad.strip():
                raise ValueError("Yeni parti için bir ad yazılmalı")

            with DATA_LOCK:
                kayitlar = [k for k in load_receipts() if not k.get("_silindi")]
                islenen = [k for k in kayitlar if k.get("_isleniyor")]
                onaysiz = [k for k in kayitlar if not k.get("_onaylandi")]
                if islenen:
                    raise ValueError(
                        f"{len(islenen)} fişin OCR işlemi sürüyor; parti henüz kapatılamaz."
                    )
                if onaysiz:
                    raise ValueError(
                        f"{len(onaysiz)} fiş henüz onaylanmadı; önce kontrolü tamamla."
                    )

                sonuc = parti_yonetimi.yeni_parti(ad, None)
                hash_indeksini_hazirla()
                arsiv = sonuc.get("arsivlenen") or {}
                self.denetle("parti_kapat", arsiv=arsiv.get("id"), arsiv_adi=arsiv.get("ad"),
                             kayit_sayisi=arsiv.get("toplam"), yeni_parti=ad)

            self.send_json({"ok": True, **sonuc})
        except (ValueError, json.JSONDecodeError, OSError) as exc:
            self.send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
        except Exception as exc:
            self.send_json({"error": str(exc)}, HTTPStatus.INTERNAL_SERVER_ERROR)

    def handle_save(self) -> None:
        try:
            payload = self.read_json()
            filename = payload.get("filename")
            fields = payload.get("fields", {})
            approved = bool(payload.get("approved", False))
            if not isinstance(filename, str) or not isinstance(fields, dict):
                raise ValueError("Geçersiz kayıt verisi")

            with DATA_LOCK:
                receipts = load_receipts()
                eslesen = [r for r in receipts if r.get("_dosya") == filename]
                if len(eslesen) > 1:
                    raise ValueError(
                        f"'{filename}' adını birden çok fiş kullanıyor; yanlış "
                        "kaydın üzerine yazmamak için işlem durduruldu."
                    )
                receipt = eslesen[0] if eslesen else None
                if receipt is None:
                    raise LookupError(f"Kayıt bulunamadı: {filename}")
                if receipt.get("_isleniyor"):
                    # OCR bitince kayıt OCR sonucuyla değişir; değişiklik kaybolmasın.
                    raise IslemCakismasi(
                        "Bu fişin OCR işlemi sürüyor; bitince tekrar deneyin."
                    )

                changed = False
                changed_fields: set[str] = set()
                onceki_degerler = {
                    field: receipt.get(field) for field in EDITABLE_FIELDS
                }
                for field in EDITABLE_FIELDS:
                    if field in fields:
                        value = fields[field]
                        value = "-" if value in (None, "") else str(value).strip()
                        if receipt.get(field) != value:
                            changed = True
                            changed_fields.add(field)
                        receipt[field] = value

                kaydi_dogrula(fis_normalize_et(receipt))

                onay_onceki = bool(receipt.get("_onaylandi"))
                zaman = datetime.now().isoformat(timespec="seconds")
                kullanici = (self.kim() or {}).get("ad")
                receipt["_onaylandi"] = approved
                receipt["_duzeltildi"] = bool(receipt.get("_duzeltildi")) or changed
                receipt["_kontrol_zamani"] = zaman
                receipt["_son_duzenleyen"] = kullanici
                if approved:
                    receipt["_onaylayan"] = kullanici
                    receipt["_onay_zamani"] = zaman
                    firma_klasorune_tasi(receipt)
                save_receipts(receipts)
                self.denetle(
                    "onayla" if approved else ("onay_kaldir" if onay_onceki else "duzenle"),
                    dosya=receipt.get("_dosya"), kayit=receipt.get("_yukleme_id"),
                    degisiklikler={
                        alan: [onceki_degerler[alan], receipt.get(alan)]
                        for alan in sorted(changed_fields)
                    },
                )
                learned_count = excel_ogrenme.duzeltmelerden_ogren(
                    receipt, changed_fields, onceki_degerler
                )

            receipt["_excel_onerileri"] = excel_ogrenme.kayit_onerileri(receipt)
            self.send_json({
                "ok": True, "receipt": receipt, "ogrenilen": learned_count,
            })
        except IslemCakismasi as exc:
            self.send_json({"error": str(exc)}, HTTPStatus.CONFLICT)
        except LookupError as exc:
            self.send_json({"error": str(exc)}, HTTPStatus.NOT_FOUND)
        except (ValueError, json.JSONDecodeError, OSError) as exc:
            self.send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)

    def handle_upload(self) -> None:
        """Fotoğrafı hızla kaydeder ve OCR'ın bitmesini beklemeden kuyruğa alır."""
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length <= 0:
                raise ValueError("Boş yükleme")
            if length > MAX_UPLOAD_BYTES:
                raise ValueError("Dosya çok büyük (30MB sınırı)")
            data = self.rfile.read(length)

            orijinal_ad = orijinal_adi_temizle(
                self.headers.get("X-Orijinal-Ad"), url_cozumle=True
            )
            ext = uzanti_belirle(self.headers.get("Content-Type"), orijinal_ad)
            kaynak = (self.headers.get("X-Kaynak") or "telefon").strip().lower()
            sonuc = fotografi_kabul_et(data, ext, orijinal_ad, kaynak=kaynak, kim=self.kim())
        except ValueError as exc:
            self.send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
            return
        except OSError as exc:
            self.send_json({"error": str(exc)}, HTTPStatus.INTERNAL_SERVER_ERROR)
            return

        if sonuc.get("duplicate"):
            self.send_json(
                {
                    "error": "Bu fotoğraf daha önce yüklendi",
                    "duplicate": True,
                    "existing_filename": sonuc["existing_filename"],
                },
                HTTPStatus.CONFLICT,
            )
            return
        self.send_json(sonuc, HTTPStatus.ACCEPTED)

    def handle_gelen_ac(self) -> None:
        """Gelen kutusu klasörünü dosya yöneticisinde açar — sadece sunucunun
        çalıştığı bilgisayardan (telefondan/ağdan gelen istek reddedilir)."""
        istemci = str(self.client_address[0])
        if istemci not in ("127.0.0.1", "::1", "::ffff:127.0.0.1"):
            self.send_json(
                {"error": "Klasör sadece programın çalıştığı bilgisayardan açılabilir"},
                HTTPStatus.FORBIDDEN,
            )
            return
        try:
            GELEN_DIR.mkdir(parents=True, exist_ok=True)
            klasoru_ac(GELEN_DIR)
            self.send_json({"ok": True, "klasor": str(GELEN_DIR)})
        except OSError as exc:
            self.send_json({"error": str(exc)}, HTTPStatus.INTERNAL_SERVER_ERROR)

    def handle_delete(self) -> None:
        """Fişi kalıcı silmez — dosyayı fisler/silinenler/ klasörüne taşır
        ve kaydı _silindi=true olarak işaretler. Geri almak istenirse dosya
        elle silinenler/'den geri taşınıp _silindi false yapılabilir."""
        try:
            payload = self.read_json()
            filename = payload.get("filename")
            if not isinstance(filename, str) or not filename:
                raise ValueError("Geçersiz dosya adı")

            with DATA_LOCK:
                receipts = load_receipts()
                eslesen = [r for r in receipts if r.get("_dosya") == filename]
                if len(eslesen) > 1:
                    raise ValueError(
                        f"'{filename}' adını birden çok fiş kullanıyor; yanlış "
                        "kaydın üzerine yazmamak için işlem durduruldu."
                    )
                receipt = eslesen[0] if eslesen else None
                if receipt is None:
                    raise LookupError(f"Kayıt bulunamadı: {filename}")
                if receipt.get("_isleniyor"):
                    # OCR bitince kayıt OCR sonucuyla değişir; değişiklik kaybolmasın.
                    raise IslemCakismasi(
                        "Bu fişin OCR işlemi sürüyor; bitince tekrar deneyin."
                    )

                mevcut = find_image(filename)
                if mevcut:
                    hedef = silinenler_dir() / mevcut.name
                    if hedef.exists():
                        hedef = benzersiz_hedef(silinenler_dir(), mevcut.stem, mevcut.suffix.lower())
                    mevcut.rename(hedef)

                receipt["_silindi"] = True
                receipt["_silinme_zamani"] = datetime.now().isoformat(timespec="seconds")
                receipt["_silen"] = (self.kim() or {}).get("ad")
                image_hash = receipt.get("_dosya_hash")
                if image_hash and IMAGE_HASHES.get(image_hash) == filename:
                    IMAGE_HASHES.pop(image_hash, None)
                save_receipts(receipts)
                self.denetle("sil", dosya=filename, kayit=receipt.get("_yukleme_id"),
                             fis_no=receipt.get("fis_no"))

            self.send_json({"ok": True})
        except IslemCakismasi as exc:
            self.send_json({"error": str(exc)}, HTTPStatus.CONFLICT)
        except LookupError as exc:
            self.send_json({"error": str(exc)}, HTTPStatus.NOT_FOUND)
        except (ValueError, json.JSONDecodeError, OSError) as exc:
            self.send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)

    def handle_export_excel(self) -> None:
        """Arayüzden seçilen dosya/sayfa/başlangıç satırına aktarır."""
        try:
            payload = self.read_json()
            dosya = payload.get("dosya")
            sayfa = payload.get("sayfa")
            baslangic = payload.get("baslangic")
            parti_id = payload.get("parti", "aktif")
            tumu = bool(payload.get("tumu", False))
            if not dosya or not sayfa:
                raise ValueError("Excel dosyası ve sayfa seçilmeli")

            with DATA_LOCK:
                data_file, images_dir, salt_okunur = parti_yonetimi.parti_yollari(parti_id)
                sonuc = excel_aktar.aktar(
                    Path(dosya), sayfa, baslangic, tumu=tumu, data_file=data_file,
                    kullanici=(self.kim() or {}).get("ad"))
                if salt_okunur:
                    parti_yonetimi.arsiv_manifesti_yaz(data_file.parent)
                self.denetle(
                    "excel_aktar_hata" if "hata" in sonuc else "excel_aktar",
                    parti=parti_id, hedef=str(dosya), sayfa=sayfa, tumu=tumu,
                    aktarilan=sonuc.get("aktarilan"), tamamlanan=sonuc.get("tamamlanan"),
                    dosyalar=[d.get("dosya") for d in sonuc.get("detay", [])],
                    kayitlar=[d.get("kayit") for d in sonuc.get("detay", [])],
                    satirlar=[d.get("satir") for d in sonuc.get("detay", [])],
                    hata=sonuc.get("hata"),
                )
            if "hata" in sonuc:
                self.send_json({"error": sonuc["hata"]}, HTTPStatus.BAD_REQUEST)
                return
            self.send_json({"ok": True, **sonuc})
        except (ValueError, json.JSONDecodeError) as exc:
            self.send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
        except Exception as exc:
            self.send_json({"error": str(exc)}, HTTPStatus.INTERNAL_SERVER_ERROR)

    def handle_excel_upload(self) -> None:
        """Tarayıcıdan .xlsx yüklenince output/ klasörüne kaydeder."""
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length <= 0:
                raise ValueError("Boş yükleme")
            if length > MAX_UPLOAD_BYTES:
                raise ValueError("Dosya çok büyük (30MB sınırı)")
            ad = unquote(self.headers.get("X-Dosya-Adi", "defter.xlsx"))
            ad = Path(ad).name
            if Path(ad).suffix.lower() not in excel_aktar.EXCEL_UZANTILARI:
                raise ValueError("Sadece .xlsx/.xlsm dosyası yüklenebilir")

            hedef_klasor = ROOT / "output"
            hedef_klasor.mkdir(exist_ok=True)
            hedef = hedef_klasor / ad
            if hedef.exists():
                hedef = benzersiz_hedef(hedef_klasor, Path(ad).stem, Path(ad).suffix.lower())
            hedef.write_bytes(self.rfile.read(length))
            self.denetle("excel_yukle", hedef=str(hedef))

            self.send_json({"ok": True, "yol": str(hedef), "ad": hedef.name})
        except ValueError as exc:
            self.send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
        except OSError as exc:
            self.send_json({"error": str(exc)}, HTTPStatus.INTERNAL_SERVER_ERROR)

    def handle_excel_learn(self) -> None:
        """Seçilen Excel sayfasını OCR düzeltme önerileri için referans yapar."""
        try:
            payload = self.read_json()
            dosya = payload.get("dosya")
            sayfa = payload.get("sayfa")
            if not isinstance(dosya, str) or not isinstance(sayfa, str):
                raise ValueError("Excel dosyası ve sayfa seçilmeli")
            yol = Path(dosya)
            if not excel_aktar.yol_guvenli_mi(yol):
                raise ValueError("Bu dosyaya erişim izni yok")
            sonuc = excel_ogrenme.referans_olustur(yol, sayfa)
            self.denetle("excel_ogren", hedef=str(yol), sayfa=sayfa,
                         satir_sayisi=sonuc.get("satir_sayisi"))
            self.send_json({
                "ok": True,
                "durum": excel_ogrenme.durum(),
                "mesaj": (
                    f"{sonuc['satir_sayisi']} satır öğrenildi · "
                    f"toplam {sonuc['toplam_kaynak']} sayfa"
                ),
            })
        except (ValueError, json.JSONDecodeError, OSError) as exc:
            self.send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
        except Exception as exc:
            self.send_json({"error": str(exc)}, HTTPStatus.INTERNAL_SERVER_ERROR)


def main() -> None:
    import argparse

    global DATA_FILE, FISLER_DIR, GELEN_DIR, PARTI_KURTARMA

    parser = argparse.ArgumentParser(description="Fiş OCR kontrol arayüzü")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument(
        "--host",
        default="0.0.0.0",
        help="Dinlenecek adres. Varsayılan 0.0.0.0 (aynı WiFi'deki telefondan "
             "erişilebilir). Sadece bu bilgisayardan erişim için 127.0.0.1 kullan.",
    )
    parser.add_argument(
        "--data-file",
        type=Path,
        default=DATA_FILE,
        help="Kullanılacak OCR sonuç dosyası (varsayılan: sonuclar.json)",
    )
    parser.add_argument(
        "--fisler-dir",
        type=Path,
        default=FISLER_DIR,
        help="Yüklenen görsellerin kaydedileceği klasör (varsayılan: fisler)",
    )
    parser.add_argument(
        "--gelen-dir",
        type=Path,
        default=GELEN_DIR,
        help="Toplu ekleme için izlenen gelen kutusu klasörü (varsayılan: gelen_fisler)",
    )
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()
    DATA_FILE = args.data_file.resolve()
    FISLER_DIR = args.fisler_dir.resolve()
    GELEN_DIR = args.gelen_dir.resolve()
    FISLER_DIR.mkdir(exist_ok=True)
    GELEN_DIR.mkdir(parents=True, exist_ok=True)
    parti_yonetimi.yollari_ayarla(ROOT, DATA_FILE, FISLER_DIR)
    # Parti kapatma yarıda kesildiyse görseller/kayıtlar taşınmadan önce uzlaştır.
    erisim.yolu_ayarla(ROOT)
    denetim.yolu_ayarla(ROOT)
    kurtarma = PARTI_KURTARMA = parti_yonetimi.yarim_parti_islemini_kurtar()
    if kurtarma and kurtarma["durum"] == "cakisma":
        print(f"UYARI: {kurtarma['mesaj']}:")
        for yol in kurtarma["cakisan"]:
            print(f"  {yol}")
    elif kurtarma:
        print(f"Yarım kalan parti kapatma uzlaştırıldı ({kurtarma['durum']}).")
    hash_indeksini_hazirla()
    try:
        ogrenilen_eslesme = excel_ogrenme.gecmis_duzeltmelerden_eslesme_ogren(
            load_receipts()
        )
        if ogrenilen_eslesme:
            print(f"Geçmiş düzeltmelerden {ogrenilen_eslesme} OCR eşleşmesi öğrenildi.")
    except (OSError, ValueError) as exc:
        print(f"Geçmiş düzeltmeler öğrenilemedi: {exc}")

    if not UI_DIR.exists():
        raise SystemExit(f"Arayüz klasörü bulunamadı: {UI_DIR}")

    os.chdir(UI_DIR)
    local_address = f"http://127.0.0.1:{args.port}"
    server = ThreadingHTTPServer((args.host, args.port), ReceiptHandler)
    threading.Thread(target=ocr_worker, args=(OCR_QUEUE,), name="ocr-worker", daemon=True).start()

    # Sunucu kapanıp açılmışsa tamamlanmamış OCR kayıtlarına kaldığı yerden devam et.
    for is_ in yarim_ocr_kayitlarini_kurtar():
        OCR_QUEUE.put(is_)

    # Gelen kutusu, yarım kalan OCR kayıtları kuyruğa girdikten sonra izlenir.
    threading.Thread(target=gelen_izleyici, name="gelen-izleyici", daemon=True).start()

    print(f"Fiş kontrol arayüzü (bu bilgisayardan): {local_address}")
    print(f"Toplu ekleme için fotoğrafları şu klasöre at: {GELEN_DIR}")
    if args.host != "127.0.0.1":
        print("Telefondan fotoğraf çekmek için (aynı WiFi'de) bu bağlantıyı aç:")
        print(f"  http://{lan_ip()}:{args.port}/cek?anahtar={erisim.yukleme_anahtari()}")
        if not erisim.kullanici_var_mi():
            print("(Tek kullanıcı modu: ağdan yalnızca bu bağlantıyla fotoğraf yüklenebilir.")
            print(" Başka bilgisayarlardan kontrol için: python -m mazot.erisim ekle <ad> kontrol)")
        print("(Telefon ve bilgisayar aynı WiFi ağında olmalı; Windows Güvenlik")
        print(" Duvarı bu portu ilk seferde sorarsa 'İzin ver' demen gerekebilir.)")
    print("Kapatmak için Ctrl+C")
    if not args.no_browser:
        threading.Timer(0.5, lambda: webbrowser.open(local_address)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nArayüz kapatıldı.")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
