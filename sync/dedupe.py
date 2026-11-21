#!/usr/bin/env python3
"""Deduplica artifacts entre las carpetas de cuenta del repo de artifacts (solo stdlib).

Duplicado = **contenido idéntico**: mismos archivos con los mismos bytes, assets y db (`_db/`)
incluidos, en la misma cuenta o en cuentas distintas, tenga o no el mismo título. Incluye las
**republicaciones** entre cuentas, que claude.ai cambia sin cambiar la obra: en un artifact de tipo
(Design, Slides) no cuenta el `index.html` (el envoltorio del tipo) ni `createdOnFiles`, y los assets
se comparan por contenido, no por id (cada publicación los sube con ids nuevos). Gana la fijada en
`latest.json`; si no, la más reciente (`updated`) y, a igual fecha, la de la cuenta `--prefer`. La carpeta del perdedor se
borra con `git rm` (queda en el historial) y su entrada del manifest se conserva con
`duplicate_of: "<cuenta>/<id>"`, para que la sync sepa que esa URL existe y no la recree.

El mismo título con contenido distinto NO es un duplicado: son versiones (v1, v2…) de una
misma obra. Se conservan todas y se anotan como familia, siempre dentro de una misma entidad
(correr antes `entities.py --apply`):
  family: "<entidad>:<título sin marcas de versión>"   family_rank: 1 = la más vieja
  family_size: N        family_latest: true en la más reciente
  family_tie: true      si la más reciente comparte fecha con otra: el orden no es confiable
  <repo>/latest.json    ["<id>", …] versiones que el usuario (o la sesión dueña) confirmó como vigentes: van
                        últimas en su familia y resuelven el empate

  dedupe.py --repo R [--prefer CUENTA]            plan (tablas), sin tocar nada
  dedupe.py --repo R [--prefer CUENTA] --apply    aplica el plan
"""
import argparse
import hashlib
import json
import re
import subprocess
import unicodedata
from collections import defaultdict
from pathlib import Path

from sync import SKIP_FILES, sha

CONTENT_SKIP_DIRS = {"artifact-type", "_comments"}  # el motor del tipo es igual en todos; la db sí es contenido
VERSION = re.compile(r"\b(v|e)\d+(\.\d+)*\b|\bgen \d+\b")
FAMILY_KEYS = ("family", "family_rank", "family_size", "family_latest", "family_tie")


def norm(title: str) -> str:
    t = unicodedata.normalize("NFKD", title).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9.]+", " ", t).strip()


def family_key(title: str) -> str:
    return re.sub(r"\s+", " ", VERSION.sub(" ", norm(title))).replace(" .", " ").strip(" .")


ASSET_ID = re.compile(r"[0-9a-f]{32}")
TEXT = {".html", ".json", ".css", ".js", ".mjs", ".md", ".svg", ".txt"}


