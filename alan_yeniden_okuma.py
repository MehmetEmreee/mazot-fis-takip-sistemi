"""Otomatik düzeltmeden sonra şüpheli kalan alanı kırpıp yeniden okur.

Şüpheli alan: firma, plaka veya ad-soyad düzeltmeden sonra geçmişte (Excel +
onaylı fişler) aynen görülmemiş bir değer taşıyorsa. Bu alanın satırı fişten
kırpılır (basılı etiket kırpmada kalır) ve Qwen'e alanın adı söylenerek
("Bu alan 'Araç Plakası' alanıdır") yeniden okutulur. Yeni okuma düzeltmeden
geçip geçmişte bilinen bir değere oturursa kabul edilir; oturmazsa ilk sonuç
kalır. Çoğu fiş ikinci çağrı almaz.

Tablo kutuları türevle bulunur: Sobel → yatay/dikey çizgiler → etiket|değer
ayracı ve onu kesen satır çizgileri (eğik fotoğrafa dayanıklı). Tablo
güvenle bulunamazsa kırpma yapılmaz, ilk sonuç korunur.

Ölçüm (18 Eki 2026, sızıntısız, sabit 100 fiş): tam doğru fiş %80 → %82,
fiş başı 5,9 → 6,2 sn; test dışı 693 fişte %68,8 → %72,2.
Kapatmak için: MAZOT_YENIDEN_OKUMA=0
"""

from __future__ import annotations

import base64
import copy
import io
import json
import os
import time
from pathlib import Path

import requests
from PIL import Image, ImageOps

AKTIF = os.environ.get("MAZOT_YENIDEN_OKUMA", "1") != "0"
ALANLAR = ("firma", "plaka", "ad_soyad")
SATIR = {"plaka": 0, "ad_soyad": 1}
KIRPMA_KENARI = 1600
SATIR_SAYISI = 5  # Araç Plakası, Adı Soyadı, K.M., Verilen Litre, İmza

_ORTAK = ("Bu görüntü Türkçe bir akaryakıt (mazot) satış fişinin kırpılmış bir bölümüdür. "
          "Fişte basılı etiketler ve yanlarına elle yazılmış değerler var. ")
PROMPTLAR = {
    "plaka": _ORTAK + (
        "Bu alan 'Araç Plakası' alanıdır. Yalnızca 'Araç Plakası' etiketinin sağındaki "
        "hücreye elle yazılanı harfi harfine oku; alttaki 'Adı Soyadı' satırını yazma. "
        "Genellikle Türk plakasıdır (iki rakam, 1-3 harf, 2-5 rakam; örn. 34 ABC 123; "
        "baştaki 34 bazen yazılmaz), bazen iş makinesi adı (JCB, KAZIK MAK, JENERATÖR) "
        "yazılır. Harfle rakamı karıştırma (G/6, Z/2, S/5, B/8, O/0, V/U). "
        "Hücre boşsa veya yalnız çizgi varsa null."),
    "ad_soyad": _ORTAK + (
        "Bu alan 'Adı Soyadı' alanıdır. Yalnızca 'Adı Soyadı' etiketinin sağındaki "
        "hücreye elle yazılanı harfi harfine oku; üstteki 'Araç Plakası' satırını yazma. "
        "Genellikle Türkçe kişi adı soyadıdır, bazen iş makinesi adı. Türkçe harfleri "
        "(Ç Ş İ I Ğ Ü Ö) koru. Hücre boşsa veya yalnız çizgi varsa null."),
    "firma": _ORTAK + (
        "Bu alan 'Sayın:' alanıdır. Sol üstteki 'Sayın:' etiketinin yanındaki noktalı "
        "satıra elle yazılan firma adını harfi harfine oku. Başlıktaki matbu 'Selim İnşaat' "
        "logosunu, 'Tarih', 'Saat', 'MRK' alanlarını yazma. Sayın satırında el yazısı "
        "yoksa null. Türkçe harfleri koru."),
}
_SCHEMA = {"type": "object", "properties": {"deger": {"type": ["string", "null"]}},
           "required": ["deger"], "additionalProperties": False}
