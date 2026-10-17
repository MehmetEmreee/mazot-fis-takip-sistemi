#!/bin/bash
# Mazot Fis Kontrol - calisan surumu kapatip guncel kodla yeniden baslatir (macOS)
cd "$(dirname "$0")" || exit 1
echo "Calisan surum kapatiliyor..."
pkill -f "uygulama\.py" 2>/dev/null
sleep 3
echo "Ekipman fisleri yeni kurala gore yeniden eslesiyor..."
.venv/bin/python ekipman_yeniden_esle.py
echo
echo "Guncel surum baslatiliyor..."
exec .venv/bin/python uygulama.py
