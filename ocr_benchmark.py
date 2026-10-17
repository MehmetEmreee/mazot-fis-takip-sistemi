"""Onaylı fişleri gerçek etiket kabul ederek OCR sürümlerini karşılaştırır.

Sabit test seti (önerilen):
    python ocr_benchmark.py --set-olustur            # benchmark_testseti.json
    python ocr_benchmark.py --set benchmark_testseti.json --model qwen2.5vl:7b \
        --output benchmark_set_qwen25vl7b_1600.json
    # Başka ortamda üretilmiş tahminleri aynı ölçütle puanlamak için:
    python ocr_benchmark.py --set benchmark_testseti.json \
        --tahminler paddle_tahminleri.json --output benchmark_set_paddle.json

Test seti yalnız dosya yolu, SHA-256 ve onaylı etiketleri içerir; gerçek
kişisel veri olduğu için benchmark_*.json olarak .gitignore'dadır.

Karşılaştırma iki türlü raporlanır:
- normalize: biçim farkları (Türkçe harf, boşluk/noktalama, MRK öneki,
  tarih ayırıcı, boş = '-' = null, boş firma = varsayılan firma) yok sayılır.
- birebir: uygulamanın ekranda/Excel'de göstereceği metin aynen aynı mı.
"""

from __future__ import annotations

import argparse
import glob
import hashlib
import json
import random
import re
import time
from collections import defaultdict
from pathlib import Path

from excel_ogrenme import arama_anahtari
from extract_json import IMG_EXT, resmi_oku
from normalizasyon import VARSAYILAN_FIRMA


ROOT = Path(__file__).resolve().parent
FIELDS = ("fis_no", "tarih", "firma", "plaka", "ad_soyad", "km", "litre")
SET_SEED = 20261018
BOS_ANAHTARLAR = {"", "NULL", "NONE", "YOK", "BOS"}


def image_index(images_dir: Path) -> dict[str, Path]:
    return {
        yol.name: yol for yol in images_dir.rglob("*")
        if yol.is_file() and yol.suffix.lower() in IMG_EXT
    }


