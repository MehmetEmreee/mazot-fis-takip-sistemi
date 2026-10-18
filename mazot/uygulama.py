"""Son kullanıcı için tek tık Mazot Fiş Kontrol başlatıcısı."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
import webbrowser
import zipfile
from pathlib import Path

from . import arayuz
from . import excel_aktar
from . import excel_ogrenme
from . import extract_json
from . import parti_yonetimi
from . import yollar


PORT = 8765


def yollari_ayarla() -> Path:
    """Modüllerin veri yollarını veri dizinine (varsayılan <kök>/data) bağlar.

    Paketli EXE'de de değişen veri EXE'nin yanındaki data/ klasörüne yazılır,
    geçici PyInstaller klasörüne değil.
    """
    root = yollar.VERI_DIZINI
    root.mkdir(parents=True, exist_ok=True)
    arayuz.ROOT = root
    arayuz.UI_DIR = yollar.UI_DIZINI
    arayuz.DATA_FILE = root / "sonuclar.json"
    arayuz.FISLER_DIR = root / "fisler"
    arayuz.GELEN_DIR = root / "gelen_fisler"

    excel_aktar.ROOT = root
    excel_aktar.DATA_FILE = arayuz.DATA_FILE
    excel_aktar.AYAR_FILE = root / "output" / ".excel_ayar.json"
    excel_aktar.ARAMA_KLASORLERI = [
        root / "output", root, yollar.UYGULAMA_KOKU, Path.home() / "Documents"]

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


WINDOWS = sys.platform.startswith("win")
MACOS = sys.platform == "darwin"
MAC_OLLAMA_APP = Path("/Applications/Ollama.app")


def kurulum_dosyasi() -> str:
    """Bu işletim sisteminde kullanıcının çalıştırması gereken kurulum dosyası."""
    if WINDOWS:
        return r"scripts\windows\KURULUM.bat"
    if MACOS:
        return "scripts/macos/KURULUM.command"
    return "scripts/macos/KURULUM.command (veya 'ollama pull qwen2.5vl:7b')"


def ollama_exe() -> Path | None:
    """Ollama komut satırı aracını bulur; yoksa None döner."""
    bulunan = shutil.which("ollama")
    if bulunan:
        return Path(bulunan)
    adaylar: list[Path] = []
    if WINDOWS:
        local = os.environ.get("LOCALAPPDATA")
        if local:
            adaylar.append(Path(local) / "Programs" / "Ollama" / "ollama.exe")
    else:
        adaylar += [
            MAC_OLLAMA_APP / "Contents" / "Resources" / "ollama",
            Path("/usr/local/bin/ollama"),
            Path("/opt/homebrew/bin/ollama"),
        ]
    for aday in adaylar:
        if aday.is_file():
            return aday
    return None


def _ollama_baslat() -> bool:
    """Ollama sunucusunu arka planda başlatır. Başlatma denendiyse True."""
    exe = ollama_exe()
    if exe is not None:
        ayarlar: dict = {"stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL}
        if WINDOWS:
            ayarlar["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        else:
            ayarlar["start_new_session"] = True
        try:
            subprocess.Popen([str(exe), "serve"], **ayarlar)
            return True
        except OSError:
            pass
    if MACOS and MAC_OLLAMA_APP.is_dir():
        try:
            subprocess.Popen(
                ["open", "-g", "-a", "Ollama"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            return True
        except OSError:
            pass
    return False


def ollamayi_hazirla() -> tuple[bool, str]:
    """Ollama kapalıysa sessizce başlatır; model eksikse anlaşılır hata verir."""
    tags_url = "http://127.0.0.1:11434/api/tags"
    try:
        tags = json_get(tags_url)
    except Exception:
        if not _ollama_baslat():
            return False, (
                f"Ollama kurulu değil. Önce {kurulum_dosyasi()} dosyasını çalıştırın."
            )
        for _ in range(40):
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
            f"Önce {kurulum_dosyasi()} dosyasını çalıştırın."
        )
    return True, "OCR hazır"


def main() -> None:
    adres = f"http://127.0.0.1:{PORT}/"
    try:
        json_get(f"http://127.0.0.1:{PORT}/api/receipts")
        webbrowser.open(adres)
        return
    except Exception:
        pass

    # Eski kurulumlarda veri kökteydi: çalışan sürüm yokken bir kez data/'ya taşı.
    try:
        tasinan = yollar.eski_veriyi_tasi()
    except yollar.TasimaHatasi as exc:
        print(f"\nHATA: {exc}")
        input("Bu pencereyi kapatmak için Enter'a basın...")
        raise SystemExit(1)
    if tasinan:
        print(f"Veriler {yollar.VERI_DIZINI} klasörüne taşındı: {', '.join(tasinan)}")
    root = yollari_ayarla()

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
