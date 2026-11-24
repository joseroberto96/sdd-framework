#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_action_contracts.py - Pruebas de contratos consultables y errores accionables (Plan 4.5 Lote 2)."""
import copy
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = ROOT / ".agent" / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import action_contracts
import state_core
import state_manager
import workflow_receipts


class TestActionContractsCatalog(unittest.TestCase):
    """Verifica cobertura, derivación dinámica y correspondencia de metadatos."""

    def test_all_builtin_actions_have_contracts(self):
        actions = action_contracts.list_actions()
        self.assertEqual(set(actions), set(state_core.BUILTIN_ACTIONS))
        self.assertEqual(len(actions), 49)  # 5.3.3: abandon_module, reassign_bug_module

    def test_describe_action_derives_keys_from_state_core(self):
        for action in state_core.BUILTIN_ACTIONS:
            contract = action_contracts.describe_action(action)
            self.assertIsNotNone(contract, f"Contrato ausente para {action}")
            self.assertEqual(contract["action"], action)
            self.assertIn("description", contract)
            self.assertTrue(len(contract["description"]) > 10)

            # Derivación dinámica sin transcripción:
            expected_required = sorted(state_core._REQUIRED_PAYLOAD_KEYS.get(action, set()))
            expected_optional = sorted(state_core._OPTIONAL_PAYLOAD_KEYS.get(action, set()))
            self.assertEqual(contract["required_keys"], expected_required, f"Mismatch required en {action}")
            self.assertEqual(contract["optional_keys"], expected_optional, f"Mismatch optional en {action}")

            # Correspondencia con _STRICT_PAYLOAD_KEYS:
            strict_keys = state_core._STRICT_PAYLOAD_KEYS[action]
            field_keys = set(contract["fields"].keys())
            self.assertEqual(field_keys, strict_keys, f"Mismatch campos documentados en {action}")

            # Ejemplo sintético válido:
            example = contract["example"]
            self.assertIsInstance(example, dict, f"Ejemplo no es dict en {action}")
            example_keys = set(example.keys())
            self.assertTrue(
                set(expected_required).issubset(example_keys),
                f"Ejemplo en {action} omite campos obligatorios: {set(expected_required) - example_keys}",
            )
            self.assertTrue(
                example_keys.issubset(strict_keys),
                f"Ejemplo en {action} contiene claves no permitidas: {example_keys - strict_keys}",
            )

    def test_describe_action_unknown_returns_none(self):
        self.assertIsNone(action_contracts.describe_action("accion_inexistente"))


class TestActionContractsCLI(unittest.TestCase):
    """Verifica el comando CLI state_manager.py describe."""

    def test_cli_describe_known_action_exit_0(self):
        res = subprocess.run(
            [sys.executable, str(SCRIPTS_DIR / "state_manager.py"), "describe", "add_work_tier"],
            capture_output=True,
            text=True,
        )
        self.assertEqual(res.returncode, 0, f"CLI describe falló: {res.stderr}")
        data = json.loads(res.stdout)
        self.assertEqual(data["action"], "add_work_tier")
        self.assertEqual(data["required_keys"], ["id", "rationale", "tier"])
        self.assertIn("classified_by", data["optional_keys"])

    def test_cli_describe_unknown_action_exit_2(self):
        res = subprocess.run(
            [sys.executable, str(SCRIPTS_DIR / "state_manager.py"), "describe", "accion_inexistente"],
            capture_output=True,
            text=True,
        )
        self.assertEqual(res.returncode, 2)
        err = json.loads(res.stderr)
        self.assertEqual(err["error"]["code"], "UNKNOWN_ACTION")
        self.assertIn("Acción desconocida: 'accion_inexistente'", err["error"]["message"])

    def test_cli_describe_missing_arg_exit_2(self):
        res = subprocess.run(
            [sys.executable, str(SCRIPTS_DIR / "state_manager.py"), "describe"],
            capture_output=True,
            text=True,
        )
        self.assertEqual(res.returncode, 2)
        err = json.loads(res.stderr)
        self.assertEqual(err["error"]["code"], "INVALID_ARGUMENT")

    def test_cli_mutate_unknown_action_exit_2(self):
        res = subprocess.run(
            [sys.executable, str(SCRIPTS_DIR / "state_manager.py"), "mutate", "--action", "inexistente", "--payload", "{}"],
            capture_output=True,
            text=True,
        )
        self.assertEqual(res.returncode, 2)
        self.assertIn("Acción desconocida: 'inexistente'", res.stderr)

    def test_cli_mutate_rejection_prints_contract_and_exit_2(self):
        res = subprocess.run(
            [sys.executable, str(SCRIPTS_DIR / "state_manager.py"), "mutate", "--action", "add_work_tier", "--payload", '{"clave_mala": 123}'],
            capture_output=True,
            text=True,
        )
        self.assertEqual(res.returncode, 2)
        self.assertIn("clave(s) de payload no reconocidas", res.stderr)
        self.assertIn("Contrato de 'add_work_tier':", res.stderr)
        self.assertIn('"required_keys"', res.stderr)


