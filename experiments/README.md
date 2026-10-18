# Deneyler

Uygulamanın parçası olmayan, ölçüm ve karar için yazılmış deney betikleri.
Sonuçları ve hangi yaklaşımın neden seçildiği `docs/ILERLEME_NOTLARI.txt` içinde.

| Dosya | Ne denendi |
| --- | --- |
| `crop_lines.py`, `goruntu_hazirla.py` | Perspektif düzeltme ve tablo çizgisi tespiti |
| `hucre_mozaik.py`, `hucre_llm_benchmark.py` | Satırları mozaik/tek tek hücre olarak okutma (kullanılmadı) |
| `test_qwen_vl.py` | Qwen-VL ile tek fiş deneme |
| `test_trocr.py` | TrOCR denemesi (ek bağımlılık: transformers, torch) |
| `Modelfile.qwen3vl-instruct` | Qwen3-VL instruct Ollama model tanımı |

Depo kökünden modül olarak çalıştırın, örneğin:

```bash
python -m experiments.crop_lines <resim>
```

Bu klasördeki betikler numpy/opencv gibi `requirements-dev.txt` bağımlılıklarını kullanabilir.
