#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Modo mantenimiento (`lifecycle: production`, FW-225).

Un producto en producción no necesita la ceremonia de construcción (fases,
scope, ACs de módulo, recibos múltiples) para un ajuste puntual, pero sí
necesita que un cambio riesgoso no se cuele por la ruta ligera. Este módulo
concentra esa frontera:

- `lifecycle_stage` / `escalation_patterns`: lectura del ciclo de vida.
- `open_change_problems` / `close_change_problems`: los gates mecánicos de un
  cambio. Se ejecutan DENTRO del motor (`state_core._apply_event`), igual que
  `closure_gate` para los módulos: la prosa del workflow los describe, el motor
  los hace cumplir.
- `escalation_hits`: el detector que obliga a convertir el cambio en módulo
  cuando el diff toca superficies frágiles (migraciones, auth, esquema,
  contratos). Es la pieza que habría atrapado Example Tool `19ba2de`.

Decisión del builder (2026-09-14): el paso a producción lo decide SOLO el
builder; el motor exige `builder_confirmed: true` en `set_lifecycle` — misma
frontera honesta que `record_approval_receipt`: atribución auditable, no
autenticación criptográfica.
"""
from __future__ import annotations

import fnmatch
import re
from pathlib import Path

STAGES = ("development", "production")
CHANGE_STATUSES = ("open", "closed", "escalated")
LIGHT_TIERS = ("tier_0", "tier_1")
MIN_REASON_CHARS = 20
MAX_CRITERIA = 3

DEFAULT_ESCALATION_PATTERNS = (
    "*migration*",
    "*/auth/*",
    "auth/*",
    "*/auth.*",
    "*schema*",
    "*api_contract.yaml",
    "*data_model.md",
)

# Andamiaje del framework: nunca es superficie de producto ni dispara escalada.
FRAMEWORK_PREFIXES = ("_project/", ".agent/", "_feedback/", ".claude/", ".agents/", ".opencode/")

RC_RE = re.compile(r"\brc\s*=\s*0\b")


def lifecycle_stage(state: dict) -> str:
    lifecycle = state.get("lifecycle")
    if isinstance(lifecycle, dict) and lifecycle.get("stage") in STAGES:
        return lifecycle["stage"]
    return "development"


def escalation_patterns(state: dict) -> list[str]:
    extra = (state.get("lifecycle") or {}).get("escalation_patterns") or []
    patterns = list(DEFAULT_ESCALATION_PATTERNS)
    patterns.extend(p for p in extra if isinstance(p, str) and p not in patterns)
    return patterns


def escalation_hits(paths: list[str], patterns) -> list[str]:
    hits: list[str] = []
    for path in paths:
        if path.startswith(FRAMEWORK_PREFIXES):
            continue
        if any(fnmatch.fnmatch(path, pattern) for pattern in patterns):
            hits.append(path)
    return hits


def changed_paths(root: Path) -> list[str]:
    """Rutas del working tree con cambios, incluidos ficheros nuevos dentro de
    directorios nuevos (`--untracked-files=all`: sin él, git colapsa `src/` y
    una migración recién creada escaparía al detector).

    FW-231: el parseo vive en `validate_no_stubs.git_changed_paths` (`-z`, para
    que un nombre no ASCII no llegue escapado en octal y deje de existir en
    disco); la escalada y el STUB GATE del cierre leen así el mismo diff."""
    import validate_no_stubs as _vs

    return _vs.git_changed_paths(root)


def next_change_id(state: dict) -> str:
    highest = 0
    for change in state.get("changes") or []:
        match = re.match(r"^CHG-(\d+)$", str(change.get("id", "")))
        if match:
            highest = max(highest, int(match.group(1)))
    return f"CHG-{highest + 1:03d}"


def find_change(state: dict, change_id: object) -> dict:
    for change in state.get("changes") or []:
        if change.get("id") == change_id:
            return change
    raise ValueError(f"cambio no registrado: {change_id!r}")


def _declared_modules(state: dict) -> set[str]:
    modules = state.get("modules") or {}
    return set(modules.get("completed", [])) | set(modules.get("in_progress", [])) | set(
        modules.get("pending", [])
    )


def _open_critical_bugs(state: dict, modules) -> list[str]:
    wanted = set(modules)
    return [
        bug["id"] for bug in state.get("_bugs", [])
        if bug.get("status") == "Abierto" and bug.get("severity") == "Crítico"
        and (not wanted or bug.get("module_id") in wanted)
    ]


def set_lifecycle_problems(state: dict, payload: dict) -> list[str]:
    problems: list[str] = []
    stage = payload.get("stage")
    if stage not in STAGES:
        problems.append(f"stage debe ser uno de {STAGES}")
    if payload.get("builder_confirmed") is not True:
        problems.append(
            "el ciclo de vida solo lo cambia el builder: se requiere builder_confirmed=true "
            "tras una petición explícita suya (el agente no lo infiere ni lo propone)"
        )
    reason = payload.get("reason")
    if not isinstance(reason, str) or len(reason.strip()) < MIN_REASON_CHARS:
        problems.append(f"reason obligatoria (≥{MIN_REASON_CHARS} caracteres)")
    patterns = payload.get("escalation_patterns")
    if patterns is not None and (
        not isinstance(patterns, list) or not all(isinstance(p, str) and p.strip() for p in patterns)
    ):
        problems.append("escalation_patterns debe ser lista de strings no vacíos")
    if stage == "production":
        statuses = state.get("module_statuses") or {}
        escalated = {
            c.get("escalated_to") for c in state.get("changes") or [] if c.get("status") == "escalated"
        }
        in_flight = sorted(
            mod for mod, status in statuses.items()
            if status in {"in_progress", "blocked", "ready_for_validation"} and mod not in escalated
        )
        if in_flight:
            problems.append(
                "no se puede pasar a production con módulos en curso: "
                + ", ".join(in_flight)
                + " — cerrarlos o descartarlos antes (decisión del builder)"
            )
        critical = _open_critical_bugs(state, ())
        if critical:
            problems.append("no se puede pasar a production con bugs críticos abiertos: " + ", ".join(critical))
    return problems


BUILDER_USE_STATUSES = ("completed", "ready_for_validation")


def confirm_builder_use_problems(state: dict, payload: dict) -> list[str]:
    """FW-275 (5.2.2 D2): el uso real del builder es una acción suya, no un sello
    del agente. Mismo patrón que `set_lifecycle`: el agente no lo infiere ni lo
    propone. Se admite después del cierre (el uso real llega más tarde)."""
    problems: list[str] = []
    module = payload.get("module")
    if not isinstance(module, str) or not module.strip():
        problems.append("module obligatorio")
    if payload.get("builder_confirmed") is not True:
        problems.append(
            "el uso real solo lo confirma el builder: se requiere builder_confirmed=true "
            "tras una afirmación explícita suya (el agente no lo infiere ni lo propone)"
        )
    evidence = payload.get("evidence")
    if not isinstance(evidence, str) or len(evidence.strip()) < MIN_REASON_CHARS:
        problems.append(
            f"evidence obligatoria (≥{MIN_REASON_CHARS} caracteres): cita lo que dijo el builder"
        )
    if isinstance(module, str) and module.strip():
        status = (state.get("module_statuses") or {}).get(module)
        if status is None and module in ((state.get("modules") or {}).get("completed") or []):
            status = "completed"
        if status not in BUILDER_USE_STATUSES:
            problems.append(
                f"'{module}' está en '{status or 'desconocido'}': el uso real se confirma "
                f"sobre módulos {' o '.join(BUILDER_USE_STATUSES)}"
            )
        if not isinstance((state.get("work_tiers") or {}).get(module), dict):
            problems.append(
                f"'{module}' no tiene entrada en work_tiers: clasificarlo con add_work_tier antes"
            )
    return problems


def open_change_problems(state: dict, payload: dict) -> list[str]:
    problems: list[str] = []
    if lifecycle_stage(state) != "production":
        problems.append(
            "open_change solo existe con lifecycle.stage=production; en desarrollo se trabaja por módulos"
        )
    open_ids = [c["id"] for c in state.get("changes") or [] if c.get("status") == "open"]
    if open_ids:
        problems.append(f"ya hay un cambio abierto ({open_ids[0]}): ciérralo o escálalo antes")
    summary = payload.get("summary")
    if not isinstance(summary, str) or not summary.strip():
        problems.append("summary obligatorio")
    tier = payload.get("tier")
    if tier == "tier_2":
        problems.append(
            "un cambio tier_2 no entra por la ruta ligera: escala a módulo "
            "(add_module + flujo completo de /ciclo)"
        )
    elif tier not in LIGHT_TIERS:
        problems.append(f"tier debe ser uno de {LIGHT_TIERS}")
    criteria = payload.get("criteria")
    if (
        not isinstance(criteria, list)
        or not 1 <= len(criteria) <= MAX_CRITERIA
        or not all(isinstance(c, str) and c.strip() for c in criteria)
    ):
        problems.append(f"criteria debe tener entre 1 y {MAX_CRITERIA} criterios no vacíos")
    affected = payload.get("affected_modules", [])
    if not isinstance(affected, list) or not all(isinstance(m, str) for m in affected):
        problems.append("affected_modules debe ser lista de strings")
    else:
        unknown = sorted(set(affected) - _declared_modules(state))
        if unknown:
            problems.append("módulos afectados no declarados: " + ", ".join(unknown))
    return problems


def close_change_problems(state: dict, payload: dict, root: Path) -> list[str]:
    import validate_acceptance as _va
    import validate_no_stubs as _vs

    try:
        change = find_change(state, payload.get("id"))
    except ValueError as exc:
        return [str(exc)]
    if change.get("status") != "open":
        return [f"{change['id']} no está abierto (estado: {change.get('status')})"]

    problems: list[str] = []
    evidence = payload.get("evidence")
    if not isinstance(evidence, dict):
        evidence = {}
    for criterion in change.get("criteria", []):
        text = evidence.get(criterion)
        if not isinstance(text, str) or not text.strip():
            problems.append(f"criterio sin evidencia: {criterion}")
            continue
        body = f"- [x] {criterion} — evidencia: {text}"
        problem = _va.evidence_problem(body)
        if problem:
            problems.append(f"criterio '{criterion}': {problem}")
        missing = _va.missing_artifacts(body, root)
        if missing:
            problems.append(f"criterio '{criterion}': evidencia cita rutas inexistentes: {', '.join(missing)}")

    tests = payload.get("tests")
    if not isinstance(tests, str) or not RC_RE.search(tests):
        problems.append("tests debe citar el comando ejecutado y su resultado 'rc=0'")
    changelog = payload.get("changelog")
    if not isinstance(changelog, str) or not changelog.strip():
        problems.append("changelog obligatorio (una línea en lenguaje del builder)")

    critical = _open_critical_bugs(state, change.get("affected_modules") or ["__ninguno__"])
    if critical:
        problems.append("bugs críticos abiertos en los módulos afectados: " + ", ".join(critical))

    paths = changed_paths(root)
    hits = escalation_hits(paths, escalation_patterns(state))
    if hits:
        problems.append(
            "el diff toca superficies frágiles (" + ", ".join(hits) + "): este cambio no puede "
            "cerrar por la ruta ligera — usar escalate_change y seguir el flujo completo de módulo"
        )
    stub_findings: list[str] = []
    for rel in paths:
        if rel.startswith(FRAMEWORK_PREFIXES):
            continue
        path = root / rel
        # is_source_file evalúa las partes de la ruta: con la absoluta, un
        # directorio padre llamado p. ej. `tmp` o `tests` excluiría el fichero.
        if path.is_file() and _vs.is_source_file(Path(rel)):
            stub_findings.extend(_vs.scan_file(path, root, only_lines=_vs.added_lines(path, root)))
    if stub_findings:
        problems.append("STUB GATE: " + "; ".join(stub_findings))
    return problems


def escalate_change_problems(state: dict, payload: dict) -> list[str]:
    try:
        change = find_change(state, payload.get("id"))
    except ValueError as exc:
        return [str(exc)]
    problems: list[str] = []
    if change.get("status") != "open":
        problems.append(f"{change['id']} no está abierto (estado: {change.get('status')})")
    module_id = payload.get("module_id")
    if not isinstance(module_id, str) or not module_id.strip():
        problems.append("module_id obligatorio")
    elif module_id in _declared_modules(state):
        problems.append(f"el módulo '{module_id}' ya está declarado")
    elif module_id.strip() in (state.get("modules") or {}).get("dropped", []) or (
        (state.get("module_statuses") or {}).get(module_id.strip()) == "dropped"
    ):
        # PA-FW-037 (revisión 5.3.3): escalar a un id abandonado lo resucitaba.
        problems.append(
            f"el módulo '{module_id}' está dropped (abandonado, terminal); escala a un id nuevo"
        )
    reason = payload.get("reason")
    if not isinstance(reason, str) or len(reason.strip()) < MIN_REASON_CHARS:
        problems.append(f"reason obligatoria (≥{MIN_REASON_CHARS} caracteres)")
    return problems


def validate_lifecycle_state(data: dict) -> None:
    """Invariantes estructurales; los usa `StateManager.validate_state`."""
    lifecycle = data.get("lifecycle")
    if lifecycle is not None:
        if not isinstance(lifecycle, dict) or lifecycle.get("stage") not in STAGES:
            raise ValueError(f"lifecycle.stage debe ser uno de {STAGES}")
        patterns = lifecycle.get("escalation_patterns")
        if patterns is not None and not (
            isinstance(patterns, list) and all(isinstance(p, str) for p in patterns)
        ):
            raise ValueError("lifecycle.escalation_patterns debe ser lista de strings")
    changes = data.get("changes")
    if changes is None:
        return
    if not isinstance(changes, list):
        raise ValueError("changes debe ser lista")
    ids: set[str] = set()
    open_count = 0
    for change in changes:
        if not isinstance(change, dict) or not all(
            k in change for k in ("id", "summary", "tier", "criteria", "status", "opened_at")
        ):
            raise ValueError("changes: entrada incompleta")
        if change["id"] in ids:
            raise ValueError(f"changes: id duplicado {change['id']}")
        ids.add(change["id"])
        if change["status"] not in CHANGE_STATUSES:
            raise ValueError(f"changes.{change['id']}.status inválido")
        if change["tier"] not in LIGHT_TIERS:
            raise ValueError(f"changes.{change['id']}.tier inválido")
        open_count += change["status"] == "open"
    if open_count > 1:
        raise ValueError("changes: solo puede haber un cambio abierto")
