#!/usr/bin/env python3
"""Estado de sincronización entre los artifacts locales y claude.ai (solo stdlib).

Las tools Artifact y ArtifactData solo existen dentro de una sesión de Claude, así que
este script no habla con claude.ai: lleva la cuenta de qué cambió desde la última sync
y le deja a la skill /lienzo-sync un plan concreto.

Las cuentas viven en sync/accounts.json (o en el archivo de LIENZO_ACCOUNTS): email → cuenta y cuenta →
carpeta, con rutas relativas a ese archivo. Ver sync/accounts.example.json.

  sync.py whoami  --email E           cuenta y carpeta (raíz) de ese email
  sync.py init    --root R --account A   línea base: lo que hay en disco = lo último sincronizado
  sync.py status  --root R --account A [--json]     qué cambió en local (páginas y db)
  sync.py mark    --root R --id ID --account A [--url U] [--version V] [--updated AAAA-MM-DD]   registrar una sync hecha
  sync.py files   --root R --id ID      archivos publicables de un artifact (para un push)
  sync.py find    QUERY [--email E]     URL, id o título → copia local, cuenta, entidad, última versión
  sync.py history --root R --id ID      versiones de claude.ai registradas + commits de git con el contenido
  sync.py new     --account A --title T --entity E [--app P]   artifact nuevo solo en local (cuenta sin acceso)

manifest.json (uno por raíz de entidad) gana por artifact:
  urls: {cuenta: url}       la URL de ese artifact en cada cuenta
  sync: {account, version, at, pages: {ruta: sha256}, db: {ruta: sha256}}
  history: [{account, version, at}]   cada versión de claude.ai que pasó por una sync
"""
import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import unicodedata
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
ACCOUNTS = Path(os.environ.get("LIENZO_ACCOUNTS") or HERE / "accounts.json").resolve()
SKIP_DIRS = {"_db", "_comments", "artifact-type"}  # db aparte; comentarios locales y motor del tipo no se publican
SKIP_FILES = {"SKILL.md", ".DS_Store"}


def load_manifest(root: Path) -> list:
    mf = root / "manifest.json"
    return json.loads(mf.read_text()) if mf.is_file() else []


def live(mf: list) -> list:
    """Entradas vigentes: las de `duplicate_of` son copias descartadas en la deduplicación."""
    return [e for e in mf if not e.get("duplicate_of")]


def accounts() -> dict:
    if not ACCOUNTS.is_file():
        sys.exit(f"falta {ACCOUNTS}: copiá sync/accounts.example.json a ese archivo y poné tus cuentas "
                 "(o apuntá LIENZO_ACCOUNTS a otro)")
    return json.loads(ACCOUNTS.read_text())


def account_root(account: str) -> Path:
    roots = accounts()["roots"]
    if account not in roots:
        sys.exit(f"cuenta sin carpeta en {ACCOUNTS.name}: {account}")
    return (ACCOUNTS.parent / roots[account]).resolve()


def check_root(root: Path, account: str):
    """Cada cuenta sincroniza solo su carpeta: evita publicar la carpeta de una cuenta desde otra."""
    expected = account_root(account)
    if root.resolve() != expected:
        sys.exit(f"--root {root} no es la carpeta de la cuenta '{account}' ({expected})")


def save_manifest(root: Path, mf: list):
    (root / "manifest.json").write_text(json.dumps(mf, ensure_ascii=False, indent=2) + "\n")


