"""
Qwen2.5-VL (Ollama, yerel) ile mazot/akaryakıt satış fişini okuyup
Excel'e aktarılabilecek yapılandırılmış JSON üretir.

Kullanım:
    python extract_json.py <resim_dosyasi_veya_klasor>

Çıktı: her fiş için bir JSON nesnesi, ayrıca sonuçlar
sonuclar.json dosyasına da yazılır (toplu iş için).
"""

import base64
import io
import json
import os
import re
import sys
import time
from pathlib import Path
import requests
from PIL import Image
from dogrulama import kaydi_dogrula
from normalizasyon import fis_normalize_et

OLLAMA_URL = "http://localhost:11434/api/chat"
MODEL = os.environ.get("QWEN_MODEL", "qwen2.5vl:7b")
IMG_EXT = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}
MAX_KENAR = 1600  # uzun kenarı bu boyuta indir (hız için)

PROMPT = """Bu bir Türkçe akaryakıt (mazot) satış fişi fotoğrafı. Fişte matbu
(basılı) etiketler ve bunların yanına/altına elle yazılmış değerler var.

Görevin SADECE her basılı etiketin yanına/altına ne yazıldığını OLDUĞU GİBİ
(yorum katmadan, anlamını değiştirmeden) okumak. Bir alana normalde
beklenmeyen bir şey yazılmış olsa bile (örn. "Araç Plakası" yazan yere bir
kişi ismi yazılmış olsa bile), SADECE ORADA NE YAZILI İSE onu aktar —
alanları kendi başına yeniden yorumlama veya taşıma, bu adımı ben yapacağım.

SADECE aşağıdaki alanları içeren bir JSON nesnesi döndür, başka hiçbir
açıklama ekleme:

{
  "fis_no": "sağ üstteki 'MRK' etiketli numara",
  "tarih": "GG/AA/YYYY formatında tarih",
  "sayin_alani": "sol üstteki 'Sayın:' etiketinin yanına/altına elle yazılan
                  metin (fişin üstündeki 'Selim İnşaat' logosu/başlığı
                  DEĞİL — o her fişte aynıdır, onu yazma)",
  "arac_plakasi_alani": "'Araç Plakası' etiketli satıra yazılan metin,
                         ne yazıyorsa harfi harfine",
  "adi_soyadi_alani": "'Adı Soyadı' etiketli satıra yazılan metin,
                       ne yazıyorsa harfi harfine",
  "km_alani": "'K.M.' etiketli satıra yazılan metin, ne yazıyorsa
              harfi harfine (üstü çizili bir şey varsa onu değil,
              geçerli/son yazılan değeri yaz)",
  "litre": "'Verilen Litre' etiketli satırdaki miktar"
}

Kurallar:
- Bir alan fişte yoksa veya okunamıyorsa değerini null yap.
- Sayıları olduğu gibi (fişte yazdığı gibi) yaz, yorum katma.
- Türkçe karakterleri (ç, ş, ı, ğ, ö, ü) koru.
- Yanıtın SADECE geçerli JSON olsun, ```json gibi işaretleyici kullanma."""


def esle(ham: dict) -> dict:
    """Ham (harfi harfine okunmuş) alanları, 'plaka' ve 'ad_soyad' anlamlarına
    eşler. Bazı fişlerde yakıt bir araca değil sabit bir ekipmana (kazık
    makinesi, jeneratör vb.) verilir; bu durumda fişi dolduran kişi
    'Araç Plakası' alanına bir KİŞİ İSMİ, 'Adı Soyadı' alanına da ekipman
    türünü yazar. Gerçek bir Türkçe plaka her zaman rakam içerir — bu yüzden
    'Araç Plakası' alanında hiç rakam yoksa bunu ekipman durumu olarak kabul
    edip alanları buna göre takas ediyoruz. Bu mantık modele değil, koda
    ait: aynı kural her fişte birebir aynı şekilde, hatasız uygulanır."""
    arac = (ham.get("arac_plakasi_alani") or "").strip()
    kisi = (ham.get("adi_soyadi_alani") or "").strip()
    km_ham = (ham.get("km_alani") or "").strip()

    ekipman_durumu = bool(arac) and not any(c.isdigit() for c in arac)

    if ekipman_durumu:
        plaka = kisi or None
        isimler = [p for p in (arac, km_ham) if p and not any(c.isdigit() for c in p)]
        ad_soyad = " - ".join(isimler) if isimler else None
        km = km_ham if km_ham and any(c.isdigit() for c in km_ham) else None
    else:
        plaka = arac or None
        ad_soyad = kisi or None
        km = km_ham or None

    return {
        "fis_no": ham.get("fis_no"),
        "tarih": ham.get("tarih"),
        "firma": ham.get("sayin_alani"),
        "plaka": plaka,
        "ad_soyad": ad_soyad,
        "km": km,
        "litre": ham.get("litre"),
        "_ekipman_durumu": ekipman_durumu,
        "_ham": ham,
    }


