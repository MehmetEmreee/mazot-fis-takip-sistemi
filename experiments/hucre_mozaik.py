"""Fiş satırlarını tek OCR çağrısı için büyütülmüş bir mozaikte toplar.

Çizgiler güvenle bulunamazsa ``None`` döner; üretim kodu bu durumda tam
fotoğrafa geri dönebilir. Böylece yanlış kırpma sessiz veri kaybı yaratmaz.
"""

from __future__ import annotations

import math
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageOps


def _kumele(degerler: list[tuple[float, float]], tolerans: float) -> list[float]:
    """(konum, ağırlık) çiftlerini yakınlıklarına göre ağırlıklı kümeler."""
    if not degerler:
        return []
    gruplar: list[list[tuple[float, float]]] = []
    for konum, agirlik in sorted(degerler):
        if not gruplar or konum - gruplar[-1][-1][0] > tolerans:
            gruplar.append([])
        gruplar[-1].append((konum, agirlik))
    return [
        sum(k * a for k, a in grup) / sum(a for _, a in grup)
        for grup in gruplar
    ]


def satir_cizgilerini_bul(img: Image.Image) -> list[int]:
    """Tablonun ardışık yatay sınırlarını bulur (en az beş çizgi)."""
    rgb = np.array(ImageOps.exif_transpose(img).convert("RGB"))
    h0, w0 = rgb.shape[:2]
    olcek = min(1.0, 1500 / max(h0, w0))
    if olcek < 1:
        rgb = cv2.resize(rgb, None, fx=olcek, fy=olcek, interpolation=cv2.INTER_AREA)
    h, w = rgb.shape[:2]
    gri = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
    gri = cv2.GaussianBlur(gri, (5, 5), 0)
    kenarlar = cv2.Canny(gri, 45, 130)
    cizgiler = cv2.HoughLinesP(
        kenarlar, 1, np.pi / 360, threshold=max(45, w // 14),
        minLineLength=max(140, int(w * 0.28)), maxLineGap=max(25, int(w * 0.06)),
    )
    adaylar: list[tuple[float, float]] = []
    if cizgiler is not None:
        for x1, y1, x2, y2 in cizgiler.reshape(-1, 4):
            dx, dy = x2 - x1, y2 - y1
            uzunluk = math.hypot(dx, dy)
            if not dx or abs(math.degrees(math.atan2(dy, dx))) > 12:
                continue
            # Çizginin görüntü orta dikeyindeki tahmini y konumu.
            orta_y = y1 + (w / 2 - x1) * dy / dx
            if h * 0.20 < orta_y < h * 0.96:
                adaylar.append((orta_y, uzunluk))
    konumlar = _kumele(adaylar, max(8, h * 0.012))
    if len(konumlar) < 5:
        return []

    # Tabloda art arda gelen çizgi aralıkları birbirine yakındır. En iyi
    # 5/6 çizgilik diziyi arayıp aşırı düzensiz adayları reddet.
    en_iyi: list[float] = []
    for adet in (6, 5):
        en_iyi_puan = float("inf")
        for i in range(len(konumlar) - adet + 1):
            grup = konumlar[i:i + adet]
            farklar = np.diff(grup)
            ort = float(np.mean(farklar))
            if not (h * 0.055 <= ort <= h * 0.20):
                continue
            dagilim = float(np.std(farklar) / ort)
            # Aynı düzenlilikte iki dizi varsa üstte başlayanı seç; alttaki
            # ikinci kâğıdın/kenarın imza satırı gibi algılanmasını engeller.
            puan = dagilim + 0.12 * (grup[0] / h)
            if puan < en_iyi_puan:
                en_iyi, en_iyi_puan = grup, puan
        # Altı sınır bulunduysa beş tablo satırının tamamı belirlenmiştir;
        # daha düzgün görünüyor diye beşli alt kümeye düşme.
        if en_iyi and en_iyi_puan <= 0.40:
            break
    if len(en_iyi) < 5 or en_iyi_puan > 0.40:
        return []
    return [round(y / olcek) for y in en_iyi]


def mozaik_olustur(yol: Path, hedef_genislik: int = 1400) -> tuple[Image.Image | None, dict]:
    img = ImageOps.exif_transpose(Image.open(yol)).convert("RGB")
    cizgiler = satir_cizgilerini_bul(img)
    bilgi = {"satir_cizgisi": len(cizgiler), "mozaik": False}
    # Altı sınır = tablonun beş satırı eksiksiz. Bir sınır eksikse hangi
    # satırın kayıp olduğunu bilemeyiz; güvenli olan tam fotoğrafa dönmektir.
    if len(cizgiler) != 6:
        return None, bilgi

    w, h = img.size
    x0, x1 = round(w * 0.08), round(w * 0.94)
    pay = max(12, round(h * 0.01))
    parcalar: list[Image.Image] = []

    # Üst bölüm Sayın, Tarih ve MRK'yı birlikte korur.
    ust_alt = min(h, cizgiler[0] + pay)
    parcalar.append(img.crop((x0, max(0, round(h * 0.04)), x1, ust_alt)))
    # İlk dört tablo satırı: plaka, ad soyad, km, litre. Beşinci imza satırı
    # OCR alanı olmadığı için mozaiğe alınmaz.
    for ust, alt in zip(cizgiler, cizgiler[1:5]):
        parcalar.append(img.crop((x0, max(0, ust - pay), x1, min(h, alt + pay))))

    bosluk = 12
    hazir: list[Image.Image] = []
    for parca in parcalar:
        oran = hedef_genislik / parca.width
        hazir.append(parca.resize((hedef_genislik, max(80, round(parca.height * oran))), Image.LANCZOS))
    toplam_y = sum(p.height for p in hazir) + bosluk * (len(hazir) - 1)
    mozaik = Image.new("RGB", (hedef_genislik, toplam_y), "white")
    cizim = ImageDraw.Draw(mozaik)
    y = 0
    for parca in hazir:
        mozaik.paste(parca, (0, y))
        y += parca.height
        if y < toplam_y:
            cizim.rectangle((0, y, hedef_genislik, y + bosluk - 1), fill="#d7ddd9")
            y += bosluk
    bilgi["mozaik"] = True
    bilgi["boyut"] = mozaik.size
    return mozaik, bilgi
