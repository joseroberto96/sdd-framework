"""FW-298 (5.2.3 L6): los recibos no caducan por pasos que el propio plan prevé.

Casos de campo reproducidos: TL FW-262 y FW-288 (specs a `done` al cerrar),
TL FW-289 (ronda `fail` corregida antes de sellarla), PA FW-274 (documentación
o evidencia anotada con la ronda abierta) y TL FW-290 (`describe_action`).
"""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / ".agent" / "scripts"))
sys.path.insert(0, str(ROOT / "tests"))

import action_contracts
import state_core
import workflow_receipts as wr
import test_workflow_receipts as twr
import test_state_manager as tsm


class ReceiptCase(unittest.TestCase):
    def setUp(self):
        twr.WorkflowReceiptTests.setUp(self)
        self.addCleanup(twr.WorkflowReceiptTests.tearDown, self)

    approve = twr.WorkflowReceiptTests.approve

    def _write(self, rel, text):
        target = self.root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")

    def start(self, reviewer="agent-2"):
        return wr.start_review(
            self.state, self.root, "M1", reviewer_id=reviewer, implementer_id="agent-1",
            mode="INDEPENDENT_SUBAGENT_NO_ISOLATION", now_iso="2026-07-15T12:10:00+00:00",
        )

    def finish(self, result, reviewer="agent-2"):
        return wr.finish_review(
            self.state, self.root, "M1", reviewer_id=reviewer, result=result,
            evidence="revisión con hallazgos" if result == "fail" else "review ok",
            now_iso="2026-07-15T12:12:00+00:00",
        )


class SpecTransitionsTests(ReceiptCase):
    def _set_specs(self, **entry):
        self.state["module_specs"]["M1"] = entry

    def test_required_a_done_con_otra_justificacion_no_caduca(self):
        """TL FW-262/FW-288: al cerrar se marca `done` y se actualiza el texto de la exención."""
        self._set_specs(data_model="required", api_contract="n/a", integration_real="required",
                        justification="sin endpoint público")
        self.approve()
        self._set_specs(data_model="done", api_contract="n/a", integration_real="done",
                        justification="sin endpoint público; modelo documentado al cerrar")
        self.assertEqual(wr.approval_problems(self.root, self.state, "M1"), [])

    def test_required_a_done_sin_n_a_no_caduca(self):
        self._set_specs(data_model="required", api_contract="required", integration_real="required")
        self.approve()
        self._set_specs(data_model="done", api_contract="done", integration_real="done")
        self.assertEqual(wr.approval_problems(self.root, self.state, "M1"), [])

    def test_pasar_de_n_a_a_required_sigue_exigiendo_aprobacion(self):
        self._set_specs(data_model="n/a", api_contract="n/a", justification="nada que modelar")
        self.approve()
        self._set_specs(data_model="required", api_contract="n/a", justification="nada que modelar")
        self.assertTrue(any("module_spec" in p for p in wr.approval_problems(self.root, self.state, "M1")))

    def test_pasar_de_required_a_n_a_sigue_exigiendo_aprobacion(self):
        self._set_specs(data_model="required", api_contract="required")
        self.approve()
        self._set_specs(data_model="n/a", api_contract="required", justification="ya no hay modelo")
        self.assertTrue(any("module_spec" in p for p in wr.approval_problems(self.root, self.state, "M1")))

    def test_recibo_sellado_con_la_formula_anterior_sigue_valido(self):
        """Un adoptante a mitad de módulo no ve caducar su recibo al actualizar el framework."""
        self._set_specs(data_model="n/a", api_contract="required", justification="sin modelo persistido")
        self.approve()
        receipt = self.state["workflow_receipts"]["approvals"]["M1"]
        legacy = wr._stable_hash({
            "data_model": "n/a", "api_contract": "applies",
            "justification": "sin modelo persistido",
        })
        receipt["basis"]["module_spec"] = legacy
        receipt["fingerprint"] = wr.approval_fingerprint(
            receipt["basis"], tier=receipt["tier"], approved_by=receipt["approved_by"],
            approval_evidence=receipt["approval_evidence"], risk_summary=receipt["risk_summary"],
            adr_refs=receipt["adr_refs"],
        )
        self.assertEqual(wr.approval_problems(self.root, self.state, "M1"), [])


