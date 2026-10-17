# -*- mode: python ; coding: utf-8 -*-


a = Analysis(
    ['uygulama.py'],
    pathex=[],
    binaries=[],
    datas=[('ui', 'ui')],
    hiddenimports=["otomatik_duzeltme"],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # Calisma zamaninda kullanilmayan agir paketler (yalniz deney betikleri kullanir)
    excludes=['numpy', 'cv2', 'tkinter', 'transformers', 'torch'],
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
