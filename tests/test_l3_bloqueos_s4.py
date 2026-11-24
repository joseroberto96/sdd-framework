#!/usr/bin/env python3
"""L3.1 de la 5.3.1: S4 y su ampliación por FW-359 en `_blocking_items`.

S4: un Mayor con `affects_ac` bloquea su módulo, los que dependen de él y la
tarea que lo declara; un Crítico sigue bloqueando todo. FW-359 (decisiones
D1-A y D2-A del builder, 2026-10-21): el bug que una unidad declara arreglar con
su propio id (`bug:BUG-NNN` o una Estabilización `estabilizacion-BUG-NNN`) no
bloquea la vista `implementation` de esa unidad; el resto de bloqueos no cambia
y el dueño `blocked` no tiene excepción.

Incluye los 10 tests de PA-FW-004 (Research App,
`tests/test_fw_blocking_scope.py`) con un solo cambio de veredicto: una tarea
(`task:T1`) deja de ver como global un Mayor ajeno (S4 a `task:`).
"""
import copy
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / ".agent" / "scripts"))

import state_queries  # noqa: E402
from state_queries import _blocking_items  # noqa: E402

TS = "2026-10-21T00:00:00Z"
BLOCKER_KINDS = {"blocker", "closure_rejection", "validation_blocker"}


# ------------------------------------------------------------ PA-FW-004 (10)
def _bug(bug_id, module_id, severity="Mayor", affects_ac=True, status="Abierto"):
    return {"id": bug_id, "module_id": module_id, "severity": severity,
            "affects_ac": affects_ac, "status": status, "description": "x"}


def _ids(state, view, target):
    return [item["id"] for item in _blocking_items(state, view, target)]


def _state(*bugs, specs=None, clars=None, rejections=None):
    return {"_bugs": list(bugs), "_clarifications": clars or [], "module_statuses": {},
            "module_specs": specs or {}, "closure_rejections": rejections or []}


class BlockingScopePAFW004Tests(unittest.TestCase):
    """Retroportados de PA-FW-004; mismo veredicto salvo `task:` (S4)."""

    def test_critico_ajeno_bloquea_global(self):
        state = _state(_bug("BUG-1", "C4", severity="Crítico"))
        for view in ("implementation", "closure", "startup"):
            self.assertEqual(_ids(state, view, "UI"), ["BUG-1"])

    def test_mayor_del_modulo_bloquea(self):
        state = _state(_bug("BUG-1", "UI"))
        self.assertEqual(_ids(state, "implementation", "UI"), ["BUG-1"])
        self.assertEqual(_ids(state, "closure", "UI"), ["BUG-1"])

    def test_mayor_ajeno_no_bloquea_implementation_ni_closure(self):
        state = _state(_bug("BUG-1", "C4"))
        self.assertEqual(_ids(state, "implementation", "UI"), [])
        self.assertEqual(_ids(state, "closure", "UI"), [])

    def test_mayor_ajeno_sigue_visible_en_startup_y_recovery(self):
        state = _state(_bug("BUG-1", "C4"))
        self.assertEqual(_ids(state, "startup", "UI"), ["BUG-1"])
        self.assertEqual(_ids(state, "recovery", "UI"), ["BUG-1"])

    def test_mayor_de_dependencia_declarada_bloquea(self):
        state = _state(_bug("BUG-1", "C4"), specs={"UI": {"depends_on": ["C4"]}})
        self.assertEqual(_ids(state, "implementation", "UI"), ["BUG-1"])

    def test_mayor_sin_modulo_bloquea_conservador(self):
        state = _state(_bug("BUG-1", None))
        self.assertEqual(_ids(state, "implementation", "UI"), ["BUG-1"])

    def test_mayor_sin_objetivo_es_global(self):
        state = _state(_bug("BUG-1", "C4"))
        self.assertEqual(_ids(state, "implementation", None), ["BUG-1"])

    def test_mayor_ajeno_no_bloquea_una_tarea_que_no_lo_declara(self):
        """Cambio de veredicto respecto a PA-FW-004 (allí, `task:T1` era global)."""
        state = _state(_bug("BUG-1", "C4"))
        self.assertEqual(_ids(state, "implementation", "task:T1"), [])

    def test_mayor_sin_affects_ac_o_cerrado_no_bloquea(self):
        state = _state(_bug("BUG-1", "UI", affects_ac=False), _bug("BUG-2", "UI", status="Resuelto"))
        self.assertEqual(_ids(state, "implementation", "UI"), [])

    def test_clarificaciones_y_rechazos_intactos(self):
        clar = {"id": "CLAR-1", "status": "Abierta", "blocks_implementation": True,
                "blocked_scope": "C4", "question": "q"}
        rej = [{"id": f"R{i}", "module_id": "C4", "reason": "r"} for i in range(2)]
        state = _state(clars=[clar], rejections=rej)
        self.assertEqual(_ids(state, "implementation", "UI"), ["CLAR-1"])
        self.assertTrue(set(_ids(state, "startup", "UI")) >= {"R0", "R1"})

    def test_depends_on_ausente_o_mal_formado_no_amplia_ni_rompe(self):
        """Solo dependencias directas; formatos inválidos se ignoran."""
        for spec in ({}, {"depends_on": "C4"}, {"depends_on": [None, 3]}, "n/a"):
            state = _state(_bug("BUG-1", "C4"), specs={"UI": spec})
            self.assertEqual(_ids(state, "implementation", "UI"), [])


