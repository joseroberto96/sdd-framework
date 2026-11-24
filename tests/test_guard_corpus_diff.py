#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Diferencial del guard sobre un corpus de comandos (L8, plan 5.2).

Condición de privacidad del builder: el corpus real contiene rutas, nombres y
posibles credenciales, así que NUNCA se escribe dentro de un repositorio. La
herramienta debe negarse, y los datos de estos tests son sintéticos.
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / ".agent" / "scripts" / "guard_corpus_diff.py"

OLD_GUARD = '''
def is_destructive(command, cwd=None):
    return ("BLOQUEA" in command, "motivo viejo" if "BLOQUEA" in command else "")
'''


def run(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(TOOL), *args], capture_output=True, text=True, timeout=120)


class PrivacyTests(unittest.TestCase):
    def test_extract_se_niega_a_escribir_dentro_del_repo(self) -> None:
        destino = ROOT / "corpus_no_debe_existir.jsonl"
        proc = run("extract", "--out", str(destino), "--transcripts", str(ROOT / "nada"))
        self.assertEqual(proc.returncode, 2, proc.stdout + proc.stderr)
        self.assertFalse(destino.exists())
        self.assertIn("repositorio", proc.stderr)

    def test_extract_se_niega_bajo_cualquier_repo_git(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "otro"
            (repo / ".git").mkdir(parents=True)
            proc = run("extract", "--out", str(repo / "sub" / "c.jsonl"), "--transcripts", str(Path(tmp) / "nada"))
            self.assertEqual(proc.returncode, 2, proc.stderr)
            self.assertFalse((repo / "sub").exists())
            self.assertIn("repositorio", proc.stderr)

    def test_no_sigue_enlaces_simbolicos_hacia_el_repo(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            enlace = Path(tmp) / "enlace"
            enlace.symlink_to(ROOT)
            proc = run("extract", "--out", str(enlace / "c.jsonl"), "--transcripts", str(Path(tmp) / "nada"))
            self.assertEqual(proc.returncode, 2, proc.stderr)
            self.assertFalse((ROOT / "c.jsonl").exists())
            self.assertIn("repositorio", proc.stderr)

    def test_diff_se_niega_a_escribir_el_informe_dentro_del_repo(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            corpus = Path(tmp) / "c.jsonl"
            corpus.write_text(json.dumps({"cmd": "ls", "cwd": None}) + "\n", encoding="utf-8")
            informe = ROOT / "informe_no_debe_existir.json"
            proc = run("diff", "--old", str(ROOT / ".agent" / "scripts" / "guard_destructive_command.py"),
                       "--corpus", str(corpus), "--report", str(informe))
            self.assertEqual(proc.returncode, 2, proc.stderr)
            self.assertFalse(informe.exists())
            self.assertIn("repositorio", proc.stderr)


class PrivacyHardeningTests(unittest.TestCase):
    def test_se_niega_dentro_de_un_repo_bare(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bare = Path(tmp) / "r.git"
            (bare / "objects").mkdir(parents=True)
            (bare / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
            proc = run("extract", "--out", str(bare / "c.jsonl"), "--transcripts", str(Path(tmp) / "nada"))
            self.assertEqual(proc.returncode, 2, proc.stderr)
            self.assertIn("repositorio", proc.stderr)

    def test_corpus_e_informe_solo_legibles_por_el_dueno(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "t"
            root.mkdir()
            (root / "s.jsonl").write_text(json.dumps({"message": {"content": [
                {"type": "tool_use", "name": "Bash", "input": {"command": "ls"}}]}, "cwd": "/x"}) + "\n", encoding="utf-8")
            corpus = Path(tmp) / "c.jsonl"
            self.assertEqual(run("extract", "--out", str(corpus), "--transcripts", str(root)).returncode, 0)
            self.assertEqual(corpus.stat().st_mode & 0o777, 0o600)
            informe = Path(tmp) / "i.json"
            old = Path(tmp) / "old.py"
            old.write_text(OLD_GUARD, encoding="utf-8")
            run("diff", "--old", str(old), "--corpus", str(corpus), "--report", str(informe))
            self.assertEqual(informe.stat().st_mode & 0o777, 0o600)


class DiffTests(unittest.TestCase):
    def _diff(self, commands: list[str]) -> tuple[dict, str]:
        with tempfile.TemporaryDirectory() as tmp:
            old = Path(tmp) / "guard_old.py"
            old.write_text(OLD_GUARD, encoding="utf-8")
            corpus = Path(tmp) / "c.jsonl"
            corpus.write_text("".join(json.dumps({"cmd": c, "cwd": None}) + "\n" for c in commands), encoding="utf-8")
            informe = Path(tmp) / "informe.json"
            proc = run("diff", "--old", str(old), "--corpus", str(corpus), "--report", str(informe))
            self.assertIn(proc.returncode, (0, 1), proc.stderr)
            return json.loads(informe.read_text(encoding="utf-8")), proc.stdout

    def test_clasifica_transiciones_y_marca_regresiones(self) -> None:
        # el guard viejo bloquea lo que contiene BLOQUEA; el nuevo es el real
        informe, salida = self._diff(["ls -la", "BLOQUEA ls", "c='rm -rf ~'; eval \"$c\""])
        transiciones = informe["transitions"]
        self.assertEqual(transiciones.get("pass->pass"), 1)
        self.assertEqual(transiciones.get("deny->pass"), 1)   # "BLOQUEA ls": el real lo deja pasar
        self.assertEqual(transiciones.get("pass->ask"), 1)
        self.assertEqual(informe["regressions"]["deny->pass"], 1)
        self.assertNotIn("BLOQUEA", salida)  # la salida estándar no lleva comandos

    def test_salida_estandar_solo_lleva_recuentos(self) -> None:
        _, salida = self._diff(["echo secreto-del-builder-123"])
        self.assertNotIn("secreto-del-builder-123", salida)


if __name__ == "__main__":
    unittest.main()
