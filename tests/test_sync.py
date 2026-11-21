"""Tests de sync.py y dedupe.py sobre un repo de prueba en un directorio temporal.

  python3 -m unittest discover tests
"""
import argparse
import io
import json
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "sync"))
import dedupe  # noqa: E402
import sync  # noqa: E402

ID = "ArtAAAAAAAAAAAAAAAAAAA"


class SyncTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.root = self.tmp / "cuenta"
        (self.root / ID / "_db" / "c").mkdir(parents=True)
        (self.root / ID / "index.html").write_text("<p>v1</p>", encoding="utf-8")
        (self.root / ID / "_db" / "c" / "d.json").write_text('{"n": 1}', encoding="utf-8")
        (self.root / ID / "_comments").mkdir()
        (self.root / ID / "_comments" / "x.json").write_text("{}", encoding="utf-8")
        (self.root / "manifest.json").write_text(json.dumps([{"id": ID, "url": "u", "title": "T", "updated": "2026-01-01"}]), encoding="utf-8")
        self._orig = sync.account_root
        sync.account_root = lambda acc: self.root.resolve()

    def tearDown(self):
        sync.account_root = self._orig
        shutil.rmtree(self.tmp)

    def mark(self, **kw):
        a = argparse.Namespace(**{"root": self.root, "id": ID, "account": "cuenta", "url": None, "version": None,
                                  "title": None, "entity": None, **kw})
        with redirect_stdout(io.StringIO()):
            sync.cmd_mark(a)
        return next(e for e in json.loads((self.root / "manifest.json").read_text(encoding="utf-8")) if e["id"] == ID)

    def test_page_files_skip_db_and_comments(self):
        self.assertEqual(list(sync.page_files(self.root, ID)), ["index.html"])
        self.assertEqual(list(sync.db_files(self.root, ID)), ["c/d.json"])

    def test_diff(self):
        self.assertEqual(sync.diff({"a": "1", "b": "2"}, {"b": "3", "c": "4"}),
                         {"added": ["c"], "removed": ["a"], "changed": ["b"]})

    def test_mark_updates_date_only_when_content_changes(self):
        e = self.mark(version="v1")
        first = e["updated"]
        self.assertNotEqual(first, "2026-01-01")             # primera sync: no había hashes, cuenta como cambio
        e["updated"] = "2026-01-02"
        mf = json.loads((self.root / "manifest.json").read_text(encoding="utf-8"))
        mf[0]["updated"] = "2026-01-02"
        (self.root / "manifest.json").write_text(json.dumps(mf), encoding="utf-8")
        self.assertEqual(self.mark(version="v1")["updated"], "2026-01-02")  # re-marcar sin cambios no la toca

    def test_mark_takes_remote_date(self):
        self.assertEqual(self.mark(version="v1", updated="2026-09-27T10:00:00Z")["updated"], "2026-09-27")  # la de claude.ai, no la del día
        (self.root / ID / "index.html").write_text("<p>v2</p>", encoding="utf-8")
        e = self.mark(version="v2")
        self.assertNotEqual(e["updated"], "2026-01-02")
        self.assertEqual([h["version"] for h in e["history"]], ["v1", "v2"])

    def test_manifest_is_utf8_whatever_the_locale(self):
        # Windows sin PYTHONUTF8 escribe cp1252 por defecto, que no tiene "⇄": el manifest se abre siempre en UTF-8
        title = "POS Kozen ⇄ Payment Cryptography — MAC & DUKPT · Árbol"
        self.assertEqual(self.mark(version="v1", title=title)["title"], title)
        self.assertIn(title, (self.root / "manifest.json").read_bytes().decode("utf-8"))

    def test_new_local_artifact(self):
        a = argparse.Namespace(account="cuenta", title="Árbol de diálogo", entity="demo", app=None)
        out = io.StringIO()
        with redirect_stdout(out):
            sync.cmd_new(a)
            sync.cmd_new(a)  # mismo título el mismo día: id distinto
        ids = [e["id"] for e in json.loads((self.root / "manifest.json").read_text(encoding="utf-8")) if e.get("local_only")]
        self.assertEqual(len(set(ids)), 2)
        self.assertTrue(all(i.startswith("local-") and "arbol-de-dialogo" in i and (self.root / i).is_dir() for i in ids))

    def test_files_map_with_explicit_types(self):
        (self.root / ID / "game.mjs").write_text("export {}", encoding="utf-8")
        (self.root / ID / "style.css").write_text("", encoding="utf-8")
        out = io.StringIO()
        with redirect_stdout(out):
            sync.cmd_files(argparse.Namespace(root=self.root, id=ID))
        m = json.loads(out.getvalue())
        self.assertNotIn("index.html", m)                      # la página va en file_path
        self.assertEqual(m["game.mjs"], {"from": "game.mjs", "contentType": "text/javascript"})
        self.assertEqual(m["style.css"], "style.css")

    def test_sha_matches_hashlib(self):
        import hashlib
        p = self.root / ID / "index.html"
        self.assertEqual(sync.sha(p), hashlib.sha256(p.read_bytes()).hexdigest())


class DedupeTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def design(self, name, asset_id, envelope, created, board="<x-dc>hola</x-dc>"):
        d = self.tmp / name
        (d / "project").mkdir(parents=True)
        (d / "assets").mkdir()
        (d / "assets" / f"{asset_id}.png").write_bytes(b"PNG-misma-imagen")
        (d / "index.html").write_text(envelope, encoding="utf-8")
        (d / "project" / "canvas.json").write_text(json.dumps({"boards": {}, "createdOnFiles": {"v": 1, "at": created}}), encoding="utf-8")
        (d / "project" / "Main.dc.html").write_text(f'<img src="/_blob/{asset_id}">{board}', encoding="utf-8")
        return d

    def test_republication_is_same_content(self):
        a = self.design("a", "a" * 32, "<script>flags de la cuenta A</script>", "2026-01-01T09:00:00Z")
        b = self.design("b", "b" * 32, "<script>flags de la cuenta B</script>", "2026-01-01T16:00:00Z")
        self.assertEqual(dedupe.fingerprint(a)[0], dedupe.fingerprint(b)[0])

    def test_real_change_is_different(self):
        a = self.design("a", "a" * 32, "x", "t")
        b = self.design("b", "b" * 32, "x", "t", board="<x-dc>chau</x-dc>")
        self.assertNotEqual(dedupe.fingerprint(a)[0], dedupe.fingerprint(b)[0])

    def test_db_counts_as_content_but_comments_do_not(self):
        a = self.design("a", "a" * 32, "x", "t")
        before = dedupe.fingerprint(a)[0]
        (a / "_comments").mkdir()
        (a / "_comments" / "c.json").write_text("{}", encoding="utf-8")
        self.assertEqual(dedupe.fingerprint(a)[0], before)
        (a / "_db" / "c").mkdir(parents=True)
        (a / "_db" / "c" / "d.json").write_text("{}", encoding="utf-8")
        self.assertNotEqual(dedupe.fingerprint(a)[0], before)

    def test_plain_page_index_counts(self):
        d = self.tmp / "p"
        d.mkdir()
        (d / "index.html").write_text("uno", encoding="utf-8")
        before = dedupe.fingerprint(d)[0]
        (d / "index.html").write_text("dos", encoding="utf-8")
        self.assertNotEqual(dedupe.fingerprint(d)[0], before)

    def test_nested_and_same_stem_assets(self):
        d = self.design("a", "a" * 32, "x", "t")
        (d / "assets" / "sub").mkdir()
        (d / "assets" / "sub" / "z.png").write_bytes(b"otra")
        (d / "assets" / f"{'a' * 32}.webp").write_bytes(b"distinta")
        self.assertIsNotNone(dedupe.fingerprint(d)[0])     # no revienta con assets anidados ni stems repetidos

    def test_title_rules(self):
        import entities
        rules = entities.load_rules(Path(__file__).resolve().parent.parent / "sync" / "entities.rules.example.json")
        self.assertEqual(entities.title_rule(rules, "Plan para ACME 2027"), "acme")
        self.assertEqual(entities.title_rule(rules, "Tablero interno"), "empresa")
        self.assertEqual(entities.title_rule(rules, "Acmeísmo"), None)       # palabra completa: "acme" no calza dentro de otra
        self.assertEqual(entities.title_rule(entities.load_rules(None), "ACME"), None)  # sin reglas, nada

    def test_family_key(self):
        self.assertEqual(dedupe.family_key("Juego demo — Los vecinos (E0.6)"), "juego demo los vecinos")
        self.assertEqual(dedupe.family_key("Tablero — Armas v4"), "tablero armas")
        self.assertEqual(dedupe.family_key("v2"), "")


if __name__ == "__main__":
    unittest.main()
