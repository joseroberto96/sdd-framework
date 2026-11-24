# -*- coding: utf-8 -*-
"""FW-084: la sección de privacidad del perfil debe estar referenciada por los
workflows que dan de alta el proyecto; si no, nunca se rellena."""
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SECTION = "Privacidad del Proveedor de IA"


class PrivacySectionWiringTests(unittest.TestCase):
    def test_section_exists_in_profile(self):
        text = (ROOT / "_project" / "20_builder_profile.md").read_text(encoding="utf-8")
        self.assertIn(SECTION, text)

    def test_alta_workflows_reference_section(self):
        for name in ("nuevo-proyecto.md", "migrar-proyecto.md"):
            text = (ROOT / ".agent" / "workflows" / name).read_text(encoding="utf-8")
            self.assertIn(SECTION, text, f"{name} no referencia la sección de privacidad")


if __name__ == "__main__":
    unittest.main()
