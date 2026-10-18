"""Uzun süreli çalışma (soak) testi: günlerce süren iş akışını tek süreçte koşar.

Her simüle gün: N fotoğraf gerçek OCR işçisi iş parçacığından geçer (OCR
sahte, gecikmeli ve belirli oranda hata veren), hatalılar güvenle yeniden
denenir, hepsi onaylanır, Excel defterine aktarılır, parti kapatılır ve
günlük tam yedek alınır. Her günün sonunda kuyruk, bellek, açık dosya,
iş parçacığı, disk kullanımı ve işlem süreleri ölçülür.

    python -m tools.uzun_sure_testi --gun 30 --gunluk 200 --cikti soak.json

Gerçek veriye dokunmaz: her şey --klasor (varsayılan geçici klasör) içinde.
Geçme ölçütleri (çıkış kodu 1 = başarısız):
- her gün kuyruk süre sınırında boşalır, 'işleniyor' kayıt kalmaz;
- 3. günden sonra Python bellek kullanımı (gc sonrası) gün başına artmaz;
- açık dosya ve iş parçacığı sayısı artmaz, yarım (.tmp) dosya kalmaz;
- yedek klasörü verinin 1,5 katını aşmaz;
- gün içi kayıt/yükleme süreleri günden güne büyümez (aktif parti küçük kalır).
"""

from __future__ import annotations

import argparse
import gc
import io
import json
import os
import queue
import random
import shutil
import statistics
import sys
import tempfile
import threading
import time
import tracemalloc
from contextlib import redirect_stdout
from pathlib import Path

from openpyxl import Workbook


def _rss_mb() -> float | None:
    try:
        import psutil  # type: ignore
        return psutil.Process().memory_info().rss / 1024 / 1024
    except ImportError:
        pass
    if sys.platform.startswith("win"):
        try:
            import ctypes
            from ctypes import wintypes

            class Sayac(ctypes.Structure):
                _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD)] + [
                    (ad, ctypes.c_size_t) for ad in (
                        "PeakWorkingSetSize", "WorkingSetSize", "QuotaPeakPagedPoolUsage",
                        "QuotaPagedPoolUsage", "QuotaPeakNonPagedPoolUsage",
                        "QuotaNonPagedPoolUsage", "PagefileUsage", "PeakPagefileUsage")]
            sayac = Sayac()
            sayac.cb = ctypes.sizeof(Sayac)
            surec = ctypes.windll.kernel32.GetCurrentProcess()
            ctypes.windll.psapi.GetProcessMemoryInfo(surec, ctypes.byref(sayac), sayac.cb)
            return sayac.WorkingSetSize / 1024 / 1024
        except Exception:
            return None
    try:
        import resource
        # Linux'ta KB, macOS'ta bayt; bu tepe değerdir (anlık değil).
        tepe = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        return tepe / 1024 / 1024 if sys.platform == "darwin" else tepe / 1024
    except Exception:
        return None


def _acik_dosya() -> int | None:
    try:
        import psutil  # type: ignore
        surec = psutil.Process()
        return surec.num_handles() if sys.platform.startswith("win") else surec.num_fds()
    except ImportError:
        pass
    for yol in ("/proc/self/fd", "/dev/fd"):
        if os.path.isdir(yol):
            return len(os.listdir(yol))
    return None


def _boyut(yol: Path) -> int:
    return sum(p.stat().st_size for p in yol.rglob("*") if p.is_file()) if yol.exists() else 0


def _gorsel(rastgele: random.Random, kb: int) -> bytes:
    return b"\xff\xd8\xff\xe0JFIF" + rastgele.randbytes(kb * 1024) + b"\xff\xd9"


