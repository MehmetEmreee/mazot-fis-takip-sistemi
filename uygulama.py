"""Son kullanıcı için tek tık Mazot Fiş Kontrol başlatıcısı."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
import webbrowser
import zipfile
from pathlib import Path

import arayuz
import excel_aktar
import excel_ogrenme
import extract_json
import parti_yonetimi


PORT = 8765


def uygulama_koku() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def paket_koku() -> Path:
    return Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))


def yollari_ayarla() -> Path:
    """Paketli EXE'de değişen veriyi geçici PyInstaller klasörüne yazma."""
    root = uygulama_koku()
    arayuz.ROOT = root
    arayuz.UI_DIR = paket_koku() / "ui"
    arayuz.DATA_FILE = root / "sonuclar.json"
    arayuz.FISLER_DIR = root / "fisler"

    excel_aktar.ROOT = root
    excel_aktar.DATA_FILE = arayuz.DATA_FILE
    excel_aktar.AYAR_FILE = root / "output" / ".excel_ayar.json"
    excel_aktar.ARAMA_KLASORLERI = [root / "output", root, Path.home() / "Documents"]

    excel_ogrenme.ROOT = root
    excel_ogrenme.REFERANS_FILE = root / "excel_referans.json"
    excel_ogrenme.KULLANICI_FILE = root / "kullanici_ogrenmeleri.json"
    excel_ogrenme.referans_yukle.cache_clear()
    excel_ogrenme.birlesik_referans_yukle.cache_clear()
    parti_yonetimi.yollari_ayarla(root, arayuz.DATA_FILE, arayuz.FISLER_DIR)
    return root


def gunluk_veri_yedegi(root: Path) -> Path | None:
    """Küçük ama kritik JSON öğrenme/veri dosyalarını günde bir kez yedekler."""
    tarih = time.strftime("%Y-%m-%d")
    klasor = root / "yedekler" / "veri_otomatik"
    klasor.mkdir(parents=True, exist_ok=True)
    mevcut = sorted(klasor.glob(f"veri_{tarih}_*.zip"))
    if mevcut:
        return mevcut[-1]
    kaynaklar = [
        root / "sonuclar.json", root / "excel_referans.json",
        root / "kullanici_ogrenmeleri.json",
        root / "output" / ".excel_ayar.json",
    ]
    kaynaklar = [yol for yol in kaynaklar if yol.is_file()]
    if not kaynaklar:
        return None
    hedef = klasor / f"veri_{time.strftime('%Y-%m-%d_%H-%M-%S')}.zip"
    with zipfile.ZipFile(hedef, "w", compression=zipfile.ZIP_DEFLATED) as paket:
        for yol in kaynaklar:
            paket.write(yol, yol.relative_to(root))
    return hedef


def json_get(url: str, timeout: float = 2.0):
    with urllib.request.urlopen(url, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def ollama_exe() -> Path:
    local = Path(os.environ.get("LOCALAPPDATA", ""))
    return local / "Programs" / "Ollama" / "ollama.exe"


def ollamayi_hazirla() -> tuple[bool, str]:
    """Ollama kapalıysa sessizce başlatır; model eksikse anlaşılır hata verir."""
    tags_url = "http://127.0.0.1:11434/api/tags"
    try:
        tags = json_get(tags_url)
    except Exception:
        exe = ollama_exe()
        if not exe.is_file():
            return False, "Ollama kurulu değil. Önce KURULUM.bat dosyasını çalıştırın."
        bayrak = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        subprocess.Popen(
            [str(exe), "serve"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=bayrak,
        )
        for _ in range(30):
            time.sleep(0.5)
            try:
                tags = json_get(tags_url)
                break
            except Exception:
                continue
        else:
            return False, "OCR motoru başlatılamadı. Bilgisayarı yeniden başlatıp tekrar deneyin."

    modeller = {m.get("name") for m in tags.get("models", [])}
    if extract_json.MODEL not in modeller:
        return False, (
            f"Gerekli OCR modeli ({extract_json.MODEL}) kurulu değil. "
            "Önce KURULUM.bat dosyasını çalıştırın."
        )
    return True, "OCR hazır"


def main() -> None:
    root = yollari_ayarla()
    adres = f"http://127.0.0.1:{PORT}/"
    try:
        json_get(f"http://127.0.0.1:{PORT}/api/receipts")
        webbrowser.open(adres)
        return
    except Exception:
        pass

    print("=" * 54)
    print("  MAZOT FİŞ KONTROL")
    print("=" * 54)
    print("1/3  Veriler güvenceye alınıyor...")
    try:
        yedek = gunluk_veri_yedegi(root)
        if yedek:
            print(f"     Yedek hazır: {yedek.name}")
    except OSError as exc:
        print(f"     Uyarı: otomatik yedek alınamadı ({exc})")

    print("2/3  OCR motoru kontrol ediliyor...")
    tamam, mesaj = ollamayi_hazirla()
    if not tamam:
        print(f"\nHATA: {mesaj}")
        input("Bu pencereyi kapatmak için Enter'a basın...")
        raise SystemExit(1)
    print(f"     {mesaj}")
    print("3/3  Program açılıyor...")
    print("\nBu pencere açık kaldığı sürece program çalışır.")
    print("Kapatmak için pencerenin X düğmesine basabilirsiniz.\n")
    sys.argv = [sys.argv[0], "--host", "0.0.0.0", "--port", str(PORT)]
    arayuz.main()


if __name__ == "__main__":
    main()
