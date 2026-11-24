#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""FW-052: /actualizar-framework debe reconciliar los _project/ framework-adjacent.

Al subir de versión, los guards de skills core exigen que `_project/19_core_skills.md`
(catálogo) y `_project/14_skill_provenance.md` (procedencia Ponytail) estén al día, pero
el upgrade no copia `_project/`. Además, el radar `_project/17_skill_recommendations.md`
puede quedar atrás y hacer que nuevas skills core no se sugieran. En saltos de varias
versiones esos archivos quedan atrás y el doctor falla o pierde señal. El workflow debe
documentar la reconciliación y preservar las skills de dominio del producto (no solo
`imported/`).
"""
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".agent" / "workflows" / "actualizar-framework.md"


class TestUpgradeReconcile(unittest.TestCase):
    def test_documents_project_adjacent_reconciliation(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("19_core_skills.md", text, "el upgrade debe reconciliar el catálogo de skills core")
        self.assertIn("17_skill_recommendations.md", text, "el upgrade debe reconciliar el radar de skills core")
        self.assertIn("14_skill_provenance.md", text, "el upgrade debe reconciliar la procedencia (Ponytail)")
        self.assertIn("FW-052", text)

    def test_documents_builder_language_recommendation_for_upgrade(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("builder_language_core", text, "el upgrade debe mencionar la nueva skill de lenguaje de builder")
        self.assertIn("Lenguaje de Builder", text, "el upgrade debe preservar la señal user-facing de Lenguaje de Builder")

    def test_preserves_domain_and_imported_skills(self):
        text = WORKFLOW.read_text(encoding="utf-8").lower()
        self.assertIn("domain", text, "el upgrade debe preservar skills/domain del producto")
        self.assertIn("imported", text, "el upgrade debe preservar skills/imported del producto")

    def test_copies_subagents_surface(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn(".agent/subagents/", text, "el upgrade debe propagar contratos de subagentes")


if __name__ == "__main__":
    unittest.main()