class AcceptanceEvidenceTests(ReceiptCase):
    def test_evidencia_en_linea_propia_no_caduca(self):
        self.approve()
        self._write("_project/08_module_acceptance.md",
                    "## M1\n- [x] AC aprobado\n  - Evidencia: tests/test_m1.py rc=0\n✅ Verificado con cliente\n")
        self.assertEqual(wr.approval_problems(self.root, self.state, "M1"), [])

    def test_evidencia_entre_parentesis_no_caduca(self):
        self.approve()
        self._write("_project/08_module_acceptance.md",
                    "## M1\n- [x] AC aprobado (evidencia: tests/test_m1.py rc=0)\n")
        self.assertEqual(wr.approval_problems(self.root, self.state, "M1"), [])

    def test_cambiar_el_texto_del_ac_sigue_caducando(self):
        self.approve()
        self._write("_project/08_module_acceptance.md", "## M1\n- [x] AC distinto — evidencia: x\n")
        self.assertTrue(any("acceptance" in p for p in wr.approval_problems(self.root, self.state, "M1")))


class ReviewRoundTests(ReceiptCase):
    def test_documentacion_con_la_ronda_abierta_no_impide_sellar(self):
        """PA FW-274: anotar evidencia y docs mientras corre la ronda."""
        self.start()
        self._write("_project/05_test_history.md", "# Historial\nronda 1 ok\n")
        self._write("docs/notas.md", "# notas\nhallazgo documental aplicado\n")
        self._write("_project/08_module_acceptance.md", "## M1\n- [x] AC aprobado — evidencia: x\n")
        self.assertEqual(self.finish("pass")["status"], "pass")

    def test_fail_corregido_antes_de_sellar_se_puede_sellar_y_abrir_otra_ronda(self):
        """TL FW-289: ronda 1 devuelve fail, el agente corrige y luego sella."""
        self.start()
        self._write("app.py", "print('corregido tras los hallazgos')\n")
        sealed = self.finish("fail")
        self.assertEqual(sealed["status"], "fail")
        self.assertTrue(sealed.get("corrected_after_fail"))
        self.assertTrue(sealed.get("diff_hash_at_seal"))
        self.assertNotEqual(sealed["diff_hash"], sealed["diff_hash_at_seal"])
        self.assertIn("review independiente no está en pass", wr.review_problems(self.state, "M1"))
        second = self.start("agent-3")
        self.assertEqual(second["status"], "pending")

    def test_pass_con_el_diff_cambiado_sigue_rechazado(self):
        self.start()
        self._write("app.py", "print('cambió durante la ronda')\n")
        with self.assertRaisesRegex(ValueError, "diff cambió"):
            self.finish("pass")

    def test_fail_sin_cambios_no_marca_correccion(self):
        self.start()
        sealed = self.finish("fail")
        self.assertFalse(sealed.get("corrected_after_fail"))


class DescribeActionEquivalenceTests(unittest.TestCase):
    """TL FW-290: el contrato público coincide con lo que el motor exige."""

    def test_derived_from_es_obligatorio_en_refresh(self):
        contract = action_contracts.describe_action("refresh_approval_receipt")
        self.assertIn("derived_from", contract["required_keys"])
        self.assertNotIn("derived_from", contract["optional_keys"])
        self.assertIn("derived_from", contract["example"])
        self.assertTrue(contract["example"]["derived_from"])

    def test_el_motor_rechaza_refresh_sin_derived_from_nombrandolo(self):
        helper = type("H", (tsm.TestMCPTransport,), {"runTest": lambda self: None})()
        helper.setUp()
        self.addCleanup(helper.tearDown)
        with self.assertRaisesRegex(ValueError, "derived_from"):
            helper.manager.mutate("refresh_approval_receipt", {
                "module": "mod-x", "parent_fingerprint": "abc", "evidence": "e",
            })

    def test_todas_las_acciones_tienen_contrato_coherente(self):
        for action in action_contracts.list_actions():
            contract = action_contracts.describe_action(action)
            required, optional = set(contract["required_keys"]), set(contract["optional_keys"])
            self.assertFalse(required & optional, f"{action}: clave a la vez obligatoria y opcional")
            known = required | optional
            self.assertLessEqual(set(contract["example"]), known, f"{action}: el ejemplo usa claves fuera del contrato")
            self.assertLessEqual(required, set(contract["example"]), f"{action}: el ejemplo omite claves obligatorias")
            self.assertLessEqual(set(contract["fields"]), known, f"{action}: campos descritos fuera del contrato")

    def test_el_motor_no_exige_como_obligatoria_una_clave_declarada_opcional(self):
        """Prueba cada acción con recibos: omitir una clave opcional no puede citarla como «requiere»."""
        import re
        source = (ROOT / ".agent" / "scripts" / "workflow_receipts.py").read_text(encoding="utf-8")
        engine_required = set(re.findall(r"(?:requiere|requires)\s+([a-z_]+)\b", source))
        optional = set(action_contracts.describe_action("refresh_approval_receipt")["optional_keys"])
        self.assertFalse(engine_required & optional, engine_required & optional)


if __name__ == "__main__":
    unittest.main()
