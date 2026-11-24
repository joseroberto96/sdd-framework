import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / ".agent" / "scripts"))

import workflow_receipts as wr
import state_core
import validate_closure as vc


class WorkflowReceiptTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        project = self.root / "_project"
        (project / "02_technical_plan").mkdir(parents=True)
        (project / "01_product_requirements.md").write_text("# PRD\nProducto aprobado\n")
        (project / "02_technical_plan" / "phase_01.md").write_text("# Fase 1\nM1\n")
        (project / "02_technical_plan" / "data_model.md").write_text("# Modelo\n")
        (project / "02_technical_plan" / "api_contract.yaml").write_text("paths: {}\n")
        (project / "03_contracts.md").write_text("# Contratos\n")
        (project / "04_active_scope.md").write_text("# M1\nScope aprobado\n")
        (project / "08_module_acceptance.md").write_text("## M1\n- [ ] AC aprobado\n")
        (self.root / "requirements.txt").write_text("pytest==8.4.1\n")
        (self.root / "requirements.lock").write_text("pytest==8.4.1\n")
        self.state = {
            "current_phase": "phase_01",
            "dependencies": ["pytest==8.4.1"],
            "work_tiers": {"M1": {"tier": "tier_2", "rationale": "alto impacto"}},
            "module_specs": {"M1": {"data_model": "n/a", "api_contract": "n/a"}},
            "_decisions": [{"id": "ADR-001", "module_id": "M1", "decision": "usar cola"}],
        }

    def tearDown(self):
        self.tmp.cleanup()

    def approve(self):
        wr.record_approval(
            self.state,
            self.root,
            "M1",
            approved_by="builder",
            evidence="Sí, adelante con M1",
            risk_summary="cambio Tier 2 con rollback",
            adr_refs=["ADR-001"],
            now_iso="2026-07-15T12:00:00+00:00",
        )

    def test_umbrella_adr_with_module_list_applies_to_every_listed_module(self):
        # TL FW-019 / BOTÓN FW-115/FW-113 (known-good): un ADR paraguas
        # (module_id como lista) cubre CUALQUIERA de los módulos de la
        # iniciativa, sin duplicar el ADR por módulo.
        (self.root / "_project" / "04_active_scope.md").write_text("# M2\nScope aprobado\n")
        (self.root / "_project" / "08_module_acceptance.md").write_text("## M2\n- [ ] AC aprobado\n")
        self.state["work_tiers"]["M2"] = {"tier": "tier_2", "rationale": "misma iniciativa que M1"}
        self.state["module_specs"]["M2"] = {"data_model": "n/a", "api_contract": "n/a"}
        self.state["_decisions"].append({
            "id": "ADR-002", "module_id": ["M1", "M2"], "decision": "arquitectura de la iniciativa",
        })
        receipt = wr.record_approval(
            self.state, self.root, "M2",
            approved_by="builder", evidence="aprobado como parte de la iniciativa",
            risk_summary="riesgo compartido con M1", adr_refs=["ADR-002"],
            now_iso="2026-08-01T00:00:00+00:00",
        )
        self.assertEqual(receipt["status"], "approved")
        self.assertEqual(wr.approval_problems(self.root, self.state, "M2"), [])
        self.assertEqual(wr.approval_receipt_state_problems(self.state, "M2"), [])

    def test_umbrella_adr_still_rejects_module_outside_the_list(self):
        # known-bad: un módulo que NO pertenece a la iniciativa listada en el
        # ADR sigue rechazado -- el paraguas no se vuelve un comodín global.
        self.state["work_tiers"]["M3"] = {"tier": "tier_2", "rationale": "otra iniciativa"}
        self.state["_decisions"].append({
            "id": "ADR-002", "module_id": ["M1", "M2"], "decision": "arquitectura de la iniciativa",
        })
        with self.assertRaisesRegex(ValueError, "otro módulo"):
            wr.record_approval(
                self.state, self.root, "M3",
                approved_by="builder", evidence="intento indebido",
                risk_summary="riesgo", adr_refs=["ADR-002"],
                now_iso="2026-08-01T00:00:00+00:00",
            )

    def test_approval_receipt_is_valid_then_stale_after_scope_change(self):
        self.approve()
        self.assertEqual(wr.approval_receipt_state_problems(self.state, "M1"), [])
        self.assertEqual(wr.approval_problems(self.root, self.state, "M1"), [])
        (self.root / "_project" / "04_active_scope.md").write_text("# M1\nScope cambiado\n")
        self.assertTrue(any("stale" in p for p in wr.approval_problems(self.root, self.state, "M1")))

    def test_checkbox_status_and_evidence_progress_do_not_stale_approval(self):
        self.approve()
        (self.root / "_project" / "08_module_acceptance.md").write_text(
            "## M1\nEstado: ✅ Verificado con cliente\n"
            "- [x] AC aprobado — evidencia: tests/test_m1.py rc=0\n"
        )
        self.assertEqual(wr.approval_problems(self.root, self.state, "M1"), [])

    def test_approval_additive_evidence_and_seal_not_stale_with_warning(self):
        self.approve()
        (self.root / "_project" / "08_module_acceptance.md").write_text(
            "## M1\n"
            "Estado: ✅ Verificado con cliente\n\n"
            "- [x] AC aprobado — evidencia: tests/test_m1.py rc=0\n"
            "Detalle adicional de verificación\n"
        )
        problems = wr.approval_problems(self.root, self.state, "M1")
        self.assertEqual(problems, [])
        self.assertIn("derivación aditiva sin re-sellar: acceptance", problems.warnings)
        self.assertIn("derivación aditiva sin re-sellar: acceptance", wr.approval_warnings(self.root, self.state, "M1"))
        self.assertIn("derivación aditiva sin re-sellar: acceptance", wr.receipt_warnings(self.state, "M1", root=self.root))

    def test_approval_rewritten_ac_text_is_stale(self):
        self.approve()
        (self.root / "_project" / "08_module_acceptance.md").write_text(
            "## M1\n- [x] AC reescrito completamente distinto\n"
        )
        problems = wr.approval_problems(self.root, self.state, "M1")
        self.assertTrue(any("stale" in p for p in problems))
        self.assertIn("acceptance", problems[0])

    def test_approval_contracts_change_is_stale(self):
        self.approve()
        (self.root / "_project" / "03_contracts.md").write_text("# Contratos alterados\n")
        problems = wr.approval_problems(self.root, self.state, "M1")
        self.assertTrue(any("stale" in p for p in problems))
        self.assertIn("contracts", problems[0])

    def test_approval_legacy_receipt_without_basis_version_continues_to_validate(self):
        self.approve()
        receipt = self.state["workflow_receipts"]["approvals"]["M1"]
        receipt.pop("basis_version", None)
        basis_v1 = wr.approval_basis(self.root, self.state, "M1", basis_version=1)
        receipt["basis"] = basis_v1
        receipt["fingerprint"] = wr.approval_fingerprint(
            basis_v1,
            tier=receipt.get("tier"),
            approved_by=receipt.get("approved_by", ""),
            approval_evidence=receipt.get("approval_evidence", ""),
            risk_summary=receipt.get("risk_summary", ""),
            adr_refs=receipt.get("adr_refs", []),
        )
        problems = wr.approval_problems(self.root, self.state, "M1")
        self.assertEqual(problems, [])

    def test_approval_missing_stored_snapshot_is_strict_stale(self):
        self.approve()
        (self.root / "_project" / "08_module_acceptance.md").write_text(
            "## M1\n- [x] AC aprobado\nDetalle aditivo nuevo\n"
        )
        store = self.root / "_project" / wr.APPROVAL_SNAPSHOT_DIR
        for p in store.glob("*.json"):
            p.unlink()
        problems = wr.approval_problems(self.root, self.state, "M1")
        self.assertTrue(any("stale" in p for p in problems))
        self.assertIn("acceptance", problems[0])

    def test_approval_snapshot_does_not_copy_scope_content_into_state(self):
        self.approve()
        receipt = self.state["workflow_receipts"]["approvals"]["M1"]
        # FW-185: el snapshot ya no vive inline en el receipt, sino en el store
        # content-addressed referenciado por `derived_snapshot_ref`.
        self.assertNotIn("derived_snapshot", receipt)
        snapshot = wr.load_derived_snapshot(self.root, receipt)
        serialized = json.dumps(snapshot, ensure_ascii=False)
        self.assertNotIn("Scope aprobado", serialized)
        self.assertTrue(snapshot["active_scope"])

    def test_derived_refresh_allows_only_derived_surfaces(self):
        self.approve()
        parent = self.state["workflow_receipts"]["approvals"]["M1"]["fingerprint"]
        (self.root / "_project" / "04_active_scope.md").write_text("# M1\nScope aprobado\nDetalle aditivo\n")
        wr.refresh_derived_approval(
            self.state,
            self.root,
            "M1",
            parent_fingerprint=parent,
            derived_from=["AC aprobado", "ADR-001"],
            evidence="Detalle técnico aditivo derivado",
            now_iso="2026-07-15T12:05:00+00:00",
        )
        self.assertEqual(wr.approval_problems(self.root, self.state, "M1"), [])

    def _append_adr(self, adr_id, module="M1"):
        """Añade una ADR nueva al estado, como haría register_decision."""
        self.state["_decisions"].append({
            "id": adr_id, "title": f"titulo {adr_id}", "date": "2026-08-20",
            "status": "Aceptada", "module_id": module,
            "context": "c", "decision": "d", "alternatives": "", "consequences": "",
        })

    def test_adding_a_new_adr_does_not_require_full_reapproval(self):
        """FW-124: registrar una ADR nueva es ADITIVO — no invalida el sello.

        Escenario real: M22 de Example Lab aprobado citando ADR-096; el gate de
        revisión exige registrar ADR-097..ADR-103; el cierre se bloqueaba dos
        veces por la misma causa.
        """
        self.approve()
        parent = self.state["workflow_receipts"]["approvals"]["M1"]["fingerprint"]
        self._append_adr("ADR-002")
        self._append_adr("ADR-003")

        receipt = wr.refresh_derived_approval(
            self.state, self.root, "M1",
            parent_fingerprint=parent,
            derived_from=["ADR-002", "ADR-003"],
            evidence="ADRs exigidas por el gate de revisión de la ronda 4",
            now_iso="2026-08-20T10:00:00+00:00",
        )
        self.assertEqual(receipt["kind"], "derived_additive")
        self.assertEqual(receipt["added_decisions"], ["ADR-002", "ADR-003"])
        self.assertEqual(wr.approval_problems(self.root, self.state, "M1"), [])

    def test_altering_an_approved_adr_still_requires_full_reapproval(self):
        """FW-124 no debe aflojar: cambiar una ADR ya aprobada SIGUE bloqueando."""
        self.approve()
        parent = self.state["workflow_receipts"]["approvals"]["M1"]["fingerprint"]
        self.state["_decisions"][0]["decision"] = "una decisión completamente distinta"
        with self.assertRaisesRegex(ValueError, "base aprobada cambió"):
            wr.refresh_derived_approval(
                self.state, self.root, "M1", parent_fingerprint=parent,
                derived_from=["ADR-001"], evidence="e",
                now_iso="2026-08-20T10:00:00+00:00",
            )

    def test_removing_an_approved_adr_still_requires_full_reapproval(self):
        self.approve()
        parent = self.state["workflow_receipts"]["approvals"]["M1"]["fingerprint"]
        self.state["_decisions"] = []
        with self.assertRaisesRegex(ValueError, "base aprobada cambió"):
            wr.refresh_derived_approval(
                self.state, self.root, "M1", parent_fingerprint=parent,
                derived_from=["ADR-001"], evidence="e",
                now_iso="2026-08-20T10:00:00+00:00",
            )

    def test_superseding_an_approved_adr_still_requires_full_reapproval(self):
        """Ronda de revisión final (hallazgo 4): `supersede_decision` colisiona
        deliberadamente con FW-124 y no está testeado en ningún lado. Esto
        FIJA ese comportamiento: supersedir una ADR sobre la que un módulo fue
        aprobado invalida el sello, igual que alterarla o quitarla. Documentado
        en ADR-056 (Consecuencias). Se replica el efecto de
        `state_core.supersede_decision` a mano sobre el mismo dict de estado
        del fixture (mutación in-place de la ADR, como hace la acción real),
        porque este fixture trabaja con `self.state` crudo, no con un
        `StateManager`."""
        self.approve()
        parent = self.state["workflow_receipts"]["approvals"]["M1"]["fingerprint"]
        adr = self.state["_decisions"][0]
        adr["status"] = "Superseded"
        adr["superseded_by"] = "ADR-002"
        adr["superseded_at"] = "2026-08-20T10:00:00+00:00"
        adr["supersede_reason"] = "el mecanismo v1 se sustituyo entero por criterios absolutos"
        with self.assertRaisesRegex(ValueError, "base aprobada cambió"):
            wr.refresh_derived_approval(
                self.state, self.root, "M1", parent_fingerprint=parent,
                derived_from=["ADR-001"], evidence="e",
                now_iso="2026-08-20T10:00:00+00:00",
            )

    def test_legacy_receipt_without_decisions_snapshot_requires_reapproval(self):
        """Degradación segura: sin snapshot no se puede probar aditividad."""
        self.approve()
        receipt = self.state["workflow_receipts"]["approvals"]["M1"]
        parent = receipt["fingerprint"]
        # FW-185: recibo legacy con snapshot inline (sin la clave `decisions`).
        # `load_derived_snapshot` prefiere el inline, así que basta ponerlo aquí.
        snapshot = dict(wr.load_derived_snapshot(self.root, receipt))
        snapshot.pop("decisions", None)
        receipt.pop("derived_snapshot_ref", None)
        receipt["derived_snapshot"] = snapshot
        self._append_adr("ADR-002")
        with self.assertRaisesRegex(ValueError, "base aprobada cambió"):
            wr.refresh_derived_approval(
                self.state, self.root, "M1", parent_fingerprint=parent,
                derived_from=["ADR-002"], evidence="e",
                now_iso="2026-08-20T10:00:00+00:00",
            )

    def test_derived_refresh_rejects_changed_prd_or_dependencies(self):
        self.approve()
        parent = self.state["workflow_receipts"]["approvals"]["M1"]["fingerprint"]
        (self.root / "_project" / "01_product_requirements.md").write_text("# PRD\nProducto distinto\n")
        with self.assertRaisesRegex(ValueError, "base aprobada cambió"):
            wr.refresh_derived_approval(
                self.state, self.root, "M1", parent_fingerprint=parent,
                derived_from=["AC"], evidence="derivado",
                now_iso="2026-07-15T12:05:00+00:00",
            )

    def test_derived_refresh_rejects_rewrite_of_approved_scope(self):
        self.approve()
        parent = self.state["workflow_receipts"]["approvals"]["M1"]["fingerprint"]
        (self.root / "_project" / "04_active_scope.md").write_text(
            "# M1\nScope sustituido por otro comportamiento\n"
        )
        with self.assertRaisesRegex(ValueError, "reescribe o elimina"):
            wr.refresh_derived_approval(
                self.state, self.root, "M1", parent_fingerprint=parent,
                derived_from=["AC"], evidence="supuesta derivación",
                now_iso="2026-07-15T12:05:00+00:00",
            )

    def test_derived_refresh_does_not_ignore_real_requirements_with_pending(self):
        (self.root / "_project" / "04_active_scope.md").write_text(
            "# M1\nMostrar pedidos pendientes\n"
        )
        self.approve()
        parent = self.state["workflow_receipts"]["approvals"]["M1"]["fingerprint"]
        (self.root / "_project" / "04_active_scope.md").write_text(
            "# M1\nBorrar definitivamente pedidos pendientes\n"
        )
        with self.assertRaisesRegex(ValueError, "reescribe o elimina"):
            wr.refresh_derived_approval(
                self.state, self.root, "M1", parent_fingerprint=parent,
                derived_from=["AC"], evidence="supuesta derivación",
                now_iso="2026-07-15T12:05:00+00:00",
            )

    def test_derived_refresh_with_rationale_allows_narrative_rewrite_of_evidenced_section(self):
        # FW-016 (known-good): extender el resumen de una sección YA aprobada,
        # sin tocar compromisos, con additive_rationale explícito → pasa.
        self.approve()
        parent = self.state["workflow_receipts"]["approvals"]["M1"]["fingerprint"]
        (self.root / "_project" / "04_active_scope.md").write_text(
            "# M1\nScope aprobado y cerrado con éxito tras la última iteración\n"
        )
        receipt = wr.refresh_derived_approval(
            self.state, self.root, "M1", parent_fingerprint=parent,
            derived_from=["AC aprobado"],
            evidence="reescritura narrativa del resumen, sin cambios de compromiso",
            now_iso="2026-07-15T12:05:00+00:00",
            additive_rationale="Solo se extendió la redacción de una sección ya "
            "evidenciada; los compromisos y ACs no cambiaron.",
        )
        self.assertEqual(receipt["additive_rationale"], (
            "Solo se extendió la redacción de una sección ya evidenciada; los "
            "compromisos y ACs no cambiaron."
        ))
        self.assertIn("active_scope", receipt["non_additive_surfaces"])

    def test_derived_refresh_rationale_does_not_bypass_contract_change(self):
        # FW-016 (known-bad): additive_rationale NO debe rescatar un cambio de
        # contrato/compromiso (clave crítica) — sigue rechazando siempre.
        self.approve()
        parent = self.state["workflow_receipts"]["approvals"]["M1"]["fingerprint"]
        (self.root / "_project" / "01_product_requirements.md").write_text(
            "# PRD\nProducto distinto\n"
        )
        with self.assertRaisesRegex(ValueError, "base aprobada cambió"):
            wr.refresh_derived_approval(
                self.state, self.root, "M1", parent_fingerprint=parent,
                derived_from=["AC"], evidence="supuesta derivación",
                now_iso="2026-07-15T12:05:00+00:00",
                additive_rationale="esto es solo narrativo, lo prometo",
            )

    def test_derived_refresh_allows_replacing_empty_api_placeholder(self):
        self.approve()
        parent = self.state["workflow_receipts"]["approvals"]["M1"]["fingerprint"]
        (self.root / "_project" / "02_technical_plan" / "api_contract.yaml").write_text(
            "paths:\n  /health:\n    get:\n      responses:\n        '200': {description: OK}\n"
        )
        wr.refresh_derived_approval(
            self.state, self.root, "M1", parent_fingerprint=parent,
            derived_from=["AC aprobado"], evidence="endpoint aditivo derivado",
            now_iso="2026-07-15T12:05:00+00:00",
        )
        self.assertEqual(wr.approval_problems(self.root, self.state, "M1"), [])

    def test_h9_refresh_v1_receipt_with_paragraphs_succeeds_and_migrates_to_v2(self):
        """H9: un recibo v1 (sin basis_version) con plan de fase con párrafos
        debe poder refrescarse ante cambios aditivos legítimos y migrar a v2."""
        # 1. Plan de fase con párrafos (líneas en blanco consecutivas)
        (self.root / "_project" / "02_technical_plan" / "phase_01.md").write_text(
            "# Fase 1\n\nPrimer párrafo con detalle.\n\nSegundo párrafo con contexto.\n",
            encoding="utf-8",
        )
        # 2. Recibo v1 construido como lo hacía la versión anterior: basis calculada con basis_version=1 y SIN campo basis_version
        basis_v1 = wr.approval_basis(self.root, self.state, "M1", basis_version=1)
        # Demostración del fallo H9 original: en v1 y v2 dan hash distinto por los párrafos
        basis_v2 = wr.approval_basis(self.root, self.state, "M1", basis_version=2)
        self.assertNotEqual(basis_v1["phase_plan"], basis_v2["phase_plan"])

        snapshot = wr.approval_derived_snapshot(self.root, self.state, "M1")
        receipt = {
            "status": "approved",
            "kind": "explicit",
            "module": "M1",
            "tier": "tier_2",
            "approved_by": "builder",
            "approval_evidence": "aprobado v1",
            "risk_summary": "riesgo",
            "adr_refs": ["ADR-001"],
            "basis": basis_v1,
            "derived_snapshot_ref": wr.store_derived_snapshot(self.root, snapshot),
            "fingerprint": wr.approval_fingerprint(
                basis_v1,
                tier="tier_2",
                approved_by="builder",
                approval_evidence="aprobado v1",
                risk_summary="riesgo",
                adr_refs=["ADR-001"],
            ),
            "approved_at": "2026-07-15T12:00:00+00:00",
        }
        self.assertNotIn("basis_version", receipt)
        wr._receipts(self.state)["approvals"]["M1"] = receipt
        self.assertEqual(wr.approval_problems(self.root, self.state, "M1"), [])

        # 3. Cambio aditivo legítimo en superficie derivada (data_model.md)
        (self.root / "_project" / "02_technical_plan" / "data_model.md").write_text(
            "# Modelo\nCampo nuevo aditivo\n", encoding="utf-8"
        )

        # 4. Refresh tiene éxito y migra a v2
        refreshed = wr.refresh_derived_approval(
            self.state,
            self.root,
            "M1",
            parent_fingerprint=receipt["fingerprint"],
            derived_from=["data_model.md"],
            evidence="Adición legítima a data_model",
            now_iso="2026-07-15T12:10:00+00:00",
        )
        self.assertEqual(refreshed["kind"], "derived_additive")
        self.assertEqual(refreshed["basis_version"], 3)
        self.assertEqual(refreshed["derived_surfaces"], ["data_model"])
        self.assertEqual(refreshed["basis"], wr.approval_basis(self.root, self.state, "M1", basis_version=3))
        self.assertEqual(wr.approval_problems(self.root, self.state, "M1"), [])

    def test_h9_refresh_v1_receipt_fails_if_phase_plan_actually_changed(self):
        """H9: si el plan de fase cambia de verdad (clave crítica), el refresh
        sigue fallando estrictamente con 'base aprobada cambió: phase_plan'."""
        (self.root / "_project" / "02_technical_plan" / "phase_01.md").write_text(
            "# Fase 1\n\nPrimer párrafo con detalle.\n\nSegundo párrafo con contexto.\n",
            encoding="utf-8",
        )
        basis_v1 = wr.approval_basis(self.root, self.state, "M1", basis_version=1)
        snapshot = wr.approval_derived_snapshot(self.root, self.state, "M1")
        receipt = {
            "status": "approved",
            "kind": "explicit",
            "module": "M1",
            "tier": "tier_2",
            "approved_by": "builder",
            "approval_evidence": "aprobado v1",
            "risk_summary": "riesgo",
            "adr_refs": ["ADR-001"],
            "basis": basis_v1,
            "derived_snapshot_ref": wr.store_derived_snapshot(self.root, snapshot),
            "fingerprint": wr.approval_fingerprint(
                basis_v1,
                tier="tier_2",
                approved_by="builder",
                approval_evidence="aprobado v1",
                risk_summary="riesgo",
                adr_refs=["ADR-001"],
            ),
            "approved_at": "2026-07-15T12:00:00+00:00",
        }
        self.assertNotIn("basis_version", receipt)
        wr._receipts(self.state)["approvals"]["M1"] = receipt

        # Cambiamos de verdad el plan de fase (clave crítica)
        (self.root / "_project" / "02_technical_plan" / "phase_01.md").write_text(
            "# Fase 1 alterada\n\nContenido totalmente nuevo.\n",
            encoding="utf-8",
        )
        (self.root / "_project" / "02_technical_plan" / "data_model.md").write_text(
            "# Modelo\nCampo nuevo aditivo\n", encoding="utf-8"
        )

        with self.assertRaisesRegex(ValueError, r"base aprobada cambió; se requiere nueva aprobación explícita:.*phase_plan"):
            wr.refresh_derived_approval(
                self.state,
                self.root,
                "M1",
                parent_fingerprint=receipt["fingerprint"],
                derived_from=["data_model.md"],
                evidence="Intento de refresh con phase_plan cambiado",
                now_iso="2026-07-15T12:10:00+00:00",
            )

    def test_h9_refresh_v2_receipt_additive_continues_to_work(self):
        """H9: un recibo sellado en v2 (base y versión de 5.2.3) sigue permitiendo refresh derivado aditivo y migra a v3."""
        basis_v2 = wr.approval_basis(self.root, self.state, "M1", basis_version=2)
        snapshot = wr.approval_derived_snapshot(self.root, self.state, "M1", basis_version=2)
        receipt = {
            "status": "approved",
            "kind": "explicit",
            "module": "M1",
            "tier": "tier_2",
            "approved_by": "builder",
            "approval_evidence": "aprobado v2",
            "risk_summary": "riesgo",
            "adr_refs": ["ADR-001"],
            "basis": basis_v2,
            "basis_version": 2,
            "derived_snapshot_ref": wr.store_derived_snapshot(self.root, snapshot),
            "fingerprint": wr.approval_fingerprint(
                basis_v2,
                tier="tier_2",
                approved_by="builder",
                approval_evidence="aprobado v2",
                risk_summary="riesgo",
                adr_refs=["ADR-001"],
            ),
            "approved_at": "2026-07-15T12:00:00+00:00",
        }
        wr._receipts(self.state)["approvals"]["M1"] = receipt
        self.assertEqual(receipt.get("basis_version"), 2)
        parent = receipt["fingerprint"]

        (self.root / "_project" / "02_technical_plan" / "data_model.md").write_text(
            "# Modelo\nCampo aditivo v2\n", encoding="utf-8"
        )
        refreshed = wr.refresh_derived_approval(
            self.state,
            self.root,
            "M1",
            parent_fingerprint=parent,
            derived_from=["data_model.md"],
            evidence="Adición legítima en v2",
            now_iso="2026-07-15T12:10:00+00:00",
        )
        self.assertEqual(refreshed["kind"], "derived_additive")
        self.assertEqual(refreshed["basis_version"], 3)
        self.assertEqual(refreshed["derived_surfaces"], ["data_model"])
        self.assertEqual(wr.approval_problems(self.root, self.state, "M1"), [])

    def test_h9_refresh_v3_receipt_additive_continues_to_work(self):
        """H9: un recibo sellado en v3 nativo permite refresh derivado aditivo."""
        self.approve()
        receipt = self.state["workflow_receipts"]["approvals"]["M1"]
        self.assertEqual(receipt.get("basis_version"), 3)
        parent = receipt["fingerprint"]

        (self.root / "_project" / "02_technical_plan" / "data_model.md").write_text(
            "# Modelo\nCampo aditivo v3\n", encoding="utf-8"
        )
        refreshed = wr.refresh_derived_approval(
            self.state,
            self.root,
            "M1",
            parent_fingerprint=parent,
            derived_from=["data_model.md"],
            evidence="Adición legítima en v3",
            now_iso="2026-07-15T12:10:00+00:00",
        )
        self.assertEqual(refreshed["kind"], "derived_additive")
        self.assertEqual(refreshed["basis_version"], 3)
        self.assertEqual(refreshed["derived_surfaces"], ["data_model"])
        self.assertEqual(wr.approval_problems(self.root, self.state, "M1"), [])

    def test_review_receipt_requires_distinct_reviewer_and_unchanged_diff(self):
        with self.assertRaisesRegex(ValueError, "distinto"):
            wr.start_review(
                self.state, self.root, "M1", reviewer_id="agent-1",
                implementer_id="agent-1", mode="INDEPENDENT_SUBAGENT_NO_ISOLATION",
                now_iso="2026-07-15T12:10:00+00:00",
            )
        wr.start_review(
            self.state, self.root, "M1", reviewer_id="agent-2",
            implementer_id="agent-1", mode="INDEPENDENT_SUBAGENT_NO_ISOLATION",
            now_iso="2026-07-15T12:10:00+00:00",
        )
        (self.root / "app.py").write_text("print('cambió durante review')\n")
        with self.assertRaisesRegex(ValueError, "diff cambió"):
            wr.finish_review(
                self.state, self.root, "M1", reviewer_id="agent-2", result="pass",
                evidence="review ok", now_iso="2026-07-15T12:12:00+00:00",
            )

    def test_passed_review_receipt_is_mechanically_valid(self):
        (self.root / "app.py").write_text("print('estable')\n")
        wr.start_review(
            self.state, self.root, "M1", reviewer_id="agent-2",
            implementer_id="agent-1", mode="INDEPENDENT_SUBAGENT_NO_ISOLATION",
            now_iso="2026-07-15T12:10:00+00:00",
        )
        wr.finish_review(
            self.state, self.root, "M1", reviewer_id="agent-2", result="pass",
            evidence="ACs y diff revisados", now_iso="2026-07-15T12:12:00+00:00",
        )
        current = wr.workspace_diff_hash(self.root)
        self.assertEqual(wr.review_problems(self.state, "M1", current), [])

    def test_review_receipt_detects_mode_change_after_review(self):
        app = self.root / "app.py"
        app.write_text("print('estable')\n")
        wr.start_review(
            self.state, self.root, "M1", reviewer_id="agent-2",
            implementer_id="agent-1", mode="INDEPENDENT_SUBAGENT_NO_ISOLATION",
            now_iso="2026-07-15T12:10:00+00:00",
        )
        wr.finish_review(
            self.state, self.root, "M1", reviewer_id="agent-2", result="pass",
            evidence="diff revisado", now_iso="2026-07-15T12:12:00+00:00",
        )
        os.chmod(app, 0o755)
        problems = wr.review_problems(
            self.state, "M1", wr.workspace_diff_hash(self.root)
        )
        self.assertTrue(any("stale" in p for p in problems), problems)

    def test_review_history_keeps_every_round_and_gate_reads_last(self):
        """FW-165: una ronda `fail` seguida de una ronda `pass` sobre el mismo
        módulo debe dejar AMBAS entradas en el historial — no sobreescribir
        la primera — y el gate de cierre solo debe mirar la última."""
        (self.root / "app.py").write_text("print('ronda 1')\n")
        wr.start_review(
            self.state, self.root, "M1", reviewer_id="agent-2",
            implementer_id="agent-1", mode="INDEPENDENT_SUBAGENT_NO_ISOLATION",
            now_iso="2026-07-15T12:00:00+00:00",
        )
        wr.finish_review(
            self.state, self.root, "M1", reviewer_id="agent-2", result="fail",
            evidence="hallazgo mayor sin resolver", now_iso="2026-07-15T12:05:00+00:00",
        )
        (self.root / "app.py").write_text("print('ronda 2, hallazgo corregido')\n")
        wr.start_review(
            self.state, self.root, "M1", reviewer_id="agent-2",
            implementer_id="agent-1", mode="INDEPENDENT_SUBAGENT_NO_ISOLATION",
            now_iso="2026-07-15T12:10:00+00:00",
        )
        wr.finish_review(
            self.state, self.root, "M1", reviewer_id="agent-2", result="pass",
            evidence="hallazgo corregido, ACs y diff revisados",
            now_iso="2026-07-15T12:15:00+00:00",
        )
        history = self.state["workflow_receipts"]["reviews"]["M1"]
        self.assertEqual(len(history), 2, history)
        self.assertEqual(history[0]["result"], "fail")
        self.assertEqual(history[0]["evidence"], "hallazgo mayor sin resolver")
        self.assertEqual(history[1]["result"], "pass")
        current = wr.workspace_diff_hash(self.root)
        self.assertEqual(wr.review_problems(self.state, "M1", current), [])

    def test_start_review_rejects_new_round_while_previous_still_pending(self):
        wr.start_review(
            self.state, self.root, "M1", reviewer_id="agent-2",
            implementer_id="agent-1", mode="INDEPENDENT_SUBAGENT_NO_ISOLATION",
            now_iso="2026-07-15T12:00:00+00:00",
        )
        with self.assertRaisesRegex(ValueError, "pendiente"):
            wr.start_review(
                self.state, self.root, "M1", reviewer_id="agent-3",
                implementer_id="agent-1", mode="INDEPENDENT_SUBAGENT_NO_ISOLATION",
                now_iso="2026-07-15T12:01:00+00:00",
            )
        self.assertEqual(len(self.state["workflow_receipts"]["reviews"]["M1"]), 1)

    def test_stale_pending_review_can_be_invalidated_before_new_round(self):
        wr.start_review(
            self.state, self.root, "M1", reviewer_id="agent-2",
            implementer_id="agent-1", mode="INDEPENDENT_SUBAGENT_NO_ISOLATION",
            now_iso="2026-07-15T12:00:00+00:00",
        )
        (self.root / "app.py").write_text("print('cambio posterior')\n")

        receipt = wr.invalidate_stale_review(
            self.state,
            self.root,
            "M1",
            approved_by="builder",
            reason="La ronda quedó stale tras reparar el módulo.",
            now_iso="2026-07-15T12:05:00+00:00",
        )

        self.assertEqual(receipt["status"], "fail")
        self.assertTrue(receipt["invalidated"])
        self.assertIn("stale", receipt["evidence"])
        wr.start_review(
            self.state, self.root, "M1", reviewer_id="agent-3",
            implementer_id="agent-1", mode="INDEPENDENT_SUBAGENT_NO_ISOLATION",
            now_iso="2026-07-15T12:06:00+00:00",
        )

    def test_stale_review_invalidation_rejects_current_pending_hash(self):
        wr.start_review(
            self.state, self.root, "M1", reviewer_id="agent-2",
            implementer_id="agent-1", mode="INDEPENDENT_SUBAGENT_NO_ISOLATION",
            now_iso="2026-07-15T12:00:00+00:00",
        )
        with self.assertRaisesRegex(ValueError, "no está stale"):
            wr.invalidate_stale_review(
                self.state,
                self.root,
                "M1",
                approved_by="builder",
                reason="Intento de reinicio sin cambio.",
                now_iso="2026-07-15T12:05:00+00:00",
            )

    def test_h8_invalidate_stale_review_rejects_v2_pending_without_changes(self):
        wr.start_review(
            self.state, self.root, "M1", reviewer_id="agent-2",
            implementer_id="agent-1", mode="INDEPENDENT_SUBAGENT_NO_ISOLATION",
            now_iso="2026-07-15T12:00:00+00:00",
        )
        current_hash = wr.workspace_diff_hash(self.root)
        self.state["workflow_receipts"]["reviews"]["M1"][-1]["diff_hash"] = current_hash.v2
        with self.assertRaisesRegex(ValueError, "no está stale"):
            wr.invalidate_stale_review(
                self.state,
                self.root,
                "M1",
                approved_by="builder",
                reason="Intento invalidar review v2 sin cambios en el código",
                now_iso="2026-07-15T12:05:00+00:00",
            )

    def test_h8_invalidate_stale_review_rejects_v3_pending_with_only_docs_changes(self):
        wr.start_review(
            self.state, self.root, "M1", reviewer_id="agent-2",
            implementer_id="agent-1", mode="INDEPENDENT_SUBAGENT_NO_ISOLATION",
            now_iso="2026-07-15T12:00:00+00:00",
        )
        (self.root / "docs").mkdir(exist_ok=True)
        (self.root / "docs" / "manual.md").write_text("# Manual\n")
        with self.assertRaisesRegex(ValueError, "no está stale"):
            wr.invalidate_stale_review(
                self.state,
                self.root,
                "M1",
                approved_by="builder",
                reason="Intento invalidar review v3 solo con cambio en docs",
                now_iso="2026-07-15T12:05:00+00:00",
            )

    def test_h8_invalidate_stale_review_allows_v3_pending_with_code_changes(self):
        wr.start_review(
            self.state, self.root, "M1", reviewer_id="agent-2",
            implementer_id="agent-1", mode="INDEPENDENT_SUBAGENT_NO_ISOLATION",
            now_iso="2026-07-15T12:00:00+00:00",
        )
        (self.root / "app.py").write_text("print('codigo alterado')\n")
        receipt = wr.invalidate_stale_review(
            self.state,
            self.root,
            "M1",
            approved_by="builder",
            reason="Invalidación legítima porque el código cambió",
            now_iso="2026-07-15T12:05:00+00:00",
        )
        self.assertEqual(receipt["status"], "fail")
        self.assertTrue(receipt["invalidated"])

    def test_dependency_bootstrap_requires_state_pin_manifest_and_lock(self):
        self.assertEqual(
            wr.dependency_bootstrap_problems(self.root, self.state, "pytest==8.4.1"), []
        )
        state_without = {**self.state, "dependencies": []}
        self.assertTrue(wr.dependency_bootstrap_problems(self.root, state_without, "pytest==8.4.1"))
        self.assertTrue(wr.dependency_bootstrap_problems(self.root, self.state, "pytest"))
        (self.root / "requirements.lock").write_text("pytest==8.3.0\n")
        self.assertTrue(wr.dependency_bootstrap_problems(self.root, self.state, "pytest==8.4.1"))
        self.assertTrue(
            wr.dependency_bootstrap_problems(self.root, self.state, "pytest==8.4.1", target="global")
        )

    def test_dependency_bootstrap_accepts_exact_pin_without_any_lockfile(self):
        # FW-084 / FW-010 pt2 (known-good): sin NINGÚN lockfile declarado, el
        # pin exacto en el manifest alcanza.
        state = {**self.state, "dependencies": ["foo==1.2.3"]}
        (self.root / "requirements.lock").unlink()
        (self.root / "requirements.txt").write_text("foo==1.2.3\n")
        self.assertEqual(
            wr.dependency_bootstrap_problems(self.root, state, "foo==1.2.3"), []
        )

    def test_dependency_bootstrap_still_requires_match_when_lockfile_exists(self):
        # known-bad: si el lockfile SÍ existe pero está desalineado, sigue
        # rechazando — no se ablanda la exigencia de coincidencia.
        state = {**self.state, "dependencies": ["foo==1.2.3"]}
        (self.root / "requirements.txt").write_text("foo==1.2.3\n")
        (self.root / "requirements.lock").write_text("foo==1.0.0\n")
        problems = wr.dependency_bootstrap_problems(self.root, state, "foo==1.2.3")
        self.assertTrue(any("lockfile" in p for p in problems), problems)

    def test_dependency_bootstrap_binds_name_and_version_in_same_declaration(self):
        state = {**self.state, "dependencies": ["foo@1.2.3"]}
        (self.root / "package.json").write_text(json.dumps({
            "dependencies": {"foo": "^1.0.0", "bar": "1.2.3"}
        }))
        (self.root / "package-lock.json").write_text(json.dumps({
            "packages": {
                "node_modules/foo": {"version": "1.0.0"},
                "node_modules/bar": {"version": "1.2.3"},
            }
        }))

        problems = wr.dependency_bootstrap_problems(self.root, state, "foo@1.2.3")
        self.assertTrue(any("manifest" in problem for problem in problems), problems)
        self.assertTrue(any("lockfile" in problem for problem in problems), problems)

        (self.root / "package.json").write_text(json.dumps({
            "dependencies": {"foo": "1.2.3"}
        }))
        (self.root / "package-lock.json").write_text(json.dumps({
            "packages": {"node_modules/foo": {"version": "1.2.3"}}
        }))
        self.assertEqual(
            wr.dependency_bootstrap_problems(self.root, state, "foo@1.2.3"), []
        )

    def test_pyproject_pin_must_be_in_a_dependency_section(self):
        state = {**self.state, "dependencies": ["foo==1.2.3"]}
        (self.root / "requirements.txt").write_text("pytest==8.4.1\n")
        (self.root / "requirements.lock").write_text("pytest==8.4.1\n")
        (self.root / "pyproject.toml").write_text(
            '[tool.unrelated]\nfoo = "1.2.3"\n'
        )
        (self.root / "uv.lock").write_text(
            '[[package]]\nname = "foo"\nversion = "1.2.3"\n'
        )
        problems = wr.dependency_bootstrap_problems(self.root, state, "foo==1.2.3")
        self.assertTrue(any("manifest" in problem for problem in problems), problems)

        (self.root / "pyproject.toml").write_text(
            '[project]\ndependencies = [\n  "foo==1.2.3",\n]\n'
        )
        self.assertEqual(
            wr.dependency_bootstrap_problems(self.root, state, "foo==1.2.3"), []
        )

    def test_dependency_manifest_and_lock_must_share_ecosystem(self):
        state = {**self.state, "dependencies": ["foo@1.2.3"]}
        (self.root / "package.json").write_text(json.dumps({
            "dependencies": {"foo": "1.2.3"}
        }))
        (self.root / "requirements.lock").write_text("foo==1.2.3\n")
        problems = wr.dependency_bootstrap_problems(self.root, state, "foo@1.2.3")
        self.assertTrue(any("lockfile" in problem for problem in problems), problems)

        (self.root / "package-lock.json").write_text(json.dumps({
            "packages": {"node_modules/foo": {"version": "1.2.3"}}
        }))
        self.assertEqual(
            wr.dependency_bootstrap_problems(self.root, state, "foo@1.2.3"), []
        )

    def test_yarn_berry_exact_pin_is_accepted(self):
        state = {**self.state, "dependencies": ["foo@1.2.3"]}
        (self.root / "package.json").write_text(json.dumps({
            "dependencies": {"foo": "1.2.3"}
        }))
        (self.root / "yarn.lock").write_text(
            '"foo@npm:1.2.3":\n  version: 1.2.3\n'
        )
        self.assertEqual(
            wr.dependency_bootstrap_problems(self.root, state, "foo@1.2.3"), []
        )

    def test_tier2_state_receipt_requires_risk_and_adr(self):
        self.approve()
        receipt = self.state["workflow_receipts"]["approvals"]["M1"]
        receipt["risk_summary"] = ""
        receipt["adr_refs"] = []
        problems = wr.approval_receipt_state_problems(self.state, "M1")
        self.assertTrue(any("riesgo" in p for p in problems), problems)
        self.assertTrue(any("ADR" in p for p in problems), problems)

    def test_tier2_approval_rejects_adr_from_another_module(self):
        self.state["_decisions"][0]["module_id"] = "M2"
        with self.assertRaisesRegex(ValueError, "otro módulo"):
            self.approve()

    # --- S3 (TL FW-008 residuo): hash de receipts inmune al servidor stale ---

    def _run_hash_subcommand(self):
        proc = subprocess.run(
            [sys.executable, str(ROOT / ".agent" / "scripts" / "workflow_receipts.py"), "hash"],
            cwd=str(ROOT), capture_output=True, text=True, timeout=30,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return proc.stdout.strip()

    def test_hash_subcommand_matches_direct_import(self):
        subcommand_hash = self._run_hash_subcommand()
        direct_hash = wr.workspace_diff_hash(wr.ROOT)
        self.assertEqual(subcommand_hash, direct_hash)
        self.assertRegex(subcommand_hash, r"^v3:code=[0-9a-f]{64};docs=[0-9a-f]{64}$")

    def test_hash_subcommand_is_stable_across_invocations(self):
        first = self._run_hash_subcommand()
        second = self._run_hash_subcommand()
        self.assertEqual(first, second)

    def _mock_only_hash_subprocess(self, effect):
        """Sustituye subprocess.run SOLO para la invocación del subcomando
        `hash` de workflow_receipts.py (última entrada del comando ==
        "hash"); cualquier otra llamada (los `git` internos de
        `_tracked_product_paths`) pasa al `subprocess.run` real. Sin esto,
        mockear `subprocess.run` a secas también intercepta esos comandos
        git y corrompe el cálculo de comparación."""
        real_run = wr.subprocess.run

        def fake_run(cmd, *args, **kwargs):
            if isinstance(cmd, list) and cmd and cmd[-1] == "hash":
                if isinstance(effect, Exception):
                    raise effect
                return effect
            return real_run(cmd, *args, **kwargs)

        return mock.patch.object(wr.subprocess, "run", side_effect=fake_run)

    def test_fresh_hash_uses_subprocess_output_on_success(self):
        real_hash = wr.workspace_diff_hash(self.root)
        fake = subprocess.CompletedProcess(args=["x"], returncode=0, stdout=real_hash + "\n", stderr="")
        with self._mock_only_hash_subprocess(fake):
            value, warning = wr.workspace_diff_hash_fresh(self.root)
        self.assertEqual(value, real_hash)
        self.assertIsNone(warning)

    def test_fresh_hash_falls_back_when_subprocess_times_out(self):
        (self.root / "app.py").write_text("print('hola')\n")
        expected = wr.workspace_diff_hash(self.root)
        timeout = subprocess.TimeoutExpired(cmd="workflow_receipts.py hash", timeout=30)
        with self._mock_only_hash_subprocess(timeout):
            value, warning = wr.workspace_diff_hash_fresh(self.root)
        self.assertEqual(value, expected)
        self.assertIsNotNone(warning)
        self.assertIn("in-process", warning)
        self.assertIn("subproceso fresco", warning)

    def test_fresh_hash_falls_back_on_nonzero_exit(self):
        expected = wr.workspace_diff_hash(self.root)
        fake = subprocess.CompletedProcess(args=["x"], returncode=1, stdout="", stderr="boom")
        with self._mock_only_hash_subprocess(fake):
            value, warning = wr.workspace_diff_hash_fresh(self.root)
        self.assertEqual(value, expected)
        self.assertIsNotNone(warning)

    def test_fresh_hash_falls_back_on_malformed_stdout(self):
        expected = wr.workspace_diff_hash(self.root)
        fake = subprocess.CompletedProcess(args=["x"], returncode=0, stdout="no-es-un-hash\n", stderr="")
        with self._mock_only_hash_subprocess(fake):
            value, warning = wr.workspace_diff_hash_fresh(self.root)
        self.assertEqual(value, expected)
        self.assertIsNotNone(warning)

    # --- S4 (Example Tool FW-T07): errores de approval receipts con contrato completo ---

    def test_record_approval_empty_payload_shows_full_contract(self):
        with self.assertRaises(ValueError) as ctx:
            wr.record_approval(
                self.state, self.root, "M1",
                approved_by="", evidence="", risk_summary="", adr_refs=[],
                now_iso="2026-07-23T00:00:00+00:00",
            )
        message = str(ctx.exception)
        for field in ("module", "approved_by", "evidence", "risk_summary", "adr_refs"):
            self.assertIn(field, message, message)
        self.assertIn("--payload", message)
        self.assertIn("record_approval_receipt", message)

    def test_record_approval_single_wrong_field_still_shows_full_contract(self):
        # Único campo roto: approved_by. evidence/risk_summary/adr_refs válidos.
        with self.assertRaises(ValueError) as ctx:
            wr.record_approval(
                self.state, self.root, "M1",
                approved_by="agente-x", evidence="evidencia válida",
                risk_summary="riesgo válido", adr_refs=["ADR-001"],
                now_iso="2026-07-23T00:00:00+00:00",
            )
        message = str(ctx.exception)
        self.assertIn("approved_by='builder'", message)
        for field in ("module", "approved_by", "evidence", "risk_summary", "adr_refs"):
            self.assertIn(field, message, message)
        self.assertIn("--payload", message)

    def test_refresh_approval_empty_payload_shows_full_contract(self):
        self.approve()
        with self.assertRaises(ValueError) as ctx:
            wr.refresh_derived_approval(
                self.state, self.root, "M1",
                parent_fingerprint="", derived_from=[], evidence="",
                now_iso="2026-07-23T00:05:00+00:00",
            )
        message = str(ctx.exception)
        for field in ("module", "parent_fingerprint", "derived_from", "evidence"):
            self.assertIn(field, message, message)
        self.assertIn("--payload", message)
        self.assertIn("refresh_approval_receipt", message)

    def test_approval_success_message_unchanged(self):
        # Solo se enriquecen los mensajes de ERROR; approve() no debe lanzar.
        self.approve()
        self.assertEqual(
            self.state["workflow_receipts"]["approvals"]["M1"]["status"], "approved"
        )


class CheckpointReceiptTests(unittest.TestCase):
    """FW-134: recibo genérico de checkpoints (domain_sweep, expert_audit),
    mismo idioma que record_harvest -- determinista del payload, sin
    diff_hash (la obligación no se invalida por un commit posterior, se
    re-satisface en la próxima invocación del mismo workflow)."""

    def setUp(self):
        self.state = {}

    def test_record_checkpoint_rejects_invalid_kind(self):
        with self.assertRaises(ValueError):
            wr.record_checkpoint(
                self.state, "M1", kind="not_a_kind", scope_id="M1",
                evidence="Barrido de sub-dominios completado sin hallazgos.",
                reference="09_domain_profile.md fila M1",
                recorded_by="agent", now_iso="2026-09-12T00:00:00Z",
            )

    def test_record_checkpoint_requires_reference(self):
        # Anti-fabricación: sin cita citable no hay checkpoint.
        with self.assertRaises(ValueError):
            wr.record_checkpoint(
                self.state, "M1", kind="domain_sweep", scope_id="M1",
                evidence="Barrido de sub-dominios completado sin hallazgos.",
                reference="",
                recorded_by="agent", now_iso="2026-09-12T00:00:00Z",
            )

    def test_record_checkpoint_requires_min_evidence(self):
        with self.assertRaises(ValueError):
            wr.record_checkpoint(
                self.state, "M1", kind="domain_sweep", scope_id="M1",
                evidence="ok",
                reference="09_domain_profile.md fila M1",
                recorded_by="agent", now_iso="2026-09-12T00:00:00Z",
            )

    def test_checkpoint_problems_missing_returns_actionable_message(self):
        problems = wr.checkpoint_problems(self.state, "domain_sweep", "M1")
        self.assertTrue(any("checkpoint" in p and "domain_sweep" in p for p in problems), problems)

    def test_checkpoint_problems_present_returns_empty(self):
        wr.record_checkpoint(
            self.state, "M1", kind="domain_sweep", scope_id="M1",
            evidence="Barrido de sub-dominios completado sin hallazgos.",
            reference="09_domain_profile.md fila M1",
            recorded_by="agent", now_iso="2026-09-12T00:00:00Z",
        )
        self.assertEqual(wr.checkpoint_problems(self.state, "domain_sweep", "M1"), [])

    def test_checkpoint_kinds_are_independent(self):
        wr.record_checkpoint(
            self.state, "M1", kind="domain_sweep", scope_id="M1",
            evidence="Barrido de sub-dominios completado sin hallazgos.",
            reference="09_domain_profile.md fila M1",
            recorded_by="agent", now_iso="2026-09-12T00:00:00Z",
        )
        # domain_sweep sellado no satisface expert_audit del mismo módulo.
        self.assertTrue(wr.checkpoint_problems(self.state, "expert_audit", "M1"))


class ApprovalSnapshotSidecarTests(unittest.TestCase):
    """FW-185: el `derived_snapshot` se guarda en un store content-addressed
    (`_project/approval_snapshots/<digest>.json`), no inline en el receipt."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def _snap(self):
        return {
            "active_scope": [wr._stable_hash("a"), wr._stable_hash("b")],
            "acceptance": [wr._stable_hash("ac")],
            "data_model": [], "api_contract": [], "contracts": [], "decisions": [],
            "module_spec": wr._stable_hash({"data_model": "applies"}),
        }

    def test_store_and_load_roundtrip(self):
        snap = self._snap()
        digest = wr.store_derived_snapshot(self.root, snap)
        self.assertTrue((self.root / "_project" / "approval_snapshots" / f"{digest}.json").is_file())
        self.assertEqual(wr.load_derived_snapshot(self.root, {"derived_snapshot_ref": digest}), snap)

    def test_store_is_content_addressed_and_idempotent(self):
        snap = self._snap()
        d1 = wr.store_derived_snapshot(self.root, snap)
        d2 = wr.store_derived_snapshot(self.root, dict(snap))
        self.assertEqual(d1, d2)
        store = self.root / "_project" / "approval_snapshots"
        self.assertEqual(list(store.glob("*.json")), [store / f"{d1}.json"])

    def test_load_prefers_inline_legacy(self):
        inline = self._snap()
        self.assertEqual(
            wr.load_derived_snapshot(self.root, {"derived_snapshot": inline, "derived_snapshot_ref": "x"}),
            inline,
        )

    def test_load_missing_sidecar_returns_none(self):
        self.assertIsNone(wr.load_derived_snapshot(self.root, {"derived_snapshot_ref": "0" * 64}))

    def test_load_no_snapshot_returns_none(self):
        self.assertIsNone(wr.load_derived_snapshot(self.root, {"fingerprint": "x"}))

    def test_load_tampered_sidecar_returns_none(self):
        digest = wr.store_derived_snapshot(self.root, self._snap())
        target = self.root / "_project" / "approval_snapshots" / f"{digest}.json"
        payload = json.loads(target.read_text())
        payload["active_scope"].append(wr._stable_hash("inyectada"))
        target.write_text(json.dumps(payload))
        self.assertIsNone(wr.load_derived_snapshot(self.root, {"derived_snapshot_ref": digest}))

    def test_snapshot_dir_excluded_from_diff_hash(self):
        """El store del motor no debe entrar en el hash del workspace (FW-174)."""
        self.assertTrue(wr._receipt_excluded("_project/approval_snapshots/deadbeef.json"))
        self.assertFalse(wr._receipt_excluded("_project/approval_snapshots.md"))


class PilotClosureSequenceTests(unittest.TestCase):
    """Test de extremo a extremo con la secuencia del piloto en el orden actual."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

        self.original_state_path = state_core.STATE_FILE_PATH
        self.original_core_path = state_core.CORE_FILE_PATH
        self.original_bugs_path = state_core.BUGS_FILE_PATH
        self.original_clars_path = state_core.CLARIFICATIONS_FILE_PATH
        self.original_decisions_path = state_core.DECISIONS_FILE_PATH
        self.original_session_brief_path = state_core.SESSION_BRIEF_PATH
        self.original_root = state_core.WORKSPACE_ROOT

        (self.root / "_project").mkdir(parents=True, exist_ok=True)
        (self.root / "_project" / "02_technical_plan").mkdir(parents=True, exist_ok=True)
        (self.root / "src").mkdir(parents=True, exist_ok=True)

        state_core.STATE_FILE_PATH = self.root / "_project" / "03_project_state.json"
        state_core.CORE_FILE_PATH = self.root / "_project" / "core.md"
        state_core.BUGS_FILE_PATH = self.root / "_project" / "06_bug_fixes.md"
        state_core.CLARIFICATIONS_FILE_PATH = self.root / "_project" / "07_pending_clarifications.md"
        state_core.DECISIONS_FILE_PATH = self.root / "_project" / "03_arch_decisions.md"
        state_core.SESSION_BRIEF_PATH = self.root / "_project" / "00_session_brief.json"
        state_core.WORKSPACE_ROOT = self.root

        (self.root / "_project" / "01_product_requirements.md").write_text("# PRD\nRequisitos base\n", encoding="utf-8")
        (self.root / "_project" / "03_contracts.md").write_text("# Contratos\nContrato base\n", encoding="utf-8")
        (self.root / "_project" / "04_active_scope.md").write_text("## M1\nScope base\n", encoding="utf-8")
        (self.root / "_project" / "08_module_acceptance.md").write_text(
            "## M1\n- [ ] AC1: Retornar resultado\n", encoding="utf-8"
        )
        (self.root / "src" / "app.py").write_text("def run():\n    pass\n", encoding="utf-8")

        initial_state = {
            "project": "pilot_e2e",
            "framework_mode": "initialized",
            "framework_version": "5.0.0",
            "current_phase": "phase_01",
            "current_step": "0",
            "last_completed_step": "0",
            "stack": {
                "backend": "Python", "frontend": "ninguno",
                "database": "ninguna", "auth": "ninguna",
            },
            "architecture_constraints": [],
            "contracts": {"public_interfaces": {}},
            "active_mcps": [],
            "_refs": {},
            "modules": {"completed": [], "in_progress": ["M1"], "pending": []},
            "module_statuses": {"M1": "in_progress"},
            "work_tiers": {
                "M1": {
                    "tier": "tier_2",
                    "rationale": "módulo tier 2",
                    "classified_at": "2026-09-18T00:00:00Z",
                    "classified_by": "builder",
                    "requires_second_review": True,
                }
            },
            "module_specs": {
                "M1": {
                    "data_model": "n/a",
                    "api_contract": "n/a",
                    "justification": "módulo sin persistencia",
                    "classified_at": "2026-09-18T00:00:00Z",
                    "classified_by": "manual",
                }
            },
            "dependencies": [],
            "_decisions": [{"id": "ADR-001", "module_id": "M1", "title": "ADR 1", "context": "contexto", "decision": "ok", "date": "2026-09-18", "status": "Aceptada"}],
            "_clarifications": [],
            "_bugs": [],
            "closure_rejections": [],
            "workflow_receipts": {"approvals": {}, "reviews": {}, "executions": {}, "checkpoints": {}},
            "execution_profile": {"capability_tier": "strong"},
        }
        state_core.STATE_FILE_PATH.write_text(json.dumps(initial_state, ensure_ascii=False, indent=2), encoding="utf-8")
        (self.root / "_project" / "state_log.jsonl").touch()

        subprocess.run(["git", "-C", str(self.root), "init", "-q"], check=True)
        subprocess.run(["git", "-C", str(self.root), "add", "-A"], check=True)
        subprocess.run(
            ["git", "-C", str(self.root), "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "init"],
            check=True,
        )
        self.manager = state_core.StateManager()

    def tearDown(self):
        state_core.STATE_FILE_PATH = self.original_state_path
        state_core.CORE_FILE_PATH = self.original_core_path
        state_core.BUGS_FILE_PATH = self.original_bugs_path
        state_core.CLARIFICATIONS_FILE_PATH = self.original_clars_path
        state_core.DECISIONS_FILE_PATH = self.original_decisions_path
        state_core.SESSION_BRIEF_PATH = self.original_session_brief_path
        state_core.WORKSPACE_ROOT = self.original_root
        self.tmp.cleanup()

    def test_secuencia_piloto_orden_actual_cierra_sin_refresh_ni_re_review(self):
        # 1. Aprobar
        self.manager.mutate("record_approval_receipt", {
            "module": "M1", "approved_by": "builder",
            "evidence": "aprobación de M1", "risk_summary": "bajo", "adr_refs": ["ADR-001"],
        })

        # 2. Implementar
        (self.root / "src" / "app.py").write_text("def run():\n    return 42\n", encoding="utf-8")
        (self.root / "src" / "test_app.py").write_text("from app import run\ndef test_run():\n    assert run() == 42\n", encoding="utf-8")
        self.manager.mutate("update_module_status", {"id": "M1", "status": "ready_for_validation"})

        # 3. Review
        self.manager.mutate("start_independent_review", {
            "module": "M1", "reviewer_id": "reviewer", "implementer_id": "builder",
            "mode": "INDEPENDENT_SUBAGENT_NO_ISOLATION",
        })
        self.manager.mutate("record_review_receipt", {
            "module": "M1", "reviewer_id": "reviewer", "result": "pass", "evidence": "diff y acs revisados ok",
        })

        # 4. Sellar execution receipt
        self.manager.mutate("record_execution_receipt", {
            "module": "M1", "kind": "ok_automatizada", "command": "pytest src/test_app.py",
            "exit_code": 0, "evidence": "tests pasaron con 1 passed en 0.05s",
        })

        # 5. Rellenar evidencia en 08_module_acceptance.md
        # 6. Escribir sello "Estado: ✅ Verificado con cliente" en 08_module_acceptance.md
        (self.root / "_project" / "08_module_acceptance.md").write_text(
            "## M1\n"
            "Estado: ✅ Verificado con cliente\n\n"
            "- [x] AC1: Retornar resultado — evidencia: `src/test_app.py` en verde\n",
            encoding="utf-8",
        )

        # 7. Cosechar fricción
        self.manager.mutate("record_harvest", {"module_id": "M1", "classes": "none"})
        # 7b. FW-276 (5.2.2 L1): checkpoints tier_2 del paso 3.9.
        for kind in ("domain_sweep", "expert_audit"):
            self.manager.mutate("record_checkpoint", {
                "module": "M1", "kind": kind, "scope_id": "M1",
                "evidence": "Barrido de sub-dominios completado sin hallazgos.",
                "reference": "09_domain_profile.md fila M1",
            })

        # 8. complete_module debe pasar sin refresh_approval_receipt ni re-review
        msg = self.manager.mutate("complete_module", {"id": "M1"})
        self.assertIn("completed", msg)
        self.assertEqual(self.manager.state_data["module_statuses"]["M1"], "completed")
        self.assertIn("M1", self.manager.state_data["modules"]["completed"])

    def test_secuencia_piloto_con_py_cambiado_tras_review_bloquea(self):
        # 1. Aprobar
        self.manager.mutate("record_approval_receipt", {
            "module": "M1", "approved_by": "builder",
            "evidence": "aprobación de M1", "risk_summary": "bajo", "adr_refs": ["ADR-001"],
        })

        # 2. Implementar
        (self.root / "src" / "app.py").write_text("def run():\n    return 42\n", encoding="utf-8")
        (self.root / "src" / "test_app.py").write_text("from app import run\ndef test_run():\n    assert run() == 42\n", encoding="utf-8")
        self.manager.mutate("update_module_status", {"id": "M1", "status": "ready_for_validation"})

        # 3. Review
        self.manager.mutate("start_independent_review", {
            "module": "M1", "reviewer_id": "reviewer", "implementer_id": "builder",
            "mode": "INDEPENDENT_SUBAGENT_NO_ISOLATION",
        })
        self.manager.mutate("record_review_receipt", {
            "module": "M1", "reviewer_id": "reviewer", "result": "pass", "evidence": "diff y acs revisados ok",
        })

        # 4. Sellar execution receipt
        self.manager.mutate("record_execution_receipt", {
            "module": "M1", "kind": "ok_automatizada", "command": "pytest src/test_app.py",
            "exit_code": 0, "evidence": "tests pasaron con 1 passed en 0.05s",
        })

        # CAMBIO DE .py TRAS EL REVIEW
        (self.root / "src" / "app.py").write_text("def run():\n    return 999\n", encoding="utf-8")

        # 5. Rellenar evidencia en 08_module_acceptance.md
        # 6. Escribir sello "Estado: ✅ Verificado con cliente" en 08_module_acceptance.md
        (self.root / "_project" / "08_module_acceptance.md").write_text(
            "## M1\n"
            "Estado: ✅ Verificado con cliente\n\n"
            "- [x] AC1: Retornar resultado — evidencia: `src/test_app.py` en verde\n",
            encoding="utf-8",
        )

        # 7. Cosechar fricción
        self.manager.mutate("record_harvest", {"module_id": "M1", "classes": "none"})
        # 7b. FW-276 (5.2.2 L1): checkpoints tier_2 del paso 3.9.
        for kind in ("domain_sweep", "expert_audit"):
            self.manager.mutate("record_checkpoint", {
                "module": "M1", "kind": kind, "scope_id": "M1",
                "evidence": "Barrido de sub-dominios completado sin hallazgos.",
                "reference": "09_domain_profile.md fila M1",
            })

        # 8. complete_module DEBE BLOQUEAR
        with self.assertRaises(ValueError) as ctx:
            self.manager.mutate("complete_module", {"id": "M1"})
        self.assertTrue("stale" in str(ctx.exception).lower() or "review" in str(ctx.exception).lower())
        self.assertNotEqual(self.manager.state_data["module_statuses"]["M1"], "completed")


if __name__ == "__main__":
    unittest.main()
