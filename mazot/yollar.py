"""Uygulamanın kod, arayüz ve veri yolları tek yerden.

- UYGULAMA_KOKU: depo kökü (kaynaktan) veya EXE'nin bulunduğu klasör.
- VERI_DIZINI:   değişen bütün veriler (fişler, kayıtlar, arşiv, yedekler,
                 öğrenme dosyaları). Varsayılan <kök>/data; MAZOT_VERI_DIZINI
                 ortam değişkeniyle değiştirilebilir.
- UI_DIZINI:     paketlenmiş HTML/CSS/JS.

Eski kurulumlarda veri doğrudan kökteydi; eski_veriyi_tasi() bunları bir kez
data/ altına taşır.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
from datetime import datetime
from pathlib import Path


def uygulama_koku() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def ui_dizini() -> Path:
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS")) / "mazot" / "ui"
    return Path(__file__).resolve().parent / "ui"


UYGULAMA_KOKU = uygulama_koku()
VERI_DIZINI = Path(os.environ.get("MAZOT_VERI_DIZINI") or UYGULAMA_KOKU / "data").resolve()
UI_DIZINI = ui_dizini()

# Eski (kökte veri tutan) kurulumlardan taşınacak öğeler.
ESKI_VERI_OGELERI = (
    "sonuclar.json", "aktif_parti.json", "excel_referans.json",
    "kullanici_ogrenmeleri.json", "fisler", "arsiv", "gelen_fisler",
    "output", "yedekler", "fisler.xlsx", "crops", "benchmark_*.json",
)
TASIMA_KAYDI = "tasima_kaydi.json"


class TasimaHatasi(RuntimeError):
    pass


def eski_veriyi_tasi(kok: Path = UYGULAMA_KOKU, veri: Path = VERI_DIZINI,
                     ogeler=ESKI_VERI_OGELERI) -> list[str]:
    """Kökteki eski veriyi veri dizinine taşır; taşınan öğe adlarını döndürür.

    Hedefte aynı adlı öğe varsa kaynağa dokunulmaz (çakışma kayda yazılır).
    Bir öğe taşınamazsa (ör. dosya açık) o ana kadar taşınanlar geri alınır
    ve TasimaHatasi fırlatılır: veri hiçbir zaman iki yere bölünmüş kalmaz.
    """
    kok, veri = Path(kok).resolve(), Path(veri).resolve()
    if kok == veri:
        return []
    adaylar = []
    for ad in ogeler:  # joker içeren öğeler (benchmark_*.json) tek tek taşınır
        adaylar += sorted(p.name for p in kok.glob(ad)) if "*" in ad else (
            [ad] if (kok / ad).exists() else [])
    if not adaylar:
        return []
    veri.mkdir(parents=True, exist_ok=True)
    tasinan, cakisan = [], []
    for ad in adaylar:
        if (veri / ad).exists():
            cakisan.append(ad)
            continue
        try:
            shutil.move(str(kok / ad), str(veri / ad))
            tasinan.append(ad)
        except OSError as exc:
            for geri in reversed(tasinan):
                try:
                    shutil.move(str(veri / geri), str(kok / geri))
                except OSError:
                    pass
            raise TasimaHatasi(
                f"'{ad}' veri klasörüne taşınamadı ({exc}). Açık program veya "
                "dosya varsa kapatıp yeniden başlatın; hiçbir veri taşınmadı."
            ) from exc
    kayit_yolu = veri / TASIMA_KAYDI
    try:
        gecmis = json.loads(kayit_yolu.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        gecmis = []
    gecmis.append({"zaman": datetime.now().isoformat(timespec="seconds"),
                   "kaynak": str(kok), "hedef": str(veri),
                   "tasinan": tasinan, "cakisan": cakisan})
    kayit_yolu.write_text(json.dumps(gecmis, ensure_ascii=False, indent=2), encoding="utf-8")
    return tasinan
