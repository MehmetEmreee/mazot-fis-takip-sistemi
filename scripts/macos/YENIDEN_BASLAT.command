#!/bin/bash
# Mazot Fis Kontrol - calisan surumu kapatip guncel kodla yeniden baslatir (macOS)
# Depo kokune gec (bu dosya scripts/macos/ altinda).
cd "$(dirname "$0")/../.." || exit 1
echo "Calisan surum kapatiliyor..."
pkill -f "uygulama\.py|-m mazot" 2>/dev/null
sleep 3
echo "Ekipman fisleri yeni kurala gore yeniden eslesiyor..."
.venv/bin/python -m tools.ekipman_yeniden_esle
echo
echo "Guncel surum baslatiliyor..."
exec .venv/bin/python -m mazot