def fingerprint(d: Path) -> tuple:
    """(sha256 del contenido, bytes, archivos). Una carpeta vacía o ausente no tiene huella."""
    if not d.is_dir():
        return None, 0, 0
    typed = (d / "project").is_dir()  # creado desde un tipo: el index.html es del tipo, no de la obra
    assets = {p.stem: sha(p) for p in (d / "assets").rglob("*") if p.is_file()}  # id → contenido, para traducir referencias
    parts, total, n = [], 0, 0
    for p in sorted(d.rglob("*")):
        rel = p.relative_to(d)
        key = rel.as_posix()  # same fingerprint on every OS
        if not p.is_file() or set(rel.parts) & CONTENT_SKIP_DIRS or p.name in SKIP_FILES:
            continue
        total, n = total + p.stat().st_size, n + 1
        if rel.parts[0] == "assets":
            parts.append(f"asset\0{p.suffix}\0{sha(p)}")  # por contenido: el nombre es un id que cambia al subir
        elif typed and key == "index.html":
            continue
        elif p.suffix in TEXT and p.stat().st_size < 20_000_000:
            text = ASSET_ID.sub(lambda m: assets.get(m.group(0), m.group(0)), p.read_text(encoding="utf-8", errors="ignore"))
            if p.name in ("canvas.json", "deck.json"):
                text = re.sub(r'"createdOnFiles"\s*:\s*\{[^}]*\},?', "", text)
            parts.append(f"{key}\0{hashlib.sha256(text.encode()).hexdigest()}")
        else:
            parts.append(f"{key}\0{sha(p)}")
    if not n:
        return None, 0, 0
    return hashlib.sha256("\n".join(sorted(parts)).encode()).hexdigest(), total, n


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--repo", type=Path, required=True)
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--prefer", help="cuenta que gana un empate de fecha entre copias idénticas")
    a = ap.parse_args()
    repo = a.repo.resolve()

    manifests, items = {}, []
    for mf in sorted(repo.glob("*/manifest.json")):
        acc = mf.parent.name
        manifests[acc] = json.loads(mf.read_text(encoding="utf-8"))
        for e in manifests[acc]:
            if not e.get("duplicate_of"):
                fp, b, n = fingerprint(mf.parent / e["id"])
                items.append({"acc": acc, "e": e, "fp": fp, "bytes": b, "n": n, "updated": e.get("updated", "")})

    # 1. Contenido idéntico → duplicado.
    pf = repo / "latest.json"
    pinned = set(json.loads(pf.read_text(encoding="utf-8"))) if pf.is_file() else set()
    by_fp = defaultdict(list)
    for it in items:
        if it["fp"]:
            by_fp[it["fp"]].append(it)
    plan = []
    for group in by_fp.values():
        group.sort(key=lambda t: (t["e"]["id"] in pinned, t["updated"], t["acc"] == a.prefer), reverse=True)
        plan += [(group[0], lose) for lose in group[1:]]
    dropped = {id(lose) for _, lose in plan}

    # 2. Misma entidad y mismo título sin marcas de versión, contenido distinto → versiones de una familia.
    fams = defaultdict(list)
    for it in items:
        key = family_key(it["e"].get("title", ""))
        if id(it) not in dropped and key:  # un título que es solo "v2" no agrupa nada
            fams[f"{it['e'].get('entity', '?')}:{key}"].append(it)
    families = {k: sorted(v, key=lambda t: (t["e"]["id"] in pinned, t["updated"], t["e"]["id"]))
                for k, v in fams.items() if len(v) > 1}

    fmt = lambda t: f"{t['acc']}/{t['e']['id']} · {t['updated'][:10]} · {t['bytes'] / 1e6:.1f} MB/{t['n']} arch."
    print("## Duplicados (contenido idéntico)\n\n| Título | Gana | Pierde |\n|---|---|---|")
    for w, l in plan:
        print(f"| {w['e'].get('title', '')[:45]} | {fmt(w)} | {fmt(l)} |")
    print(f"\n{len(plan)} copias a descartar\n\n## Versiones (mismo título, contenido distinto: se conservan)\n")
    print("| Familia | Versiones (de la más vieja a la más reciente) |\n|---|---|")
    for k, v in sorted(families.items()):
        tie = "" if v[-1]["e"]["id"] in pinned else " ⚠️ empate de fecha" if v[-1]["updated"][:10] == v[-2]["updated"][:10] else ""
        print(f"| {k[:45]} | " + " → ".join(f"v{i} {t['acc'][0]}/{t['e']['id'][:6]} {t['updated'][5:10]}"
                                            for i, t in enumerate(v, 1)) + f"{tie} |")
    print(f"\n{len(families)} familias con más de una versión")

    if not a.apply:
        return
    for mf in manifests.values():  # también las entradas ya descartadas: no deben quedar con datos de familia viejos
        for e in mf:
            for k in FAMILY_KEYS:
                e.pop(k, None)
    for k, v in families.items():
        last_day = v[-1]["updated"][:10]
        tied = v[-1]["e"]["id"] not in pinned and sum(t["updated"][:10] == last_day for t in v) > 1
        for i, t in enumerate(v, 1):
            t["e"].update(family=k, family_rank=i, family_size=len(v), family_latest=i == len(v))
            if tied and t["updated"][:10] == last_day:
                t["e"]["family_tie"] = True
    for w, l in plan:
        acc, e = l["acc"], l["e"]
        folder = repo / acc / e["id"]
        if folder.exists():
            dirty = subprocess.run(["git", "-C", str(repo), "status", "--porcelain", "--", f"{acc}/{e['id']}"],
                                   capture_output=True, text=True, encoding="utf-8").stdout.strip()
            if dirty or any((folder / "_comments").glob("*.json")):
                # nunca borrar trabajo sin commit ni comentarios locales: se resuelve a mano
                print(f"saltado {acc}/{e['id']}: " + ("tiene cambios sin commit" if dirty else "tiene comentarios locales"))
                continue
        e["duplicate_of"] = f"{w['acc']}/{w['e']['id']}"
        win_urls = w["e"].setdefault("urls", {})
        for k, v in (e.get("urls") or {acc: e.get("url")}).items():
            win_urls.setdefault(k, v)  # mismo contenido: la URL del perdedor en su cuenta sirve para el ganador
        if folder.exists():
            subprocess.run(["git", "-C", str(repo), "rm", "-rq", f"{acc}/{e['id']}"], check=True)
    for acc, mf in manifests.items():
        (repo / acc / "manifest.json").write_text(json.dumps(mf, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("aplicado")


if __name__ == "__main__":
    main()
