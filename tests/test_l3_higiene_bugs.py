#!/usr/bin/env python3
"""L3.2 de la 5.3.1: higiene de bugs (FW-300, TL-FW-313).

FW-300 (decisión D3-A del builder, 2026-10-21): `register_bug` no reabre ni
bloquea un módulo `completed`; reabrir es `reopen_module` con motivo. La marca
interna `_keeps_completed` del evento logueado hace que la reconstrucción desde
la bitácora sea idéntica: un `register_bug` histórico sin marca conserva el
efecto antiguo (FW-020). `resolve_bug` nunca toca un módulo `completed`.

TL-FW-313: el doctor avisa de bugs `Abierto` o `Deuda Técnica` cuyo `fix` dice
que está corregido.
"""
import copy
import json
import sys
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / ".agent" / "scripts"))

import framework_doctor  # noqa: E402
import replay_state_log  # noqa: E402
import state_core  # noqa: E402
import validate_transition  # noqa: E402

TS = "2026-10-21T00:00:00Z"


def completed_state():
    return {
        "_bugs": [],
        "modules": {"completed": ["M1"], "in_progress": [], "pending": []},
        "module_statuses": {"M1": "completed"},
        "workflow_receipts": {"approvals": {"M1": {"basis": {}}}, "reviews": {"M1": [{"status": "pass"}]}},
    }


def manager(state, *, replay=False):
    m = state_core.StateManager.__new__(state_core.StateManager)
    m.state_data = state
    if replay:
        m._is_replay = True
    return m


def register(severity, affects_ac=False):
    return {"module_id": "M1", "severity": severity, "description": "regresión real", "affects_ac": affects_ac}


class RegisterBugKeepsCompletedTests(unittest.TestCase):
    """FW-300: el bug no reabre el módulo cerrado."""

    def test_critico_sobre_completed_no_lo_reabre(self):
        state = completed_state()
        payload = register("Crítico")
        msg = manager(state)._apply_event("register_bug", payload, TS)
        self.assertEqual(state["module_statuses"]["M1"], "completed")
        self.assertEqual(state["modules"]["completed"], ["M1"])
        self.assertEqual(state["modules"]["in_progress"], [])
        self.assertIn("M1", state["workflow_receipts"]["approvals"])
        self.assertIn("M1", state["workflow_receipts"]["reviews"])
        self.assertIs(payload.get("_keeps_completed"), True)
        self.assertEqual(state["_bugs"][0]["status"], "Abierto")
        self.assertIn("reopen_module", msg)

    def test_mayor_con_ac_sobre_completed_no_lo_reabre(self):
        state = completed_state()
        payload = register("Mayor", affects_ac=True)
        manager(state)._apply_event("register_bug", payload, TS)
        self.assertEqual(state["module_statuses"]["M1"], "completed")
        self.assertIn("M1", state["workflow_receipts"]["approvals"])
        self.assertIs(payload.get("_keeps_completed"), True)

    def test_modulo_no_cerrado_se_sigue_bloqueando_sin_marca(self):
        state = completed_state()
        state["module_statuses"]["M1"] = "in_progress"
        state["modules"] = {"completed": [], "in_progress": ["M1"], "pending": []}
        payload = register("Crítico")
        manager(state)._apply_event("register_bug", payload, TS)
        self.assertEqual(state["module_statuses"]["M1"], "blocked")
        self.assertNotIn("_keeps_completed", payload)

    def test_menor_no_lleva_marca(self):
        state = completed_state()
        payload = register("Menor")
        manager(state)._apply_event("register_bug", payload, TS)
        self.assertEqual(state["module_statuses"]["M1"], "completed")
        self.assertNotIn("_keeps_completed", payload)

    def test_resolve_bug_no_toca_un_modulo_completed(self):
        state = completed_state()
        state["_bugs"].append({"id": "BUG-001", "module_id": "M1", "severity": "Mayor", "affects_ac": True,
                               "status": "Abierto", "description": "d", "fix": "", "affected_files": []})
        payload = {"id": "BUG-001", "fix": "corregido en src/x.py"}
        manager(state)._apply_event("resolve_bug", payload, TS)
        self.assertEqual(state["module_statuses"]["M1"], "completed")
        self.assertNotIn("_status_effect", payload)

    def test_el_caller_no_puede_mandar_la_marca(self):
        m = manager(completed_state())
        payload = dict(register("Crítico"), _keeps_completed=True)
        with self.assertRaises(ValueError) as ctx:
            m._mutate_locked("register_bug", payload)
        self.assertIn("_keeps_completed", str(ctx.exception))