# ------------------------------------------------- prueba roja R1–R4 y tareas
def base_state():
    state = json.loads(
        (ROOT / "tests/fixtures/progressive_context/state_base.json").read_text("utf-8")
    )
    for mod in ("S", "X", "Z", "UI", "C4", "estabilizacion-BUG-001"):
        state["module_statuses"][mod] = "in_progress"
        state["modules"]["in_progress"].append(mod)
    state["module_specs"]["S"] = {"stabilization": True}
    state["module_specs"]["estabilizacion-BUG-001"] = {"stabilization": True}
    return state


def bug(bug_id, module_id, severity, affects_ac=True, status="Abierto"):
    return {"id": bug_id, "module_id": module_id, "severity": severity, "status": status,
            "affects_ac": affects_ac, "description": f"fixture {bug_id}", "created_at": TS}


def tier():
    return {"tier": "tier_2", "rationale": "fixture", "classified_at": TS, "classified_by": "fixture"}


def full_spec(**extra):
    spec = {"data_model": "n/a", "api_contract": "n/a", "justification": "f",
            "classified_at": TS, "classified_by": "f"}
    spec.update(extra)
    return spec


def mayor_en_x_bloqueado():
    state = base_state()
    state["module_statuses"]["X"] = "blocked"
    state["_bugs"] = [bug("BUG-001", "X", "Mayor")]
    return state


