"""
El yazısı fişi satır satır kırpar (otomatik):
1) Kağıt bölgesini (ahşap masa arka planından ayırarak) bulur.
2) Kağıt içinde yatay izdüşüm (satır başına koyu piksel sayısı) ile
   mürekkep içeren satır bantlarını tespit eder.
3) Her bandı biraz pay bırakarak ayrı bir PNG olarak kaydeder.

Çıktı: crops/line_01.png, crops/line_02.png, ...
"""

import sys
from pathlib import Path
import numpy as np
from PIL import Image


def kagit_bolgesini_bul(gray: np.ndarray, esik=170):
    # Kağıt (açık renk) vs ahşap masa (koyu) -> satır/sütun bazında
    # "parlak piksel oranı" yüksek olan bölgeyi bul.
    bright = gray > esik
    row_ratio = bright.mean(axis=1)
    col_ratio = bright.mean(axis=0)

    def buyuk_blok(ratio, min_oran=0.5):
        mask = ratio > min_oran
        idx = np.where(mask)[0]
        if len(idx) == 0:
            return 0, len(ratio)
        return idx.min(), idx.max() + 1

    y0, y1 = buyuk_blok(row_ratio)
    x0, x1 = buyuk_blok(col_ratio)
    return x0, y0, x1, y1


def satirlari_bul(gray: np.ndarray, esik=130, min_mürekkep_orani=0.03, min_bosluk=6, min_yukseklik=15):
    # Koyu (mürekkep) piksel oranı satır bazında
    dark_ratio = (gray < esik).mean(axis=1)
    has_ink = dark_ratio > min_mürekkep_orani

    bantlar = []
    start = None
    bosluk_sayaci = 0
    for y, val in enumerate(has_ink):
        if val:
            if start is None:
                start = y
            bosluk_sayaci = 0
        else:
            if start is not None:
                bosluk_sayaci += 1
                if bosluk_sayaci > min_bosluk:
                    end = y - bosluk_sayaci
                    if end - start >= min_yukseklik:
                        bantlar.append((start, end))
                    start = None
                    bosluk_sayaci = 0
    if start is not None:
        end = len(has_ink) - bosluk_sayaci
        if end - start >= min_yukseklik:
            bantlar.append((start, end))
    return bantlar


def main():
    if len(sys.argv) < 2:
        print("Kullanım: python -m experiments.crop_lines <resim_dosyasi> [cikis_klasoru]")
        sys.exit(1)

    resim_yolu = Path(sys.argv[1])
    cikis = Path(sys.argv[2]) if len(sys.argv) > 2 else Path("crops")
    cikis.mkdir(exist_ok=True)

    img = Image.open(resim_yolu).convert("RGB")
    gray = np.array(img.convert("L"))

    x0, y0, x1, y1 = kagit_bolgesini_bul(gray)
    print(f"Kağıt bölgesi: x=[{x0},{x1}] y=[{y0},{y1}] (orijinal {img.size})")

    kagit = img.crop((x0, y0, x1, y1))
    kagit_gray = np.array(kagit.convert("L"))

    bantlar = satirlari_bul(kagit_gray)
    print(f"{len(bantlar)} satır bandı bulundu.\n")

    pay = 6  # piksel, üst/alt pay
    for i, (y0b, y1b) in enumerate(bantlar, start=1):
        yb0 = max(0, y0b - pay)
        yb1 = min(kagit.height, y1b + pay)
        satir = kagit.crop((0, yb0, kagit.width, yb1))
        out_path = cikis / f"line_{i:02d}.png"
        satir.save(out_path)
        print(f"  line_{i:02d}.png  y=[{yb0},{yb1}]  boyut={satir.size}")

    print(f"\nKırpılan satırlar '{cikis}/' klasörüne kaydedildi.")


if __name__ == "__main__":
    main()
