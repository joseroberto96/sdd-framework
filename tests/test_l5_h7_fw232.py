#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests para el Lote L5 del plan 5.1: TL FW-235, TL FW-234 y TL FW-232."""

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / ".agent" / "scripts"))

import framework_doctor  # noqa: E402
import state_core  # noqa: E402
import validate_transition as vt  # noqa: E402


class TestClarificationAndBugPendingRestoration(unittest.TestCase):
    """TL FW-235: resolve_clarification y resolve_bug restauran not_started
    si el módulo está en modules.pending, evitando error de bucket en el doctor."""

    def test_pending_module_clarification_cycle_restores_not_started(self):
        m = state_core.StateManager.__new__(state_core.StateManager)
        m.state_data = {
            "_bugs": [],
            "_clarifications": [],
            "modules": {"completed": [], "in_progress": [], "pending": ["mod-pending"]},
            "module_statuses": {"mod-pending": "not_started"},
            "last_completed_step": "",
        }

        # 1. Registrar clarificación bloqueante sobre módulo en pending
        reg_payload = {
            "blocked_scope": "mod-pending",
            "question": "¿Cuál es la política de retry?",
            "blocks_start": True,
        }
        res = m._apply_event("register_clarification", reg_payload, "2026-09-18T12:00:00Z")
        self.assertIn("registrada", res)
        self.assertEqual(m.state_data["module_statuses"]["mod-pending"], "blocked")
        self.assertIn("mod-pending", m.state_data["modules"]["pending"])
        self.assertEqual(reg_payload.get("_status_effect"), {"module": "mod-pending", "status": "blocked"})

        clar = m.state_data["_clarifications"][0]
        self.assertEqual(clar.get("prev_status"), "not_started")

        # 2. Resolver la clarificación
        resolve_payload = {
            "id": clar["id"],
            "answer": "3 reintentos con backoff exponencial",
        }
        res_solve = m._apply_event("resolve_clarification", resolve_payload, "2026-09-18T12:05:00Z")
        self.assertIn("resuelta", res_solve)

        # Debe volver a not_started porque mod-pending está en modules.pending
        self.assertEqual(m.state_data["module_statuses"]["mod-pending"], "not_started")
        self.assertIn("mod-pending", m.state_data["modules"]["pending"])
        self.assertEqual(
            resolve_payload.get("_status_effect"),
            {"module": "mod-pending", "status": "not_started"},
        )

        # El doctor valida consistencia de buckets sin errores
        errors = []
        warnings = []
        framework_doctor.validate_state(m.state_data, errors, warnings)
        bucket_errors = [e for e in errors if "modules.pending contiene" in e or "bucket" in e]
        self.assertEqual(bucket_errors, [], f"No debe haber error de bucket en doctor: {errors}")

    def test_in_progress_module_clarification_cycle_restores_in_progress(self):
        m = state_core.StateManager.__new__(state_core.StateManager)
        m.state_data = {
            "_bugs": [],
            "_clarifications": [],
            "modules": {"completed": [], "in_progress": ["mod-active"], "pending": []},
            "module_statuses": {"mod-active": "in_progress"},
            "last_completed_step": "",
        }

        reg_payload = {
            "blocked_scope": "mod-active",
            "question": "¿Formato de fecha?",
            "blocks_implementation": True,
        }
        m._apply_event("register_clarification", reg_payload, "2026-09-18T12:00:00Z")
        self.assertEqual(m.state_data["module_statuses"]["mod-active"], "blocked")

        clar_id = m.state_data["_clarifications"][0]["id"]
        resolve_payload = {"id": clar_id, "answer": "ISO 8601"}
        m._apply_event("resolve_clarification", resolve_payload, "2026-09-18T12:05:00Z")

        self.assertEqual(m.state_data["module_statuses"]["mod-active"], "in_progress")
        self.assertEqual(
            resolve_payload.get("_status_effect"),
            {"module": "mod-active", "status": "in_progress"},
        )

    def test_pending_module_resolve_bug_restores_not_started(self):
        m = state_core.StateManager.__new__(state_core.StateManager)
        m.state_data = {
            "_bugs": [
                {
                    "id": "BUG-001",
                    "module_id": "mod-pending",
                    "severity": "Crítico",
                    "affects_ac": False,
                    "status": "Abierto",
                }
            ],
            "_clarifications": [],
            "modules": {"completed": [], "in_progress": [], "pending": ["mod-pending"]},
            "module_statuses": {"mod-pending": "blocked"},
            "last_completed_step": "",
        }

        resolve_payload = {"id": "BUG-001", "fix": "reparado"}
        m._apply_event("resolve_bug", resolve_payload, "2026-09-18T12:10:00Z")
        self.assertEqual(m.state_data["module_statuses"]["mod-pending"], "not_started")
        self.assertEqual(
            resolve_payload.get("_status_effect"),
            {"module": "mod-pending", "status": "not_started"},
        )

    def test_validate_transition_status_effect_for_clarifications(self):
        reg_effect = vt._status_effect(
            "register_clarification",
            {"_status_effect": {"module": "M1", "status": "blocked"}},
        )
        self.assertEqual(reg_effect, ("M1", "blocked"))

        resolve_effect = vt._status_effect(
            "resolve_clarification",
            {"_status_effect": {"module": "M1", "status": "not_started"}},
        )
        self.assertEqual(resolve_effect, ("M1", "not_started"))


