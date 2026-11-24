#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Pruebas del gate de tamaño de workflows (Plan 4.5 — Lote 4, Criterio 1).

Verifica que el cuerpo renderizado de ciclo.md y cierre-modulo.md permanezca
estrictamente por debajo de 5.000 tokens (Item 13 del Plan: el gate detecta
workflows que excedan dicho límite).
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
import sys
sys.path.insert(0, str(ROOT / ".agent" / "scripts"))

import framework_doctor as doctor


class WorkflowRenderedSizeGateTests(unittest.TestCase):
    def test_real_workflows_are_under_token_budget(self):
        """Los workflows clave en el repositorio real deben estar bajo 5.000 tokens."""
        errors: list[str] = []
        doctor.validate_workflow_rendered_token_budget(errors)
        self.assertEqual(errors, [], f"Los workflows reales exceden el presupuesto: {errors}")

        # Verificación directa de métricas
        workflows_dir = ROOT / ".agent" / "workflows"
        for name, limit in doctor.WORKFLOW_TOKEN_BUDGETS.items():
            path = workflows_dir / name
            text = path.read_text(encoding="utf-8")
            toks = doctor.estimate_workflow_tokens(text)
            self.assertLessEqual(
                toks,
                limit,
                f"{name} tiene {toks} tokens estimados (> {limit})",
            )
            # También verificamos por heurística de caracteres (<= 20.000 chars)
            self.assertLessEqual(
                len(text),
                20000,
                f"{name} excede 20.000 caracteres: {len(text)}",
            )

    def test_gate_detects_workflow_exceeding_budget(self):
        """Item 13: Workflow con cuerpo renderizado por encima de 5.000 tokens: el gate lo detecta."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_root = Path(tmp)
            wf_dir = tmp_root / ".agent" / "workflows"
            wf_dir.mkdir(parents=True)

            # Workflow simulado con > 5000 tokens (e.g. 6000 palabras / 25000 caracteres)
            bloated_content = "# Bloated Workflow\n\n" + ("Instruccion repetitiva de relleno para exceder el limite.\n" * 450)
            (wf_dir / "ciclo.md").write_text(bloated_content, encoding="utf-8")
            (wf_dir / "cierre-modulo.md").write_text("# Compact closure\n", encoding="utf-8")

            old_root = doctor.ROOT
            doctor.ROOT = tmp_root
            try:
                errors: list[str] = []
                doctor.validate_workflow_rendered_token_budget(errors)
            finally:
                doctor.ROOT = old_root

            self.assertTrue(
                any("ciclo.md" in err and "excede el límite de 5000 tokens" in err for err in errors),
                f"El gate debía detectar el desborde en ciclo.md: {errors}",
            )


if __name__ == "__main__":
    unittest.main()
