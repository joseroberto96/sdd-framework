"""Pruebas de reproducción de los casos 1 a 6 de L6 (FW-337).

Paso 0 de L6: reproduce cada caso y sus contrarios con fixtures mínimos en rojo.
- Caso 1 (TL:FW-301): Dependencia ya aprobada (covers_dependencies) vs no cubierta.
- Caso 2 (TL:FW-309): ADR aditiva que documenta una ya aprobada via refresco; ADR sin declarar marcada.
- Caso 3 (TL:FW-304, TL:FW-299): Memos/evidencias declaradas bajo _project/ con extensiones permitidas vs contrarios.
- Caso 4 (TL:FW-310): Aislamiento de sección en active_scope/acceptance con cabeceras reales (incluye ## === FASE 17 ===).
- Caso 5 (PA:FW-284): Re-registro de tier con mismo nivel vs cambio de nivel (ambas direcciones).
- Caso 6 (TL:FW-308): Tests modificados por otro módulo cerrado con record_execution vs modificados a posteriori.
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / ".agent" / "scripts"))
sys.path.insert(0, str(ROOT / "tests"))

import test_workflow_receipts as twr
import workflow_receipts as wr


class TestL6RecibosLigeros(unittest.TestCase):
    def setUp(self) -> None:
        twr.WorkflowReceiptTests.setUp(self)
        self.addCleanup(twr.WorkflowReceiptTests.tearDown, self)

    def approve(self, **kwargs) -> dict:
        payload = {
            "approved_by": "builder",
            "evidence": "Sí, adelante con M1",
            "risk_summary": "cambio Tier 2 con rollback",
            "adr_refs": ["ADR-001"],
            "now_iso": "2026-07-15T12:00:00+00:00",
        }
        payload.update(kwargs)
        return wr.record_approval(
            self.state,
            self.root,
            "M1",
            **payload,
        )

    # -------------------------------------------------------------------------
    # Caso 1 (TL:FW-301) — Dependencia ya aprobada
    # -------------------------------------------------------------------------
    def test_caso_1_dependencia_aprobada_no_caduca(self) -> None:
        """Sellar el recibo cubriendo una dependencia aprobada y registrarla después no debe caducar."""
        self.approve(covers_dependencies=["rich==15.0.0"])
        self.state["dependencies"].append("rich==15.0.0")

        problems = wr.approval_problems(self.root, self.state, "M1")
        self.assertEqual(problems, [], "La dependencia cubierta no debe caducar el recibo")

    def test_caso_1_contrario_dependencia_no_cubierta_si_caduca(self) -> None:
        """Registrar una dependencia no cubierta en covers_dependencies debe caducar el recibo."""
        self.approve(covers_dependencies=["rich==15.0.0"])
        self.state["dependencies"].append("requests==2.31.0")

        problems = wr.approval_problems(self.root, self.state, "M1")
        self.assertTrue(any("dependencies" in p for p in problems), "Dependencia no cubierta debe dar stale")

    def test_caso_1_manipulacion_de_evidencia_en_recibo_con_dependencia_cubierta_da_stale(self) -> None:
        """Un recibo con covers_dependencies donde se manipula approval_evidence debe dar stale por fingerprint."""
        self.approve(covers_dependencies=["rich==15.0.0"])
        self.state["dependencies"].append("rich==15.0.0")
        receipt = self.state["workflow_receipts"]["approvals"]["M1"]
        receipt["approval_evidence"] = "evidencia falsificada a posteriori"

        problems = wr.approval_problems(self.root, self.state, "M1")
        self.assertTrue(any("stale" in p or "fingerprint" in p for p in problems), "Manipulación de evidencia debe dar stale")

    def test_caso_1_manipulacion_de_risk_summary_o_adrs_da_stale(self) -> None:
        """Manipular risk_summary o adr_refs en un recibo con dependencia cubierta debe dar stale."""
        self.approve(covers_dependencies=["rich==15.0.0"])
        self.state["dependencies"].append("rich==15.0.0")
        receipt = self.state["workflow_receipts"]["approvals"]["M1"]
        receipt["risk_summary"] = "riesgo alterado"

        problems = wr.approval_problems(self.root, self.state, "M1")
        self.assertTrue(len(problems) > 0, "Manipular risk_summary debe dar stale")

    def test_caso_1_refresco_posterior_con_dependencia_cubierta_funciona(self) -> None:
        """El refresco posterior a registrar una dependencia cubierta debe funcionar y preservar covers_dependencies."""
        self.approve(covers_dependencies=["rich==15.0.0"])
        self.state["dependencies"].append("rich==15.0.0")
        parent = self.state["workflow_receipts"]["approvals"]["M1"]["fingerprint"]

        # Modificación aditiva en data_model.md
        (self.root / "_project" / "02_technical_plan" / "data_model.md").write_text("# Modelo\nCampo aditivo\n", encoding="utf-8")

        refreshed = wr.refresh_derived_approval(
            self.state,
            self.root,
            "M1",
            parent_fingerprint=parent,
            derived_from=["data_model.md"],
            evidence="adición a data_model tras cubrir dependencia",
            now_iso="2026-07-15T12:05:00+00:00",
        )
        self.assertEqual(refreshed["kind"], "derived_additive")
        self.assertIn("rich==15.0.0", refreshed.get("covers_dependencies", []))
        self.assertEqual(wr.approval_problems(self.root, self.state, "M1"), [])

    def test_caso_1_contrario_quitar_o_cambiar_dependencia_aprobada_sigue_caducando_aunque_se_anada_cubierta(self) -> None:
        """Quitar o cambiar una dependencia aprobada sigue caducando el recibo aunque se añada la cubierta."""
        # Aprobado con pytest==8.4.1
        self.approve(covers_dependencies=["rich==15.0.0"])
        # Quitamos pytest==8.4.1 y dejamos solo rich==15.0.0
        self.state["dependencies"] = ["rich==15.0.0"]

        problems = wr.approval_problems(self.root, self.state, "M1")
        self.assertTrue(any("dependencies" in p for p in problems), "Quitar dependencia aprobada debe dar stale")

    def test_caso_1_dependencias_con_tipos_no_string_no_lanzan_excepcion(self) -> None:
        """Una entrada en dependencies o covers_dependencies que no sea texto no debe lanzar excepción."""
        self.state["dependencies"] = ["pytest==8.4.1", None, 123, {"bad": True}]
        self.approve(covers_dependencies=["rich==15.0.0", None, 456])
        self.state["dependencies"].append("rich==15.0.0")

        problems = wr.approval_problems(self.root, self.state, "M1")
        self.assertEqual(problems, [])

    # -------------------------------------------------------------------------
    # Caso 2 (TL:FW-309) — ADR aditiva que documenta una ya aprobada via refresco
    # -------------------------------------------------------------------------
    def test_caso_2_adr_aditiva_documentada_pasa_con_refresco(self) -> None:
        """La ADR que documenta una aprobada da stale inicial nombrando la ADR y ofreciendo refresco, y pasa tras refresco."""
        self.approve()
        parent = self.state["workflow_receipts"]["approvals"]["M1"]["fingerprint"]

        # Se registra una ADR declarando a cuál documenta
        self.state["_decisions"].append({
            "id": "ADR-002",
            "module_id": "M1",
            "title": "Detalle técnico de caché",
            "decision": "documentar detalle aditivo de ADR-001",
            "documents_decision": "ADR-001",
        })

        # 1. Antes del refresco, approval_problems sigue dando stale, nombra ADR-002 y ofrece refresco
        problems = wr.approval_problems(self.root, self.state, "M1")
        self.assertTrue(len(problems) > 0, "Debe dar stale antes del refresco")
        stale_msg = problems[0]
        self.assertIn("ADR-002", stale_msg)
        self.assertIn("refresh_approval_receipt", stale_msg)

        # 2. Tras refresco con cita y justificación, el recibo se actualiza y pasa el gate
        refreshed = wr.refresh_derived_approval(
            self.state,
            self.root,
            "M1",
            parent_fingerprint=parent,
            derived_from=["ADR-002"],
            evidence="ADR-002 documenta detalle de implementación de ADR-001",
            additive_rationale="Detalla el backend de caché aprobado en ADR-001",
            now_iso="2026-07-15T12:05:00+00:00",
        )
        self.assertEqual(refreshed["kind"], "derived_additive")
        self.assertEqual(refreshed["added_decisions"], ["ADR-002"])
        self.assertEqual(refreshed.get("additive_rationale"), "Detalla el backend de caché aprobado en ADR-001")
        self.assertEqual(refreshed.get("documents_decisions"), {"ADR-002": "ADR-001"})
        self.assertEqual(wr.approval_problems(self.root, self.state, "M1"), [])

    def test_caso_2_contrario_adr_sin_declaracion_admitida_y_marcada(self) -> None:
        """Una ADR nueva sin documents_decision se admite en refresco (FW-124), pero queda marcada como sin documentar."""
        self.approve()
        parent = self.state["workflow_receipts"]["approvals"]["M1"]["fingerprint"]

        self.state["_decisions"].append({
            "id": "ADR-003",
            "module_id": "M1",
            "title": "Decisión no enlazada",
            "decision": "aditiva general",
        })

        refreshed = wr.refresh_derived_approval(
            self.state,
            self.root,
            "M1",
            parent_fingerprint=parent,
            derived_from=["ADR-003"],
            evidence="ADR-003 añadida",
            now_iso="2026-07-15T12:05:00+00:00",
        )
        self.assertEqual(refreshed["kind"], "derived_additive")
        self.assertEqual(refreshed["added_decisions"], ["ADR-003"])
        # Debe marcarse como no documentada
        docs = refreshed.get("documents_decisions", {})
        self.assertTrue(docs.get("ADR-003") is None or docs.get("ADR-003") == "")

    # -------------------------------------------------------------------------
    # Caso 3 (TL:FW-304, TL:FW-299) — Memos y evidencias declaradas
    # -------------------------------------------------------------------------
    def test_caso_3_memo_y_evidencia_declaradas_no_cuentan_como_codigo(self) -> None:
        """Memos y evidencias bajo _project/ con extensiones permitidas (.md, .json, .csv, .txt) declaradas van a documentación."""
        acc_path = self.root / "_project" / "08_module_acceptance.md"
        acc_path.write_text(
            "## M1\n"
            "- [ ] AC1: memo en _project/02_technical_plan/m1_result.md\n"
            "- [ ] AC2: evidencia en _project/m1_evidence.json\n"
            "- [ ] AC3: datos en _project/m1_data.csv\n"
            "- [ ] AC4: log en _project/m1_output.txt\n"
        )
        self.approve()

        wr.start_review(
            self.state,
            self.root,
            "M1",
            reviewer_id="agent-2",
            implementer_id="agent-1",
            mode="INDEPENDENT_SUBAGENT_NO_ISOLATION",
            now_iso="2026-07-15T12:10:00+00:00",
        )
        wr.finish_review(
            self.state,
            self.root,
            "M1",
            reviewer_id="agent-2",
            result="pass",
            evidence="review ok",
            now_iso="2026-07-15T12:12:00+00:00",
        )

        # Generar los ficheros declarados bajo _project/
        (self.root / "_project" / "02_technical_plan" / "m1_result.md").write_text("# Memo de resultado\n")
        (self.root / "_project" / "m1_evidence.json").write_text('{"status": "applied"}\n')
        (self.root / "_project" / "m1_data.csv").write_text("id,val\n1,2\n")
        (self.root / "_project" / "m1_output.txt").write_text("output log\n")

        current_hash = wr._compute_workspace_content_hash(self.root)
        problems = wr.review_problems(self.state, "M1", current_hash)
        self.assertEqual(problems, [], "Ficheros declarados permitidos no deben caducar diff_hash")

    def test_caso_3_contrario_sql_citado_en_ac_sigue_caducando(self) -> None:
        """Un fichero .sql citado en AC sigue contando como código (extensión no permitida)."""
        acc_path = self.root / "_project" / "08_module_acceptance.md"
        acc_path.write_text("## M1\n- [ ] AC1: migración en _project/migration.sql\n")
        self.approve()

        wr.start_review(
            self.state, self.root, "M1",
            reviewer_id="agent-2", implementer_id="agent-1",
            mode="INDEPENDENT_SUBAGENT_NO_ISOLATION",
            now_iso="2026-07-15T12:10:00+00:00",
        )
        wr.finish_review(
            self.state, self.root, "M1",
            reviewer_id="agent-2", result="pass",
            evidence="review ok", now_iso="2026-07-15T12:12:00+00:00",
        )

        (self.root / "_project" / "migration.sql").write_text("CREATE TABLE t (id INT);\n")

        current_hash = wr._compute_workspace_content_hash(self.root)
        problems = wr.review_problems(self.state, "M1", current_hash)
        self.assertTrue(len(problems) > 0, "Fichero .sql debe seguir contando como código")

    def test_caso_3_contrario_package_json_citado_en_ac_sigue_caducando(self) -> None:
        """Un package.json fuera de _project/ citado en AC sigue contando como código."""
        acc_path = self.root / "_project" / "08_module_acceptance.md"
        acc_path.write_text("## M1\n- [ ] AC1: deps en package.json\n")
        self.approve()

        wr.start_review(
            self.state, self.root, "M1",
            reviewer_id="agent-2", implementer_id="agent-1",
            mode="INDEPENDENT_SUBAGENT_NO_ISOLATION",
            now_iso="2026-07-15T12:10:00+00:00",
        )
        wr.finish_review(
            self.state, self.root, "M1",
            reviewer_id="agent-2", result="pass",
            evidence="review ok", now_iso="2026-07-15T12:12:00+00:00",
        )

        (self.root / "package.json").write_text('{"name": "test"}\n')

        current_hash = wr._compute_workspace_content_hash(self.root)
        problems = wr.review_problems(self.state, "M1", current_hash)
        self.assertTrue(len(problems) > 0, "package.json debe seguir contando como código")

    def test_caso_3_contrario_fichero_fuera_de_project_sigue_caducando(self) -> None:
        """Un fichero .json declarado fuera de _project/ (ej. docs/meta.json) sigue contando como código."""
        acc_path = self.root / "_project" / "08_module_acceptance.md"
        acc_path.write_text("## M1\n- [ ] AC1: meta en docs/meta.json\n")
        self.approve()

        wr.start_review(
            self.state, self.root, "M1",
            reviewer_id="agent-2", implementer_id="agent-1",
            mode="INDEPENDENT_SUBAGENT_NO_ISOLATION",
            now_iso="2026-07-15T12:10:00+00:00",
        )
        wr.finish_review(
            self.state, self.root, "M1",
            reviewer_id="agent-2", result="pass",
            evidence="review ok", now_iso="2026-07-15T12:12:00+00:00",
        )

        (self.root / "docs").mkdir(parents=True, exist_ok=True)
        (self.root / "docs" / "meta.json").write_text('{"extra": true}\n')

        current_hash = wr._compute_workspace_content_hash(self.root)
        problems = wr.review_problems(self.state, "M1", current_hash)
        self.assertTrue(len(problems) > 0, "Fichero fuera de _project/ no puede ser eximido")

    def test_caso_3_regla_de_exclusion_de_ficheros_numerados_y_technical_plan(self) -> None:
        """Quedan fuera de lo declarable: plan de fase, technical_plan salvo memos, y ficheros numerados."""
        acc_path = self.root / "_project" / "08_module_acceptance.md"
        acc_path.write_text(
            "## M1\n"
            "- [ ] AC1: memo en _project/02_technical_plan/memos/resultado.md\n"
            "- [ ] AC2: restricción en _project/10_arch_constraints.md\n"
            "- [ ] AC3: plan en _project/02_technical_plan/phase_01.md\n"
            "- [ ] AC4: modelo en _project/02_technical_plan/data_model.md\n"
        )
        self.approve()
        wr.start_review(
            self.state, self.root, "M1",
            reviewer_id="agent-2", implementer_id="agent-1",
            mode="INDEPENDENT_SUBAGENT_NO_ISOLATION", now_iso="2026-07-15T12:10:00+00:00",
        )
        wr.finish_review(
            self.state, self.root, "M1",
            reviewer_id="agent-2", result="pass", evidence="ok", now_iso="2026-07-15T12:12:00+00:00",
        )

        # 1. Modificar un memo de resultado bajo 02_technical_plan/memos/ NO caduca
        (self.root / "_project" / "02_technical_plan" / "memos").mkdir(parents=True, exist_ok=True)
        (self.root / "_project" / "02_technical_plan" / "memos" / "resultado.md").write_text("# Memo ok\n")
        current_hash = wr.workspace_diff_hash(self.root)
        self.assertEqual(wr.review_problems(self.state, "M1", current_hash), [])

        # 2. Modificar un fichero numerado _project/10_arch_constraints.md SÍ caduca
        (self.root / "_project" / "10_arch_constraints.md").write_text("# Restricciones nuevas\n")
        current_hash = wr.workspace_diff_hash(self.root)
        self.assertTrue(len(wr.review_problems(self.state, "M1", current_hash)) > 0, "Fichero numerado debe seguir caducando")








    # -------------------------------------------------------------------------
    # Caso 5 (PA:FW-284) — Tier re-registrado con mismo nivel vs cambio de nivel
    # -------------------------------------------------------------------------
    def test_caso_5_re_registrar_tier_con_mismo_nivel_no_caduca(self) -> None:
        """Re-registrar work_tiers con el mismo nivel y distinta justificación no caduca."""
        self.approve()
        self.state["work_tiers"]["M1"] = {
            "tier": "tier_2",
            "rationale": "revisión cambio-seguro: preservación estricta",
            "classified_by": "cambio-seguro",
        }
        self.assertEqual(wr.approval_problems(self.root, self.state, "M1"), [])

    def test_caso_5_contrario_cambio_de_nivel_de_tier_si_caduca(self) -> None:
        """Cambiar el nivel de tier en cualquiera de las dos direcciones (tier_2 -> tier_1 y tier_1 -> tier_2) caduca."""
        # Dirección 1: aprobado en tier_2, cambia a tier_1
        self.approve()
        self.state["work_tiers"]["M1"] = {
            "tier": "tier_1",
            "rationale": "subida de riesgo",
            "classified_by": "cambio-seguro",
        }
        problems_1 = wr.approval_problems(self.root, self.state, "M1")
        self.assertTrue(any("tier" in p for p in problems_1), "Cambio de tier_2 a tier_1 debe dar stale")

        # Dirección 2: aprobado en tier_1, cambia a tier_2
        self.state["work_tiers"]["M1"] = {"tier": "tier_1"}
        wr.record_approval(
            self.state, self.root, "M1",
            approved_by="builder", evidence="aprobado en tier_1",
            risk_summary="r", adr_refs=[], now_iso="2026-07-15T12:00:00+00:00",
        )
        self.state["work_tiers"]["M1"] = {
            "tier": "tier_2",
            "rationale": "escalada a tier_2",
            "classified_by": "cambio-seguro",
        }
        problems_2 = wr.approval_problems(self.root, self.state, "M1")
        self.assertTrue(any("tier" in p for p in problems_2), "Cambio de tier_1 a tier_2 debe dar stale")




    def test_punto_8_mensajes_stale_nombran_dimension_elemento_y_salida(self) -> None:
        """Todo mensaje stale en aprobación, revisión y ejecución nombra dimensión, elemento y salida."""
        # 1. Aprobación: stale por decisiones
        self.approve()
        self.state["_decisions"].append({
            "id": "ADR-002", "module_id": "M1", "decision": "cambio",
        })
        ap_probs = wr.approval_problems(self.root, self.state, "M1")
        self.assertTrue(len(ap_probs) > 0)
        msg = ap_probs[0]
        self.assertIn("decisions", msg)
        self.assertIn("ADR-002", msg)
        self.assertIn("refresh_approval_receipt", msg)

        # 2. Revisión: stale por code_diff remite a /cierre-modulo (no a /preflight-review)
        wr.start_review(
            self.state, self.root, "M1",
            reviewer_id="agent-2", implementer_id="agent-1",
            mode="INDEPENDENT_SUBAGENT_NO_ISOLATION", now_iso="2026-07-15T12:10:00+00:00",
        )
        wr.finish_review(
            self.state, self.root, "M1",
            reviewer_id="agent-2", result="pass", evidence="ok", now_iso="2026-07-15T12:12:00+00:00",
        )
        (self.root / "code.py").write_text("x = 1\n")
        rev_probs = wr.review_problems(self.state, "M1", wr.workspace_diff_hash(self.root))
        self.assertTrue(len(rev_probs) > 0)
        rev_msg = rev_probs[0]
        self.assertIn("code_diff", rev_msg)
        self.assertIn("/cierre-modulo", rev_msg)
        self.assertNotIn("/preflight-review", rev_msg)

    # -------------------------------------------------------------------------
    # Punto 1: Salidas de mensajes (críticas solo nueva aprobación, revisión /cierre-modulo, rechazo execution)
    # -------------------------------------------------------------------------
    def test_punto_1_dimensiones_criticas_solo_ofrecen_nueva_aprobacion(self) -> None:
        """Dimensiones críticas (dependencies, prd, phase_plan, tier, current_phase) solo ofrecen nueva aprobación del builder."""
        self.approve()

        # 1. Dependencia no cubierta: exige nueva aprobación del builder, no refresco
        self.state["dependencies"].append("uncovered_pkg==1.0.0")
        dep_probs = wr.approval_problems(self.root, self.state, "M1")
        self.assertTrue(len(dep_probs) > 0)
        self.assertIn("uncovered_pkg==1.0.0", dep_probs[0])
        self.assertIn("nueva aprobación del builder", dep_probs[0])
        self.assertNotIn("refresh_approval_receipt", dep_probs[0])

        # 2. Tier cambiado: exige nueva aprobación del builder, no refresco
        self.state["work_tiers"]["M1"]["tier"] = "tier_1"
        tier_probs = wr.approval_problems(self.root, self.state, "M1")
        self.assertTrue(any("tier" in p and "nueva aprobación del builder" in p for p in tier_probs))
        self.assertFalse(any("tier" in p and "refresh_approval_receipt" in p for p in tier_probs))

    def test_punto_1_rechazo_gate_de_pruebas_dice_execution_receipt_rechazado(self) -> None:
        """El gate de pruebas FW-250 dice 'execution receipt rechazado para <módulo>' (es un rechazo, no caducidad)."""
        gate = {
            "status": "ok",
            "base": "main",
            "base_source": "merge_base",
            "base_kind": "explicit",
            "modified_tests": ["tests/test_x.py"],
            "prod_files": ["src/app.py"],
            "requires_declaration": True,
            "test_changes": [],
            "test_status": {"tests/test_x.py": "M"},
        }
        msg = wr._undeclared_message("M1", gate, ["tests/test_x.py"])
        self.assertTrue(msg.startswith("execution receipt rechazado para 'M1':"))
        self.assertNotIn("execution receipt stale:", msg)






    # -------------------------------------------------------------------------
    # Punto 3: Declarables en 02_technical_plan/ (_result.md, _memo.md, memos/) y fuera approval_snapshots/
    # -------------------------------------------------------------------------
    def test_punto_3_regla_estricta_declarables_en_technical_plan_y_fuera_snapshots(self) -> None:
        """En 02_technical_plan/ solo _result.md, _memo.md o bajo memos/. Fuera approval_snapshots/."""
        # 1. En 02_technical_plan/: solo termina en _result.md o _memo.md, o bajo memos/
        self.assertTrue(wr._is_declarable_path("_project/02_technical_plan/f17_result.md"))
        self.assertTrue(wr._is_declarable_path("_project/02_technical_plan/analisis_memo.md"))
        self.assertTrue(wr._is_declarable_path("_project/02_technical_plan/memos/cualquiera.md"))
        self.assertTrue(wr._is_declarable_path("_project/02_technical_plan/memos/datos.csv"))

        # Ficheros que NO cumplen la regla estricta en 02_technical_plan/
        self.assertFalse(wr._is_declarable_path("_project/02_technical_plan/resultado.md"))
        self.assertFalse(wr._is_declarable_path("_project/02_technical_plan/memo_inicio.md"))
        self.assertFalse(wr._is_declarable_path("_project/02_technical_plan/data_model.md"))
        self.assertFalse(wr._is_declarable_path("_project/02_technical_plan/phase_01.md"))

        # 2. Fuera _project/approval_snapshots/
        self.assertFalse(wr._is_declarable_path("_project/approval_snapshots/snapshot.json"))
        self.assertFalse(wr._is_declarable_path("_project/approval_snapshots/M1_result.md"))

    # -------------------------------------------------------------------------
    # Punto 4: Fixtures de reproducción de FW-311 y JS:FW-275
    # -------------------------------------------------------------------------
    def test_punto_4_fixture_fw311_reproduccion_cierre_tier_2(self) -> None:
        """Reproduce FW-311 (TL:bug159): checkboxes/evidencias en scope/ACs pasan, contratos nuevos piden refresco."""
        scope = self.root / "_project" / "04_active_scope.md"
        scope.write_text("## task:bug159\n- [ ] Tarea 1\n- [ ] Tarea 2\n")
        acc = self.root / "_project" / "08_module_acceptance.md"
        acc.write_text("## task:bug159\n- [ ] AC1\n- [ ] AC2\n")
        contracts = self.root / "_project" / "03_contracts.md"
        contracts.write_text("# Contratos\nBase aprobada\n")

        self.state["_decisions"] = [{"id": "ADR-159", "module_id": "task:bug159", "decision": "regla base"}]
        self.state.setdefault("work_tiers", {})["task:bug159"] = {"tier": "tier_2"}
        app = wr.record_approval(
            self.state, self.root, "task:bug159",
            approved_by="builder", evidence="aprobado tier 2",
            risk_summary="r", adr_refs=["ADR-159"], now_iso="2026-07-15T12:00:00+00:00",
        )

        # 1. Progreso de tareas y ACs con evidencia anexada: NO pide refresco
        scope.write_text("## task:bug159\n- [x] Tarea 1\n- [x] Tarea 2\n")
        acc.write_text("## task:bug159\n- [x] AC1\n- [x] AC2\nEvidencia: pytest ok 2 passed\n")
        probs_step1 = wr.approval_problems(self.root, self.state, "task:bug159")
        self.assertEqual(probs_step1, [], "El progreso y evidencia anexada no deben pedir refresco")

        # 2. Si la sección de contratos fue puramente aditiva: la tolerancia aditiva la cubre sin refresco
        contracts.write_text("# Contratos\nBase aprobada\n\n## task:bug159\nContrato aditivo nuevo\n")
        probs_step2 = wr.approval_problems(self.root, self.state, "task:bug159")
        self.assertEqual(probs_step2, [], "La adición pura en contratos es tolerada por tolerancia aditiva")

        # 3. En FW-311, la razón real citaba 'reescritura narrativa': si reescribe cláusulas previas,
        # NO es puramente aditivo y SÍ pide refresco nombrando contracts y ofreciendo refresco.
        contracts.write_text("# Contratos\nBase aprobada y reformulada narrativamente\n\n## task:bug159\nContrato ajustado\n")
        probs_step3 = wr.approval_problems(self.root, self.state, "task:bug159")
        self.assertTrue(any("contracts" in p for p in probs_step3), "Reescritura narrativa de contratos pide refresco")
        self.assertTrue(any("refresh_approval_receipt" in p or "refresco" in p for p in probs_step3))

        # 4. Con refresh_derived_approval y additive_rationale, se resuelve limpiamente
        refreshed = wr.refresh_derived_approval(
            self.state, self.root, "task:bug159",
            parent_fingerprint=app["fingerprint"],
            derived_from=["03_contracts.md"],
            evidence="reescritura narrativa y contratos completados",
            additive_rationale="reescritura narrativa del estado/progreso",
            now_iso="2026-07-15T12:30:00+00:00",
        )
        self.assertEqual(refreshed["kind"], "derived_additive")
        self.assertEqual(wr.approval_problems(self.root, self.state, "task:bug159"), [])

    def test_punto_4_fixture_js_fw275_reproduccion_cierre_tier_2(self) -> None:
        """Reproduce JS:FW-275 (Example Service): ADR aditiva que documenta previa y api_contract.yaml."""
        api_contract = self.root / "_project" / "02_technical_plan" / "api_contract.yaml"
        api_contract.write_text("paths:\n  /initial: {}\n")

        self.state["_decisions"] = [
            {"id": "ADR-011", "module_id": "core", "decision": "regla base"},
            {"id": "ADR-013", "module_id": "phase_09_bug_013", "decision": "regla modulo", "documents_decision": "ADR-011"},
        ]
        self.state.setdefault("work_tiers", {})["phase_09_bug_013"] = {"tier": "tier_2"}
        app = wr.record_approval(
            self.state, self.root, "phase_09_bug_013",
            approved_by="builder", evidence="aprobado tier 2",
            risk_summary="r", adr_refs=["ADR-013"], now_iso="2026-07-15T12:00:00+00:00",
        )

        # Durante la implementación, se añade ADR-015 que documenta ADR-013 y se actualiza api_contract.yaml
        self.state["_decisions"].append({
            "id": "ADR-015", "module_id": "phase_09_bug_013",
            "decision": "documentar detalle", "documents_decision": "ADR-013",
        })
        api_contract.write_text("paths:\n  /initial: {}\n  /nuevo_endpoint: {}\n")

        probs = wr.approval_problems(self.root, self.state, "phase_09_bug_013")
        # Ambos cambios piden refresco estructurado (nombrando el elemento y ofreciendo refresco)
        self.assertTrue(any("ADR-015" in p for p in probs))
        self.assertTrue(any("api_contract" in p for p in probs))

        # Tras refresh con la justificación y citando la ADR documentada, pasa limpio
        refreshed = wr.refresh_derived_approval(
            self.state, self.root, "phase_09_bug_013",
            parent_fingerprint=app["fingerprint"],
            derived_from=["ADR-015", "02_technical_plan/api_contract.yaml"],
            evidence="ADR-015 detalla ADR-013 y api_contract documenta endpoint del scope",
            additive_rationale="especificación de endpoint acordada en scope",
            now_iso="2026-07-15T12:20:00+00:00",
        )
        self.assertEqual(refreshed["documents_decisions"], {"ADR-015": "ADR-013"})
        self.assertEqual(wr.approval_problems(self.root, self.state, "phase_09_bug_013"), [])


if __name__ == "__main__":
    unittest.main()
