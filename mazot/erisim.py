"""Kullanıcılar, roller, oturumlar ve telefon yükleme anahtarı.

Kurallar:
- Hiç kullanıcı tanımlı değilse (tek kullanıcılı kurulum) programın çalıştığı
  bilgisayardan gelen istekler "yerel" yönetici sayılır; ağdan gelen istekler
  yalnızca telefon yükleme anahtarıyla ve yalnızca fotoğraf yüklemek için
  kabul edilir.
- Kullanıcı tanımlıysa herkes (bu bilgisayar dahil) adı ve parolasıyla girer;
  böylece her değişiklik bir kişiye bağlanır.

Kullanıcı yönetimi (program kapalıyken veya açıkken):
    python -m mazot.erisim ekle <ad> <rol>      # parola sorulur
    python -m mazot.erisim sil <ad>
    python -m mazot.erisim listele
    python -m mazot.erisim anahtar-yenile        # telefon bağlantısını değiştirir
"""

from __future__ import annotations

import getpass
import hashlib
import hmac
import json
import os
import re
import secrets
import sys
import threading
import time
import uuid
from pathlib import Path

from . import yollar

KULLANICI_FILE = yollar.VERI_DIZINI / "kullanicilar.json"

ROLLER = {
    "yonetici": {"goruntule", "yukle", "duzenle", "aktar", "yonet"},
    "kontrol": {"goruntule", "yukle", "duzenle", "aktar"},
    "yukleme": {"yukle"},
}
OTURUM_COOKIE = "mazot_oturum"
YUKLEME_COOKIE = "mazot_yukleme"
OTURUM_SURESI_SN = 12 * 3600
PBKDF2_TUR = 200_000
YEREL_ADRESLER = {"127.0.0.1", "::1", "::ffff:127.0.0.1"}
# Parola denemesi sınırı: aynı adresten bu kadar hatalı girişten sonra bekletilir.
HATALI_GIRIS_SINIRI = 5
HATALI_GIRIS_BEKLEME_SN = 60

_KILIT = threading.Lock()
_OTURUMLAR: dict[str, dict] = {}
_HATALI: dict[str, list[float]] = {}


class ErisimHatasi(ValueError):
    pass


def yolu_ayarla(kok: Path) -> None:
    global KULLANICI_FILE
    KULLANICI_FILE = Path(kok) / "kullanicilar.json"
    with _KILIT:
        _OTURUMLAR.clear()
        _HATALI.clear()


