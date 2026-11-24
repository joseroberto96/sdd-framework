"""FW-278 (5.2.2 L2): recibos de aprobación que caducaban al cumplir el propio plan."""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / ".agent" / "scripts"))
sys.path.insert(0, str(ROOT / "tests"))

import workflow_receipts as wr
import test_workflow_receipts as twr
import test_state_manager as tsm


class AditivoEnSpecsTests(unittest.TestCase):
    def setUp(self):
        twr.WorkflowReceiptTests.setUp(self)
        self.addCleanup(twr.WorkflowReceiptTests.tearDown, self)

    approve = twr.WorkflowReceiptTests.approve

    def _write(self, rel, text):
        (self.root / "_project" / rel).write_text(text, encoding="utf-8")

    def test_documentar_data_model_y_contratos_no_caduca_la_aprobacion(self):
        self.approve()
        self._write("02_technical_plan/data_model.md", "# Modelo\n\n## CashBalance\n- amount: Decimal\n")
        self._write("02_technical_plan/api_contract.yaml", "paths: {}\n/cash:\n  get: {}\n")
        self._write("03_contracts.md", "# Contratos\n\n## CashBalance\nLectura por cuenta.\n")
        problems = wr.approval_problems(self.root, self.state, "M1")
        self.assertEqual(problems, [])
        self.assertTrue(any("derivación aditiva" in w and "data_model" in w for w in problems.warnings))

    def test_cambiar_una_linea_aprobada_del_contrato_sigue_caducando(self):
        self._write("03_contracts.md", "# Contratos\nLectura por cuenta.\n")
        self.approve()
        self._write("03_contracts.md", "# Contratos\nEscritura por cuenta.\n")
        problems = wr.approval_problems(self.root, self.state, "M1")
        self.assertTrue(any("stale" in p and "contracts" in p for p in problems), problems)

    def test_supersede_explica_que_es_a_proposito(self):
        self.approve()
        parent = self.state["workflow_receipts"]["approvals"]["M1"]["fingerprint"]
        adr = self.state["_decisions"][0]
        adr["status"] = "Superseded"
        adr["superseded_by"] = "ADR-002"
        with self.assertRaises(ValueError) as cm:
            wr.refresh_derived_approval(
                self.state, self.root, "M1", parent_fingerprint=parent,
                derived_from=["ADR-001"], evidence="e", now_iso="2026-08-20T10:00:00+00:00",
            )
        msg = str(cm.exception)
        self.assertIn("base aprobada cambió", msg)
        self.assertIn("ADR-001", msg)
        self.assertIn("ADR-056", msg)

    def test_snapshot_ausente_lo_dice(self):
        self.approve()
        receipt = self.state["workflow_receipts"]["approvals"]["M1"]
        parent = receipt["fingerprint"]
        for f in (self.root / "_project" / "approval_snapshots").glob("*.json"):
            f.unlink()
        twr.WorkflowReceiptTests._append_adr(self, "ADR-002")
        with self.assertRaises(ValueError) as cm:
            wr.refresh_derived_approval(
                self.state, self.root, "M1", parent_fingerprint=parent,
                derived_from=["ADR-002"], evidence="e", now_iso="2026-08-20T10:00:00+00:00",
            )
        self.assertIn("snapshot aprobado", str(cm.exception))


class JustificacionConservadaTests(unittest.TestCase):
    def setUp(self):
        tsm.TestMCPTransport.setUp(self)
        self.addCleanup(tsm.TestMCPTransport.tearDown, self)

    def test_required_a_done_sin_justificacion_conserva_la_anterior(self):
        self.manager.mutate("classify_module_spec", {
            "id": "mod-x", "data_model": "required", "api_contract": "n/a",
            "justification": "sin endpoint público", "classified_by": "manual",
        })
        self.manager.mutate("classify_module_spec", {
            "id": "mod-x", "data_model": "done", "api_contract": "n/a",
            "classified_by": "manual",
        })
        spec = self.manager.state_data["module_specs"]["mod-x"]
        self.assertEqual(spec["justification"], "sin endpoint público")
        self.assertEqual(spec["data_model"], "done")

    def test_una_justificacion_nueva_sustituye_a_la_anterior(self):
        for text in ("primera razón válida", "segunda razón válida"):
            self.manager.mutate("classify_module_spec", {
                "id": "mod-x", "data_model": "n/a", "api_contract": "n/a",
                "justification": text, "classified_by": "manual",
            })
        spec = self.manager.state_data["module_specs"]["mod-x"]
        self.assertEqual(spec["justification"], "segunda razón válida")


if __name__ == "__main__":
    unittest.main()
