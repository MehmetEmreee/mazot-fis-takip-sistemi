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

sys.path.insert(0, str(Path(__file__).resolve().parent))
from extract_json import resmi_oku  # noqa: E402
from dogrulama import kaydi_dogrula  # noqa: E402
from normalizasyon import fis_normalize_et  # noqa: E402
import excel_aktar  # noqa: E402
import excel_ogrenme  # noqa: E402
import alan_yeniden_okuma  # noqa: E402
import parti_yonetimi  # noqa: E402

try:  # iPhone HEIC fotoğraflarını Pillow ile açabilmek için (pillow-heif)
    from pillow_heif import register_heif_opener

    register_heif_opener()
except ImportError:
    pass

ROOT = Path(__file__).resolve().parent
UI_DIR = ROOT / "ui"
DATA_FILE = ROOT / "sonuclar.json"
FISLER_DIR = ROOT / "fisler"
# Toplu ekleme "gelen kutusu": buraya atılan/senkronlanan fotoğraflar
# otomatik olarak OCR kuyruğuna alınır (uygulama.py paketli exe için değiştirir).
GELEN_DIR = ROOT / "gelen_fisler"
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
                       kaynak: str = "telefon") -> dict:
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
    dest = temp_dir() / filename
    image_hash = hashlib.sha256(data).hexdigest()
    with DATA_LOCK:
        onceki_dosya = IMAGE_HASHES.get(image_hash)
        if onceki_dosya:
            return {"ok": False, "duplicate": True, "existing_filename": onceki_dosya}
        # Eşzamanlı özdeş isteklerin ikisinin de kabul edilmesini önle.
        IMAGE_HASHES[image_hash] = filename
    try:
        dest.write_bytes(data)
    except OSError:
        with DATA_LOCK:
            if IMAGE_HASHES.get(image_hash) == filename:
                IMAGE_HASHES.pop(image_hash, None)
        raise

    kayit = {
        "_dosya": filename,
        "_isleniyor": True,
        "_yukleme_id": upload_id,
        "_dosya_hash": image_hash,
        "_yukleme_zamani": datetime.now().isoformat(timespec="seconds"),
        "_kaynak": kaynak,
        "_kontrol_gerekli": True,
        "_kontrol_nedeni": "OCR kuyruğunda",
    }
    if orijinal_ad:
        kayit["_orijinal_ad"] = orijinal_ad
    record = fis_normalize_et(kayit)
    try:
        with DATA_LOCK:
            receipts = load_receipts()
            receipts.append(record)
            save_receipts(receipts)
    except (OSError, ValueError):
        # Kayıt yazılamadıysa yarım kalmış dosyayı ve hash'i geri al.
        with DATA_LOCK:
            if IMAGE_HASHES.get(image_hash) == filename:
                IMAGE_HASHES.pop(image_hash, None)
        dest.unlink(missing_ok=True)
        raise

    OCR_QUEUE.put((dest, image_hash))
    return {
        "ok": True,
        "filename": filename,
        "receipt": record,
        "queue_size": OCR_QUEUE.qsize(),
    }


def ocr_worker() -> None:
    """Yüklenen görselleri arka planda, GPU'yu yormadan tek tek işler."""
    while True:
        image_path, image_hash = OCR_QUEUE.get()
        original_name = image_path.name
        try:
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
                ham_kayit = copy.deepcopy(record)
                excel_ogrenme.guvenli_otomatik_duzelt(record)
                # Geçmişte karşılığı olmayan alanı kırpıp alan adıyla yeniden okut.
                alan_yeniden_okuma.yeniden_oku_ve_duzelt(record, ham_kayit, image_path)
                kaydi_dogrula(fis_normalize_et(record))

            # OCR başarılıysa fiş no ile adlandırma kuralını koru (hâlâ temp/'te).
            if not record.get("HATA"):
                fis_no = guvenli_fis_no(record.get("fis_no"))
                if fis_no:
                    try:
                        with DATA_LOCK:
                            mevcut_kayitlar = load_receipts()
                        yeni_hedef = benzersiz_kayit_adi(
                            temp_dir(), fis_no, image_path.suffix.lower(),
                            [k for k in mevcut_kayitlar
                             if k.get("_dosya") != original_name],
                        )
                        image_path.rename(yeni_hedef)
                        image_path = yeni_hedef
                        record["_dosya"] = image_path.name
                    except OSError:
                        record["_dosya"] = original_name

            record["_isleniyor"] = False
            record["_dosya_hash"] = image_hash
            with DATA_LOCK:
                receipts = load_receipts()
                index = next(
                    (i for i, item in enumerate(receipts)
                     if item.get("_dosya") == original_name),
                    None,
                )
                if index is None:
                    receipts.append(record)
                    index = len(receipts) - 1
                else:
                    onceki = receipts[index]
                    record["_yukleme_zamani"] = onceki.get("_yukleme_zamani")
                    record["_yukleme_id"] = onceki.get("_yukleme_id")
                    for anahtar in ("_orijinal_ad", "_kaynak"):
                        if onceki.get(anahtar):
                            record[anahtar] = onceki[anahtar]
                    receipts[index] = record

                IMAGE_HASHES[image_hash] = record.get("_dosya", original_name)
                fis_no = str(record.get("fis_no") or "").strip()
                if re.fullmatch(r"\d{6}", fis_no):
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
        finally:
            OCR_QUEUE.task_done()


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
    for yol in gelen_dosyalarini_listele():
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


