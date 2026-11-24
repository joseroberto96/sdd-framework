# tests/test_decision_translation_core.py
import importlib.util
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


class TestDecisionTranslationSkill(unittest.TestCase):
    def test_skill_exists_with_required_sections(self):
        skill = ROOT / ".agent" / "skills" / "decision_translation_core.md"
        self.assertTrue(skill.exists(), "falta .agent/skills/decision_translation_core.md")
        text = skill.read_text(encoding="utf-8").lower()
        for needle in (
            "core: true",
            "se nota desde fuera",      # regla de clasificación
            "routing de expertise",      # routing al skill de dominio
            "modo a",
            "modo b",
            "modo c",
            "choice_text",               # portabilidad del render
            "register_decision",         # registro vía MCP
            "guardrails",                # subordinación
        ):
            self.assertIn(needle, text, f"la skill debe contener: {needle}")


class TestChoiceTextRow(unittest.TestCase):
    def test_matrix_has_choice_text_row(self):
        text = (ROOT / ".agent" / "adapters" / "_capability_matrix.md").read_text(encoding="utf-8")
        self.assertIn("CHOICE_TEXT", text)
        self.assertIn("elección estructurada", text.lower())


class TestDecisionTranslationWiring(unittest.TestCase):
    def setUp(self):
        self.doctor = _load_doctor()

    def test_wiring_passes_on_real_repo(self):
        errors = []
        old = self.doctor.ROOT
        try:
            self.doctor.ROOT = ROOT
            self.doctor.validate_decision_translation_core_wiring(errors)
        finally:
            self.doctor.ROOT = old
        self.assertEqual(errors, [], f"el repo real debe estar wired: {errors}")

    def test_wiring_fails_when_skill_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            from pathlib import Path as P
            root = P(tmp)
            old = self.doctor.ROOT
            try:
                self.doctor.ROOT = root
                errors = []
                self.doctor.validate_decision_translation_core_wiring(errors)
                self.assertTrue(any("decision_translation_core" in e for e in errors))
            finally:
                self.doctor.ROOT = old


class TestBuilderLanguageCore(unittest.TestCase):
    def test_skill_exists(self):
        """La skill existe. Su vocabulario interno ya no se vigila.

        Pase de resta 2026-07-24: las aserciones sobre el contenido de la
        skill (`lenguaje cotidiano`, `analogía`, `qué significa para ti`…),
        su presencia en los catálogos de `_project/`, su mención en el README
        y en siete workflows, y el orden de la plantilla en preflight-review
        se retiraron. Ninguna tenía consecuencia observable: quien verifica de
        verdad que la salida al builder lleve la plantilla es
        `validate_checkpoint.py`, sobre el texto real emitido.
        """
        skill = ROOT / ".agent" / "skills" / "builder_language_core.md"
        self.assertTrue(skill.exists(), "falta .agent/skills/builder_language_core.md")


class TestBuilderLanguageWiring(unittest.TestCase):
    def setUp(self):
        self.doctor = _load_doctor()

    def test_wiring_passes_on_real_repo(self):
        errors = []
        old = self.doctor.ROOT
        try:
            self.doctor.ROOT = ROOT
            self.doctor.validate_builder_language_core_wiring(errors)
        finally:
            self.doctor.ROOT = old
        self.assertEqual(errors, [], f"builder_language_core debe estar wired: {errors}")

    def test_wiring_fails_when_skill_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            from pathlib import Path as P
            root = P(tmp)
            old = self.doctor.ROOT
            try:
                self.doctor.ROOT = root
                errors = []
                self.doctor.validate_builder_language_core_wiring(errors)
                self.assertTrue(any("builder_language_core" in e for e in errors))
            finally:
                self.doctor.ROOT = old


if __name__ == "__main__":
    unittest.main()
