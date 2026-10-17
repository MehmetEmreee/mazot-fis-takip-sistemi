#!/bin/bash
# Mazot Fis Kontrol - macOS ilk kurulum (Finder'da cift tiklayin)
cd "$(dirname "$0")" || exit 1
export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"
source ./_ortam_hazirla.sh
MODEL="qwen2.5vl:7b"

bitir() {
  echo
  read -r -p "Kapatmak icin Enter'a basin..." _
  exit "$1"
}

echo "=============================================="
echo "  MAZOT FIS KONTROL - ILK KURULUM (macOS)"
echo "=============================================="
echo

OLLAMA="$(command -v ollama || true)"
if [ -z "$OLLAMA" ] && [ -x "/Applications/Ollama.app/Contents/Resources/ollama" ]; then
  OLLAMA="/Applications/Ollama.app/Contents/Resources/ollama"
fi
if [ -z "$OLLAMA" ]; then
  echo "Ollama bulunamadi."
  echo "https://ollama.com/download adresinden Ollama'yi kurun"
  echo "(veya: brew install ollama), sonra bu dosyayi yeniden calistirin."
  bitir 1
fi

if ! curl -s -o /dev/null http://127.0.0.1:11434/api/tags; then
  echo "Ollama baslatiliyor..."
  if [ -d "/Applications/Ollama.app" ]; then
    open -g -a Ollama
  else
    nohup "$OLLAMA" serve >/dev/null 2>&1 &
  fi
  for _ in $(seq 1 30); do
    sleep 1
    curl -s -o /dev/null http://127.0.0.1:11434/api/tags && break
  done
fi

echo "OCR modeli kontrol ediliyor. Ilk kurulumda indirme uzun surebilir..."
if ! "$OLLAMA" pull "$MODEL"; then
  echo
  echo "Model indirilemedi. Internet baglantisini kontrol edip tekrar deneyin."
  bitir 1
fi

echo
if ! venv_hazirla; then
  echo "Python ortami hazirlanamadi."
  bitir 1
fi

echo
echo "Kurulum tamamlandi. Bundan sonra sadece BASLAT.command dosyasini acin."
bitir 0
