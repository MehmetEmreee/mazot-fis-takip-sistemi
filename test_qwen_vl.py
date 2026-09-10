"""
Qwen2.5-VL (Ollama üzerinden, yerel) ile el yazısı fiş okuma testi.
TrOCR ile karşılaştırma için: aynı ornek.jpg, tüm görsel tek seferde verilir
(VLM'ler TrOCR'ın aksine satır satır kırpmaya ihtiyaç duymaz).
"""

import base64
import json
import sys
import time
import requests

OLLAMA_URL = "http://localhost:11434/api/chat"
MODEL = "qwen2.5vl:7b"

PROMPT = (
    "Bu, elle doldurulmuş bir Türkçe dizel/mazot satış fişi fotoğrafı. "
    "Fişteki el yazısıyla yazılmış kalem listesini oku. "
    "Her satırı 'kalem adı: tutar' formatında, gördüğün sırayla listele. "
    "Türkçe kelimeleri ve karakterleri (ç, ş, ı, ğ, ö, ü) olduğu gibi koru, "
    "tahmin ederek İngilizceye çevirme. En altta toplam tutarı da belirt. "
    "Sadece listeyi yaz, başka açıklama ekleme."
)


def resmi_oku(image_path: str) -> str:
    with open(image_path, "rb") as f:
        b64 = base64.b64encode(f.read()).decode("utf-8")

    payload = {
        "model": MODEL,
        "messages": [
            {"role": "user", "content": PROMPT, "images": [b64]}
        ],
        "stream": False,
        "options": {"temperature": 0.0},
    }

    t0 = time.time()
    resp = requests.post(OLLAMA_URL, json=payload, timeout=300)
    resp.raise_for_status()
    dt = time.time() - t0

    data = resp.json()
    text = data["message"]["content"]
    print(f"(süre: {dt:.1f} sn)\n")
    return text


def main():
    image_path = sys.argv[1] if len(sys.argv) > 1 else "ornek.jpg"
    print(f"Model: {MODEL}")
    print(f"Görsel: {image_path}\n")
    print("-" * 60)
    sonuc = resmi_oku(image_path)
    print(sonuc)
    print("-" * 60)


if __name__ == "__main__":
    main()
