import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / ".agent" / "scripts"))

import workflow_receipts as wr
import validate_closure as vc
import state_manager
import state_queries


def _git(root: Path, *args: str) -> str:
    out = subprocess.run(
        ["git", "-C", str(root), *args], capture_output=True, text=True, check=True
    )
    return out.stdout.strip()


class ReceiptHashV3ClassifierTests(unittest.TestCase):
    """Pruebas exhaustivas del clasificador _is_documentation.
    Condición 1: allowlist cerrada.
    Condición 2: casos fijados explícitamente como código y como documentación.
    """

    def test_archivos_fijados_como_codigo(self):
        code_paths = [
            "AGENTS.md",
            "CLAUDE.md",
            "GEMINI.md",
            "GUARDRAILS_MASTER.md",
            "PRODUCT.md",
            "DESIGN.md",
            "_project/01_product_requirements.md",
            "_project/03_contracts.md",
            "_project/03_invariants.md",
            "_project/10_arch_constraints.md",
            "_project/11_file_dependencies.md",
            "_project/02_technical_plan/data_model.md",
            "docs/api.yaml",
            ".agent/workflows/ciclo.md",
            ".agent/docs/CHANGELOG.md",
        ]
        for rel in code_paths:
            with self.subTest(path=rel):
                self.assertFalse(
                    wr._is_documentation(rel),
                    f"{rel} debe clasificarse como CÓDIGO (False)",
                )

    def test_archivos_fijados_como_documentacion(self):
        doc_paths = [
            "docs/guia.md",
            "docs/sub/arquitectura.md",
            "README.md",
            "CHANGELOG.md",
            ".agent/docs/field_report.md",
            ".agent/docs/friction_buffer.md",
            "_project/04_active_scope.md",
            "_project/04_scope_archive.md",
            "_project/08_module_acceptance.md",
            "_project/08_module_acceptance_phase_01.md",
            "_project/08_module_acceptance_phase_mvp.md",
        ]
        for rel in doc_paths:
            with self.subTest(path=rel):
                self.assertTrue(
                    wr._is_documentation(rel),
                    f"{rel} debe clasificarse como DOCUMENTACIÓN (True)",
                )

    def test_allowlist_cerrada_casos_frontera(self):
        # Ficheros no .md en docs/
        self.assertFalse(wr._is_documentation("docs/api.json"))
        self.assertFalse(wr._is_documentation("docs/schema.yaml"))
        self.assertFalse(wr._is_documentation("docs/README"))
        # README / CHANGELOG fuera de la raíz
        self.assertFalse(wr._is_documentation("src/README.md"))
        self.assertFalse(wr._is_documentation("nested/CHANGELOG.md"))
        # 08 en subdirectorios
        self.assertFalse(wr._is_documentation("_project/sub/08_module_acceptance_phase_01.md"))
        # Ficheros desconocidos o futuros
        self.assertFalse(wr._is_documentation("nuevo_modulo.py"))
        self.assertFalse(wr._is_documentation("notas.md"))
        self.assertFalse(wr._is_documentation(".agent/rules/runtime_core.md"))


class DiffHashAccessorsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "src").mkdir()
        (self.root / "src" / "app.py").write_text("print('hello')\n")
        _git(self.root, "init", "-q")
        _git(self.root, "add", "-A")
        _git(self.root, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "init")

    def tearDown(self):
        self.tmp.cleanup()

    def test_diff_hash_str_compatibility_and_accessors(self):
        h = wr.workspace_diff_hash(self.root)
        self.assertIsInstance(h, str)
        self.assertIsInstance(h, wr.DiffHash)
        self.assertTrue(h.startswith("v3:"))
        self.assertFalse(h.startswith("v2:"))  # No sobrescribe startswith
        self.assertTrue(h.v2.startswith("v2:"))
        self.assertRegex(h.v2, r"^v2:[0-9a-f]{64}$")
        self.assertRegex(h.legacy, r"^[0-9a-f]{64}$")

    def test_diff_hash_match_boolean_and_unpacking(self):
        m1 = wr.DiffHashMatch(True, False)
        self.assertTrue(m1)
        self.assertEqual(m1, True)
        self.assertFalse(m1.docs_changed)
        matches, docs = m1
        self.assertTrue(matches)
        self.assertFalse(docs)

        m2 = wr.DiffHashMatch(True, True)
        self.assertTrue(m2)
        self.assertTrue(m2.docs_changed)
        self.assertTrue(m2.docs_differ)
        matches, docs = m2
        self.assertTrue(matches)
        self.assertTrue(docs)

        m3 = wr.DiffHashMatch(False, False)
        self.assertFalse(m3)
        self.assertEqual(m3, False)
        self.assertFalse(m3.docs_changed)