def _oku() -> dict:
    try:
        veri = json.loads(KULLANICI_FILE.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {"kullanicilar": {}}
    except (OSError, json.JSONDecodeError) as exc:
        # Bozuk dosyada sessizce "kullanıcı yok" (herkese açık) moda düşme.
        raise ErisimHatasi(f"kullanicilar.json okunamadı: {exc}") from exc
    veri.setdefault("kullanicilar", {})
    return veri


def _yaz(veri: dict) -> None:
    KULLANICI_FILE.parent.mkdir(parents=True, exist_ok=True)
    gecici = KULLANICI_FILE.with_name(f".{KULLANICI_FILE.name}.{uuid.uuid4().hex}.tmp")
    gecici.write_text(json.dumps(veri, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(gecici, KULLANICI_FILE)


def _ozet(parola: str, tuz: str, tur: int = PBKDF2_TUR) -> str:
    return hashlib.pbkdf2_hmac("sha256", parola.encode("utf-8"),
                               bytes.fromhex(tuz), tur).hex()


def kullanici_var_mi() -> bool:
    return bool(_oku()["kullanicilar"])


def kullanici_ekle(ad: str, parola: str, rol: str) -> None:
    ad = str(ad or "").strip()
    if not re.fullmatch(r"[\w.-]{2,32}", ad):
        raise ErisimHatasi("Kullanıcı adı 2-32 harf/rakam olmalı")
    if rol not in ROLLER:
        raise ErisimHatasi(f"Rol şunlardan biri olmalı: {', '.join(ROLLER)}")
    if len(parola or "") < 6:
        raise ErisimHatasi("Parola en az 6 karakter olmalı")
    with _KILIT:
        veri = _oku()
        tuz = secrets.token_hex(16)
        veri["kullanicilar"][ad] = {
            "rol": rol, "tuz": tuz, "tur": PBKDF2_TUR, "ozet": _ozet(parola, tuz),
        }
        _yaz(veri)
        for anahtar in [k for k, o in _OTURUMLAR.items() if o["ad"] == ad]:
            _OTURUMLAR.pop(anahtar, None)


def kullanici_sil(ad: str) -> None:
    with _KILIT:
        veri = _oku()
        veri["kullanicilar"].pop(ad, None)
        _yaz(veri)
        for anahtar in [k for k, o in _OTURUMLAR.items() if o["ad"] == ad]:
            _OTURUMLAR.pop(anahtar, None)


def yukleme_anahtari(yenile: bool = False) -> str:
    """Telefon sayfası bağlantısına eklenen kalıcı, tahmin edilemez anahtar."""
    with _KILIT:
        veri = _oku()
        if yenile or not veri.get("yukleme_anahtari"):
            veri["yukleme_anahtari"] = secrets.token_urlsafe(18)
            _yaz(veri)
        return veri["yukleme_anahtari"]


def giris_yap(ad: str, parola: str, istemci: str) -> str:
    """Doğruysa oturum anahtarı döndürür; değilse ErisimHatasi."""
    simdi = time.monotonic()
    with _KILIT:
        denemeler = [t for t in _HATALI.get(istemci, []) if simdi - t < HATALI_GIRIS_BEKLEME_SN]
        _HATALI[istemci] = denemeler
        if len(denemeler) >= HATALI_GIRIS_SINIRI:
            raise ErisimHatasi("Çok fazla hatalı giriş; bir dakika sonra tekrar deneyin")
    bilgi = _oku()["kullanicilar"].get(str(ad or ""))
    dogru = bool(bilgi) and hmac.compare_digest(
        _ozet(str(parola or ""), bilgi["tuz"], bilgi.get("tur", PBKDF2_TUR)), bilgi["ozet"])
    with _KILIT:
        if not dogru:
            _HATALI.setdefault(istemci, []).append(simdi)
            raise ErisimHatasi("Kullanıcı adı veya parola hatalı")
        _HATALI.pop(istemci, None)
        anahtar = secrets.token_urlsafe(32)
        _OTURUMLAR[anahtar] = {"ad": ad, "rol": bilgi["rol"], "son": time.time()}
        return anahtar


def cikis_yap(anahtar: str | None) -> None:
    with _KILIT:
        _OTURUMLAR.pop(anahtar or "", None)


def cerezleri_ayristir(baslik: str | None) -> dict[str, str]:
    cerezler = {}
    for parca in (baslik or "").split(";"):
        if "=" in parca:
            ad, deger = parca.split("=", 1)
            cerezler[ad.strip()] = deger.strip()
    return cerezler


def kimlik(cerez_basligi: str | None, istemci: str) -> dict | None:
    """İsteği yapanı belirler: {"ad", "rol"} ya da None (yetkisiz)."""
    cerezler = cerezleri_ayristir(cerez_basligi)
    oturum = cerezler.get(OTURUM_COOKIE)
    if oturum:
        with _KILIT:
            bilgi = _OTURUMLAR.get(oturum)
            if bilgi and time.time() - bilgi["son"] < OTURUM_SURESI_SN:
                bilgi["son"] = time.time()
                return {"ad": bilgi["ad"], "rol": bilgi["rol"]}
            _OTURUMLAR.pop(oturum, None)
    veri = _oku()
    anahtar = cerezler.get(YUKLEME_COOKIE)
    if anahtar and veri.get("yukleme_anahtari") and hmac.compare_digest(
            anahtar, veri["yukleme_anahtari"]):
        return {"ad": "telefon", "rol": "yukleme"}
    if not veri["kullanicilar"] and istemci in YEREL_ADRESLER:
        return {"ad": "yerel", "rol": "yonetici"}
    return None


def yetkili_mi(kim: dict | None, yetki: str) -> bool:
    return bool(kim) and yetki in ROLLER.get(kim["rol"], set())


def main() -> None:
    komut = sys.argv[1:] or ["listele"]
    try:
        if komut[0] == "ekle" and len(komut) == 3:
            parola = getpass.getpass(f"{komut[1]} için parola: ")
            if parola != getpass.getpass("Parola (tekrar): "):
                raise ErisimHatasi("Parolalar aynı değil")
            kullanici_ekle(komut[1], parola, komut[2])
            print(f"{komut[1]} ({komut[2]}) kaydedildi.")
        elif komut[0] == "sil" and len(komut) == 2:
            kullanici_sil(komut[1])
            print(f"{komut[1]} silindi.")
        elif komut[0] == "anahtar-yenile":
            yukleme_anahtari(yenile=True)
            print("Telefon yükleme anahtarı yenilendi; eski bağlantı artık çalışmaz.")
        elif komut[0] == "listele":
            kullanicilar = _oku()["kullanicilar"]
            if not kullanicilar:
                print("Kullanıcı yok: yalnızca bu bilgisayardan erişilebilir (tek kullanıcı modu).")
            for ad, bilgi in sorted(kullanicilar.items()):
                print(f"  {ad}: {bilgi['rol']}")
        else:
            print(__doc__)
            raise SystemExit(2)
    except ErisimHatasi as exc:
        print(f"HATA: {exc}")
        raise SystemExit(1)


if __name__ == "__main__":
    main()
