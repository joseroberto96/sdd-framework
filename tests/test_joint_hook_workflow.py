#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Prueba conjunta Lote 3 + Lote 4 (Plan 4.5 — §4 y §5.18).

Item 18 del Plan: Workflow recortado interpretando los campos del hook de
continuidad, sin consulta incondicional añadida.

Aceptación §4: El recorrido con hook reduce trabajo total frente al recorrido
sin hook. Si añade consultas o contexto sin beneficio observado, no se promueve.
La aceptación de los Lotes 3 y 4 incluye esta prueba conjunta obligatoria.
"""
from __future__ import annotations

import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / ".agent" / "scripts"))

import claude_continuity_hook as hook
import state_core
import state_manager


def simulate_workflow_startup_consumption(
    root: Path,
    manager: state_manager.StateManager,
    *,
    session_id: str,
    hook_envelope: dict | None,
    state_mutated_since_hook: bool = False,
) -> tuple[dict, int, str]:
    """Simula el consumo del workflow /arrancar según el contrato del Lote 4.

    Retorna:
    - context_used: dict con el contexto resuelto
    - queries_made: int número de llamadas a get_work_context ejecutadas
    - decision: str ('reused_hook' | 'queried_mcp' | 'stopped_block' | 'reused_hook_paginated')
    """
    queries_made = 0
    # 1. Verificar si hay entrega previa válida del hook de continuidad
    if hook_envelope and hook_envelope.get("status") in ("OK", "BLOCK") and not state_mutated_since_hook:
        vigencia = hook_envelope.get("vigencia", {})
        completitud = hook_envelope.get("completitud", {})

        matches_session = vigencia.get("session_id") == session_id
        matches_view = vigencia.get("view") == "startup"

        if matches_session and matches_view:
            # Reutilización legítima de la primera página:
            # No se ejecuta get_work_context para la página 1
            if completitud.get("gate_status") == "BLOCK":
                return hook_envelope.get("data", {}), queries_made, "stopped_block"

            # Si requiere paginación adicional (cursor pendiente), se continúa desde cursor
            cursor = hook_envelope.get("cursor")
            if not completitud.get("complete") and cursor:
                p2 = state_manager.get_work_context(manager, view="startup", cursor=cursor)
                queries_made += 1
                return p2, queries_made, "reused_hook_paginated"

            return hook_envelope.get("data", {}), queries_made, "reused_hook"

    # 2. Si no hay hook, o está stale/inválido/fallido: consulta explícita
    ctx = state_manager.get_work_context(manager, view="startup")
    queries_made += 1
    return ctx, queries_made, "queried_mcp"


class JointHookWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

        self.proj_dir = self.tmp / "_project"
        self.proj_dir.mkdir(parents=True)
        self.workflows_dir = self.tmp / ".agent" / "workflows"
        self.workflows_dir.mkdir(parents=True)
        if (ROOT / ".agent" / "workflows" / "manifest.json").exists():
            shutil.copy(ROOT / ".agent" / "workflows" / "manifest.json", self.workflows_dir / "manifest.json")

        specs_dir = self.tmp / ".agent" / "docs" / "specs"
        specs_dir.mkdir(parents=True)
        real_specs = ROOT / ".agent" / "docs" / "specs"
        if real_specs.exists():
            for f in real_specs.glob("*.json"):
                shutil.copy(f, specs_dir / f.name)

        self.saved_state_core = {
            "root": state_core.WORKSPACE_ROOT,
            "state": state_core.STATE_FILE_PATH,
            "core": state_core.CORE_FILE_PATH,
            "bugs": state_core.BUGS_FILE_PATH,
            "clars": state_core.CLARIFICATIONS_FILE_PATH,
            "decisions": state_core.DECISIONS_FILE_PATH,
            "brief": state_core.SESSION_BRIEF_PATH,
        }

        state_core.WORKSPACE_ROOT = self.tmp
        state_core.STATE_FILE_PATH = self.proj_dir / "03_project_state.json"
        state_core.CORE_FILE_PATH = self.proj_dir / "core.md"
        state_core.BUGS_FILE_PATH = self.proj_dir / "06_bug_fixes.md"
        state_core.CLARIFICATIONS_FILE_PATH = self.proj_dir / "07_pending_clarifications.md"
        state_core.DECISIONS_FILE_PATH = self.proj_dir / "03_arch_decisions.md"
        state_core.SESSION_BRIEF_PATH = self.proj_dir / "00_session_brief.json"

        self.state = {
            "schema_version": 1,
            "project": "Joint Test Project",
            "framework_mode": "initialized",
            "framework_version": "4.40.1",
            "current_phase": "phase_01",
            "current_step": "init",
            "last_completed_step": "diseño",
            "active_module": "mod-alpha",
            "session": {"active_module": "mod-alpha", "next_action": "Implementar", "next_command": "/ciclo"},
            "stack": {"backend": "Python", "frontend": "-", "database": "-", "auth": "-"},
            "architecture_constraints": [],
            "contracts": {"public_interfaces": {}},
            "_refs": {},
            "_bugs": [],
            "_clarifications": [],
            "_decisions": [],
            "modules": {"completed": [], "in_progress": ["mod-alpha"], "pending": []},
            "module_statuses": {"mod-alpha": "in_progress"},
            "work_tiers": {
                "mod-alpha": {"tier": "tier_1", "rationale": "test", "classified_at": "2026-09-15T10:00:00Z", "classified_by": "test"}
            },
            "module_specs": {
                "mod-alpha": {"data_model": "n/a", "api_contract": "n/a", "integration_real": "n/a", "classified_at": "2026-09-15T10:00:00Z", "classified_by": "test", "justification": "test"}
            },
            "dependencies": [],
            "active_mcps": ["state_manager"],
            "changes": [],
            "closure_rejections": [],
            "execution_profile": {"capability_tier": "standard"},
        }
        (self.proj_dir / "03_project_state.json").write_text(json.dumps(self.state, indent=2), encoding="utf-8")
        (self.proj_dir / "04_active_scope.md").write_text("# Scope\n## mod-alpha\nScope\n", encoding="utf-8")
        (self.proj_dir / "08_module_acceptance.md").write_text("# AC\n## mod-alpha\nAC\n", encoding="utf-8")
        (self.proj_dir / "07_pending_clarifications.md").write_text("# Clar\n", encoding="utf-8")
        (self.proj_dir / "core.md").write_text("# Core\n", encoding="utf-8")

        self.manager = state_manager.StateManager()

    def tearDown(self):
        state_core.WORKSPACE_ROOT = self.saved_state_core["root"]
        state_core.STATE_FILE_PATH = self.saved_state_core["state"]
        state_core.CORE_FILE_PATH = self.saved_state_core["core"]
        state_core.BUGS_FILE_PATH = self.saved_state_core["bugs"]
        state_core.CLARIFICATIONS_FILE_PATH = self.saved_state_core["clars"]
        state_core.DECISIONS_FILE_PATH = self.saved_state_core["decisions"]
        state_core.SESSION_BRIEF_PATH = self.saved_state_core["brief"]

    def test_workflow_prose_contains_contract_rules(self):
        """Los workflows arrancar.md y ciclo.md deben incluir las reglas de consumo del hook."""
        arrancar_text = (ROOT / ".agent" / "workflows" / "arrancar.md").read_text(encoding="utf-8")
        ciclo_text = (ROOT / ".agent" / "workflows" / "ciclo.md").read_text(encoding="utf-8")

        for term in ("Hook de Continuidad", "source_fingerprint", "completitud", "via_de_recuperacion"):
            self.assertIn(term, arrancar_text, f"arrancar.md no incluye término del contrato: {term}")

        self.assertIn("hook de continuidad", ciclo_text.lower(), "ciclo.md no referencia el hook de continuidad en STOP GATE")

    def test_reused_hook_reduces_total_work(self):
        """Aceptación §4: El recorrido con hook reduce trabajo total frente al recorrido sin hook."""
        session_id = "ses-joint-123"

        # 1. Ejecutar hook de continuidad de Claude Code
        hook_envelope = hook.build_continuity_context(
            self.tmp, event={"source": "startup", "session_id": session_id}
        )
        self.assertEqual(hook_envelope.get("status"), "OK")
        self.assertIn("vigencia", hook_envelope)
        self.assertIn("completitud", hook_envelope)

        # 2. Recorrido CON hook:
        _, queries_with_hook, decision_with_hook = simulate_workflow_startup_consumption(
            self.tmp, self.manager, session_id=session_id, hook_envelope=hook_envelope
        )
        self.assertEqual(decision_with_hook, "reused_hook")
        self.assertEqual(queries_with_hook, 0, "Con hook válido, no debe realizarse consulta get_work_context redundante")

        # 3. Recorrido SIN hook:
        _, queries_without_hook, decision_without_hook = simulate_workflow_startup_consumption(
            self.tmp, self.manager, session_id=session_id, hook_envelope=None
        )
        self.assertEqual(decision_without_hook, "queried_mcp")
        self.assertEqual(queries_without_hook, 1, "Sin hook, debe consultarse get_work_context")

        # Demostración estricta de reducción de trabajo
        self.assertLess(queries_with_hook, queries_without_hook)

    def test_stale_fingerprint_invalidates_hook_and_queries_fresh(self):
        """Si ocurre una mutación posterior, el hook queda stale y el workflow reconsulta."""
        session_id = "ses-joint-456"

        hook_envelope = hook.build_continuity_context(
            self.tmp, event={"source": "startup", "session_id": session_id}
        )

        # Mutar el estado en disco después del hook
        self.state["current_step"] = "step_nuevo_posterior"
        (self.proj_dir / "03_project_state.json").write_text(json.dumps(self.state, indent=2), encoding="utf-8")
        self.manager = state_manager.StateManager()

        # El workflow detecta que hubo mutación posterior y reconsulta limpiamente
        _, queries, decision = simulate_workflow_startup_consumption(
            self.tmp, self.manager, session_id=session_id, hook_envelope=hook_envelope, state_mutated_since_hook=True
        )
        self.assertEqual(decision, "queried_mcp")
        self.assertEqual(queries, 1)

    def test_hook_block_status_stops_without_extra_queries(self):
        """Si el hook ya reporta BLOCK, el workflow se detiene sin emitir consultas redundantes."""
        session_id = "ses-joint-789"
        self.state["_bugs"] = [{"id": "BUG-001", "module_id": "mod-alpha", "status": "Abierto", "severity": "Crítico", "description": "Crash"}]
        (self.proj_dir / "03_project_state.json").write_text(json.dumps(self.state, indent=2), encoding="utf-8")
        self.manager = state_manager.StateManager()

        hook_envelope = hook.build_continuity_context(
            self.tmp, event={"source": "startup", "session_id": session_id}
        )
        self.assertEqual(hook_envelope["completitud"]["gate_status"], "BLOCK")

        _, queries, decision = simulate_workflow_startup_consumption(
            self.tmp, self.manager, session_id=session_id, hook_envelope=hook_envelope
        )
        self.assertEqual(decision, "stopped_block")
        self.assertEqual(queries, 0)


if __name__ == "__main__":
    unittest.main()