def select_sample(limit: int, data_file: Path) -> list[dict]:
    rows = json.loads(data_file.read_text(encoding="utf-8"))
    rows = [
        r for r in rows if r.get("_onaylandi") and r.get("_ham")
        and not r.get("_silindi")
    ]
    equipment = [r for r in rows if r.get("_ekipman_durumu")]
    vehicles = [r for r in rows if not r.get("_ekipman_durumu")]
    rng = random.Random(20260910)
    rng.shuffle(equipment)
    rng.shuffle(vehicles)
    equipment_count = min(len(equipment), max(1, limit // 4))
    sample = equipment[:equipment_count] + vehicles[: limit - equipment_count]
    rng.shuffle(sample)
    return sample


# --- Normalize karşılaştırma -------------------------------------------------

def _anahtar(deger) -> str:
    anahtar = arama_anahtari(deger)
    return "" if anahtar in BOS_ANAHTARLAR else anahtar


def normalize(alan: str, deger) -> str:
    """Biçim farklarını yok sayan karşılaştırma anahtarı."""
    anahtar = _anahtar(deger)
    if alan == "fis_no":
        rakam = re.sub(r"\D", "", anahtar)
        return str(int(rakam)) if rakam else anahtar
    if alan == "tarih":
        parcalar = re.findall(r"\d+", anahtar)
        if len(parcalar) == 3:
            gun, ay, yil = (int(p) for p in parcalar)
            return f"{gun}/{ay}/{yil + 2000 if yil < 100 else yil}"
        return anahtar
    if alan == "firma":
        return anahtar or arama_anahtari(VARSAYILAN_FIRMA)
    if alan == "plaka":
        sade = anahtar.replace(" ", "")
        return f"34{sade}" if re.fullmatch(r"[A-Z]{1,3}\d{2,5}", sade) else sade
    if alan == "ad_soyad":
        return anahtar.replace(" ", "")
    if alan == "km":
        return str(int(anahtar)) if anahtar.isdigit() else anahtar.replace(" ", "")
    if alan == "litre":
        metin = str(deger or "").strip().replace(",", ".").split("=")[-1]
        try:
            return f"{float(metin):g}"
        except ValueError:
            return re.sub(r"\s+", "", metin).upper()
    return anahtar


def same(left, right, alan: str | None = None) -> bool:
    if alan is None:
        return arama_anahtari(left) == arama_anahtari(right)
    return normalize(alan, left) == normalize(alan, right)


def birebir(left, right) -> bool:
    return str(left if left is not None else "-").strip() == \
        str(right if right is not None else "-").strip()


# --- Sabit test seti ---------------------------------------------------------

def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def onayli_kayitlar(root: Path = ROOT) -> list[dict]:
    """sonuclar.json + arsiv/**/sonuclar.json içindeki onaylı, silinmemiş kayıtlar."""
    dosyalar = [root / "sonuclar.json"] + [
        Path(p) for p in sorted(glob.glob(str(root / "arsiv" / "**" / "sonuclar.json"), recursive=True))
    ]
    kayitlar, gorulen = [], set()
    for dosya in dosyalar:
        if not dosya.exists():
            continue
        for kayit in json.loads(dosya.read_text(encoding="utf-8")):
            if not kayit.get("_onaylandi") or kayit.get("_silindi"):
                continue
            anahtar = kayit.get("_dosya_hash") or kayit.get("_dosya")
            if anahtar in gorulen:
                continue
            gorulen.add(anahtar)
            kayit["_kaynak_dosya"] = dosya.relative_to(root).as_posix()
            kayitlar.append(kayit)
    return kayitlar


def set_olustur(boyut: int, root: Path = ROOT, seed: int = SET_SEED) -> dict:
    """Ekipman/araç ve parti oranını koruyan, sabit tohumlu test seti."""
    adlar = defaultdict(list)
    for klasor in ("fisler", "arsiv"):
        for yol in (root / klasor).rglob("*"):
            if yol.is_file() and yol.suffix.lower() in IMG_EXT:
                adlar[yol.name].append(yol)
    hash_onbellek: dict[Path, str] = {}

    def resmi_bul(kayit: dict) -> Path | None:
        adaylar = adlar.get(kayit.get("_dosya"), [])
        beklenen = kayit.get("_dosya_hash")
        if beklenen:
            for yol in adaylar:
                if yol not in hash_onbellek:
                    hash_onbellek[yol] = _sha256(yol)
                if hash_onbellek[yol] == beklenen:
                    return yol
            return None
        return adaylar[0] if len(adaylar) == 1 else None

    gruplar = defaultdict(list)
    eksik = 0
    for kayit in onayli_kayitlar(root):
        yol = resmi_bul(kayit)
        if not yol:
            eksik += 1
            continue
        kayit["_yol"] = yol
        tur = "ekipman" if kayit.get("_ekipman_durumu") else "arac"
        gruplar[(tur, kayit["_kaynak_dosya"])].append(kayit)

    toplam = sum(len(v) for v in gruplar.values())
    rng = random.Random(seed)
    # En büyük kalan yöntemiyle orantılı kota
    anahtarlar = sorted(gruplar)
    kesin = {a: len(gruplar[a]) * boyut / toplam for a in anahtarlar}
    kota = {a: int(kesin[a]) for a in anahtarlar}
    for a in sorted(anahtarlar, key=lambda a: kesin[a] - kota[a], reverse=True)[
        : boyut - sum(kota.values())
    ]:
        kota[a] += 1

    secilen = []
    for a in anahtarlar:
        aday = sorted(gruplar[a], key=lambda k: k.get("_dosya_hash") or k["_dosya"])
        rng.shuffle(aday)
        secilen.extend(aday[: kota[a]])
    secilen.sort(key=lambda k: k.get("_dosya_hash") or k["_dosya"])

    return {
        "aciklama": "Sabit OCR test seti; kişisel veri içerir, repoya konmaz.",
        "seed": seed,
        "havuz": toplam,
        "resmi_bulunamayan": eksik,
        "dagilim": {f"{t}|{k}": kota[(t, k)] for t, k in anahtarlar},
        "fisler": [
            {
                "id": (k.get("_dosya_hash") or _sha256(k["_yol"]))[:16],
                "dosya": k["_dosya"],
                "yol": k["_yol"].relative_to(root).as_posix(),
                "sha256": k.get("_dosya_hash"),
                "kaynak": k["_kaynak_dosya"],
                "ekipman": bool(k.get("_ekipman_durumu")),
                "etiket": {alan: k.get(alan) for alan in FIELDS},
            }
            for k in secilen
        ],
    }


# --- Puanlama ----------------------------------------------------------------

def puanla(fis: dict, tahmin: dict) -> dict:
    etiket = fis["etiket"]
    alanlar = {
        alan: {
            "ok": same(tahmin.get(alan), etiket.get(alan), alan),
            "birebir": birebir(tahmin.get(alan), etiket.get(alan)),
            "beklenen": etiket.get(alan),
            "okunan": tahmin.get(alan),
        }
        for alan in FIELDS
    }
    return {
        "id": fis.get("id"),
        "dosya": fis.get("dosya"),
        "ekipman": fis.get("ekipman"),
        "sure_sn": tahmin.get("_sure_sn"),
        "hata": tahmin.get("HATA"),
        "alanlar": alanlar,
    }


def ozet(results: list[dict]) -> dict:
    n = len(results)
    if not n:
        return {"ornek": 0}

    def yuzde(sayi):
        return round(sayi / n * 100, 1)

    alan_norm = {a: yuzde(sum(r["alanlar"][a]["ok"] for r in results)) for a in FIELDS}
    alan_birebir = {a: yuzde(sum(r["alanlar"][a]["birebir"] for r in results)) for a in FIELDS}
    sureler = [float(r["sure_sn"]) for r in results if r.get("sure_sn") is not None]
    tam = [all(d["ok"] for d in r["alanlar"].values()) for r in results]
    return {
        "ornek": n,
        "alan_dogrulugu": round(sum(alan_norm.values()) / len(FIELDS), 1),
        "alan_dogrulugu_birebir": round(sum(alan_birebir.values()) / len(FIELDS), 1),
        "alan_bazinda": alan_norm,
        "alan_bazinda_birebir": alan_birebir,
        "tam_dogru_fis": yuzde(sum(tam)),
        "tam_dogru_fis_birebir": yuzde(
            sum(all(d["birebir"] for d in r["alanlar"].values()) for r in results)
        ),
        "tam_dogru_ekipman": _oran([t for t, r in zip(tam, results) if r.get("ekipman")]),
        "tam_dogru_arac": _oran([t for t, r in zip(tam, results) if not r.get("ekipman")]),
        "fis_no_hata": sum(not r["alanlar"]["fis_no"]["ok"] for r in results),
        "litre_hata": sum(not r["alanlar"]["litre"]["ok"] for r in results),
        "okuma_hatasi": sum(bool(r.get("hata")) for r in results),
        "ortalama_sure_sn": round(sum(sureler) / len(sureler), 2) if sureler else None,
    }


def _oran(degerler: list[bool]) -> float | None:
    return round(sum(degerler) / len(degerler) * 100, 1) if degerler else None


def resim_yolu(fis: dict, root: Path = ROOT, _adlar: dict = {}) -> Path | None:
    """Setteki fişin resmini bulur. Parti arşivlenince dosyalar taşındığı için
    kayıtlı yol yoksa aynı adlı dosyalar arasında SHA-256 ile aranır."""
    yol = root / fis["yol"]
    if yol.exists():
        return yol
    if not _adlar:
        for klasor in ("fisler", "arsiv"):
            for aday in (root / klasor).rglob("*"):
                if aday.is_file() and aday.suffix.lower() in IMG_EXT:
                    _adlar.setdefault(aday.name, []).append(aday)
    for aday in _adlar.get(fis["dosya"], []):
        if not fis.get("sha256") or _sha256(aday) == fis["sha256"]:
            return aday
    return None


def run_set(
    set_file: Path, model: str | None = None, max_edge: int = 1600,
    tahmin_file: Path | None = None, limit: int | None = None,
) -> dict:
    test_set = json.loads(set_file.read_text(encoding="utf-8"))
    fisler = test_set["fisler"][:limit] if limit else test_set["fisler"]
    tahminler = (
        json.loads(tahmin_file.read_text(encoding="utf-8")) if tahmin_file else None
    )
    results = []
    started = time.perf_counter()
    for number, fis in enumerate(fisler, 1):
        if tahminler is not None:
            tahmin = tahminler.get(fis["id"])
            if tahmin is None:
                tahmin = {"HATA": "tahmin yok"}
        else:
            print(f"[{number}/{len(fisler)}] {fis['id']} · {model} · {max_edge}px", flush=True)
            try:
                yol = resim_yolu(fis)
                if not yol:
                    raise FileNotFoundError(fis["dosya"])
                tahmin = resmi_oku(yol, model=model, max_kenar=max_edge)
            except Exception as exc:  # okuma hatası yanlış sayılır, ölçüm sürer
                tahmin = {"HATA": str(exc)}
        results.append(puanla(fis, tahmin))
    return {
        "model": model or (tahmin_file.name if tahmin_file else None),
        "max_kenar": max_edge if tahminler is None else None,
        "set": set_file.name,
        **ozet(results),
        "toplam_sure_sn": round(time.perf_counter() - started, 1),
        "sonuclar": results,
    }


def run(
    model: str, max_edge: int, limit: int,
    data_file: Path | None = None, images_dir: Path | None = None,
) -> dict:
    data_file = data_file or ROOT / "sonuclar.json"
    images_dir = images_dir or ROOT / "fisler"
    images = image_index(images_dir)
    sample = select_sample(limit, data_file)
    results = []
    started = time.perf_counter()
    for number, truth in enumerate(sample, 1):
        path = images.get(truth.get("_dosya"))
        if not path:
            continue
        print(f"[{number}/{len(sample)}] {path.name} · {model} · {max_edge}px", flush=True)
        prediction = resmi_oku(path, model=model, max_kenar=max_edge)
        fields = {
            field: {
                "ok": same(prediction.get(field), truth.get(field)),
                "beklenen": truth.get(field),
                "okunan": prediction.get(field),
            }
            for field in FIELDS
        }
        results.append({
            "dosya": path.name,
            "sure_sn": prediction.get("_sure_sn"),
            "alanlar": fields,
        })
    total_fields = len(results) * len(FIELDS)
    correct = sum(
        int(detail["ok"]) for result in results for detail in result["alanlar"].values()
    )
    return {
        "model": model,
        "max_kenar": max_edge,
        "ornek": len(results),
        "alan_dogrulugu": round(correct / total_fields * 100, 1) if total_fields else 0,
        "ortalama_sure_sn": round(
            sum(float(r["sure_sn"] or 0) for r in results) / len(results), 2
        ) if results else 0,
        "toplam_sure_sn": round(time.perf_counter() - started, 1),
        "sonuclar": results,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model")
    parser.add_argument("--max-kenar", type=int, default=1600)
    parser.add_argument("--ornek", type=int, default=8)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--data-file", type=Path, default=ROOT / "sonuclar.json")
    parser.add_argument("--images-dir", type=Path, default=ROOT / "fisler")
    parser.add_argument("--set-olustur", action="store_true",
                        help="onaylı kayıtlardan sabit test seti üret")
    parser.add_argument("--set-boyut", type=int, default=100)
    parser.add_argument("--set", type=Path, help="sabit test seti JSON dosyası")
    parser.add_argument("--tahminler", type=Path,
                        help="{id: alanlar} biçiminde dışarıda üretilmiş tahminler")
    parser.add_argument("--limit", type=int, help="setin ilk N fişi (deneme için)")
    args = parser.parse_args()

    if args.set_olustur:
        test_set = set_olustur(args.set_boyut)
        output = args.output or ROOT / "benchmark_testseti.json"
        output.write_text(json.dumps(test_set, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps({k: v for k, v in test_set.items() if k != "fisler"},
                         ensure_ascii=False, indent=2))
        print(f"Test seti: {output} ({len(test_set['fisler'])} fiş)")
        return

    if args.set:
        if not (args.model or args.tahminler):
            parser.error("--set ile --model veya --tahminler gerekli")
        report = run_set(args.set, args.model, args.max_kenar, args.tahminler, args.limit)
    else:
        if not args.model:
            parser.error("--model gerekli")
        report = run(
            args.model, args.max_kenar, args.ornek,
            data_file=args.data_file, images_dir=args.images_dir,
        )
    output = args.output or ROOT / "benchmark_sonucu.json"
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in report.items() if k != "sonuclar"}, ensure_ascii=False, indent=2))
    print(f"Rapor: {output}")


if __name__ == "__main__":
    main()
