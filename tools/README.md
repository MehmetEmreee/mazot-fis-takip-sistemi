# Araçlar

Ölçüm ve bakım betikleri. Depo kökünden modül olarak çalıştırılır; veriyi
`data/` klasöründen okur (bkz. `mazot/yollar.py`).

| Komut | Amaç |
| --- | --- |
| `python -m tools.ocr_benchmark --set data/benchmark_testseti.json --model qwen2.5vl:7b --esik-tam 35 --esik-alan 85` | Sabit test setinde OCR doğruluğu ve süresi (eşik altı → çıkış kodu 1) |
| `python -m tools.duzeltme_benchmark --excel <defter.xlsx> --rapor <ocr raporu>` | Otomatik düzeltmeyi sızıntısız ölçme |
| `python -m tools.ogrenme_benchmark` | Öğrenme önerilerinin isabeti |
| `python -m tools.benzerlik_karsilastir` | Benzerlik ölçülerini karşılaştırma |
| `python -m tools.ekipman_yeniden_esle` | Ekipman fişlerini güncel kurala göre yeniden eşleme (program kapalıyken) |
| `python -m tools.dosya_adi_onar` | Görsel dosya adlarını kayıtlarla onarma (program kapalıyken) |
| `python -m tools.export_excel [sonuclar.json] [fisler.xlsx]` | Kayıtları basit bir Excel'e dökme |
| `python -m tools.excel_dogruluk --defter <defter.xlsx> --sayfa "2026 MAZOT"` | Excel aktarım doğruluğu: onaylı fişler ↔ defter satırları ve defter kopyasına simülasyon |
| `python -m tools.uzun_sure_testi --gun 30 --gunluk 200` | Uzun süreli çalışma: kuyruk, bellek, açık dosya, disk ve süre eğilimleri |

Ölçüm çıktıları (`benchmark_*.json`) kişisel veri içerir; `.gitignore` dışında tutmayın.
