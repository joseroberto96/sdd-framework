#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Validador de tamaño de archivo — modo WARN (FW-108, Lote A).

Cubre el contrato de A5: (a) un archivo trackeado de 600 líneas dispara un
WARN en la salida con exit 0; (b) un archivo de 200 líneas no aparece.
Exit code SIEMPRE 0 — el validador nunca bloquea.
"""
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / ".agent" / "scripts"))
import validate_file_size as validator  # noqa: E402

_CLEAN_ENV = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}


def _git(sb, *args):
    return subprocess.run(
        ["git", *args], cwd=sb, capture_output=True, text=True, check=True, env=_CLEAN_ENV,
    )


class TestValidateFileSize(unittest.TestCase):
    def _init_repo(self, sb: Path) -> None:
        _git(sb, "init", "-q")
        _git(sb, "config", "user.email", "test@example.com")
        _git(sb, "config", "user.name", "Test")

    def test_oversized_file_is_flagged_with_exit_zero(self):
        with tempfile.TemporaryDirectory() as tmp:
            sb = Path(tmp)
            self._init_repo(sb)
            big = sb / "big_module.js"
            big.write_text("\n".join(f"line {i}" for i in range(600)), encoding="utf-8")
            _git(sb, "add", "big_module.js")

            findings = validator.find_oversized(sb)

            self.assertEqual(len(findings), 1)
            rel_path, n_lines = findings[0]
            self.assertEqual(rel_path, "big_module.js")
            self.assertGreater(n_lines, 500)

            exit_code = validator.main(sb)
            self.assertEqual(exit_code, 0)

    def test_small_file_is_not_flagged(self):
        with tempfile.TemporaryDirectory() as tmp:
            sb = Path(tmp)
            self._init_repo(sb)
            small = sb / "small_module.js"
            small.write_text("\n".join(f"line {i}" for i in range(200)), encoding="utf-8")
            _git(sb, "add", "small_module.js")

            findings = validator.find_oversized(sb)

            self.assertEqual(findings, [])
            exit_code = validator.main(sb)
            self.assertEqual(exit_code, 0)

    def test_excluded_prefixes_are_never_flagged(self):
        with tempfile.TemporaryDirectory() as tmp:
            sb = Path(tmp)
            self._init_repo(sb)
            (sb / "_project").mkdir()
            big_in_project = sb / "_project" / "big_state_dump.md"
            big_in_project.write_text("\n".join(f"line {i}" for i in range(600)), encoding="utf-8")
            _git(sb, "add", "_project/big_state_dump.md")

            findings = validator.find_oversized(sb)

            self.assertEqual(findings, [])


if __name__ == "__main__":
    unittest.main()
