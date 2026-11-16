#!/usr/bin/env python3
"""Etiqueta cada artifact del repo de artifacts con su entidad y su app (solo stdlib).

La cuenta donde vive un artifact no es su entidad (una cuenta puede guardar trabajo de varias). La entidad se
decide por la primera fuente que responda:

  manual   <repo>/entities.json  {"<id>": "<entidad>"}   la corrección del usuario; siempre manda
  session  la que registró la sesión que publicó (`sync.py mark --entity`); se conserva
  ref      un archivo del workspace enlaza la URL del artifact → la entidad de esa carpeta (`folders`)
  title    reglas sobre el título (`title_rules`)
  content  palabras clave en el HTML (`keywords`), si una entidad domina con claridad
  ?        sin respuesta: va a la tabla para corregirla en entities.json

Escribe `entity` y `entity_source` en cada manifest.json.

También la **app** (la pestaña del visor), con la misma lógica: `<repo>/apps.json` manda; si no, la carpeta del
producto que enlaza la URL (`app_folders`, con `app_names` para renombrar); si no, `app_titles`; si no, la de su
entidad (`entity_app`) u "Otros". Escribe `app` y `app_source`.

Las reglas son de cada usuario: <repo>/entities.rules.json (o --rules). Sin ese archivo solo cuentan las fuentes
manual y session. Ver sync/entities.rules.example.json.

  entities.py --repo R [--workspace W] [--rules F]           tabla (sin tocar nada)
  entities.py --repo R [--workspace W] [--rules F] --apply   escribe los manifests
"""
import argparse
import json
import re
import subprocess
from collections import Counter, defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
URL = re.compile(r"claude\.ai/(?:code/)?artifact/([A-Za-z0-9-]{20,36})")
EMPTY = {"folders": [], "title_rules": [], "keywords": {}, "app_folders": [], "app_names": {}, "app_titles": [],
         "entity_app": {}}


def load_rules(path: Path) -> dict:
    """folders: [[prefijo de ruta del workspace, entidad]] (el más específico primero) · title_rules y app_titles:
    [[regex sobre el título en minúsculas y sin tildes, entidad o app]] (la primera que calza gana) · keywords:
    {entidad: regex} · app_folders: [regex con un grupo = carpeta del producto] · app_names: {carpeta: app} ·
    entity_app: {entidad: app por defecto}."""
    rules = {**EMPTY, **(json.loads(path.read_text()) if path and path.is_file() else {})}
    rules["folders"] = [tuple(x) for x in rules["folders"]]
    rules["title_rules"] = [tuple(x) for x in rules["title_rules"]]
    rules["app_titles"] = [tuple(x) for x in rules["app_titles"]]
    return rules


def fold(s: str) -> str:
    return s.lower().translate(str.maketrans("áéíóúñ", "aeioun"))


def title_rule(rules: dict, title: str):
    return next((ent for rx, ent in rules["title_rules"] if re.search(rx, fold(title))), None)


def folder_entity(rules: dict, path: str):
    return next((e for pre, e in rules["folders"] if path.startswith(pre)), None)


def app_folder(rules: dict, path: str):
    for rx in rules["app_folders"]:
        if m := re.search(rx, path):
            return rules["app_names"].get(m.group(1), m.group(1))
    return None


def workspace_refs(rules: dict, ws: Path, repo: Path) -> tuple:
    """(id → Counter de entidades, id → Counter de apps) según las carpetas que enlazan su URL. Usa ripgrep (`rg`)."""
    try:
        out = subprocess.run(
            ["rg", "-o", "--no-messages", "-g", f"!**/{repo.name}/**", "-g", "!**/node_modules/**",
             "-g", f"!**/{HERE.parent.name}/**", "-g", "*.{md,json,txt,yaml,yml,html}", URL.pattern, "."],
            cwd=ws, capture_output=True, text=True).stdout
    except FileNotFoundError:
        print("aviso: sin ripgrep (rg) no se buscan referencias en el workspace")
        out = ""
    votes, apps = defaultdict(Counter), defaultdict(Counter)
    for line in out.splitlines():
        path, _, match = line.partition(":")
        path, m = path.removeprefix("./"), URL.search(match)
        if not m:
            continue
        if ent := folder_entity(rules, path):
            votes[m.group(1)][ent] += 1
        if app := app_folder(rules, path):
            apps[m.group(1)][app] += 1
    return votes, apps


