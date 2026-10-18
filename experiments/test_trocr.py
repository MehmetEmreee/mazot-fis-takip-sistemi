"""
TrOCR El Yazısı Test Scripti
------------------------------
Bu script, mazot fişlerindeki el yazısını TrOCR modeli ile okumayı dener.
İlk çalıştırmada model internetten indirilir (~1.3 GB, sadece bir kere).

KURULUM (Windows'ta, önce bir kere):
    pip install torch transformers pillow

KULLANIM:
    python -m experiments.test_trocr fis_ornek.jpg
    (veya klasördeki tüm resimleri test etmek için: python -m experiments.test_trocr klasor_yolu/)
"""

import sys
import os
from pathlib import Path
from PIL import Image
from transformers import TrOCRProcessor, VisionEncoderDecoderModel

# Desteklenen resim uzantıları
IMG_EXT = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}


def modeli_yukle():
    print("Model yükleniyor (ilk seferde indirilecek, biraz sürebilir)...")
    processor = TrOCRProcessor.from_pretrained("microsoft/trocr-base-handwritten")
    model = VisionEncoderDecoderModel.from_pretrained("microsoft/trocr-base-handwritten")
    print("Model hazır.\n")
    return processor, model


def resmi_oku(image_path, processor, model):
    image = Image.open(image_path).convert("RGB")
    pixel_values = processor(images=image, return_tensors="pt").pixel_values
    generated_ids = model.generate(pixel_values, max_length=64)
    text = processor.batch_decode(generated_ids, skip_special_tokens=True)[0]
    return text


def main():
    if len(sys.argv) < 2:
        print("Kullanım: python -m experiments.test_trocr <resim_dosyasi_veya_klasor>")
        sys.exit(1)

    hedef = Path(sys.argv[1])
    if not hedef.exists():
        print(f"Bulunamadı: {hedef}")
        sys.exit(1)

    # Test edilecek dosyaları topla
    if hedef.is_dir():
        dosyalar = sorted([p for p in hedef.iterdir() if p.suffix.lower() in IMG_EXT])
    else:
        dosyalar = [hedef]

    if not dosyalar:
        print("Klasörde resim bulunamadı.")
        sys.exit(1)

    processor, model = modeli_yukle()

    print(f"{len(dosyalar)} dosya test edilecek.\n")
    print("-" * 60)
    for dosya in dosyalar:
        try:
            sonuc = resmi_oku(dosya, processor, model)
            print(f"{dosya.name:30s} -> '{sonuc}'")
        except Exception as e:
            print(f"{dosya.name:30s} -> HATA: {e}")
    print("-" * 60)
    print("\nNOT: Bu model tüm fişi değil, TEK BİR SATIR/HÜCREYİ okumak için")
    print("tasarlanmıştır. En iyi sonuç için resmi önceden kırpıp (plaka,")
    print("tarih, litre gibi tek tek alanlar halinde) vermek gerekir.")
    print("Tüm fiş fotoğrafını doğrudan verirsen sonuç muhtemelen anlamsız çıkar.")


if __name__ == "__main__":
    main()
