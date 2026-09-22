r"""Ekipman fişlerinde plaka/ad_soyad alanlarını yeni kurala göre yeniden eşler.

Yalnız HENÜZ ONAYLANMAMIŞ ve elle düzeltilmemiş kayıtlara dokunur; onaylanan
işin üzerine asla yazmaz. Çalışmadan önce sonuclar.json'un yedeğini alır.

Kullanım (program kapalıyken):
    .venv\Scripts\python.exe ekipman_yeniden_esle.py
"""

from __future__ import annotations

import json
import shutil
import socket
import sys
from datetime import datetime
from pathlib import Path

import excel_ogrenme
import extract_json
from dogrulama import kaydi_dogrula
from normalizasyon import fis_normalize_et

ROOT = Path(__file__).resolve().parent
DATA_FILE = ROOT / "sonuclar.json"
YEDEK_DIR = ROOT / "yedekler" / "parti_kurtarma"


def program_calisiyor_mu(port: int = 8765) -> bool:
    with socket.socket() as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", port)) == 0


def main() -> int:
    if program_calisiyor_mu():
        print("Program açık. Önce Mazot Fiş Kontrol'ü kapat, sonra tekrar çalıştır.")
        return 1
    if not DATA_FILE.exists():
        print("sonuclar.json bulunamadı.")
        return 1

    kayitlar = json.loads(DATA_FILE.read_text(encoding="utf-8"))
    YEDEK_DIR.mkdir(parents=True, exist_ok=True)
    yedek = YEDEK_DIR / f"sonuclar_ekipman_oncesi_{datetime.now():%Y%m%d_%H%M%S}.json"
    shutil.copy2(DATA_FILE, yedek)

    degisen = 0
    for kayit in kayitlar:
        ham = kayit.get("_ham")
        if not isinstance(ham, dict):
            continue
        if kayit.get("_onaylandi") or kayit.get("_duzeltildi") or kayit.get("_isleniyor"):
            continue
        yeni = extract_json.esle(ham)
        # Yalnız ekipman fişlerine dokun; normal araç fişlerini olduğu gibi bırak.
        if not (yeni["_ekipman_durumu"] or kayit.get("_ekipman_durumu")):
            continue
        eski_plaka, eski_ad = kayit.get("plaka"), kayit.get("ad_soyad")
        kayit["plaka"] = yeni["plaka"]
        kayit["ad_soyad"] = yeni["ad_soyad"]
        kayit["_ekipman_durumu"] = yeni["_ekipman_durumu"]
        fis_normalize_et(kayit)
        # Ham değerden yeniden eşlerken daha önce öğrenilmiş otomatik
        # düzeltmeler kaybolmasın.
        kayit.pop("_otomatik_duzeltmeler", None)
        kayit.pop("_otomatik_duzeltildi", None)
        excel_ogrenme.guvenli_otomatik_duzelt(kayit)
        kaydi_dogrula(fis_normalize_et(kayit))
        if (kayit.get("plaka"), kayit.get("ad_soyad")) != (eski_plaka, eski_ad):
            degisen += 1
            print(f"  {kayit.get('_dosya')}: {eski_plaka} / {eski_ad}"
                  f"  ->  {kayit['plaka']} / {kayit['ad_soyad']}")

    if degisen:
        gecici = DATA_FILE.with_suffix(".json.yeni")
        gecici.write_text(
            json.dumps(kayitlar, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        gecici.replace(DATA_FILE)
    print(f"\nYedek: {yedek.name}")
    print(f"Güncellenen kayıt: {degisen} (onaylanan/düzeltilen kayıtlara dokunulmadı)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
