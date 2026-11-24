"""Tests para N3 y N4: bloqueos y criterio de parada en el motor (L5).

Verifica:
- N3: Rechazo de update_module_status al salir de blocked con bloqueantes vivos,
      nombrando los IDs y la vía de resolución.
- N4 (P4c): Rechazo de arrancar módulo (not_started -> in_progress) con >= 2 closure_rejections,
      salvo si stabilization: true.
- N4 (P5b): Rechazo de arrancar módulo con bug crítico abierto, salvo stabilization: true.
- Tolerancia estricta de validate_state para module_specs: solo stabilization: bool aislado.
- Preservación en replay (D7): _is_replay tolera transiciones históricas.
"""

import copy
import sys
import unittest
from pathlib import Path
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / ".agent" / "scripts"))

from state_core import StateManager


def _minimal_valid_state() -> dict:
    return {
        "project": "Test Project",
        "framework_mode": "initialized",
        "framework_version": "5.1.1",
        "current_phase": "phase_01",
        "current_step": "step_01",
        "last_completed_step": "Init",
        "stack": {
            "backend": "python",
            "frontend": "none",
            "database": "none",
            "auth": "none",
        },
        "modules": {
            "completed": [],
            "in_progress": ["mod_active", "mod_blocked"],
            "pending": ["mod_pending"],
        },
        "module_statuses": {
            "mod_active": "in_progress",
            "mod_pending": "not_started",
            "mod_blocked": "blocked",
        },
        "work_tiers": {
            "mod_active": {
                "tier": "tier_1",
                "rationale": "r",
                "classified_at": "2026-09-19T00:00:00Z",
                "classified_by": "manual",
            },
            "mod_pending": {
                "tier": "tier_1",
                "rationale": "r",
                "classified_at": "2026-09-19T00:00:00Z",
                "classified_by": "manual",
            },
            "mod_blocked": {
                "tier": "tier_1",
                "rationale": "r",
                "classified_at": "2026-09-19T00:00:00Z",
                "classified_by": "manual",
            },
        },
        "module_specs": {
            "mod_active": {
                "data_model": "n/a",
                "api_contract": "n/a",
                "classified_at": "2026-09-19T00:00:00Z",
                "classified_by": "manual",
                "justification": "test",
            },
            "mod_blocked": {
                "data_model": "n/a",
                "api_contract": "n/a",
                "classified_at": "2026-09-19T00:00:00Z",
                "classified_by": "manual",
                "justification": "test",
            },
        },
        "contracts": {"public_interfaces": {}},
        "dependencies": [],
        "architecture_constraints": [],
        "active_mcps": [],
        "closure_rejections": [],
        "_bugs": [],
        "_clarifications": [],
        "_refs": {},
    }