def by_content(rules: dict, d: Path):
    if not rules["keywords"]:
        return None
    text = "".join(fold(p.read_text(errors="ignore")) for p in d.rglob("*.html") if p.stat().st_size < 8_000_000)
    hits = Counter({e: len(re.findall(rx, text)) for e, rx in rules["keywords"].items()})
    (top, n), *rest = hits.most_common(2) + [(None, 0)]
    return top if n >= 3 and n >= 2 * rest[0][1] else None


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--repo", type=Path, required=True)
    ap.add_argument("--workspace", type=Path, help="carpeta donde buscar links a los artifacts (sin ella, no hay fuente ref)")
    ap.add_argument("--rules", type=Path, help="reglas (por defecto <repo>/entities.rules.json)")
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()
    repo = a.repo.resolve()
    rules = load_rules(a.rules or repo / "entities.rules.json")
    manual_f = repo / "entities.json"
    manual = json.loads(manual_f.read_text()) if manual_f.is_file() else {}
    refs, app_refs = workspace_refs(rules, a.workspace.resolve(), repo) if a.workspace else ({}, {})
    apps_f = repo / "apps.json"
    manual_apps = json.loads(apps_f.read_text()) if apps_f.is_file() else {}

    manifests, rows = {}, []
    for mf in sorted(repo.glob("*/manifest.json")):
        acc = mf.parent.name
        manifests[acc] = json.loads(mf.read_text())
        for e in manifests[acc]:
            title = fold(e.get("title", ""))
            if e["id"] in manual:
                ent, src = manual[e["id"]], "manual"
            elif e.get("entity_source") == "session":
                ent, src = e["entity"], "session"
            elif e["id"] in refs:
                ent, src = refs[e["id"]].most_common(1)[0][0], "ref"
            elif rule := title_rule(rules, title):
                ent, src = rule, "title"
            elif not e.get("duplicate_of") and (c := by_content(rules, mf.parent / e["id"])):
                ent, src = c, "content"
            else:
                ent, src = "?", "?"
            e["entity"], e["entity_source"] = ent, src
            if e["id"] in manual_apps:
                e["app"], e["app_source"] = manual_apps[e["id"]], "manual"
            elif e["id"] in app_refs:
                e["app"], e["app_source"] = app_refs[e["id"]].most_common(1)[0][0], "ref"
            elif rule := next((app for rx, app in rules["app_titles"] if re.search(rx, title)), None):
                e["app"], e["app_source"] = rule, "title"
            else:  # sin pista de app: la pestaña es la de su entidad
                e["app"], e["app_source"] = rules["entity_app"].get(ent, "Otros"), "entity"
            if not e.get("duplicate_of"):
                rows.append((acc, e))

    total = Counter(e["entity"] for _, e in rows)
    print("| Entidad | Artifacts |\n|---|---|")
    for ent, n in total.most_common():
        print(f"| {ent} | {n} |")
    print("\nPara revisar (sin referencia en el workspace):\n\n| Cuenta | id | Título | Entidad | Fuente |\n|---|---|---|---|---|")
    for acc, e in sorted(rows, key=lambda r: (r[1]["entity_source"] == "title", r[1]["entity"])):
        if e["entity_source"] not in ("ref", "manual", "session"):
            print(f"| {acc} | {e['id']} | {e.get('title', '')[:45]} | {e['entity']} | {e['entity_source']} |")
    apps = Counter(e["app"] for _, e in rows)
    print("\n| App | Artifacts |\n|---|---|\n" + "\n".join(f"| {k} | {n} |" for k, n in apps.most_common()))
    print("\nApp tomada de la entidad (sin pista propia):",
          ", ".join(f'{e.get("title", "")[:34]} → {e["app"]}' for _, e in rows if e["app_source"] == "entity") or "ninguno")
    if a.apply:
        for acc, mf in manifests.items():
            (repo / acc / "manifest.json").write_text(json.dumps(mf, ensure_ascii=False, indent=2) + "\n")
        print("\naplicado")


if __name__ == "__main__":
    main()
