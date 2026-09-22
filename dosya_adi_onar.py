# -*- coding: utf-8 -*-
r"""Aynı dosya adını paylaşan fiş kayıtlarını benzersiz adlara ayırır.

Aynı fiş numarası iki kez yüklendiğinde ikinci fiş de aynı adı alabiliyordu;
bu durumda arayüzden yapılan onay yanlış kayda gidiyordu. Bu betik dosyaları
içerik karmasıyla bulup ikinci, üçüncü... kopyaları '_2', '_3' olarak
adlandırır. Hiçbir fiş silinmez, hiçbir alan değiştirilmez.

Kullanım (program kapalıyken):
    .venv\Scripts\python.exe dosya_adi_onar.py
"""

from __future__ import annotations

import collections
import hashlib
import json
import shutil
import socket
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DATA_FILE = ROOT / "sonuclar.json"
FISLER_DIR = ROOT / "fisler"
YEDEK_DIR = ROOT / "yedekler" / "parti_kurtarma"
GORSEL_UZANTILARI = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}


def program_calisiyor_mu(port: int = 8765) -> bool:
    with socket.socket() as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", port)) == 0


def dosya_hashi(yol: Path) -> str:
    ozet = hashlib.sha256()
    with yol.open("rb") as dosya:
        for parca in iter(lambda: dosya.read(1024 * 1024), b""):
            ozet.update(parca)
    return ozet.hexdigest()


def main() -> int:
    if program_calisiyor_mu():
        print("Program açık. Önce Mazot Fiş Kontrol'ü kapat, sonra tekrar çalıştır.")
        return 1

    kayitlar = json.loads(DATA_FILE.read_text(encoding="utf-8"))
    sayac = collections.Counter(k.get("_dosya") for k in kayitlar if k.get("_dosya"))
    cakisanlar = {ad for ad, adet in sayac.items() if adet > 1}
    if not cakisanlar:
        print("Çakışan dosya adı yok.")
        return 0

    YEDEK_DIR.mkdir(parents=True, exist_ok=True)
    yedek = YEDEK_DIR / f"sonuclar_dosyaadi_oncesi_{datetime.now():%Y%m%d_%H%M%S}.json"
    shutil.copy2(DATA_FILE, yedek)

    # Diskteki görselleri içerik karmasına göre indeksle.
    karma_index: dict[str, list[Path]] = collections.defaultdict(list)
    for yol in FISLER_DIR.rglob("*"):
        if yol.is_file() and yol.suffix.lower() in GORSEL_UZANTILARI:
            karma_index[dosya_hashi(yol)].append(yol)

    kullanilan = {str(k.get("_dosya")) for k in kayitlar if k.get("_dosya")}
    kullanilan.update(
        yol.name for yollar in karma_index.values() for yol in yollar
    )

    duzeltilen = 0
    for ad in sorted(cakisanlar):
        grup = [k for k in kayitlar if k.get("_dosya") == ad]
        for kayit in grup[1:]:
            karma = kayit.get("_dosya_hash")
            adaylar = [y for y in karma_index.get(karma, []) if y.name == ad]
            if not adaylar:
                print(f"  ATLANDI {ad}: görsel bulunamadı (karma {str(karma)[:8]})")
                continue
            kaynak = adaylar[0]
            taban, ext = Path(ad).stem, Path(ad).suffix.lower()
            sayi = 2
            while f"{taban}_{sayi}{ext}" in kullanilan:
                sayi += 1
            yeni_ad = f"{taban}_{sayi}{ext}"
            kaynak.rename(kaynak.with_name(yeni_ad))
            kullanilan.add(yeni_ad)
            kayit["_dosya"] = yeni_ad
            duzeltilen += 1
            print(f"  {ad}  ->  {yeni_ad}   ({kayit.get('firma')} / {kayit.get('plaka')})")

    if duzeltilen:
        gecici = DATA_FILE.with_suffix(".json.yeni")
        gecici.write_text(
            json.dumps(kayitlar, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        gecici.replace(DATA_FILE)
    print(f"\nYedek: {yedek.name}")
    print(f"Benzersiz ada taşınan kayıt: {duzeltilen}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
