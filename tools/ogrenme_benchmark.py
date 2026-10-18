"""Geçmiş öğrenme önerilerinin onaylı fişler üzerindeki isabetini ölçer."""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

from mazot.yollar import VERI_DIZINI

from mazot.excel_ogrenme import arama_anahtari, birlesik_referans_yukle, kayit_onerileri
from mazot.extract_json import esle
from mazot.normalizasyon import fis_normalize_et


ROOT = VERI_DIZINI  # data/: sonuclar.json, fisler/, arsiv/ ...


def main() -> None:
    rows = json.loads((ROOT / "sonuclar.json").read_text(encoding="utf-8"))
    totals = defaultdict(lambda: {"adet": 0, "dogru": 0})
    reference = birlesik_referans_yukle() or {}
    known = {
        field: {arama_anahtari(item.get("deger")) for item in reference.get("alanlar", {}).get(field, [])}
        for field in ("plaka", "ad_soyad")
    }
    cross = {"adet": 0, "dogru": 0}
    for truth in rows:
        if not truth.get("_onaylandi") or not truth.get("_ham") or truth.get("_silindi"):
            continue
        raw = fis_normalize_et(esle(truth["_ham"]))
        plate_key = arama_anahtari(raw.get("plaka"))
        name_key = arama_anahtari(raw.get("ad_soyad"))
        if plate_key in known["ad_soyad"] and name_key in known["plaka"]:
            cross["adet"] += 1
            cross["dogru"] += int(
                name_key == arama_anahtari(truth.get("plaka"))
                and plate_key == arama_anahtari(truth.get("ad_soyad"))
            )
        result = kayit_onerileri(raw) or {}
        for suggestion in result.get("oneriler", []):
            score = int(suggestion["benzerlik"])
            correct = arama_anahtari(suggestion["onerilen"]) == arama_anahtari(
                truth.get(suggestion["alan"])
            )
            frequency = int(suggestion.get("gecmis_adet", 0))
            for threshold in (90, 95, 97):
                for minimum_count in (1, 2, 3, 5):
                    if score >= threshold and frequency >= minimum_count:
                        key = f"%{threshold} / {minimum_count}+ geçmiş"
                        totals[key]["adet"] += 1
                        totals[key]["dogru"] += int(correct)
    report = {
        threshold: {
            **counts,
            "isabet": round(counts["dogru"] / counts["adet"] * 100, 1)
            if counts["adet"] else 0,
        }
        for threshold, counts in sorted(totals.items())
    }
    report["çapraz alan takası"] = {
        **cross,
        "isabet": round(cross["dogru"] / cross["adet"] * 100, 1) if cross["adet"] else 0,
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
