#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""FW-238: versión mínima de Python declarada y diagnosticada con verdad.

El README pedía "Python 3"; el código usa `tomllib` (3.11+) y la CI prueba
3.12–3.14. Con el Python 3.9 de macOS, el doctor de un adoptante Codex daba
FAIL diciendo que `.codex/config.toml` "no es TOML válido" con el TOML correcto:
el síntoma apuntaba al fichero equivocado.
"""
from __future__ import annotations

import builtins
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / ".agent" / "scripts"
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import codex_runtime_smoke  # noqa: E402
import ensure_codex_integration  # noqa: E402
import framework_doctor  # noqa: E402
from test_codex_integration import _copy_codex_runtime  # noqa: E402

_real_import = builtins.__import__


def _sin_tomllib(name, *args, **kwargs):
    if name == "tomllib":
        raise ModuleNotFoundError("No module named 'tomllib'")
    return _real_import(name, *args, **kwargs)


class DoctorPythonVersionTests(unittest.TestCase):
    def _check(self, version: tuple[int, int, int]) -> tuple[list[str], list[str]]:
        errors: list[str] = []
        warnings: list[str] = []
        with mock.patch.object(framework_doctor.sys, "version_info", version):
            framework_doctor.validate_python_version(errors, warnings)
        return errors, warnings

    def test_python_antiguo_falla_con_mensaje_propio(self):
        errors, _ = self._check((3, 9, 6))
        self.assertEqual(len(errors), 1, errors)
        self.assertIn("3.9.6", errors[0])
        self.assertIn("3.11", errors[0])

    def test_python_311_avisa_que_no_esta_probado(self):
        errors, warnings = self._check((3, 11, 9))
        self.assertEqual(errors, [])
        self.assertTrue(any("3.12" in w for w in warnings), warnings)

    def test_python_probado_no_dice_nada(self):
        self.assertEqual(self._check((3, 12, 0)), ([], []))


class TomllibMissingDiagnosisTests(unittest.TestCase):
    def _codex_project(self, tmp: str) -> Path:
        root = Path(tmp)
        _copy_codex_runtime(root)
        ensure_codex_integration.ensure_codex_integration(root, "Codex", native_only=True)
        (root / "_project").mkdir()
        (root / "_project" / "15_environment_profile.md").write_text("- Herramienta: Codex\n", encoding="utf-8")
        return root

    def test_doctor_no_culpa_al_toml_si_falta_tomllib(self):
        original_root = framework_doctor.ROOT
        with tempfile.TemporaryDirectory() as tmp:
            try:
                framework_doctor.ROOT = self._codex_project(tmp)
                errors: list[str] = []
                with mock.patch.object(builtins, "__import__", side_effect=_sin_tomllib):
                    framework_doctor.validate_codex_integration("initialized", {}, errors, [])
            finally:
                framework_doctor.ROOT = original_root
        texto = "\n".join(errors)
        self.assertNotIn("no es TOML válido", texto)
        self.assertIn("tomllib", texto)
        self.assertIn("3.11", texto)

    def test_smoke_no_culpa_al_toml_si_falta_tomllib(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._codex_project(tmp)
            checks: dict = {}
            with mock.patch.object(builtins, "__import__", side_effect=_sin_tomllib):
                codex_runtime_smoke._structural_checks(root, checks)
            detalle = checks["codex_config"]["detail"]
            self.assertNotIn("ilegible", detalle)
            self.assertIn("tomllib", detalle)


if __name__ == "__main__":
    unittest.main()
