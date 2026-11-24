#!/usr/bin/env python3
"""Tests para replay_state_log.py (A7: recovery tool)."""
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / ".agent" / "scripts"))
import state_core
import replay_state_log


class ReplayStateLogTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.tmp_root = Path(self.tmp.name)

        self.original_state_path = state_core.STATE_FILE_PATH
        self.original_core_path = state_core.CORE_FILE_PATH
        self.original_bugs_path = state_core.BUGS_FILE_PATH
        self.original_clars_path = state_core.CLARIFICATIONS_FILE_PATH
        self.original_decisions_path = state_core.DECISIONS_FILE_PATH
        self.original_session_brief_path = state_core.SESSION_BRIEF_PATH
        self.original_workspace_root = state_core.WORKSPACE_ROOT

        state_core.STATE_FILE_PATH = self.tmp_root / "03_project_state.json"
        state_core.CORE_FILE_PATH = self.tmp_root / "core.md"
        state_core.BUGS_FILE_PATH = self.tmp_root / "06_bug_fixes.md"
        state_core.CLARIFICATIONS_FILE_PATH = self.tmp_root / "07_pending_clarifications.md"
        state_core.DECISIONS_FILE_PATH = self.tmp_root / "03_arch_decisions.md"
        state_core.SESSION_BRIEF_PATH = self.tmp_root / "00_session_brief.json"
        state_core.WORKSPACE_ROOT = self.tmp_root

    def tearDown(self):
        state_core.STATE_FILE_PATH = self.original_state_path
        state_core.CORE_FILE_PATH = self.original_core_path
        state_core.BUGS_FILE_PATH = self.original_bugs_path
        state_core.CLARIFICATIONS_FILE_PATH = self.original_clars_path
        state_core.DECISIONS_FILE_PATH = self.original_decisions_path
        state_core.SESSION_BRIEF_PATH = self.original_session_brief_path
        state_core.WORKSPACE_ROOT = self.original_workspace_root
        self.tmp.cleanup()

    def _write_log(self, entries):
        log = self.tmp_root / "state_log.jsonl"
        log.write_text(
            "\n".join(json.dumps(e) for e in entries) + "\n",
            encoding="utf-8",
        )
        return log

    def test_replay_reconstructs_simple_actions(self):
        """3 acciones simples (set_phase, add_active_mcp, update_last_step) →
        state reconstruido idéntico al que produciría mutate_state."""
        log = self._write_log([
            {"ts": "2026-06-29T10:00:00Z", "action": "set_phase",
             "payload": {"current_phase": "phase_02", "current_step": "arrancando"}},
            {"ts": "2026-06-29T10:01:00Z", "action": "add_active_mcp",
             "payload": {"name": "playwright"}},
            {"ts": "2026-06-29T10:02:00Z", "action": "update_last_step",
             "payload": {"last_completed_step": "Replay OK"}},
        ])

        stats = replay_state_log.replay(log, state_core.STATE_FILE_PATH, strict=False)

        self.assertEqual(stats["applied"], 3)
        self.assertEqual(stats["skipped"], 0)
        self.assertEqual(stats["errors"], [])
        state = stats["state"]
        self.assertEqual(state["current_phase"], "phase_02")
        self.assertEqual(state["current_step"], "arrancando")
        self.assertIn("playwright", state["active_mcps"])
        self.assertEqual(state["last_completed_step"], "Replay OK")

    def test_replay_reproduces_a_module_closure(self):
        """Un cierre registrado en el log debe poder replicarse.

        Regresión de campo (sabotaje controlado 2026-07-24): `complete_module`
        re-ejecutaba los gates de cierre contra el WORKSPACE ACTUAL durante el
        replay. Un cierre legítimo de hace semanas se re-juzgaba con el
        contexto de hoy (ACs en disco, diff hash del workspace) y abortaba el
        replay entero. Efecto medido: el replay estricto fallaba en 5 de los 7
        proyectos reales, dejando la vía de auditoría inservible justo donde
        más falta hace. En replay, el evento YA fue validado cuando ocurrió:
        la historia se reproduce, no se vuelve a juzgar.
        """
        log = self._write_log([
            {"ts": "2026-07-01T10:00:00Z", "action": "add_module",
             "payload": {"id": "M01_core"}},
            {"ts": "2026-07-01T10:01:00Z", "action": "update_module_status",
             "payload": {"id": "M01_core", "status": "in_progress"}},
            {"ts": "2026-07-01T10:02:00Z", "action": "update_module_status",
             "payload": {"id": "M01_core", "status": "ready_for_validation"}},
            {"ts": "2026-07-01T10:03:00Z", "action": "complete_module",
             "payload": {"id": "M01_core"}},
        ])

        stats = replay_state_log.replay(log, state_core.STATE_FILE_PATH, strict=False)

        self.assertEqual(stats["errors"], [], f"el replay no reprodujo el cierre: {stats['errors']}")
        self.assertEqual(stats["applied"], 4)
        self.assertIn("M01_core", stats["state"]["modules"]["completed"])

    def test_replay_no_relitiga_el_harvest_gate(self):
        """FW-201: el HARVEST GATE (`record_harvest` + `closure_gate`) es un
        gate más de `complete_module`, así que hereda la misma exención de
        replay (FW-186) sin código propio: `_is_replay` retorna ANTES de
        evaluar `closure_gate` en absoluto, así que un cierre histórico se
        reproduce aunque el log NUNCA tenga un evento `record_harvest` para
        ese módulo — el gate no se re-litiga contra la ausencia de un dato que
        en su momento pudo vivir solo en la prosa (el gate es posterior a
        cierres ya hechos)."""
        log = self._write_log([
            {"ts": "2026-07-01T10:00:00Z", "action": "add_module",
             "payload": {"id": "M02_core"}},
            {"ts": "2026-07-01T10:01:00Z", "action": "update_module_status",
             "payload": {"id": "M02_core", "status": "in_progress"}},
            {"ts": "2026-07-01T10:02:00Z", "action": "update_module_status",
             "payload": {"id": "M02_core", "status": "ready_for_validation"}},
            {"ts": "2026-07-01T10:03:00Z", "action": "complete_module",
             "payload": {"id": "M02_core"}},
        ])

        stats = replay_state_log.replay(log, state_core.STATE_FILE_PATH, strict=False)

        self.assertEqual(stats["errors"], [], f"el replay no debe re-litigar el harvest gate: {stats['errors']}")
        self.assertIn("M02_core", stats["state"]["modules"]["completed"])
        self.assertNotIn("harvest", stats["state"].get("work_tiers", {}).get("M02_core", {}))

    def test_audit_detects_entities_the_log_never_created(self):
        """Auditoría de respaldo: entidad en el estado sin evento que la creara.

        Es el hueco que midió el sabotaje controlado (2026-07-24): un agente
        que escribe directamente en 03_project_state.json puede inventar
        decisiones, receipts de revisión o aprobaciones, y ni el doctor ni el
        pre-commit lo ven. La bitácora es la única fuente que sabe qué pasó de
        verdad. No hace falta reconstruir el estado entero para comprobarlo —
        basta cruzar los identificadores, lo que además funciona con logs
        incompletos (proyectos migrados), donde el replay no puede terminar.
        """
        log = self._write_log([
            {"ts": "2026-07-01T10:00:00Z", "action": "register_decision",
             "payload": {"id": "ADR-001", "title": "Real", "context": "c",
                         "decision": "d", "status": "accepted"}},
        ])
        estado = {
            "_decisions": [{"id": "ADR-001"}, {"id": "ADR-099"}],
            "workflow_receipts": {"reviews": {"task:inventado": [{"result": "pass"}]}},
        }

        informe = replay_state_log.audit_state_backing(estado, log)

        self.assertEqual(informe["_decisions"], ["ADR-099"])
        self.assertEqual(informe["reviews"], ["task:inventado"])
        self.assertTrue(informe["sin_respaldo"])

    def test_audit_clean_state_reports_nothing(self):
        log = self._write_log([
            {"ts": "2026-07-01T10:00:00Z", "action": "register_decision",
             "payload": {"id": "ADR-001", "title": "Real", "context": "c",
                         "decision": "d", "status": "accepted"}},
        ])
        informe = replay_state_log.audit_state_backing({"_decisions": [{"id": "ADR-001"}]}, log)
        self.assertFalse(informe["sin_respaldo"], informe)

    def test_register_bug_logs_its_own_autoassigned_id(self):
        """BOTÓN FW-114: el id se autoasigna DESPUÉS de leer el payload del
        caller (max_num + 1), así que nunca quedaba en el evento logueado —
        el auditor marcaba TODO bug como "sin respaldo" pese a existir el
        evento real, porque buscaba "id" en un payload que nunca lo traía."""
        m = state_core.StateManager.__new__(state_core.StateManager)
        m.state_data = {"_bugs": [], "_clarifications": []}
        payload = {"module_id": "M12", "severity": "Crítico", "description": "falla real"}
        msg = m._apply_event("register_bug", payload, "2026-08-01T00:00:00Z")
        self.assertIn("BUG-001", msg)
        self.assertEqual(payload.get("id"), "BUG-001",
                          "el payload que se loguea debe traer el id autoasignado")

    def test_audit_finds_registered_bug_backed_by_its_own_event(self):
        log = self._write_log([
            {"ts": "2026-08-01T00:00:00Z", "action": "register_bug",
             "payload": {"module_id": "M12", "severity": "Crítico",
                         "description": "falla real", "id": "BUG-036"}},
        ])
        estado = {
            "_bugs": [{"id": "BUG-036", "module_id": "M12", "description": "falla real"}],
        }
        informe = replay_state_log.audit_state_backing(estado, log)
        self.assertEqual(informe["_bugs"], [], informe)
        self.assertFalse(informe["sin_respaldo"], informe)

    def test_audit_still_flags_a_bug_truly_without_any_event(self):
        # known-bad: un bug fabricado a mano (sin evento real) sigue
        # marcandose -- el fix no ablanda la deteccion real.
        log = self._write_log([
            {"ts": "2026-08-01T00:00:00Z", "action": "register_bug",
             "payload": {"module_id": "M12", "severity": "Crítico",
                         "description": "otro distinto", "id": "BUG-001"}},
        ])
        estado = {
            "_bugs": [{"id": "BUG-999", "module_id": "M12", "description": "fabricado"}],
        }
        informe = replay_state_log.audit_state_backing(estado, log)
        self.assertEqual(informe["_bugs"], ["BUG-999"], informe)

    def test_closure_rejects_a_review_receipt_the_log_never_recorded(self):
        """Cerrar un tier_2 con un receipt de revisión fabricado debe bloquearse.

        Es el vector que el sabotaje controlado (2026-07-24) dejó abierto: un
        agente escribe a mano en `03_project_state.json` un review receipt con
        `result: pass`, y ni el doctor ni el pre-commit lo detectan, porque
        ambos leen el estado y el estado ya dice que la revisión ocurrió. La
        bitácora es la única que sabe que nunca pasó. Este es el punto donde la
        comprobación importa y no genera ruido: el cierre de ESE módulo, no un
        censo del proyecto entero (los proyectos migrados tienen cientos de
        entidades legítimamente anteriores a la bitácora).
        """
        state = {
            "workflow_receipts": {
                "reviews": {
                    "task:M01_core": [{"module": "M01_core", "result": "pass",
                                       "status": "completed"}],
                },
            },
        }
        log = self._write_log([
            {"ts": "2026-07-01T10:00:00Z", "action": "update_last_step",
             "payload": {"last_completed_step": "trabajo real"}},
        ])

        problemas = replay_state_log.unbacked_module_receipts(state, "M01_core", log)

        self.assertTrue(problemas, "un receipt sin evento en la bitácora debe bloquear")
        self.assertIn("task:M01_core", problemas[0])

    def test_closure_accepts_a_receipt_the_log_recorded(self):
        state = {
            "workflow_receipts": {
                "reviews": {"task:M01_core": [{"module": "M01_core", "result": "pass"}]},
            },
        }
        log = self._write_log([
            {"ts": "2026-07-01T10:00:00Z", "action": "record_review_receipt",
             "payload": {"id": "task:M01_core", "result": "pass"}},
        ])

        self.assertEqual(replay_state_log.unbacked_module_receipts(state, "M01_core", log), [])

    def test_log_chain_detects_a_tampered_event(self):
        """Editar un evento ya escrito rompe la cadena de hashes.

        Último eslabón del sabotaje controlado (2026-07-24): con la auditoría
        de respaldo, fabricar evidencia en el estado ya no cuela — pero seguía
        siendo posible escribir directamente en la bitácora, que es justo la
        fuente contra la que se contrasta. Cada evento sella el hash del
        anterior, así que tocar uno invalida todos los posteriores.
        """
        log = self.tmp_root / "state_log.jsonl"
        manager = state_core.StateManager()
        manager.append_event("register_decision", {"id": "ADR-001"}, "ok", "2026-07-24T10:00:00Z")
        manager.append_event("update_last_step", {"last_completed_step": "x"}, "ok", "2026-07-24T10:01:00Z")

        self.assertEqual(state_core.verify_log_chain(log), [], "cadena recién escrita debe ser válida")

        lineas = log.read_text(encoding="utf-8").splitlines()
        primero = json.loads(lineas[0])
        primero["payload"]["id"] = "ADR-999"          # manipulación
        lineas[0] = json.dumps(primero, ensure_ascii=False, sort_keys=True)
        log.write_text("\n".join(lineas) + "\n", encoding="utf-8")

        problemas = state_core.verify_log_chain(log)
        self.assertTrue(problemas, "un evento editado debe romper la cadena")
        self.assertIn("1", problemas[0])

    def test_log_chain_detects_a_deleted_event(self):
        log = self.tmp_root / "state_log.jsonl"
        manager = state_core.StateManager()
        for i in range(3):
            manager.append_event("update_last_step", {"last_completed_step": f"paso {i}"},
                                 "ok", f"2026-07-24T10:0{i}:00Z")
        lineas = log.read_text(encoding="utf-8").splitlines()
        del lineas[1]                                   # borrar un evento intermedio
        log.write_text("\n".join(lineas) + "\n", encoding="utf-8")

        self.assertTrue(state_core.verify_log_chain(log), "un evento borrado debe romper la cadena")

    def test_log_chain_tolerates_events_written_before_the_chain(self):
        """Los 7 proyectos tienen logs previos sin hashes: no se invalidan."""
        log = self.tmp_root / "state_log.jsonl"
        log.write_text(
            json.dumps({"ts": "2026-01-01T00:00:00Z", "action": "set_phase",
                        "payload": {"current_phase": "phase_01"}, "result": "ok"}) + "\n",
            encoding="utf-8",
        )
        manager = state_core.StateManager()
        manager.append_event("update_last_step", {"last_completed_step": "nuevo"}, "ok",
                             "2026-07-24T10:00:00Z")

        self.assertEqual(state_core.verify_log_chain(log), [],
                         "un log con historia previa a la cadena debe seguir siendo válido")

    def test_replay_handles_corrupt_lines(self):
        """Líneas corruptas del log se cuentan como skipped, no detienen la replay."""
        log = self.tmp_root / "state_log.jsonl"
        log.write_text(
            "\n".join([
                '{"ts": "2026-06-29T10:00:00Z", "action": "set_phase", "payload": {"current_phase": "phase_02"}}',
                "",  # línea vacía
                "this is not json",  # JSON inválido
                '{"ts": "2026-06-29T10:01:00Z", "action": "add_active_mcp", "payload": {"name": "playwright"}}',
            ]) + "\n",
            encoding="utf-8",
        )

        stats = replay_state_log.replay(log, state_core.STATE_FILE_PATH, strict=False)

        self.assertEqual(stats["applied"], 2)
        # las líneas en blanco ya no cuentan como skipped (solo eventos
        # ilegibles reales); el json inválido sí.
        self.assertEqual(stats["skipped"], 1)
        self.assertIn("playwright", stats["state"]["active_mcps"])

    def test_replay_tolerates_business_rule_failures(self):
        """A4: add_work_tier para módulo no declarado falla en vivo, pero la
        replay la acepta como histórica. La replay es disaster-recovery, no
        re-validación con reglas actuales."""
        log = self._write_log([
            {"ts": "2026-06-29T10:00:00Z", "action": "add_work_tier",
             "payload": {"id": "mod-que-no-existe", "tier": "tier_1",
                         "rationale": "histórico, no declarado"}},
        ])

        stats = replay_state_log.replay(log, state_core.STATE_FILE_PATH, strict=False)

        self.assertEqual(stats["applied"], 1)
        self.assertEqual(stats["errors"], [])
        self.assertIn("mod-que-no-existe", stats["state"]["work_tiers"])

    def test_replay_dry_run_does_not_write(self):
        """--dry-run: state en memoria, no escrito a disco."""
        log = self._write_log([
            {"ts": "2026-06-29T10:00:00Z", "action": "add_active_mcp",
             "payload": {"name": "playwright"}},
        ])

        stats = replay_state_log.replay(log, state_core.STATE_FILE_PATH, dry_run=True)

        self.assertIsNone(stats["state"])  # dry-run no devuelve state
        self.assertFalse(state_core.STATE_FILE_PATH.exists())

    def test_replay_estricto_aborta_ante_linea_corrupta(self):
        # Triaje 2026-07-09: estricto por defecto — un log dañado durante
        # disaster-recovery no debe producir un estado parcial con rc 0.
        log = self._write_log([
            {"ts": "t1", "action": "update_last_step",
             "payload": {"last_completed_step": "a"}, "result": "ok"},
        ])
        with log.open("a", encoding="utf-8") as f:
            f.write("esto no es json\n")
        with self.assertRaises(replay_state_log.ReplayError):
            replay_state_log.replay(log, state_core.STATE_FILE_PATH)
        # nada escrito
        self.assertFalse(state_core.STATE_FILE_PATH.exists())

    def test_replay_estricto_reconstruye_y_renderiza(self):
        log = self._write_log([
            {"ts": "t1", "action": "update_last_step",
             "payload": {"last_completed_step": "paso uno"}, "result": "ok"},
        ])
        stats = replay_state_log.replay(log, state_core.STATE_FILE_PATH)
        self.assertEqual(stats["applied"], 1)
        self.assertTrue(state_core.STATE_FILE_PATH.exists())
        core = state_core.STATE_FILE_PATH.parent.parent / "_project" / "core.md"
        # el fix del bug AttributeError: las proyecciones se regeneran de verdad
        self.assertTrue(core.exists(), "core.md debe regenerarse tras replay")

    def test_replay_estricto_acepta_id_explicito_consistente_en_register_decision(self):
        """Ronda de revisión final (hallazgo 1): un `state_log.jsonl` histórico
        real puede traer `register_decision` con un `id` explícito que ya
        coincide con el autoasignado (así se descubrió FW-125: el id pedido
        coincidía con el que le tocaba, o el bug daba el id "correcto" por
        casualidad). El replay estricto NO debe abortar ese caso — solo el id
        INCONSISTENTE es el caso peligroso real."""
        log = self._write_log([
            {"ts": "t1", "action": "register_decision",
             "payload": {"id": "ADR-001", "title": "t", "context": "c",
                         "decision": "d", "module_id": "framework"},
             "result": "ok"},
        ])
        stats = replay_state_log.replay(log, state_core.STATE_FILE_PATH)
        self.assertEqual(stats["applied"], 1)
        self.assertEqual(stats["errors"], [])
        self.assertEqual(stats["state"]["_decisions"][0]["id"], "ADR-001")

    def test_replay_raises_on_missing_log(self):
        """Si el log no existe, replay lanza FileNotFoundError claro."""
        missing = self.tmp_root / "no_existe.jsonl"
        with self.assertRaises(FileNotFoundError) as ctx:
            replay_state_log.replay(missing, state_core.STATE_FILE_PATH)
        self.assertIn("no_existe.jsonl", str(ctx.exception))

    def test_replay_writes_to_out_path(self):
        """A7 (fix doble-check): --out path se respeta. Antes el replay siempre
        escribía a state_core.STATE_FILE_PATH sin usar el argumento out_path,
        lo cual rompía disaster recovery a un path temporal."""
        log = self._write_log([
            {"ts": "2026-06-29T10:00:00Z", "action": "set_phase",
             "payload": {"current_phase": "phase_02"}},
        ])
        out = self.tmp_root / "recovered.json"
        # El state_core.STATE_FILE_PATH apunta a OTRO path; el replay debe
        # escribir a `out`, no a STATE_FILE_PATH.
        other = self.tmp_root / "other.json"
        state_core.STATE_FILE_PATH = other

        replay_state_log.replay(log, out, strict=False)

        self.assertTrue(out.exists(), f"replay no escribió a {out}")
        self.assertFalse(other.exists(), f"replay escribió a STATE_FILE_PATH ({other}) en lugar de out")
        data = json.loads(out.read_text(encoding="utf-8"))
        self.assertEqual(data["current_phase"], "phase_02")

    def test_replay_dry_run_does_not_create_out(self):
        """Sanity: --dry-run no debe crear el archivo out."""
        log = self._write_log([
            {"ts": "2026-06-29T10:00:00Z", "action": "add_active_mcp",
             "payload": {"name": "playwright"}},
        ])
        out = self.tmp_root / "should_not_exist.json"

        replay_state_log.replay(log, out, dry_run=True)

        self.assertFalse(out.exists())

    def test_replay_without_base_state_preserves_default_behavior(self):
        """Sin base_state, replay() mantiene comportamiento idéntico (arranca con [REPLAY])."""
        log = self._write_log([
            {"ts": "2026-06-29T10:00:00Z", "action": "set_phase",
             "payload": {"current_phase": "phase_03", "current_step": "step_01"}},
        ])
        out = self.tmp_root / "03_project_state.json"
        stats = replay_state_log.replay(log, out, strict=False, base_state=None)
        self.assertEqual(stats["applied"], 1)
        self.assertEqual(stats["state"]["project"], "[REPLAY]")
        self.assertEqual(stats["state"]["current_phase"], "phase_03")

    def test_replay_with_base_state_applies_over_copy(self):
        """Con base_state, replay() aplica eventos sobre una copia profunda del estado base."""
        base = {
            "project": "MiProyectoBase",
            "framework_mode": "initialized",
            "framework_version": state_core.FRAMEWORK_VERSION,
            "current_phase": "phase_01",
            "current_step": "inicio",
            "last_completed_step": "",
            "stack": {"backend": "python", "frontend": "", "database": "", "auth": ""},
            "dependencies": [],
            "architecture_constraints": [],
            "contracts": {"public_interfaces": {}},
            "modules": {"completed": [], "in_progress": ["mod_a"], "pending": []},
            "module_statuses": {"mod_a": "in_progress"},
            "work_tiers": {},
            "module_specs": {},
            "closure_rejections": [],
            "active_mcps": [],
            "_refs": {},
            "_bugs": [],
            "_clarifications": [],
            "_decisions": [],
            "custom_property_preserved": 42,
        }
        import copy
        base_copy = copy.deepcopy(base)

        log = self._write_log([
            {"ts": "2026-06-29T10:00:00Z", "action": "set_phase",
             "payload": {"current_phase": "phase_02", "current_step": "step_02"}},
        ])
        out = self.tmp_root / "03_project_state.json"
        stats = replay_state_log.replay(log, out, strict=False, force=True, base_state=base)

        self.assertEqual(stats["applied"], 1)
        # Campos preservados del base_state
        self.assertEqual(stats["state"]["project"], "MiProyectoBase")
        self.assertEqual(stats["state"]["custom_property_preserved"], 42)
        # Campo mutado por el evento
        self.assertEqual(stats["state"]["current_phase"], "phase_02")
        # Inmutabilidad: base_state no fue alterado
        self.assertEqual(base["current_phase"], "phase_01")
        self.assertEqual(base, base_copy)


