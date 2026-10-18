#!/bin/bash
# Ortak yardimci: .venv yoksa olusturur, requirements.txt degistiyse yeniden kurar.
# BASLAT.command ve KURULUM.command tarafindan depo kokunde "source" edilir.

venv_hazirla() {
  local isaret=".venv/.requirements.kurulu"
  if [ ! -x ".venv/bin/python" ]; then
    echo "Python ortami olusturuluyor (.venv)..."
    if command -v uv >/dev/null 2>&1; then
      uv venv --python 3.12 .venv || uv venv .venv || return 1
    elif command -v python3 >/dev/null 2>&1; then
      python3 -m venv .venv || return 1
    else
      echo "HATA: Ne 'uv' ne de 'python3' bulundu."
      echo "Python 3 kurun (https://www.python.org/downloads/) veya: brew install uv"
      return 1
    fi
    rm -f "$isaret"
  fi
  if [ ! -f "$isaret" ] || ! cmp -s requirements.txt "$isaret"; then
    echo "Gerekli Python paketleri kuruluyor..."
    if command -v uv >/dev/null 2>&1; then
      uv pip install --python .venv/bin/python -r requirements.txt || return 1
    else
      .venv/bin/python -m pip install --upgrade pip >/dev/null
      .venv/bin/python -m pip install -r requirements.txt || return 1
    fi
    cp requirements.txt "$isaret"
  fi
  return 0
}
