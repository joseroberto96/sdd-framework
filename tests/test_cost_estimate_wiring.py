#!/usr/bin/env python3
"""L3 (5.2.3): la estimación de coste llega al punto de aprobación del plan."""
from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / ".agent" / "skills" / "decision_translation_core.md"
MARCA = "ESTIMACION_DE_COSTE"


def _flat(text: str) -> str:
    return re.sub(r"[\s>]+", " ", text)


class CostEstimateWiringTests(unittest.TestCase):
    # workflow -> marca del punto donde el plan se presenta o se revisa antes de aprobar
    APPROVAL_POINTS = {
        "ciclo.md": "### Recibo de aprobación del plan",
        "scope-siguiente.md": "Presentar al usuario:",
        "preflight-review.md": "3. REVISIÓN DEL PLAN",
    }
    MAX_DISTANCIA = 20

    def _lines(self, name: str) -> list[str]:
        return (ROOT / ".agent" / "workflows" / name).read_text(encoding="utf-8").splitlines()

    def test_core_skill_states_the_three_elements_and_the_thresholds(self):
        text = _flat(SKILL.read_text(encoding="utf-8"))
        self.assertIn(MARCA, text)
        for exigido in ("3 o más tareas", "más de 10 min", "Duración estimada",
                        "Subagentes previstos", "0 por defecto", "opcionales",
                        "sin base de estimación", "APPROVE_RECEIPT"):
            self.assertIn(exigido, text)

    def test_each_approval_point_carries_the_estimate_next_to_it(self):
        for name, marca in self.APPROVAL_POINTS.items():
            with self.subTest(workflow=name):
                lines = self._lines(name)
                punto = next(i for i, line in enumerate(lines) if marca in line)
                cercanas = [i for i, line in enumerate(lines)
                            if MARCA in line and abs(i - punto) <= self.MAX_DISTANCIA]
                self.assertTrue(cercanas, f"{name}: sin {MARCA} a ≤{self.MAX_DISTANCIA} líneas de «{marca}»")

    def test_each_workflow_points_to_the_core_skill_and_states_the_default(self):
        for name in self.APPROVAL_POINTS:
            with self.subTest(workflow=name):
                text = _flat("\n".join(self._lines(name)))
                self.assertIn("decision_translation_core.md", text)
                self.assertIn("Estimación de coste antes de aprobar", text)
                self.assertRegex(text, r"subagentes previstos \(0 por defecto\)|subagentes previstos: 0")

    def test_the_scope_template_has_an_estimate_line(self):
        text = "\n".join(self._lines("scope-siguiente.md"))
        self.assertIn("### Estimación:", text)

    def test_missing_estimate_in_preflight_is_a_finding_not_a_hard_block(self):
        text = _flat("\n".join(self._lines("preflight-review.md")))
        self.assertIn("hallazgo del plan, no un bloqueo duro", text)


if __name__ == "__main__":
    unittest.main()