class ReceiptHashV3LifecycleTests(unittest.TestCase):
    """Los 8 casos obligatorios de la tabla de L2."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "_project").mkdir(parents=True)
        (self.root / "docs").mkdir(parents=True)
        (self.root / ".agent" / "docs").mkdir(parents=True)
        (self.root / ".agent" / "workflows").mkdir(parents=True)
        (self.root / "src").mkdir(parents=True)

        (self.root / "src" / "app.py").write_text("def run():\n    return 42\n", encoding="utf-8")
        (self.root / "docs" / "x.md").write_text("# Doc X\n", encoding="utf-8")
        (self.root / ".agent" / "docs" / "field_report.md").write_text("# Field Report\n", encoding="utf-8")
        (self.root / ".agent" / "workflows" / "ciclo.md").write_text("# Ciclo\n", encoding="utf-8")
        (self.root / "_project" / "03_contracts.md").write_text("# Contratos\n", encoding="utf-8")
        (self.root / "_project" / "04_active_scope.md").write_text("# Scope\n## M1\n", encoding="utf-8")
        (self.root / "_project" / "08_module_acceptance.md").write_text(
            "## M1\n- [ ] AC1\n", encoding="utf-8"
        )
        self.state = {
            "project": "test_proj",
            "current_phase": "phase_01",
            "module_statuses": {"M1": "ready_for_validation"},
            "work_tiers": {
                "M1": {
                    "tier": "tier_2",
                    "requires_second_review": True,
                    "harvest": {"classes": []},
                }
            },
            "module_specs": {
                "M1": {
                    "data_model": "n/a",
                    "api_contract": "n/a",
                    "justification": "test",
                    "classified_by": "manual",
                }
            },
            "_decisions": [{"id": "ADR-001", "module_id": "M1", "decision": "ok"}],
            "workflow_receipts": {"reviews": {}, "executions": {}, "approvals": {}},
        }
        _git(self.root, "init", "-q")
        _git(self.root, "add", "-A")
        _git(self.root, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "init")

    def tearDown(self):
        self.tmp.cleanup()

    def _seal_receipts(self):
        wr.start_review(
            self.state, self.root, "M1",
            reviewer_id="reviewer", implementer_id="builder",
            mode="INDEPENDENT_SUBAGENT_NO_ISOLATION", now_iso="2026-09-18T12:00:00Z",
        )
        wr.finish_review(
            self.state, self.root, "M1",
            reviewer_id="reviewer", result="pass",
            evidence="Revisión independiente del diff", now_iso="2026-09-18T12:01:00Z",
        )
        wr.record_execution(
            self.state, self.root, "M1",
            kind="ok_automatizada", command="pytest tests", exit_code=0,
            evidence="Ejecución de tests con resultado 0", now_iso="2026-09-18T12:02:00Z",
        )

    def test_caso_1_editar_docs_tras_sellar_no_stale_con_aviso(self):
        self._seal_receipts()
        (self.root / "docs" / "x.md").write_text("# Doc X modificado\n", encoding="utf-8")
        current_hash = wr.workspace_diff_hash(self.root)

        # No stale
        self.assertEqual(wr.review_problems(self.state, "M1", current_hash), [])
        self.assertEqual(wr.execution_problems(self.state, "M1", current_hash), [])

        # Aviso de deriva documental presente
        warnings = wr.receipt_warnings(self.state, "M1", current_hash)
        self.assertIn("deriva documental posterior al sello (cambió la huella de documentación)", warnings)

        # En validate_closure no bloquea
        closure_warns = vc.closure_warnings(self.state, "M1", current_hash)
        self.assertIn("deriva documental posterior al sello (cambió la huella de documentación)", closure_warns)

    def test_caso_2_marcar_ac_y_sello_en_08_no_stale(self):
        self._seal_receipts()
        (self.root / "_project" / "08_module_acceptance.md").write_text(
            "## M1\n\nEstado: ✅ Verificado con cliente\n\n- [x] AC1\n",
            encoding="utf-8",
        )
        current_hash = wr.workspace_diff_hash(self.root)
        self.assertEqual(wr.review_problems(self.state, "M1", current_hash), [])
        self.assertEqual(wr.execution_problems(self.state, "M1", current_hash), [])

    def test_caso_3_escribir_en_field_report_no_stale(self):
        self._seal_receipts()
        (self.root / ".agent" / "docs" / "field_report.md").write_text(
            "# Field Report\n- Fricción registrada\n", encoding="utf-8"
        )
        current_hash = wr.workspace_diff_hash(self.root)
        self.assertEqual(wr.review_problems(self.state, "M1", current_hash), [])
        self.assertEqual(wr.execution_problems(self.state, "M1", current_hash), [])

    def test_caso_4_editar_py_tras_sellar_stale(self):
        self._seal_receipts()
        (self.root / "src" / "app.py").write_text("def run():\n    return 99\n", encoding="utf-8")
        current_hash = wr.workspace_diff_hash(self.root)
        rev_probs = wr.review_problems(self.state, "M1", current_hash)
        exec_probs = wr.execution_problems(self.state, "M1", current_hash)
        self.assertTrue(any("stale" in p for p in rev_probs))
        self.assertTrue(any("stale" in p for p in exec_probs))

    def test_caso_5_editar_contracts_tras_sellar_stale(self):
        self._seal_receipts()
        (self.root / "_project" / "03_contracts.md").write_text(
            "# Contratos modificados\n", encoding="utf-8"
        )
        current_hash = wr.workspace_diff_hash(self.root)
        rev_probs = wr.review_problems(self.state, "M1", current_hash)
        exec_probs = wr.execution_problems(self.state, "M1", current_hash)
        self.assertTrue(any("stale" in p for p in rev_probs))
        self.assertTrue(any("stale" in p for p in exec_probs))

    def test_caso_5b_editar_api_contract_yaml_tras_sellar_stale(self):
        plan_dir = self.root / "_project" / "02_technical_plan"
        plan_dir.mkdir(parents=True, exist_ok=True)
        (plan_dir / "api_contract.yaml").write_text("openapi: 3.0.0\n", encoding="utf-8")
        _git(self.root, "add", "-A")
        _git(self.root, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "add api_contract")
        self._seal_receipts()
        (plan_dir / "api_contract.yaml").write_text("openapi: 3.1.0\n", encoding="utf-8")
        current_hash = wr.workspace_diff_hash(self.root)
        rev_probs = wr.review_problems(self.state, "M1", current_hash)
        exec_probs = wr.execution_problems(self.state, "M1", current_hash)
        self.assertTrue(any("stale" in p for p in rev_probs))
        self.assertTrue(any("stale" in p for p in exec_probs))

    def test_caso_6_editar_workflow_ciclo_tras_sellar_stale(self):
        self._seal_receipts()
        (self.root / ".agent" / "workflows" / "ciclo.md").write_text(
            "# Ciclo modificado\n", encoding="utf-8"
        )
        current_hash = wr.workspace_diff_hash(self.root)
        rev_probs = wr.review_problems(self.state, "M1", current_hash)
        exec_probs = wr.execution_problems(self.state, "M1", current_hash)
        self.assertTrue(any("stale" in p for p in rev_probs))
        self.assertTrue(any("stale" in p for p in exec_probs))

    def test_caso_7_recibo_v2_sin_tocar_nada_sigue_valido(self):
        current_hash = wr.workspace_diff_hash(self.root)
        stored_v2 = current_hash.v2
        self.assertTrue(stored_v2.startswith("v2:"))
        self.state["workflow_receipts"]["reviews"]["M1"] = [{
            "status": "pass",
            "result": "pass",
            "reviewer_id": "reviewer",
            "implementer_id": "builder",
            "mode": "INDEPENDENT_SUBAGENT_NO_ISOLATION",
            "diff_hash": stored_v2,
            "evidence": "revisión v2 histórica",
        }]
        self.assertTrue(wr.diff_hash_matches(stored_v2, current_hash))
        self.assertEqual(wr.review_problems(self.state, "M1", current_hash), [])

    def test_caso_8_recibo_v2_editando_py_stale(self):
        current_hash = wr.workspace_diff_hash(self.root)
        stored_v2 = current_hash.v2
        self.state["workflow_receipts"]["reviews"]["M1"] = [{
            "status": "pass",
            "result": "pass",
            "reviewer_id": "reviewer",
            "implementer_id": "builder",
            "mode": "INDEPENDENT_SUBAGENT_NO_ISOLATION",
            "diff_hash": stored_v2,
            "evidence": "revisión v2 histórica",
        }]
        (self.root / "src" / "app.py").write_text("def run():\n    return 100\n", encoding="utf-8")
        fresh_hash = wr.workspace_diff_hash(self.root)
        self.assertFalse(wr.diff_hash_matches(stored_v2, fresh_hash))
        rev_probs = wr.review_problems(self.state, "M1", fresh_hash)
        self.assertTrue(any("stale" in p for p in rev_probs))


class ReceiptHashV3IntegrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "_project").mkdir(parents=True)
        (self.root / "docs").mkdir(parents=True)
        (self.root / ".agent" / "docs").mkdir(parents=True)
        (self.root / ".agent" / "scripts").mkdir(parents=True)
        (self.root / "src").mkdir(parents=True)

        import shutil
        for relative in (
            ".agent/workflows/cambio-seguro.md",
            ".agent/workflows/tdd.md",
            ".agent/workflows/enriquecer-ticket.md",
            ".agent/workflows/verificar-ejecutable.md",
            ".agent/workflows/preflight-review.md",
            ".agent/workflows/cierre-modulo.md",
            ".agent/workflows/reconciliar.md",
            ".agent/workflows/publicar-github.md",
            ".agent/rules/architecture_constraints.md",
            ".agent/skills/spec_contracts.md",
            ".agent/docs/specs/progressive_context_obligations.json",
            "AGENTS.md",
        ):
            source = ROOT / relative
            target = self.root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            if source.exists():
                shutil.copy2(source, target)
        (self.root / ".agent" / "workflows" / "manifest.json").write_text(
            json.dumps({"commands": [{"name": "ciclo", "builder_facing": True}]}),
            encoding="utf-8",
        )

        (self.root / "src" / "app.py").write_text("def run():\n    return 42\n", encoding="utf-8")
        (self.root / "docs" / "x.md").write_text("# Doc X\n", encoding="utf-8")
        (self.root / "_project" / "04_active_scope.md").write_text("# Scope\n## M1\n", encoding="utf-8")
        (self.root / "_project" / "08_module_acceptance.md").write_text(
            "## M1\n\nEstado: ✅ Verificado con cliente\n\n- [x] AC1 verificado\n",
            encoding="utf-8",
        )
        base_path = ROOT / "tests" / "fixtures" / "progressive_context" / "state_base.json"
        self.state = json.loads(base_path.read_text(encoding="utf-8"))
        self.state.update({
            "current_phase": "phase_01",
            "modules": {
                "completed": [],
                "in_progress": ["M1"],
                "pending": [],
            },
            "module_statuses": {"M1": "ready_for_validation"},
            "work_tiers": {
                "M1": {
                    "tier": "tier_2",
                    "rationale": "test tier 2",
                    "classified_at": "2026-09-18T12:00:00Z",
                    "classified_by": "builder",
                    "requires_second_review": True,
                    "harvest": {"classes": []},
                }
            },
            "module_specs": {
                "M1": {
                    "data_model": "n/a",
                    "api_contract": "n/a",
                    "justification": "test",
                    "classified_at": "2026-09-18T12:00:00Z",
                    "classified_by": "manual",
                }
            },
            "_decisions": [{"id": "ADR-001", "title": "ADR 1", "context": "ctx", "module_id": "M1", "decision": "ok"}],
            "workflow_receipts": {"reviews": {}, "executions": {}, "approvals": {}},
        })
        (self.root / "_project" / "03_project_state.json").write_text(
            json.dumps(self.state), encoding="utf-8"
        )
        _git(self.root, "init", "-q")
        _git(self.root, "add", "-A")
        _git(self.root, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "init")

    def tearDown(self):
        self.tmp.cleanup()

    def test_closure_view_and_cli_show_drift_warning_when_docs_change(self):
        wr.record_approval(
            self.state, self.root, "M1",
            approved_by="builder", evidence="aprobado", risk_summary="bajo",
            adr_refs=["ADR-001"], now_iso="2026-09-18T12:00:00Z",
        )
        wr.start_review(
            self.state, self.root, "M1",
            reviewer_id="reviewer", implementer_id="builder",
            mode="INDEPENDENT_SUBAGENT_NO_ISOLATION", now_iso="2026-09-18T12:01:00Z",
        )
        wr.finish_review(
            self.state, self.root, "M1",
            reviewer_id="reviewer", result="pass",
            evidence="Revisión independiente", now_iso="2026-09-18T12:02:00Z",
        )
        wr.record_execution(
            self.state, self.root, "M1",
            kind="ok_automatizada", command="pytest", exit_code=0,
            evidence="Test ejecutado con éxito", now_iso="2026-09-18T12:03:00Z",
        )
        # FW-276 (5.2.2 L1): checkpoints tier_2 del paso 3.9.
        for kind in ("domain_sweep", "expert_audit"):
            wr.record_checkpoint(
                self.state, "M1", kind=kind, scope_id="M1",
                evidence="Barrido de sub-dominios completado sin hallazgos.",
                reference="09_domain_profile.md fila M1", recorded_by="test",
                now_iso="2026-09-18T12:03:30Z",
            )
        (self.root / "_project" / "03_project_state.json").write_text(
            json.dumps(self.state), encoding="utf-8"
        )

        (self.root / "docs" / "x.md").write_text("# Doc X actualizado\n", encoding="utf-8")

        paths = {
            "WORKSPACE_ROOT": self.root,
            "STATE_FILE_PATH": self.root / "_project" / "03_project_state.json",
            "CORE_FILE_PATH": self.root / "_project" / "core.md",
            "BUGS_FILE_PATH": self.root / "_project" / "06_bug_fixes.md",
            "CLARIFICATIONS_FILE_PATH": self.root / "_project" / "07_pending_clarifications.md",
            "DECISIONS_FILE_PATH": self.root / "_project" / "03_arch_decisions.md",
            "SESSION_BRIEF_PATH": self.root / "_project" / "00_session_brief.json",
        }
        with mock.patch.multiple("state_core", **paths):
            mgr = state_manager.StateManager()
            ctx = state_manager.get_work_context(mgr, view="closure", module_id="M1")

        dumped = json.dumps(ctx, ensure_ascii=False)
        self.assertIn("deriva documental posterior al sello (cambió la huella de documentación)", dumped)

        with mock.patch("validate_closure.STATE", self.root / "_project" / "03_project_state.json"), \
             mock.patch("validate_closure.ACCEPTANCE", self.root / "_project" / "08_module_acceptance.md"), \
             mock.patch("validate_closure.ROOT", self.root):
            buf = io.StringIO()
            with redirect_stdout(buf):
                exit_code = vc.main(["validate_closure.py", "M1"])
            self.assertEqual(exit_code, 0)
            output = buf.getvalue()
            self.assertIn("CLOSURE GATE: checks mecánicos + cobertura de ACs OK", output)
            self.assertIn("deriva documental posterior al sello (cambió la huella de documentación)", output)


if __name__ == "__main__":
    unittest.main()
