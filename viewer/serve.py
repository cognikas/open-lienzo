#!/usr/bin/env python3
"""Servidor local de artifacts de claude.ai copiados a disco (solo stdlib).

Sirve una carpeta de artifacts entera (todas las cuentas, un solo índice) o una carpeta de cuenta
(ver README.md: formato de la carpeta y contrato de la API):
  /                          índice generado desde los manifest.json (los ids son únicos entre cuentas)
  /<id>/canvas               visor de lienzo para artifacts tipo Design
  /<id>/deck                 visor de presentación para artifacts tipo Slides (project/deck.json)
  /<id>/artifact-type/*      el motor del tipo, desde _runtime/<tipo>-<versión>/
  /<id>/project/support.js   el motor dc-runtime.js local (lo que claude.ai entrega como support.js). Es de
                             Anthropic y no viene en este repo: se toma del artifact Design del usuario (README)
  /_blob/<assetId>           assets por id, buscados en <id>/assets/ de cualquier artifact
  /_viewer/*                 archivos de este visor
  /_api/{doc,col,rev}/<id>   la db local que usa claude-local.js (en <id>/_db/)
  /_api/comments/<id>        comentarios locales (en <id>/_comments/<cid>.json): GET, POST, PATCH y DELETE ?cid=
  /_api/board/<id>?path=X    GET: el archivo crudo y su sha256 · PUT: reescribe <id>/project/X.dc.html (edición desde el lienzo) si nadie lo cambió desde
                             que se leyó (sha256 `prev`); queda como cambio local
  /_fonts/{g,s}/…            Google Fonts desde la caché _runtime/fonts/ (sin conexión); si falta algo y hay
                             internet, se baja y se guarda. El HTML y el CSS se reescriben para apuntar acá
  el resto                   archivos estáticos de la raíz; el HTML de las páginas que
                             llaman a claude.use() recibe el shim claude-local.js
  /_health                   healthcheck: 200 "ok"

Uso:  python serve.py --root CARPETA_DE_ARTIFACTS [--host 127.0.0.1] [--port 8765] [--author NOMBRE]
      python serve.py --root CARPETA --prefetch-fonts      llena la caché de fuentes de todos los artifacts y sale
      (--root, --host y --author también se toman de LIENZO_ROOT, LIENZO_HOST y LIENZO_AUTHOR. La API solo
      acepta Host 127.0.0.1/localhost aunque escuche en 0.0.0.0: publicar el puerto solo en el loopback)
"""
import argparse
import html
import hashlib
import json
import mimetypes
import os
import re
import sys
import secrets
import threading
from datetime import datetime, timezone
import urllib.request
from hashlib import sha1
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import quote, unquote, urlsplit

HERE = Path(__file__).resolve().parent
USES_RUNTIME = re.compile(r"claude\??\.use\??\.?\(")  # claude.use( y también claude?.use?.(
FONTS = {"g": "https://fonts.googleapis.com/", "s": "https://fonts.gstatic.com/"}
FONT_UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36"

CDN = "https://cdn.jsdelivr.net/npm/"
VENDOR = {  # URL que pide dc-runtime.js -> archivo en _runtime/vendor/
    CDN + "react@18.3.1/umd/react.production.min.js": "react.production.min.js",
    CDN + "react-dom@18.3.1/umd/react-dom.production.min.js": "react-dom.production.min.js",
    CDN + "@babel/standalone@7.29.0/babel.min.js": "babel.min.js",
}

mimetypes.add_type("text/javascript", ".mjs")
mimetypes.add_type("font/woff2", ".woff2")

SEG = re.compile(r"^(?!\.\.?$)[A-Za-z0-9_\-.~:@+]{1,200}$")
MAX_DOC = 256 * 1024
MAX_BOARD = 8 * 1024 * 1024
BOARD = re.compile(r"^[^/\\]{1,200}\.dc\.html$")
AUTHOR = os.environ.get("LIENZO_AUTHOR") or "Local"  # nombre en comentarios y en claude.use('user'); --author lo cambia