class RegisterBugReplayTests(unittest.TestCase):
    """La reconstrucción desde la bitácora sigue siendo idéntica."""

    def test_register_bug_historico_sin_marca_reabre_como_antes(self):
        state = completed_state()
        manager(state, replay=True)._apply_event("register_bug", register("Crítico"), TS)
        self.assertEqual(state["module_statuses"]["M1"], "blocked")
        self.assertNotIn("M1", state["modules"]["completed"])
        self.assertNotIn("M1", state["workflow_receipts"]["approvals"])
        self.assertNotIn("M1", state["workflow_receipts"]["reviews"])

    def test_register_bug_con_marca_se_reproduce_igual(self):
        for severity, affects in (("Crítico", False), ("Mayor", True)):
            with self.subTest(severity):
                live, logged = completed_state(), register(severity, affects)
                before = copy.deepcopy(live)
                manager(live)._apply_event("register_bug", logged, TS)
                replayed = before
                manager(replayed, replay=True)._apply_event("register_bug", copy.deepcopy(logged), TS)
                self.assertEqual(replayed, live)


class RegisterBugTransitionTests(unittest.TestCase):
    def test_con_marca_no_respalda_ninguna_transicion(self):
        payload = dict(register("Crítico"), _keeps_completed=True)
        self.assertIsNone(validate_transition._status_effect("register_bug", payload))

    def test_sin_marca_respalda_completed_a_blocked(self):
        self.assertEqual(validate_transition._status_effect("register_bug", register("Crítico")),
                         ("M1", "blocked"))


BASE_STATE = {
    "project": "Higiene", "framework_mode": "initialized",
    "framework_version": state_core.FRAMEWORK_VERSION, "current_phase": "phase_02",
    "current_step": "s", "last_completed_step": "",
    "stack": {"backend": "Python", "frontend": "-", "database": "-", "auth": "-"},
    "dependencies": [], "architecture_constraints": [],
    "modules": {"completed": ["m1"], "in_progress": [], "pending": []},
    "module_statuses": {"m1": "completed"},
    "work_tiers": {}, "module_specs": {}, "closure_rejections": [],
    "active_mcps": [], "contracts": {"public_interfaces": {}},
    "_refs": {}, "_bugs": [], "_clarifications": [], "_decisions": [],
}


@contextmanager
def entorno(state):
    attrs = ("WORKSPACE_ROOT", "STATE_FILE_PATH", "CORE_FILE_PATH", "BUGS_FILE_PATH",
             "CLARIFICATIONS_FILE_PATH", "DECISIONS_FILE_PATH", "SESSION_BRIEF_PATH")
    original = {name: getattr(state_core, name) for name in attrs}
    try:
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            (tmp / "_project").mkdir()
            for attr, rel in (
                ("STATE_FILE_PATH", "_project/03_project_state.json"),
                ("CORE_FILE_PATH", "_project/core.md"),
                ("BUGS_FILE_PATH", "_project/06_bug_fixes.md"),
                ("CLARIFICATIONS_FILE_PATH", "_project/07_pending_clarifications.md"),
                ("DECISIONS_FILE_PATH", "_project/03_arch_decisions.md"),
                ("SESSION_BRIEF_PATH", "_project/00_session_brief.json"),
            ):
                setattr(state_core, attr, tmp / rel)
            state_core.WORKSPACE_ROOT = tmp
            (tmp / "_project" / "03_project_state.json").write_text(
                json.dumps(state, ensure_ascii=False), encoding="utf-8")
            yield tmp, state_core.StateManager()
    finally:
        for name, value in original.items():
            setattr(state_core, name, value)