def sha(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:  # por bloques: hay assets de decenas de MB
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def page_files(root: Path, art_id: str) -> dict:
    """Archivos publicables (sin assets: esos van por la asset store, no por files)."""
    base = root / art_id
    out = {}
    for p in sorted(base.rglob("*")):
        rel = p.relative_to(base)
        if p.is_file() and not (set(rel.parts) & SKIP_DIRS) and rel.parts[0] != "assets" and p.name not in SKIP_FILES:
            out[str(rel)] = sha(p)
    return out


def db_files(root: Path, art_id: str) -> dict:
    base = root / art_id / "_db"
    return {str(p.relative_to(base)): sha(p) for p in sorted(base.rglob("*.json"))} if base.is_dir() else {}


def diff(old: dict, new: dict) -> dict:
    return {
        "added": sorted(set(new) - set(old)),
        "removed": sorted(set(old) - set(new)),
        "changed": sorted(k for k in set(old) & set(new) if old[k] != new[k]),
    }


def empty(d: dict) -> bool:
    return not any(d.values())


def now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def cmd_whoami(a):
    acc = accounts()["emails"].get(a.email)
    if not acc:
        sys.exit(f"email sin cuenta registrada en {ACCOUNTS.name}: {a.email}")
    print(json.dumps({"account": acc, "root": str(account_root(acc))}, indent=2))


def cmd_init(a):
    root = a.root.resolve()
    check_root(root, a.account)
    mf = load_manifest(root)
    for e in live(mf):
        e.setdefault("urls", {})[a.account] = e["url"]
        e["sync"] = {"account": a.account, "version": e.get("version"), "at": now(),
                     "pages": page_files(root, e["id"]), "db": db_files(root, e["id"])}
    save_manifest(root, mf)
    print(f"línea base: {len(mf)} artifacts, cuenta {a.account}")


def cmd_status(a):
    root = a.root.resolve()
    check_root(root, a.account)
    mf = load_manifest(root)
    known = {e["id"] for e in mf}
    rows = []
    for e in live(mf):
        s = e.get("sync") or {}
        pages, db = diff(s.get("pages", {}), page_files(root, e["id"])), diff(s.get("db", {}), db_files(root, e["id"]))
        url = (e.get("urls") or {}).get(a.account)
        if not (empty(pages) and empty(db)) or not url:
            rows.append({"id": e["id"], "title": e.get("title", ""), "pages": pages, "db": db,
                         "url": url, "action": "update" if url else "create"})
    new = [p.name for p in root.iterdir() if p.is_dir() and not p.name.startswith(("_", ".")) and p.name not in known]
    if a.json:
        print(json.dumps({"changed": rows, "untracked": new}, ensure_ascii=False, indent=2))
        return
    print(f"{len(live(mf))} artifacts · {len(rows)} con cambios o sin URL en '{a.account}' · {len(new)} sin registrar")
    for r in rows:
        n = lambda d: sum(len(v) for v in d.values())
        print(f"  {r['action']:6} {r['id']}  páginas:{n(r['pages'])} db:{n(r['db'])}  {r['title'][:50]}")
    for i in new:
        print(f"  nuevo  {i}  (carpeta sin entrada en manifest.json)")


def cmd_mark(a):
    root = a.root.resolve()
    check_root(root, a.account)
    mf = load_manifest(root)
    e = next((e for e in mf if e["id"] == a.id), None)
    if e is not None and e.get("duplicate_of"):
        sys.exit(f"{a.id} es una copia descartada (duplicate_of {e['duplicate_of']}): sincronizá esa")
    if e is None:  # artifact nuevo, publicado desde otra sesión: se registra
        e = {"id": a.id, "url": a.url, "title": a.title or a.id}
        mf.append(e)
    if a.title:
        e["title"] = a.title
    if a.entity:
        e["entity"], e["entity_source"] = a.entity, "session"
    if a.url:
        e.setdefault("urls", {})[a.account] = a.url
    pages = page_files(root, a.id)
    # `updated` ordena las versiones de una familia: cambia solo si el contenido cambió, no por re-marcar
    # o espejar una versión vieja
    if getattr(a, "updated", None):  # la fecha de la versión en claude.ai (Artifact list): mejor que el día de la bajada
        e["updated"] = a.updated[:10]
    elif "updated" not in e or pages != (e.get("sync") or {}).get("pages"):
        e["updated"] = now()[:10]
    e["sync"] = {"account": a.account, "version": a.version, "at": now(), "pages": pages, "db": db_files(root, a.id)}
    hist = e.setdefault("history", [])
    if a.version and not any(h["version"] == a.version and h["account"] == a.account for h in hist):
        hist.append({"account": a.account, "version": a.version, "at": e["sync"]["at"]})
    save_manifest(root, mf)
    print(f"marcado {a.id} ({a.account})")


def cmd_history(a):
    """claude.ai solo entrega la versión actual de un artifact, así que el historial es el que registramos:
    las versiones que pasaron por una sync y los commits de git que guardan cada contenido."""
    root = a.root.resolve()
    e = next((e for e in load_manifest(root) if e["id"] == a.id), None)
    if e is None:
        sys.exit(f"{a.id} no está en {root / 'manifest.json'}")
    print(f"{e.get('title', a.id)} ({a.id})\n\nversiones de claude.ai registradas:")
    for h in e.get("history", []) or [{"account": "-", "version": "(ninguna: anterior al registro)", "at": ""}]:
        print(f"  {h['at']:20}  {h['account']:9} {h['version']}")
    log = subprocess.run(["git", "-C", str(root), "log", "--format=%h  %ad  %s", "--date=format:%Y-%m-%d %H:%M",
                          "--", a.id], capture_output=True, text=True).stdout.strip()
    print("\ncontenido en git (git -C <raíz> show <commit>:<id>/<archivo>):\n" + "\n".join("  " + l for l in log.splitlines()))


def cmd_find(a):
    """Busca en todas las carpetas de cuenta del repo. `remote` es la URL que la cuenta de la sesión puede
    abrir; si es null, hay que usar la copia local (y queda pendiente de subir cuando haya acceso)."""
    acc_now = accounts()["emails"].get(a.email) if a.email else None
    m = re.search(r"artifact/([A-Za-z0-9-]{20,36})", a.query)
    q = (m.group(1) if m else a.query).strip().lower()
    repo = account_root(next(iter(accounts()["roots"]))).parent
    entries = {}  # se recorre el repo entero, no solo las cuentas de accounts.json: dedupe también lo hace
    for mf in sorted(repo.glob("*/manifest.json")):
        for e in json.loads(mf.read_text()):
            entries[(mf.parent.name, e["id"])] = (mf.parent, e)

    def urls_of(acc, e):
        return {**({acc: e["url"]} if e.get("url") else {}), **(e.get("urls") or {})}

    hits = [k for k, (_, e) in entries.items() if q == e["id"].lower() or q in e.get("title", "").lower()
            or any(q in u.lower() for u in urls_of(k[0], e).values())]
    out = []
    for key in hits:
        seen = set()
        while entries[key][1].get("duplicate_of") and key not in seen:  # copia idéntica: se usa la que quedó
            seen.add(key)
            nxt = tuple(entries[key][1]["duplicate_of"].split("/", 1))
            if nxt not in entries:
                break
            key = nxt
        acc, (root, e) = key[0], entries[key]
        fam = e.get("family")  # las familias ya son de una sola entidad (dedupe.py)
        latest = next((x for _, x in entries.values() if fam and x.get("family") == fam and x.get("family_latest")), e)
        urls = urls_of(acc, e)
        local = root / e["id"]
        out.append({"id": e["id"], "title": e.get("title"), "account": acc, "entity": e.get("entity", "?"),
                    "local": str(local) if local.is_dir() else None, "urls": urls, "duplicate_of": e.get("duplicate_of"),
                    "warning": None if local.is_dir() else "sin copia local: la cadena duplicate_of está rota o apunta "
                                                          "a una cuenta que no está en el repo; usá las urls",
                    "remote": urls.get(acc_now) if acc_now else None, "session_account": acc_now,
                    "version": f"v{e['family_rank']}/{e['family_size']}" if fam else None,
                    "latest": None if latest is e else {"id": latest["id"], "title": latest.get("title"),
                                                        "entity": latest.get("entity", "?")},
                    "latest_uncertain": bool(fam and latest.get("family_tie"))})
    uniq = {o["local"]: o for o in out}
    print(json.dumps(list(uniq.values()), ensure_ascii=False, indent=2) if uniq else f"sin resultados: {a.query}")


def cmd_new(a):
    """Artifact nuevo cuando la cuenta dueña no está disponible: vive en local con un id `local-…` hasta el push.
    Al publicarlo, `mark --url <url nueva>` anota la URL de claude.ai en este mismo registro (la carpeta no cambia)."""
    root = account_root(a.account)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d")
    plain = unicodedata.normalize("NFKD", a.title).encode("ascii", "ignore").decode().lower()  # "Árbol" → "arbol"
    slug = re.sub(r"[^a-z0-9]+", "-", plain).strip("-")[:32] or "artifact"
    art_id = f"local-{stamp}-{slug}"
    n = 2
    while (root / art_id).exists():
        art_id = f"local-{stamp}-{slug}-{n}"
        n += 1
    (root / art_id).mkdir(parents=True)
    mf = load_manifest(root)
    e = {"id": art_id, "url": None, "title": a.title, "updated": stamp[:4] + "-" + stamp[4:6] + "-" + stamp[6:],
         "entity": a.entity, "entity_source": "session", "local_only": True}
    if a.app:
        e["app"], e["app_source"] = a.app, "manual"
    mf.append(e)
    save_manifest(root, mf)
    print(json.dumps({"id": art_id, "dir": str(root / art_id), "viewer": f"http://127.0.0.1:8765/{art_id}/",
                      "next": "escribí index.html ahí, commit en el repo de artifacts; push con /lienzo-sync cuando "
                              "la cuenta vuelva (status lo muestra como create)"}, ensure_ascii=False, indent=2))


# Tipos que la tool Artifact no deduce sola por la extensión: van explícitos en el mapa `files` del publish.
# Un .mjs sin text/javascript no corre como módulo en el navegador.
EXPLICIT_TYPES = {".mjs": "text/javascript", ".cjs": "text/javascript", ".wasm": "application/wasm",
                  ".webmanifest": "application/manifest+json", ".glb": "model/gltf-binary", ".gltf": "model/gltf+json",
                  ".ogg": "audio/ogg", ".opus": "audio/ogg", ".flac": "audio/flac", ".wav": "audio/wav",
                  ".map": "application/json", ".txt": "text/plain; charset=utf-8"}


def cmd_files(a):
    """El mapa `files` listo para `Artifact publish` (con `root` = la carpeta del artifact): ruta publicada → ruta
    local, o {from, contentType} cuando el tipo no se deduce de la extensión. Sin index.html (va en file_path)."""
    root = a.root.resolve()
    files = sorted(page_files(root, a.id))
    if (root / a.id / "project" / "canvas.json").is_file():  # Design: la página es del tipo; se publica solo project/
        files = [f for f in files if f.startswith("project/")]
    out = {}
    for f in files:
        if f == "index.html":
            continue
        ctype = EXPLICIT_TYPES.get(Path(f).suffix.lower())
        out[f] = {"from": f, "contentType": ctype} if ctype else f
    print(json.dumps(out, ensure_ascii=False, indent=2))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    w = sub.add_parser("whoami"); w.add_argument("--email", required=True)
    f = sub.add_parser("find"); f.add_argument("query"); f.add_argument("--email")
    n = sub.add_parser("new")
    for flag in ("--account", "--title", "--entity"):
        n.add_argument(flag, required=True)
    n.add_argument("--app")
    h = sub.add_parser("history"); h.add_argument("--root", type=Path, required=True); h.add_argument("--id", required=True)
    for name in ("init", "status", "mark", "files"):
        p = sub.add_parser(name)
        p.add_argument("--root", type=Path, required=True)
        if name in ("init", "mark", "status"):
            p.add_argument("--account", required=True)
        if name == "status":
            p.add_argument("--json", action="store_true")
        if name in ("mark", "files"):
            p.add_argument("--id", required=True)
        if name == "mark":
            p.add_argument("--url")
            p.add_argument("--version")
            p.add_argument("--updated", help="fecha de la versión en claude.ai (AAAA-MM-DD, la de Artifact list)")
            p.add_argument("--title")
            p.add_argument("--entity")
    a = ap.parse_args()
    {"whoami": cmd_whoami, "find": cmd_find, "history": cmd_history, "new": cmd_new, "init": cmd_init, "status": cmd_status, "mark": cmd_mark, "files": cmd_files}[a.cmd](a)


if __name__ == "__main__":
    main()
