#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_state_manager.py - Pruebas unitarias para el gestor de estado MCP
"""

import unittest
import json
import sys
import io
import shutil
import tempfile
from pathlib import Path

# Cargar el script state_manager dinámicamente
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / ".agent" / "scripts"))
import state_manager
import state_core
import workflow_receipts


class TestMCPTransport(unittest.TestCase):
    def setUp(self):
        # Crear un directorio temporal para simular el workspace
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.tmp_root = Path(self.tmp_dir.name)

        # Parchear las rutas en state_core (canonical location)
        self.original_state_path = state_core.STATE_FILE_PATH
        self.original_core_path = state_core.CORE_FILE_PATH
        self.original_bugs_path = state_core.BUGS_FILE_PATH
        self.original_clars_path = state_core.CLARIFICATIONS_FILE_PATH
        self.original_decisions_path = state_core.DECISIONS_FILE_PATH
        self.original_session_brief_path = state_core.SESSION_BRIEF_PATH
        self.original_root = state_core.WORKSPACE_ROOT

        # Crear la estructura de directorios simulada
        (self.tmp_root / "_project").mkdir(parents=True, exist_ok=True)

        state_core.STATE_FILE_PATH = self.tmp_root / "_project" / "03_project_state.json"
        state_core.CORE_FILE_PATH = self.tmp_root / "_project" / "core.md"
        state_core.BUGS_FILE_PATH = self.tmp_root / "_project" / "06_bug_fixes.md"
        state_core.CLARIFICATIONS_FILE_PATH = self.tmp_root / "_project" / "07_pending_clarifications.md"
        state_core.DECISIONS_FILE_PATH = self.tmp_root / "_project" / "03_arch_decisions.md"
        state_core.SESSION_BRIEF_PATH = self.tmp_root / "_project" / "00_session_brief.json"
        state_core.WORKSPACE_ROOT = self.tmp_root

        manifest_dir = self.tmp_root / ".agent" / "workflows"
        manifest_dir.mkdir(parents=True, exist_ok=True)
        (manifest_dir / "manifest.json").write_text(
            json.dumps(
                {
                    "commands": [
                        {"name": "arrancar", "builder_facing": True},
                        {"name": "reconciliar", "builder_facing": True},
                        {"name": "gycd", "builder_facing": True},
                        {"name": "diseñar", "builder_facing": True},
                        {"name": "nuevo-bug", "builder_facing": True},
                        {"name": "nuevo-proyecto", "builder_facing": True},
                        {"name": "migrar-proyecto", "builder_facing": True},
                        {"name": "actualizar-framework", "builder_facing": True},
                        {"name": "ciclo", "builder_facing": True},
                        {"name": "tdd", "builder_facing": False},
                        {"name": "enriquecer-ticket", "builder_facing": False},
                    ]
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        # Inicializar estado base válido
        self.base_state = {
            "project": "TestProject",
            "framework_mode": "template",
            "framework_version": "3.2",
            "current_phase": "phase_01",
            "current_step": "init",
            "last_completed_step": "Ninguno",
            "stack": {
                "backend": "Python",
                "frontend": "Vanilla JS",
                "database": "SQLite",
                "auth": "None"
            },
            "dependencies": [],
            "architecture_constraints": [],
            "contracts": {
                "public_interfaces": {}
            },
            "modules": {
                "completed": [],
                "in_progress": ["mod-test"],
                "pending": ["mod-pending"]
            },
            "module_statuses": {
                "mod-test": "in_progress",
                "mod-pending": "not_started"
            },
            "work_tiers": {
                "mod-test": {
                    "tier": "tier_1",
                    "rationale": "Test module tier",
                    "classified_at": "2026-06-08T00:00:00Z",
                    "classified_by": "manual"
                }
            },
            "module_specs": {},
            "closure_rejections": [],
            "active_mcps": [],
            "_refs": {}
        }
        with open(state_core.STATE_FILE_PATH, "w", encoding="utf-8") as f:
            json.dump(self.base_state, f, indent=2, sort_keys=True)

        self.manager = state_manager.StateManager()

    def tearDown(self):
        # Restaurar las rutas y limpiar archivos temporales
        state_core.STATE_FILE_PATH = self.original_state_path
        state_core.CORE_FILE_PATH = self.original_core_path
        state_core.BUGS_FILE_PATH = self.original_bugs_path
        state_core.CLARIFICATIONS_FILE_PATH = self.original_clars_path
        state_core.DECISIONS_FILE_PATH = self.original_decisions_path
        state_core.SESSION_BRIEF_PATH = self.original_session_brief_path
        state_core.WORKSPACE_ROOT = self.original_root
        self.tmp_dir.cleanup()

    def test_initialize(self):
        req = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {}
        }
        resp = state_manager.handle_mcp_request(self.manager, req)
        self.assertIsNotNone(resp)
        self.assertEqual(resp["id"], 1)
        self.assertEqual(resp["result"]["protocolVersion"], "2024-11-05")
        self.assertEqual(resp["result"]["serverInfo"]["name"], "mcp-state-manager")

    def test_tools_list(self):
        req = {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/list",
            "params": {}
        }
        resp = state_manager.handle_mcp_request(self.manager, req)
        self.assertIsNotNone(resp)
        self.assertEqual(resp["id"], 2)
        tools = resp["result"]["tools"]
        tool_names = [t["name"] for t in tools]
        self.assertIn("get_session_brief", tool_names)
        self.assertIn("get_full_state", tool_names)
        self.assertIn("lookup_state", tool_names)
        self.assertIn("mutate_state", tool_names)

    def test_record_approval_receipt_is_persisted_and_stale_is_detected(self):
        project = self.tmp_root / "_project"
        (project / "04_active_scope.md").write_text("# mod-test\nScope aprobado\n")
        (project / "08_module_acceptance.md").write_text("## mod-test\n- [ ] AC\n")
        msg = self.manager.mutate("record_approval_receipt", {
            "module": "mod-test",
            "approved_by": "builder",
            "evidence": "adelante con este módulo",
            "risk_summary": "tier 1 reversible",
            "adr_refs": [],
        })
        self.assertIn("APPROVE_RECEIPT", msg)
        self.assertEqual(
            workflow_receipts.approval_problems(self.tmp_root, self.manager.state_data, "mod-test"),
            [],
        )
        (project / "04_active_scope.md").write_text("# mod-test\nScope cambiado\n")
        self.assertTrue(any(
            "stale" in p for p in workflow_receipts.approval_problems(
                self.tmp_root, self.manager.state_data, "mod-test"
            )
        ))

    def test_receipt_log_replays_exact_payload_without_rehashing_workspace(self):
        self.manager.mutate("record_approval_receipt", {
            "module": "mod-test",
            "approved_by": "builder",
            "evidence": "adelante",
            "risk_summary": "tier 1 reversible",
            "adr_refs": [],
        })
        log_path = self.tmp_root / "_project" / "state_log.jsonl"
        events = [json.loads(line) for line in log_path.read_text().splitlines()]
        event = next(e for e in reversed(events) if e["action"] == "record_approval_receipt")
        self.assertIn("_receipt", event["payload"])

        replay_manager = state_manager.StateManager.__new__(state_manager.StateManager)
        replay_manager._is_replay = True
        replay_manager.state_data = json.loads(json.dumps(self.base_state))
        replay_manager._apply_event(event["action"], event["payload"], event["ts"])
        self.assertEqual(
            replay_manager.state_data["workflow_receipts"]["approvals"]["mod-test"],
            event["payload"]["_receipt"],
        )

    def test_caller_cannot_inject_internal_receipt(self):
        with self.assertRaisesRegex(ValueError, "metadata interna"):
            self.manager.mutate("record_approval_receipt", {
                "module": "mod-test",
                "approved_by": "builder",
                "evidence": "adelante",
                "risk_summary": "tier 1 reversible",
                "adr_refs": [],
                "_receipt": {"status": "approved"},
            })

    def test_review_actions_require_distinct_agent_and_unchanged_diff(self):
        with self.assertRaisesRegex(ValueError, "distinto"):
            self.manager.mutate("start_independent_review", {
                "module": "mod-test",
                "reviewer_id": "agent-1",
                "implementer_id": "agent-1",
                "mode": "INDEPENDENT_SUBAGENT_NO_ISOLATION",
            })
        self.manager.mutate("start_independent_review", {
            "module": "mod-test",
            "reviewer_id": "agent-2",
            "implementer_id": "agent-1",
            "mode": "INDEPENDENT_SUBAGENT_NO_ISOLATION",
        })
        msg = self.manager.mutate("record_review_receipt", {
            "module": "mod-test",
            "reviewer_id": "agent-2",
            "result": "pass",
            "evidence": "diff y ACs revisados",
        })
        self.assertIn("'pass'", msg)
        receipt = self.manager.state_data["workflow_receipts"]["reviews"]["mod-test"][-1]
        self.assertEqual(receipt["reviewer_id"], "agent-2")

    def test_invalidate_stale_review_is_auditable_and_replayable(self):
        self.manager.mutate("start_independent_review", {
            "module": "mod-test",
            "reviewer_id": "agent-2",
            "implementer_id": "agent-1",
            "mode": "INDEPENDENT_SUBAGENT_NO_ISOLATION",
        })
        (self.tmp_root / "repair.py").write_text("print('reparado')\n")

        message = self.manager.mutate("invalidate_stale_review", {
            "module": "mod-test",
            "approved_by": "builder",
            "reason": "La ronda quedó stale tras la reparación canónica.",
        })

        self.assertIn("iniciar una nueva ronda", message)
        receipt = self.manager.state_data["workflow_receipts"]["reviews"]["mod-test"][-1]
        self.assertEqual(receipt["status"], "fail")
        self.assertTrue(receipt["invalidated"])
        event = next(
            event for event in reversed(
                (self.tmp_root / "_project" / "state_log.jsonl").read_text().splitlines()
            )
            if json.loads(event)["action"] == "invalidate_stale_review"
        )
        payload = json.loads(event)["payload"]
        self.assertEqual(payload["_receipt"], receipt)

    def test_replay_applies_synthetic_review_invalidation_events(self):
        import replay_state_log

        module = "mod-test"
        self.manager.mutate("start_independent_review", {
            "module": module,
            "reviewer_id": "example-reviewer",
            "implementer_id": "example-implementer",
            "mode": "INDEPENDENT_SUBAGENT_NO_ISOLATION",
        })
        (self.tmp_root / "example.py").write_text("print('example')\n", encoding="utf-8")
        reason = "El cambio de ejemplo requiere revisar nuevamente el diff."
        self.manager.mutate("invalidate_stale_review", {
            "module": module, "approved_by": "builder", "reason": reason,
        })

        log = self.tmp_root / "_project" / "state_log.jsonl"
        stats = replay_state_log.replay(
            log, log.with_name("reconstructed_state.json"), strict=True,
            base_state=self.base_state,
        )
        self.assertEqual(stats["errors"], [])
        reviews = stats["state"]["workflow_receipts"]["reviews"][module]
        self.assertEqual(reviews[-1]["status"], "fail")
        self.assertTrue(reviews[-1]["invalidated"])
        self.assertEqual(reviews[-1]["invalidation_reason"], reason)
        self.assertEqual(reviews[-1], self.manager.state_data["workflow_receipts"]["reviews"][module][-1])

    def test_record_checkpoint_via_mutate(self):
        msg = self.manager.mutate("record_checkpoint", {
            "module": "mod-test", "kind": "domain_sweep", "scope_id": "mod-test",
            "evidence": "Barrido de sub-dominios completado sin hallazgos.",
            "reference": "09_domain_profile.md fila mod-test",
        })
        self.assertIn("domain_sweep", msg)
        receipt = self.manager.state_data["workflow_receipts"]["checkpoints"]["domain_sweep"]["mod-test"]
        self.assertEqual(receipt["reference"], "09_domain_profile.md fila mod-test")

    def test_record_checkpoint_rejects_invalid_kind_via_mutate(self):
        with self.assertRaises(ValueError):
            self.manager.mutate("record_checkpoint", {
                "module": "mod-test", "kind": "no_existe", "scope_id": "mod-test",
                "evidence": "x" * 25, "reference": "algo",
            })

    def test_get_session_brief(self):
        req = {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {
                "name": "get_session_brief",
                "arguments": {}
            }
        }
        resp = state_manager.handle_mcp_request(self.manager, req)
        self.assertIsNotNone(resp)
        content = resp["result"]["content"][0]
        brief_data = json.loads(content["text"])
        self.assertEqual(brief_data["project"], "TestProject")
        self.assertEqual(brief_data["active_module"], "mod-test")

    def _mutate_state_on_disk(self, **changes):
        """Simula otro proceso (otro agente) mutando 03_project_state.json en
        disco mientras este servidor ya tenía el estado en memoria (FW-041)."""
        mutated = dict(self.base_state)
        mutated.update(changes)
        with open(state_core.STATE_FILE_PATH, "w", encoding="utf-8") as f:
            json.dump(mutated, f, indent=2, sort_keys=True)

    def test_get_full_state_reflects_external_disk_mutation(self):
        self._mutate_state_on_disk(last_completed_step="Cerrado por OTRO proceso")
        req = {"jsonrpc": "2.0", "id": 8, "method": "tools/call",
               "params": {"name": "get_full_state", "arguments": {}}}
        resp = state_manager.handle_mcp_request(self.manager, req)
        served = json.loads(resp["result"]["content"][0]["text"])
        self.assertEqual(
            served["last_completed_step"], "Cerrado por OTRO proceso",
            "get_full_state debe releer el disco tras una mutación de otro proceso")

    def test_get_session_brief_reflects_external_disk_mutation(self):
        self._mutate_state_on_disk(last_completed_step="Paso externo")
        req = {"jsonrpc": "2.0", "id": 9, "method": "tools/call",
               "params": {"name": "get_session_brief", "arguments": {}}}
        resp = state_manager.handle_mcp_request(self.manager, req)
        brief = json.loads(resp["result"]["content"][0]["text"])
        self.assertEqual(brief["last_completed_step"], "Paso externo")

    def test_invalid_state_validation(self):
        corrupted_state = dict(self.base_state)
        corrupted_state.pop("project")
        with self.assertRaises(ValueError):
            self.manager.validate_state(corrupted_state)

    def test_update_module_status_transition(self):
        # Transición válida: in_progress -> ready_for_validation
        res = self.manager.mutate("update_module_status", {"id": "mod-test", "status": "ready_for_validation"})
        self.assertIn("ready_for_validation", res)

        # Verificar estado
        self.assertEqual(self.manager.state_data["module_statuses"]["mod-test"], "ready_for_validation")
        self.assertIn("mod-test", self.manager.state_data["modules"]["in_progress"])

        # Transición inválida: ready_for_validation -> blocked (debe fallar según validate_transition)
        with self.assertRaises(ValueError):
            self.manager.mutate("update_module_status", {"id": "mod-test", "status": "blocked"})

    def test_update_module_status_warns_when_starting_without_module_specs(self):
        # Example Tool FW-T08 (known-bad): un módulo arranca sin module_specs
        # clasificado -- antes el hueco solo se descubría en /cierre-modulo,
        # obligando a reconstruir ACs retroactivamente. Ahora avisa temprano
        # (WARN, no bloqueo -- el gate duro de cierre no cambia).
        res = self.manager.mutate("update_module_status", {"id": "mod-pending", "status": "in_progress"})
        self.assertIn("AVISO", res)
        self.assertIn("module_specs", res)

    def test_update_module_status_silent_when_module_specs_classified(self):
        # known-good: si ya se clasificó (p. ej. vía /scope-siguiente antes
        # de arrancar), no hay nada que avisar.
        self.manager.mutate("classify_module_spec", {
            "id": "mod-pending", "data_model": "n/a", "api_contract": "n/a",
            "justification": "módulo sin persistencia ni HTTP",
            "classified_by": "scope-siguiente",
        })
        res = self.manager.mutate("update_module_status", {"id": "mod-pending", "status": "in_progress"})
        self.assertNotIn("AVISO", res)

    def test_append_to_list_field_dependencies(self):
        # Retroporte de fricción (Example Lab FW-005, Example Documents
        # FW-001): añadir a `dependencies` sin editar el estado a mano (Regla 2).
        res = self.manager.mutate("append_to_list_field", {"field": "dependencies", "value": "pyarrow==24.0.0"})
        self.assertIn("pyarrow==24.0.0", res)
        self.assertIn("pyarrow==24.0.0", self.manager.state_data["dependencies"])

    def test_append_to_list_field_is_idempotent(self):
        self.manager.mutate("append_to_list_field", {"field": "architecture_constraints", "value": "sin red en tests"})
        self.manager.mutate("append_to_list_field", {"field": "architecture_constraints", "value": "sin red en tests"})
        self.assertEqual(self.manager.state_data["architecture_constraints"].count("sin red en tests"), 1)

    def test_append_to_list_field_rejects_unknown_field(self):
        # Solo campos whitelisted: no permitir mutación arbitraria del estado.
        with self.assertRaises(ValueError):
            self.manager.mutate("append_to_list_field", {"field": "module_statuses", "value": "x"})

    def test_append_to_list_field_requires_value(self):
        with self.assertRaises(ValueError):
            self.manager.mutate("append_to_list_field", {"field": "dependencies", "value": "  "})

    # --- Patrón A (cosecha 2026-07): campos del estado sin verbo de mutación ---

    def test_add_module_entra_en_pending(self):
        # Example Lab FW-003: no había forma canónica de añadir un módulo
        # nuevo al roadmap sin editar el JSON a mano.
        res = self.manager.mutate("add_module", {"id": "M09_nuevo"})
        self.assertIn("M09_nuevo", res)
        self.assertIn("M09_nuevo", self.manager.state_data["modules"]["pending"])
        self.assertEqual(self.manager.state_data["module_statuses"]["M09_nuevo"], "not_started")

    def test_add_module_rechaza_duplicado(self):
        self.manager.mutate("add_module", {"id": "M09_nuevo"})
        with self.assertRaises(ValueError):
            self.manager.mutate("add_module", {"id": "M09_nuevo"})
        # También si ya existe en otro bucket (mod-test está in_progress)
        with self.assertRaises(ValueError):
            self.manager.mutate("add_module", {"id": "mod-test"})

    def test_add_module_requiere_id(self):
        with self.assertRaises(ValueError):
            self.manager.mutate("add_module", {"id": "  "})

    def test_add_module_luego_transicionable(self):
        # El módulo añadido debe poder arrancar por el camino normal.
        self.manager.mutate("add_module", {"id": "M09_nuevo"})
        self.manager.mutate("update_module_status", {"id": "M09_nuevo", "status": "in_progress"})
        self.assertIn("M09_nuevo", self.manager.state_data["modules"]["in_progress"])

    def test_set_active_module_reordena_in_progress(self):
        # Example Lab FW-002: con dos módulos in_progress la proyección
        # (in_progress[0]) mostraba el más antiguo como activo.
        self.manager.mutate("add_module", {"id": "M09_nuevo"})
        self.manager.mutate("update_module_status", {"id": "M09_nuevo", "status": "in_progress"})
        # mod-test fue el primero; el foco real es M09_nuevo
        self.assertEqual(self.manager.state_data["modules"]["in_progress"][0], "mod-test")
        res = self.manager.mutate("set_active_module", {"id": "M09_nuevo"})
        self.assertIn("M09_nuevo", res)
        self.assertEqual(self.manager.state_data["modules"]["in_progress"][0], "M09_nuevo")
        # mod-test sigue in_progress, solo cambió el orden
        self.assertIn("mod-test", self.manager.state_data["modules"]["in_progress"])

    def test_set_active_module_exige_in_progress(self):
        self.manager.mutate("add_module", {"id": "M09_nuevo"})  # queda pending
        with self.assertRaises(ValueError):
            self.manager.mutate("set_active_module", {"id": "M09_nuevo"})
        with self.assertRaises(ValueError):
            self.manager.mutate("set_active_module", {"id": "no_existe"})

    def test_drop_module_removes_a_module_that_never_started(self):
        """FW-118a: entrada fantasma en pending tras dividir en submódulos."""
        self.manager.mutate("add_module", {"id": "M09_headless_agent_mcp"})
        self.manager.mutate("drop_module", {
            "id": "M09_headless_agent_mcp",
            "reason": "dividido en M09a_mcp_surface y M09b_autonomous_loop, que ya cubren su alcance",
        })
        estado = self._leer_estado()
        self.assertNotIn("M09_headless_agent_mcp", estado["modules"]["pending"])
        self.assertNotIn("M09_headless_agent_mcp", estado.get("module_statuses", {}))

    def test_drop_module_refuses_anything_that_ever_started(self):
        """No se afloja: solo sale del roadmap lo que nunca empezó."""
        self.manager.mutate("add_module", {"id": "M1"})
        self.manager.mutate("update_module_status", {"id": "M1", "status": "in_progress"})
        with self.assertRaises(ValueError) as ctx:
            self.manager.mutate("drop_module", {
                "id": "M1", "reason": "intento de sacar del roadmap algo que ya arranco",
            })
        self.assertIn("in_progress", str(ctx.exception))

    def test_drop_module_refuses_a_module_with_a_classified_spec(self):
        self.manager.mutate("add_module", {"id": "M2"})
        self.manager.mutate("classify_module_spec", {
            "id": "M2", "data_model": "required", "api_contract": "n/a",
            "justification": "sin superficie de API", "classified_by": "scope-siguiente",
        })
        with self.assertRaises(ValueError) as ctx:
            self.manager.mutate("drop_module", {
                "id": "M2", "reason": "intento de sacar del roadmap algo ya clasificado",
            })
        self.assertIn("module_spec", str(ctx.exception))

    def test_drop_module_refuses_an_undeclared_module(self):
        with self.assertRaises(ValueError):
            self.manager.mutate("drop_module", {
                "id": "M_inexistente", "reason": "intento de retirar algo que no esta declarado",
            })

    def test_drop_module_limpia_work_tiers_huerfano(self):
        """Hallazgo 7a (revisión Minor 2026-08-20): un módulo pending puede
        tener tier declarado (add_work_tier no exige in_progress/completed);
        drop_module debe limpiarlo, no dejar basura huérfana."""
        self.manager.mutate("add_module", {"id": "M09_headless_agent_mcp"})
        self.manager.mutate("add_work_tier", {
            "id": "M09_headless_agent_mcp", "tier": "tier_1",
            "rationale": "clasificado antes de dividir", "classified_by": "test",
        })
        self.assertIn("M09_headless_agent_mcp", self.manager.state_data["work_tiers"])
        self.manager.mutate("drop_module", {
            "id": "M09_headless_agent_mcp",
            "reason": "dividido en M09a_mcp_surface y M09b_autonomous_loop, que ya cubren su alcance",
        })
        estado = self._leer_estado()
        self.assertNotIn("M09_headless_agent_mcp", estado.get("work_tiers", {}))

    def test_drop_module_rechaza_si_hay_bug_abierto_apuntando(self):
        """Hallazgo 7b (revisión Minor 2026-08-20): un bug Abierto con
        module_id == mod_id quedaría sin módulo válido al que apuntar (amend_bug
        no permite reasignar module_id) si el módulo desaparece del roadmap."""
        self.manager.mutate("add_module", {"id": "M09_headless_agent_mcp"})
        self.manager.mutate("register_bug", {
            "module_id": "M09_headless_agent_mcp", "severity": "Menor",
            "description": "typo en el borrador de la spec, no bloquea nada",
        })
        with self.assertRaises(ValueError) as ctx:
            self.manager.mutate("drop_module", {
                "id": "M09_headless_agent_mcp",
                "reason": "dividido en submódulos, pero el bug se me olvido resolver",
            })
        mensaje = str(ctx.exception)
        self.assertTrue("bug" in mensaje.lower() or "abierto" in mensaje.lower())
        # El módulo sigue en pending: el retiro fue rechazado, no aplicado.
        estado = self._leer_estado()
        self.assertIn("M09_headless_agent_mcp", estado["modules"]["pending"])

    def test_add_work_tier_acepta_ids_de_tarea_y_bug(self):
        # Example CLI FW-005: los bugfixes puntuales no eran registrables
        # porque add_work_tier solo aceptaba módulos declarados.
        res = self.manager.mutate("add_work_tier", {
            "id": "bug:BUG-003_clock_injection", "tier": "tier_1",
            "rationale": "fix puntual", "classified_by": "test",
        })
        self.assertIn("bug:BUG-003_clock_injection", res)
        self.assertIn("bug:BUG-003_clock_injection", self.manager.state_data["work_tiers"])
        self.manager.mutate("add_work_tier", {
            "id": "task:refactor_menor", "tier": "tier_0",
            "rationale": "tarea suelta", "classified_by": "test",
        })
        self.assertIn("task:refactor_menor", self.manager.state_data["work_tiers"])

    # --- Oleada 1 del triaje Codex (C1): completed solo vía complete_module ---

    def _preparar_modulo_cerrable(self, mod="mod-test"):
        """Deja mod-test listo para cierre legítimo: ready_for_validation,
        tier declarado, specs done y ACs 100% en el archivo de aceptación."""
        self.manager.mutate("update_module_status", {"id": mod, "status": "ready_for_validation"})
        self.manager.mutate("add_work_tier", {
            "id": mod, "tier": "tier_1", "rationale": "test", "classified_by": "test",
        })
        self.manager.mutate("classify_module_spec", {
            "id": mod, "data_model": "n/a", "api_contract": "n/a",
            "justification": "módulo de test sin persistencia ni API",
            "classified_by": "manual",
        })
        acc = self.tmp_root / "_project" / "08_module_acceptance.md"
        # Formato canónico de evidencia (`— evidencia: …`) y artefacto que
        # existe de verdad: desde que el EVIDENCE GATE corre dentro del motor
        # (2026-07-24), un módulo "cerrable" tiene que serlo también para el
        # gate de evidencia, no solo para el de cobertura. El fixture anterior
        # usaba evidencia entre paréntesis y citaba un test inexistente.
        (self.tmp_root / "tests").mkdir(exist_ok=True)
        (self.tmp_root / "tests" / "test_x.py").write_text("def test_x():\n    pass\n", encoding="utf-8")
        acc.write_text(
            f"# ACs\n\n## {mod}\n\nEstado: ✅ Verificado con cliente\n\n"
            "- [x] AC1 pasa — evidencia: `tests/test_x.py` en verde (1 caso)\n",
            encoding="utf-8",
        )
        # FW-144: el EJECUTABLE GATE deja receipt. Un módulo "cerrable" tiene
        # que serlo también para él, igual que pasó con el evidence gate.
        self.manager.mutate("record_execution_receipt", {
            "module": mod, "kind": "no_aplica", "command": "",
            "evidence": "módulo de test sin superficie ejecutable (specs en n/a)",
        })
        # FW-201: HARVEST GATE exige cosecha declarada antes del cierre.
        self.manager.mutate("record_harvest", {"module_id": mod, "classes": "none"})

    def _sellar_tier2(self, mod="mod-test"):
        """Añade ADR, aprobación explícita y review final sobre diff estable."""
        self.manager.mutate("add_work_tier", {
            "id": mod, "tier": "tier_2", "rationale": "alto impacto",
            "classified_by": "test",
        })
        self.manager.mutate("register_decision", {
            "module_id": mod,
            "title": "Rollback Tier 2",
            "context": "cambio de alto impacto",
            "decision": "proceder con rollback documentado",
        })
        self.manager.mutate("record_approval_receipt", {
            "module": mod,
            "approved_by": "builder",
            "evidence": "adelante con el módulo Tier 2",
            "risk_summary": "alto impacto con rollback",
            "adr_refs": ["ADR-001"],
        })
        self.manager.mutate("start_independent_review", {
            "module": mod,
            "reviewer_id": "reviewer-2",
            "implementer_id": "implementer-1",
            "mode": "INDEPENDENT_SUBAGENT_NO_ISOLATION",
        })
        self.manager.mutate("record_review_receipt", {
            "module": mod,
            "reviewer_id": "reviewer-2",
            "result": "pass",
            "evidence": "diff final, ACs y seguridad revisados",
        })
        # FW-201: add_work_tier reemplaza la entrada entera (incluida
        # 'harvest' registrada en _preparar_modulo_cerrable) — reafirmar.
        self.manager.mutate("record_harvest", {"module_id": mod, "classes": "none"})
        # FW-276 (5.2.2 L1): EXPERT GATE en el motor.
        for kind in ("domain_sweep", "expert_audit"):
            self.manager.mutate("record_checkpoint", {
                "module": mod, "kind": kind, "scope_id": mod,
                "evidence": "Barrido de sub-dominios completado sin hallazgos.",
                "reference": f"09_domain_profile.md fila {mod}",
            })

    def test_update_module_status_ya_no_permite_completed(self):
        self._preparar_modulo_cerrable()
        with self.assertRaises(ValueError) as ctx:
            self.manager.mutate("update_module_status", {"id": "mod-test", "status": "completed"})
        self.assertIn("complete_module", str(ctx.exception))

    def test_replay_historico_sigue_aceptando_completed(self):
        # La prohibición vive en mutate() (gate), no en _apply_event: los logs
        # históricos con update_module_status→completed deben poder reproducirse.
        self.manager.mutate("update_module_status", {"id": "mod-test", "status": "ready_for_validation"})
        msg = self.manager._apply_event(
            "update_module_status", {"id": "mod-test", "status": "completed"},
            "2026-07-09T00:00:00+00:00",
        )
        self.assertIn("completed", msg)

    def test_complete_module_bloquea_sin_gates(self):
        # in_progress (no ready_for_validation), sin tier, sin specs → bloquea
        with self.assertRaises(ValueError) as ctx:
            self.manager.mutate("complete_module", {"id": "mod-test"})
        self.assertIn("bloqueado", str(ctx.exception).lower())
        self.assertNotIn("mod-test", self.manager.state_data["modules"]["completed"])

    def test_complete_module_bloquea_con_acs_incompletos(self):
        self._preparar_modulo_cerrable()
        acc = self.tmp_root / "_project" / "08_module_acceptance.md"
        acc.write_text(
            "# ACs\n\n## mod-test\n\n- [x] AC1 — pasa\n- [ ] AC2 — pendiente\n",
            encoding="utf-8",
        )
        with self.assertRaises(ValueError) as ctx:
            self.manager.mutate("complete_module", {"id": "mod-test"})
        self.assertIn("cobertura", str(ctx.exception).lower())

    def test_add_work_tier_marca_segunda_revision_para_ejecutor_limited(self):
        """FW-151: el motor debe poder escribir la marca que su propio gate exige."""
        self.manager.mutate("set_execution_profile", {"capability_tier": "limited"})
        self.manager.mutate("add_work_tier", {
            "id": "mod-test", "tier": "tier_1", "rationale": "cambio acotado",
            "classified_by": "manual",
        })
        entrada = self.manager.state_data["work_tiers"]["mod-test"]
        self.assertIs(entrada.get("requires_second_review"), True)

    def test_add_work_tier_honra_requires_second_review_explicito(self):
        self.manager.mutate("add_work_tier", {
            "id": "mod-test", "tier": "tier_1", "rationale": "toca auth",
            "classified_by": "manual", "requires_second_review": True,
        })
        entrada = self.manager.state_data["work_tiers"]["mod-test"]
        self.assertIs(entrada.get("requires_second_review"), True)

    def test_add_work_tier_no_marca_de_mas_en_tier_1_estandar(self):
        self.manager.mutate("add_work_tier", {
            "id": "mod-test", "tier": "tier_1", "rationale": "rutinario",
            "classified_by": "manual",
        })
        self.assertNotIn(
            "requires_second_review", self.manager.state_data["work_tiers"]["mod-test"]
        )

    def test_add_work_tier_accepts_handles_real_data_flag(self):
        self.manager.mutate("add_work_tier", {
            "id": "mod-test", "tier": "tier_1", "rationale": "persiste PII",
            "classified_by": "manual", "handles_real_data": True,
        })
        self.assertIs(
            self.manager.state_data["work_tiers"]["mod-test"]["handles_real_data"], True
        )

    def test_record_data_safety_check_requires_work_tier(self):
        with self.assertRaises(ValueError) as ctx:
            self.manager.mutate("record_data_safety_check", {
                "module_id": "modulo-sin-tier", "target_env": "prod",
                "backup_verified": True,
                "evidence": "Backup verificado restaurando snapshot en staging.",
            })
        self.assertIn("work_tiers", str(ctx.exception))

    def test_record_data_safety_check_rejects_invalid_target_env(self):
        with self.assertRaises(ValueError) as ctx:
            self.manager.mutate("record_data_safety_check", {
                "module_id": "mod-test", "target_env": "local",
                "backup_verified": True,
                "evidence": "Backup verificado restaurando snapshot en staging.",
            })
        self.assertIn("target_env", str(ctx.exception))

    def test_record_data_safety_check_rejects_short_evidence(self):
        with self.assertRaises(ValueError) as ctx:
            self.manager.mutate("record_data_safety_check", {
                "module_id": "mod-test", "target_env": "prod",
                "backup_verified": True, "evidence": "ok",
            })
        self.assertIn("evidence", str(ctx.exception))

    def test_record_data_safety_check_writes_entry(self):
        msg = self.manager.mutate("record_data_safety_check", {
            "module_id": "mod-test", "target_env": "staging",
            "backup_verified": True,
            "evidence": "Backup verificado restaurando snapshot en staging.",
        })
        self.assertIn("mod-test", msg)
        entry = self.manager.state_data["work_tiers"]["mod-test"]["data_safety"]
        self.assertIs(entry["backup_verified"], True)
        self.assertEqual(entry["target_env"], "staging")

    def test_stub_gate_ausente_bloquea_en_vez_de_saltarse(self):
        """FW-145/FW-146 — un gate ausente no es un gate que pasa.

        La primera versión cableaba el STUB GATE con `except ImportError: pass`:
        un gate contra el fail-open que fallaba abierto. Con el script borrado o
        a medio instalar, el doctor seguía en OK y los módulos cerraban sin
        escanear nada. Aquí se simula el import roto y se exige bloqueo con
        mensaje accionable.
        """
        import unittest.mock
        self._preparar_modulo_cerrable()
        with unittest.mock.patch.dict(sys.modules, {"validate_no_stubs": None}):
            with self.assertRaises(ValueError) as ctx:
                self.manager.mutate("complete_module", {"id": "mod-test"})
        self.assertIn("STUB GATE no disponible", str(ctx.exception))
        self.assertNotIn("mod-test", self.manager.state_data["modules"]["completed"])

    def test_complete_module_cierra_con_gates_verdes(self):
        self._preparar_modulo_cerrable()
        msg = self.manager.mutate("complete_module", {"id": "mod-test"})
        self.assertIn("completed", msg)
        self.assertIn("mod-test", self.manager.state_data["modules"]["completed"])

    def test_complete_module_blocks_without_data_safety_check(self):
        self._preparar_modulo_cerrable()
        self.manager.mutate("add_work_tier", {
            "id": "mod-test", "tier": "tier_1", "rationale": "persiste PII",
            "classified_by": "test", "handles_real_data": True,
        })
        # add_work_tier reemplaza la entrada entera (incluido 'harvest') —
        # reafirmar, mismo patrón que _sellar_tier2.
        self.manager.mutate("record_harvest", {"module_id": "mod-test", "classes": "none"})
        with self.assertRaises(ValueError) as ctx:
            self.manager.mutate("complete_module", {"id": "mod-test"})
        self.assertIn("FW-083", str(ctx.exception))

    def test_complete_module_closes_with_data_safety_check_sealed(self):
        self._preparar_modulo_cerrable()
        self.manager.mutate("add_work_tier", {
            "id": "mod-test", "tier": "tier_1", "rationale": "persiste PII",
            "classified_by": "test", "handles_real_data": True,
        })
        self.manager.mutate("record_harvest", {"module_id": "mod-test", "classes": "none"})
        self.manager.mutate("record_data_safety_check", {
            "module_id": "mod-test", "target_env": "prod", "backup_verified": True,
            "evidence": "Backup verificado restaurando snapshot en staging.",
        })
        msg = self.manager.mutate("complete_module", {"id": "mod-test"})
        self.assertIn("completed", msg)

    def test_complete_module_tier2_bloquea_sin_recibos(self):
        self._preparar_modulo_cerrable()
        self.manager.mutate("add_work_tier", {
            "id": "mod-test", "tier": "tier_2", "rationale": "alto impacto",
            "classified_by": "test",
        })
        with self.assertRaises(ValueError) as ctx:
            self.manager.mutate("complete_module", {"id": "mod-test"})
        self.assertIn("APPROVE_RECEIPT", str(ctx.exception))
        self.assertIn("review receipt", str(ctx.exception))

    def test_complete_module_tier2_cierra_con_recibos_vigentes(self):
        self._preparar_modulo_cerrable()
        self._sellar_tier2()
        msg = self.manager.mutate("complete_module", {"id": "mod-test"})
        self.assertIn("completed", msg)

    def test_complete_module_tier2_bloquea_review_stale(self):
        self._preparar_modulo_cerrable()
        self._sellar_tier2()
        (self.tmp_root / "app.py").write_text("print('cambio posterior')\n")
        with self.assertRaises(ValueError) as ctx:
            self.manager.mutate("complete_module", {"id": "mod-test"})
        self.assertIn("review receipt stale", str(ctx.exception))

    def test_complete_module_falla_cerrado_sin_archivo_de_acs(self):
        self._preparar_modulo_cerrable()
        (self.tmp_root / "_project" / "08_module_acceptance.md").unlink()
        with self.assertRaises(ValueError):
            self.manager.mutate("complete_module", {"id": "mod-test"})

    # --- Oleada 1 (C5): mutación transaccional ---

    def test_mutacion_invalida_no_publica_proyecciones_ni_estado(self):
        core_antes = state_core.CORE_FILE_PATH.read_text(encoding="utf-8") \
            if state_core.CORE_FILE_PATH.exists() else None
        step_antes = self.manager.state_data.get("last_completed_step")
        # payload que corrompe el schema: status inválido llega a validate_state
        with self.assertRaises(ValueError):
            self.manager.mutate("update_module_status", {"id": "mod-test", "status": "estado_marciano"})
        # memoria restaurada (no candidato fantasma)
        self.assertEqual(self.manager.state_data.get("last_completed_step"), step_antes)
        # proyecciones no reescritas con el candidato rechazado
        core_despues = state_core.CORE_FILE_PATH.read_text(encoding="utf-8") \
            if state_core.CORE_FILE_PATH.exists() else None
        self.assertEqual(core_antes, core_despues)

    def test_fallo_de_render_restaura_memoria(self):
        from unittest import mock as _mock
        step_antes = self.manager.state_data.get("last_completed_step")
        with _mock.patch.object(
            self.manager, "render_projections", side_effect=RuntimeError("boom")
        ):
            with self.assertRaises(RuntimeError):
                self.manager.mutate("update_last_step", {"last_completed_step": "candidato"})
        self.assertEqual(self.manager.state_data.get("last_completed_step"), step_antes)
        # y el disco tampoco tiene el candidato
        import json as _json
        en_disco = _json.loads(state_core.STATE_FILE_PATH.read_text(encoding="utf-8"))
        self.assertEqual(en_disco.get("last_completed_step"), step_antes)

    # --- Diferidos del triaje (2026-07-09) ---

    def test_session_brief_cumple_su_descripcion(self):
        # El brief promete bugs y blockers; antes solo devolvía 5 campos.
        self.manager.mutate("register_bug", {
            "module_id": "mod-test", "severity": "Mayor",
            "description": "algo roto", "affects_ac": False,
        })
        req = {"jsonrpc": "2.0", "id": 30, "method": "tools/call",
               "params": {"name": "get_session_brief", "arguments": {}}}
        resp = state_manager.handle_mcp_request(self.manager, req)
        brief = json.loads(resp["result"]["content"][0]["text"])
        self.assertIn("open_bugs", brief)
        self.assertIn("open_blockers", brief)
        self.assertIn("active_module_tier", brief)
        self.assertEqual(len(brief["open_bugs"]), 1)

    def test_register_clarification_rechaza_boolean_string_invalido(self):
        # PoC de la auditoría: blocks_closure "false" (string) se persistía y
        # se evaluaba truthy. Ahora: bool o "true"/"false" explícitos.
        with self.assertRaises(ValueError):
            self.manager.mutate("register_clarification", {
                "question": "¿?", "blocked_scope": "mod-test",
                "blocks_closure": "quizás",
            })
        self.manager.mutate("register_clarification", {
            "question": "¿bloquea?", "blocked_scope": "mod-test",
            "blocks_closure": "false",
        })
        clar = self.manager.state_data["_clarifications"][-1]
        self.assertIs(clar["blocks_closure"], False)

    def test_add_work_tier_sigue_rechazando_modulos_no_declarados(self):
        with self.assertRaises(ValueError):
            self.manager.mutate("add_work_tier", {
                "id": "modulo_fantasma", "tier": "tier_1",
                "rationale": "x", "classified_by": "test",
            })

    def test_register_decision_with_expertise_traceability(self):
        # 4.1.0 (FW-072): la decisión cita al experto/skill que la fundó y la
        # proyección de 03_arch_decisions.md lo muestra.
        self.manager.mutate("register_decision", {
            "title": "Sizing por USD absolutos",
            "context": "El filtro rápido descarta posiciones no rentables",
            "decision": "Traducir siempre % a USD según capital declarado",
            "expertise": "domain/inversion_retail.md",
        })
        dec = self.manager.state_data["_decisions"][-1]
        self.assertEqual(dec["expertise"], "domain/inversion_retail.md")
        adr_text = state_core.DECISIONS_FILE_PATH.read_text(encoding="utf-8")
        self.assertIn("Fundada en (expertise):", adr_text)
        self.assertIn("domain/inversion_retail.md", adr_text)

    def test_in_progress_to_completed_error_includes_remedy(self):
        # FW-003 BF: el atajo in_progress -> completed se rechaza con hint
        # accionable (pasar por ready_for_validation), no solo con el rechazo.
        with self.assertRaises(ValueError) as ctx:
            self.manager.mutate("update_module_status", {"id": "mod-test", "status": "completed"})
        self.assertIn("ready_for_validation", str(ctx.exception))
        self.assertIn("cierre-modulo", str(ctx.exception))

    def test_set_framework_version_validates_format(self):
        res = self.manager.mutate("set_framework_version", {"version": "3.3"})
        self.assertIn("3.3", res)
        self.assertEqual(self.manager.state_data["framework_version"], "3.3")

        with self.assertRaises(ValueError):
            self.manager.mutate("set_framework_version", {"version": "tres punto tres"})
        with self.assertRaises(ValueError):
            self.manager.mutate("set_framework_version", {})

    def test_mutate_releases_lock_on_success_and_failure(self):
        lock_path = self.tmp_root / "_project" / ".state.lock"

        self.manager.mutate("update_last_step", {"last_completed_step": "con lock"})
        self.assertFalse(lock_path.exists())

        with self.assertRaises(ValueError):
            self.manager.mutate("update_module_status", {"id": "mod-x", "status": "estado-invalido"})
        self.assertFalse(lock_path.exists())

    def test_lock_contention_times_out_with_clear_error(self):
        lock_path = self.tmp_root / "_project" / ".state.lock"
        lock_path.write_text("99999 2026-06-10T00:00:00+00:00\n", encoding="utf-8")
        try:
            with self.assertRaises(RuntimeError) as ctx:
                with state_core.state_write_lock(timeout=0.2, stale=3600):
                    pass
            self.assertIn("otra mutación en curso", str(ctx.exception))
        finally:
            lock_path.unlink()

    def test_stale_lock_is_broken_and_acquired(self):
        import os as _os
        lock_path = self.tmp_root / "_project" / ".state.lock"
        lock_path.write_text("99999 2026-06-09T00:00:00+00:00\n", encoding="utf-8")
        old = 1_000_000.0
        _os.utime(lock_path, (old, old))

        with state_core.state_write_lock(timeout=1.0, stale=60.0):
            content = lock_path.read_text(encoding="utf-8")
            self.assertIn(str(_os.getpid()), content)
        self.assertFalse(lock_path.exists())

    def test_cli_render_acquires_write_lock(self):
        """A1: el subcomando `render` del CLI ahora adquiere el write lock.
        Si hay un lock previo no-stale, render falla con timeout claro."""
        import io, contextlib
        lock_path = self.tmp_root / "_project" / ".state.lock"
        lock_path.write_text("99999 2026-06-10T00:00:00+00:00\n", encoding="utf-8")
        # C4: mismo camino de código, timeout de test — no pagar los 10s reales
        old_timeout = state_core.LOCK_TIMEOUT_SECONDS
        state_core.LOCK_TIMEOUT_SECONDS = 0.2
        self.addCleanup(setattr, state_core, "LOCK_TIMEOUT_SECONDS", old_timeout)
        old_argv = sys.argv
        sys.argv = ["state_manager.py", "render"]
        err = io.StringIO()
        try:
            with contextlib.redirect_stderr(err):
                with self.assertRaises(SystemExit) as ctx:
                    state_manager.main()
        finally:
            sys.argv = old_argv
            if lock_path.exists():
                lock_path.unlink()
        self.assertEqual(ctx.exception.code, 1)
        self.assertIn("Error:", err.getvalue())

    def test_cli_main_corrupt_state_guided_message_not_traceback(self):
        import io, contextlib
        state_core.STATE_FILE_PATH.write_text("{roto", encoding="utf-8")
        argv = ["state_manager.py", "mutate", "--action", "update_last_step", "--payload", "{}"]
        old_argv = sys.argv
        sys.argv = argv
        err = io.StringIO()
        try:
            with contextlib.redirect_stderr(err):
                with self.assertRaises(SystemExit) as ctx:
                    state_manager.main()
        finally:
            sys.argv = old_argv
        self.assertEqual(ctx.exception.code, 2)
        out = err.getvalue()
        self.assertIn("Recuperación", out)
        self.assertIn("state_log.jsonl", out)
        self.assertNotIn("Traceback (most recent call last)", out)

    def test_corrupt_state_fails_loudly_instead_of_silent_empty(self):
        state_core.STATE_FILE_PATH.write_text("{json roto", encoding="utf-8")
        with self.assertRaises(RuntimeError) as ctx:
            self.manager.load_state()
        self.assertIn("state_log.jsonl", str(ctx.exception))

    def test_missing_state_file_loads_empty(self):
        state_core.STATE_FILE_PATH.unlink()
        self.manager.load_state()
        self.assertEqual(self.manager.state_data, {})

    def test_save_state_is_atomic_and_leaves_no_tmp(self):
        self.manager.mutate("update_last_step", {"last_completed_step": "guardado atómico"})
        tmp_path = state_core.STATE_FILE_PATH.with_suffix(".json.tmp")
        self.assertFalse(tmp_path.exists())
        reloaded = json.loads(state_core.STATE_FILE_PATH.read_text(encoding="utf-8"))
        self.assertEqual(reloaded["last_completed_step"], "guardado atómico")

    def test_mutate_appends_event_log(self):
        log_path = self.tmp_root / "_project" / "state_log.jsonl"
        self.assertFalse(log_path.exists())

        self.manager.mutate("update_module_status", {"id": "mod-test", "status": "ready_for_validation"})
        self.manager.mutate("update_last_step", {"last_completed_step": "paso de prueba"})

        lines = log_path.read_text(encoding="utf-8").strip().splitlines()
        self.assertEqual(len(lines), 2)
        first = json.loads(lines[0])
        self.assertEqual(first["action"], "update_module_status")
        self.assertEqual(first["payload"]["id"], "mod-test")
        self.assertIn("ts", first)
        self.assertIn("result", first)
        second = json.loads(lines[1])
        self.assertEqual(second["action"], "update_last_step")

    def test_failed_mutation_does_not_log_event(self):
        log_path = self.tmp_root / "_project" / "state_log.jsonl"
        with self.assertRaises(ValueError):
            self.manager.mutate("update_module_status", {"id": "mod-test", "status": "estado-invalido"})
        self.assertFalse(log_path.exists())

    def test_register_and_resolve_bug(self):
        # Registrar bug Crítico (debe bloquear el módulo)
        res = self.manager.mutate("register_bug", {
            "module_id": "mod-test",
            "severity": "Crítico",
            "description": "Fallo crítico en motor",
            "affects_ac": False
        })
        self.assertIn("BUG-001 registrado", res)
        self.assertEqual(self.manager.state_data["module_statuses"]["mod-test"], "blocked")
        self.assertEqual(len(self.manager.state_data["_bugs"]), 1)
        self.assertEqual(self.manager.state_data["_bugs"][0]["id"], "BUG-001")
        self.assertEqual(self.manager.state_data["_bugs"][0]["status"], "Abierto")

        # Resolver bug
        res_resolve = self.manager.mutate("resolve_bug", {
            "id": "BUG-001",
            "fix": "Corregido buffer overflow",
            "affected_files": ["src/motor.py"]
        })
        self.assertIn("BUG-001 resuelto", res_resolve)
        self.assertEqual(self.manager.state_data["_bugs"][0]["status"], "Resuelto")
        self.assertEqual(self.manager.state_data["module_statuses"]["mod-test"], "in_progress") # desbloqueado

    def _leer_estado(self):
        with open(state_core.STATE_FILE_PATH, encoding="utf-8") as f:
            return json.load(f)

    def test_amend_bug_corrects_description_and_keeps_the_original(self):
        """FW-122: un dato erróneo en un bug registrado debe poder corregirse
        sin borrar el rastro de lo que decía antes."""
        self.manager.mutate("register_bug", {
            "module_id": "mod-test", "severity": "Mayor",
            "description": "la cobertura cayó a 23 tests",
        })
        self.manager.mutate("amend_bug", {
            "id": "BUG-001",
            "description": "la cobertura cayó a 12 tests",
            "reason": "la cifra 23 vino de un subagente y no se reprodujo; el conteo real es 12",
        })
        bug = self._leer_estado()["_bugs"][0]
        self.assertEqual(bug["description"], "la cobertura cayó a 12 tests")
        self.assertEqual(len(bug["amendments"]), 1)
        self.assertEqual(bug["amendments"][0]["previous"]["description"],
                         "la cobertura cayó a 23 tests")

    def test_amend_bug_refuses_to_change_severity_or_status(self):
        """No se afloja: los campos que mueven gates NO son enmendables."""
        self.manager.mutate("register_bug", {
            "module_id": "mod-test", "severity": "Menor", "description": "d",
        })
        for campo, valor in (("severity", "Crítico"), ("status", "Resuelto"),
                             ("affects_ac", True), ("module_id", "M2")):
            with self.assertRaises(ValueError) as ctx:
                self.manager.mutate("amend_bug", {
                    "id": "BUG-001", campo: valor,
                    "reason": "intento de mover la semantica del gate por la puerta de atras",
                })
            self.assertIn(campo, str(ctx.exception))

    def test_amend_bug_requires_a_substantive_reason(self):
        self.manager.mutate("register_bug", {
            "module_id": "mod-test", "severity": "Menor", "description": "d",
        })
        with self.assertRaises(ValueError):
            self.manager.mutate("amend_bug", {
                "id": "BUG-001", "description": "otra", "reason": "typo",
            })

    def test_register_and_resolve_clarification(self):
        # Registrar clarificación bloqueante
        res = self.manager.mutate("register_clarification", {
            "blocked_scope": "mod-test",
            "question": "¿Qué motor de BD usar?",
            "title": "Decisión BD",
            "blocks_start": True
        })
        self.assertIn("CLAR-001 registrada", res)
        self.assertEqual(self.manager.state_data["module_statuses"]["mod-test"], "blocked")

        # Resolver clarificación
        res_resolve = self.manager.mutate("resolve_clarification", {
            "id": "CLAR-001",
            "answer": "Usaremos SQLite."
        })
        self.assertIn("CLAR-001 resuelta", res_resolve)
        self.assertEqual(self.manager.state_data["module_statuses"]["mod-test"], "in_progress")

    def test_register_decision(self):
        res = self.manager.mutate("register_decision", {
            "title": "Migración a Python 3.12",
            "context": "Python 3.10 está por expirar",
            "decision": "Migrar entorno",
            "module_id": "infra"
        })
        self.assertIn("ADR-001 registrada", res)
        self.assertEqual(len(self.manager.state_data["_decisions"]), 1)
        self.assertEqual(self.manager.state_data["_decisions"][0]["id"], "ADR-001")

    def test_register_decision_accepts_umbrella_module_list(self):
        # TL FW-019 / BOTÓN FW-115/FW-113: ADR paraguas para una iniciativa
        # multi-módulo -- module_id como lista de módulos.
        res = self.manager.mutate("register_decision", {
            "title": "Arquitectura de la iniciativa de modularización",
            "context": "varios módulos comparten el mismo rediseño",
            "decision": "unificar bajo un solo ADR",
            "module_id": ["mod-test", "mod-pending"],
        })
        self.assertIn("ADR-001 registrada", res)
        stored = self.manager.state_data["_decisions"][0]["module_id"]
        self.assertEqual(stored, ["mod-test", "mod-pending"])

    def test_register_decision_rejects_empty_module_list(self):
        with self.assertRaises(ValueError):
            self.manager.mutate("register_decision", {
                "title": "t", "context": "c", "decision": "d", "module_id": [],
            })

    def test_register_decision_rejects_non_string_non_list_module_id(self):
        with self.assertRaises(ValueError):
            self.manager.mutate("register_decision", {
                "title": "t", "context": "c", "decision": "d", "module_id": 42,
            })

    def test_register_decision_module_id_survives_opencode_transport_wrapping(self):
        # FW-103: el mismo patrón de corrupción del transport OpenCode que
        # afecta adr_refs/derived_from ahora también expone module_id (nuevo
        # campo lista). {"item": [...]} debe desenvolverse antes de validar.
        res = self.manager.mutate("register_decision", {
            "title": "t", "context": "c", "decision": "d",
            "module_id": {"item": ["mod-test", "mod-pending"]},
        })
        self.assertIn("ADR-001 registrada", res)
        self.assertEqual(
            self.manager.state_data["_decisions"][0]["module_id"],
            ["mod-test", "mod-pending"],
        )

    def test_register_decision_rejects_an_explicit_id_instead_of_ignoring_it(self):
        """FW-125: el fallo era SILENCIOSO — devolvía éxito con otro id."""
        self.manager.mutate("register_decision", {
            "title": "t", "context": "c", "decision": "d", "module_id": "M1",
        })
        with self.assertRaises(ValueError) as ctx:
            self.manager.mutate("register_decision", {
                "id": "ADR-001", "title": "t2", "context": "c", "decision": "d",
                "module_id": "mod-test",
            })
        self.assertIn("supersede_decision", str(ctx.exception))

    def test_register_decision_accepts_the_id_it_would_have_autoassigned(self):
        """Ronda de revisión final (hallazgo 1): un id CONSISTENTE (el mismo
        que se autoasignaría) no debe rechazarse — así es como se ve un
        replay estricto fiel de `state_log.jsonl` histórico, donde el id
        registrado coincidía por casualidad (o por bug) con el autoasignado."""
        res = self.manager.mutate("register_decision", {
            "id": "ADR-001", "title": "t", "context": "c", "decision": "d",
            "module_id": "M1",
        })
        self.assertIn("ADR-001 registrada", res)
        self.assertEqual(self.manager.state_data["_decisions"][0]["id"], "ADR-001")

    def test_register_decision_still_rejects_an_inconsistent_id(self):
        """No se afloja: un id que NO es el que se autoasignaría sigue
        rechazándose — ese es el caso peligroso real (hallazgo 1)."""
        self.manager.mutate("register_decision", {
            "title": "t", "context": "c", "decision": "d", "module_id": "M1",
        })
        with self.assertRaises(ValueError) as ctx:
            self.manager.mutate("register_decision", {
                "id": "ADR-005", "title": "t2", "context": "c", "decision": "d",
                "module_id": "mod-test",
            })
        self.assertIn("supersede_decision", str(ctx.exception))

    def test_supersede_decision_marks_the_old_adr_and_points_to_the_new_one(self):
        for titulo in ("mecanismo v1", "mecanismo v2"):
            self.manager.mutate("register_decision", {
                "title": titulo, "context": "c", "decision": "d", "module_id": "mod-test",
            })
        self.manager.mutate("supersede_decision", {
            "id": "ADR-001", "superseded_by": "ADR-002",
            "reason": "el mecanismo v1 se sustituyo entero por criterios absolutos",
        })
        adrs = {d["id"]: d for d in self._leer_estado()["_decisions"]}
        self.assertEqual(adrs["ADR-001"]["status"], "Superseded")
        self.assertEqual(adrs["ADR-001"]["superseded_by"], "ADR-002")
        self.assertEqual(adrs["ADR-002"]["status"], "Aceptada")

    def test_supersede_decision_rejects_unknown_or_self_references(self):
        self.manager.mutate("register_decision", {
            "title": "t", "context": "c", "decision": "d", "module_id": "M1",
        })
        casos = [
            {"id": "ADR-999", "superseded_by": "ADR-001"},   # objetivo inexistente
            {"id": "ADR-001", "superseded_by": "ADR-999"},   # reemplazo inexistente
            {"id": "ADR-001", "superseded_by": "ADR-001"},   # a sí misma
        ]
        for caso in casos:
            with self.assertRaises(ValueError):
                self.manager.mutate("supersede_decision", dict(
                    caso, reason="motivo suficientemente largo para pasar la validacion"))

    def test_supersede_decision_is_not_idempotent_by_accident(self):
        """Supersedir dos veces debe fallar ruidosamente, no sobrescribir."""
        for _ in range(2):
            self.manager.mutate("register_decision", {
                "title": "t", "context": "c", "decision": "d", "module_id": "mod-test",
            })
        payload = {"id": "ADR-001", "superseded_by": "ADR-002",
                   "reason": "el mecanismo v1 se sustituyo entero por criterios absolutos"}
        self.manager.mutate("supersede_decision", payload)
        with self.assertRaises(ValueError) as ctx:
            self.manager.mutate("supersede_decision", payload)
        self.assertIn("ya está superseded", str(ctx.exception))

    def test_render_projections(self):
        # Escribir criterios de aceptación en 08_module_acceptance.md simulado
        (self.tmp_root / "_project" / "08_module_acceptance.md").write_text(
            "## Módulo N — mod-test\nEstado: 🔄 En progreso\n\n### Criterios\n- [ ] AC1\n",
            encoding="utf-8"
        )

        # Mutar para disparar renderizado
        self.manager.mutate("update_last_step", {"last_completed_step": "Test projection"})

        # Verificar que existen los archivos
        self.assertTrue(state_core.CORE_FILE_PATH.exists())
        self.assertTrue(state_core.BUGS_FILE_PATH.exists())
        self.assertTrue(state_core.CLARIFICATIONS_FILE_PATH.exists())
        self.assertTrue(state_core.DECISIONS_FILE_PATH.exists())

        core_text = state_core.CORE_FILE_PATH.read_text(encoding="utf-8")
        self.assertIn("<!-- GENERADO AUTOMÁTICAMENTE - NO EDITAR A MANO -->", core_text)
        self.assertIn("mod-test", core_text)
        self.assertIn("- [ ] AC1", core_text)


    def test_validator_requires_public_interfaces(self):
        bad = dict(self.base_state)
        bad["contracts"] = {}  # missing public_interfaces
        with self.assertRaises(ValueError):
            self.manager.validate_state(bad)

    def test_validator_checks_bug_records(self):
        bad = dict(self.base_state)
        bad["_bugs"] = [{"id": "BUG-001", "module_id": "m", "severity": "X",
                         "description": "d", "status": "Abierto"}]
        with self.assertRaises(ValueError):
            self.manager.validate_state(bad)

    def test_validator_checks_closure_rejections(self):
        bad = dict(self.base_state)
        bad["closure_rejections"] = [{"module_id": "m"}]  # missing reason/timestamp
        with self.assertRaises(ValueError):
            self.manager.validate_state(bad)


    def test_set_session_pointer_renders_in_core(self):
        self.manager.mutate("set_session_pointer", {
            "next_action": "Implementar login",
            "next_command": "/ciclo",
            "active_module_description": "Autenticación de usuarios",
            "next_module": "mod-pending",
        })
        sess = self.manager.state_data["session"]
        self.assertEqual(sess["next_action"], "Implementar login")
        core = state_core.CORE_FILE_PATH.read_text(encoding="utf-8")
        self.assertIn("Siguiente: /ciclo Implementar login", core)
        self.assertIn("Implementar login", core)
        self.assertIn("Autenticación de usuarios", core)

    def test_set_session_pointer_rejects_prompt_embedded_in_next_command(self):
        with self.assertRaisesRegex(ValueError, "next_command"):
            self.manager.mutate("set_session_pointer", {
                "next_action": "Implementar login",
                "next_command": "/tdd — empezar con prueba",
            })

    def test_set_session_pointer_rejects_internal_next_command(self):
        with self.assertRaisesRegex(ValueError, "builder-facing"):
            self.manager.mutate("set_session_pointer", {
                "next_action": "Detallar la HU antes de implementar.",
                "next_command": "/enriquecer-ticket",
            })

    def test_set_session_pointer_accepts_ciclo_fase(self):
        self.manager.mutate("set_session_pointer", {
            "next_action": "Validar y cerrar el hito de fase.",
            "next_command": "/ciclo fase",
        })
        core = state_core.CORE_FILE_PATH.read_text(encoding="utf-8")
        self.assertIn(
            "Siguiente: /ciclo fase Validar y cerrar el hito de fase.",
            core,
        )

    def test_set_session_pointer_accepts_ciclo_fase_parallel(self):
        self.manager.mutate("set_session_pointer", {
            "next_action": "Validar los módulos independientes del hito de fase.",
            "next_command": "/ciclo fase --paralelo",
        })
        core = state_core.CORE_FILE_PATH.read_text(encoding="utf-8")
        self.assertIn(
            "Siguiente: /ciclo fase --paralelo Validar los módulos independientes del hito de fase.",
            core,
        )

    def test_set_session_pointer_rejects_unknown_key(self):
        # FW-112 (Botón, known-bad): una clave mal escrita (p. ej.
        # "next_steps" en vez de "next_action") se ignoraba en silencio y el
        # caller creía que el puntero se había actualizado.
        with self.assertRaises(ValueError):
            self.manager.mutate("set_session_pointer", {"next_steps": "algo"})

    def test_set_session_pointer_reorders_in_progress_module_to_head(self):
        """N10 / FW-264: set_session_pointer mueve next_module a la posición 0 de in_progress."""
        specs_dir = self.tmp_root / ".agent" / "docs" / "specs"
        specs_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy(
            ROOT / ".agent" / "docs" / "specs" / "progressive_context_obligations.json",
            specs_dir / "progressive_context_obligations.json",
        )

        # Configurar 3 módulos: mod-test (activo), mod-second y mod-third
        self.manager.mutate("add_module", {"id": "mod-second"})
        self.manager.mutate("update_module_status", {"id": "mod-second", "status": "in_progress"})
        self.manager.mutate("add_module", {"id": "mod-third"})
        self.manager.mutate("update_module_status", {"id": "mod-third", "status": "in_progress"})

        # Cerrar mod-test de forma legítima
        self._preparar_modulo_cerrable("mod-test")
        self.manager.mutate("complete_module", {"id": "mod-test"})

        # En curso quedan mod-second (en pos 0) y mod-third (en pos 1)
        self.assertEqual(
            self.manager.state_data["modules"]["in_progress"],
            ["mod-second", "mod-third"],
        )

        # Fijar puntero con next_module = mod-third
        self.manager.mutate("set_session_pointer", {
            "next_action": "Implementar mod-third",
            "next_command": "/ciclo",
            "next_module": "mod-third",
        })

        # mod-third debe haberse movido a la posición 0
        self.assertEqual(
            self.manager.state_data["modules"]["in_progress"],
            ["mod-third", "mod-second"],
        )

        # La vista startup debe mostrar mod-third como objetivo
        ctx = state_manager.get_work_context(self.manager, view="startup")
        self.assertEqual(ctx["module_id"], "mod-third")
        self.assertEqual(ctx["data"]["summary"]["target_id"], "mod-third")

    def test_set_session_pointer_replay_tolerance(self):
        """FW-186 / D7: Replay tolera set_session_pointer tanto con módulo en in_progress como ausente."""
        self.manager.state_data["modules"]["in_progress"] = ["mod-a", "mod-b"]
        self.manager._is_replay = True

        # Replay con next_module presente en in_progress
        self.manager._apply_event("set_session_pointer", {
            "next_action": "Accion",
            "next_command": "/ciclo",
            "next_module": "mod-b",
        }, "2026-09-19T00:00:00Z")
        self.assertEqual(self.manager.state_data["modules"]["in_progress"], ["mod-b", "mod-a"])

        # Replay con next_module ausente en in_progress (ej. pendiente o legado)
        self.manager._apply_event("set_session_pointer", {
            "next_action": "Accion",
            "next_command": "/ciclo",
            "next_module": "mod-pending",
        }, "2026-09-19T00:00:00Z")
        self.assertEqual(self.manager.state_data["modules"]["in_progress"], ["mod-b", "mod-a"])
        self.assertEqual(self.manager.state_data["session"]["next_module"], "mod-pending")

    def test_retired_codex_pointer_action_is_rejected_by_public_manager(self):
        with self.assertRaisesRegex(ValueError, "LEGACY_MIGRATION_REQUIRED"):
            self.manager.mutate(
                "set_codex_execution_pointer",
                {"pointer": None},
            )


    # ---- Action 1: classify_module_spec ----

    def test_classify_module_spec_ok(self):
        self.manager.mutate("classify_module_spec", {
            "id": "mod-test", "data_model": "required", "api_contract": "n/a",
            "justification": "Módulo sin HTTP", "classified_by": "scope-siguiente",
        })
        spec = self.manager.state_data["module_specs"]["mod-test"]
        self.assertEqual(spec["data_model"], "required")
        self.assertEqual(spec["api_contract"], "n/a")
        self.assertEqual(spec["classified_by"], "scope-siguiente")

    def test_classify_module_spec_requires_justification_for_na(self):
        with self.assertRaises(ValueError):
            self.manager.mutate("classify_module_spec", {
                "id": "mod-test", "data_model": "n/a", "api_contract": "required",
                "classified_by": "scope-siguiente",
            })

    def test_classify_module_spec_rejects_bad_classifier(self):
        with self.assertRaises(ValueError):
            self.manager.mutate("classify_module_spec", {
                "id": "mod-test", "data_model": "required", "api_contract": "required",
                "classified_by": "bogus",
            })

    def test_classify_module_spec_error_lists_valid_classified_by_values(self):
        # FW-111/FW-102/GW-045: el error debe listar los valores válidos, no
        # solo repetir el valor inválido recibido.
        with self.assertRaisesRegex(
            ValueError, r"scope-siguiente.*enriquecer-ticket.*migrar-proyecto.*manual"
        ):
            self.manager.mutate("classify_module_spec", {
                "id": "mod-test", "data_model": "required", "api_contract": "required",
                "classified_by": "bogus",
            })

    def test_classify_module_spec_error_lists_valid_data_model_values(self):
        with self.assertRaisesRegex(ValueError, r"required.*n/a.*done"):
            self.manager.mutate("classify_module_spec", {
                "id": "mod-test", "data_model": "bogus", "api_contract": "required",
                "classified_by": "scope-siguiente",
            })

    # ---- Action 2: add_closure_rejection ----

    def test_add_closure_rejection_ok(self):
        self.manager.mutate("add_closure_rejection", {
            "module_id": "mod-test", "reason": "ac_incomplete",
        })
        rejs = self.manager.state_data["closure_rejections"]
        self.assertEqual(rejs[-1]["module_id"], "mod-test")
        self.assertEqual(rejs[-1]["reason"], "ac_incomplete")
        self.assertIn("timestamp", rejs[-1])

    def test_add_closure_rejection_bad_reason(self):
        with self.assertRaises(ValueError):
            self.manager.mutate("add_closure_rejection", {
                "module_id": "mod-test", "reason": "nope",
            })

    # ---- Action 3: set_phase, set_execution_profile, add_active_mcp ----

    def test_set_phase(self):
        self.manager.mutate("set_phase", {"current_phase": "phase_02", "current_step": "inicio"})
        self.assertEqual(self.manager.state_data["current_phase"], "phase_02")
        self.assertEqual(self.manager.state_data["current_step"], "inicio")

    def test_set_phase_rejects_bad_pattern(self):
        with self.assertRaises(ValueError):
            self.manager.mutate("set_phase", {"current_phase": "fase2"})

    def test_set_phase_allows_arbitrary_skip_with_warning(self):
        """Regresión para A5: el motor acepta saltos de fase pero emite WARN
        en el mensaje de retorno para que el workflow `/nueva-fase` pueda
        detectarlo y aplicar el criterio de salida de la fase previa.

        El motor primitivo sigue siendo permisivo (cambios hacia atrás y
        skips son válidos); la capa de workflow es la que debe gate-ar.
        """
        result = self.manager.mutate(
            "set_phase", {"current_phase": "phase_99", "current_step": "x"}
        )
        self.assertEqual(self.manager.state_data["current_phase"], "phase_99")
        self.assertIn("WARN:", result, f"se esperaba WARN: en el mensaje, obtuve: {result!r}")
        self.assertIn("salto de fase phase_01 → phase_99", result)

    def test_set_phase_adjacent_does_not_warn(self):
        """Sanity: phase_01 → phase_02 (adyacente) no genera WARN."""
        result = self.manager.mutate(
            "set_phase", {"current_phase": "phase_02", "current_step": "y"}
        )
        self.assertNotIn("WARN:", result)
        self.assertIn("phase_02", result)

    def test_add_work_tier_rejects_unknown_module(self):
        """Regresión para A4: add_work_tier rechaza módulos no declarados.

        Antes: un typo en `unit` (ej. `mod-ghost` cuando el módulo real es
        `mod-test`) se aceptaba silenciosamente y reventaba al cierre con un
        mensaje confuso. Ahora: la mutación falla con un ValueError claro
        que lista los módulos conocidos.
        """
        before = dict(self.manager.state_data.get("work_tiers", {}))
        self.assertNotIn("mod-ghost", before)
        with self.assertRaises(ValueError) as ctx:
            self.manager.mutate("add_work_tier", {
                "id": "mod-ghost",
                "tier": "tier_1",
                "rationale": "typo del dev, no declarado en modules",
            })
        self.assertIn("mod-ghost", str(ctx.exception))
        self.assertIn("no es un módulo declarado", str(ctx.exception))
        self.assertEqual(self.manager.state_data.get("work_tiers", {}), before)

    def test_add_work_tier_accepts_declared_module(self):
        """Sanity: el gate de A4 no rompe el caso feliz — mod-test está
        declarado en el state base del setUp, así que el add_work_tier
        para mod-test debe funcionar."""
        if "mod-test" in self.manager.state_data.get("work_tiers", {}):
            del self.manager.state_data["work_tiers"]["mod-test"]
        self.manager.mutate("add_work_tier", {
            "id": "mod-test",
            "tier": "tier_1",
            "rationale": "re-asignado en test",
        })
        self.assertEqual(
            self.manager.state_data["work_tiers"]["mod-test"]["tier"], "tier_1"
        )

    def test_set_execution_profile(self):
        self.manager.mutate("set_execution_profile", {
            "capability_tier": "limited", "model": "claude-haiku-4-5", "rationale": "ejecutor débil",
        })
        ep = self.manager.state_data["execution_profile"]
        self.assertEqual(ep["capability_tier"], "limited")

    def test_set_execution_profile_bad_tier(self):
        with self.assertRaises(ValueError):
            self.manager.mutate("set_execution_profile", {"capability_tier": "ultra"})

    def test_add_active_mcp(self):
        self.manager.mutate("add_active_mcp", {"name": "playwright"})
        self.assertIn("playwright", self.manager.state_data["active_mcps"])
        self.manager.mutate("add_active_mcp", {"name": "playwright"})
        self.assertEqual(self.manager.state_data["active_mcps"].count("playwright"), 1)

    # ---- Action 4: defer_bug_to_tech_debt ----

    def test_defer_bug_to_tech_debt(self):
        self.manager.mutate("register_bug", {
            "module_id": "mod-test", "severity": "Menor", "description": "cosmético",
        })
        self.manager.mutate("defer_bug_to_tech_debt", {"id": "BUG-001"})
        bug = self.manager.state_data["_bugs"][0]
        self.assertEqual(bug["status"], "Deuda Técnica")

    def test_defer_unblocks_when_no_blockers_left(self):
        self.manager.mutate("register_bug", {
            "module_id": "mod-test", "severity": "Crítico", "description": "fatal",
        })
        self.assertEqual(self.manager.state_data["module_statuses"]["mod-test"], "blocked")
        self.manager.mutate("defer_bug_to_tech_debt", {"id": "BUG-001"})
        self.assertEqual(self.manager.state_data["module_statuses"]["mod-test"], "in_progress")

    def test_session_brief_projection(self):
        import json as _json
        self.manager.mutate("update_last_step", {"last_completed_step": "Hito X"})
        brief_path = state_core.SESSION_BRIEF_PATH
        self.assertTrue(brief_path.exists())
        data = _json.loads(brief_path.read_text(encoding="utf-8"))
        self.assertEqual(data["last_completed_step"], "Hito X")
        self.assertEqual(data["active_module"], "mod-test")

    def test_mutate_render_failure_aborts_cleanly(self):
        """Regresión para A6: si render_projections falla, la mutación debe abortar
        sin persistir el JSON ni escribir el evento en el log.

        El orden en `_mutate_locked` valida y renderiza antes de publicar el
        estado y la bitácora. Un fallo de render o de sellado debe restaurar el
        snapshot completo; la mutación es atómica con sus proyecciones.
        """
        import projections as projections_module

        json_path = state_core.STATE_FILE_PATH
        log_path = json_path.parent / "state_log.jsonl"
        before_json = json_path.read_text(encoding="utf-8")
        before_log_exists = log_path.exists()
        before_log = log_path.read_text(encoding="utf-8") if before_log_exists else ""

        def boom(_state):
            raise RuntimeError("render failure forzado por test")
        original_render = projections_module.render
        projections_module.render = boom
        try:
            with self.assertRaises(RuntimeError):
                self.manager.mutate(
                    "set_phase", {"current_phase": "phase_77", "current_step": "boom"}
                )
        finally:
            projections_module.render = original_render

        after_json = json_path.read_text(encoding="utf-8")
        after_log = log_path.read_text(encoding="utf-8") if log_path.exists() else ""
        self.assertEqual(
            after_json, before_json,
            "el JSON quedó actualizado a pesar del fallo de render. Contrato "
            "esperado: la mutación aborta sin persistir.",
        )
        self.assertEqual(
            after_log, before_log,
            "el log se escribió a pesar del fallo de render. Contrato esperado: "
            "log y JSON se escriben solo si el render tuvo éxito.",
        )


if __name__ == "__main__":
    unittest.main()
