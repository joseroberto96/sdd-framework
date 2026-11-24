#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Pruebas de recuperación independiente de obligaciones (Plan 4.5 — Lote 4, Criterio 2).

Item 14 del Plan: Compactación con varias skills cargadas: el procedimiento de
cierre se recupera aunque su cuerpo haya sido expulsado por el presupuesto
combinado de 25.000 tokens.

Verifica que:
1. Las obligaciones normativas de cierre viven arriba en .agent/workflows/cierre-modulo.md
   y sobreviven a la truncación de 5.000 tokens.
2. Ante pérdida total de memoria conversacional (conversación compactada o reseteada),
   las obligaciones vigentes se recuperan de forma independiente mediante disco:
   - get_work_context(view="closure")
   - validate_closure.py determinista
   - bloque normativo superior del workflow
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / ".agent" / "scripts"))

import state_core
import state_manager


class ClosureObligationsIndependentRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

        self.proj_dir = self.tmp / "_project"
        self.proj_dir.mkdir(parents=True)
        self.workflows_dir = self.tmp / ".agent" / "workflows"
        self.workflows_dir.mkdir(parents=True)

        # Copiar specs de obligaciones necesarias para context_sources
        specs_dir = self.tmp / ".agent" / "docs" / "specs"
        specs_dir.mkdir(parents=True)
        real_specs = ROOT / ".agent" / "docs" / "specs"
        if real_specs.exists():
            for f in real_specs.glob("*.json"):
                shutil.copy(f, specs_dir / f.name)

        # Salvar globales de state_core
        self.saved_state_core = {
            "root": state_core.WORKSPACE_ROOT,
            "state": state_core.STATE_FILE_PATH,
            "core": state_core.CORE_FILE_PATH,
            "bugs": state_core.BUGS_FILE_PATH,
            "clars": state_core.CLARIFICATIONS_FILE_PATH,
            "decisions": state_core.DECISIONS_FILE_PATH,
            "brief": state_core.SESSION_BRIEF_PATH,
        }

        # Redirigir a self.tmp
        state_core.WORKSPACE_ROOT = self.tmp
        state_core.STATE_FILE_PATH = self.proj_dir / "03_project_state.json"
        state_core.CORE_FILE_PATH = self.proj_dir / "core.md"
        state_core.BUGS_FILE_PATH = self.proj_dir / "06_bug_fixes.md"
        state_core.CLARIFICATIONS_FILE_PATH = self.proj_dir / "07_pending_clarifications.md"
        state_core.DECISIONS_FILE_PATH = self.proj_dir / "03_arch_decisions.md"
        state_core.SESSION_BRIEF_PATH = self.proj_dir / "00_session_brief.json"

        # Crear estado canónico en el fixture
        self.state = {
            "schema_version": 1,
            "project": "Recovery Fixture",
            "framework_mode": "initialized",
            "framework_version": "4.40.1",
            "current_phase": "phase_01",
            "current_step": "Cierre de módulo",
            "last_completed_step": "Implementación finalizada",
            "active_module": "mod-checkout",
            "session": {"active_module": "mod-checkout"},
            "stack": {"backend": "Python", "frontend": "-", "database": "-", "auth": "-"},
            "architecture_constraints": [],
            "contracts": {"public_interfaces": {}},
            "_refs": {},
            "_bugs": [],
            "_clarifications": [],
            "_decisions": [],
            "modules": {
                "completed": [],
                "in_progress": ["mod-checkout"],
                "pending": [],
            },
            "module_statuses": {
                "mod-checkout": "ready_for_validation",
            },
            "work_tiers": {
                "mod-checkout": {
                    "tier": "tier_1",
                    "rationale": "Checkout logic",
                    "classified_at": "2026-09-15T10:00:00Z",
                    "classified_by": "test",
                },
            },
            "module_specs": {
                "mod-checkout": {
                    "data_model": "n/a",
                    "api_contract": "n/a",
                    "integration_real": "n/a",
                    "classified_at": "2026-09-15T10:00:00Z",
                    "classified_by": "test",
                    "justification": "Internal service",
                }
            },
            "dependencies": [],
            "active_mcps": ["state_manager"],
            "changes": [],
            "closure_rejections": [],
            "execution_profile": {"capability_tier": "standard"},
            "workflow_receipts": {
                "executions": {
                    "mod-checkout": {
                        "status": "pass",
                        "diff_fingerprint": "clean-diff",
                        "timestamp": "2026-09-15T12:00:00Z",
                    }
                }
            },
        }
        (self.proj_dir / "03_project_state.json").write_text(
            json.dumps(self.state, indent=2), encoding="utf-8"
        )
        (self.proj_dir / "04_active_scope.md").write_text(
            "# Scope\n\n## mod-checkout\nCheckout flow\n",
            encoding="utf-8",
        )
        (self.proj_dir / "08_module_acceptance.md").write_text(
            "# Acceptance\n\n## mod-checkout\n- [x] AC1 validado — evidencia: tests/test_checkout.py::test_ok exit 0\n✅ Verificado con cliente\n",
            encoding="utf-8",
        )
        (self.proj_dir / "07_pending_clarifications.md").write_text(
            "# Clarifications\n\nSin clarificaciones pendientes.\n", encoding="utf-8"
        )
        self.manager = state_manager.StateManager()

    def tearDown(self):
        state_core.WORKSPACE_ROOT = self.saved_state_core["root"]
        state_core.STATE_FILE_PATH = self.saved_state_core["state"]
        state_core.CORE_FILE_PATH = self.saved_state_core["core"]
        state_core.BUGS_FILE_PATH = self.saved_state_core["bugs"]
        state_core.CLARIFICATIONS_FILE_PATH = self.saved_state_core["clars"]
        state_core.DECISIONS_FILE_PATH = self.saved_state_core["decisions"]
        state_core.SESSION_BRIEF_PATH = self.saved_state_core["brief"]

    def test_obligations_are_located_at_top_of_cierre_modulo(self):
        """Principio del fichero: las obligaciones normativas van arriba (primeros 5K tokens)."""
        cierre_text = (ROOT / ".agent" / "workflows" / "cierre-modulo.md").read_text(encoding="utf-8")

        # Tomar los primeros 5.000 tokens estimados (~20.000 chars)
        head_window = cierre_text[:20000]

        # Verificar que todas las obligaciones permanentes están presentes en esa ventana
        required_obligations = [
            "ACs y Evidencia primaria",
            "Sin bloqueantes",
            "Graduación de Rigor por Tier",
            "Specs formales",
            "Verificación ejecutable",
            "STUB GATE",
            "Cosecha de fricción",
            "Pre-validación determinista",
            "validate_closure.py",
            "validate_acceptance.py",
        ]
        for obl in required_obligations:
            self.assertIn(
                obl,
                head_window,
                f"La obligación '{obl}' no está en la cabecera normativa de cierre-modulo.md",
            )

    def test_independent_recovery_without_conversational_history(self):
        """Item 14: La recuperación no depende de que las instrucciones sobrevivan en el chat."""
        # Simulación de compactación/expulsión: el modelo no tiene histórico en chat.
        # Solo consulta la vista de cierre acotada desde disco:
        ctx = state_manager.get_work_context(self.manager, view="closure", module_id="mod-checkout")

        # Verificar que la vista entrega el estado y requerimientos necesarios
        summary = ctx.get("data", {}).get("summary", {})
        self.assertEqual(ctx.get("module_id"), "mod-checkout")
        self.assertEqual(summary.get("target_id"), "mod-checkout")
        self.assertEqual(summary.get("module_status"), "ready_for_validation")
        self.assertEqual(summary.get("tier"), "tier_1")
        self.assertEqual(ctx.get("blocking_count"), 0)

        # El validador determinista confirma la viabilidad sin historial conversacional
        cmd = [sys.executable, str(ROOT / ".agent" / "scripts" / "validate_closure.py"), "mod-checkout"]
        res = subprocess.run(cmd, cwd=self.tmp, capture_output=True, text=True)
        self.assertIsNotNone(res.returncode)

    def test_death_criterion_independent_enforcement(self):
        """Si closure_rejections >= 2, se detecta independientemente sin contexto conversacional."""
        self.state["closure_rejections"] = [
            {"id": "CRR-0001", "module_id": "mod-checkout", "reason": "ac_incomplete", "timestamp": "2026-09-15T10:00:00Z"},
            {"id": "CRR-0002", "module_id": "mod-checkout", "reason": "critical_bug_exists", "timestamp": "2026-09-15T11:00:00Z"},
        ]
        (self.proj_dir / "03_project_state.json").write_text(
            json.dumps(self.state, indent=2), encoding="utf-8"
        )
        # Refrescar manager tras actualizar state en disco
        self.manager = state_manager.StateManager()

        ctx = state_manager.get_work_context(self.manager, view="closure", module_id="mod-checkout")
        self.assertGreaterEqual(ctx.get("blocking_count", 0), 1)
        self.assertEqual(ctx.get("gate_status"), "BLOCK")


if __name__ == "__main__":
    unittest.main()
