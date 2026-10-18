"""Fiş fotoğraflarını OCR'a vermeden önce düzeltir.

Üç aşama, her biri ayrı ayrı kullanılabilir:
  1) exif_duzelt      — telefonun yön notunu uygular (fotoğraf yan gitmesin)
  2) belgeyi_dikleştir — kağıdın 4 köşesini bulup perspektifi düzeltir,
                         arka planı (masa) atar
  3) tablo_hucreleri   — tablo çizgilerini bulup hücre kutularını verir

Neden gerekli: telefon fotoğrafları EXIF'te "90° döndür" notu taşıyor ama
PIL bunu kendiliğinden uygulamıyor; ayrıca fotoğrafın yarısı masa oluyor ve
model boşuna oraya bakıyor.
"""

from __future__ import annotations

import cv2
import numpy as np
from PIL import Image, ImageOps


def exif_duzelt(img: Image.Image) -> Image.Image:
    """Telefonun EXIF yön bilgisini uygular. PIL bunu varsayılan yapmaz."""
    return ImageOps.exif_transpose(img)


def _kose_sirala(noktalar: np.ndarray) -> np.ndarray:
    """Dört köşeyi sol-üst, sağ-üst, sağ-alt, sol-alt sırasına dizer."""
    noktalar = noktalar.reshape(4, 2).astype("float32")
    sirali = np.zeros((4, 2), dtype="float32")
    toplam = noktalar.sum(axis=1)
    fark = np.diff(noktalar, axis=1)
    sirali[0] = noktalar[np.argmin(toplam)]   # sol-üst
    sirali[2] = noktalar[np.argmax(toplam)]   # sağ-alt
    sirali[1] = noktalar[np.argmin(fark)]     # sağ-üst
    sirali[3] = noktalar[np.argmax(fark)]     # sol-alt
    return sirali