def calistir(gun: int, gunluk: int, kok: Path, gorsel_kb: int = 40,
             ocr_gecikme_sn: float = 0.002, hata_orani: float = 0.03,
             tohum: int = 7, kuyruk_sinir_sn: float = 120.0,
             yazdir: bool = True) -> dict:
    from mazot import (alan_yeniden_okuma, arayuz, denetim, erisim, excel_aktar,
                       excel_ogrenme, parti_yonetimi)

    # Aynı süreçteki başka kodu (ör. test paketi) etkilememek için sonunda geri alınır.
    eski = [(m, a, getattr(m, a)) for m, adlar in (
        (arayuz, ("ROOT", "DATA_FILE", "FISLER_DIR", "GELEN_DIR", "OCR_QUEUE", "resmi_oku")),
        (excel_aktar, ("ROOT", "DATA_FILE", "AYAR_FILE", "ARAMA_KLASORLERI")),
        (parti_yonetimi, ("ROOT", "DATA_FILE", "FISLER_DIR", "ARSIV_DIR", "AKTIF_META_FILE",
                          "ISLEM_GUNLUGU")),
        (denetim, ("DENETIM_DIR",)), (erisim, ("KULLANICI_FILE",)),
        (excel_ogrenme, ("guvenli_otomatik_duzelt",)),
        (alan_yeniden_okuma, ("yeniden_oku_ve_duzelt",)),
    ) for a in adlar]
    eski_hash = dict(arayuz.IMAGE_HASHES)
    try:
        return _calistir(gun, gunluk, kok, gorsel_kb, ocr_gecikme_sn, hata_orani,
                         tohum, kuyruk_sinir_sn, yazdir)
    finally:
        for modul, ad, deger in eski:
            setattr(modul, ad, deger)
        arayuz.IMAGE_HASHES.clear()
        arayuz.IMAGE_HASHES.update(eski_hash)