class State:
    root: Path     # el repo (todas las cuentas) o una carpeta de cuenta
    dirs: dict     # id -> carpeta del artifact
    rtbase: Path   # carpeta _runtime/ (en la raíz de la cuenta o, compartida, en la de arriba)
    runtime: Path  # dc-runtime.js; None si no está (los boards de Design se ven en blanco)
    blobs: dict    # assetId -> Path
    manifest: list
    live: set      # ids de artifacts cuyo código llama a claude.use()
    rev: int = 0   # sube con cada escritura; las suscripciones lo sondean
    lock = threading.Lock()


def manifest_stamp(root: Path) -> tuple:
    """Huella de los manifests: cuando otra sesión baja o marca artifacts, cambia y el servidor recarga."""
    mfs = [root / "manifest.json"] if (root / "manifest.json").is_file() else sorted(root.glob("*/manifest.json"))
    return tuple((str(m), m.stat().st_mtime_ns) for m in mfs)


def load_state(root: Path) -> State:
    s = State()
    s.root = root
    s.stamp = manifest_stamp(root)
    s.rtbase = next((d for d in (root / "_runtime", root.parent / "_runtime") if d.is_dir()), root / "_runtime")
    def version(p: Path) -> tuple:  # design-0.2.47 -> (0, 2, 47): orden numérico, no de texto
        return tuple(int(n) for n in re.findall(r"\d+", p.parents[1].name))
    runtimes = sorted(s.rtbase.glob("design-*/artifact-type/dc-runtime.js"), key=version)
    s.runtime = runtimes[-1] if runtimes else None
    if s.runtime is None:
        print(f"aviso: no hay dc-runtime.js en {s.rtbase}/design-*/artifact-type/: los boards de Design se verán "
              "en blanco (README: «El motor de Design»)", file=sys.stderr)
    single = (root / "manifest.json").is_file()
    accounts = [root] if single else sorted(m.parent for m in root.glob("*/manifest.json"))
    s.manifest, s.dirs = [], {}
    for acc in accounts:
        for e in json.loads((acc / "manifest.json").read_text(encoding="utf-8")):
            s.manifest.append({**e, "account": acc.name})
            if (acc / e["id"]).is_dir():
                s.dirs[e["id"]] = acc / e["id"]
    s.blobs = {p.stem: p for d in s.dirs.values() for p in d.glob("assets/*") if p.is_file()}
    s.live = {
        art_id for art_id, d in s.dirs.items()
        if any(USES_RUNTIME.search(p.read_text(encoding="utf-8", errors="ignore"))
               for ext in ("*.html", "*.js", "*.mjs") for p in d.glob(ext))
    }
    return s


# --- db local: <root>/<id>/_db/<colección>/<doc>.json (layout de ArtifactData out_dir) ---

def db_path(state: State, art_id: str, path: str, parity: int):
    """Archivo (doc) o carpeta (colección) de una ruta de db; None si la ruta es inválida."""
    segs = path.split("/")
    if not SEG.match(art_id) or art_id not in state.dirs:
        return None
    if len(segs) > 16 or len(segs) % 2 != parity or not all(SEG.match(s) for s in segs):
        return None
    # ArtifactData guarda "~" como "@" en el nombre de archivo; mantenemos la convención y,
    # para que no sea ambigua, rechazamos ids con "@" literal
    if any("@" in s for s in segs):
        return None
    segs = [s.replace("~", "@") for s in segs]
    base = state.dirs[art_id] / "_db"
    return base.joinpath(*segs[:-1], segs[-1] + ".json") if parity == 0 else base.joinpath(*segs)


def deep_merge(dst: dict, src: dict) -> dict:
    for k, v in src.items():
        if isinstance(v, dict) and v.get("__delete__") is True:
            dst.pop(k, None)
        elif isinstance(v, dict) and isinstance(dst.get(k), dict):
            deep_merge(dst[k], v)
        else:
            dst[k] = v
    return dst


