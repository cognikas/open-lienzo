#!/usr/bin/env python3
"""Entrada de la imagen de open-lienzo: un comando y el resto de los argumentos pasan tal cual.

  serve [args]      el visor (por defecto): viewer/serve.py con LIENZO_ROOT=/data, LIENZO_HOST=0.0.0.0
  fonts [args]      llena la caché de Google Fonts (serve.py --prefetch-fonts) y sale
  sync <cmd> ...    sync/sync.py (whoami, status, mark, files, find, history, new, init)
  entities [args]   sync/entities.py
  dedupe [args]     sync/dedupe.py
  install-hook [--repo R] [--force]
                    instala hooks/artifacts-pre-commit (escaneo de secretos con Trivy) como pre-commit del repo de
                    artifacts (por defecto LIENZO_ROOT, /data); no pisa un hook existente sin --force
  test              los tests unitarios
"""
import argparse
import os
import shutil
import sys
from pathlib import Path

APP = Path(__file__).resolve().parent.parent
HOOK = APP / "hooks" / "artifacts-pre-commit"
SCRIPTS = {
    "serve": [APP / "viewer" / "serve.py"],
    "fonts": [APP / "viewer" / "serve.py", "--prefetch-fonts"],
    "sync": [APP / "sync" / "sync.py"],
    "entities": [APP / "sync" / "entities.py"],
    "dedupe": [APP / "sync" / "dedupe.py"],
    "test": ["-m", "unittest", "discover", "-s", APP / "tests"],
}


def install_hook(argv: list) -> int:
    ap = argparse.ArgumentParser(prog="install-hook")
    ap.add_argument("--repo", type=Path, default=Path(os.environ.get("LIENZO_ROOT", "/data")))
    ap.add_argument("--force", action="store_true", help="pisar un pre-commit existente")
    a = ap.parse_args(argv)
    hooks = a.repo / ".git" / "hooks"
    if not hooks.parent.is_dir():
        print(f"{a.repo} no es la raíz de un repo git (no hay .git/)", file=sys.stderr)
        return 1
    dest = hooks / "pre-commit"
    if dest.exists() and not a.force:
        same = dest.read_bytes() == HOOK.read_bytes()
        print(f"{dest} ya {'está instalado' if same else 'existe y es otro hook: --force para pisarlo'}")
        return 0 if same else 1
    hooks.mkdir(exist_ok=True)
    shutil.copyfile(HOOK, dest)
    dest.chmod(0o755)
    print(f"instalado: {dest} (escanea secretos en lo que se commitea; usa trivy o Docker en el host)")
    return 0


def main():
    cmd, *rest = sys.argv[1:] or ["serve"]
    if cmd in ("-h", "--help", "help"):
        print(__doc__)
        return
    if cmd == "install-hook":
        sys.exit(install_hook(rest))
    if cmd not in SCRIPTS:
        sys.exit(f"comando desconocido: {cmd}\n{__doc__}")
    os.execv(sys.executable, [sys.executable, *map(str, SCRIPTS[cmd]), *rest])


if __name__ == "__main__":
    main()
