# -*- coding: utf-8 -*-
"""Wiring declarativo del doctor (3.18.0, FW-069).

Calibración del engine genérico y del self-check de integridad:
- known-good: el repo real pasa el spec completo y la integridad.
- known-bad: una feature muerta en el spec (sin validador que la adjudique)
  hace fallar validate_wiring_spec_integrity; un needle roto hace fallar
  validate_wiring_feature (los negativos por feature viven en los tests
  históricos: test_focus_wiring, test_decision_translation_core, etc., que
  ahora ejercitan este mismo engine a través de los wrappers).
"""
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _load_doctor():
    path = ROOT / ".agent" / "scripts" / "framework_doctor.py"
    spec = importlib.util.spec_from_file_location("framework_doctor", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class TestWiringSpec(unittest.TestCase):
    def setUp(self):
        self.doctor = _load_doctor()
        self.doctor.ROOT = ROOT

    def test_spec_file_parses_and_has_features(self):
        spec_path = ROOT / ".agent" / "scripts" / "doctor_wiring.json"
        self.assertTrue(spec_path.exists(), "doctor_wiring.json faltante")
        spec = json.loads(spec_path.read_text(encoding="utf-8"))
        self.assertIsInstance(spec.get("features"), dict)
        self.assertGreaterEqual(len(spec["features"]), 10)

    def test_integrity_passes_on_real_repo(self):
        errors: list[str] = []
        self.doctor.validate_wiring_spec_integrity(errors)
        self.assertEqual(errors, [], f"integridad del spec debe pasar en repo real: {errors}")

    def test_integrity_fails_on_dead_feature(self):
        spec = json.loads(
            (ROOT / ".agent" / "scripts" / "doctor_wiring.json").read_text(encoding="utf-8")
        )
        spec["features"]["feature_fantasma"] = {"files": []}
        self.doctor._WIRING_SPEC_CACHE = spec
        errors: list[str] = []
        self.doctor.validate_wiring_spec_integrity(errors)
        self.assertTrue(
            any("feature_fantasma" in e and "entrada muerta" in e for e in errors),
            f"una feature sin validador debe fallar: {errors}",
        )

    def test_all_declared_features_pass_on_real_repo(self):
        manifest = json.loads(
            (ROOT / ".agent" / "workflows" / "manifest.json").read_text(encoding="utf-8")
        )
        spec = self.doctor._wiring_spec()
        for feature in spec["features"]:
            errors: list[str] = []
            self.doctor.validate_wiring_feature(feature, manifest, errors)
            self.assertEqual(errors, [], f"feature '{feature}' rota en repo real: {errors}")

    def test_engine_detects_broken_needle(self):
        spec = json.loads(
            (ROOT / ".agent" / "scripts" / "doctor_wiring.json").read_text(encoding="utf-8")
        )
        spec["features"]["_kb"] = {
            "files": [
                {
                    "path": "CLAUDE.md",
                    "missing_message": "CLAUDE.md faltante",
                    "needles": [
                        {"needle": "cadena-que-no-existe-jamas-xyz", "message": "needle roto detectado"}
                    ],
                }
            ]
        }
        self.doctor._WIRING_SPEC_CACHE = spec
        errors: list[str] = []
        self.doctor.validate_wiring_feature("_kb", None, errors)
        self.assertEqual(errors, ["needle roto detectado"])

    def test_template_only_file_is_skipped_for_initialized_projects(self):
        """La documentación del framework no se impone al README del producto."""
        spec = {
            "features": {
                "_conditional": {
                    "files": [
                        {
                            "path": "README.md",
                            "template_only": True,
                            "missing_message": "README faltante",
                            "needles": [{"needle": "solo-plantilla", "message": "README contaminado"}],
                        }
                    ]
                }
            }
        }
        original_root = self.doctor.ROOT
        original_spec = self.doctor._WIRING_SPEC_CACHE
        with tempfile.TemporaryDirectory() as tmp:
            try:
                root = Path(tmp)
                (root / "_project").mkdir()
                (root / "_project" / "03_project_state.json").write_text(
                    '{"framework_mode":"initialized"}\n', encoding="utf-8"
                )
                (root / "README.md").write_text("README propio del producto\n", encoding="utf-8")
                self.doctor.ROOT = root
                self.doctor._WIRING_SPEC_CACHE = spec
                errors: list[str] = []
                self.doctor.validate_wiring_feature("_conditional", None, errors)
                self.assertEqual(errors, [])
            finally:
                self.doctor.ROOT = original_root
                self.doctor._WIRING_SPEC_CACHE = original_spec


if __name__ == "__main__":
    unittest.main()