class TestActionContractsMCP(unittest.TestCase):
    """Verifica la herramienta MCP describe_action y el enriquecimiento de errores en mutate_state."""

    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.tmp_root = Path(self.tmp_dir.name)
        (self.tmp_root / "_project").mkdir(parents=True, exist_ok=True)

        self.orig_state_path = state_core.STATE_FILE_PATH
        self.orig_root = state_core.WORKSPACE_ROOT

        state_core.STATE_FILE_PATH = self.tmp_root / "_project" / "03_project_state.json"
        state_core.WORKSPACE_ROOT = self.tmp_root

        base_state = {
            "project": "TestProject",
            "framework_mode": "template",
            "framework_version": "4.5.0",
            "current_phase": "phase_01",
            "current_step": "init",
            "last_completed_step": "Ninguno",
            "modules": {"completed": [], "in_progress": ["mod-test"], "pending": []},
            "module_statuses": {"mod-test": "in_progress"},
            "work_tiers": {
                "mod-test": {
                    "tier": "tier_1",
                    "rationale": "Test module tier",
                    "classified_at": "2026-06-08T00:00:00Z",
                    "classified_by": "manual",
                }
            },
            "dependencies": [],
            "closure_rejections": [],
            "_bugs": [],
            "_clarifications": [],
            "_decisions": [],
        }
        state_core.STATE_FILE_PATH.write_text(json.dumps(base_state, indent=2), encoding="utf-8")
        self.manager = state_manager.StateManager()

    def tearDown(self):
        state_core.STATE_FILE_PATH = self.orig_state_path
        state_core.WORKSPACE_ROOT = self.orig_root
        self.tmp_dir.cleanup()

    def test_mcp_tools_list_includes_describe_action(self):
        req = {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}
        resp = state_manager.handle_mcp_message(self.manager, req)
        tools = resp.get("result", {}).get("tools", [])
        tool_names = [t["name"] for t in tools]
        self.assertIn("describe_action", tool_names)
        self.assertIn("lookup_state", tool_names)
        describe_tool = next(t for t in tools if t["name"] == "describe_action")
        self.assertIn("action", describe_tool["inputSchema"]["properties"])

        # get_full_state actualizado con mención diagnóstica explícita
        gfs_tool = next(t for t in tools if t["name"] == "get_full_state")
        self.assertIn("DIAGNÓSTICO", gfs_tool["description"])

    def test_mcp_describe_action_call(self):
        req = {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {"name": "describe_action", "arguments": {"action": "set_focus"}},
        }
        resp = state_manager.handle_mcp_message(self.manager, req)
        self.assertNotIn("error", resp)
        content = resp["result"]["content"][0]["text"]
        data = json.loads(content)
        self.assertEqual(data["action"], "set_focus")
        self.assertEqual(data["required_keys"], ["area"])

    def test_mcp_describe_action_unknown_error(self):
        req = {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {"name": "describe_action", "arguments": {"action": "accion_fantasma"}},
        }
        resp = state_manager.handle_mcp_message(self.manager, req)
        self.assertTrue(resp["result"]["isError"])
        content = json.loads(resp["result"]["content"][0]["text"])
        self.assertEqual(content["error"]["code"], "UNKNOWN_ACTION")

    def test_mcp_mutate_state_rejection_enriches_contract(self):
        req = {
            "jsonrpc": "2.0",
            "id": 4,
            "method": "tools/call",
            "params": {"name": "mutate_state", "arguments": {"action": "set_focus", "payload": {"clave_erronea": "x"}}},
        }
        resp = state_manager.handle_mcp_message(self.manager, req)
        self.assertTrue(resp["result"]["isError"])
        content = json.loads(resp["result"]["content"][0]["text"])
        self.assertEqual(content["error"]["code"], "MUTATION_REJECTED")
        self.assertIn("Contrato de 'set_focus':", content["error"]["message"])
        self.assertIn('"required_keys": [\n    "area"\n  ]', content["error"]["message"])


