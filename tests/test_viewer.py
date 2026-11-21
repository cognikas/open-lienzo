"""Tests del visor local: API de db y de comentarios, inyección del runtime, fuentes y el shim
claude-local.js corriendo de verdad en Chrome headless (si está instalado).

  python3 -m unittest discover tests
"""
import importlib.util
import hashlib
from datetime import datetime
import json
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
SERVE = HERE.parent / "viewer" / "serve.py"
CHROME = Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
ART = "PageAAAAAAAAAAAAAAAAAA"

# Página que ejercita la API db del runtime y deja el resultado en el DOM para leerlo con --dump-dom.
PROBE = """<!doctype html><html><head><meta charset="utf-8"></head><body><pre id="out">pendiente</pre>
<script>
(async () => {
  const out = [];
  const db = await window.claude?.use?.('db');
  try {
    await db.doc('t/a').set({n: 2, tag: 'x', deep: {k: 1, gone: true}});
    await db.doc('t/b').set({n: 1, tag: 'x'});
    await db.doc('t/c').set({n: 3, tag: 'y'});
    await db.doc('t/a').update({deep: {k2: 2, gone: {__delete__: true}}});
    const a = await db.doc('t/a').get();
    out.push('get:' + JSON.stringify(a.data()));
    const q = await db.collection('t').where('tag', '==', 'x').orderBy('n', 'desc').limit(5).get();
    out.push('query:' + q.docs.map(d => d.id).join(','));
    out.push('frozen:' + Object.isFrozen(a.data()));
    let snaps = 0;
    await new Promise(res => {
      const off = db.collection('t').onSnapshot(s => {
        snaps++;
        if (snaps === 1) { out.push('snap1:' + s.size + ':' + s.docChanges().map(c => c.type).join(',')); db.doc('t/b').delete(); }
        else { out.push('snap2:' + s.size + ':' + s.docChanges().map(c => c.type + c.doc.id).join(',')); off(); res(); }
      });
    });
    try { db.doc('t'); out.push('parity:no'); } catch { out.push('parity:ok'); }
    out.push('user:' + (await (await window.claude.use('user')).isOwner()));
    out.push('none:' + (await window.claude.use('nope')));
  } catch (e) { out.push('ERROR ' + (e.code || '') + ' ' + e.message); }
  document.getElementById('out').textContent = out.join('|');
})();
</script></body></html>"""


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class ViewerTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp())
        rt = cls.tmp / "_runtime" / "design-0.0.1" / "artifact-type"
        rt.mkdir(parents=True)
        (rt / "dc-runtime.js").write_text("/* runtime de prueba */", encoding="utf-8")
        acc = cls.tmp / "cuenta"
        (acc / ART).mkdir(parents=True)
        (acc / ART / "index.html").write_text(PROBE, encoding="utf-8")
        (acc / "Static0000000000000000").mkdir()
        (acc / "Static0000000000000000" / "index.html").write_text(
            '<html><head><link href="https://fonts.googleapis.com/css2?family=Inter&amp;display=swap" rel="stylesheet"></head></html>', encoding="utf-8")
        (acc / ART / "project").mkdir()
        (acc / ART / "project" / "B.dc.html").write_text("<x-dc><h2>Hola</h2></x-dc>", encoding="utf-8")
        (acc / "manifest.json").write_text(json.dumps([{"id": ART, "title": "Sonda"},
                                                        {"id": "Static0000000000000000", "title": "Estática"}]), encoding="utf-8")
        cls.port = free_port()
        cls.base = f"http://127.0.0.1:{cls.port}"
        cls.proc = subprocess.Popen([sys.executable, str(SERVE), "--root", str(cls.tmp), "--port", str(cls.port)],
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(50):
            try:
                urllib.request.urlopen(cls.base + "/", timeout=1)
                break
            except OSError:
                time.sleep(0.1)

    @classmethod
    def tearDownClass(cls):
        cls.proc.terminate()
        cls.proc.wait()
        shutil.rmtree(cls.tmp)

    def req(self, method, path, data=None, host=None):
        r = urllib.request.Request(self.base + path, method=method,
                                   data=None if data is None else json.dumps({"data": data}).encode(),
                                   headers={"Content-Type": "application/json", **({"Host": host} if host else {})})
        try:
            with urllib.request.urlopen(r, timeout=5) as resp:
                body = resp.read()
                return resp.status, json.loads(body) if body and resp.headers.get_content_type() == "application/json" else body
        except urllib.error.HTTPError as e:
            return e.code, None

    # --- edición de boards desde el lienzo ---
    def test_board_edit_checks_prev(self):
        q = f"/_api/board/{ART}?path=B.dc.html"
        st, got = self.req("GET", q)
        self.assertEqual((st, got["src"]), (200, "<x-dc><h2>Hola</h2></x-dc>"))
        new = "<x-dc><h2>Chau</h2></x-dc>"
        self.assertEqual(self.req("PUT", q, {"src": new, "prev": "0" * 64})[0], 409)  # alguien lo cambió: no se pisa
        st, put = self.req("PUT", q, {"src": new, "prev": got["sha"]})
        self.assertEqual(st, 200)
        self.assertEqual(self.req("GET", q)[1], {"src": new, "sha": put["sha"]})
        self.req("PUT", q, {"src": got["src"], "prev": put["sha"]})

    def test_board_edit_rejects_bad_input(self):
        for path in ("../manifest.json", "B.html", "Nope.dc.html", ".x.dc.html"):
            self.assertIn(self.req("PUT", f"/_api/board/{ART}?path={path}", {"src": "<x-dc></x-dc>", "prev": ""})[0], (400, 404), path)
        sha = self.req("GET", f"/_api/board/{ART}?path=B.dc.html")[1]["sha"]
        self.assertEqual(self.req("PUT", f"/_api/board/{ART}?path=B.dc.html", {"src": "no es un board", "prev": sha})[0], 400)

    # --- db ---
    def test_db_roundtrip(self):
        p = f"/_api/doc/{ART}?path=u/one"
        self.assertEqual(self.req("PUT", p, {"a": 1, "o": {"x": 1, "y": 2}})[0], 204)
        self.assertEqual(self.req("PATCH", p, {"o": {"y": {"__delete__": True}, "z": 3}})[0], 204)
        self.assertEqual(self.req("GET", p)[1], {"exists": True, "data": {"a": 1, "o": {"x": 1, "z": 3}}})
        self.assertEqual(self.req("GET", f"/_api/col/{ART}?path=u")[1], [{"id": "one", "data": {"a": 1, "o": {"x": 1, "z": 3}}}])
        self.assertEqual(self.req("DELETE", p)[0], 204)
        self.assertFalse(self.req("GET", p)[1]["exists"])

    def test_db_tilde_saved_as_at(self):
        self.assertEqual(self.req("PUT", f"/_api/doc/{ART}?path=u/a~b", {"v": 1})[0], 204)
        self.assertTrue((self.tmp / "cuenta" / ART / "_db" / "u" / "a@b.json").is_file())
        self.assertEqual(self.req("GET", f"/_api/col/{ART}?path=u")[1][0]["id"], "a~b")

    def test_db_rejects_bad_input(self):
        self.assertEqual(self.req("GET", f"/_api/doc/{ART}?path=solo")[0], 400)           # paridad: colección como doc
        self.assertEqual(self.req("GET", f"/_api/doc/{ART}?path=u/a@b")[0], 400)          # @ literal es ambiguo
        self.assertEqual(self.req("GET", f"/_api/doc/{ART}?path=../x")[0], 400)
        self.assertEqual(self.req("GET", "/_api/doc/NoExiste0000000000000?path=u/a")[0], 400)
        self.assertEqual(self.req("PATCH", f"/_api/doc/{ART}?path=u/nuevo", {"a": 1})[0], 400)  # update exige que exista
        self.assertEqual(self.req("GET", f"/_api/doc/{ART}?path=u/a", host="evil.example")[0], 403)

    def test_api_rejects_cross_site_writes(self):
        api = f"{self.base}/_api/comments/{ART}"
        body = json.dumps({"data": {"text": "csrf", "anchor": {"kind": "x"}}}).encode()
        for headers in ({"Content-Type": "text/plain"}, {"Content-Type": "application/json", "Origin": "https://evil.example"}):
            with self.assertRaises(urllib.error.HTTPError) as e:
                urllib.request.urlopen(urllib.request.Request(api, data=body, method="POST", headers=headers))
            self.assertEqual(e.exception.code, 403)

    # --- comentarios ---
    def test_comments_crud(self):
        api = f"/_api/comments/{ART}"
        s, c = self.req("POST", api, {"text": "Revisar el título", "anchor": {"kind": "slide", "slide": "s1", "x": 10, "y": 20}})
        self.assertEqual(s, 200)
        self.assertEqual((c["author"], c["resolved"]), ("Local", False))
        self.assertTrue((self.tmp / "cuenta" / ART / "_comments" / f"{c['id']}.json").is_file())
        self.assertEqual(self.req("PATCH", f"{api}?cid={c['id']}", {"resolved": True})[1]["resolved"], True)
        self.assertEqual([x["id"] for x in self.req("GET", api)[1]], [c["id"]])
        self.assertEqual(self.req("DELETE", f"{api}?cid={c['id']}")[0], 200)
        self.assertEqual(self.req("GET", api)[1], [])

    def test_comments_patch_types(self):
        api = f"/_api/comments/{ART}"
        c = self.req("POST", api, {"text": "hola", "anchor": {"kind": "slide"}})[1]
        self.assertEqual(self.req("PATCH", f"{api}?cid={c['id']}", {"text": 123})[0], 400)
        self.assertEqual(self.req("PATCH", f"{api}?cid={c['id']}", {"resolved": "sí"})[0], 400)
        self.assertEqual(self.req("PATCH", f"{api}?cid={c['id']}", {"text": "chau"})[1]["text"], "chau")
        self.req("DELETE", f"{api}?cid={c['id']}")

    def test_artifact_without_slash_redirects(self):
        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, *a, **k):
                return None
        try:
            urllib.request.build_opener(NoRedirect).open(f"{self.base}/{ART}")
            self.fail("esperaba 301")
        except urllib.error.HTTPError as e:
            self.assertEqual((e.code, e.headers["Location"]), (301, f"/{ART}/"))

    def test_comments_validation(self):
        api = f"/_api/comments/{ART}"
        self.assertEqual(self.req("POST", api, {"text": "  ", "anchor": {}})[0], 400)
        self.assertEqual(self.req("POST", api, {"text": "hola"})[0], 400)
        self.assertEqual(self.req("PATCH", f"{api}?cid=../../etc", {"resolved": True})[0], 400)
        self.assertEqual(self.req("DELETE", f"{api}?cid=noexiste")[0], 404)

    # --- páginas ---
    def test_shim_injected_for_optional_chaining(self):
        body = urllib.request.urlopen(f"{self.base}/{ART}/index.html").read().decode()
        self.assertIn('/_viewer/claude-local.js" data-art="' + ART, body)

    def test_static_page_gets_no_shim_and_local_fonts(self):
        body = urllib.request.urlopen(f"{self.base}/Static0000000000000000/").read().decode()
        self.assertNotIn("claude-local.js", body)
        self.assertIn('href="/_fonts/g/css2?family=Inter', body)
        self.assertNotIn("fonts.googleapis.com", body)

    def test_index_lists_account(self):
        body = urllib.request.urlopen(self.base + "/").read().decode()
        data = json.loads(body.split('<script type="application/json" id="data">', 1)[1].split("</script>", 1)[0])
        self.assertEqual({r["id"] for r in data}, {ART, "Static0000000000000000"})
        self.assertEqual({r["account"] for r in data}, {"cuenta"})
        self.assertTrue(all("app" in r and "entity" in r for r in data))
        self.assertTrue(next(r for r in data if r["id"] == ART)["href"].endswith(f"/{ART}/"))

    def test_index_modified_is_local_change(self):
        spec = importlib.util.spec_from_file_location("serve_mod", SERVE)
        serve = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(serve)
        d = Path(tempfile.mkdtemp())
        try:
            (d / "index.html").write_text("hola", encoding="utf-8")
            (d / "_comments").mkdir()
            (d / "_comments" / "c.json").write_text("{}", encoding="utf-8")  # comentarios y db no cuentan como cambio del artifact
            synced = {"index.html": hashlib.sha256(b"hola").hexdigest()}
            today = datetime.now().strftime("%Y-%m-%d")
            self.assertEqual(serve.local_change(d, synced), "")                       # igual a lo sincronizado
            self.assertEqual(serve.local_change(d, {"index.html": "otro"}), today)     # editado en local
            self.assertEqual(serve.local_change(d, {**synced, "b.html": "x"}), today)  # algo se borró
            self.assertEqual(serve.local_change(d, None), today)                       # solo local
            (d / "project").mkdir()
            (d / "project" / "canvas.json").write_text("{}", encoding="utf-8")
            nested = {**synced, "project/canvas.json": hashlib.sha256(b"{}").hexdigest()}
            self.assertEqual(serve.local_change(d, nested), "")  # claves POSIX del manifest, también en Windows
        finally:
            shutil.rmtree(d)

    def test_starts_without_design_runtime(self):
        """El motor de Design es de Anthropic y no viene en el repo: sin él, el servidor arranca, el resto
        anda y support.js responde 404 en vez de un motor."""
        root = Path(tempfile.mkdtemp())
        try:
            acc = root / "cuenta"
            (acc / ART / "project").mkdir(parents=True)
            (acc / ART / "project" / "canvas.json").write_text("{}", encoding="utf-8")
            (acc / "manifest.json").write_text(json.dumps([{"id": ART, "title": "Sin motor"}]), encoding="utf-8")
            port = free_port()
            proc = subprocess.Popen([sys.executable, str(SERVE), "--root", str(root), "--port", str(port),
                                     "--author", "Ana"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            try:
                base = f"http://127.0.0.1:{port}"
                for _ in range(50):
                    try:
                        urllib.request.urlopen(base + "/", timeout=1)
                        break
                    except OSError:
                        time.sleep(0.1)
                self.assertEqual(urllib.request.urlopen(f"{base}/{ART}/canvas", timeout=5).status, 200)
                with self.assertRaises(urllib.error.HTTPError) as e:
                    urllib.request.urlopen(f"{base}/{ART}/project/support.js", timeout=5)
                self.assertEqual(e.exception.code, 404)
                r = urllib.request.Request(f"{base}/_api/comments/{ART}", method="POST", headers={"Content-Type": "application/json"},
                                           data=json.dumps({"data": {"text": "x", "anchor": {"kind": "canvas", "x": 1, "y": 2}}}).encode())
                with urllib.request.urlopen(r, timeout=5) as resp:
                    self.assertEqual(json.loads(resp.read())["author"], "Ana")
            finally:
                proc.terminate()
                proc.wait()
        finally:
            shutil.rmtree(root)

    @unittest.skipUnless(CHROME.exists(), "Chrome no está instalado")
    def test_shim_in_browser(self):
        dom = subprocess.run([str(CHROME), "--headless=new", "--disable-gpu", "--virtual-time-budget=8000", "--dump-dom",
                              f"{self.base}/{ART}/index.html"], capture_output=True, text=True, timeout=60, encoding="utf-8").stdout
        out = dom.split('<pre id="out">', 1)[1].split("</pre>", 1)[0]
        self.assertNotIn("ERROR", out)
        parts = dict(p.split(":", 1) for p in out.split("|"))
        self.assertEqual(json.loads(parts["get"]), {"n": 2, "tag": "x", "deep": {"k": 1, "k2": 2}})
        self.assertEqual(parts["query"], "a,b")             # where + orderBy desc + limit
        self.assertEqual(parts["frozen"], "true")
        self.assertEqual(parts["snap1"], "3:added,added,added")
        self.assertEqual(parts["snap2"], "2:removedb")      # docChanges ve el borrado
        self.assertEqual(parts["parity"], "ok")
        self.assertEqual(parts["user"], "true")
        self.assertEqual(parts["none"], "null")


if __name__ == "__main__":
    unittest.main()
