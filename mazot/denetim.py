"""Kim, ne zaman, neyi değiştirdi/onayladı/aktardı: yalnız sona eklenen kayıt.

Her olay data/denetim/<yıl-ay>.jsonl dosyasına bir satır olarak eklenir ve
diske zorlanır (fsync). Satırlar düzeltilmez/silinmez; aylık dosyalar
okunabilir boyutta kalır. Fiş kaydının kendisinde de son düzenleyen, onaylayan
ve aktaran alanları tutulur; ayrıntılı geçmiş buradadır.
"""

from __future__ import annotations

import json
import os
import threading
from datetime import datetime
from pathlib import Path

from . import yollar

DENETIM_DIR = yollar.VERI_DIZINI / "denetim"
_KILIT = threading.Lock()


def yolu_ayarla(kok: Path) -> None:
    global DENETIM_DIR
    DENETIM_DIR = Path(kok) / "denetim"


def kaydet(islem: str, kim: dict | None, **ayrinti) -> dict:
    """Olayı ekler. Yazılamazsa OSError fırlatır: izlenemeyen değişiklik
    sessizce kabul edilmesin diye çağıran işlemi durdurmalıdır."""
    zaman = datetime.now()
    olay = {
        "zaman": zaman.isoformat(timespec="seconds"),
        "islem": islem,
        "kullanici": (kim or {}).get("ad", "bilinmiyor"),
        "rol": (kim or {}).get("rol"),
        **{k: v for k, v in ayrinti.items() if v is not None},
    }
    satir = json.dumps(olay, ensure_ascii=False, default=str) + "\n"
    with _KILIT:
        DENETIM_DIR.mkdir(parents=True, exist_ok=True)
        with (DENETIM_DIR / f"{zaman:%Y-%m}.jsonl").open("a", encoding="utf-8") as dosya:
            dosya.write(satir)
            dosya.flush()
            os.fsync(dosya.fileno())
    return olay


def oku(aranan: str | None = None, islem: str | None = None,
        limit: int = 200) -> list[dict]:
    """Son olaylar (yeniden eskiye). aranan bir dosya adı ya da yükleme
    kimliğiyse yalnız o fişin geçmişi (OCR sonrası ad değişse de bulunur)."""
    if not DENETIM_DIR.is_dir():
        return []
    sonuc: list[dict] = []
    for yol in sorted(DENETIM_DIR.glob("*.jsonl"), reverse=True):
        try:
            satirlar = yol.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        for satir in reversed(satirlar):
            try:
                olay = json.loads(satir)
            except json.JSONDecodeError:
                continue
            if islem and olay.get("islem") != islem:
                continue
            if aranan and aranan not in (
                    olay.get("dosya"), olay.get("kayit"), olay.get("onceki_dosya"),
                    *(olay.get("dosyalar") or []), *(olay.get("kayitlar") or [])):
                continue
            sonuc.append(olay)
            if len(sonuc) >= limit:
                return sonuc
    return sonuc
