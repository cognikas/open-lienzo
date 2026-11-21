"""Tests de docker/entrypoint.py: install-hook sobre un repo de artifacts de prueba.

  python -m unittest discover -s tests
"""
import io
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "docker"))
import entrypoint  # noqa: E402


class InstallHookTest(unittest.TestCase):
    def setUp(self):
        self.repo = Path(tempfile.mkdtemp())
        (self.repo / ".git").mkdir()
        self.dest = self.repo / ".git" / "hooks" / "pre-commit"

    def tearDown(self):
        shutil.rmtree(self.repo, ignore_errors=True)

    def run_hook(self, *args):
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            return entrypoint.install_hook(["--repo", str(self.repo), *args])

    def test_installs_the_secret_scan(self):
        self.assertEqual(self.run_hook(), 0)
        self.assertEqual(self.dest.read_bytes(), entrypoint.HOOK.read_bytes())
        self.assertIn(b"--scanners secret", self.dest.read_bytes())
        self.assertEqual(self.run_hook(), 0)  # ya instalado: no falla

    def test_keeps_another_hook_unless_forced(self):
        self.dest.parent.mkdir(parents=True)
        self.dest.write_text("#!/bin/sh\necho mio\n", encoding="utf-8")
        self.assertEqual(self.run_hook(), 1)
        self.assertIn("echo mio", self.dest.read_text(encoding="utf-8"))
        self.assertEqual(self.run_hook("--force"), 0)
        self.assertEqual(self.dest.read_bytes(), entrypoint.HOOK.read_bytes())

    def test_needs_a_git_repo(self):
        shutil.rmtree(self.repo / ".git")
        self.assertEqual(self.run_hook(), 1)
        self.assertFalse(self.dest.exists())


if __name__ == "__main__":
    unittest.main()