class TestGatesStopCriterionN3N4(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.mgr = StateManager.__new__(StateManager)
        self.mgr._is_replay = False
        self.mgr._migration_mode = False
        self.mgr.state_data = _minimal_valid_state()
        self.mgr._workspace_root = lambda: self.root

    def tearDown(self):
        self.tmp.cleanup()

    # --- N3: Desbloqueo indebido ---

    def test_n3_unblock_rejected_when_bug_open(self):
        """update_module_status no puede salir de blocked a in_progress si hay un bug bloqueante abierto."""
        self.mgr.state_data["_bugs"].append({
            "id": "BUG-011",
            "module_id": "mod_blocked",
            "severity": "Mayor",
            "status": "Abierto",
            "description": "Bug mayor que afecta ACs",
            "affects_ac": True,
            "created_at": "2026-09-19T00:00:00Z",
        })

        with self.assertRaises(ValueError) as ctx:
            self.mgr._apply_event(
                "update_module_status",
                {"id": "mod_blocked", "status": "in_progress"},
                "2026-09-19T01:00:00Z",
            )
        err = str(ctx.exception)
        self.assertIn("BUG-011", err)
        self.assertIn("resolve_bug", err)
        self.assertIn("defer_bug_to_tech_debt", err)

    def test_n3_unblock_rejected_when_clarification_open(self):
        """update_module_status no puede salir de blocked si hay una clarificación bloqueante abierta."""
        self.mgr.state_data["_clarifications"].append({
            "id": "CLAR-001",
            "blocked_scope": "mod_blocked",
            "status": "Abierta",
            "question": "¿Pregunta?",
            "blocks_start": True,
            "blocks_implementation": False,
            "blocks_closure": False,
        })

        with self.assertRaises(ValueError) as ctx:
            self.mgr._apply_event(
                "update_module_status",
                {"id": "mod_blocked", "status": "in_progress"},
                "2026-09-19T01:00:00Z",
            )
        err = str(ctx.exception)
        self.assertIn("CLAR-001", err)
        self.assertIn("resolve_clarification", err)

    def test_n3_unblock_allowed_after_resolve_bug(self):
        """Tras resolver el bug bloqueante, el módulo se desbloquea o puede transicionar."""
        self.mgr.state_data["_bugs"].append({
            "id": "BUG-011",
            "module_id": "mod_blocked",
            "severity": "Mayor",
            "status": "Abierto",
            "description": "Bug mayor",
            "affects_ac": True,
            "created_at": "2026-09-19T00:00:00Z",
        })
        # resolve_bug desbloquea automáticamente si no quedan bloqueantes
        self.mgr._apply_event(
            "resolve_bug",
            {"id": "BUG-011", "fix": "Arreglado en test"},
            "2026-09-19T01:00:00Z",
        )
        self.assertEqual(self.mgr.state_data["module_statuses"]["mod_blocked"], "in_progress")

    # --- N4: Criterio de parada (P4c y P5b) ---

    def test_n4_p4c_start_module_rejected_with_two_rejections(self):
        """P4c: no se puede arrancar un módulo (not_started -> in_progress) con >=2 rechazos de cierre."""
        self.mgr.state_data["closure_rejections"] = [
            {"id": "CR-1", "module_id": "mod_active", "reason": "ac_incomplete", "timestamp": "2026-09-19T00:00:00Z"},
            {"id": "CR-2", "module_id": "mod_active", "reason": "security_gate_failed", "timestamp": "2026-09-19T00:01:00Z"},
        ]

        with self.assertRaises(ValueError) as ctx:
            self.mgr._apply_event(
                "update_module_status",
                {"id": "mod_pending", "status": "in_progress"},
                "2026-09-19T01:00:00Z",
            )
        err = str(ctx.exception)
        self.assertIn("criterio de parada activo", err)
        self.assertIn("stabilization", err)
        self.assertIn("resolve_closure_rejections", err)

    def test_n4_rejections_without_id_show_module_and_reason(self):
        """Rechazos de cierre sin id se muestran como <module_id>/<reason> en vez de CR-?."""
        self.mgr._apply_event(
            "add_closure_rejection",
            {"module_id": "probe_mod_x", "reason": "ac_incomplete"},
            "2026-09-19T00:00:00Z",
        )
        self.mgr._apply_event(
            "add_closure_rejection",
            {"module_id": "probe_mod_x", "reason": "ac_incomplete"},
            "2026-09-19T00:01:00Z",
        )

        with self.assertRaises(ValueError) as ctx:
            self.mgr._apply_event(
                "update_module_status",
                {"id": "mod_pending", "status": "in_progress"},
                "2026-09-19T01:00:00Z",
            )
        err = str(ctx.exception)
        self.assertIn("probe_mod_x/ac_incomplete", err)
        self.assertNotIn("CR-?", err)

    def test_n4_p4c_stabilization_module_can_start_with_two_rejections(self):
        """P4c: un módulo de estabilización (stabilization: true) SÍ puede arrancar con >=2 rechazos."""
        self.mgr.state_data["closure_rejections"] = [
            {"id": "CR-1", "module_id": "mod_active", "reason": "ac_incomplete", "timestamp": "2026-09-19T00:00:00Z"},
            {"id": "CR-2", "module_id": "mod_active", "reason": "security_gate_failed", "timestamp": "2026-09-19T00:01:00Z"},
        ]
        # Añadir módulo de estabilización
        self.mgr._apply_event(
            "add_module",
            {"id": "mod_stab", "stabilization": True},
            "2026-09-19T00:02:00Z",
        )
        self.assertEqual(
            self.mgr.state_data.get("module_specs", {}).get("mod_stab", {}).get("stabilization"),
            True,
        )
        # Arrancar módulo de estabilización debe permitirse
        res = self.mgr._apply_event(
            "update_module_status",
            {"id": "mod_stab", "status": "in_progress"},
            "2026-09-19T00:03:00Z",
        )
        self.assertIn("actualizado a 'in_progress'", res)
        self.assertEqual(self.mgr.state_data["module_statuses"]["mod_stab"], "in_progress")

    def test_n4_p5b_start_module_rejected_with_critical_bug_open(self):
        """P5b: no se puede arrancar un módulo (not_started -> in_progress) con bug crítico abierto."""
        self.mgr.state_data["_bugs"].append({
            "id": "BUG-CRIT",
            "module_id": "mod_active",
            "severity": "Crítico",
            "status": "Abierto",
            "description": "Fallo de integridad crítico",
            "created_at": "2026-09-19T00:00:00Z",
        })

        with self.assertRaises(ValueError) as ctx:
            self.mgr._apply_event(
                "update_module_status",
                {"id": "mod_pending", "status": "in_progress"},
                "2026-09-19T01:00:00Z",
            )
        err = str(ctx.exception)
        self.assertIn("criterio de parada activo", err)
        self.assertIn("BUG-CRIT", err)
        self.assertIn("stabilization", err)

    def test_n4_p5b_stabilization_module_can_start_with_critical_bug(self):
        """P5b: un módulo de estabilización SÍ puede arrancar con bug crítico abierto."""
        self.mgr.state_data["_bugs"].append({
            "id": "BUG-CRIT",
            "module_id": "mod_active",
            "severity": "Crítico",
            "status": "Abierto",
            "description": "Fallo crítico",
            "created_at": "2026-09-19T00:00:00Z",
        })
        self.mgr._apply_event(
            "add_module",
            {"id": "mod_stab", "stabilization": True},
            "2026-09-19T00:02:00Z",
        )
        res = self.mgr._apply_event(
            "update_module_status",
            {"id": "mod_stab", "status": "in_progress"},
            "2026-09-19T00:03:00Z",
        )
        self.assertIn("actualizado a 'in_progress'", res)

    # --- Condición 2 del builder: tolerancia estricta de validate_state ---

    def test_validate_state_accepts_only_stabilization_flag(self):
        """validate_state acepta una entrada en module_specs si su único campo es stabilization: bool."""
        state = copy.deepcopy(self.mgr.state_data)
        state["module_specs"]["mod_stab"] = {"stabilization": True}
        # No debe lanzar excepción
        self.mgr.validate_state(state, root=self.root)

    def test_validate_state_rejects_stabilization_with_incomplete_fields(self):
        """validate_state rechaza module_specs si tiene stabilization y otros campos incompletos."""
        state = copy.deepcopy(self.mgr.state_data)
        state["module_specs"]["mod_stab"] = {
            "stabilization": True,
            "data_model": "required",
            # faltan api_contract, classified_at, classified_by
        }
        with self.assertRaises(ValueError) as ctx:
            self.mgr.validate_state(state, root=self.root)
        self.assertIn("module_specs.mod_stab incompleto", str(ctx.exception))

    def test_validate_state_rejects_non_boolean_stabilization(self):
        """validate_state rechaza module_specs con stabilization no booleano."""
        state = copy.deepcopy(self.mgr.state_data)
        state["module_specs"]["mod_stab"] = {"stabilization": "yes"}
        with self.assertRaises(ValueError):
            self.mgr.validate_state(state, root=self.root)

    # --- Condición 4 del builder: Preservación en Replay (D7) ---

    def test_replay_preserves_historical_transitions(self):
        """Bajo _is_replay=True, update_module_status no aborta ante bloqueantes o criterio de parada."""
        self.mgr._is_replay = True
        self.mgr.state_data["_bugs"].append({
            "id": "BUG-011",
            "module_id": "mod_blocked",
            "severity": "Mayor",
            "status": "Abierto",
            "affects_ac": True,
        })
        self.mgr.state_data["closure_rejections"] = [
            {"id": "CR-1", "module_id": "mod_active", "reason": "ac_incomplete", "timestamp": "2026-09-19T00:00:00Z"},
            {"id": "CR-2", "module_id": "mod_active", "reason": "ac_incomplete", "timestamp": "2026-09-19T00:01:00Z"},
        ]

        # 1. blocked -> in_progress con bug abierto no debe abortar en replay
        res1 = self.mgr._apply_event(
            "update_module_status",
            {"id": "mod_blocked", "status": "in_progress"},
            "2026-09-19T01:00:00Z",
        )
        self.assertIn("actualizado a 'in_progress'", res1)

        # 2. not_started -> in_progress con 2 rechazos no debe abortar en replay
        res2 = self.mgr._apply_event(
            "update_module_status",
            {"id": "mod_pending", "status": "in_progress"},
            "2026-09-19T01:00:00Z",
        )
        self.assertIn("actualizado a 'in_progress'", res2)


if __name__ == "__main__":
    unittest.main()
