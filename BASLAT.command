#!/bin/bash
# Mazot Fis Kontrol - macOS baslatici (Finder'da cift tiklayin)
cd "$(dirname "$0")" || exit 1
export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"
source ./_ortam_hazirla.sh

if ! venv_hazirla; then
  echo
  echo "Python ortami hazirlanamadi. Once KURULUM.command dosyasini calistirin."
  read -r -p "Kapatmak icin Enter'a basin..." _
  exit 1
fi

exec .venv/bin/python uygulama.py