def type_runtime(state: State, art_id: str, rel: str):
    """artifact-type/* de un artifact creado desde un tipo: el motor es del tipo y vive una sola vez en
    _runtime/<tipo>-<versión>/ (Slides: project/deck.json; Design: project/canvas.json)."""
    d = state.dirs.get(art_id)
    if d is None or not rel.startswith("artifact-type/"):
        return None
    kind = "slides" if (d / "project" / "deck.json").is_file() else "design" if is_design(state, art_id) else None
    for base in sorted(state.rtbase.glob(f"{kind}-*"), key=lambda b: [int(n) for n in re.findall(r"\d+", b.name)],
                       reverse=True) if kind else []:
        f = (base / rel).resolve()
        if f.is_relative_to(base.resolve()) and f.is_file():
            return f
    return None


def is_design(state: State, art_id: str) -> bool:
    return art_id in state.dirs and (state.dirs[art_id] / "project" / "canvas.json").is_file()


def is_deck(state: State, art_id: str) -> bool:
    return art_id in state.dirs and (state.dirs[art_id] / "project" / "deck.json").is_file()


# --- Google Fonts sin conexión: caché en _runtime/fonts/{css,files}/ ---

def fetch(url: str):
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": FONT_UA}), timeout=8) as r:
            return r.read()
    except OSError:
        return None


def cache_write(f: Path, data: bytes):
    f.parent.mkdir(parents=True, exist_ok=True)
    tmp = f.with_name(f"{f.name}.{secrets.token_hex(4)}.tmp")  # único: dos hilos pueden escribir la misma caché
    tmp.write_bytes(data)
    os.replace(tmp, f)


def font_css(state: State, path_query: str):
    """La hoja de Google Fonts (con un User-Agent moderno, para woff2), apuntando a /_fonts/s/.
    La clave es la URL decodificada: el HTML la trae literal y el navegador la pide codificada."""
    pq = unquote(path_query)
    f = state.rtbase / "fonts" / "css" / (sha1(pq.encode()).hexdigest()[:16] + ".css")
    if not f.is_file():
        data = fetch(FONTS["g"] + quote(pq, safe="/?&=:;,@+|*()!~'-._"))
        if data is None:
            return None
        cache_write(f, data.replace(FONTS["s"].encode(), b"/_fonts/s/"))
    return f.read_bytes()


def font_file(state: State, rel: str):
    if not all(SEG.match(p) for p in rel.split("/")):
        return None
    f = state.rtbase / "fonts" / "files" / rel
    if not f.is_file():
        data = fetch(FONTS["s"] + rel)
        if data is None:
            return None
        cache_write(f, data)
    return f


def localize_fonts(body: bytes) -> bytes:
    return body.replace(FONTS["g"].encode(), b"/_fonts/g/").replace(FONTS["s"].encode(), b"/_fonts/s/")


def prefetch_fonts(state: State):
    found = set()
    for d in state.dirs.values():
        for p in d.rglob("*"):
            if p.suffix in (".html", ".css") and p.is_file() and p.stat().st_size < 20_000_000:
                for m in re.finditer(r"https://fonts\.googleapis\.com/([^\"'\s)<>]+)", p.read_text(encoding="utf-8", errors="ignore")):
                    found.add(html.unescape(m.group(1)))
    ok = files = 0
    for pq in sorted(found):
        css = font_css(state, pq)
        if css is None:
            print(f"  sin bajar: {pq[:80]}")
            continue
        ok += 1
        for rel in re.findall(rb"/_fonts/s/([^)\s'\"]+)", css):
            files += font_file(state, rel.decode()) is not None
    print(f"fuentes: {ok}/{len(found)} hojas y {files} archivos en {state.rtbase / 'fonts'}")


def support_js(state: State) -> bytes:
    """dc-runtime.js precedido de un mapa __resources que redirige el CDN a las copias locales."""
    vendor = state.rtbase / "vendor"
    res = {url: "/_runtime/vendor/" + name for url, name in VENDOR.items() if (vendor / name).is_file()}
    prefix = f"window.__resources=Object.assign(window.__resources||{{}},{json.dumps(res)});\n"
    return prefix.encode() + state.runtime.read_bytes()


