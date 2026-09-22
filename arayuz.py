r"""Fiş görsellerini OCR sonuçlarıyla yan yana kontrol etmek için yerel arayüz.

Kullanım:
    .\.venv\Scripts\python.exe arayuz.py

Arayüz varsayılan olarak http://127.0.0.1:8765 adresinde açılır.
"""

from __future__ import annotations

import hashlib
import json
import mimetypes
import os
import queue
import re
import socket
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
import parti_yonetimi  # noqa: E402

ROOT = Path(__file__).resolve().parent
UI_DIR = ROOT / "ui"
DATA_FILE = ROOT / "sonuclar.json"
FISLER_DIR = ROOT / "fisler"
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}
EDITABLE_FIELDS = ("fis_no", "tarih", "firma", "plaka", "ad_soyad", "km", "litre")
MAX_UPLOAD_BYTES = 30 * 1024 * 1024  # 30MB güvenlik sınırı
DATA_LOCK = threading.Lock()
OCR_QUEUE: queue.Queue[tuple[Path, str]] = queue.Queue()
IMAGE_HASHES: dict[str, str] = {}


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
                excel_ogrenme.guvenli_otomatik_duzelt(record)
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
                    record["_yukleme_zamani"] = receipts[index].get("_yukleme_zamani")
                    record["_yukleme_id"] = receipts[index].get("_yukleme_id")
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

            content_type = self.headers.get("Content-Type", "image/jpeg").split(";")[0].strip()
            ext = mimetypes.guess_extension(content_type) or ".jpg"
            if ext in (".jpe", ".jfif"):
                ext = ".jpg"

            ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:-3]
            upload_id = uuid.uuid4().hex
            filename = f"fis_{ts}_{upload_id[:6]}{ext}"
            dest = temp_dir() / filename
            image_hash = hashlib.sha256(data).hexdigest()
            with DATA_LOCK:
                onceki_dosya = IMAGE_HASHES.get(image_hash)
                if onceki_dosya:
                    self.send_json(
                        {
                            "error": "Bu fotoğraf daha önce yüklendi",
                            "duplicate": True,
                            "existing_filename": onceki_dosya,
                        },
                        HTTPStatus.CONFLICT,
                    )
                    return
                # Eşzamanlı özdeş isteklerin ikisinin de kabul edilmesini önle.
                IMAGE_HASHES[image_hash] = filename
            try:
                dest.write_bytes(data)
            except OSError:
                with DATA_LOCK:
                    if IMAGE_HASHES.get(image_hash) == filename:
                        IMAGE_HASHES.pop(image_hash, None)
                raise
        except ValueError as exc:
            self.send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
            return
        except OSError as exc:
            self.send_json({"error": str(exc)}, HTTPStatus.INTERNAL_SERVER_ERROR)
            return

        record = fis_normalize_et({
            "_dosya": filename,
            "_isleniyor": True,
            "_yukleme_id": upload_id,
            "_dosya_hash": image_hash,
            "_yukleme_zamani": datetime.now().isoformat(timespec="seconds"),
            "_kontrol_gerekli": True,
            "_kontrol_nedeni": "OCR kuyruğunda",
        })
        with DATA_LOCK:
            receipts = load_receipts()
            receipts.append(record)
            save_receipts(receipts)

        OCR_QUEUE.put((dest, image_hash))
        self.send_json(
            {
                "ok": True,
                "filename": filename,
                "receipt": record,
                "queue_size": OCR_QUEUE.qsize(),
            },
            HTTPStatus.ACCEPTED,
        )

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

    global DATA_FILE, FISLER_DIR

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
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()
    DATA_FILE = args.data_file.resolve()
    FISLER_DIR = args.fisler_dir.resolve()
    FISLER_DIR.mkdir(exist_ok=True)
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

    print(f"Fiş kontrol arayüzü (bu bilgisayardan): {local_address}")
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
