"""Veri klasörünün artımlı tam yedeği, doğrulaması ve geri yüklemesi.

Yedek içerik adreslidir: her dosya SHA-256 özetiyle yedekler/tam/nesneler/
altında bir kez saklanır, her anlık yedek yalnızca bir manifesttir
(yol → özet). Aynı fiş görseli yüzlerce yedekte yer kaplamaz; disk kullanımı
yaklaşık verinin kendisi kadardır. Eski anlık yedekler silinince artık hiçbir
manifestin kullanmadığı nesneler de temizlenir.

Kullanım (geri yükleme sırasında program KAPALI olmalı):
    python -m mazot.yedekleme listele
    python -m mazot.yedekleme al
    python -m mazot.yedekleme dogrula [<yedek kimliği>]
    python -m mazot.yedekleme geri-yukle <yedek kimliği>

Geri yükleme önce yedeği baştan sona doğrular, mevcut veriyi silmez:
yedekler/geri_yukleme_oncesi_<zaman>/ klasörüne taşır. Herhangi bir adım
başarısız olursa mevcut veri yerine geri konur.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import socket
import sys
import uuid
from datetime import datetime, timedelta
from pathlib import Path

from . import yollar

YEDEK_KLASORU = "yedekler"
TAM_YEDEK = Path(YEDEK_KLASORU) / "tam"
# Yedeğe girmeyenler: yedeklerin kendisi, teşhis kırpıntıları ve gelen
# kutusunun zaten fiş olarak alınmış/elenmiş kopyaları.
HARIC_UST = {YEDEK_KLASORU, "crops"}
HARIC_GELEN_ALT = {"_islendi", "_tekrar"}
VARSAYILAN_TUTULACAK = 14
PARCA = 1024 * 1024


class YedekHatasi(RuntimeError):
    pass


def _tam_kok(kok: Path) -> Path:
    return Path(kok) / TAM_YEDEK


def _yedeklenecek_mi(goreli: Path) -> bool:
    parcalar = goreli.parts
    if not parcalar or parcalar[0] in HARIC_UST:
        return False
    if parcalar[0] == "gelen_fisler" and len(parcalar) > 2 and parcalar[1].lower() in HARIC_GELEN_ALT:
        return False
    ad = parcalar[-1]
    return not (ad.endswith(".tmp") or ad.startswith("~$"))


def _nesne_yolu(tam: Path, ozet: str) -> Path:
    return tam / "nesneler" / ozet[:2] / ozet


def _dosya_ozeti(yol: Path) -> str:
    ozet = hashlib.sha256()
    with yol.open("rb") as dosya:
        for parca in iter(lambda: dosya.read(PARCA), b""):
            ozet.update(parca)
    return ozet.hexdigest()


def _nesneye_kopyala(kaynak: Path, tam: Path) -> tuple[str, int, bool]:
    """Dosyayı okurken özetler; özet daha önce yoksa nesne olarak saklar.
    Saklanan içerik ile özet aynı okumadan geldiği için tutarlıdır."""
    gecici = tam / "nesneler" / f".{uuid.uuid4().hex}.tmp"
    gecici.parent.mkdir(parents=True, exist_ok=True)
    ozet = hashlib.sha256()
    boyut = 0
    try:
        with kaynak.open("rb") as giris, gecici.open("wb") as cikis:
            for parca in iter(lambda: giris.read(PARCA), b""):
                ozet.update(parca)
                cikis.write(parca)
                boyut += len(parca)
            cikis.flush()
            os.fsync(cikis.fileno())
        hedef = _nesne_yolu(tam, ozet.hexdigest())
        if hedef.exists():
            return ozet.hexdigest(), boyut, False
        hedef.parent.mkdir(parents=True, exist_ok=True)
        os.replace(gecici, hedef)
        return ozet.hexdigest(), boyut, True
    finally:
        gecici.unlink(missing_ok=True)


def _json_yaz(yol: Path, veri) -> None:
    yol.parent.mkdir(parents=True, exist_ok=True)
    gecici = yol.with_name(f".{yol.name}.{uuid.uuid4().hex}.tmp")
    with gecici.open("w", encoding="utf-8") as dosya:
        json.dump(veri, dosya, ensure_ascii=False, indent=1)
        dosya.flush()
        os.fsync(dosya.fileno())
    os.replace(gecici, yol)


def anlik_yedek_al(kok: Path, etiket: str = "") -> dict:
    """Veri klasörünün tam anlık yedeğini alır; özetini döndürür.

    Önceki anlık yedekte aynı yol, boyut ve değişiklik zamanıyla bulunan ve
    nesnesi duran dosyalar yeniden okunmaz (her gün gigabaytlarca arşivi
    baştan özetlememek için). Doğrulama ve geri yükleme her zaman içerikle
    yapılır.
    """
    kok = Path(kok).resolve()
    tam = _tam_kok(kok)
    zaman = datetime.now()
    kimlik = f"{zaman:%Y%m%d_%H%M%S}" + (f"_{etiket}" if etiket else "")
    onceki: dict[str, dict] = {}
    try:
        onceki = {d["yol"]: d for d in _manifest(kok, None)["dosyalar"] if "mtime_ns" in d}
    except YedekHatasi:
        pass
    dosyalar, yeni_bayt, yeni_nesne, okunan = [], 0, 0, 0
    for yol in sorted(kok.rglob("*")):
        goreli = yol.relative_to(kok)
        if not yol.is_file() or not _yedeklenecek_mi(goreli):
            continue
        try:
            bilgi = yol.stat()
            eski = onceki.get(goreli.as_posix())
            if (eski and eski["boyut"] == bilgi.st_size and eski["mtime_ns"] == bilgi.st_mtime_ns
                    and _nesne_yolu(tam, eski["sha256"]).is_file()):
                ozet, boyut, yeni = eski["sha256"], eski["boyut"], False
            else:
                ozet, boyut, yeni = _nesneye_kopyala(yol, tam)
                okunan += 1
        except FileNotFoundError:
            continue  # tarama ile kopyalama arasında silinmiş
        dosyalar.append({"yol": goreli.as_posix(), "boyut": boyut, "sha256": ozet,
                         "mtime_ns": bilgi.st_mtime_ns})
        if yeni:
            yeni_bayt += boyut
            yeni_nesne += 1
    manifest = {
        "kimlik": kimlik,
        "olusturma_zamani": zaman.isoformat(timespec="seconds"),
        "kaynak": str(kok),
        "dosya_sayisi": len(dosyalar),
        "toplam_boyut": sum(d["boyut"] for d in dosyalar),
        "dosyalar": dosyalar,
    }
    _json_yaz(tam / "anliklar" / f"{kimlik}.json", manifest)
    return {"kimlik": kimlik, "dosya_sayisi": len(dosyalar),
            "toplam_boyut": manifest["toplam_boyut"], "okunan_dosya": okunan,
            "yeni_nesne": yeni_nesne, "yeni_bayt": yeni_bayt}


def anliklari_listele(kok: Path) -> list[dict]:
    klasor = _tam_kok(kok) / "anliklar"
    sonuc = []
    for yol in sorted(klasor.glob("*.json")) if klasor.is_dir() else []:
        try:
            veri = json.loads(yol.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            sonuc.append({"kimlik": yol.stem, "bozuk": True})
            continue
        sonuc.append({k: veri.get(k) for k in (
            "kimlik", "olusturma_zamani", "dosya_sayisi", "toplam_boyut")})
    return sonuc


def _manifest(kok: Path, kimlik: str | None) -> dict:
    """Verilen (yoksa en yeni okunabilen) anlık yedeğin manifesti."""
    klasor = _tam_kok(kok) / "anliklar"
    if kimlik is None:
        for yol in sorted(klasor.glob("*.json"), reverse=True) if klasor.is_dir() else []:
            try:
                return json.loads(yol.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
        raise YedekHatasi("Hiç anlık yedek yok")
    yol = klasor / f"{Path(kimlik).name}.json"
    try:
        return json.loads(yol.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise YedekHatasi(f"Yedek manifesti okunamadı: {kimlik} ({exc})") from exc


def yedegi_dogrula(kok: Path, kimlik: str | None = None) -> dict:
    """Manifestteki her nesneyi yeniden özetleyerek doğrular."""
    manifest = _manifest(kok, kimlik)
    tam = _tam_kok(kok)
    hatalar = []
    for bilgi in manifest["dosyalar"]:
        nesne = _nesne_yolu(tam, bilgi["sha256"])
        if not nesne.is_file():
            hatalar.append(f"eksik: {bilgi['yol']}")
        elif nesne.stat().st_size != bilgi["boyut"] or _dosya_ozeti(nesne) != bilgi["sha256"]:
            hatalar.append(f"bozuk: {bilgi['yol']}")
        if not _yedeklenecek_mi(Path(bilgi["yol"])) or ".." in Path(bilgi["yol"]).parts \
                or Path(bilgi["yol"]).is_absolute():
            hatalar.append(f"geçersiz yol: {bilgi['yol']}")
    return {"kimlik": manifest["kimlik"], "saglam": not hatalar, "hatalar": hatalar,
            "kontrol_edilen": len(manifest["dosyalar"])}


def program_calisiyor_mu(port: int = 8765) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", port)) == 0


def geri_yukle(kok: Path, kimlik: str | None = None) -> dict:
    """Anlık yedeği veri klasörüne eksiksiz geri yükler (program kapalıyken).

    Mevcut veri silinmez, yedekler/geri_yukleme_oncesi_<zaman>/ altına taşınır.
    Her geri yazılan dosya yeniden özetlenir; bir hata olursa mevcut veri
    yerine konur ve YedekHatasi fırlatılır.
    """
    kok = Path(kok).resolve()
    dogrulama = yedegi_dogrula(kok, kimlik)
    if not dogrulama["saglam"]:
        raise YedekHatasi(f"Yedek sağlam değil, geri yüklenmedi: {dogrulama['hatalar'][:5]}")
    manifest = _manifest(kok, dogrulama["kimlik"])
    tam = _tam_kok(kok)
    zaman = datetime.now()
    kenar = kok / YEDEK_KLASORU / f"geri_yukleme_oncesi_{zaman:%Y%m%d_%H%M%S}"
    kenar.mkdir(parents=True)

    tasinan: list[str] = []
    yazilan: list[Path] = []
    try:
        # 1) Yedeğe giren her şeyi kenara al (aynı diskte taşıma, kopya değil).
        for oge in sorted(kok.iterdir()):
            if oge.name in HARIC_UST:
                continue
            if oge.name == "gelen_fisler":
                # Kullanıcının işlenmiş gelen kutusu kopyaları yerinde kalır.
                for alt in sorted(oge.iterdir()):
                    if alt.name.lower() in HARIC_GELEN_ALT:
                        continue
                    (kenar / "gelen_fisler").mkdir(exist_ok=True)
                    os.replace(alt, kenar / "gelen_fisler" / alt.name)
                    tasinan.append(f"gelen_fisler/{alt.name}")
                continue
            os.replace(oge, kenar / oge.name)
            tasinan.append(oge.name)
        # 2) Yedekteki dosyaları yaz ve tek tek doğrula.
        for bilgi in manifest["dosyalar"]:
            hedef = kok / Path(bilgi["yol"])
            hedef.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(_nesne_yolu(tam, bilgi["sha256"]), hedef)
            yazilan.append(hedef)
            if _dosya_ozeti(hedef) != bilgi["sha256"]:
                raise YedekHatasi(f"Geri yazılan dosya doğrulanamadı: {bilgi['yol']}")
    except BaseException:
        for yol in reversed(yazilan):
            yol.unlink(missing_ok=True)
        for goreli in reversed(tasinan):
            hedef = kok / goreli
            if hedef.is_dir():
                shutil.rmtree(hedef)
            hedef.parent.mkdir(parents=True, exist_ok=True)
            os.replace(kenar / goreli, hedef)
        raise
    rapor = {
        "kimlik": manifest["kimlik"], "geri_yuklenen": len(yazilan),
        "toplam_boyut": manifest["toplam_boyut"], "onceki_veri": str(kenar),
        "zaman": zaman.isoformat(timespec="seconds"),
    }
    _json_yaz(kenar / "geri_yukleme_raporu.json", rapor)
    return rapor


def eski_anliklari_temizle(kok: Path, tutulacak: int = VARSAYILAN_TUTULACAK) -> dict:
    """En yeni `tutulacak` anlık yedek kalır; hiçbir manifestin kullanmadığı
    nesneler silinir. Bozuk/okunamayan manifest varsa nesne silinmez."""
    tam = _tam_kok(kok)
    klasor = tam / "anliklar"
    manifestler: dict[Path, dict | None] = {}
    for yol in sorted(klasor.glob("*.json")) if klasor.is_dir() else []:
        try:
            manifestler[yol] = json.loads(yol.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            manifestler[yol] = None
    silinen = []
    saglam = [yol for yol, veri in manifestler.items() if veri is not None]
    for yol in saglam[:-tutulacak] if tutulacak > 0 else []:
        yol.unlink(missing_ok=True)
        silinen.append(yol.stem)
        manifestler.pop(yol)
    if any(veri is None for veri in manifestler.values()):
        return {"silinen_anlik": silinen, "silinen_nesne": 0, "not": "bozuk manifest var"}
    kullanilan = set()
    for veri in manifestler.values():
        kullanilan.update(d["sha256"] for d in veri["dosyalar"])
    silinen_nesne = 0
    nesneler = tam / "nesneler"
    for nesne in nesneler.rglob("*") if nesneler.is_dir() else []:
        if nesne.is_file() and (nesne.name not in kullanilan or nesne.name.endswith(".tmp")):
            nesne.unlink()
            silinen_nesne += 1
    return {"silinen_anlik": silinen, "silinen_nesne": silinen_nesne}


def eski_dosyalari_temizle(klasor: Path, desen: str, gun: int) -> list[str]:
    """Otomatik yedek klasörlerinde `gun`den eski dosyaları siler."""
    if not klasor.is_dir():
        return []
    sinir = (datetime.now() - timedelta(days=gun)).timestamp()
    silinen = []
    for yol in klasor.glob(desen):
        if yol.is_file() and yol.stat().st_mtime < sinir:
            yol.unlink()
            silinen.append(yol.name)
    return silinen


def gunluk_yedek(kok: Path, tutulacak: int = VARSAYILAN_TUTULACAK) -> dict | None:
    """Bugün anlık yedek yoksa alır ve saklama kuralını uygular."""
    bugun = f"{datetime.now():%Y%m%d}_"
    if any(str(a.get("kimlik", "")).startswith(bugun) for a in anliklari_listele(kok)):
        return None
    sonuc = anlik_yedek_al(kok)
    sonuc["temizlik"] = eski_anliklari_temizle(kok, tutulacak)
    kok = Path(kok)
    eski_dosyalari_temizle(kok / YEDEK_KLASORU / "excel_otomatik", "*_aktarim_oncesi.*", 90)
    eski_dosyalari_temizle(kok / YEDEK_KLASORU / "veri_otomatik", "veri_*.zip", 30)
    return sonuc


def _mb(bayt: int) -> str:
    return f"{bayt / 1024 / 1024:,.1f} MB"


def main() -> None:
    kok = yollar.VERI_DIZINI
    komut = sys.argv[1:] or ["listele"]
    try:
        if komut[0] == "listele":
            for anlik in anliklari_listele(kok):
                if anlik.get("bozuk"):
                    print(f"  {anlik['kimlik']}: BOZUK MANİFEST")
                else:
                    print(f"  {anlik['kimlik']}  {anlik['dosya_sayisi']} dosya  "
                          f"{_mb(anlik['toplam_boyut'])}")
        elif komut[0] == "al":
            sonuc = anlik_yedek_al(kok)
            print(f"Yedek {sonuc['kimlik']}: {sonuc['dosya_sayisi']} dosya, "
                  f"{_mb(sonuc['toplam_boyut'])} (yeni: {_mb(sonuc['yeni_bayt'])})")
        elif komut[0] == "dogrula":
            sonuc = yedegi_dogrula(kok, komut[1] if len(komut) > 1 else None)
            print(f"{sonuc['kimlik']}: {'SAĞLAM' if sonuc['saglam'] else 'HATALI'} "
                  f"({sonuc['kontrol_edilen']} dosya)")
            for hata in sonuc["hatalar"][:20]:
                print(f"  {hata}")
            raise SystemExit(0 if sonuc["saglam"] else 1)
        elif komut[0] == "geri-yukle" and len(komut) == 2:
            if program_calisiyor_mu():
                raise YedekHatasi("Program açık; geri yüklemeden önce kapatın")
            sonuc = geri_yukle(kok, komut[1])
            from . import denetim
            denetim.yolu_ayarla(kok)
            denetim.kaydet("geri_yukle", {"ad": os.environ.get("USERNAME") or os.environ.get("USER")
                                          or "komut-satiri", "rol": "yonetici"},
                           yedek=sonuc["kimlik"], onceki_veri=sonuc["onceki_veri"])
            print(f"{sonuc['kimlik']} geri yüklendi: {sonuc['geri_yuklenen']} dosya. "
                  f"Önceki veri: {sonuc['onceki_veri']}")
        else:
            print(__doc__)
            raise SystemExit(2)
    except YedekHatasi as exc:
        print(f"HATA: {exc}")
        raise SystemExit(1)


if __name__ == "__main__":
    main()