PAGE_SKIP_DIRS = {"_db", "_comments", "artifact-type", "assets"}  # lo que no se publica por `files` a claude.ai
PAGE_SKIP_FILES = {"SKILL.md", ".DS_Store"}
HASHES: dict = {}  # (ruta, mtime_ns, tamaño) -> sha256: el índice no rehashea lo que no cambió


def file_sha(p: Path, st) -> str:
    key = (str(p), st.st_mtime_ns, st.st_size)
    if key not in HASHES:
        HASHES[key] = hashlib.sha256(p.read_bytes()).hexdigest()
    return HASHES[key]


def day(ts: float) -> str:
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d")


def local_change(d: Path, synced) -> str:
    """Fecha del último cambio local que claude.ai todavía no tiene: un archivo publicable distinto del de la
    última sync, nuevo o borrado. "" si la copia local es igual a lo sincronizado. Sin sync registrada (un
    artifact solo local), el archivo más reciente."""
    latest, seen = 0.0, set()
    for p in d.rglob("*"):
        rel = p.relative_to(d)
        if not p.is_file() or set(rel.parts) & PAGE_SKIP_DIRS or p.name in PAGE_SKIP_FILES:
            continue
        st = p.stat()
        key = rel.as_posix()  # the manifest's keys are POSIX on every OS
        seen.add(key)
        if synced is None or synced.get(key) != file_sha(p, st):
            latest = max(latest, st.st_mtime)
    if synced and set(synced) - seen:  # se borró algo: la carpeta registra cuándo
        latest = max(latest, d.stat().st_mtime)
    return day(latest) if latest else ""


def index_page(state: State) -> bytes:
    """El índice lleva los datos como JSON y la página filtra y ordena en el navegador."""
    rows = []
    for a in state.manifest:
        art_id = a["id"]
        if a.get("duplicate_of") or art_id not in state.dirs:  # descartado: vive en la copia que quedó
            continue
        d = state.dirs[art_id]
        kind, href = ("Design", f"/{art_id}/canvas") if is_design(state, art_id) else \
            ("Slides", f"/{art_id}/deck") if is_deck(state, art_id) else ("Página", f"/{art_id}/")
        comments = [json.loads(c.read_text(encoding="utf-8")) for c in (d / "_comments").glob("*.json")] if (d / "_comments").is_dir() else []
        rows.append({
            "id": art_id, "title": a.get("title", art_id), "href": href, "kind": kind,
            "account": a["account"], "entity": a.get("entity", "?"), "app": a.get("app", "Otros"),
            "updated": a.get("updated", "")[:10],  # la versión de claude.ai (o la del alta local)
            "modified": max(a.get("updated", "")[:10], local_change(d, (a.get("sync") or {}).get("pages"))),
            "remote": bool(a.get("url") or a.get("urls")),
            "ver": f'v{a["family_rank"]}/{a["family_size"]}' if a.get("family") else "",
            "latest": a.get("family_latest", True), "tie": bool(a.get("family_tie")),
            "db": art_id in state.live and any((d / "_db").rglob("*.json")),
            "comments": sum(not c.get("resolved") for c in comments),
        })
    rows.sort(key=lambda r: r["modified"], reverse=True)
    data = json.dumps(rows, ensure_ascii=False).replace("</", "<\\/")  # sin cierre de </script> dentro del JSON
    return (HERE / "index.html").read_text(encoding="utf-8").replace("{{DATA}}", data).encode()