class ReceiptHandler(SimpleHTTPRequestHandler):
    server_version = "FisKontrol/1.0"

    def log_message(self, format: str, *args) -> None:
        print(f"[{self.log_date_time_string()}] {format % args}")

    def send_json(self, payload, status=HTTPStatus.OK) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def read_json(self) -> dict:
        length = int(self.headers.get("Content-Length", "0"))
        return json.loads(self.rfile.read(length).decode("utf-8"))

    def do_GET(self) -> None:
        parsed = urlparse(self.path)

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
                excel_aktar.DATA_FILE = data_file
                self.send_json({
                    "dosyalar": excel_aktar.excel_dosyalarini_bul(),
                    "son_ayar": excel_aktar.son_ayar(),
                    "bekleyen": len(excel_aktar.bekleyen_kayitlar()),
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
                excel_aktar.DATA_FILE = data_file
                tumu = query.get("tumu", ["0"])[0] in ("1", "true", "True")
                onizleme = excel_aktar.sayfa_onizleme(yol, sayfa)
                onizleme["bekleyen"] = len(excel_aktar.bekleyen_kayitlar(tumu=tumu))
                onizleme["aktarilmis"] = len(
                    [k for k in excel_aktar.bekleyen_kayitlar(tumu=True)
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
        if path == "/api/partiler/yeni":
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

                receipt["_onaylandi"] = approved
                receipt["_duzeltildi"] = bool(receipt.get("_duzeltildi")) or changed
                receipt["_kontrol_zamani"] = datetime.now().isoformat(timespec="seconds")
                if approved:
                    firma_klasorune_tasi(receipt)
                save_receipts(receipts)
                learned_count = excel_ogrenme.duzeltmelerden_ogren(
                    receipt, changed_fields, onceki_degerler
                )

            receipt["_excel_onerileri"] = excel_ogrenme.kayit_onerileri(receipt)
            self.send_json({
                "ok": True, "receipt": receipt, "ogrenilen": learned_count,
            })
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
            sonuc = fotografi_kabul_et(data, ext, orijinal_ad, kaynak=kaynak)
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

                mevcut = find_image(filename)
                if mevcut:
                    hedef = silinenler_dir() / mevcut.name
                    if hedef.exists():
                        hedef = benzersiz_hedef(silinenler_dir(), mevcut.stem, mevcut.suffix.lower())
                    mevcut.rename(hedef)

                receipt["_silindi"] = True
                receipt["_silinme_zamani"] = datetime.now().isoformat(timespec="seconds")
                image_hash = receipt.get("_dosya_hash")
                if image_hash and IMAGE_HASHES.get(image_hash) == filename:
                    IMAGE_HASHES.pop(image_hash, None)
                save_receipts(receipts)

            self.send_json({"ok": True})
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
                excel_aktar.DATA_FILE = data_file
                sonuc = excel_aktar.aktar(Path(dosya), sayfa, baslangic, tumu=tumu)
                if salt_okunur:
                    parti_yonetimi.arsiv_manifesti_yaz(data_file.parent)
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

    global DATA_FILE, FISLER_DIR, GELEN_DIR

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
    threading.Thread(target=ocr_worker, name="ocr-worker", daemon=True).start()

    # Sunucu kapanıp açılmışsa tamamlanmamış OCR kayıtlarına kaldığı yerden devam et.
    with DATA_LOCK:
        for receipt in load_receipts():
            if receipt.get("_isleniyor"):
                image_path = find_image(receipt.get("_dosya", ""))
                if image_path:
                    image_hash = receipt.get("_dosya_hash") or dosya_hashi(image_path)
                    OCR_QUEUE.put((image_path, image_hash))

    # Gelen kutusu, yarım kalan OCR kayıtları kuyruğa girdikten sonra izlenir.
    threading.Thread(target=gelen_izleyici, name="gelen-izleyici", daemon=True).start()

    print(f"Fiş kontrol arayüzü (bu bilgisayardan): {local_address}")
    print(f"Toplu ekleme için fotoğrafları şu klasöre at: {GELEN_DIR}")
    if args.host != "127.0.0.1":
        print(f"Telefondan fotoğraf çekmek için (aynı WiFi'de): http://{lan_ip()}:{args.port}/cek")
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
