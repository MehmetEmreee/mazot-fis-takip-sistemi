"""
Qwen2.5-VL (Ollama, yerel) ile mazot/akaryakıt satış fişini okuyup
Excel'e aktarılabilecek yapılandırılmış JSON üretir.

Kullanım:
    python -m mazot.extract_json <resim_dosyasi_veya_klasor>

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
import unicodedata
from pathlib import Path
import requests
from rapidfuzz import fuzz
from PIL import Image, ImageOps
from .dogrulama import kaydi_dogrula
from .normalizasyon import fis_normalize_et

OLLAMA_URL = "http://localhost:11434/api/chat"
MODEL = os.environ.get("QWEN_MODEL", "qwen2.5vl:7b")
IMG_EXT = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}
MAX_KENAR = 1600  # uzun kenarı bu boyuta indir (hız için)
# Ölçümde 4096 ve 8192 aynı alan doğruluğunu verdi; 8192, 8 GB ekran
# kartında ciddi CPU taşmasına yol açtığı için güvenli yüksek kalite profili 4096.
NUM_CTX = int(os.environ.get("QWEN_NUM_CTX", "4096"))

JSON_SCHEMA = {
    "type": "object",
    "properties": {
        alan: {"type": ["string", "null"]}
        for alan in (
            "fis_no", "tarih", "sayin_alani", "arac_plakasi_alani",
            "adi_soyadi_alani", "km_alani", "litre",
        )
    },
    "required": [
        "fis_no", "tarih", "sayin_alani", "arac_plakasi_alani",
        "adi_soyadi_alani", "km_alani", "litre",
    ],
    "additionalProperties": False,
}

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
- Yanıtın SADECE geçerli JSON olsun, ```json gibi işaretleyici kullanma.

DİKKAT EDİLECEK NOKTALAR:
- Türkçe harfleri asla latinleştirme: Ç, Ş, İ, I, Ğ, Ü, Ö harflerini gördüğün
  gibi yaz (VİNÇ'i VINS/VINC, FORKLİFT'i FORCLIFT yapma).
- Bir hücrede sadece yatay bir çizgi/tire varsa o hücre BOŞTUR: null yaz,
  çizgiyi '1' veya '-' diye okuma.
- Litre hücresinde toplama varsa (örn. '(30+30)') sonucunu yaz: 60.
- 'Sayın:' satırında el yazısı yoksa null yaz; fişin başlığındaki matbu
  'Selim İnşaat' yazısını oraya kopyalama.
- 'MRK' numarasını rakam rakam say, baştaki sıfırlar dahil hepsini yaz."""


# Ekipman fişlerinde hangi hücrenin ekipman, hangisinin kişi olduğunu
# ayırt etmek için kullanılan sözlük. OCR harfleri bozabildiği için
# eşleştirme bire bir değil, benzerlik puanıyla yapılır.
EKIPMAN_SOZLUGU = (
    "SILINDIR", "JENERATOR", "FORKLIFT", "KAZIK", "MAKINE", "MAKINA", "VINC",
    "JCB", "KATO", "KAWAZAKI", "ZOOMLION", "MANITOU", "TANK", "EKSKAVATOR",
    "KEPCE", "LODER", "BEKO", "MIKSER", "POMPA", "KOMPRESOR", "GREYDER",
    "DOZER", "PALETLI", "KIRALIK", "KOMATSU", "HIDROMEK", "BOMAG", "DYNAPAC",
    "TAKEUCHI", "KUBOTA", "CATERPILLAR", "VIBRATOR", "ASFALT", "KULE",
)
_RAKAM_HARF = str.maketrans(
    {"0": "O", "1": "I", "2": "Z", "4": "A", "5": "S", "6": "G", "7": "T", "8": "B"}
)


def _ekipman_anahtari(metin: str) -> str:
    """Türkçe harfleri ve OCR'ın harf yerine okuduğu rakamları sadeleştirir."""
    metin = unicodedata.normalize("NFKD", (metin or "").upper())
    metin = "".join(c for c in metin if not unicodedata.combining(c))
    for eski, yeni in (
        ("Ç", "C"), ("Ğ", "G"), ("Ö", "O"), ("Ş", "S"), ("Ü", "U"), ("İ", "I")
    ):
        metin = metin.replace(eski, yeni)
    metin = metin.translate(_RAKAM_HARF)
    return re.sub(r"[^A-Z0-9 ]+", " ", metin).strip()


def ekipman_puani(metin: str) -> float:
    """Metnin bir iş makinesi/ekipman adı olma ihtimalini 0-100 arası verir."""
    anahtar = _ekipman_anahtari(metin)
    if not anahtar:
        return 0.0
    en_iyi = 0.0
    for kelime in anahtar.split():
        if len(kelime) < 3:
            continue
        for sozcuk in EKIPMAN_SOZLUGU:
            en_iyi = max(en_iyi, fuzz.ratio(kelime, sozcuk))
    for sozcuk in EKIPMAN_SOZLUGU:
        if len(sozcuk) >= 5:
            en_iyi = max(en_iyi, fuzz.partial_ratio(anahtar, sozcuk))
    return en_iyi


EKIPMAN_ESIGI = 78


