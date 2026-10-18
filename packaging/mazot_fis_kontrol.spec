# -*- mode: python ; coding: utf-8 -*-
# Windows EXE: depo kokunden `scripts\windows\EXE_OLUSTUR.bat` ile olusturulur.
import os

KOK = os.path.abspath(os.path.join(SPECPATH, ".."))

a = Analysis(
    [os.path.join(KOK, "mazot", "__main__.py")],
    pathex=[KOK],
    binaries=[],
    datas=[(os.path.join(KOK, "mazot", "ui"), os.path.join("mazot", "ui"))],
    # Fonksiyon icinde (tembel) ice aktarilan moduller ve OpenCV
    hiddenimports=["mazot.otomatik_duzeltme", "mazot.alan_yeniden_okuma", "cv2", "numpy"],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # Calisma zamaninda kullanilmayan agir paketler (yalniz deney betikleri kullanir).
    # numpy/cv2 artik gerekli: alan_yeniden_okuma tablo kutularini OpenCV ile bulur.
    excludes=['tkinter', 'transformers', 'torch'],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='Mazot Fis Kontrol',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
