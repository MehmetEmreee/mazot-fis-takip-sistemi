"""Gerçek ayrı-hücre LLM yaklaşımını az sayıda fişte ölçen deney."""

from __future__ import annotations

import base64
import io
import json
import time
from pathlib import Path

import requests
from PIL import Image, ImageOps

from excel_ogrenme import arama_anahtari
from hucre_mozaik import satir_cizgilerini_bul
from ocr_benchmark import image_index, select_sample


ROOT = Path(__file__).resolve().parent
MODEL = "qwen2.5vl:7b"
URL = "http://127.0.0.1:11434/api/chat"
FIELDS = ("fis_no", "tarih", "firma", "plaka", "ad_soyad", "km", "litre")
SCHEMA = {
    "type": "object",
    "properties": {field: {"type": ["string", "null"]} for field in FIELDS},
    "required": list(FIELDS),
    "additionalProperties": False,
}
PROMPT = """Bu görüntü Türkçe mazot fişinin yalnızca bir bölümü veya hücresidir.
Görünen basılı etiketi ve onun yanındaki el yazısı değeri oku. Yalnızca görünen
alanları doldur, görünmeyen bütün alanları null bırak. Sayın=firma,
Araç Plakası=plaka, Adı Soyadı=ad_soyad, K.M.=km, Verilen Litre=litre,
MRK=fis_no. Türkçe harfleri aynen koru. Yalnızca JSON döndür."""


def jpeg_b64(img: Image.Image, max_edge: int = 1100) -> str:
    ratio = max_edge / max(img.size)
    if ratio < 1:
        img = img.resize((round(img.width * ratio), round(img.height * ratio)), Image.LANCZOS)
    buffer = io.BytesIO()
    img.convert("RGB").save(buffer, "JPEG", quality=92)
    return base64.b64encode(buffer.getvalue()).decode()


def bolgeler(path: Path) -> list[Image.Image]:
    img = ImageOps.exif_transpose(Image.open(path)).convert("RGB")
    lines = satir_cizgilerini_bul(img)
    if len(lines) != 6:
        return []
    w, h = img.size
    x0, x1, pad = round(w * .08), round(w * .94), round(h * .012)
    parts = [img.crop((x0, round(h * .04), x1, min(h, lines[0] + pad)))]
    parts.extend(
        img.crop((x0, max(0, top - pad), x1, min(h, bottom + pad)))
        for top, bottom in zip(lines, lines[1:])
    )
    return parts


def oku(img: Image.Image) -> tuple[dict, float]:
    started = time.perf_counter()
    response = requests.post(URL, json={
        "model": MODEL,
        "messages": [{"role": "user", "content": PROMPT, "images": [jpeg_b64(img)]}],
        "format": SCHEMA, "stream": False, "keep_alive": "30m",
        "options": {"temperature": 0, "num_ctx": 8192, "num_predict": 180, "seed": 42},
    }, timeout=300)
    response.raise_for_status()
    raw = response.json()["message"]["content"]
    return json.loads(raw), time.perf_counter() - started


def main() -> None:
    images = image_index()
    # Aynı regresyon örnekleminden plaka/ad-soyad hatasına açık iki fiş.
    truths = {row["_dosya"]: row for row in select_sample(6)}
    chosen = [name for name in ("003216.jpg", "002340.jpg") if name in truths]
    for name in chosen:
        merged = {field: None for field in FIELDS}
        total = 0.0
        parts = bolgeler(images[name])
        print(f"{name}: {len(parts)} bölge", flush=True)
        for number, part in enumerate(parts, 1):
            data, elapsed = oku(part)
            total += elapsed
            for field, value in data.items():
                if field in merged and value not in (None, "", "-"):
                    merged[field] = value
            print(f"  {number}/{len(parts)} {elapsed:.1f} sn {data}", flush=True)
        comparison = {
            field: {
                "ok": arama_anahtari(merged[field]) == arama_anahtari(truths[name].get(field)),
                "okunan": merged[field], "beklenen": truths[name].get(field),
            }
            for field in FIELDS
        }
        print(json.dumps({"dosya": name, "sure": round(total, 1), "alanlar": comparison}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
