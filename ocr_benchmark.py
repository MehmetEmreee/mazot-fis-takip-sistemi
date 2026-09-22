"""Onaylı fişleri gerçek etiket kabul ederek OCR sürümlerini karşılaştırır."""

from __future__ import annotations

import argparse
import json
import random
import time
from pathlib import Path

from excel_ogrenme import arama_anahtari
from extract_json import IMG_EXT, resmi_oku


ROOT = Path(__file__).resolve().parent
FIELDS = ("fis_no", "tarih", "firma", "plaka", "ad_soyad", "km", "litre")


def image_index(images_dir: Path) -> dict[str, Path]:
    return {
        yol.name: yol for yol in images_dir.rglob("*")
        if yol.is_file() and yol.suffix.lower() in IMG_EXT
    }


def select_sample(limit: int, data_file: Path) -> list[dict]:
    rows = json.loads(data_file.read_text(encoding="utf-8"))
    rows = [
        r for r in rows if r.get("_onaylandi") and r.get("_ham")
        and not r.get("_silindi")
    ]
    equipment = [r for r in rows if r.get("_ekipman_durumu")]
    vehicles = [r for r in rows if not r.get("_ekipman_durumu")]
    rng = random.Random(20260910)
    rng.shuffle(equipment)
    rng.shuffle(vehicles)
    equipment_count = min(len(equipment), max(1, limit // 4))
    sample = equipment[:equipment_count] + vehicles[: limit - equipment_count]
    rng.shuffle(sample)
    return sample


def same(left, right) -> bool:
    return arama_anahtari(left) == arama_anahtari(right)


def run(
    model: str, max_edge: int, limit: int,
    data_file: Path | None = None, images_dir: Path | None = None,
) -> dict:
    data_file = data_file or ROOT / "sonuclar.json"
    images_dir = images_dir or ROOT / "fisler"
    images = image_index(images_dir)
    sample = select_sample(limit, data_file)
    results = []
    started = time.perf_counter()
    for number, truth in enumerate(sample, 1):
        path = images.get(truth.get("_dosya"))
        if not path:
            continue
        print(f"[{number}/{len(sample)}] {path.name} · {model} · {max_edge}px", flush=True)
        prediction = resmi_oku(path, model=model, max_kenar=max_edge)
        fields = {
            field: {
                "ok": same(prediction.get(field), truth.get(field)),
                "beklenen": truth.get(field),
                "okunan": prediction.get(field),
            }
            for field in FIELDS
        }
        results.append({
            "dosya": path.name,
            "sure_sn": prediction.get("_sure_sn"),
            "alanlar": fields,
        })
    total_fields = len(results) * len(FIELDS)
    correct = sum(
        int(detail["ok"]) for result in results for detail in result["alanlar"].values()
    )
    return {
        "model": model,
        "max_kenar": max_edge,
        "ornek": len(results),
        "alan_dogrulugu": round(correct / total_fields * 100, 1) if total_fields else 0,
        "ortalama_sure_sn": round(
            sum(float(r["sure_sn"] or 0) for r in results) / len(results), 2
        ) if results else 0,
        "toplam_sure_sn": round(time.perf_counter() - started, 1),
        "sonuclar": results,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", required=True)
    parser.add_argument("--max-kenar", type=int, default=1600)
    parser.add_argument("--ornek", type=int, default=8)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--data-file", type=Path, default=ROOT / "sonuclar.json")
    parser.add_argument("--images-dir", type=Path, default=ROOT / "fisler")
    args = parser.parse_args()
    report = run(
        args.model, args.max_kenar, args.ornek,
        data_file=args.data_file, images_dir=args.images_dir,
    )
    output = args.output or ROOT / "benchmark_sonucu.json"
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in report.items() if k != "sonuclar"}, ensure_ascii=False, indent=2))
    print(f"Rapor: {output}")


if __name__ == "__main__":
    main()
