# -*- coding: utf-8 -*-
"""Política de evolución v2 "Evolución Sustractiva" (4.12.0, ADR-055).

Calibración:
- known-good: el repo real declara las secciones canónicas de la v2 y el
  cableado (pase de resta en retrospectiva, EARS graduado, frontera en
  GUARDRAILS) está presente.
- known-bad: una política sin una sección canónica de la v2 falla en el doctor.
"""
import importlib.util
import re
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOCTOR_PATH = ROOT / ".agent" / "scripts" / "framework_doctor.py"


def load_doctor():
    spec = importlib.util.spec_from_file_location("framework_doctor", DOCTOR_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _norm(text: str) -> str:
    """Colapsa espacios/saltos para asertar frases que el wrap parte en líneas."""
    return re.sub(r"\s+", " ", text)


class TestEvolutionPolicyV2(unittest.TestCase):
    def setUp(self):
        self.doctor = load_doctor()
        self.doctor.ROOT = ROOT
        self.policy = (ROOT / ".agent" / "docs" / "framework_evolution_policy.md").read_text(encoding="utf-8")

    def test_policy_passes_doctor_on_real_repo(self):
        errors = []
        self.doctor.validate_evolution_policy(errors)
        self.assertEqual(errors, [])

    def test_policy_without_v2_section_is_rejected(self):
        # known-bad: conserva las claves v1 (freeze, retroporte) y las demás v2
        # pero omite la cadencia por lotes — el doctor debe fallar por ella.
        original_root = self.doctor.ROOT
        with tempfile.TemporaryDirectory() as tmp:
            try:
                self.doctor.ROOT = Path(tmp)
                (Path(tmp) / ".agent" / "docs").mkdir(parents=True)
                (Path(tmp) / ".agent" / "docs" / "framework_evolution_policy.md").write_text(
                    "# Política\nfeature freeze. retroporte. núcleo eterno. capa volátil.\n"
                    "vigilancia trimestral. delegación al harness. regla de resta.\n",
                    encoding="utf-8",
                )
                errors = []
                self.doctor.validate_evolution_policy(errors)
                self.assertTrue(any("cadencia por lotes" in e for e in errors), errors)
            finally:
                self.doctor.ROOT = original_root

    def test_policy_declares_quarterly_review_date(self):
        # La vigilancia trimestral debe llevar fecha de próxima revisión —
        # sin fecha, la ventana no es operativa.
        self.assertRegex(
            self.policy,
            r"próxima revisión: \*\*20\d\d-\d\d\*\*",
            "la vigilancia trimestral debe declarar fecha de próxima revisión (AAAA-MM)",
        )

    def test_policy_declares_three_paradigm_signals(self):
        for señal in (
            "Memoria/estado de proyecto nativo del harness",
            "SDD nativo integrado",
            "Loops de planning/verificación nativos",
        ):
            self.assertIn(señal, self.policy, f"señal de paradigma ausente: {señal}")

    def test_pase_de_resta_prohibe_el_borrado_automatico(self):
        """Invariante real: el pase de resta propone, nunca borra solo.

        El resto de aserciones de este bloque (título de la sección, nombres
        de las tres decisiones, formato de la tabla del informe, plantillas
        EARS, vocabulario de GUARDRAILS, entrada del CHANGELOG) se retiraron
        en el pase de resta de 2026-07-24: vigilaban la redacción de
        documentos de doctrina, sin consecuencia observable. Lo que queda es
        lo único con consecuencia: si esta prohibición desaparece del
        workflow, una retrospectiva puede borrar superficie sin decisión
        humana.
        """
        text = _norm((ROOT / ".agent" / "workflows" / "retrospectiva-framework.md").read_text(encoding="utf-8"))
        self.assertIn("nunca borrar de forma automática", text,
                      "el pase de resta debe prohibir el borrado automático")


if __name__ == "__main__":
    unittest.main()