_BOS = {"", "null", "none", "-"}


# --- Tablo kutuları -----------------------------------------------------------

def tablo_bul(img: Image.Image) -> dict | None:
    """Tablonun sol/sağ sınırı, satır sınırları (y) ve satır yüksekliği.

    Dönen koordinatlar özgün görüntü pikselidir; bulunamazsa None.
    """
    import cv2  # ağır bağımlılık: yalnız gerektiğinde
    import numpy as np

    rgb = np.array(img.convert("RGB"))
    olcek = min(1.0, 1600 / max(rgb.shape[:2]))
    if olcek < 1:
        rgb = cv2.resize(rgb, None, fx=olcek, fy=olcek, interpolation=cv2.INTER_AREA)
    gri = cv2.GaussianBlur(cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY), (3, 3), 0)
    h, w = gri.shape

    def ikile(turev):
        turev = cv2.normalize(np.abs(turev), None, 0, 255, cv2.NORM_MINMAX).astype(np.uint8)
        _, ikili = cv2.threshold(turev, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        return ikili

    def cekirdek(gen, yuk):
        return cv2.getStructuringElement(cv2.MORPH_RECT, (gen, yuk))

    # y-türevi yatay, x-türevi dikey kenarlar. Eğik çizgiler kısa çekirdekte
    # kopmasın diye önce çizgiye dik yönde kalınlaştırılır; uzun çekirdekli
    # açma kısa el yazısı/basılı yazı kenarlarını siler.
    yatay = cv2.dilate(ikile(cv2.Sobel(gri, cv2.CV_32F, 0, 1, ksize=3)), cekirdek(1, 5))
    yatay = cv2.morphologyEx(yatay, cv2.MORPH_OPEN, cekirdek(max(25, w // 25), 1))
    yatay = cv2.dilate(yatay, cekirdek(15, 3))
    dikey = cv2.dilate(ikile(cv2.Sobel(gri, cv2.CV_32F, 1, 0, ksize=3)), cekirdek(7, 1))
    dikey = cv2.morphologyEx(dikey, cv2.MORPH_OPEN, cekirdek(1, max(25, h // 45)))
    dikey = cv2.dilate(dikey, cekirdek(3, 15))

    def cizgiler(maske, uzun_mu, yatay_mi):
        """Her uzun bileşene doğru uydurur: (başlangıç, bitiş, konum fonksiyonu)."""
        sayi, etiket, istat, _ = cv2.connectedComponentsWithStats(maske, 8)
        sonuc = []
        for i in range(1, sayi):
            x, y, bw, bh, _ = istat[i]
            if not uzun_mu(bw, bh):
                continue
            ys, xs = np.nonzero(etiket == i)
            vx, vy, x0, y0 = cv2.fitLine(np.column_stack([xs, ys]).astype(np.float32),
                                         cv2.DIST_L2, 0, 0.01, 0.01).ravel()
            if yatay_mi:
                if abs(vy / vx) > 0.15:
                    continue
                sonuc.append((x, x + bw, lambda X, x0=x0, y0=y0, e=vy / vx: y0 + (X - x0) * e))
            else:
                if abs(vx / vy) > 0.15:
                    continue
                sonuc.append((y, y + bh, lambda Y, x0=x0, y0=y0, e=vx / vy: x0 + (Y - y0) * e))
        return sonuc

    yataylar = cizgiler(yatay, lambda bw, bh: bw > w * 0.25, True)
    dikeyler = cizgiler(dikey, lambda bw, bh: bh > h * 0.15, False)

    # Etiket|değer ayracı: kendisini en çok yatay çizginin kestiği, eşitlikte en
    # içte kalan dikey. Tablonun dış kenarları sık sık kaybolur, ayraç kalır.
    en_iyi = None
    for vy0, vy1, x_of in dikeyler:
        kesen = []
        for hx0, hx1, y_of in yataylar:
            X = x_of(y_of(x_of((vy0 + vy1) / 2)))
            if hx0 + w * 0.05 < X < hx1 - w * 0.05 and vy0 - 0.03 * h < y_of(X) < vy1 + 0.03 * h:
                kesen.append((y_of(X), hx0, hx1, X))
        if len(kesen) < 3:
            continue
        anahtar = (len(kesen), min(min(X - a, b - X) for _, a, b, X in kesen))
        if en_iyi is None or anahtar > en_iyi[0]:
            en_iyi = (anahtar, sorted(kesen))
    if en_iyi is None:
        return None
    kesen = en_iyi[1]
    satirlar = []
    for yy, *_ in kesen:  # aynı çizginin iki kenarını tek say
        if not satirlar or yy - satirlar[-1] >= h * 0.015:
            satirlar.append(yy)
    satirlar = satirlar[:SATIR_SAYISI + 1]
    if len(satirlar) < 3:
        return None
    farklar = np.diff(satirlar)
    r = float(np.median(farklar))
    if not (h * 0.03 <= r <= h * 0.2) or farklar.min() < 0.55 * r or farklar[:2].max() > 1.6 * r:
        return None
    geri = 1 / olcek
    return {
        "sol": min(a for _, a, _, _ in kesen) * geri,
        "sag": max(b for _, _, b, _ in kesen) * geri,
        "satirlar": [float(s) * geri for s in satirlar],
        "r": r * geri,
    }


def alan_kirp(img: Image.Image, alan: str, tablo: dict | None, pay: float = 0.4):
    """Alanın satır kutusunu (etiket + değer) eğim payıyla büyütüp kırpar."""
    if tablo is None:
        return None
    W, H = img.size
    r, s = tablo["r"], tablo["satirlar"]
    if alan == "firma":
        # 'Sayın:' kutusu tablonun üstünde, sol tarafta; Tarih/MRK sağda.
        x0 = tablo["sol"] - 0.4 * r
        x1 = tablo["sol"] + 0.75 * (tablo["sag"] - tablo["sol"])
        y0, y1 = s[0] - 3.0 * r, s[0] - 0.1 * r
    else:
        i = SATIR[alan]
        if len(s) < i + 2:
            return None
        x0, x1 = tablo["sol"] - pay * r, tablo["sag"] + pay * r
        y0, y1 = s[i] - pay * r, s[i + 1] + pay * r
    x0, y0, x1, y1 = max(0, round(x0)), max(0, round(y0)), min(W, round(x1)), min(H, round(y1))
    if x1 - x0 < 50 or y1 - y0 < 30:
        return None
    return img.crop((x0, y0, x1, y1))


# --- Yeniden okuma ----------------------------------------------------------

def alani_oku(parca: Image.Image, alan: str) -> str | None:
    """Kırpılmış alanı Qwen'e alanın adıyla okutur; okunamazsa None."""
    from extract_json import MODEL, NUM_CTX, OLLAMA_URL, jpeg_hazirla

    oran = KIRPMA_KENARI / max(parca.size)
    parca = parca.resize((round(parca.width * oran), round(parca.height * oran)), Image.LANCZOS)
    resp = requests.post(OLLAMA_URL, json={
        "model": MODEL, "stream": False, "keep_alive": "30m", "format": _SCHEMA,
        "messages": [{"role": "user", "content": PROMPTLAR[alan],
                      "images": [base64.b64encode(jpeg_hazirla(parca, KIRPMA_KENARI)).decode()]}],
        "options": {"temperature": 0.0, "num_ctx": NUM_CTX, "num_predict": 80, "seed": 42},
    }, timeout=120)
    resp.raise_for_status()
    try:
        deger = json.loads(resp.json()["message"]["content"]).get("deger")
    except (json.JSONDecodeError, AttributeError):  # tekrar döngüsü / yarım yanıt
        return None
    if not isinstance(deger, str) or deger.strip().casefold() in _BOS:
        return None
    return deger.strip()


def supheli_alanlar(kayit: dict, gecmiste_var, anlamli) -> list[str]:
    sonuc = []
    for alan in ALANLAR:
        if alan == "ad_soyad" and kayit.get("_ekipman_durumu"):
            continue  # esle() ekipman fişinde ad + K.M. hücresini birleştirir
        deger = kayit.get(alan)
        if anlamli(deger, alan) and not gecmiste_var(alan, deger):
            sonuc.append(alan)
    return sonuc


def yeniden_oku_ve_duzelt(kayit: dict, ham: dict, resim: Path, *,
                          duzelt=None, gecmiste_var=None, anlamli=None,
                          okuyucu=alani_oku) -> list[dict]:
    """Şüpheli alanları yeniden okur; kabul edilenleri kayda yazar.

    kayit: otomatik düzeltilmiş kayıt (yerinde değişir).
    ham:   aynı kaydın düzeltmeden önceki kopyası; yeni okuma bunun üstüne
           konup aynı düzeltmeden geçirilir.
    Hiçbir hata OCR akışını bozmaz; denenen her alan _yeniden_okuma'ya yazılır.
    """
    if not AKTIF:
        return []
    if duzelt is None or gecmiste_var is None or anlamli is None:
        import excel_ogrenme
        duzelt = duzelt or excel_ogrenme.guvenli_otomatik_duzelt
        gecmiste_var = gecmiste_var or excel_ogrenme.gecmiste_var
        anlamli = anlamli or excel_ogrenme._anlamli
    from normalizasyon import fis_normalize_et

    try:
        alanlar = supheli_alanlar(kayit, gecmiste_var, anlamli)
        if not alanlar:
            return []
        img = ImageOps.exif_transpose(Image.open(resim)).convert("RGB")
        tablo = tablo_bul(img)
    except Exception as exc:  # cv2 yok, resim açılamadı...
        kayit["_yeniden_okuma"] = [{"hata": str(exc)}]
        return []

    kabul, iz = [], []
    for alan in alanlar:
        t0 = time.perf_counter()
        bilgi = {"alan": alan, "ilk": kayit.get(alan), "okunan": None, "kabul": False}
        try:
            parca = alan_kirp(img, alan, tablo)
            if parca is None:
                bilgi["neden"] = "tablo bulunamadı"
            else:
                okunan = okuyucu(parca, alan)
                bilgi["okunan"] = okunan
                if okunan:
                    deneme = copy.deepcopy(ham)
                    deneme.pop("_otomatik_duzeltmeler", None)
                    deneme[alan] = okunan
                    duzelt(fis_normalize_et(deneme))  # ana OCR yoluyla aynı biçim
                    yeni = deneme.get(alan)
                    if gecmiste_var(alan, yeni) and str(yeni).strip() != str(kayit.get(alan)).strip():
                        degisiklik = {
                            "alan": alan, "eski": kayit.get(alan), "yeni": yeni,
                            "benzerlik": None,
                            "neden": f"alan yakından yeniden okundu ('{okunan}'), geçmiş kayıtla eşleşti",
                        }
                        kayit[alan] = yeni
                        kayit.setdefault("_otomatik_duzeltmeler", []).append(degisiklik)
                        kayit["_otomatik_duzeltildi"] = True
                        kabul.append(degisiklik)
                        bilgi["kabul"] = True
        except Exception as exc:
            bilgi["hata"] = str(exc)
        bilgi["sure_sn"] = round(time.perf_counter() - t0, 2)
        iz.append(bilgi)
    kayit["_yeniden_okuma"] = iz
    return kabul
