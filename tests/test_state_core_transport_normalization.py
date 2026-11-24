#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_state_core_transport_normalization.py - FW-103 (Example CLI): el
wrapper del tool MCP en el entorno OpenCode corrompe arrays simples de
strings al pasarlos por su transport (XML->JSON), convirtiendo
`adr_refs: ["ADR-1", "ADR-2"]` en `{"item": ["ADR-1", "ADR-2"]}` o en el
array anidado `[["item", "ADR-1", "ADR-2"]]`. `state_core._unwrap_transport_list`
repara ESTRECHAMENTE esos dos patrones antes de que la validación existente
los rechace con el error críptico "APPROVE_RECEIPT referencia ADRs
inexistentes: ['item']". Cualquier otra forma inválida debe seguir
fallando sin cambios.
"""

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / ".agent" / "scripts"))

import state_core
from state_core import _unwrap_transport_list


class UnwrapTransportListUnitTests(unittest.TestCase):
    """Prueba _unwrap_transport_list de forma aislada, sin fixture de estado."""

    def test_dict_item_wrapper_is_unwrapped(self):
        self.assertEqual(
            _unwrap_transport_list({"item": ["ADR-1", "ADR-2"]}),
            ["ADR-1", "ADR-2"],
        )

    def test_nested_item_array_is_unwrapped(self):
        self.assertEqual(
            _unwrap_transport_list([["item", "ADR-1", "ADR-2"]]),
            ["ADR-1", "ADR-2"],
        )

    def test_plain_list_is_returned_untouched(self):
        value = ["ADR-1", "ADR-2"]
        self.assertIs(_unwrap_transport_list(value), value)

    def test_dict_with_other_key_is_not_normalized(self):
        value = {"otra_clave": ["ADR-1"]}
        self.assertIs(_unwrap_transport_list(value), value)

    def test_dict_with_item_plus_extra_key_is_not_normalized(self):
        # Debe tener EXACTAMENTE la clave "item" — dos claves no cuenta.
        value = {"item": ["ADR-1"], "otra": "x"}
        self.assertIs(_unwrap_transport_list(value), value)

    def test_plain_string_is_not_normalized(self):
        value = "ADR-1"
        self.assertIs(_unwrap_transport_list(value), value)

    def test_dict_item_value_not_a_list_is_not_normalized(self):
        value = {"item": "ADR-1"}
        self.assertIs(_unwrap_transport_list(value), value)

    def test_nested_list_with_more_than_one_element_is_not_normalized(self):
        # Solo el patrón de un único elemento envoltorio cuenta.
        value = [["item", "ADR-1"], ["item", "ADR-2"]]
        self.assertIs(_unwrap_transport_list(value), value)

    def test_nested_list_whose_inner_first_element_is_not_item_marker(self):
        value = [["ADR-1", "ADR-2"]]
        self.assertIs(_unwrap_transport_list(value), value)

    def test_empty_list_is_not_normalized(self):
        value = []
        self.assertIs(_unwrap_transport_list(value), value)


class RecordApprovalReceiptTransportNormalizationTests(unittest.TestCase):
    """Prueba de integración: mutate('record_approval_receipt', ...) con los
    dos patrones de corrupción de FW-103, contra un fixture de proyecto
    mínimo válido con ADRs registrados."""

    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.tmp_root = Path(self.tmp_dir.name)

        self.original_state_path = state_core.STATE_FILE_PATH
        self.original_core_path = state_core.CORE_FILE_PATH
        self.original_bugs_path = state_core.BUGS_FILE_PATH
        self.original_clars_path = state_core.CLARIFICATIONS_FILE_PATH
        self.original_decisions_path = state_core.DECISIONS_FILE_PATH
        self.original_session_brief_path = state_core.SESSION_BRIEF_PATH
        self.original_root = state_core.WORKSPACE_ROOT

        (self.tmp_root / "_project").mkdir(parents=True, exist_ok=True)

        state_core.STATE_FILE_PATH = self.tmp_root / "_project" / "03_project_state.json"
        state_core.CORE_FILE_PATH = self.tmp_root / "_project" / "core.md"
        state_core.BUGS_FILE_PATH = self.tmp_root / "_project" / "06_bug_fixes.md"
        state_core.CLARIFICATIONS_FILE_PATH = self.tmp_root / "_project" / "07_pending_clarifications.md"
        state_core.DECISIONS_FILE_PATH = self.tmp_root / "_project" / "03_arch_decisions.md"
        state_core.SESSION_BRIEF_PATH = self.tmp_root / "_project" / "00_session_brief.json"
        state_core.WORKSPACE_ROOT = self.tmp_root

        self.base_state = {
            "project": "TestProject",
            "framework_mode": "template",
            "framework_version": "4.12.0",
            "current_phase": "phase_01",
            "current_step": "init",
            "last_completed_step": "Ninguno",
            "stack": {
                "backend": "Python",
                "frontend": "Vanilla JS",
                "database": "SQLite",
                "auth": "None",
            },
            "dependencies": [],
            "architecture_constraints": [],
            "contracts": {"public_interfaces": {}},
            "modules": {
                "completed": [],
                "in_progress": ["mod-test"],
                "pending": [],
            },
            "module_statuses": {"mod-test": "in_progress"},
            "work_tiers": {
                "mod-test": {
                    "tier": "tier_2",
                    "rationale": "cambio de alto impacto",
                    "classified_at": "2026-06-08T00:00:00Z",
                    "classified_by": "manual",
                }
            },
            "module_specs": {},
            "closure_rejections": [],
            "active_mcps": [],
            "_refs": {},
        }
        import json
        with open(state_core.STATE_FILE_PATH, "w", encoding="utf-8") as f:
            json.dump(self.base_state, f, indent=2, sort_keys=True)

        project = self.tmp_root / "_project"
        (project / "04_active_scope.md").write_text("# mod-test\nScope aprobado\n")
        (project / "08_module_acceptance.md").write_text("## mod-test\n- [ ] AC\n")

        self.manager = state_core.StateManager()
        # ADR-001 y ADR-002 asociados a mod-test — record_approval exige que
        # los adr_refs existan en _decisions con module_id en {module, "framework"}.
        self.manager.mutate("register_decision", {
            "module_id": "mod-test",
            "title": "Decisión 1",
            "context": "contexto 1",
            "decision": "decisión 1",
        })
        self.manager.mutate("register_decision", {
            "module_id": "mod-test",
            "title": "Decisión 2",
            "context": "contexto 2",
            "decision": "decisión 2",
        })
        self.assertIn("ADR-001", [d["id"] for d in self.manager.state_data["_decisions"]])
        self.assertIn("ADR-002", [d["id"] for d in self.manager.state_data["_decisions"]])

    def tearDown(self):
        state_core.STATE_FILE_PATH = self.original_state_path
        state_core.CORE_FILE_PATH = self.original_core_path
        state_core.BUGS_FILE_PATH = self.original_bugs_path
        state_core.CLARIFICATIONS_FILE_PATH = self.original_clars_path
        state_core.DECISIONS_FILE_PATH = self.original_decisions_path
        state_core.SESSION_BRIEF_PATH = self.original_session_brief_path
        state_core.WORKSPACE_ROOT = self.original_root
        self.tmp_dir.cleanup()

    def _approve(self, adr_refs):
        return self.manager.mutate("record_approval_receipt", {
            "module": "mod-test",
            "approved_by": "builder",
            "evidence": "adelante con mod-test",
            "risk_summary": "alto impacto con rollback",
            "adr_refs": adr_refs,
        })

    def test_dict_item_wrapper_pattern_is_normalized_and_receipt_registered(self):
        msg = self._approve({"item": ["ADR-001", "ADR-002"]})
        self.assertIn("payload normalizado", msg)
        self.assertIn("adr_refs", msg)
        receipt = self.manager.state_data["workflow_receipts"]["approvals"]["mod-test"]
        self.assertEqual(receipt["adr_refs"], ["ADR-001", "ADR-002"])

    def test_nested_item_array_pattern_is_normalized_and_receipt_registered(self):
        msg = self._approve([["item", "ADR-001", "ADR-002"]])
        self.assertIn("payload normalizado", msg)
        self.assertIn("adr_refs", msg)
        receipt = self.manager.state_data["workflow_receipts"]["approvals"]["mod-test"]
        self.assertEqual(receipt["adr_refs"], ["ADR-001", "ADR-002"])

    def test_dict_with_unrelated_key_still_fails_as_before(self):
        # No es el patrón de transport OpenCode: la red no se ensancha, debe
        # seguir fallando (hoy: ValueError de ADRs inexistentes, porque el
        # motor no valida el tipo de adr_refs antes de esta llamada).
        with self.assertRaises(ValueError):
            self._approve({"otra_clave": ["ADR-001"]})
        self.assertNotIn("mod-test", self.manager.state_data.get("workflow_receipts", {}).get("approvals", {}))

    def test_plain_string_still_fails_as_before(self):
        with self.assertRaises(ValueError):
            self._approve("ADR-001")
        self.assertNotIn("mod-test", self.manager.state_data.get("workflow_receipts", {}).get("approvals", {}))

    def test_normal_list_payload_is_unaffected_no_spurious_warning(self):
        msg = self._approve(["ADR-001"])
        self.assertNotIn("payload normalizado", msg)
        receipt = self.manager.state_data["workflow_receipts"]["approvals"]["mod-test"]
        self.assertEqual(receipt["adr_refs"], ["ADR-001"])


if __name__ == "__main__":
    unittest.main()
