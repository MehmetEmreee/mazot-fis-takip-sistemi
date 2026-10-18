"""Fiş gruplarını geri alınabilir partiler halinde arşivler ve listeler."""

from __future__ import annotations

import json
import hashlib
import os
import re
import shutil
import uuid
from datetime import datetime
from pathlib import Path

from . import yollar


ROOT = yollar.VERI_DIZINI  # değişen veriler (data/)
DATA_FILE = ROOT / "sonuclar.json"
FISLER_DIR = ROOT / "fisler"
ARSIV_DIR = ROOT / "arsiv"
AKTIF_META_FILE = ROOT / "aktif_parti.json"
# Parti kapatma sürerken var olan işlem günlüğü; süreç yarıda kesilirse
# başlangıçta yarim_parti_islemini_kurtar() buna bakarak tamamlar/geri alır.
ISLEM_GUNLUGU = ROOT / ".parti_islemi.json"


class PartiIslemHatasi(OSError):
    pass


def yollari_ayarla(root: Path, data_file: Path | None = None, fisler_dir: Path | None = None) -> None:
    global ROOT, DATA_FILE, FISLER_DIR, ARSIV_DIR, AKTIF_META_FILE, ISLEM_GUNLUGU
    ROOT = Path(root).resolve()
    DATA_FILE = Path(data_file or ROOT / "sonuclar.json").resolve()
    FISLER_DIR = Path(fisler_dir or ROOT / "fisler").resolve()
    ARSIV_DIR = ROOT / "arsiv"
    AKTIF_META_FILE = ROOT / "aktif_parti.json"
    ISLEM_GUNLUGU = ROOT / ".parti_islemi.json"


