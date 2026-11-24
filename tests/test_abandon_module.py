#!/usr/bin/env python3
"""PA-FW-037 (5.3.3): retirar un módulo que ya arrancó y mover un bug abierto.

`drop_module` solo retira lo que nunca arrancó. `abandon_module` cierra un
módulo no terminal en el estado terminal `dropped` (distinto de `completed`),
con motivo y evidencia, y deja rastro en `module_abandonments`.
`reassign_bug_module` mueve un bug `Abierto` a otro módulo vivo, con motivo y
el valor previo en `amendments`.
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

import _transitions  # noqa: E402
import action_contracts  # noqa: E402
import framework_doctor  # noqa: E402
import projections  # noqa: E402
import replay_state_log  # noqa: E402
import state_core  # noqa: E402
import state_queries  # noqa: E402
import validate_transition  # noqa: E402

TS = "2026-10-31T00:00:00Z"
REASON = "Duplicado de C8b tras el inventario; el builder aprobó el cierre"
EVIDENCE = "inventario §2 y aprobación del builder del 2026-10-29"


def bug(bug_id, module_id, status="Abierto", severity="Menor"):
    return {"id": bug_id, "module_id": module_id, "severity": severity, "status": status,
            "description": "residuo real", "affects_ac": False, "fix": "", "affected_files": [],
            "created_at": TS}


def base_state():
    return {
        "project": "Abandono", "framework_mode": "initialized",
        "framework_version": state_core.FRAMEWORK_VERSION, "current_phase": "phase_02",
        "current_step": "s", "last_completed_step": "",
        "stack": {"backend": "Python", "frontend": "-", "database": "-", "auth": "-"},
        "dependencies": [], "architecture_constraints": [],
        "modules": {"completed": ["done"], "in_progress": ["wip", "blk", "rfv"], "pending": ["todo", "vivo"]},
        "module_statuses": {"done": "completed", "wip": "in_progress", "blk": "blocked",
                            "rfv": "ready_for_validation", "todo": "not_started", "vivo": "not_started"},
        "work_tiers": {}, "module_specs": {}, "closure_rejections": [],
        "active_mcps": [], "contracts": {"public_interfaces": {}},
        "_refs": {}, "_bugs": [], "_clarifications": [], "_decisions": [],
    }


def manager(state):
    m = state_core.StateManager.__new__(state_core.StateManager)
    m.state_data = state
    return m


def abandon(mod_id, **extra):
    payload = {"id": mod_id, "reason": REASON, "evidence": EVIDENCE}
    payload.update(extra)
    return payload


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


class AbandonModuleTests(unittest.TestCase):
    def test_abandona_desde_cada_estado_no_terminal(self):
        for mod_id, previo in (("wip", "in_progress"), ("blk", "blocked"),
                               ("rfv", "ready_for_validation"), ("todo", "not_started")):
            with self.subTest(previo=previo):
                state = base_state()
                msg = manager(state)._apply_event("abandon_module", abandon(mod_id), TS)
                self.assertIn(mod_id, msg)
                self.assertEqual(state["module_statuses"][mod_id], "dropped")
                self.assertEqual(state["modules"]["dropped"], [mod_id])
                for bucket in ("completed", "in_progress", "pending"):
                    self.assertNotIn(mod_id, state["modules"][bucket])
                record = state["module_abandonments"][mod_id]
                self.assertEqual(record["from_status"], previo)
                self.assertEqual(record["reason"], REASON)
                self.assertEqual(record["evidence"], EVIDENCE)
                self.assertEqual(record["abandoned_at"], TS)

    def test_exige_motivo_y_evidencia(self):
        for payload in ({"id": "wip", "evidence": EVIDENCE},
                        {"id": "wip", "reason": "corto", "evidence": EVIDENCE},
                        {"id": "wip", "reason": REASON},
                        {"id": "wip", "reason": REASON, "evidence": "   "},
                        {"reason": REASON, "evidence": EVIDENCE}):
            with self.subTest(payload=payload):
                state = base_state()
                with self.assertRaises(ValueError):
                    manager(state)._apply_event("abandon_module", payload, TS)
                self.assertEqual(state["module_statuses"]["wip"], "in_progress")

    def test_rechaza_estados_terminales_y_modulos_desconocidos(self):
        state = base_state()
        with self.assertRaisesRegex(ValueError, "terminal"):
            manager(state)._apply_event("abandon_module", abandon("done"), TS)
        manager(state)._apply_event("abandon_module", abandon("wip"), TS)
        with self.assertRaisesRegex(ValueError, "terminal"):
            manager(state)._apply_event("abandon_module", abandon("wip"), TS)
        with self.assertRaisesRegex(ValueError, "no declarado"):
            manager(state)._apply_event("abandon_module", abandon("fantasma"), TS)

    def test_rechaza_con_bugs_abiertos_y_apunta_a_reasignar(self):
        state = base_state()
        state["_bugs"] = [bug("BUG-171", "wip"), bug("BUG-170", "wip", status="Resuelto")]
        with self.assertRaisesRegex(ValueError, "reassign_bug_module"):
            manager(state)._apply_event("abandon_module", abandon("wip"), TS)
        self.assertEqual(state["module_statuses"]["wip"], "in_progress")

    def test_dropped_es_terminal_para_update_module_status(self):
        state = base_state()
        with self.assertRaisesRegex(ValueError, "abandon_module"):
            manager(state)._apply_event("update_module_status", {"id": "wip", "status": "dropped"}, TS)
        manager(state)._apply_event("abandon_module", abandon("wip"), TS)
        for destino in ("in_progress", "not_started", "completed", "blocked"):
            with self.assertRaises(ValueError):
                manager(state)._apply_event("update_module_status", {"id": "wip", "status": destino}, TS)
        self.assertFalse(_transitions.is_legal("in_progress", "dropped"))
        self.assertIn("dropped", _transitions.VALID_MODULE_STATUSES)

    def test_add_module_no_redeclara_un_modulo_abandonado(self):
        state = base_state()
        manager(state)._apply_event("abandon_module", abandon("wip"), TS)
        with self.assertRaisesRegex(ValueError, "ya está declarado"):
            manager(state)._apply_event("add_module", {"id": "wip"}, TS)


class ReassignBugModuleTests(unittest.TestCase):
    def payload(self, **extra):
        p = {"id": "BUG-171", "module_id": "vivo",
             "reason": "El residuo sigue vivo y pertenece al módulo que lo absorbe"}
        p.update(extra)
        return p

    def test_mueve_el_bug_y_deja_rastro(self):
        state = base_state()
        state["_bugs"] = [bug("BUG-171", "wip")]
        msg = manager(state)._apply_event("reassign_bug_module", self.payload(), TS)
        self.assertIn("BUG-171", msg)
        moved = state["_bugs"][0]
        self.assertEqual(moved["module_id"], "vivo")
        self.assertEqual(moved["status"], "Abierto")
        self.assertEqual(moved["amendments"][-1]["previous"], {"module_id": "wip"})
        self.assertEqual(moved["amendments"][-1]["at"], TS)

    def test_rechazos(self):
        casos = (
            (self.payload(module_id="fantasma"), "no declarado"),
            (self.payload(module_id="wip"), "ya apunta"),
            (self.payload(reason="corto"), "reason"),
            (self.payload(id="BUG-999"), "no encontrado"),
            (self.payload(id="BUG-170"), "Abierto"),
            (self.payload(module_id="muerto"), "dropped"),
        )
        for payload, patron in casos:
            with self.subTest(patron=patron):
                state = base_state()
                state["_bugs"] = [bug("BUG-171", "wip"), bug("BUG-170", "wip", status="Resuelto")]
                state["modules"]["dropped"] = ["muerto"]
                state["module_statuses"]["muerto"] = "dropped"
                with self.assertRaisesRegex(ValueError, patron):
                    manager(state)._apply_event("reassign_bug_module", payload, TS)
                self.assertEqual(state["_bugs"][0]["module_id"], "wip")

    def test_amend_bug_sigue_sin_cambiar_module_id(self):
        state = base_state()
        state["_bugs"] = [bug("BUG-171", "wip")]
        with self.assertRaisesRegex(ValueError, "reassign_bug_module"):
            manager(state)._apply_event("amend_bug", self.payload(), TS)


class EndToEndTests(unittest.TestCase):
    def test_reasignar_abandonar_log_y_reconstruccion_identica(self):
        state = base_state()
        state["_bugs"] = [bug("BUG-171", "wip"), bug("BUG-172", "wip")]
        with entorno(state) as (tmp, mgr):
            with self.assertRaises(ValueError):
                mgr.mutate("abandon_module", abandon("wip"))
            for bug_id in ("BUG-171", "BUG-172"):
                mgr.mutate("reassign_bug_module", {
                    "id": bug_id, "module_id": "vivo",
                    "reason": "Residuo real que pasa al módulo vivo que lo absorbe"})
            mgr.mutate("abandon_module", abandon("wip"))
            live = json.loads((tmp / "_project" / "03_project_state.json").read_text("utf-8"))
            self.assertEqual(live["module_statuses"]["wip"], "dropped")
            self.assertEqual([b["module_id"] for b in live["_bugs"]], ["vivo", "vivo"])
            log = tmp / "_project" / "state_log.jsonl"
            events = [json.loads(line) for line in log.read_text("utf-8").splitlines() if line.strip()]
            actions = [e["action"] for e in events]
            self.assertEqual(actions[-3:], ["reassign_bug_module", "reassign_bug_module", "abandon_module"])
            self.assertEqual(events[-1]["payload"]["evidence"], EVIDENCE)
            stats = replay_state_log.replay(log, tmp / "replayed.json", strict=False,
                                            base_state=copy.deepcopy(state))
            self.assertEqual(stats["errors"], [])
            for key in ("module_statuses", "modules", "_bugs", "module_abandonments"):
                self.assertEqual(stats["state"][key], live[key], key)
            core = (tmp / "_project" / "core.md").read_text("utf-8")
            self.assertIn("1 retirados", core)
            brief = json.loads((tmp / "_project" / "00_session_brief.json").read_text("utf-8"))
            self.assertEqual(brief["module_counts"]["dropped"], 1)
            self.assertEqual(brief["module_counts"]["pending"], 4)


class ConsumersTests(unittest.TestCase):
    def dropped_state(self):
        state = base_state()
        manager(state)._apply_event("abandon_module", abandon("wip"), TS)
        return state

    def test_recuentos_no_lo_cuentan_como_pendiente_ni_completado(self):
        counts = projections.module_counts(self.dropped_state())
        self.assertEqual(counts["completed"], 1)
        self.assertEqual(counts["pending"], 4)
        self.assertEqual(counts["dropped"], 1)
        self.assertIn("1 retirados", projections.module_counts_line(self.dropped_state()))

    def test_proyecto_sin_abandonos_no_cambia(self):
        counts = projections.module_counts(base_state())
        self.assertNotIn("dropped", counts)
        self.assertNotIn("retirados", projections.module_counts_line(base_state()))

    def test_doctor_acepta_el_bucket_dropped(self):
        errors, warnings = [], []
        framework_doctor.validate_state(self.dropped_state(), errors, warnings)
        self.assertFalse([e for e in errors if "modules" in e or "wip" in e], errors)
        self.assertFalse([w for w in warnings if "wip" in w], warnings)

    def test_doctor_rechaza_dropped_con_estado_incoherente(self):
        state = self.dropped_state()
        state["module_statuses"]["wip"] = "in_progress"
        errors, warnings = [], []
        framework_doctor.validate_state(state, errors, warnings)
        self.assertTrue(any("modules.dropped" in e for e in errors), errors)

    def test_gate_de_git_respaldado_por_el_log(self):
        self.assertEqual(validate_transition._status_effect("abandon_module", abandon("wip")),
                         ("wip", "dropped"))
        prev, new = base_state(), self.dropped_state()
        self.assertTrue(validate_transition.transition_problems(prev, new))
        self.assertEqual(validate_transition.transition_problems(
            prev, new, logged_statuses={"wip": "dropped"}), [])

    def test_consultas_por_estado(self):
        result = state_queries.query_state_point(self.dropped_state(), kind="modules_by_status")
        self.assertEqual(result["dropped"], ["wip"])
        point = state_queries.query_state_point(self.dropped_state(), kind="module", module_id="wip")
        self.assertEqual(point["bucket"], "dropped")

    def test_validate_state_del_motor_acepta_dropped(self):
        manager(base_state()).validate_state(self.dropped_state())

    def test_schema_declara_dropped(self):
        schema = json.loads((ROOT / ".agent" / "schemas" / "project_state.schema.json").read_text("utf-8"))
        self.assertIn("dropped", schema["properties"]["module_statuses"]["additionalProperties"]["enum"])
        self.assertIn("dropped", schema["properties"]["modules"]["properties"])
        self.assertNotIn("dropped", schema["properties"]["modules"]["required"])
        self.assertIn("module_abandonments", schema["properties"])

    def test_contratos_documentados(self):
        for action in ("abandon_module", "reassign_bug_module"):
            with self.subTest(action=action):
                self.assertIn(action, state_core.BUILTIN_ACTIONS)
                contract = action_contracts.describe_action(action)
                self.assertIsNotNone(contract)
                self.assertTrue(contract["description"])


def rejection(rej_id, module_id, **extra):
    entry = {"id": rej_id, "module_id": module_id, "reason": "ac_incomplete",
             "timestamp": "2026-10-30T00:00:00Z"}
    entry.update(extra)
    return entry


class ReassignNoSaltaElGateTests(unittest.TestCase):
    """Revisión FAIL 1: reassign_bug_module no puede vaciar el gate de cierre."""

    MOTIVO = "El residuo pertenece al módulo destino según el inventario"
    APROBACION = "aprobación del builder del 2026-10-31"

    def estado_rfv(self, severity="Mayor", affects_ac=True):
        state = base_state()
        b = bug("BUG-001", "rfv", severity=severity)
        b["affects_ac"] = affects_ac
        state["_bugs"] = [b]
        return state

    def test_rechaza_destino_completed_y_el_gate_sigue_viendo_el_bug(self):
        state = self.estado_rfv()
        with self.assertRaisesRegex(ValueError, "reopen_module"):
            manager(state)._apply_event("reassign_bug_module", {
                "id": "BUG-001", "module_id": "done", "reason": self.MOTIVO,
                "evidence": self.APROBACION}, TS)
        self.assertEqual(state["_bugs"][0]["module_id"], "rfv")
        bloqueantes, _ = manager(state)._module_blockers("rfv")
        self.assertEqual([b["id"] for b in bloqueantes], ["BUG-001"])

    def test_bug_bloqueante_exige_evidencia_con_aprobacion(self):
        for severity, affects_ac in (("Crítico", False), ("Mayor", True)):
            for evidence in (None, "corta"):
                with self.subTest(severity=severity, evidence=evidence):
                    state = self.estado_rfv(severity, affects_ac)
                    payload = {"id": "BUG-001", "module_id": "vivo", "reason": self.MOTIVO}
                    if evidence is not None:
                        payload["evidence"] = evidence
                    with self.assertRaisesRegex(ValueError, "evidence"):
                        manager(state)._apply_event("reassign_bug_module", payload, TS)
                    self.assertEqual(state["_bugs"][0]["module_id"], "rfv")
            with self.subTest(severity=severity, evidence="valida"):
                state = self.estado_rfv(severity, affects_ac)
                manager(state)._apply_event("reassign_bug_module", {
                    "id": "BUG-001", "module_id": "vivo", "reason": self.MOTIVO,
                    "evidence": self.APROBACION}, TS)
                moved = state["_bugs"][0]
                self.assertEqual(moved["module_id"], "vivo")
                self.assertEqual(moved["amendments"][-1]["evidence"], self.APROBACION)

    def test_bug_no_bloqueante_no_exige_evidencia(self):
        for severity, affects_ac in (("Menor", False), ("Mayor", False)):
            with self.subTest(severity=severity):
                state = self.estado_rfv(severity, affects_ac)
                manager(state)._apply_event("reassign_bug_module", {
                    "id": "BUG-001", "module_id": "vivo", "reason": self.MOTIVO}, TS)
                self.assertEqual(state["_bugs"][0]["module_id"], "vivo")

    def test_contrato_documenta_evidencia_y_destino_completed(self):
        contract = action_contracts.describe_action("reassign_bug_module")
        self.assertIn("evidence", contract["optional_keys"])
        self.assertIn("evidence", contract["fields"])
        condiciones = " ".join(contract["conditions"])
        self.assertIn("completed", condiciones)
        self.assertIn("reopen_module", condiciones)
        self.assertIn("evidence", condiciones)


class DroppedNoResucitaTests(unittest.TestCase):
    """Revisión FAIL 2: ninguna acción devuelve un `dropped` a la vida."""

    def estado(self):
        state = base_state()
        state["changes"] = [{"id": "CHG-001", "status": "open", "summary": "s",
                             "affected_modules": ["vivo"], "tier": "tier_1",
                             "criteria": ["c"], "opened_at": TS}]
        manager(state)._apply_event("abandon_module", abandon("wip"), TS)
        return state

    def assert_sigue_dropped(self, state):
        self.assertEqual(state["module_statuses"]["wip"], "dropped")
        self.assertEqual(state["modules"]["dropped"], ["wip"])
        for bucket in ("completed", "in_progress", "pending"):
            self.assertNotIn("wip", state["modules"][bucket])

    def test_vias_rechazadas(self):
        vias = (
            ("register_bug", {"module_id": "wip", "severity": "Crítico", "description": "regresión"}),
            ("register_bug", {"module_id": "wip", "severity": "Mayor", "description": "regresión",
                              "affects_ac": True}),
            ("register_bug", {"module_id": "wip", "severity": "Menor", "description": "detalle"}),
            ("register_clarification", {"blocked_scope": "wip", "question": "¿sigue?",
                                        "blocks_start": True}),
            ("escalate_change", {"id": "CHG-001", "module_id": "wip",
                                 "reason": "Supera el umbral de complejidad del cambio ligero"}),
            ("update_module_status", {"id": "wip", "status": "dropped"}),
            ("set_active_module", {"id": "wip"}),
        )
        for action, payload in vias:
            with self.subTest(action=action, payload=payload):
                state = self.estado()
                with self.assertRaisesRegex(ValueError, "dropped"):
                    manager(state)._apply_event(action, payload, TS)
                self.assert_sigue_dropped(state)
                self.assertEqual(state["_bugs"], [])
                self.assertEqual(state["_clarifications"], [])

    def test_register_bug_rechazado_deja_el_doctor_limpio(self):
        with entorno(base_state()) as (tmp, mgr):
            mgr.mutate("abandon_module", abandon("wip"))
            with self.assertRaisesRegex(ValueError, "estabilización"):
                mgr.mutate("register_bug", {"module_id": "wip", "severity": "Crítico",
                                            "description": "regresión x"})
            live = json.loads((tmp / "_project" / "03_project_state.json").read_text("utf-8"))
            self.assert_sigue_dropped(live)
            errors, warnings = [], []
            framework_doctor.validate_state(live, errors, warnings)
            self.assertFalse([e for e in errors if "wip" in e or "dropped" in e], errors)

    def test_restore_closure_rejections_no_reactiva_rechazos_de_un_dropped(self):
        state = base_state()
        state["closure_rejections"] = [rejection("CRR-0001", "rfv"), rejection("CRR-0002", "rfv")]
        manager(state)._apply_event("abandon_module", abandon("rfv"), TS)
        with self.assertRaisesRegex(ValueError, "dropped"):
            manager(state)._apply_event("restore_closure_rejections", {
                "module_id": "rfv", "archive_ids": ["CRR-0001", "CRR-0002"],
                "reason": "reabrir los rechazos del módulo abandonado"}, TS)
        self.assertEqual(state["closure_rejections"], [])


class AbandonArchivaRechazosTests(unittest.TestCase):
    """Revisión FAIL 3: los rechazos del abandonado no bloquean el criterio de parada."""

    def test_archiva_los_rechazos_y_el_criterio_de_parada_se_libera(self):
        state = base_state()
        state["closure_rejections"] = [
            rejection("CRR-0001", "rfv"), rejection("CRR-0002", "rfv"),
            rejection("CRR-0003", "blk"),
        ]
        manager(state)._apply_event("abandon_module", abandon("rfv"), TS)
        self.assertEqual([r["id"] for r in state["closure_rejections"]], ["CRR-0003"])
        archive = state["closure_rejection_archive"]
        self.assertEqual([r["id"] for r in archive], ["CRR-0001", "CRR-0002"])
        for entry in archive:
            self.assertEqual(entry["module_id"], "rfv")
            self.assertEqual(entry["resolved_at"], TS)
            self.assertEqual(entry["archived_at"], TS)
            self.assertIn("abandonado", entry["resolution"])
            self.assertEqual(entry["evidence"], EVIDENCE)
        manager(base_state()).validate_state(state)
        state["closure_rejections"] = []
        manager(state)._apply_event("update_module_status", {"id": "vivo", "status": "in_progress"}, TS)
        self.assertEqual(state["module_statuses"]["vivo"], "in_progress")

    def test_archiva_tambien_los_ya_resueltos_y_los_restaurados(self):
        state = base_state()
        state["closure_rejections"] = [
            rejection("CRR-0001", "rfv", resolved_at=TS, resolution="corregido",
                      evidence="tests verdes del módulo"),
            rejection("CRR-0002", "rfv", resolved_at=TS, resolution="corregido",
                      evidence="tests verdes del módulo", restored_at=TS,
                      restore_reason="la corrección no era real"),
        ]
        manager(state)._apply_event("abandon_module", abandon("rfv"), TS)
        self.assertEqual(state["closure_rejections"], [])
        by_id = {r["id"]: r for r in state["closure_rejection_archive"]}
        self.assertEqual(by_id["CRR-0001"]["resolution"], "corregido")
        self.assertIn("abandonado", by_id["CRR-0002"]["resolution"])
        self.assertNotIn("restored_at", by_id["CRR-0002"])
        manager(base_state()).validate_state(state)

    def test_extremo_a_extremo_con_reconstruccion(self):
        state = base_state()
        with entorno(state) as (tmp, mgr):
            for _ in range(2):
                mgr.mutate("add_closure_rejection", {"module_id": "rfv", "reason": "ac_incomplete"})
            with self.assertRaisesRegex(ValueError, "criterio de parada"):
                mgr.mutate("update_module_status", {"id": "vivo", "status": "in_progress"})
            mgr.mutate("abandon_module", abandon("rfv"))
            mgr.mutate("update_module_status", {"id": "vivo", "status": "in_progress"})
            live = json.loads((tmp / "_project" / "03_project_state.json").read_text("utf-8"))
            self.assertEqual(live["closure_rejections"], [])
            self.assertEqual(len(live["closure_rejection_archive"]), 2)
            log = tmp / "_project" / "state_log.jsonl"
            stats = replay_state_log.replay(log, tmp / "replayed.json", strict=False,
                                            base_state=copy.deepcopy(state))
            self.assertEqual(stats["errors"], [])
            for key in ("closure_rejections", "closure_rejection_archive", "module_statuses"):
                self.assertEqual(stats["state"][key], live[key], key)


class AbandonMenoresTests(unittest.TestCase):
    def test_limpia_next_module_si_apuntaba_al_abandonado(self):
        state = base_state()
        state["session"] = {"next_module": "wip", "next_module_description": "x",
                            "next_action": "seguir"}
        state["work_tiers"] = {"wip": {"tier": "tier_1", "rationale": "r",
                                       "classified_at": TS, "classified_by": "agent"}}
        manager(state)._apply_event("abandon_module", abandon("wip"), TS)
        self.assertNotIn("next_module", state["session"])
        self.assertNotIn("next_module_description", state["session"])
        self.assertEqual(state["session"]["next_action"], "seguir")
        self.assertIn("wip", state["work_tiers"])
        self.assertIsNone(projections.expected_session_brief(state)["next_module"])

    def test_no_toca_next_module_ajeno(self):
        state = base_state()
        state["session"] = {"next_module": "vivo"}
        manager(state)._apply_event("abandon_module", abandon("wip"), TS)
        self.assertEqual(state["session"]["next_module"], "vivo")

    def test_brief_no_pinta_un_dropped_como_siguiente(self):
        state = base_state()
        manager(state)._apply_event("abandon_module", abandon("wip"), TS)
        state["session"] = {"next_module": "wip"}
        state["work_tiers"] = {"wip": {"tier": "tier_1", "rationale": "r",
                                       "classified_at": TS, "classified_by": "agent"}}
        self.assertIsNone(projections.expected_session_brief(state)["next_module"])

    def test_mensajes_veraces_al_abandonar_terminales(self):
        state = base_state()
        with self.assertRaisesRegex(ValueError, "reopen_module"):
            manager(state)._apply_event("abandon_module", abandon("done"), TS)
        manager(state)._apply_event("abandon_module", abandon("wip"), TS)
        with self.assertRaises(ValueError) as ctx:
            manager(state)._apply_event("abandon_module", abandon("wip"), TS)
        self.assertNotIn("reopen_module", str(ctx.exception))
        self.assertIn("dropped", str(ctx.exception))

    def test_contrato_abandon_alineado_con_lo_que_se_comprueba(self):
        contract = action_contracts.describe_action("abandon_module")
        precondiciones = " ".join(contract["preconditions"])
        self.assertIn("evidence", precondiciones)
        self.assertIn("10", precondiciones)
        self.assertNotIn("Cierre aprobado por el builder.", contract["preconditions"])


class AbandonCuatroMenoresTests(unittest.TestCase):
    """Re-revisión PASS con cuatro menores (a-d), cada uno con su test."""

    # a) seed_snapshot no resucita un dropped
    def test_seed_snapshot_no_resiembra_un_modulo_dropped(self):
        state = base_state()
        manager(state)._apply_event("abandon_module", abandon("wip"), TS)
        msg = manager(state)._apply_event("seed_snapshot", {
            "source_commit": "abc1234", "reason": "ancla de prueba",
            "entities": {"modules": {"in_progress": ["wip"], "pending": ["wip"]},
                         "module_statuses": {"wip": "in_progress"}},
        }, TS)
        self.assertEqual(state["module_statuses"]["wip"], "dropped")
        self.assertEqual(state["modules"]["dropped"], ["wip"])
        for bucket in ("completed", "in_progress", "pending"):
            self.assertEqual(state["modules"][bucket].count("wip"), 0, bucket)
        self.assertIn("0 elemento(s)", msg)

    def test_seed_snapshot_sigue_sembrando_lo_ausente(self):
        state = base_state()
        manager(state)._apply_event("abandon_module", abandon("wip"), TS)
        manager(state)._apply_event("seed_snapshot", {
            "source_commit": "abc1234", "reason": "ancla de prueba",
            "entities": {"modules": {"pending": ["nuevo", "wip"]}},
        }, TS)
        self.assertIn("nuevo", state["modules"]["pending"])
        self.assertNotIn("wip", state["modules"]["pending"])

    # b) add_closure_rejection rechaza un dropped
    def test_add_closure_rejection_rechaza_un_modulo_dropped(self):
        state = base_state()
        manager(state)._apply_event("abandon_module", abandon("wip"), TS)
        with self.assertRaises(ValueError) as ctx:
            manager(state)._apply_event(
                "add_closure_rejection", {"module_id": "wip", "reason": "ac_incomplete"}, TS)
        self.assertIn("dropped", str(ctx.exception))
        self.assertIn("resolve_closure_rejections", str(ctx.exception))
        self.assertEqual(state["closure_rejections"], [])
        self.assertEqual(state["module_statuses"]["wip"], "dropped")

    def test_add_closure_rejection_sigue_aceptando_un_modulo_vivo(self):
        state = base_state()
        manager(state)._apply_event("abandon_module", abandon("wip"), TS)
        manager(state)._apply_event(
            "add_closure_rejection", {"module_id": "rfv", "reason": "ac_incomplete"}, TS)
        self.assertEqual([r["module_id"] for r in state["closure_rejections"]], ["rfv"])

    # c) abandon_module rechaza si queda una clarificación bloqueante abierta
    def clarificacion(self, state, scope="wip", **flags):
        manager(state)._apply_event(
            "register_clarification", {"blocked_scope": scope, "question": "¿sigue vigente?", **flags}, TS)
        return state["_clarifications"][-1]["id"]

    def test_rechaza_con_clarificacion_bloqueante_abierta_y_apunta_a_resolverla(self):
        for flag in ("blocks_start", "blocks_implementation", "blocks_closure"):
            with self.subTest(flag=flag):
                state = base_state()
                clar_id = self.clarificacion(state, **{flag: True})
                previo = copy.deepcopy(state)
                with self.assertRaises(ValueError) as ctx:
                    manager(state)._apply_event("abandon_module", abandon("wip"), TS)
                mensaje = str(ctx.exception)
                self.assertIn(clar_id, mensaje)
                self.assertIn("resolve_clarification", mensaje)
                self.assertEqual(state, previo)

    def test_resuelta_la_clarificacion_el_abandono_procede(self):
        state = base_state()
        clar_id = self.clarificacion(state, blocks_implementation=True)
        manager(state)._apply_event(
            "resolve_clarification", {"id": clar_id, "answer": "El módulo se abandona"}, TS)
        manager(state)._apply_event("abandon_module", abandon("wip"), TS)
        self.assertEqual(state["module_statuses"]["wip"], "dropped")

    def test_no_bloquean_el_abandono_las_ajenas_las_resueltas_ni_las_no_bloqueantes(self):
        state = base_state()
        self.clarificacion(state)                                   # abierta, sin blocks_*
        self.clarificacion(state, scope="vivo", blocks_start=True)  # bloqueante, de otro módulo
        resuelta = self.clarificacion(state, blocks_closure=True)
        manager(state)._apply_event("resolve_clarification", {"id": resuelta, "answer": "ok"}, TS)
        manager(state)._apply_event("abandon_module", abandon("wip"), TS)
        self.assertEqual(state["module_statuses"]["wip"], "dropped")

    def test_contrato_abandon_documenta_la_clarificacion_bloqueante(self):
        contract = action_contracts.describe_action("abandon_module")
        condiciones = " ".join(contract["conditions"])
        self.assertIn("resolve_clarification", condiciones)
        self.assertIn("add_closure_rejection", condiciones)

    # d) lo que el código garantiza sobre un dropped, y la salida de import_markdown_entities
    def test_import_markdown_admite_un_bug_sobre_un_dropped_y_reassign_lo_rescata(self):
        state = base_state()
        manager(state)._apply_event("abandon_module", abandon("wip"), TS)
        manager(state)._apply_event("import_markdown_entities", {"bugs": [bug("BUG-900", "wip")]}, TS)
        self.assertEqual(state["_bugs"][0]["module_id"], "wip")
        self.assertEqual(state["module_statuses"]["wip"], "dropped")
        manager(state)._apply_event("reassign_bug_module", {
            "id": "BUG-900", "module_id": "vivo",
            "reason": "El residuo importado sigue vivo y pertenece al módulo que lo absorbe"}, TS)
        self.assertEqual(state["_bugs"][0]["module_id"], "vivo")
        self.assertEqual(state["_bugs"][0]["status"], "Abierto")
        self.assertEqual(state["module_statuses"]["wip"], "dropped")

    def test_adr_079_dice_lo_que_el_codigo_garantiza(self):
        adr = (ROOT / ".agent" / "docs" / "framework_arch_decisions.md").read_text(encoding="utf-8")
        inicio = adr.index("### ADR-079")
        fin = adr.find("\n### ADR-", inicio + 1)
        texto = adr[inicio:fin if fin != -1 else None]
        self.assertNotIn("Ninguna otra acción toca un `dropped`", texto)
        for nombre in ("add_closure_rejection", "import_markdown_entities", "reassign_bug_module",
                       "seed_snapshot", "resolve_clarification", "add_work_tier"):
            self.assertIn(nombre, texto, nombre)


if __name__ == "__main__":
    unittest.main()
