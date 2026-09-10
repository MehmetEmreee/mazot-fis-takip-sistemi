r"""Fiş görsellerini OCR sonuçlarıyla yan yana kontrol etmek için yerel arayüz.

Kullanım:
    .\.venv\Scripts\python.exe arayuz.py

Arayüz varsayılan olarak http://127.0.0.1:8765 adresinde açılır.
"""

from __future__ import annotations

import json
import mimetypes
import os
import queue
import re
import socket
import sys
import threading
import uuid
import webbrowser
from datetime import datetime
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent))
from extract_json import resmi_oku  # noqa: E402
from normalizasyon import fis_normalize_et  # noqa: E402

ROOT = Path(__file__).resolve().parent
UI_DIR = ROOT / "ui"
DATA_FILE = ROOT / "sonuclar.json"
FISLER_DIR = ROOT / "fisler"
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}
EDITABLE_FIELDS = ("fis_no", "tarih", "firma", "plaka", "ad_soyad", "km", "litre")
MAX_UPLOAD_BYTES = 30 * 1024 * 1024  # 30MB güvenlik sınırı
DATA_LOCK = threading.Lock()
OCR_QUEUE: queue.Queue[Path] = queue.Queue()


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


def load_receipts() -> list[dict]:
    if not DATA_FILE.exists():
        return []
    try:
        data = json.loads(DATA_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        raise ValueError(f"sonuclar.json okunamadı: {exc}") from exc
    if not isinstance(data, list):
        raise ValueError("sonuclar.json bir liste içermeli")
    return [fis_normalize_et(receipt) for receipt in data]


def save_receipts(receipts: list[dict]) -> None:
    receipts = [fis_normalize_et(receipt) for receipt in receipts]
    temporary = DATA_FILE.with_suffix(".json.tmp")
    temporary.write_text(
        json.dumps(receipts, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    os.replace(temporary, DATA_FILE)


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


def ocr_worker() -> None:
    """Yüklenen görselleri arka planda, GPU'yu yormadan tek tek işler."""
    while True:
        image_path = OCR_QUEUE.get()
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

            # OCR başarılıysa Claude'un eklediği fiş-no ile adlandırma kuralını koru.
            if not record.get("HATA"):
                fis_no = guvenli_fis_no(record.get("fis_no"))
                if fis_no:
                    try:
                        yeni_hedef = benzersiz_hedef(
                            FISLER_DIR, fis_no, image_path.suffix.lower()
                        )
                        image_path.rename(yeni_hedef)
                        image_path = yeni_hedef
                        record["_dosya"] = image_path.name
                    except OSError:
                        record["_dosya"] = original_name

            record["_isleniyor"] = False
            with DATA_LOCK:
                receipts = load_receipts()
                index = next(
                    (i for i, item in enumerate(receipts)
                     if item.get("_dosya") == original_name),
                    None,
                )
                if index is None:
                    receipts.append(record)
                else:
                    record["_yukleme_zamani"] = receipts[index].get("_yukleme_zamani")
                    receipts[index] = record
                save_receipts(receipts)
        finally:
            OCR_QUEUE.task_done()


def find_image(filename: str) -> Path | None:
    """Yalnızca proje kökü altındaki, adı tam eşleşen görselleri döndürür."""
    safe_name = Path(filename).name
    if not safe_name or safe_name != filename:
        return None

    direct = ROOT / safe_name
    if direct.is_file() and direct.suffix.lower() in IMAGE_EXTENSIONS:
        return direct

    for path in ROOT.rglob(safe_name):
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

        if parsed.path == "/api/receipts":
            try:
                receipts = load_receipts()
                modified = (
                    datetime.fromtimestamp(DATA_FILE.stat().st_mtime).isoformat()
                    if DATA_FILE.exists()
                    else None
                )
                self.send_json({"receipts": receipts, "modified": modified})
            except ValueError as exc:
                self.send_json({"error": str(exc)}, HTTPStatus.INTERNAL_SERVER_ERROR)
            return

        if parsed.path == "/api/image":
            filename = parse_qs(parsed.query).get("file", [""])[0]
            image_path = find_image(unquote(filename))
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

        if parsed.path == "/":
            self.path = "/index.html"
        elif parsed.path == "/cek":
            self.path = "/cek.html"
        elif parsed.path.startswith("/ui/"):
            self.path = parsed.path.removeprefix("/ui")
        super().do_GET()

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        if path == "/api/save":
            self.handle_save()
        elif path == "/api/upload":
            self.handle_upload()
        else:
            self.send_error(HTTPStatus.NOT_FOUND)

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
                receipt = next((r for r in receipts if r.get("_dosya") == filename), None)
                if receipt is None:
                    raise LookupError(f"Kayıt bulunamadı: {filename}")

                changed = False
                for field in EDITABLE_FIELDS:
                    if field in fields:
                        value = fields[field]
                        value = "-" if value in (None, "") else str(value).strip()
                        if receipt.get(field) != value:
                            changed = True
                        receipt[field] = value

                fis_normalize_et(receipt)

                receipt["_onaylandi"] = approved
                receipt["_duzeltildi"] = bool(receipt.get("_duzeltildi")) or changed
                receipt["_kontrol_zamani"] = datetime.now().isoformat(timespec="seconds")
                save_receipts(receipts)

            self.send_json({"ok": True, "receipt": receipt})
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

            FISLER_DIR.mkdir(exist_ok=True)
            ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:-3]
            filename = f"fis_{ts}_{uuid.uuid4().hex[:6]}{ext}"
            dest = FISLER_DIR / filename
            dest.write_bytes(data)
        except ValueError as exc:
            self.send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
            return
        except OSError as exc:
            self.send_json({"error": str(exc)}, HTTPStatus.INTERNAL_SERVER_ERROR)
            return

        record = fis_normalize_et({
            "_dosya": filename,
            "_isleniyor": True,
            "_yukleme_zamani": datetime.now().isoformat(timespec="seconds"),
            "_kontrol_gerekli": True,
            "_kontrol_nedeni": "OCR kuyruğunda",
        })
        with DATA_LOCK:
            receipts = load_receipts()
            receipts.append(record)
            save_receipts(receipts)

        OCR_QUEUE.put(dest)
        self.send_json(
            {
                "ok": True,
                "filename": filename,
                "receipt": record,
                "queue_size": OCR_QUEUE.qsize(),
            },
            HTTPStatus.ACCEPTED,
        )


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
                    OCR_QUEUE.put(image_path)

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