class TestActionableReceiptAndReviewErrors(unittest.TestCase):
    """Verifica mensajes accionables para stale APPROVE_RECEIPT y modos de review."""

    def test_stale_approve_receipt_identifies_changed_dimensions(self):
        state = {
            "work_tiers": {"M1": {"tier": "tier_1"}},
            "workflow_receipts": {
                "approvals": {
                    "M1": {
                        "status": "approved",
                        "approved_by": "builder",
                        "tier": "tier_1",
                        "fingerprint": "fake_fingerprint",
                        "basis": {
                            "active_scope": "hash_old_scope",
                            "acceptance": "hash_same_acceptance",
                        },
                        "approval_evidence": "evidencia suficiente",
                        "risk_summary": "resumen riesgo",
                    }
                }
            },
        }
        # Simular basis actual diferente en active_scope
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            (tmp_path / "_project").mkdir()
            current_basis = {
                "active_scope": "hash_new_scope",
                "acceptance": "hash_same_acceptance",
            }
            problems = workflow_receipts.approval_problems(
                tmp_path, state, "M1", current_basis=current_basis
            )
            stale_problems = [p for p in problems if "stale" in p]
            self.assertTrue(len(stale_problems) > 0)
            self.assertIn("active_scope", stale_problems[0])
            self.assertIn("cambiaron dimensiones (active_scope", stale_problems[0])

    def test_invalid_review_mode_lists_valid_modes_in_start_review(self):
        state = {"work_tiers": {"M1": {"tier": "tier_1"}}, "workflow_receipts": {"reviews": {}}}
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError) as ctx:
                workflow_receipts.start_review(
                    state,
                    Path(tmp),
                    "M1",
                    reviewer_id="reviewer",
                    implementer_id="coder",
                    mode="MODO_INVALIDO",
                    now_iso="2026-09-15T00:00:00Z",
                )
            self.assertIn("modo de review inválido: MODO_INVALIDO", str(ctx.exception))
            self.assertIn("Modos válidos:", str(ctx.exception))
            self.assertIn("READ_ONLY_SUBAGENTS", str(ctx.exception))
            self.assertIn("INDEPENDENT_SUBAGENT_NO_ISOLATION", str(ctx.exception))