def cases():
    """(nombre, estado, vista, objetivo, validación, bloqueantes esperados)."""
    out = []

    # R1 — Crítico BUG-001 en X; la unidad que lo declara arreglar.
    s = base_state(); s["_bugs"] = [bug("BUG-001", "X", "Crítico")]
    out.append(("R1a Estabilización que declara BUG-001", s, "implementation",
                "estabilizacion-BUG-001", None, set()))
    s = base_state(); s["_bugs"] = [bug("BUG-001", "X", "Crítico")]
    out.append(("R1a' Estabilización que no declara nada", s, "implementation", "S", None, {"BUG-001"}))
    s = base_state(); s["_bugs"] = [bug("BUG-001", "X", "Crítico")]; s["work_tiers"]["bug:BUG-001"] = tier()
    out.append(("R1b unidad bug:BUG-001 (Crítico)", s, "implementation", "bug:BUG-001", None, set()))
    for view in ("startup", "recovery", "closure"):
        s = base_state(); s["_bugs"] = [bug("BUG-001", "X", "Crítico")]
        out.append((f"R1c/d {view} con el sujeto abierto", s, view, "estabilizacion-BUG-001", None, {"BUG-001"}))
    s = base_state(); s["_bugs"] = [bug("BUG-001", "X", "Crítico")]; s["work_tiers"]["bug:BUG-001"] = tier()
    out.append(("R1d closure de bug:BUG-001 con el sujeto abierto", s, "closure", "bug:BUG-001",
                None, {"BUG-001"}))

    # R2 — Mayor+AC BUG-001 en X `blocked`.
    out.append(("R2a Estabilización sin relación (PA-FW-004)", mayor_en_x_bloqueado(), "implementation",
                "S", None, set()))
    s = mayor_en_x_bloqueado()
    s["module_specs"]["estabilizacion-BUG-001"] = full_spec(stabilization=True, depends_on=["X"])
    out.append(("R2b Estabilización que declara BUG-001 con depends_on X", s, "implementation",
                "estabilizacion-BUG-001", None, set()))
    s = mayor_en_x_bloqueado(); s["work_tiers"]["bug:BUG-001"] = tier()
    out.append(("R2c unidad bug:BUG-001 (Mayor)", s, "implementation", "bug:BUG-001", None, set()))
    out.append(("R2d dueño X blocked, sin excepción (D2-A)", mayor_en_x_bloqueado(), "implementation", "X",
                None, {"BUG-001", "module:X:blocked"}))

    # R4 — lo que sigue bloqueando.
    s = base_state(); s["_bugs"] = [bug("BUG-001", "X", "Crítico"), bug("BUG-009", "Z", "Crítico", affects_ac=False)]
    out.append(("R4a Crítico ajeno a la Estabilización que arregla otro", s, "implementation",
                "estabilizacion-BUG-001", None, {"BUG-009"}))
    s = base_state(); s["_bugs"] = [bug("BUG-002", None, "Mayor")]
    out.append(("R4b Mayor sin módulo", s, "implementation", "S", None, {"BUG-002"}))
    s = mayor_en_x_bloqueado(); s["_bugs"].append(bug("BUG-003", "X", "Mayor"))
    out.append(("R4c otro Mayor del dueño del sujeto", s, "implementation", "estabilizacion-BUG-001",
                None, {"BUG-003"}))
    s = base_state(); s["_clarifications"] = [{"id": "CLAR-1", "blocked_scope": "S", "question": "q",
                                                "status": "Abierta", "blocks_implementation": True}]
    out.append(("R4d clarificación bloqueante", s, "implementation", "S", None, {"CLAR-1"}))
    out.append(("R4e validation.hard_blockers", base_state(), "implementation", "S",
                {"hard_blockers": ["x"]}, {"validation:block:0000"}))
    s = base_state(); s["_bugs"] = [bug("BUG-001", "X", "Mayor")]; s["work_tiers"]["task:T"] = tier()
    out.append(("R4f tarea sin relación con un Mayor ajeno (PA-FW-014 reducido)", s, "implementation",
                "task:T", None, set()))
    s = base_state(); s["closure_rejections"] = [{"id": "REJ-S", "module_id": "S", "reason": "ac_incomplete",
                                                  "timestamp": TS}]
    out.append(("R4g rechazo de cierre activo", s, "closure", "S", None, {"REJ-S"}))
    s = base_state(); s["_bugs"] = [bug("BUG-001", "X", "Crítico")]
    s["module_specs"]["X-BUG-001"] = full_spec()
    s["module_statuses"]["X-BUG-001"] = "in_progress"; s["modules"]["in_progress"].append("X-BUG-001")
    out.append(("R4h módulo normal cuyo id nombra el bug", s, "implementation", "X-BUG-001", None, {"BUG-001"}))
    s = base_state(); s["_bugs"] = [bug("BUG-001", "X", "Crítico", status="Resuelto"), bug("BUG-009", "Z", "Crítico")]
    s["work_tiers"]["bug:BUG-001"] = tier()
    out.append(("R4i unidad bug: de un bug ya resuelto", s, "implementation", "bug:BUG-001", None, {"BUG-009"}))
    s = base_state(); s["_bugs"] = [bug("BUG-004", "Z", "Mayor")]; s["work_tiers"]["bug:BUG-777"] = tier()
    out.append(("R4j unidad bug: de un bug inexistente", s, "implementation", "bug:BUG-777", None, {"BUG-004"}))
    s = base_state(); s["_bugs"] = [bug("BUG-004", None, "Crítico"), bug("BUG-005", "Z", "Mayor")]
    s["work_tiers"]["bug:BUG-004"] = tier()
    out.append(("R4k unidad bug: de un bug sin módulo", s, "implementation", "bug:BUG-004",
                None, {"BUG-004", "BUG-005"}))
    s = base_state(); s["_bugs"] = [bug("BUG-001", "estabilizacion-BUG-001", "Crítico")]
    out.append(("R4l Estabilización cuyo bug es suyo", s, "implementation", "estabilizacion-BUG-001",
                None, {"BUG-001"}))
    # Revisión `security` de L3, hallazgo 3: módulo vacío o de espacios = sin módulo.
    for empty in ("", " "):
        s = base_state(); s["_bugs"] = [bug("BUG-004", empty, "Crítico"), bug("BUG-005", "Z", "Mayor")]
        s["work_tiers"]["bug:BUG-004"] = tier()
        out.append((f"R4m unidad bug: de un bug con módulo {empty!r}", s, "implementation", "bug:BUG-004",
                    None, {"BUG-004", "BUG-005"}))
        s = base_state(); s["_bugs"] = [bug("BUG-006", empty, "Mayor")]
        out.append((f"R4n Mayor+AC con módulo {empty!r} bloquea siempre", s, "implementation", "UI",
                    None, {"BUG-006"}))

    # PA-FW-004 en la vista completa y ampliación a task:.
    s = base_state(); s["_bugs"] = [bug("BUG-086", "C4", "Mayor")]
    out.append(("PA-FW-004 Mayor ajeno, módulo UI independiente", s, "implementation", "UI", None, set()))
    s = base_state(); s["_bugs"] = [bug("BUG-086", "C4", "Mayor")]
    out.append(("PA-FW-004 Mayor ajeno en closure", s, "closure", "UI", None, set()))
    s = base_state(); s["_bugs"] = [bug("BUG-086", "C4", "Mayor")]
    s["module_specs"]["UI"] = full_spec(depends_on=["C4"])
    out.append(("PA-FW-004 Mayor de una dependencia declarada", s, "implementation", "UI", None, {"BUG-086"}))
    s = base_state(); s["_bugs"] = [bug("BUG-086", "C4", "Mayor")]
    out.append(("PA-FW-004 Mayor ajeno sigue visible en startup", s, "startup", "UI", None, {"BUG-086"}))
    s = base_state(); s["_bugs"] = [bug("BUG-005", "C4", "Crítico")]
    out.append(("Crítico ajeno bloquea un módulo independiente", s, "implementation", "UI", None, {"BUG-005"}))
    s = base_state(); s["_bugs"] = [bug("BUG-005", "C4", "Crítico")]; s["work_tiers"]["task:T"] = tier()
    out.append(("Crítico ajeno bloquea una tarea", s, "implementation", "task:T", None, {"BUG-005"}))
    s = base_state(); s["_bugs"] = [bug(f"BUG-1{i:02d}", f"M{i}", "Mayor") for i in range(8)]
    s["work_tiers"]["task:forense"] = tier()
    out.append(("PA-FW-014 tarea con 8 Mayores ajenos", s, "implementation", "task:forense", None, set()))
    s = base_state(); s["_bugs"] = [bug("BUG-006", "task:T", "Mayor")]; s["work_tiers"]["task:T"] = tier()
    out.append(("Mayor del propio módulo de la tarea", s, "implementation", "task:T", None, {"BUG-006"}))
    s = base_state(); s["_bugs"] = [bug("BUG-007", "C4", "Mayor")]; s["work_tiers"]["task:revisar-BUG-007"] = tier()
    out.append(("Mayor que la tarea declara en su id", s, "implementation", "task:revisar-BUG-007", None,
                {"BUG-007"}))
    s = base_state(); s["_bugs"] = [bug("BUG-007", "C4", "Crítico")]; s["work_tiers"]["task:revisar-BUG-007"] = tier()
    out.append(("Crítico que una tarea nombra sigue bloqueando (sin exención)", s, "implementation",
                "task:revisar-BUG-007", None, {"BUG-007"}))
    s = base_state(); s["_bugs"] = [bug("BUG-008", "C4", "Mayor")]
    s["changes"] = [{"id": "CHG-001", "summary": "s", "tier": "tier_1", "criteria": ["c"],
                     "affected_modules": ["C4"], "status": "open", "opened_at": TS}]
    s["work_tiers"]["task:CHG-001"] = tier()
    out.append(("Mayor de un módulo afectado por el cambio de mantenimiento", s, "implementation",
                "task:CHG-001", None, {"BUG-008"}))
    s = base_state(); s["_bugs"] = [bug("BUG-086", "C4", "Mayor")]
    out.append(("Mayor ajeno con objetivo implícito M1", s, "implementation", None, None, set()))
    return out


class BlockingScopeQueryContextTests(unittest.TestCase):
    """Los casos de la prueba roja de FW-359 y PA-FW-014 en `query_context`."""

    def test_casos(self):
        for name, state, view, target, validation, expected in cases():
            with self.subTest(name):
                before = copy.deepcopy(state)
                result = state_queries.query_context(
                    state, view=view, module_id=target, workspace_root=ROOT, validation=validation
                )
                self.assertEqual(state, before, "la consulta no muta el estado")
                items = [i for i in result["data"]["items"] if i.get("kind") in BLOCKER_KINDS]
                self.assertEqual({i["id"] for i in items}, expected)
                self.assertEqual(result["gate_status"] == "BLOCK", bool(expected))
                self.assertEqual(result["blocking_count"], len(items))


class RuntimeCoreDiagnosisLineTests(unittest.TestCase):
    def test_leer_medir_y_diagnosticar_no_son_implementacion(self):
        text = " ".join((ROOT / ".agent/rules/runtime_core.md").read_text("utf-8").split())
        self.assertIn("Leer, medir y diagnosticar no son implementación", text)
        self.assertIn("los límites duros siguen activos", text)


if __name__ == "__main__":
    unittest.main()