def _belge_koselerini_bul(bgr: np.ndarray, min_alan_orani=0.15):
    """Kağıdın dört köşesini arar; bulamazsa None döner."""
    yukseklik, genislik = bgr.shape[:2]
    olcek = 1000 / max(yukseklik, genislik)
    kucuk = cv2.resize(bgr, None, fx=olcek, fy=olcek, interpolation=cv2.INTER_AREA)

    gri = cv2.cvtColor(kucuk, cv2.COLOR_BGR2GRAY)
    gri = cv2.GaussianBlur(gri, (7, 7), 0)
    # Kağıt açık, masa koyu: Otsu eşiği ikisini ayırır. Kenar tespitine göre
    # daha dayanıklı, çünkü kağıdın kendi çizgileri kenar olarak sayılmıyor.
    _, ikili = cv2.threshold(gri, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    cekirdek = cv2.getStructuringElement(cv2.MORPH_RECT, (9, 9))
    ikili = cv2.morphologyEx(ikili, cv2.MORPH_CLOSE, cekirdek, iterations=2)

    konturlar, _ = cv2.findContours(ikili, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not konturlar:
        return None

    kucuk_alan = kucuk.shape[0] * kucuk.shape[1]
    for kontur in sorted(konturlar, key=cv2.contourArea, reverse=True)[:5]:
        alan = cv2.contourArea(kontur)
        if alan < kucuk_alan * min_alan_orani:
            break
        cevre = cv2.arcLength(kontur, True)
        for tolerans in (0.02, 0.04, 0.06):
            yaklasik = cv2.approxPolyDP(kontur, tolerans * cevre, True)
            if len(yaklasik) == 4:
                return _kose_sirala(yaklasik) / olcek
        # Dört köşeye indirgenemiyorsa döndürülmüş dikdörtgenle yetin.
        kutu = cv2.boxPoints(cv2.minAreaRect(kontur))
        if cv2.contourArea(kutu.astype(np.float32)) > alan * 0.75:
            return _kose_sirala(kutu) / olcek
    return None


def belgeyi_dikleştir(img: Image.Image) -> tuple[Image.Image, bool]:
    """Kağıdı bulup perspektifini düzeltir. (görüntü, basarili) döner —
    bulunamazsa görüntü olduğu gibi geri verilir, çünkü yanlış kırpmak
    hiç kırpmamaktan kötüdür (veri sessizce kaybolur)."""
    bgr = cv2.cvtColor(np.array(img.convert("RGB")), cv2.COLOR_RGB2BGR)
    koseler = _belge_koselerini_bul(bgr)
    if koseler is None:
        return img, False

    (sol_ust, sag_ust, sag_alt, sol_alt) = koseler
    genislik = int(max(np.linalg.norm(sag_alt - sol_alt), np.linalg.norm(sag_ust - sol_ust)))
    yukseklik = int(max(np.linalg.norm(sag_ust - sag_alt), np.linalg.norm(sol_ust - sol_alt)))
    if genislik < 200 or yukseklik < 200:
        return img, False

    hedef = np.array([[0, 0], [genislik - 1, 0],
                      [genislik - 1, yukseklik - 1], [0, yukseklik - 1]], dtype="float32")
    donusum = cv2.getPerspectiveTransform(koseler, hedef)
    duz = cv2.warpPerspective(bgr, donusum, (genislik, yukseklik))
    return Image.fromarray(cv2.cvtColor(duz, cv2.COLOR_BGR2RGB)), True


def tablo_hucreleri(img: Image.Image, min_satir=4) -> list[tuple[int, int, int, int]]:
    """Tablo çizgilerini bulup değer hücrelerinin kutularını döndürür.

    Fişin tablosu iki sütunlu (etiket | değer) ve çizgili olduğu için yatay ve
    dikey çizgiler morfolojik süzgeçle çıkarılır. Sabit yüzde kullanmak yerine
    gerçek çizgilere bakılır — fotoğraf açıları değiştiği için sabit oran
    kayıyordu."""
    gri = np.array(img.convert("L"))
    ikili = cv2.adaptiveThreshold(gri, 255, cv2.ADAPTIVE_THRESH_MEAN_C,
                                  cv2.THRESH_BINARY_INV, 25, 15)
    y, g = ikili.shape

    yatay_cekirdek = cv2.getStructuringElement(cv2.MORPH_RECT, (max(20, g // 15), 1))
    dikey_cekirdek = cv2.getStructuringElement(cv2.MORPH_RECT, (1, max(20, y // 15)))
    yataylar = cv2.morphologyEx(ikili, cv2.MORPH_OPEN, yatay_cekirdek, iterations=2)
    dikeyler = cv2.morphologyEx(ikili, cv2.MORPH_OPEN, dikey_cekirdek, iterations=2)

    def cizgi_konumlari(maske, eksen, en_az_oran=0.4):
        profil = maske.sum(axis=eksen) / 255
        sinir = maske.shape[1 - eksen] * en_az_oran
        var = profil > sinir
        gruplar, baslangic = [], None
        for i, deger in enumerate(var):
            if deger and baslangic is None:
                baslangic = i
            elif not deger and baslangic is not None:
                gruplar.append((baslangic + i - 1) // 2)
                baslangic = None
        if baslangic is not None:
            gruplar.append((baslangic + len(var) - 1) // 2)
        return gruplar

    satir_cizgileri = cizgi_konumlari(yataylar, 1)
    sutun_cizgileri = cizgi_konumlari(dikeyler, 0)
    if len(satir_cizgileri) < min_satir + 1 or len(sutun_cizgileri) < 2:
        return []

    # Değer sütunu: en sağdaki iki dikey çizgi arası.
    sol, sag = sutun_cizgileri[-2], sutun_cizgileri[-1]
    hucreler = []
    for ust, alt in zip(satir_cizgileri, satir_cizgileri[1:]):
        if alt - ust < y * 0.04:      # çok ince aralıkları atla
            continue
        hucreler.append((sol, ust, sag, alt))
    return hucreler


def hazirla(yol, dikleştir=True) -> tuple[Image.Image, dict]:
    """Tek çağrıda: EXIF düzelt + (istenirse) belgeyi dikleştir."""
    img = exif_duzelt(Image.open(yol))
    bilgi = {"exif_uygulandi": True, "dikleştirildi": False}
    if dikleştir:
        img, ok = belgeyi_dikleştir(img)
        bilgi["dikleştirildi"] = ok
    return img.convert("RGB"), bilgi