class TestSyntheticExamplesAgainstTemporaryFixture(unittest.TestCase):
    """Ejecuta los ejemplos sintéticos del catálogo contra un fixture temporal real (sin dry-run simulado)."""

    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.tmp_root = Path(self.tmp_dir.name)
        (self.tmp_root / "_project").mkdir(parents=True, exist_ok=True)
        manifest_dir = self.tmp_root / ".agent" / "workflows"
        manifest_dir.mkdir(parents=True, exist_ok=True)
        (manifest_dir / "manifest.json").write_text(
            json.dumps(
                {
                    "commands": [
                        {"name": "arrancar", "builder_facing": True},
                        {"name": "reconciliar", "builder_facing": True},
                        {"name": "gycd", "builder_facing": True},
                        {"name": "ciclo", "builder_facing": True},
                        {"name": "cierre-modulo", "builder_facing": True},
                    ]
                }
            ),
            encoding="utf-8",
        )

        self.orig_state_path = state_core.STATE_FILE_PATH
        self.orig_core_path = state_core.CORE_FILE_PATH
        self.orig_bugs_path = state_core.BUGS_FILE_PATH
        self.orig_clars_path = state_core.CLARIFICATIONS_FILE_PATH
        self.orig_decisions_path = state_core.DECISIONS_FILE_PATH
        self.orig_session_brief_path = state_core.SESSION_BRIEF_PATH
        self.orig_root = state_core.WORKSPACE_ROOT

        state_core.STATE_FILE_PATH = self.tmp_root / "_project" / "03_project_state.json"
        state_core.CORE_FILE_PATH = self.tmp_root / "_project" / "core.md"
        state_core.BUGS_FILE_PATH = self.tmp_root / "_project" / "06_bug_fixes.md"
        state_core.CLARIFICATIONS_FILE_PATH = self.tmp_root / "_project" / "07_pending_clarifications.md"
        state_core.DECISIONS_FILE_PATH = self.tmp_root / "_project" / "03_arch_decisions.md"
        state_core.SESSION_BRIEF_PATH = self.tmp_root / "_project" / "00_session_brief.json"
        state_core.WORKSPACE_ROOT = self.tmp_root

    def tearDown(self):
        state_core.STATE_FILE_PATH = self.orig_state_path
        state_core.CORE_FILE_PATH = self.orig_core_path
        state_core.BUGS_FILE_PATH = self.orig_bugs_path
        state_core.CLARIFICATIONS_FILE_PATH = self.orig_clars_path
        state_core.DECISIONS_FILE_PATH = self.orig_decisions_path
        state_core.SESSION_BRIEF_PATH = self.orig_session_brief_path
        state_core.WORKSPACE_ROOT = self.orig_root
        self.tmp_dir.cleanup()

    def _init_base_state(self):
        base_state = {
            "project": "FixtureProject",
            "framework_mode": "template",
            "framework_version": "4.5.0",
            "current_phase": "phase_01",
            "current_step": "init",
            "last_completed_step": "Ninguno",
            "stack": {
                "backend": "Python",
                "frontend": "Vanilla JS",
                "database": "SQLite",
                "auth": "None",
            },
            "contracts": {"public_interfaces": {}},
            "modules": {
                "completed": ["mod-done"],
                "in_progress": ["mod-test"],
                "pending": ["mod-pending"],
            },
            "module_statuses": {
                "mod-done": "completed",
                "mod-test": "in_progress",
                "mod-pending": "not_started",
            },
            "work_tiers": {
                "mod-test": {
                    "tier": "tier_1",
                    "rationale": "Initial tier",
                    "classified_at": "2026-06-08T00:00:00Z",
                    "classified_by": "manual",
                },
                "mod-done": {
                    "tier": "tier_1",
                    "rationale": "Done tier",
                    "classified_at": "2026-06-08T00:00:00Z",
                    "classified_by": "manual",
                },
            },
            "dependencies": [],
            "architecture_constraints": [],
            "active_mcps": [],
            "closure_rejections": [],
            "closure_rejection_archive": [],
            "module_specs": {},
            "_refs": {},
            "_bugs": [
                {
                    "id": "BUG-001",
                    "module_id": "mod-test",
                    "severity": "Menor",
                    "description": "Bug de prueba para enmiendas",
                    "status": "Abierto",
                    "fix": "",
                    "affected_files": [],
                    "created_at": "2026-06-08T00:00:00Z",
                }
            ],
            "_clarifications": [
                {
                    "id": "CLAR-001",
                    "title": "Pregunta",
                    "blocked_scope": "mod-test",
                    "blocks_start": False,
                    "blocks_implementation": False,
                    "blocks_closure": False,
                    "blocking_condition": "ninguna",
                    "options": "opcion 1, opcion 2",
                    "impact": "bajo",
                    "question": "¿Duda de prueba?",
                    "status": "Abierta",
                    "answer": "",
                    "created_at": "2026-06-08T00:00:00Z",
                }
            ],
            "_decisions": [
                {
                    "id": "ADR-001",
                    "title": "Decision previa",
                    "date": "2026-06-08",
                    "context": "Contexto",
                    "decision": "Decisión",
                    "status": "Aceptada",
                    "module_id": "mod-test",
                    "created_at": "2026-06-08T00:00:00Z",
                },
                {
                    "id": "ADR-002",
                    "title": "Decision nueva",
                    "date": "2026-06-08",
                    "context": "Contexto nuevo",
                    "decision": "Decisión nueva",
                    "status": "Aceptada",
                    "module_id": "mod-test",
                    "created_at": "2026-06-08T00:00:00Z",
                },
            ],
            "changes": [],
            "lifecycle": {
                "stage": "production",
            },
        }
        state_core.STATE_FILE_PATH.write_text(json.dumps(base_state, indent=2), encoding="utf-8")
        return state_manager.StateManager()

    def test_synthetic_examples_execute_against_fixture(self):
        """Verifica que los ejemplos sintéticos son payloads legalmente aceptados por el motor."""
        executable_actions = [
            "add_active_mcp",
            "add_closure_rejection",
            "add_module",
            "add_work_tier",
            "amend_bug",
            "append_to_list_field",
            "ciclo_started",
            "ciclo_paused",
            "ciclo_finished",
            "classify_module_spec",
            "defer_bug_to_tech_debt",
            "drop_module",
            "abandon_module",
            "reassign_bug_module",
            "open_change",
            "record_checkpoint",
            "record_data_safety_check",
            "record_harvest",
            "register_bug",
            "register_clarification",
            "register_decision",
            "reopen_module",
            "resolve_bug",
            "resolve_clarification",
            "set_active_module",
            "set_execution_profile",
            "set_focus",
            "set_framework_version",
            "set_lifecycle",
            "set_phase",
            "set_session_pointer",
            "supersede_decision",
            "update_last_step",
            "update_module_status",
        ]

        for action in executable_actions:
            mgr = self._init_base_state()
            contract = action_contracts.describe_action(action)
            example = copy.deepcopy(contract["example"])
            try:
                msg = mgr.mutate(action, example)
                self.assertIsInstance(msg, str, f"Acción {action} no devolvió mensaje str")
            except Exception as e:
                self.fail(f"Ejemplo sintético de '{action}' falló al mutar en fixture: {e}")

    def test_approval_and_review_synthetic_examples(self):
        """Verifica los ejemplos sintéticos de receipts de aprobación y revisión."""
        mgr = self._init_base_state()

        # record_approval_receipt
        contract_approval = action_contracts.describe_action("record_approval_receipt")
        ex_approval = copy.deepcopy(contract_approval["example"])
        msg = mgr.mutate("record_approval_receipt", ex_approval)
        self.assertIn("APPROVE_RECEIPT", msg)

        # start_independent_review
        contract_start_rev = action_contracts.describe_action("start_independent_review")
        ex_start_rev = copy.deepcopy(contract_start_rev["example"])
        msg_rev = mgr.mutate("start_independent_review", ex_start_rev)
        self.assertIn("Review independiente iniciado", msg_rev)

        # record_review_receipt
        contract_rec_rev = action_contracts.describe_action("record_review_receipt")
        ex_rec_rev = copy.deepcopy(contract_rec_rev["example"])
        msg_rec = mgr.mutate("record_review_receipt", ex_rec_rev)
        self.assertIn("Review independiente", msg_rec)

        # record_execution_receipt
        contract_exec = action_contracts.describe_action("record_execution_receipt")
        ex_exec = copy.deepcopy(contract_exec["example"])
        msg_exec = mgr.mutate("record_execution_receipt", ex_exec)
        self.assertIn("Verificación ejecutable", msg_exec)

    def test_closure_rejection_actions_synthetic_examples(self):
        """Verifica los ejemplos de resolución y restauración de rechazos de cierre."""
        mgr = self._init_base_state()
        # Precondiciones de cierre para mod-test
        mgr.state_data["module_statuses"]["mod-test"] = "ready_for_validation"
        mgr.state_data["module_specs"]["mod-test"] = {
            "data_model": "n/a",
            "api_contract": "n/a",
            "classified_at": "2026-06-08T00:00:00Z",
            "classified_by": "manual",
            "justification": "test",
        }
        mgr.state_data["work_tiers"]["mod-test"]["harvest"] = {"classes": "none", "recorded_at": "2026-06-08T00:00:00Z"}
        (self.tmp_root / "tests").mkdir(parents=True, exist_ok=True)
        (self.tmp_root / "tests" / "test_mod.py").write_text("# test file\n", encoding="utf-8")
        acceptance_content = (
            "# Acceptance\n\n"
            "## mod-test\n"
            "- [x] AC1 — evidencia: verificada en tests/test_mod.py rc=0\n\n"
            "### Sello del Builder (mod-test)\n"
            "- Validación del Builder: APROBADO\n"
        )
        (self.tmp_root / "_project" / "08_module_acceptance.md").write_text(acceptance_content, encoding="utf-8")
        mgr.state_data["closure_rejections"] = [
            {"id": "CRR-0001", "module_id": "mod-test", "reason": "ac_incomplete", "timestamp": "2026-06-08T00:00:00Z"}
        ]
        mgr.save_state()

        # resolve_closure_rejections
        contract_res = action_contracts.describe_action("resolve_closure_rejections")
        ex_res = copy.deepcopy(contract_res["example"])
        msg_res = mgr.mutate("resolve_closure_rejections", ex_res)
        self.assertIn("rechazos de cierre resueltos", msg_res)

        # restore_closure_rejections
        contract_rest = action_contracts.describe_action("restore_closure_rejections")
        ex_rest = copy.deepcopy(contract_rest["example"])
        # Poner mod-test en not completed para permitir restore y archivar el rechazo
        mgr.state_data["module_statuses"]["mod-test"] = "in_progress"
        mgr.state_data["closure_rejections"] = []
        mgr.state_data["closure_rejection_archive"] = [
            {"id": "CRR-0001", "module_id": "mod-test", "reason": "ac_incomplete", "timestamp": "2026-06-08T00:00:00Z"}
        ]
        mgr.save_state()
        msg_rest = mgr.mutate("restore_closure_rejections", ex_rest)
        self.assertIn("rechazos de cierre restaurados", msg_rest)

    def test_maintenance_change_lifecycle_synthetic_examples(self):
        """Verifica cierre y escalado de cambios en modo mantenimiento."""
        mgr = self._init_base_state()
        (self.tmp_root / "tests").mkdir(parents=True, exist_ok=True)
        (self.tmp_root / "tests" / "test_change.py").write_text("# test\n", encoding="utf-8")
        # Asegurar estado production con cambio CHG-001 abierto
        mgr.state_data["changes"] = [
            {
                "id": "CHG-001",
                "summary": "Cambio de prueba",
                "affected_modules": ["mod-test"],
                "tier": "tier_0",
                "criteria": ["Respuestas HTTP incluyen Access-Control-Allow-Origin"],
                "status": "open",
                "opened_at": "2026-06-08T00:00:00Z",
            }
        ]
        mgr.save_state()

        # close_change
        contract_close = action_contracts.describe_action("close_change")
        ex_close = copy.deepcopy(contract_close["example"])
        msg_close = mgr.mutate("close_change", ex_close)
        self.assertIn("cerrado", msg_close.lower())

    def test_refresh_approval_receipt_lifecycle(self):
        """Verifica la invocación exitosa y validaciones de refresh_approval_receipt (R2.5 / H1)."""
        mgr = self._init_base_state()

        # 1. Registrar aprobación inicial para mod-test
        contract_approval = action_contracts.describe_action("record_approval_receipt")
        ex_approval = copy.deepcopy(contract_approval["example"])
        msg_app = mgr.mutate("record_approval_receipt", ex_approval)
        self.assertIn("APPROVE_RECEIPT", msg_app)
        orig_receipt = mgr.state_data["workflow_receipts"]["approvals"]["mod-test"]
        orig_fp = orig_receipt["fingerprint"]

        # 2. Rechazo si faltan claves obligatorias (parent_fingerprint o evidence)
        with self.assertRaises(ValueError) as ctx:
            mgr.mutate("refresh_approval_receipt", {"module": "mod-test"})
        self.assertIn("parent_fingerprint", str(ctx.exception))
        self.assertIn("evidence", str(ctx.exception))

        # 3. Realizar cambio aditivo: añadir ADR a mod-test
        mgr.mutate("register_decision", {
            "title": "Decisión aditiva",
            "context": "Contexto aditivo",
            "decision": "Decisión aditiva aprobada",
            "module_id": "mod-test",
        })

        # 4. Refrescar aprobación con éxito de extremo a extremo
        msg_ref = mgr.mutate("refresh_approval_receipt", {
            "module": "mod-test",
            "parent_fingerprint": orig_fp,
            "evidence": "Ampliación de especificación técnica con ADR-003",
            "derived_from": ["_project/03_project_state.json"],
            "additive_rationale": "Ampliación aditiva",
        })
        self.assertIn("APPROVE_RECEIPT derivado actualizado", msg_ref)
        ref_receipt = mgr.state_data["workflow_receipts"]["approvals"]["mod-test"]
        self.assertNotEqual(ref_receipt["fingerprint"], orig_fp)
        self.assertEqual(ref_receipt["parent_fingerprint"], orig_fp)

    def test_append_to_list_field_unique_flag(self):
        """Verifica que append_to_list_field soporta la clave opcional unique (R2.5 / H1)."""
        mgr = self._init_base_state()
        # Con unique=True (default), duplicados se ignoran
        mgr.mutate("append_to_list_field", {"field": "dependencies", "value": "dep-a", "unique": True})
        self.assertIn("dep-a", mgr.state_data["dependencies"])
        mgr.mutate("append_to_list_field", {"field": "dependencies", "value": "dep-a", "unique": True})
        self.assertEqual(mgr.state_data["dependencies"].count("dep-a"), 1)

        # Con unique=False, duplicados se permiten
        mgr.mutate("append_to_list_field", {"field": "dependencies", "value": "dep-a", "unique": False})
        self.assertEqual(mgr.state_data["dependencies"].count("dep-a"), 2)