class Handler(SimpleHTTPRequestHandler):
    state: State = None

    def log_message(self, fmt, *args):
        pass

    def send_bytes(self, body: bytes, ctype: str, status=200):
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def send_file(self, path: Path, art_id: str = None):
        ctype = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        if path.name.endswith(".dc.html"):
            ctype = "text/html; charset=utf-8"
        body = path.read_bytes()
        if path.suffix in (".html", ".css"):
            body = localize_fonts(body)
        if art_id in self.state.live and path.suffix == ".html":
            tag = (f'<script src="/_viewer/claude-local.js" data-art="{art_id}" '
                   f'data-user="{html.escape(AUTHOR, quote=True)}"></script>').encode()
            m = re.search(rb"<head[^>]*>", body, re.I)
            body = body[:m.end()] + tag + body[m.end():] if m else tag + body
        self.send_bytes(body, ctype)

    def send_json(self, obj, status=200):
        self.send_bytes(json.dumps(obj, ensure_ascii=False).encode(), "application/json", status)

    def read_body(self, limit=MAX_DOC):
        try:
            n = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            return None
        if not 0 <= n <= limit + 1024:
            return None
        try:
            data = json.loads(self.rfile.read(n) or b"{}").get("data")
        except (ValueError, AttributeError):
            return None
        return data if isinstance(data, dict) else None

    def comments(self, method: str, art_id: str, cid: str):
        """Comentarios locales: un JSON por comentario, para que git los versione y los merges no choquen."""
        st = self.state
        if art_id not in st.dirs or (cid and not SEG.match(cid)):
            return self.send_error(400, "comentario inválido")
        base = st.dirs[art_id] / "_comments"
        if method == "GET":
            rows = [json.loads(p.read_text(encoding="utf-8")) for p in sorted(base.glob("*.json"))] if base.is_dir() else []
            return self.send_json(rows)
        with st.lock:
            if method == "POST":
                data = self.read_body()
                if not data or not str(data.get("text", "")).strip() or not isinstance(data.get("anchor"), dict):
                    return self.send_error(400, "falta el texto o el ancla")
                now = datetime.now(timezone.utc)
                cid = now.strftime("%Y%m%dT%H%M%S") + "-" + secrets.token_hex(3)
                row = {"id": cid, "text": str(data["text"])[:4000], "anchor": data["anchor"], "author": AUTHOR,
                       "at": now.strftime("%Y-%m-%dT%H:%M:%SZ"), "resolved": False}
            else:
                f = base / f"{cid}.json"
                if not cid or not f.is_file():
                    return self.send_error(404, "no existe ese comentario")
                if method == "DELETE":
                    f.unlink()
                    st.rev += 1
                    return self.send_json({"ok": True})
                data = self.read_body() or {}
                if "text" in data and not (isinstance(data["text"], str) and data["text"].strip()):
                    return self.send_error(400, "text debe ser un texto no vacío")
                if "resolved" in data and not isinstance(data["resolved"], bool):
                    return self.send_error(400, "resolved debe ser true o false")
                row = json.loads(f.read_text(encoding="utf-8"))
                row.update({k: data[k] for k in ("text", "resolved") if k in data})
                row["text"] = row["text"][:4000]
            cache_write(base / f"{row['id']}.json", json.dumps(row, ensure_ascii=False, indent=2).encode())
            st.rev += 1
        return self.send_json(row)

    def board(self, method: str, art_id: str, name: str):
        """Edición de un board de Design desde el lienzo: el cliente manda el archivo completo y el sha256 del
        que leyó; si no coincide con el de disco (otra pestaña, una sesión, un pull), 409 y no se pisa nada."""
        st = self.state
        if method not in ("GET", "PUT"):
            return self.send_error(405)
        if art_id not in st.dirs or not BOARD.match(name) or name.startswith("."):
            return self.send_error(400, "board inválido")
        f = st.dirs[art_id] / "project" / name
        if not f.is_file():
            return self.send_error(404, "no existe ese board")
        if method == "GET":  # crudo: sin fuentes locales ni shim, que es lo que se edita
            raw = f.read_bytes()
            try:
                return self.send_json({"src": raw.decode(), "sha": hashlib.sha256(raw).hexdigest()})
            except UnicodeDecodeError:
                return self.send_error(422, "el board no es UTF-8: no se edita desde el lienzo")
        data = self.read_body(MAX_BOARD) or {}
        src, prev = data.get("src"), data.get("prev")
        if not isinstance(src, str) or not isinstance(prev, str) or "<x-dc" not in src:
            return self.send_error(400, "src no es un board")
        with st.lock:
            if hashlib.sha256(f.read_bytes()).hexdigest() != prev:
                return self.send_error(409, "el board cambió en disco: recargá")
            body = src.encode()
            try:
                cache_write(f, body)
            except OSError as e:
                return self.send_error(500, f"no se pudo escribir el board: {e.strerror}")
            st.rev += 1
        return self.send_json({"sha": hashlib.sha256(body).hexdigest()})

    def same_origin(self) -> bool:
        """Solo el propio visor usa la API. Host corta el DNS rebinding; Origin corta que una página de otro
        sitio abierta en el navegador escriba por CSRF (un POST simple no pasa por preflight de CORS)."""
        host = (self.headers.get("Host") or "").rsplit(":", 1)[0]
        if host not in ("127.0.0.1", "localhost"):
            return False
        origin = self.headers.get("Origin")
        if origin is not None and urlsplit(origin).hostname not in ("127.0.0.1", "localhost"):
            return False
        if self.command in ("POST", "PUT", "PATCH") and \
                (self.headers.get("Content-Type") or "").split(";")[0].strip() != "application/json":
            return False  # application/json obliga al navegador a pedir permiso (preflight), que nunca damos
        return True

    def api(self, method: str, parts: list, query: str):
        st = self.state
        if not self.same_origin():
            return self.send_error(403)
        kind, art_id = parts[1], parts[2]
        params = dict(p.split("=", 1) for p in query.split("&") if "=" in p)
        if kind == "comments":
            return self.comments(method, art_id, unquote(params.get("cid", "")))
        if kind == "board":
            return self.board(method, art_id, unquote(params.get("path", "")))
        path = unquote(params.get("path", ""))
        if kind == "rev" and method == "GET":
            return self.send_json({"rev": st.rev})
        f = db_path(st, art_id, path, 1 if kind == "col" else 0)
        if f is None:
            return self.send_error(400, "ruta de db inválida")
        if kind == "col" and method == "GET":
            rows = []
            if f.is_dir():
                for p in sorted(f.glob("*.json")):
                    rows.append({"id": p.stem.replace("@", "~"), "data": json.loads(p.read_text(encoding="utf-8"))})
            return self.send_json(rows)
        if kind != "doc":
            return self.send_error(404)
        if method == "GET":
            exists = f.is_file()
            return self.send_json({"exists": exists, "data": json.loads(f.read_text(encoding="utf-8")) if exists else None})
        with st.lock:
            if method == "DELETE":
                f.unlink(missing_ok=True)
            else:
                data = self.read_body()
                if data is None:
                    return self.send_error(400, "el cuerpo debe ser un objeto JSON de hasta 256 KiB")
                if method == "PATCH":
                    if not f.is_file():
                        return self.send_error(400, "update exige que el documento exista")
                    data = deep_merge(json.loads(f.read_text(encoding="utf-8")), data)
                cache_write(f, json.dumps(data, ensure_ascii=False, indent=2).encode())  # atómica: un GET nunca ve medio archivo
            st.rev += 1
        self.send_response(204)
        self.end_headers()

    def do_PUT(self):
        self.dispatch_api("PUT")

    def do_PATCH(self):
        self.dispatch_api("PATCH")

    def do_DELETE(self):
        self.dispatch_api("DELETE")

    def do_POST(self):
        self.dispatch_api("POST")

    def dispatch_api(self, method: str):
        u = urlsplit(self.path)
        parts = [p for p in u.path.split("/") if p]
        if len(parts) == 3 and parts[0] == "_api":
            return self.api(method, parts, u.query)
        self.send_error(405)

    def safe(self, base: Path, rel: str):
        p = (base / rel).resolve()
        return p if p.is_relative_to(base.resolve()) and p.is_file() else None

    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        st = self.state
        path = unquote(urlsplit(self.path).path)
        parts = [p for p in path.split("/") if p]

        if parts == ["_health"]:  # healthcheck del contenedor: no toca el estado ni el disco
            return self.send_bytes(b"ok", "text/plain; charset=utf-8")
        if not parts:
            if manifest_stamp(st.root) != st.stamp:  # servicio permanente: ver lo que otras sesiones bajaron
                with State.lock:
                    rev = st.rev
                    st = Handler.state = load_state(st.root)
                    st.rev = rev + 1
            return self.send_bytes(index_page(st), "text/html; charset=utf-8")
        if len(parts) == 3 and parts[0] == "_api":
            return self.api("GET", parts, urlsplit(self.path).query)
        if parts[0] == "_viewer" and len(parts) == 2:
            f = self.safe(HERE, parts[1])
            return self.send_file(f) if f else self.send_error(404)
        if parts[0] == "_runtime" and len(parts) > 1:
            f = self.safe(st.rtbase, "/".join(parts[1:]))
            return self.send_file(f) if f else self.send_error(404)
        if parts[0] == "_fonts" and len(parts) > 2 and parts[1] == "g":
            q = urlsplit(self.path).query
            css = font_css(st, "/".join(parts[2:]) + (f"?{q}" if q else ""))
            # sin conexión y sin caché: hoja vacía, la página usa sus fuentes de respaldo
            return self.send_bytes(css or b"/* fuente no disponible sin conexion */", "text/css; charset=utf-8")
        if parts[0] == "_fonts" and len(parts) > 2 and parts[1] == "s":
            f = font_file(st, "/".join(parts[2:]))
            return self.send_file(f) if f else self.send_error(404)
        if parts[0] == "_blob" and len(parts) == 2:
            f = st.blobs.get(parts[1].split(".")[0])
            return self.send_file(f) if f else self.send_error(404, "asset no encontrado")
        if len(parts) == 2 and parts[1] == "canvas" and is_design(st, parts[0]):
            return self.send_file(HERE / "canvas.html")
        if len(parts) == 2 and parts[1] == "deck" and is_deck(st, parts[0]):
            return self.send_file(HERE / "deck.html")
        if len(parts) >= 3 and parts[1] == "project" and parts[-1] == "support.js":
            if st.runtime is None:
                return self.send_error(404, "falta el motor de Design (dc-runtime.js): ver README")
            return self.send_bytes(support_js(st), "text/javascript; charset=utf-8")

        base = st.dirs.get(parts[0])
        if base is None:
            return self.send_error(404)
        rel = "/".join(parts[1:])
        if not rel and not path.endswith("/"):
            self.send_response(301)
            self.send_header("Location", f"/{parts[0]}/")
            self.end_headers()
            return
        f = (self.safe(base, rel) or type_runtime(st, parts[0], rel)) if rel else None
        if f is None and (path.endswith("/") or not rel):
            f = self.safe(base, (rel + "/" if rel else "") + "index.html")
        return self.send_file(f, parts[0]) if f else self.send_error(404)