def _calistir(gun, gunluk, kok, gorsel_kb, ocr_gecikme_sn, hata_orani, tohum,
              kuyruk_sinir_sn, yazdir) -> dict:
    from mazot import (alan_yeniden_okuma, arayuz, denetim, erisim, excel_aktar,
                       excel_ogrenme, parti_yonetimi, yedekleme)

    kok = Path(kok).resolve()

    kok.mkdir(parents=True, exist_ok=True)
    arayuz.ROOT, arayuz.DATA_FILE, arayuz.FISLER_DIR = kok, kok / "sonuclar.json", kok / "fisler"
    arayuz.GELEN_DIR = kok / "gelen_fisler"
    arayuz.FISLER_DIR.mkdir(exist_ok=True)
    excel_aktar.ROOT, excel_aktar.DATA_FILE = kok, kok / "sonuclar.json"
    excel_aktar.AYAR_FILE = kok / "output" / ".excel_ayar.json"
    excel_aktar.ARAMA_KLASORLERI = [kok / "output", kok]
    parti_yonetimi.yollari_ayarla(kok, kok / "sonuclar.json", kok / "fisler")
    denetim.yolu_ayarla(kok)
    erisim.yolu_ayarla(kok)
    arayuz.IMAGE_HASHES.clear()
    arayuz.OCR_QUEUE = queue.Queue()

    rastgele = random.Random(tohum)
    sayac = {"fis": 100000}
    kilit = threading.Lock()

    def sahte_ocr(yol: Path) -> dict:
        time.sleep(ocr_gecikme_sn)
        with kilit:
            if rastgele.random() < hata_orani:
                raise TimeoutError("sentetik OCR zaman aşımı")
            sayac["fis"] += 1
            numara = sayac["fis"]
        return {"_dosya": yol.name, "fis_no": str(numara), "tarih": "05.05.2026",
                "firma": "Selim İnşaat", "plaka": "34 ABC 123", "ad_soyad": "Ali Veli",
                "km": str(10000 + numara % 5000), "litre": f"{20 + numara % 60},5"}

    arayuz.resmi_oku = sahte_ocr
    excel_ogrenme.guvenli_otomatik_duzelt = lambda kayit: None
    alan_yeniden_okuma.yeniden_oku_ve_duzelt = lambda *a: None

    defter = kok / "output" / "defter.xlsx"
    defter.parent.mkdir(exist_ok=True)
    wb = Workbook()
    ws = wb.active
    ws.title = "2026 MAZOT"
    ws.append(["TARİH", "FATURA NO", "FİRMA", "PLAKA", "AD", "KM", None, "LİTRE"])
    ws.append(["01.05.2026", 1, "X", "34 X 1", "Y", 10, None, 5])
    wb.save(defter)

    threading.Thread(target=arayuz.ocr_worker, args=(arayuz.OCR_QUEUE,),
                     name="ocr-worker", daemon=True).start()
    tracemalloc.start()
    kim = {"ad": "soak", "rol": "yonetici"}
    gunler: list[dict] = []
    toplam_aktarilan = 0

    def kuyrugu_bekle() -> float:
        bas = time.monotonic()
        while arayuz.OCR_QUEUE.unfinished_tasks:
            if time.monotonic() - bas > kuyruk_sinir_sn:
                raise TimeoutError("OCR kuyruğu süre sınırında boşalmadı")
            time.sleep(0.01)
        return time.monotonic() - bas

    for g in range(1, gun + 1):
        gun_bas = time.monotonic()
        yukleme_sureleri = []
        sessiz = io.StringIO()
        with redirect_stdout(sessiz):
            for _ in range(gunluk):
                t = time.perf_counter()
                arayuz.fotografi_kabul_et(_gorsel(rastgele, gorsel_kb), ".jpg", kim=kim)
                yukleme_sureleri.append(time.perf_counter() - t)
            kuyruk_sn = kuyrugu_bekle()

            # Başarısızlar görünür ve güvenle yeniden denenir.
            hatali = tekrar = 0
            for _deneme in range(5):
                with arayuz.DATA_LOCK:
                    kayitlar = arayuz.load_receipts()
                    bekleyen = [k for k in kayitlar if k.get("HATA") and not k.get("_silindi")]
                    for k in bekleyen:
                        yol = arayuz.find_image(k["_dosya"])
                        if yol is None:
                            raise RuntimeError(f"Gün {g}: hatalı fişin görseli kayıp: {k['_dosya']}")
                        k.pop("HATA")
                        k["_isleniyor"] = True
                        arayuz.OCR_QUEUE.put((yol, k["_dosya_hash"]))
                    if bekleyen:
                        arayuz.save_receipts(kayitlar)
                if not bekleyen:
                    break
                hatali += len(bekleyen) if _deneme == 0 else 0
                tekrar += len(bekleyen)
                kuyruk_sn += kuyrugu_bekle()

            # Onay (handle_save'in yaptığı gibi firma klasörüne taşıyarak).
            t = time.perf_counter()
            with arayuz.DATA_LOCK:
                kayitlar = arayuz.load_receipts()
                for k in kayitlar:
                    k["_onaylandi"] = True
                    k["_onaylayan"] = "soak"
                    arayuz.firma_klasorune_tasi(k)
                arayuz.save_receipts(kayitlar)
            onay_sn = time.perf_counter() - t

            t = time.perf_counter()
            with arayuz.DATA_LOCK:
                aktarim = excel_aktar.aktar(defter, "2026 MAZOT", kullanici="soak")
            aktarim_sn = time.perf_counter() - t
            if "hata" in aktarim:
                raise RuntimeError(f"Gün {g} aktarım hatası: {aktarim['hata']}")
            toplam_aktarilan += aktarim["aktarilan"]

            t = time.perf_counter()
            with arayuz.DATA_LOCK:
                kalan = [k for k in arayuz.load_receipts() if k.get("_isleniyor") or k.get("HATA")]
                parti_yonetimi.yeni_parti(f"Gün {g + 1}")
                arayuz.hash_indeksini_hazirla()
            parti_sn = time.perf_counter() - t

            t = time.perf_counter()
            yedek = yedekleme.anlik_yedek_al(kok, f"gun{g:03d}")
            yedekleme.eski_anliklari_temizle(kok, tutulacak=yedekleme.VARSAYILAN_TUTULACAK)
            yedek_sn = time.perf_counter() - t

        gc.collect()
        mevcut, tepe = tracemalloc.get_traced_memory()
        veri_bayt = _boyut(kok) - _boyut(kok / "yedekler")
        olcum = {
            "gun": g,
            "sure_sn": round(time.monotonic() - gun_bas, 2),
            "kuyruk_bosalma_sn": round(kuyruk_sn, 3),
            "kalan_islenmemis": len(kalan),
            "ocr_hatasi": hatali, "tekrar_deneme": tekrar,
            "aktarilan": aktarim["aktarilan"],
            "defter_satir": aktarim["son_satir"],
            "yukleme_ms_medyan": round(statistics.median(yukleme_sureleri) * 1000, 2),
            "yukleme_ms_p95": round(sorted(yukleme_sureleri)[int(len(yukleme_sureleri) * .95) - 1] * 1000, 2),
            "onay_ms": round(onay_sn * 1000, 1), "aktarim_ms": round(aktarim_sn * 1000, 1),
            "parti_kapatma_ms": round(parti_sn * 1000, 1), "yedek_ms": round(yedek_sn * 1000, 1),
            "yedek_yeni_mb": round(yedek["yeni_bayt"] / 1024 / 1024, 2),
            "yedek_dosya": yedek["dosya_sayisi"], "yedek_okunan": yedek["okunan_dosya"],
            "py_bellek_mb": round(mevcut / 1024 / 1024, 2),
            "py_bellek_tepe_mb": round(tepe / 1024 / 1024, 2),
            # macOS/Linux'ta tepe RSS (ru_maxrss), Windows'ta anlık çalışma kümesi.
            "rss_mb": (round(_rss_mb(), 1) if _rss_mb() is not None else None),
            "acik_dosya": _acik_dosya(),
            "is_parcacigi": threading.active_count(),
            "hash_indeksi": len(arayuz.IMAGE_HASHES),
            "veri_mb": round(veri_bayt / 1024 / 1024, 2),
            "yedek_mb": round(_boyut(kok / "yedekler") / 1024 / 1024, 2),
            "denetim_mb": round(_boyut(kok / "denetim") / 1024 / 1024, 3),
            "yarim_tmp": len([p for p in kok.rglob("*.tmp")]),
        }
        tracemalloc.reset_peak()
        gunler.append(olcum)
        if yazdir:
            print(f"gün {g:3d}: {olcum['sure_sn']:6.1f} sn · kuyruk {olcum['kuyruk_bosalma_sn']:5.2f} sn"
                  f" · hata/tekrar {hatali}/{tekrar} · bellek {olcum['py_bellek_mb']:5.1f} MB"
                  f" · veri {olcum['veri_mb']:7.1f} MB · yedek {olcum['yedek_mb']:7.1f} MB"
                  f" · dosya {olcum['acik_dosya']} · iş {olcum['is_parcacigi']}", flush=True)

    tracemalloc.stop()
    return {"parametreler": {"gun": gun, "gunluk": gunluk, "gorsel_kb": gorsel_kb,
                             "hata_orani": hata_orani, "ocr_gecikme_sn": ocr_gecikme_sn},
            "gunler": gunler, "toplam_aktarilan": toplam_aktarilan,
            "kontroller": degerlendir(gunler, gun * gunluk, toplam_aktarilan)}


