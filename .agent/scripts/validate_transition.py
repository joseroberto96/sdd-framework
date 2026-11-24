#!/usr/bin/env python3
"""Module state-transition legality gate, adjudicated from a state DIFF.

`framework_doctor.py` validates a single `03_project_state.json` snapshot: a
state that is internally consistent passes, regardless of how it got there. So
the transitions table in `.agent/rules/architecture_constraints.md` (which
forbids e.g. `blocked → completed` and `in_progress → completed`) was enforced
only by the prose of `/cierre-modulo`. A hand-edit or a faithless agent could
commit an illegal jump to `completed` and the doctor would stay green.

This script closes that gap. Given the PREVIOUS and the NEW state, it compares
`module_statuses[module]` per module and rejects any transition not authorized
by the canonical table. It is meant to run in pre-commit / CI on the diff
between `HEAD:_project/03_project_state.json` and the staged version, so an
illegal transition blocks the commit — the "closure integrity check when a
module reaches completed".

Authorized transitions (architecture_constraints.md → "Invariantes de Estado"):
    not_started → in_progress
    in_progress → blocked
    blocked → in_progress
    in_progress → ready_for_validation
    ready_for_validation → completed
Plus, kept legal here to avoid false positives on documented flows:
    <same> → <same>                (no-op; status unchanged across commits)
    ready_for_validation → in_progress   (re-open on EJECUTABLE GATE 'fallida', cierre-modulo step 3.8)
    <absent> → not_started | in_progress | blocked   (a newly-tracked module)

Everything else is rejected (architecture_constraints.md: "Cualquier transición
no listada arriba" está prohibida). In particular, reaching `completed` is legal
ONLY from `ready_for_validation` (or staying `completed`); reopening a
`completed` module is not an authorized transition.

What it does NOT decide (the ceiling): whether the closure that produced
`ready_for_validation → completed` actually satisfied the ACs/specs/security
gates (that is `validate_closure.py` + model judgment). This script only checks
that the *path* between states is legal.

Usage:
    python3 .agent/scripts/validate_transition.py <prev_state.json> <new_state.json> [<state_log.jsonl>]
The optional state_log.jsonl lets a legal multi-step path committed at once pass
without reopening the illegal-jump hole (FW-037); omit it for strict single-step.
Exit codes: 0 = all transitions legal, 1 = illegal transition(s), 2 = usage/file error.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _transitions import LEGAL_PREV, VALID_MODULE_STATUSES


def _statuses(state: dict) -> dict:
    statuses = state.get("module_statuses")
    return statuses if isinstance(statuses, dict) else {}


def transition_problems(
    prev_state: dict, new_state: dict, logged_statuses: dict | None = None
) -> list[str]:
    """List of illegal module transitions from prev_state to new_state (empty == legal).

    A commit can fold several legal mutations into one diff (FW-037): a session that
    walks a module in_progress → ready_for_validation → completed via the engine and
    commits at the end produces a diff in_progress → completed, illegal as an atomic
    jump. `logged_statuses` maps a module to the LAST status the engine recorded for
    it in state_log.jsonl; since the engine validates is_legal on every mutation
    before logging, that status is reachable by a legal path. A staged status that
    matches it is therefore legal. Every other case — no log entry for the module, or
    a staged status the log does not back (a hand-edit that skipped the engine) —
    falls back to strict single-step adjacency, which still rejects an illegal jump
    to `completed`. `logged_statuses=None` reproduces the historical strict behavior.
    """
    prev = _statuses(prev_state)
    new = _statuses(new_state)
    logged = logged_statuses or {}
    problems: list[str] = []
    for module, new_status in new.items():
        if new_status not in VALID_MODULE_STATUSES:
            # Invalid status values are the doctor's concern, not a transition.
            continue
        if logged.get(module) == new_status:
            # Backed by the state_log: the engine recorded a legal path to this status.
            continue
        prev_status = prev.get(module)
        if prev_status is not None and prev_status not in VALID_MODULE_STATUSES:
            prev_status = None  # treat an unknown previous value as "fresh"
        if prev_status not in LEGAL_PREV[new_status]:
            origin = prev_status if prev_status is not None else "ausente"
            problems.append(
                f"transición ilegal para '{module}': {origin} → {new_status} "
                f"(no autorizada por la tabla de transiciones de architecture_constraints.md "
                f"ni respaldada por state_log.jsonl)"
            )
    return problems


def _status_effect(action: str, payload: dict) -> tuple[str, str] | None:
    """(module_id, status) que el motor dejó registrado para esta acción, o None.

    FW-096 (Research App FW-037, reincidencia 2026-07-11): el respaldo por
    log solo leía `update_module_status`, así que un módulo creado y cerrado por
    el camino canónico (`add_module` → … → `complete_module`) sin commit
    intermedio aparecía como "ausente → completed" y bloqueaba el commit de
    seguridad de /gycd. El motor loguea DESPUÉS de aplicar con éxito, así que
    cada evento aquí implica un estado legalmente alcanzado.
    Se omiten a propósito los efectos condicionales que dependen del estado en
    ese momento (p. ej. `resolve_bug` solo des-bloquea si no quedan bloqueantes):
    inferirlos desde el log podría bendecir estados que el motor nunca escribió.
    Igualmente, las acciones agregadas de ingestión de entidades (como
    `import_markdown_entities`) se omiten deliberadamente: no mutan
    `module_statuses` (la contención de bugs/clarificaciones bloqueantes
    opera dinámicamente en query-time vía state_queries._blocking_items).
    """
    if action == "update_module_status":
        mod_id, status = payload.get("id"), payload.get("status")
        if isinstance(mod_id, str) and isinstance(status, str):
            return (mod_id, status)
        return None
    if action == "add_module":
        mod_id = payload.get("id")
        if isinstance(mod_id, str):
            return (mod_id.strip(), "not_started")
        return None
    if action == "escalate_change":
        # FW-225: escalar un cambio de mantenimiento crea el módulo directamente
        # en in_progress (∅ → in_progress), respaldado por este evento.
        mod_id = payload.get("module_id")
        if isinstance(mod_id, str):
            return (mod_id.strip(), "in_progress")
        return None
    if action == "complete_module":
        mod_id = payload.get("id")
        if isinstance(mod_id, str):
            return (mod_id.strip(), "completed")
        return None
    if action == "abandon_module":
        # PA-FW-037 (5.3.3): `dropped` no tiene previo legal en LEGAL_PREV; solo
        # este evento lo respalda.
        mod_id = payload.get("id")
        if isinstance(mod_id, str):
            return (mod_id.strip(), "dropped")
        return None
    if action == "reopen_module":
        # TL FW-006: reapertura explícita completed → in_progress con reason;
        # el motor la valida antes de loguearla, así que el diff resultante
        # (completed → in_progress) está respaldado por un camino legal.
        mod_id = payload.get("id")
        if isinstance(mod_id, str):
            return (mod_id.strip(), "in_progress")
        return None
    if action == "register_bug":
        # El motor bloquea el módulo de forma determinista según el payload.
        mod_id = payload.get("module_id")
        severity = payload.get("severity")
        affects = payload.get("affects_ac")
        if payload.get("_keeps_completed") is True:  # FW-300: el módulo sigue completed
            return None
        if isinstance(mod_id, str) and (
            severity == "Crítico" or (severity == "Mayor" and affects is True)
        ):
            return (mod_id, "blocked")
        return None
    if action == "register_clarification":
        effect = payload.get("_status_effect")
        if (
            isinstance(effect, dict)
            and isinstance(effect.get("module"), str)
            and effect.get("status") in VALID_MODULE_STATUSES
        ):
            return (effect["module"], effect["status"])
        mod_id = payload.get("blocked_scope")
        b_start = payload.get("blocks_start", False)
        b_impl = payload.get("blocks_implementation", False)
        b_close = payload.get("blocks_closure", False)
        if isinstance(mod_id, str) and (b_start or b_impl or b_close):
            return (mod_id, "blocked")
        return None
    if action in ("resolve_bug", "defer_bug_to_tech_debt", "resolve_clarification"):
        # TL FW-109 / TL FW-235: a diferencia de register_bug/reopen_module, el efecto de
        # estas acciones es CONDICIONAL (solo desbloquean si no quedan
        # otros bugs/clarificaciones bloqueantes) — no se puede derivar de
        # forma determinista desde action+payload solos, y antes de este fix
        # no se intentaba, así que un ciclo legal reopen_module→register_bug→
        # resolve_bug dejaba el último status logueado congelado en 'blocked'
        # y el TRANSITION GATE bloqueaba commits legítimos posteriores.
        # state_core.py anota el resultado real en payload["_status_effect"]
        # (mismo patrón que "_receipt") SOLO cuando el motor de verdad
        # desbloqueó el módulo — nunca se infiere a ciegas desde el nombre de
        # la acción, así que un resolve_bug/resolve_clarification que NO desbloqueó
        # (quedan otros bloqueantes) sigue sin backing, correctamente.
        effect = payload.get("_status_effect")
        if (
            isinstance(effect, dict)
            and isinstance(effect.get("module"), str)
            and effect.get("status") in VALID_MODULE_STATUSES
        ):
            return (effect["module"], effect["status"])
        return None
    return None


def _last_logged_statuses(log_path) -> dict:
    """Last status the engine recorded per module in state_log.jsonl.

    El motor valida cada mutación antes de loguearla, así que el último estado
    registrado es alcanzable por camino legal por construcción (FW-037), sea
    cual sea la acción que lo produjo (FW-096). Missing file or corrupt lines
    are ignored — this never raises, so a missing/unreadable log degrades to
    strict adjacency.
    """
    result: dict[str, str] = {}
    path = Path(log_path)
    if not path.exists():
        return result
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return result
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(entry, dict):
            continue
        effect = _status_effect(entry.get("action", ""), entry.get("payload") or {})
        if effect is not None:
            result[effect[0]] = effect[1]
    return result


def _load(path_str: str) -> dict:
    return json.loads(Path(path_str).read_text(encoding="utf-8"))


def main(argv: list[str]) -> int:
    if len(argv) not in (3, 4):
        print("uso: validate_transition.py <prev_state.json> <new_state.json> [<state_log.jsonl>]")
        return 2
    try:
        prev_state = _load(argv[1])
        new_state = _load(argv[2])
    except FileNotFoundError as exc:
        print(f"FALTA: {exc}")
        return 2
    except json.JSONDecodeError as exc:
        print(f"JSON inválido: {exc}")
        return 2
    logged = _last_logged_statuses(argv[3]) if len(argv) == 4 else None
    problems = transition_problems(prev_state, new_state, logged)
    if problems:
        print("TRANSITION GATE: FAIL")
        for problem in problems:
            print(f"- {problem}")
        return 1
    print("TRANSITION GATE: OK")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