def main():
    global AUTHOR
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", type=Path, default=os.environ.get("LIENZO_ROOT"),
                    help="carpeta de artifacts (todas las cuentas) o de una cuenta; o LIENZO_ROOT")
    ap.add_argument("--host", default=os.environ.get("LIENZO_HOST") or "127.0.0.1",
                    help="dirección donde escucha; o LIENZO_HOST (por defecto 127.0.0.1; en Docker 0.0.0.0 y el "
                         "puerto se publica solo en 127.0.0.1 del host)")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--author", default=AUTHOR, help="nombre local del usuario; o LIENZO_AUTHOR (por defecto: Local)")
    ap.add_argument("--prefetch-fonts", action="store_true")
    args = ap.parse_args()
    if args.root is None:
        ap.error("falta --root (o la variable LIENZO_ROOT)")
    root = Path(args.root).resolve()
    if not root.is_dir():
        ap.error(f"--root no es una carpeta: {root}")
    AUTHOR = args.author.strip()[:80] or "Local"
    Handler.state = load_state(root)
    if args.prefetch_fonts:
        return prefetch_fonts(Handler.state)
    srv = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"artifacts: {Handler.state.root}\n  {len(Handler.state.manifest)} artifacts, "
          f"{len(Handler.state.blobs)} assets, {len(Handler.state.live)} con db local\n"
          f"  http://127.0.0.1:{args.port}/")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
