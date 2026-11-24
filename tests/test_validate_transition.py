#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Gate de transición sobre el DIFF de estado, respaldado por el state_log (FW-037).

El gate compara HEAD vs staged. Una sesión que avanza un módulo varios estados por
el camino legal vía el motor (in_progress → ready_for_validation → completed) y
commitea al final colapsa en el diff a `in_progress → completed`, que atómicamente
está prohibido → falso TRANSITION GATE: FAIL que entrena --no-verify.

Solución (regla "last-log-status"): el motor valida is_legal en cada paso antes de
registrar el evento en state_log.jsonl, así que el último status registrado de un
módulo es legal-alcanzable por construcción. Si el staged coincide con ese último
status, el camino fue legal; si no (hand-edit que salta), se aplica la adyacencia
estricta de siempre, que sigue rechazando el salto a `completed` por edición a mano.
"""
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / ".agent" / "scripts"))
import validate_transition as vt  # noqa: E402


def _state(statuses: dict) -> dict:
    return {"module_statuses": statuses}


class TestLogBackedTransition(unittest.TestCase):
    def test_compound_path_backed_by_log_is_legal(self):
        # in_progress → completed en el diff, pero el log confirma que el motor
        # llevó el módulo hasta completed (camino legal multi-paso). FW-037.
        problems = vt.transition_problems(
            _state({"M": "in_progress"}), _state({"M": "completed"}),
            logged_statuses={"M": "completed"},
        )
        self.assertEqual(problems, [], f"camino respaldado por log debe ser legal: {problems}")

    def test_jump_not_backed_by_log_is_illegal(self):
        # Hand-edit: el staged dice completed pero el log no registró ese estado
        # (su último status es in_progress) → el salto debe rechazarse.
        problems = vt.transition_problems(
            _state({"M": "in_progress"}), _state({"M": "completed"}),
            logged_statuses={"M": "in_progress"},
        )
        self.assertEqual(len(problems), 1, "salto no respaldado por log debe rechazarse")

    def test_jump_with_no_log_is_illegal(self):
        # Retrocompatibilidad: sin log (default None) el gate adjudica por
        # adyacencia estricta, como hasta ahora.
        problems = vt.transition_problems(
            _state({"M": "in_progress"}), _state({"M": "completed"}),
        )
        self.assertEqual(len(problems), 1, "sin log, comportamiento estricto intacto")

    def test_legal_single_step_still_passes_without_log(self):
        problems = vt.transition_problems(
            _state({"M": "in_progress"}), _state({"M": "ready_for_validation"}),
        )
        self.assertEqual(problems, [])


class TestStatusEffectResolveBugAndDefer(unittest.TestCase):
    """TL FW-109: resolve_bug/defer_bug_to_tech_debt son condicionales (solo
    desbloquean si no quedan otros bloqueantes) — a diferencia de
    register_bug/reopen_module, no eran mapeados en absoluto en
    _status_effect, así que el último status logueado quedaba congelado en
    'blocked' tras un ciclo legal reopen_module→register_bug→resolve_bug, y
    el TRANSITION GATE bloqueaba commits legítimos posteriores durante horas."""

    def test_resolve_bug_with_engine_signal_is_backed(self):
        # known-good: el motor SÍ desbloqueó (payload trae _status_effect,
        # inyectado por state_core.py solo cuando de verdad ocurrió).
        effect = vt._status_effect(
            "resolve_bug",
            {"id": "BUG-001", "fix": "corregido",
             "_status_effect": {"module": "M12", "status": "in_progress"}},
        )
        self.assertEqual(effect, ("M12", "in_progress"))

    def test_resolve_bug_without_engine_signal_is_not_backed(self):
        # known-bad: sin la señal del motor (el bug se resolvió pero quedan
        # otros bloqueantes, o es un payload fabricado a mano) no se infiere
        # nada a ciegas desde el nombre de la acción.
        effect = vt._status_effect("resolve_bug", {"id": "BUG-001", "fix": "corregido"})
        self.assertIsNone(effect)

    def test_defer_bug_to_tech_debt_with_engine_signal_is_backed(self):
        effect = vt._status_effect(
            "defer_bug_to_tech_debt",
            {"id": "BUG-002", "_status_effect": {"module": "M12", "status": "in_progress"}},
        )
        self.assertEqual(effect, ("M12", "in_progress"))

    def test_end_to_end_reopen_register_resolve_cycle_is_backed(self):
        # Reproduce el escenario exacto de FW-109: reopen_module (completed
        # → in_progress) → register_bug Crítico (→ blocked) → resolve_bug
        # (→ in_progress, sin otros bloqueantes). El diff HEAD→staged salta
        # directo de 'completed' a 'in_progress' — solo el último evento
        # logueado (resolve_bug) debe bastar para respaldarlo.
        sys.path.insert(0, str(ROOT / ".agent" / "scripts"))
        import state_core  # noqa: E402

        m = state_core.StateManager.__new__(state_core.StateManager)
        m.state_data = {
            "_bugs": [],
            "_clarifications": [],
            "modules": {"completed": ["M12"], "in_progress": [], "pending": []},
            "module_statuses": {"M12": "completed"},
            "workflow_receipts": {"approvals": {"M12": {}}, "reviews": {"M12": [{}]}},
        }
        m._apply_event(
            "reopen_module",
            {"id": "M12", "reason": "regresión detectada en producción, hay que arreglarla"},
            "t1",
        )
        m._apply_event(
            "register_bug",
            {"module_id": "M12", "severity": "Crítico", "description": "falla real"},
            "t2",
        )
        resolve_payload = {"id": "BUG-001", "fix": "corregido"}
        m._apply_event("resolve_bug", resolve_payload, "t3")
        self.assertEqual(m.state_data["module_statuses"]["M12"], "in_progress")

        # El payload que quedaría logueado (append_event usa el mismo objeto
        # ya mutado por _apply_event) trae la señal del motor:
        effect = vt._status_effect("resolve_bug", resolve_payload)
        self.assertEqual(effect, ("M12", "in_progress"))

        prev_state = {"module_statuses": {"M12": "completed"}}
        new_state = {"module_statuses": {"M12": "in_progress"}}
        logged = dict([effect])
        problems = vt.transition_problems(prev_state, new_state, logged)
        self.assertEqual(problems, [], f"ciclo legal debe quedar respaldado: {problems}")


class TestLastLoggedStatuses(unittest.TestCase):
    def _write_log(self, lines: list[str]) -> Path:
        tmp = tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False, encoding="utf-8")
        tmp.write("\n".join(lines) + "\n")
        tmp.close()
        self.addCleanup(lambda: Path(tmp.name).unlink(missing_ok=True))
        return Path(tmp.name)

    def test_returns_last_status_per_module(self):
        log = self._write_log([
            json.dumps({"action": "update_module_status", "payload": {"id": "M", "status": "in_progress"}}),
            json.dumps({"action": "set_session_pointer", "payload": {"next_module": "M"}}),
            json.dumps({"action": "update_module_status", "payload": {"id": "M", "status": "ready_for_validation"}}),
            json.dumps({"action": "update_module_status", "payload": {"id": "M", "status": "completed"}}),
            json.dumps({"action": "update_module_status", "payload": {"id": "N", "status": "in_progress"}}),
        ])
        result = vt._last_logged_statuses(log)
        self.assertEqual(result.get("M"), "completed")
        self.assertEqual(result.get("N"), "in_progress")

    def test_ignores_corrupt_lines_and_missing_file(self):
        log = self._write_log([
            "{ no es json",
            json.dumps({"action": "update_module_status", "payload": {"id": "M", "status": "in_progress"}}),
            json.dumps({"action": "update_module_status", "payload": {}}),  # sin id/status
        ])
        result = vt._last_logged_statuses(log)
        self.assertEqual(result.get("M"), "in_progress")
        self.assertEqual(vt._last_logged_statuses(Path("/no/existe/state_log.jsonl")), {})


if __name__ == "__main__":
    unittest.main()


class ModuleBornAfterHeadTests(unittest.TestCase):
    """FW-096 (Research App FW-037 reincidencia 2026-07-11): un módulo
    creado y cerrado vía el motor (add_module → update_module_status →
    complete_module) sin commit intermedio debe pasar el gate — el diff lo
    muestra como 'ausente → completed' pero el log respalda el camino entero."""

    def _log_file(self, entries):
        tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False)
        tmp.write("\n".join(json.dumps(e) for e in entries) + "\n")
        tmp.close()
        return tmp.name

    def test_module_created_and_completed_via_engine_is_legal(self):
        log = self._log_file([
            {"action": "add_module", "payload": {"id": "L1"}, "ts": "t1"},
            {"action": "update_module_status", "payload": {"id": "L1", "status": "in_progress"}, "ts": "t2"},
            {"action": "update_module_status", "payload": {"id": "L1", "status": "ready_for_validation"}, "ts": "t3"},
            {"action": "complete_module", "payload": {"id": "L1"}, "ts": "t4"},
        ])
        logged = vt._last_logged_statuses(log)
        self.assertEqual(logged["L1"], "completed")
        prev = {"module_statuses": {}}
        new = {"module_statuses": {"L1": "completed"}}
        self.assertEqual(vt.transition_problems(prev, new, logged), [])

    def test_module_created_only_by_hand_is_still_illegal(self):
        log = self._log_file([
            {"action": "update_last_step", "payload": {"last_completed_step": "x"}, "ts": "t1"},
        ])
        logged = vt._last_logged_statuses(log)
        prev = {"module_statuses": {}}
        new = {"module_statuses": {"L1": "completed"}}
        problems = vt.transition_problems(prev, new, logged)
        self.assertEqual(len(problems), 1)

    def test_register_bug_blocking_is_backed(self):
        log = self._log_file([
            {"action": "register_bug",
             "payload": {"module_id": "M1", "severity": "Crítico", "affects_ac": False}, "ts": "t1"},
        ])
        logged = vt._last_logged_statuses(log)
        self.assertEqual(logged["M1"], "blocked")

    def test_register_bug_menor_does_not_back_blocked(self):
        log = self._log_file([
            {"action": "register_bug",
             "payload": {"module_id": "M1", "severity": "Menor", "affects_ac": False}, "ts": "t1"},
        ])
        logged = vt._last_logged_statuses(log)
        self.assertNotIn("M1", logged)