if __name__ == "__main__":
    unittest.main()


class ReplayEntityLossGuardTests(ReplayStateLogTests):
    """FW-094 (Example Tool): replay con --write no debe vaciar un estado real cuando
    el log no contiene la historia completa (estado construido por migración)."""

    RICH_STATE = {
        "_bugs": [{"id": "BUG-001"}, {"id": "BUG-002"}],
        "_decisions": [{"id": "ADR-001"}],
        "_clarifications": [],
        "modules": {"completed": ["m1", "m2"], "in_progress": [], "pending": []},
        "work_tiers": {"m1": {}, "m2": {}},
    }

    def _write_rich_state(self):
        state_core.STATE_FILE_PATH.write_text(
            json.dumps(self.RICH_STATE), encoding="utf-8"
        )

    def test_replay_aborts_when_reconstruction_loses_entities(self):
        self._write_rich_state()
        log = self._write_log([
            {"action": "update_last_step",
             "payload": {"last_completed_step": "solo"}, "ts": "2026-01-01T00:00:00Z"},
        ])
        with self.assertRaises(replay_state_log.ReplayError):
            replay_state_log.replay(log, state_core.STATE_FILE_PATH, strict=False)
        # El estado rico sigue intacto
        current = json.loads(state_core.STATE_FILE_PATH.read_text(encoding="utf-8"))
        self.assertEqual(len(current["_bugs"]), 2)

    def test_force_writes_despite_loss_and_leaves_backup(self):
        self._write_rich_state()
        log = self._write_log([
            {"action": "update_last_step",
             "payload": {"last_completed_step": "solo"}, "ts": "2026-01-01T00:00:00Z"},
        ])
        replay_state_log.replay(log, state_core.STATE_FILE_PATH, strict=False, force=True)
        baks = list(self.tmp_root.glob("03_project_state.json.bak-*"))
        self.assertEqual(len(baks), 1, "debe existir backup del estado previo")
        old = json.loads(baks[0].read_text(encoding="utf-8"))
        self.assertEqual(len(old["_bugs"]), 2, "el backup preserva el estado rico")

    def test_no_guard_when_out_does_not_exist(self):
        log = self._write_log([
            {"action": "update_last_step",
             "payload": {"last_completed_step": "solo"}, "ts": "2026-01-01T00:00:00Z"},
        ])
        out = self.tmp_root / "fresh" / "03_project_state.json"
        stats = replay_state_log.replay(log, out, strict=False)
        self.assertTrue(out.exists())
        self.assertNotIn("backup", stats)

    def test_cli_default_is_dry_run(self):
        self._write_rich_state()
        log = self._write_log([
            {"action": "update_last_step",
             "payload": {"last_completed_step": "solo"}, "ts": "2026-01-01T00:00:00Z"},
        ])
        argv = sys.argv
        try:
            sys.argv = ["replay_state_log.py", "--log", str(log),
                        "--out", str(state_core.STATE_FILE_PATH)]
            rc = replay_state_log.main()
        finally:
            sys.argv = argv
        self.assertEqual(rc, 0)
        current = json.loads(state_core.STATE_FILE_PATH.read_text(encoding="utf-8"))
        self.assertEqual(len(current["_bugs"]), 2,
                         "sin --write el CLI no debe tocar el estado")