def kucult(image_path: Path, max_kenar=MAX_KENAR) -> bytes:
    img = Image.open(image_path).convert("RGB")
    oran = max_kenar / max(img.size)
    if oran < 1:
        img = img.resize((int(img.width * oran), int(img.height * oran)), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=90)
    return buf.getvalue()


def plaka_supheli_mi(plaka) -> bool:
    """Bir plaka grubunda (boşlukla ayrılmış parça) hem harf hem rakam
    karışıksa (örn. 'H2'), muhtemelen OCR karıştırması var demektir
    (Z/2, O/0, S/5, B/8 gibi). Gerçek plaka parçaları hep ya tamamen
    harf ya da tamamen rakamdır."""
    if not plaka or not isinstance(plaka, str):
        return True
    for parca in plaka.split():
        harf_var = any(c.isalpha() for c in parca)
        rakam_var = any(c.isdigit() for c in parca)
        if harf_var and rakam_var:
            return True
    return False


def metin_supheli_mi(deger) -> bool:
    """Boş olması normal olan serbest metin alanları (firma, ad_soyad gibi)
    için: model bazen boş bir alanı rastgele tek bir karakter/rakamla
    'hallüsine' ediyor. Dolu ama anlamsız kısa (<=2 karakter) veya tamamen
    rakamlardan oluşan bir değer, muhtemelen böyle bir hallüsinasyondur."""
    if not deger or not isinstance(deger, str):
        return False  # boş olması normal, şüpheli değil
    d = deger.strip()
    return len(d) <= 2 or d.isdigit()


def tarih_supheli_mi(tarih) -> bool:
    if not tarih or not isinstance(tarih, str):
        return True
    m = re.match(r"^(\d{1,2})[./](\d{1,2})[./](\d{4})$", tarih.strip())
    if not m:
        return True
    gun, ay, yil = (int(x) for x in m.groups())
    return not (1 <= gun <= 31 and 1 <= ay <= 12 and 2015 <= yil <= 2030)


def dogrula(veri: dict) -> dict:
    return kaydi_dogrula(fis_normalize_et(veri))


def resmi_oku(image_path: Path) -> dict:
    b64 = base64.b64encode(kucult(image_path)).decode("utf-8")

    payload = {
        "model": MODEL,
        "messages": [{"role": "user", "content": PROMPT, "images": [b64]}],
        "stream": False,
        "options": {"temperature": 0.0, "num_ctx": 8192},
        "format": "json",
    }

    t0 = time.time()
    resp = requests.post(OLLAMA_URL, json=payload, timeout=300)
    resp.raise_for_status()
    dt = time.time() - t0

    raw = resp.json()["message"]["content"]
    try:
        ham = json.loads(raw)
        veri = esle(ham)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", raw, re.DOTALL)
        if m:
            veri = esle(json.loads(m.group(0)))
        else:
            veri = {"HATA": "JSON parse edilemedi", "ham_cikti": raw}

    veri["_dosya"] = image_path.name
    veri["_sure_sn"] = round(dt, 1)
    return fis_normalize_et(dogrula(veri))


def main():
    if len(sys.argv) < 2:
        print("Kullanım: python extract_json.py <resim_dosyasi_veya_klasor>")
        sys.exit(1)

    hedef = Path(sys.argv[1])
    dosyalar = sorted(p for p in hedef.iterdir() if p.suffix.lower() in IMG_EXT) if hedef.is_dir() else [hedef]

    print(f"Model: {MODEL}\n")

    sonuclar = []
    for dosya in dosyalar:
        print(f"Okunuyor: {dosya.name} ...")
        try:
            veri = resmi_oku(dosya)
        except Exception as e:
            veri = fis_normalize_et(dogrula({"_dosya": dosya.name, "HATA": str(e)}))
        print(json.dumps(veri, ensure_ascii=False, indent=2))
        print("-" * 60)
        sonuclar.append(veri)

    out_path = Path("sonuclar.json")
    out_path.write_text(json.dumps(sonuclar, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n{len(sonuclar)} sonuç '{out_path}' dosyasına yazıldı.")


if __name__ == "__main__":
    main()