def _egim(degerler: list[float]) -> float:
    """En küçük kareler doğrusunun gün başına eğimi."""
    n = len(degerler)
    if n < 2:
        return 0.0
    ort_x, ort_y = (n - 1) / 2, statistics.fmean(degerler)
    pay = sum((i - ort_x) * (y - ort_y) for i, y in enumerate(degerler))
    payda = sum((i - ort_x) ** 2 for i in range(n))
    return pay / payda


def degerlendir(gunler: list[dict], beklenen: int, aktarilan: int) -> dict:
    isinmis = gunler[2:] if len(gunler) > 4 else gunler[1:]
    kontroller = {
        "tum_fisler_aktarildi": aktarilan == beklenen,
        "islenmemis_kalmadi": all(g["kalan_islenmemis"] == 0 for g in gunler),
        "yarim_dosya_yok": all(g["yarim_tmp"] == 0 for g in gunler),
        # Gün başına 0,5 MB'den hızlı büyüyen Python belleği sızıntı sayılır.
        "bellek_sabit": _egim([g["py_bellek_mb"] for g in isinmis]) < 0.5,
        "acik_dosya_sabit": len({g["acik_dosya"] for g in isinmis}) <= 2
        if all(g["acik_dosya"] is not None for g in isinmis) else None,
        "is_parcacigi_sabit": len({g["is_parcacigi"] for g in gunler}) == 1,
        "yedek_veriyle_orantili": all(g["yedek_mb"] <= 1.5 * g["veri_mb"] + 1 for g in gunler),
        # Gün içi işlemler veri büyüdükçe yavaşlamamalı (aktif parti hep küçük).
        "yukleme_suresi_sabit": isinmis[-1]["yukleme_ms_medyan"]
        <= 3 * max(isinmis[0]["yukleme_ms_medyan"], 1.0),
        # xlsx her aktarımda baştan yazılır; süre defter boyutuyla orantılı
        # büyür. Satır başına süre sabit kalmalı (gizli ikinci büyüme olmamalı).
        "aktarim_satir_basina_sabit": isinmis[-1]["aktarim_ms"] / isinmis[-1]["defter_satir"]
        <= 2 * max(isinmis[0]["aktarim_ms"] / isinmis[0]["defter_satir"], 0.05),
        # Tam anlık yedek her dosyayı listeler; dosya başına süre sabit kalmalı
        # ve yalnız yeni/değişen dosyalar okunmalı.
        "yedek_dosya_basina_sabit": isinmis[-1]["yedek_ms"] / isinmis[-1]["yedek_dosya"]
        <= 2 * max(isinmis[0]["yedek_ms"] / isinmis[0]["yedek_dosya"], 0.5),
        "yedek_yalniz_yenileri_okur": all(g["yedek_okunan"] < g["yedek_dosya"] / 2 for g in isinmis),
    }
    kontroller["gecti"] = all(v is not False for v in kontroller.values())
    return kontroller


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--gun", type=int, default=30)
    parser.add_argument("--gunluk", type=int, default=200)
    parser.add_argument("--gorsel-kb", type=int, default=40)
    parser.add_argument("--hata-orani", type=float, default=0.03)
    parser.add_argument("--ocr-gecikme", type=float, default=0.002)
    parser.add_argument("--klasor", type=Path, help="çalışma klasörü (varsayılan geçici)")
    parser.add_argument("--cikti", type=Path)
    args = parser.parse_args()
    gecici = None
    kok = args.klasor
    if kok is None:
        gecici = tempfile.mkdtemp(prefix="mazot_soak_")
        kok = Path(gecici)
    try:
        rapor = calistir(args.gun, args.gunluk, kok / "data", args.gorsel_kb,
                         args.ocr_gecikme, args.hata_orani)
    finally:
        if gecici:
            shutil.rmtree(gecici, ignore_errors=True)
    print(json.dumps(rapor["kontroller"], ensure_ascii=False, indent=2))
    if args.cikti:
        args.cikti.write_text(json.dumps(rapor, ensure_ascii=False, indent=2), encoding="utf-8")
    raise SystemExit(0 if rapor["kontroller"]["gecti"] else 1)


if __name__ == "__main__":
    main()