class ReplayHistoryToleranceTests(unittest.TestCase):
    """FW-186: en modo replay, `_apply_event` reproduce eventos históricos que
    violan reglas/precondiciones POSTERIORES (bitácora sembrada por migración
    incompleta). Corrupción real sigue abortando."""

    def _mgr(self, is_replay, **extra):
        mgr = state_core.StateManager.__new__(state_core.StateManager)
        mgr._is_replay = is_replay
        base = {
            "module_statuses": {}, "modules": {"completed": [], "in_progress": [], "pending": []},
            "work_tiers": {}, "_decisions": [],
            "workflow_receipts": {"approvals": {}, "reviews": {}, "executions": {}},
        }
        base.update(extra)
        mgr.state_data = base
        return mgr

    def test_replay_tolerates_illegal_module_transition(self):
        mgr = self._mgr(True)
        mgr._apply_event("update_module_status",
                         {"id": "phase_00", "status": "ready_for_validation"}, "2026-08-03T18:41:04Z")
        self.assertEqual(mgr.state_data["module_statuses"]["phase_00"], "ready_for_validation")

    def test_normal_path_still_rejects_illegal_transition(self):
        with self.assertRaisesRegex(ValueError, "Transici"):
            self._mgr(False)._apply_event("update_module_status",
                                          {"id": "M", "status": "ready_for_validation"}, "2026-08-30T00:00:00Z")

    def test_replay_still_rejects_invalid_status(self):
        with self.assertRaises(ValueError):
            self._mgr(True)._apply_event("update_module_status",
                                         {"id": "M", "status": "publicado"}, "2026-08-03T00:00:00Z")

    def test_replay_reproduces_receipt_without_work_tier(self):
        mgr = self._mgr(True)
        mgr._apply_event("record_approval_receipt",
                         {"module": "M17", "_receipt": {"status": "approved", "module": "M17", "fingerprint": "abc"}},
                         "2026-08-03T22:15:36Z")
        self.assertEqual(mgr.state_data["workflow_receipts"]["approvals"]["M17"]["fingerprint"], "abc")

    def test_replay_reproduces_review_without_work_tier(self):
        mgr = self._mgr(True)
        mgr._apply_event("start_independent_review",
                         {"module": "M17", "_receipt": {"status": "pending", "module": "M17", "diff_hash": "d"}},
                         "2026-08-03T22:18:34Z")
        self.assertEqual(mgr.state_data["workflow_receipts"]["reviews"]["M17"][-1]["diff_hash"], "d")

    def test_replay_double_pending_review_reinits_round(self):
        mgr = self._mgr(True)
        for _ in range(2):
            mgr._apply_event("start_independent_review",
                             {"module": "M17", "_receipt": {"status": "pending", "module": "M17"}},
                             "2026-08-03T22:18:34Z")
        self.assertEqual(len(mgr.state_data["workflow_receipts"]["reviews"]["M17"]), 1)

    def test_normal_path_still_requires_work_tier(self):
        with self.assertRaisesRegex(ValueError, "work_tier"):
            self._mgr(False)._apply_event("record_approval_receipt",
                                          {"module": "M17", "approved_by": "builder"}, "2026-08-30T00:00:00Z")

    def test_replay_uses_logged_adr_id_when_counter_behind(self):
        mgr = self._mgr(True, _decisions=[{"id": "ADR-001", "title": "x", "module_id": "framework",
                                           "context": "c", "decision": "d", "alternatives": "", "consequences": ""}])
        mgr._apply_event("register_decision",
                         {"id": "ADR-081", "title": "T", "context": "C", "decision": "D", "module_id": "framework"},
                         "2026-08-17T20:46:56Z")
        self.assertIn("ADR-081", [d["id"] for d in mgr.state_data["_decisions"]])

    def test_normal_path_still_rejects_inconsistent_adr_id(self):
        with self.assertRaisesRegex(ValueError, "autoasign"):
            self._mgr(False)._apply_event("register_decision",
                                          {"id": "ADR-081", "title": "T", "context": "C", "decision": "D",
                                           "module_id": "framework"}, "2026-08-30T00:00:00Z")

    def test_replay_skips_supersede_when_target_absent(self):
        msg = self._mgr(True)._apply_event("supersede_decision",
                                           {"id": "ADR-102", "superseded_by": "ADR-140",
                                            "reason": "cambio documentado hace mucho tiempo"}, "2026-08-21T00:47:10Z")
        self.assertIn("omitido", msg.lower())

    def test_normal_path_still_rejects_supersede_of_missing_adr(self):
        with self.assertRaisesRegex(ValueError, "no existe"):
            self._mgr(False)._apply_event("supersede_decision",
                                          {"id": "ADR-102", "superseded_by": "ADR-140",
                                           "reason": "cambio documentado hace mucho tiempo"}, "2026-08-30T00:00:00Z")