class TestActionContractImplementationCoverage(unittest.TestCase):
    """Verifica estáticamente que _STRICT_PAYLOAD_KEYS cubre todas las claves
    que consume la implementación del motor en state_core._apply_event y helpers (R2.5).
    """

    def test_coverage_across_all_builtin_actions(self):
        import ast
        import inspect

        src = (SCRIPTS_DIR / "state_core.py").read_text(encoding="utf-8")
        tree = ast.parse(src)

        apply_node = None
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == "_apply_event":
                apply_node = node
                break
        self.assertIsNotNone(apply_node, "No se encontró _apply_event en state_core.py")

        branches = {}
        curr = apply_node.body[1]
        while isinstance(curr, ast.If):
            actions = []
            if isinstance(curr.test, ast.Compare):
                left = curr.test.left
                ops = curr.test.ops
                comps = curr.test.comparators
                if isinstance(left, ast.Name) and left.id == "action" and len(ops) == 1:
                    if isinstance(ops[0], ast.Eq) and isinstance(comps[0], ast.Constant):
                        actions = [comps[0].value]
                    elif isinstance(ops[0], ast.In) and isinstance(comps[0], ast.Set):
                        actions = [elt.value for elt in comps[0].elts if isinstance(elt, ast.Constant)]
            for a in actions:
                branches[a] = curr.body
            if curr.orelse:
                if len(curr.orelse) == 1 and isinstance(curr.orelse[0], ast.If):
                    curr = curr.orelse[0]
                else:
                    break
            else:
                break

        # Acciones de mantenimiento delegadas a _apply_maintenance_action
        maint_node = None
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == "_apply_maintenance_action":
                maint_node = node
                break
        self.assertIsNotNone(maint_node, "No se encontró _apply_maintenance_action en state_core.py")

        maint_branches = {}
        for stmt in maint_node.body:
            if isinstance(stmt, ast.If):
                curr = stmt
                while isinstance(curr, ast.If):
                    if isinstance(curr.test, ast.Compare):
                        left = curr.test.left
                        ops = curr.test.ops
                        comps = curr.test.comparators
                        if isinstance(left, ast.Name) and left.id == "action" and len(ops) == 1 and isinstance(ops[0], ast.Eq):
                            maint_branches[comps[0].value] = curr.body
                    if curr.orelse:
                        if len(curr.orelse) == 1 and isinstance(curr.orelse[0], ast.If):
                            curr = curr.orelse[0]
                        else:
                            maint_branches["escalate_change"] = curr.orelse
                            break
                    else:
                        break

        branches.update(maint_branches)

        helper_dispatch = {
            "record_approval_receipt": workflow_receipts.record_approval,
            "refresh_approval_receipt": workflow_receipts.refresh_derived_approval,
            "start_independent_review": workflow_receipts.start_review,
            "record_review_receipt": workflow_receipts.finish_review,
            "record_execution_receipt": workflow_receipts.record_execution,
            "record_checkpoint": workflow_receipts.record_checkpoint,
            "grandfather_checkpoint_gate": workflow_receipts.grandfather_checkpoints,
        }

        # Parámetros contextuales pasados por el motor (no provenientes de payload)
        excluded_params = {"state", "manager", "now_iso", "workspace_root", "root"}

        missing_by_action = {}
        for action in state_core.BUILTIN_ACTIONS:
            body = branches.get(action, [])
            consumed_keys = set()
            for sub in ast.walk(ast.Module(body=body, type_ignores=[])):
                if isinstance(sub, ast.Subscript):
                    if isinstance(sub.value, ast.Name) and sub.value.id == "payload":
                        if isinstance(sub.slice, ast.Constant) and isinstance(sub.slice.value, str):
                            consumed_keys.add(sub.slice.value)
                elif isinstance(sub, ast.Call):
                    func = sub.func
                    if isinstance(func, ast.Attribute) and func.attr == "get":
                        if isinstance(func.value, ast.Name) and func.value.id == "payload":
                            if sub.args and isinstance(sub.args[0], ast.Constant) and isinstance(sub.args[0].value, str):
                                consumed_keys.add(sub.args[0].value)

            if action in helper_dispatch:
                fn = helper_dispatch[action]
                sig = inspect.signature(fn)
                for param_name in sig.parameters:
                    if param_name in excluded_params:
                        continue
                    consumed_keys.add(param_name)

            # Excluir claves internas inyectadas por el propio motor. Ningún
            # llamador las pasa y NO deben declararse en contratos públicos:
            # declarar `_receipt` permitiría a un llamador inyectar un recibo
            # fabricado, que es justo lo que los receipts existen para impedir.
            #
            # - Prefijo '_': _receipt (state_core 2264/2284/2321),
            #   _status_effect (2137/3512), _closed_by (3266).
            # - `archived_ids` es interna pese a NO llevar prefijo: la escribe
            #   state_core:1818 (`event_payload["archived_ids"] = ...`). No se
            #   renombra a `_archived_ids` porque los logs históricos de los
            #   adoptantes ya la contienen y el replay dejaría de reaplicarlos.
            consumed_keys = {k for k in consumed_keys if not k.startswith("_") and k != "archived_ids"}

            declared_keys = state_core._STRICT_PAYLOAD_KEYS.get(action, set())
            missing = consumed_keys - declared_keys
            if missing:
                missing_by_action[action] = {
                    "missing": sorted(missing),
                    "consumed": sorted(consumed_keys),
                    "declared": sorted(declared_keys),
                }

        self.assertEqual(
            missing_by_action,
            {},
            f"Huecos de contrato encontrados en acciones builtin (claves consumidas no declaradas): {missing_by_action}",
        )


if __name__ == "__main__":
    unittest.main()