def _json_oku(yol: Path, varsayilan):
    if not yol.is_file():
        return varsayilan
    try:
        return json.loads(yol.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return varsayilan


def _json_yaz(yol: Path, veri) -> None:
    yol.parent.mkdir(parents=True, exist_ok=True)
    gecici = yol.with_name(f".{yol.name}.{uuid.uuid4().hex}.tmp")
    gecici.write_text(json.dumps(veri, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(gecici, yol)


def _ozet(kayitlar: list[dict]) -> dict:
    gorunen = [k for k in kayitlar if not k.get("_silindi")]
    return {
        "toplam": len(gorunen),
        "onayli": sum(bool(k.get("_onaylandi")) for k in gorunen),
        "bekleyen": sum(not k.get("_onaylandi") for k in gorunen),
        "isleniyor": sum(bool(k.get("_isleniyor")) for k in gorunen),
        "excel_aktarilan": sum(bool(k.get("_excel_aktarildi")) for k in gorunen),
    }


def arsiv_manifesti_yaz(klasor: Path) -> dict:
    """Arşivin eksiksizliğini sonradan doğrulamak için boyut/hash manifesti."""
    data_file = klasor / "sonuclar.json"
    fisler_dir = klasor / "fisler"
    kayitlar = _json_oku(data_file, [])
    dosyalar = []
    adlar = set()
    for yol in sorted(p for p in fisler_dir.rglob("*") if p.is_file()):
        ozet = hashlib.sha256()
        with yol.open("rb") as dosya:
            for parca in iter(lambda: dosya.read(1024 * 1024), b""):
                ozet.update(parca)
        adlar.add(yol.name)
        dosyalar.append({
            "yol": str(yol.relative_to(klasor)),
            "boyut": yol.stat().st_size,
            "sha256": ozet.hexdigest(),
        })
    eksik = sorted({
        str(k.get("_dosya")) for k in kayitlar
        if k.get("_dosya") and str(k.get("_dosya")) not in adlar
    })
    manifest = {
        "olusturma_zamani": datetime.now().isoformat(timespec="seconds"),
        "kayit_sayisi": len(kayitlar),
        "gorsel_sayisi": len(dosyalar),
        "toplam_boyut": sum(d["boyut"] for d in dosyalar),
        "eksik_gorseller": eksik,
        "sonuclar_sha256": hashlib.sha256(data_file.read_bytes()).hexdigest(),
        "dosyalar": dosyalar,
    }
    _json_yaz(klasor / "manifest.json", manifest)
    return manifest


def arsiv_manifesti_dogrula(klasor: Path) -> dict:
    """Manifestteki her görseli ve sonuç dosyasını yeniden hashleyip doğrular."""
    manifest = _json_oku(klasor / "manifest.json", {})
    hatalar = []
    data_file = klasor / "sonuclar.json"
    if not data_file.is_file():
        hatalar.append("sonuclar.json eksik")
    elif hashlib.sha256(data_file.read_bytes()).hexdigest() != manifest.get("sonuclar_sha256"):
        hatalar.append("sonuclar.json özeti değişmiş")
    for bilgi in manifest.get("dosyalar", []):
        yol = klasor / bilgi.get("yol", "")
        if not yol.is_file():
            hatalar.append(f"eksik: {bilgi.get('yol')}")
            continue
        ozet = hashlib.sha256()
        with yol.open("rb") as dosya:
            for parca in iter(lambda: dosya.read(1024 * 1024), b""):
                ozet.update(parca)
        if yol.stat().st_size != bilgi.get("boyut") or ozet.hexdigest() != bilgi.get("sha256"):
            hatalar.append(f"değişmiş: {bilgi.get('yol')}")
    return {"saglam": not hatalar, "hatalar": hatalar, "kontrol_edilen": len(manifest.get("dosyalar", []))}


def _temiz_ad(ad: str) -> str:
    ad = re.sub(r"\s+", " ", str(ad or "").strip())
    return ad[:80] or f"Parti {datetime.now():%d.%m.%Y %H:%M}"


def _klasor_eki(ad: str) -> str:
    sade = re.sub(r"[^A-Za-z0-9]+", "-", ad).strip("-").lower()
    return sade[:32] or "fisler"


def aktif_parti() -> dict:
    meta = _json_oku(AKTIF_META_FILE, {})
    if not isinstance(meta, dict) or not meta.get("id"):
        meta = {
            "id": "aktif",
            "ad": f"Parti {datetime.now():%d.%m.%Y}",
            "olusturma_zamani": datetime.now().isoformat(timespec="seconds"),
            "aktif": True,
        }
        _json_yaz(AKTIF_META_FILE, meta)
    meta = dict(meta)
    meta.update(_ozet(_json_oku(DATA_FILE, [])))
    meta["id"] = "aktif"
    meta["aktif"] = True
    meta["salt_okunur"] = False
    return meta


def _arsiv_bilgisi(klasor: Path) -> dict | None:
    data_file = klasor / "sonuclar.json"
    if not data_file.is_file():
        return None
    kayitlar = _json_oku(data_file, [])
    meta = _json_oku(klasor / "parti.json", {})
    if not isinstance(meta, dict):
        meta = {}
    bilgi = {
        "id": klasor.name,
        "ad": meta.get("ad") or klasor.name.replace("_", " "),
        "olusturma_zamani": meta.get("olusturma_zamani"),
        "kapanma_zamani": meta.get("kapanma_zamani") or datetime.fromtimestamp(
            data_file.stat().st_mtime
        ).isoformat(timespec="seconds"),
        "aktif": False,
        "salt_okunur": True,
        "excel": meta.get("excel"),
    }
    bilgi.update(_ozet(kayitlar if isinstance(kayitlar, list) else []))
    return bilgi


def partileri_listele() -> list[dict]:
    sonuc = [aktif_parti()]
    if ARSIV_DIR.is_dir():
        arsivler = []
        for klasor in ARSIV_DIR.iterdir():
            if klasor.is_dir():
                bilgi = _arsiv_bilgisi(klasor)
                if bilgi:
                    arsivler.append(bilgi)
        arsivler.sort(key=lambda p: p.get("kapanma_zamani") or "", reverse=True)
        sonuc.extend(arsivler)
    return sonuc


def arsiv_klasoru(parti_id: str) -> Path | None:
    if not isinstance(parti_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]+", parti_id):
        return None
    aday = (ARSIV_DIR / parti_id).resolve()
    try:
        aday.relative_to(ARSIV_DIR.resolve())
    except ValueError:
        return None
    return aday if (aday / "sonuclar.json").is_file() else None


def parti_yollari(parti_id: str | None) -> tuple[Path, Path, bool]:
    if not parti_id or parti_id == "aktif":
        return DATA_FILE, FISLER_DIR, False
    klasor = arsiv_klasoru(parti_id)
    if not klasor:
        raise LookupError("Parti bulunamadı")
    return klasor / "sonuclar.json", klasor / "fisler", True


def _dosya_ozeti(yol: Path) -> str:
    ozet = hashlib.sha256()
    with yol.open("rb") as dosya:
        for parca in iter(lambda: dosya.read(1024 * 1024), b""):
            ozet.update(parca)
    return ozet.hexdigest()


def _klasoru_geri_birlestir(kaynak: Path, hedef: Path) -> list[str]:
    """kaynak'taki dosyaları hedef'e geri taşır. Hedefte aynı içerikli dosya
    varsa kaynaktaki fazlalık silinir; farklı içerikli dosyanın üzerine
    yazılmaz, çakışma olarak döndürülür. (Farklı disklerde shutil.move
    kopyala+sil yaptığı için kesinti iki tarafta da dosya bırakabilir.)"""
    if not hedef.exists():
        shutil.move(str(kaynak), str(hedef))
        return []
    cakisan = []
    for yol in sorted(p for p in kaynak.rglob("*") if p.is_file()):
        goreli = yol.relative_to(kaynak)
        karsi = hedef / goreli
        if not karsi.exists():
            karsi.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(yol), str(karsi))
        elif karsi.is_file() and karsi.stat().st_size == yol.stat().st_size \
                and _dosya_ozeti(karsi) == _dosya_ozeti(yol):
            yol.unlink()
        else:
            cakisan.append(str(yol))
    for klasor in sorted((p for p in kaynak.rglob("*") if p.is_dir()), reverse=True):
        try:
            klasor.rmdir()
        except OSError:
            pass
    try:
        kaynak.rmdir()
    except OSError:
        pass
    return cakisan


def _arsivi_geri_al(hedef: Path) -> list[str]:
    """Yarım kalmış arşivlemeyi geri alır; çakışan dosyaları döndürür."""
    cakisan = []
    hedef_data = hedef / "sonuclar.json"
    if hedef_data.exists():
        if not DATA_FILE.exists():
            os.replace(hedef_data, DATA_FILE)
        elif DATA_FILE.read_bytes() == hedef_data.read_bytes():
            hedef_data.unlink()
        else:
            cakisan.append(str(hedef_data))
    hedef_fisler = hedef / "fisler"
    if hedef_fisler.exists():
        cakisan += _klasoru_geri_birlestir(hedef_fisler, FISLER_DIR)
    if not cakisan and hedef.is_dir():
        for ad in ("parti.json", "manifest.json"):
            (hedef / ad).unlink(missing_ok=True)
        for gecici in hedef.glob(".*.tmp"):  # yarıda kesilmiş _json_yaz
            gecici.unlink(missing_ok=True)
        try:
            hedef.rmdir()
        except OSError:
            pass
    return cakisan


def _yeni_aktifi_baslat(yeni: dict) -> None:
    """Arşivleme bittikten sonra boş aktif partiyi açar; tekrar çağrılabilir."""
    FISLER_DIR.mkdir(parents=True, exist_ok=True)
    if not DATA_FILE.exists():
        _json_yaz(DATA_FILE, [])
    _json_yaz(AKTIF_META_FILE, yeni)
    ISLEM_GUNLUGU.unlink(missing_ok=True)


def yarim_parti_islemini_kurtar() -> dict | None:
    """Süreç parti kapatırken kesildiyse diskteki durumu uzlaştırır.

    Arşivleme tamamlanmışsa (aşama 'arsivlendi') yeni aktif parti açılarak
    işlem ileri tamamlanır; değilse taşınan veri ve görseller aktif partiye
    geri alınır. Çelişen dosyaların üzerine yazılmaz: günlük korunur ve
    'cakisma' döner, yeni parti kapatma o zamana kadar reddedilir.
    """
    for ad in (ISLEM_GUNLUGU.name, AKTIF_META_FILE.name):
        for gecici in ROOT.glob(f".{ad}.*.tmp"):  # yarıda kesilmiş _json_yaz
            gecici.unlink(missing_ok=True)
    gunluk = _json_oku(ISLEM_GUNLUGU, None)
    if gunluk is None:
        if ISLEM_GUNLUGU.exists():
            return {"durum": "cakisma", "cakisan": [str(ISLEM_GUNLUGU)],
                    "mesaj": "Parti işlem günlüğü okunamadı"}
        return None
    hedef = Path(gunluk.get("hedef") or "")
    if gunluk.get("asama") == "arsivlendi" and (hedef / "sonuclar.json").is_file():
        _yeni_aktifi_baslat(gunluk["yeni"])
        return {"durum": "tamamlandi", "arsiv": hedef.name}
    cakisan = _arsivi_geri_al(hedef) if gunluk.get("hedef") else []
    if cakisan:
        return {"durum": "cakisma", "cakisan": cakisan,
                "mesaj": "Yarım kalan parti kapatma geri alınamadı; çelişen dosyalar var"}
    ISLEM_GUNLUGU.unlink(missing_ok=True)
    return {"durum": "geri_alindi", "arsiv": hedef.name}


def yeni_parti(acilacak_ad: str, excel_sonucu: dict | None = None) -> dict:
    """Aktif partiyi arşivler ve boş bir aktif parti açar.

    Excel aktarımı bu fonksiyondan önce yapılır. Taşımadan önce işlem günlüğü
    yazılır: hata halinde adımlar burada, süreç kesilirse başlangıçta
    yarim_parti_islemini_kurtar() ile geri alınır ya da tamamlanır.
    """
    if ISLEM_GUNLUGU.exists():
        kurtarma = yarim_parti_islemini_kurtar()
        if kurtarma and kurtarma["durum"] == "cakisma":
            raise PartiIslemHatasi(
                f"{kurtarma['mesaj']}: {', '.join(kurtarma['cakisan'][:5])}"
            )
    acilacak_ad = _temiz_ad(acilacak_ad)
    onceki = aktif_parti()
    kayitlar = _json_oku(DATA_FILE, [])
    arsivlenen = None
    yeni = {
        "id": "aktif",
        "ad": acilacak_ad,
        "olusturma_zamani": datetime.now().isoformat(timespec="seconds"),
        "aktif": True,
    }

    if kayitlar:
        zaman = datetime.now()
        taban = f"parti_{zaman:%Y%m%d_%H%M%S}_{_klasor_eki(onceki['ad'])}"
        ARSIV_DIR.mkdir(parents=True, exist_ok=True)
        hedef = ARSIV_DIR / taban
        sayac = 2
        while hedef.exists():
            hedef = ARSIV_DIR / f"{taban}_{sayac}"
            sayac += 1
        gunluk = {"hedef": str(hedef), "asama": "basladi", "yeni": yeni,
                  "zaman": zaman.isoformat(timespec="seconds")}
        _json_yaz(ISLEM_GUNLUGU, gunluk)
        try:
            hedef.mkdir()
            os.replace(DATA_FILE, hedef / "sonuclar.json")
            if FISLER_DIR.exists():
                shutil.move(str(FISLER_DIR), str(hedef / "fisler"))
            meta = {
                "id": hedef.name,
                "ad": onceki["ad"],
                "olusturma_zamani": onceki.get("olusturma_zamani"),
                "kapanma_zamani": zaman.isoformat(timespec="seconds"),
                "excel": excel_sonucu,
            }
            _json_yaz(hedef / "parti.json", meta)
            manifest = arsiv_manifesti_yaz(hedef)
            if manifest["eksik_gorseller"]:
                raise OSError(
                    f"Arşiv bütünlük kontrolü başarısız: "
                    f"{len(manifest['eksik_gorseller'])} görsel eksik"
                )
            arsivlenen = {**meta, **_ozet(kayitlar)}
        except Exception:
            if not _arsivi_geri_al(hedef):
                ISLEM_GUNLUGU.unlink(missing_ok=True)
            raise
        _json_yaz(ISLEM_GUNLUGU, {**gunluk, "asama": "arsivlendi"})

    _yeni_aktifi_baslat(yeni)
    return {"aktif": aktif_parti(), "arsivlenen": arsivlenen}