class LegacyCodexReplayTests(unittest.TestCase):
    """5.3.1 L5.5: las migraciones de 5.2.x se retiraron, pero los logs antiguos de Codex se siguen
    reproduciendo (codex_budget_legacy.py y las acciones históricas de state_core.py). Movido tal cual
    desde tests/test_codex_budget_retirement.py, que se borró con su migración."""

    POINTER = {
        "run_id": "run-1", "module_id": "M1", "unit_id": "unit-1", "unit_index": 1, "unit_total": 1,
        "status": "planned", "estimate_ratio": 0.2, "profile_id": "codex-context-v1",
        "checkpoint_path": "_project/codex_execution/run-1.json", "checkpoint_revision": 0,
        "plan_fingerprint": "a" * 64, "last_event_id": None, "updated_at": "2026-09-09T00:00:00Z",
    }

    def test_replay_accepts_legacy_pointer_then_retirement_event(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "_project").mkdir()
            log = root / "_project" / "state_log.jsonl"
            migration_id = "codex-budget-retirement-" + ("b" * 16)
            retire_payload = {
                "migration_id": migration_id,
                "expected_fingerprint": "c" * 64,
                "expected_pointer": self.POINTER,
                "archive_path": f"_project/migrations/codex-budget-retirement/{migration_id}/manifest.json",
                "archive_sha256": "d" * 64,
                "next_action": "Continuar M1",
                "next_command": "/ciclo",
            }
            entries = [
                {"action": "set_codex_execution_pointer", "payload": {"pointer": self.POINTER},
                 "ts": "2026-09-09T00:00:00Z"},
                {"action": "retire_codex_budget", "payload": retire_payload, "ts": "2026-09-09T00:01:00Z"},
            ]
            log.write_text("\n".join(json.dumps(e, sort_keys=True) for e in entries) + "\n", encoding="utf-8")
            result = replay_state_log.replay(log, root / "_project" / "replayed.json", dry_run=False, strict=True)
            self.assertEqual(result["applied"], 2)
            self.assertNotIn("codex_execution", result["state"].get("session", {}))
            self.assertEqual(result["state"]["framework_migrations"][migration_id]["status"], "applied")

if __name__ == "__main__":
    unittest.main()
