"""Wiring de la regla artefacto-primero + presupuesto de evidencia (know-how
portado de graphify: query-first con --budget en vez de exploración abierta).

La regla vive en .agent/skills/prompt_lean.md (fuente canónica de lean-context)
y el bloque de contexto mínimo debe llevar un presupuesto de evidencia explícito.
"""
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROMPT_LEAN = ROOT / ".agent" / "skills" / "prompt_lean.md"


class TestArtifactFirstWiring(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = PROMPT_LEAN.read_text(encoding="utf-8")

    def test_regla_artefacto_primero_existe(self):
        self.assertIn("artefacto-primero", self.text.lower())

    def test_prohibe_rederivar_lo_ya_proyectado(self):
        # La regla debe nombrar las proyecciones como fuente antes que releer repo
        self.assertIn("proyecc", self.text.lower())
        self.assertIn("re-derivar", self.text.lower())

    def test_bloque_lean_incluye_presupuesto_de_evidencia(self):
        self.assertIn("PRESUPUESTO DE EVIDENCIA", self.text)

    def test_digest_de_lecciones_como_fuente_lean(self):
        # lean-context debe preferir el digest (FW-A) sobre 22_lessons.md completo
        self.assertIn("22_lessons_digest.md", self.text)


if __name__ == "__main__":
    unittest.main()