def _plaka_gibi(metin: str) -> bool:
    """Metin gerçek bir Türkçe plaka kalıbına uyuyor mu (34 ABC 123)?"""
    anahtar = re.sub(r"\s+", " ", _ekipman_anahtari(metin))
    return bool(re.fullmatch(r"\d{2} ?[A-Z]{1,3} ?\d{2,5}", anahtar))


ISIM_OLMAYANLAR = {"NULL", "NONE", "YOK", "BOS", "KM", "K M", "MRK"}


def _isim_gibi(metin: str) -> bool:
    """'-', 'K.M.', '7', 'null' gibi doldurma değerlerini isim saymaz.

    OCR bir harfi rakam okuyabildiği için (RAMA2AN) rakam içeren metinler
    tümden elenmez; en az üç harf olması ve doldurma sözcüğü olmaması yeter.
    """
    metin = (metin or "").strip()
    if len(metin) < 3 or sum(c.isalpha() for c in metin) < 3:
        return False
    return _ekipman_anahtari(metin).replace(" ", "") not in {
        a.replace(" ", "") for a in ISIM_OLMAYANLAR
    }


def esle(ham: dict) -> dict:
    """Ham (harfi harfine okunmuş) alanları, 'plaka' ve 'ad_soyad' anlamlarına
    eşler. Bazı fişlerde yakıt bir araca değil sabit bir ekipmana (kazık
    makinesi, jeneratör vb.) verilir; bu durumda fişi dolduran kişi
    'Araç Plakası' alanına bazen ekipman adını, bazen de kişi ismini yazar;
    kalan hücreye de diğerini yazar. Gerçek bir Türkçe plaka her zaman rakam
    içerir — bu yüzden 'Araç Plakası' alanında hiç rakam yoksa bunu ekipman
    durumu olarak kabul ediyoruz. Hangi hücrenin ekipman olduğunu yerine göre
    değil İÇERİĞE bakarak buluyoruz: ekipman adı 'plaka' alanına, kişi ismi
    'ad_soyad' alanına gider. İki taraf da ekipman sözlüğüne uymazsa fişte
    yazdığı gibi bırakılır. Bu mantık modele değil, koda ait: aynı kural her
    fişte birebir aynı şekilde uygulanır."""
    arac = (ham.get("arac_plakasi_alani") or "").strip()
    kisi = (ham.get("adi_soyadi_alani") or "").strip()
    km_ham = (ham.get("km_alani") or "").strip()

    # Gerçek plakada her zaman rakam vardır; ama OCR ekipman adındaki harfleri
    # rakam olarak okuyabiliyor (KAZIK MAK -> KA21K MAK). Bu yüzden plaka
    # kalıbına uymayan ve ekipman sözlüğüne uyan metinler de ekipman sayılır.
    ekipman_durumu = bool(arac) and (
        not any(c.isdigit() for c in arac)
        or (not _plaka_gibi(arac) and ekipman_puani(arac) >= EKIPMAN_ESIGI)
    )

    if ekipman_durumu:
        arac_puani = ekipman_puani(arac)
        kisi_puani = ekipman_puani(kisi)
        if max(arac_puani, kisi_puani) < EKIPMAN_ESIGI or arac_puani >= kisi_puani:
            # Ekipman 'Araç Plakası' hücresinde ya da karar verilemiyor:
            # fişte yazdığı gibi bırak.
            ekipman_metni, kisi_metni = arac, kisi
        else:
            # Ekipman 'Adı Soyadı' hücresine yazılmış: hücreleri takas et.
            ekipman_metni, kisi_metni = kisi, arac
        plaka = ekipman_metni or None
        isimler = [p for p in (kisi_metni, km_ham) if _isim_gibi(p)]
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


def jpeg_hazirla(img: Image.Image, max_kenar=MAX_KENAR) -> bytes:
    img = img.convert("RGB")
    oran = max_kenar / max(img.size)
    if oran < 1:
        img = img.resize((int(img.width * oran), int(img.height * oran)), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=90)
    return buf.getvalue()


def kucult(image_path: Path, max_kenar=MAX_KENAR) -> bytes:
    # Telefonun EXIF yön bilgisini piksele uygula; yan/dik ters fişler modelin
    # doğruluğunu ve süresini ciddi biçimde bozmasın.
    img = ImageOps.exif_transpose(Image.open(image_path))
    return jpeg_hazirla(img, max_kenar)


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


def resmi_oku(
    image_path: Path, *, model: str | None = None, max_kenar: int | None = None,
) -> dict:
    """Bir fişi okur. Model/çözünürlük parametreleri regresyon testi içindir."""
    secilen_model = model or MODEL
    secilen_kenar = max_kenar or MAX_KENAR
    b64 = base64.b64encode(kucult(image_path, secilen_kenar)).decode("utf-8")

    payload = {
        "model": secilen_model,
        "messages": [{"role": "user", "content": PROMPT, "images": [b64]}],
        "stream": False,
        "options": {
            "temperature": 0.0, "num_ctx": NUM_CTX, "num_predict": 260,
            "seed": 42,
        },
        "format": JSON_SCHEMA,
        "keep_alive": "30m",
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
    veri["_model"] = secilen_model
    veri["_max_kenar"] = secilen_kenar
    return fis_normalize_et(dogrula(veri))


def main():
    if len(sys.argv) < 2:
        print("Kullanım: python -m mazot.extract_json <resim_dosyasi_veya_klasor>")
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