class RegisterBugEndToEndTests(unittest.TestCase):
    def test_mutacion_real_y_reconstruccion_identica(self):
        with entorno(BASE_STATE) as (tmp, mgr):
            msg = mgr.mutate("register_bug", {"module_id": "m1", "severity": "Crítico",
                                              "description": "se cae al imprimir"})
            self.assertIn("reopen_module", msg)
            live = json.loads((tmp / "_project" / "03_project_state.json").read_text("utf-8"))
            self.assertEqual(live["module_statuses"]["m1"], "completed")
            log = tmp / "_project" / "state_log.jsonl"
            events = [json.loads(line) for line in log.read_text("utf-8").splitlines() if line.strip()]
            self.assertIs(events[-1]["payload"].get("_keeps_completed"), True)
            stats = replay_state_log.replay(log, tmp / "replayed.json", strict=False,
                                            base_state=copy.deepcopy(BASE_STATE))
            self.assertEqual(stats["errors"], [])
            for key in ("module_statuses", "modules", "_bugs"):
                self.assertEqual(stats["state"][key], live[key], key)

    def test_reopen_module_sigue_siendo_la_via_para_reabrir(self):
        with entorno(BASE_STATE) as (tmp, mgr):
            mgr.mutate("register_bug", {"module_id": "m1", "severity": "Crítico",
                                        "description": "se cae al imprimir"})
            mgr.mutate("reopen_module", {"id": "m1", "reason": "BUG-001 rompe la impresión en campo"})
            live = json.loads((tmp / "_project" / "03_project_state.json").read_text("utf-8"))
            self.assertEqual(live["module_statuses"]["m1"], "in_progress")


class FixedButOpenDoctorTests(unittest.TestCase):
    """TL-FW-313: el doctor avisa (nunca error) de bugs corregidos que siguen abiertos."""

    SAMPLES = [
        ({"id": "BUG-001", "status": "Abierto", "fix": "Corregido en task:debt_b1 (ADR-351); tests en verde"}, True),
        ({"id": "BUG-002", "status": "Deuda Técnica", "fix": "Arreglado en task:funding: benchmark eliminado"}, True),
        ({"id": "BUG-003", "status": "Abierto", "fix": "PARCIAL (ADR-192): corregida la parte de almacenamiento"}, False),
        ({"id": "BUG-004", "status": "Abierto", "fix": "Corregido solo con modelo de costes (ADR-310)"}, False),
        ({"id": "BUG-005", "status": "Abierto", "fix": "NO RESUELTO — reabierto como BUG-079"}, False),
        ({"id": "BUG-006", "status": "Resuelto", "fix": "Corregido en task:x"}, False),
    ]

    def test_avisa_de_los_corregidos_y_calla_el_resto(self):
        warnings: list[str] = []
        framework_doctor.validate_bugs_fixed_but_open({"_bugs": [b for b, _ in self.SAMPLES]}, warnings)
        self.assertEqual(len(warnings), 1, warnings)
        for bug, expected in self.SAMPLES:
            self.assertEqual(bug["id"] in warnings[0], expected, bug["id"])
        self.assertIn("resolve_bug", warnings[0])
        self.assertIn("amend_bug", warnings[0])

    def test_sin_bugs_o_estado_raro_calla(self):
        for state in ({}, {"_bugs": None}, {"_bugs": ["x", {"status": "Abierto", "fix": None}]}, None):
            warnings: list[str] = []
            framework_doctor.validate_bugs_fixed_but_open(state, warnings)
            self.assertEqual(warnings, [])


class CierreModuloProposesResolveTests(unittest.TestCase):
    def test_cierre_propone_resolver_los_bugs_que_citan_las_adr(self):
        text = " ".join((ROOT / ".agent/workflows/cierre-modulo.md").read_text("utf-8").split())
        section = text[text.index("### 2. VÁLVULA DE ESCAPE"):text.index("### 3. AUDITORÍAS EN PARALELO")]
        self.assertIn("resolve_bug", section)
        self.assertIn("ADR", section)


if __name__ == "__main__":
    unittest.main()