class TestModuleAcceptanceDoctorWarning(unittest.TestCase):
    """TL FW-234: aviso cosmético cuando Estado: en 08 contradice module_statuses."""

    def test_desynchronized_estado_emits_cosmetic_warning_with_canonical_status(self):
        original_root = framework_doctor.ROOT
        with tempfile.TemporaryDirectory() as tmp:
            try:
                root = Path(tmp)
                framework_doctor.ROOT = root
                project_dir = root / "_project"
                project_dir.mkdir(parents=True)

                # 08 desincronizado: el módulo M1 tiene encabezado "Estado: 🔄 En progreso"
                # pero el estado canónico ya es "completed"
                acceptance_content = (
                    "# ACs\n\n"
                    "## Módulo 1 — M1\n"
                    "Estado: 🔄 En progreso\n\n"
                    "- [x] AC1 — evidencia: prueba pasada con éxito de extremo a extremo\n\n"
                    "Estado: ✅ Verificado con cliente\n"
                )
                (project_dir / "08_module_acceptance.md").write_text(acceptance_content, encoding="utf-8")

                state = {
                    "module_statuses": {"M1": "completed"},
                }
                warnings = []
                framework_doctor.validate_module_acceptance_status_alignment("initialized", state, warnings)

                self.assertTrue(len(warnings) >= 1, f"Debe emitir warning: {warnings}")
                warn_msg = warnings[0]
                self.assertIn("M1", warn_msg)
                self.assertIn("cosmético", warn_msg.lower())
                self.assertIn("canónico", warn_msg.lower())
                self.assertIn("completed", warn_msg)
            finally:
                framework_doctor.ROOT = original_root

    def test_synchronized_estado_emits_no_warning(self):
        original_root = framework_doctor.ROOT
        with tempfile.TemporaryDirectory() as tmp:
            try:
                root = Path(tmp)
                framework_doctor.ROOT = root
                project_dir = root / "_project"
                project_dir.mkdir(parents=True)

                acceptance_content = (
                    "# ACs\n\n"
                    "## Módulo 1 — M1\n"
                    "Estado: ✅ Verificado con cliente\n\n"
                    "- [x] AC1 — evidencia: prueba pasada con éxito de extremo a extremo\n"
                )
                (project_dir / "08_module_acceptance.md").write_text(acceptance_content, encoding="utf-8")

                state = {
                    "module_statuses": {"M1": "completed"},
                }
                warnings = []
                framework_doctor.validate_module_acceptance_status_alignment("initialized", state, warnings)

                self.assertEqual(warnings, [])
            finally:
                framework_doctor.ROOT = original_root


class TestPreflightReviewVerificationTag(unittest.TestCase):
    """TL FW-232: paso 4.c y plantilla Hallazgos con VERIFICADO / NO_VERIFICADO."""

    def test_preflight_review_has_step_4c_and_tags(self):
        wf_path = ROOT / ".agent" / "workflows" / "preflight-review.md"
        subagent_path = ROOT / ".agent" / "subagents" / "preflight-reviewer.md"

        wf_text = wf_path.read_text(encoding="utf-8")
        subagent_text = subagent_path.read_text(encoding="utf-8")

        self.assertIn("4.c ETIQUETADO DE VERIFICACIÓN", wf_text)
        self.assertIn("VERIFICADO (archivo:línea | comando)", wf_text)
        self.assertIn("NO_VERIFICADO", wf_text)
        self.assertIn("no puede sostener un `pass`", wf_text)

        self.assertIn("VERIFICADO (archivo:línea | comando)", subagent_text)
        self.assertIn("NO_VERIFICADO", subagent_text)


if __name__ == "__main__":
    unittest.main()
