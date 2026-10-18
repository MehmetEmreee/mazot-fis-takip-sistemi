# Araçlar

Ölçüm ve bakım betikleri. Depo kökünden modül olarak çalıştırılır; veriyi
`data/` klasöründen okur (bkz. `mazot/yollar.py`).

| Komut | Amaç |
| --- | --- |
| `python -m tools.ocr_benchmark --set data/benchmark_testseti.json --model qwen2.5vl:7b` | Sabit test setinde OCR doğruluğu ve süresi |
| `python -m tools.duzeltme_benchmark --excel <defter.xlsx> --rapor <ocr raporu>` | Otomatik düzeltmeyi sızıntısız ölçme |
| `python -m tools.ogrenme_benchmark` | Öğrenme önerilerinin isabeti |
| `python -m tools.benzerlik_karsilastir` | Benzerlik ölçülerini karşılaştırma |
| `python -m tools.ekipman_yeniden_esle` | Ekipman fişlerini güncel kurala göre yeniden eşleme (program kapalıyken) |
| `python -m tools.dosya_adi_onar` | Görsel dosya adlarını kayıtlarla onarma (program kapalıyken) |
| `python -m tools.export_excel [sonuclar.json] [fisler.xlsx]` | Kayıtları basit bir Excel'e dökme |

Ölçüm çıktıları (`benchmark_*.json`) kişisel veri içerir; `.gitignore` dışında tutmayın.
