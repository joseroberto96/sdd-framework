#!/usr/bin/env python3
"""Closure-decision gate for /cierre-modulo, extracted as an executable check.

Several closure gates of `.agent/workflows/cierre-modulo.md` are deterministic
yet lived only in prose, so an environment that does not faithfully execute the
workflow could close a module the rules forbid. This script encodes the
machine-decidable subset so /cierre-modulo, pre-commit or CI can adjudicate the
block/allow DECISION mechanically (enforcement hardening — the gate becomes a
script, not a sentence). It mirrors the extraction pattern of
`validate_acceptance.py`.

It adjudicates the single module being closed (received as argument), exactly
like `validate_acceptance.py` only inspects the module under closure. That makes
the policy "enforce on close, going forward" — already-closed legacy modules are
never re-evaluated retroactively.

Rules encoded (each line cites its prose source in cierre-modulo.md):
- TIER GATE (step 0 / GUARDRAILS "Graduación de Rigor por Tier"):
  - the module MUST have a `work_tiers[module]` entry → else block;
  - `tier_0` cannot close as a project module → block;
  - `tier_2` MUST declare `requires_second_review: true`, retain a valid builder
    APPROVE_RECEIPT, AND a passed independent review receipt bound to the
    current diff → else block.
- SPEC GATE presence (step 3.7, ADR-013):
  - the module MUST have a `module_specs[module]` entry with valid `data_model`
    and `api_contract` status; `n/a` requires non-empty `justification`;
    `classified_by` must be a valid classifier → else block.
- DEATH CRITERION (step 5):
  - >= 2 `closure_rejections` for this module (every reason is a hard gate) →
    block; recommend opening a "Estabilización" module instead of closing.
- EXECUTOR CAPABILITY (GUARDRAILS "Graduación por capacidad del ejecutor"):
  - if `execution_profile.capability_tier == "limited"`, the module MUST declare
    `requires_second_review: true` on EVERY tier (not just tier_2) → else block.

AC COVERAGE (CLI only, step 1.c): `main()` additionally reads
08_module_acceptance.md and blocks when the module's AC coverage is < 100%
(`k/n` marked `[x]`). This is the machine-decidable subset of cierre-modulo step
1.c: a freshly opened module with 0 checked ACs is no longer reported as OK. The
pure `closure_gate(state, module)` function stays state-only so its tests remain
isolated; coverage is adjudicated in `main()`.

What it does NOT decide (the ceiling — model judgment, not machine-decidable):
adequacy of the PRESERVAR inventory, quality of the second-pass review, semantic
correctness of an AC, completeness of spec *content*, or the real behavior of
the EJECUTABLE GATE. Those stay in prose and human/agent judgment. The success
message names this ceiling so OK is never read as a blanket closure approval.

Usage:
    python3 .agent/scripts/validate_closure.py "<module_id>"
    python3 .agent/scripts/validate_closure.py --calidad <quality.yaml>   # L7.4: solo aviso, rc 0
Exit codes: 0 = gate OK, 1 = blocked (lists reasons), 2 = usage/file error.
"""

from __future__ import annotations

import json
import re
import shlex
import subprocess
import sys
from collections import Counter
from pathlib import Path

# Reuse the canonical AC parser from validate_acceptance (same dir) instead of
# duplicating regexes between gates.
sys.path.insert(0, str(Path(__file__).resolve().parent))
import validate_acceptance as _va  # noqa: E402
import workflow_receipts as _wr  # noqa: E402
import state_core as _state_core  # noqa: E402


ROOT = Path(__file__).resolve().parents[2]
STATE = ROOT / "_project" / "03_project_state.json"
ACCEPTANCE = ROOT / "_project" / "08_module_acceptance.md"

DEATH_THRESHOLD = 2
VALID_WORK_TIERS = {"tier_0", "tier_1", "tier_2"}
VALID_CAPABILITY_TIERS = {"strong", "standard", "limited"}
VALID_SPEC_STATUS = {"required", "n/a", "done"}
VALID_SPEC_CLASSIFIER = {"scope-siguiente", "enriquecer-ticket", "migrar-proyecto", "manual"}
# FW-147: literal exacto que /cierre-modulo paso 6 escribe en la aceptación.
# FW-275 (5.2.2 D1): el agente firma lo que verificó él; el sello viejo decía
# «con cliente» sin que el cliente interviniera. El literal viejo se sigue
# aceptando (módulos ya cerrados) pero ningún workflow lo vuelve a escribir. El
# uso real del builder es otra acción (`confirm_builder_use`), no un sello.
AGENT_SEAL = "✅ Verificado por el agente (código + integración)"
LEGACY_CLIENT_SEAL = "✅ Verificado con cliente"
CLIENT_SEAL = AGENT_SEAL  # alias de compatibilidad para importadores
ACCEPTED_SEALS = (AGENT_SEAL, LEGACY_CLIENT_SEAL)
# Every closure_rejection reason is a hard gate (architecture_constraints.md:
# "Los rechazos por gates duros no se convierten en deuda técnica").
VALID_CLOSURE_REASONS = {
    "critical_bug_exists",
    "clar_pending",
    "ac_incomplete",
    "ac_evidence_missing",
    "module_specs_missing",
    "data_model_missing",
    "api_contract_missing",
    "data_safety_missing",
    "domain_sweep_missing",
    "expert_audit_missing",
}
_DIAGNOSTIC = re.compile(r"^(\S+?):\d+(?::\d+)?:")  # L7: ruta:línea[:col]: mensaje


def tier_problems(state: dict, module: str) -> list[str]:
    """TIER GATE — cierre-modulo step 0."""
    work_tiers = state.get("work_tiers")
    entry = work_tiers.get(module) if isinstance(work_tiers, dict) else None
    if not isinstance(entry, dict):
        return [f"sin entrada work_tiers['{module}']: clasificar tier antes de cerrar (paso 0)"]
    problems: list[str] = []
    tier = entry.get("tier")
    if tier not in VALID_WORK_TIERS:
        problems.append(f"work_tiers['{module}'].tier inválido: {tier!r}")
    if tier == "tier_0":
        problems.append(f"work_tiers['{module}'] es tier_0: un módulo efímero no puede cerrarse; reclasificar a tier_1/tier_2")
    if tier == "tier_2" and entry.get("requires_second_review") is not True:
        problems.append(f"work_tiers['{module}'] es tier_2 y debe declarar requires_second_review: true (ADR + rollback + revisión de segundo paso)")
    return problems


def spec_problems(state: dict, module: str) -> list[str]:
    """SPEC GATE presence — cierre-modulo step 3.7 (ADR-013)."""
    module_specs = state.get("module_specs")
    if not isinstance(module_specs, dict):
        return [f"module_specs ausente o no es objeto: clasificar module_specs['{module}'] con /scope-siguiente o /enriquecer-ticket (SPEC GATE)"]
    entry = module_specs.get(module)
    if not isinstance(entry, dict):
        return [f"sin entrada module_specs['{module}']: ejecutar /scope-siguiente o /enriquecer-ticket antes de cerrar (SPEC GATE)"]
    problems: list[str] = []
    data_model = entry.get("data_model")
    api_contract = entry.get("api_contract")
    if data_model not in VALID_SPEC_STATUS:
        problems.append(f"module_specs['{module}'].data_model inválido: {data_model!r}")
    if api_contract not in VALID_SPEC_STATUS:
        problems.append(f"module_specs['{module}'].api_contract inválido: {api_contract!r}")
    if data_model == "n/a" or api_contract == "n/a":
        justification = entry.get("justification")
        if not (isinstance(justification, str) and justification.strip()):
            problems.append(f"module_specs['{module}'] marca 'n/a' sin justification")
    if entry.get("classified_by") not in VALID_SPEC_CLASSIFIER:
        problems.append(f"module_specs['{module}'].classified_by inválido: {entry.get('classified_by')!r}")
    # FW-148: tercera spec, aditiva. Ausente = entrada anterior a 4.21.0, no se
    # juzga (los módulos ya clasificados no se reabren retroactivamente). Presente
    # = mismo contrato que las otras dos: 'required' bloquea, 'n/a' exige razón.
    integration_real = entry.get("integration_real")
    if integration_real is not None:
        if integration_real not in VALID_SPEC_STATUS:
            problems.append(
                f"module_specs['{module}'].integration_real inválido: {integration_real!r}"
            )
        elif integration_real == "n/a" and not str(entry.get("justification") or "").strip():
            problems.append(
                f"module_specs['{module}'].integration_real es 'n/a' sin justification"
            )
    return problems


def data_safety_problems(state: dict, module: str) -> list[str]:
    """DATA SAFETY GATE (FW-083, cierre-modulo paso 3.6). Espejo de
    harvest_problems: declaración explícita, no inferencia. El agente marca
    handles_real_data=true en el paso 0 (add_work_tier) cuando el módulo toca
    PII más allá de leerla o escribe datos reales (GUARDRAILS clasificador);
    el motor exige el sello de record_data_safety_check antes de cerrar."""
    entry = state.get("work_tiers", {}).get(module)
    if not isinstance(entry, dict) or entry.get("handles_real_data") is not True:
        return []
    ds = entry.get("data_safety")
    if not isinstance(ds, dict):
        return [
            f"'{module}' declara handles_real_data=true sin verificación de "
            "seguridad de datos — llamar a record_data_safety_check con "
            "target_env, backup_verified y evidence antes de cerrar (FW-083)"
        ]
    problems: list[str] = []
    if ds.get("target_env") not in {"dev", "staging", "prod"}:
        problems.append(f"data_safety['{module}'].target_env inválido: {ds.get('target_env')!r}")
    if ds.get("backup_verified") is not True:
        problems.append(
            f"'{module}' maneja datos reales/PII sin backup_verified=true: no se "
            "puede cerrar sobre la primera escritura destructiva sin backup "
            "probado (FW-083)"
        )
    if len(str(ds.get("evidence") or "").strip()) < 20:
        problems.append(f"data_safety['{module}'].evidence insuficiente (< 20 caracteres)")
    return problems


def client_seal_problems(acceptance_text: str, module: str) -> list[str]:
    """FW-147 — el sello de cierre es parte del cierre, no un post-it aparte.

    `/cierre-modulo` paso 6 escribe `AGENT_SEAL` (antes `LEGACY_CLIENT_SEAL`,
    que se sigue aceptando; FW-275) en la sección del
    módulo ANTES de llamar a complete_module, pero hasta 4.21.0 nadie lo
    comprobaba en el cierre: solo `/nueva-fase` lo leía, fases después. Un módulo
    podía quedar `completed` en el estado y sin sello en la aceptación, y la
    divergencia aparecía cuando ya no era reparable sin reabrir historia
    (Example Service, phase_05). Se mira SOLO la sección del módulo: la leyenda de
    estados de la cabecera del archivo contiene el mismo literal y satisfaría un
    grep global.
    """
    section = _va.module_section_lines(acceptance_text, module)
    if section is None:
        # Se mantiene fail-closed (no devolver [] por no duplicar con
        # ac_coverage_problems: silenciar un gate para que la salida quede
        # limpia es el instinto equivocado). Redactado como continuación de esa
        # razón, no como un segundo hallazgo distinto.
        return [
            f"…y por lo mismo tampoco hay dónde leer el sello de cierre "
            f"para '{module}' (FW-147)"
        ]
    if any(seal in line for line in section for seal in ACCEPTED_SEALS):
        return []
    return [
        f"falta el sello 'Estado: {AGENT_SEAL}' en la sección de '{module}' de "
        "08_module_acceptance.md: el paso 6 de /cierre-modulo lo escribe antes "
        "de cerrar. Sin sello, estado y aceptación divergen (FW-147)"
    ]


def executor_problems(state: dict, module: str) -> list[str]:
    """Executor-capability graduation (GUARDRAILS → "Graduación por capacidad del
    ejecutor"). A 'limited' executor compensates weaker judgment with mandatory
    independent second-review on EVERY tier (not just tier_2)."""
    profile = state.get("execution_profile")
    tier = profile.get("capability_tier") if isinstance(profile, dict) else None
    if tier != "limited":
        return []
    work_tiers = state.get("work_tiers")
    entry = work_tiers.get(module) if isinstance(work_tiers, dict) else None
    if isinstance(entry, dict) and entry.get("requires_second_review") is True:
        return []
    return [
        f"ejecutor 'limited': work_tiers['{module}'] debe declarar requires_second_review:true "
        f"en todo tier (revisión independiente obligatoria — graduación por capacidad del ejecutor)"
    ]


def review_receipt_problems(
    state: dict, module: str, current_diff_hash: str | None = None
) -> list[str]:
    """Tier 2/limited review is evidence, not a self-declared boolean."""
    entry = state.get("work_tiers", {}).get(module, {})
    profile = state.get("execution_profile", {})
    required = (
        entry.get("tier") == "tier_2"
        or entry.get("requires_second_review") is True
        or profile.get("capability_tier") == "limited"
    )
    if not required:
        return []
    return _wr.review_problems(state, module, current_diff_hash)


def approval_receipt_problems(
    state: dict, module: str, workspace_root: Path | None = None,
    current_basis: dict[str, str] | None = None,
) -> list[str]:
    """Tier 2 no puede cerrarse con una aprobación reconstruida en prosa."""
    entry = state.get("work_tiers", {}).get(module, {})
    if entry.get("tier") != "tier_2":
        return []
    problems = _wr.approval_receipt_state_problems(state, module)
    if workspace_root is not None and not problems:
        problems.extend(
            _wr.approval_problems(
                workspace_root, state, module, current_basis=current_basis,
            )
        )
    return problems


def clarification_problems(state: dict, module: str) -> list[str]:
    """CLAR GATE — cierre-modulo: una clarificación ABIERTA con
    `blocks_closure: true` cuyo `blocked_scope` incluye el módulo bloquea el
    cierre con `clar_pending`. Antes, el script listaba `clar_pending` como
    razón válida pero NUNCA la evaluaba: el OK mecánico podía pasar por encima
    de un bloqueo semántico declarado (FW-031, severidad alta).

    El match de scope tokeniza `blocked_scope` por coma/espacio, así una entrada
    compuesta ("M0_orquestacion, M7_decision_log") bloquea ambos módulos."""
    clars = state.get("_clarifications")
    if not isinstance(clars, list):
        return []
    problems: list[str] = []
    for c in clars:
        if not isinstance(c, dict):
            continue
        if c.get("status") != "Abierta":
            continue
        if c.get("blocks_closure") is not True:
            continue
        scope = str(c.get("blocked_scope") or "")
        tokens = [t.strip() for t in re.split(r"[,\s]+", scope) if t.strip()]
        if module == scope or module in tokens:
            problems.append(
                f"CLAR {c.get('id', '?')} abierta con blocks_closure para '{module}': "
                f"resolver con /clarificar antes de cerrar (clar_pending)"
            )
    return problems


def harvest_problems(state: dict, module: str) -> list[str]:
    """HARVEST GATE (FW-201/FW-185, cierre-modulo paso 5.5): el cierre exige
    una declaración de cosecha de fricción para el módulo, no que haya
    hallazgos. `"none"` es un valor de primera clase — la regla central de
    `.agent/prompts/cosecha_sesion.md` es que cero hallazgos es normal y
    preferible a inventar. Lo que el gate hace imposible es el SILENCIO: antes
    de este gate, "una línea si no hay nada" no era exigible y un paso no
    bloqueante era indistinguible de uno que no se ejecutó (FW-201).

    Se lee de `work_tiers[module].harvest`, escrito por la acción
    `record_harvest` (única vía de escritura, mismo idioma que los receipts)."""
    entry = state.get("work_tiers", {}).get(module)
    if not isinstance(entry, dict):
        return []  # el tier gate ya bloquea la ausencia de work_tiers[module]
    harvest = entry.get("harvest")
    if not isinstance(harvest, dict) or "classes" not in harvest:
        return [
            f"'{module}' no tiene cosecha de fricción declarada — llama a "
            f"record_harvest con module_id='{module}', classes='none' si no "
            "hubo hallazgos, o con la lista de slugs si los hubo. Ver "
            ".agent/prompts/cosecha_sesion.md — 'none' es una declaración "
            "legítima, no una omisión."
        ]
    return []


def _resolution_marker_is_valid(rejection: dict) -> bool:
    marker = rejection.get("resolution_event")
    return (
        isinstance(marker, dict)
        and marker.get("action") == "resolve_closure_rejections"
        and marker.get("id") == rejection.get("id")
        and marker.get("timestamp") == rejection.get("resolved_at")
    )


def _state_log_chain_is_valid(log_path: Path) -> bool:
    """No usa provenance de un log cuya cadena append-only esté rota."""
    try:
        import state_core as _state_core
        lines = log_path.read_text(encoding="utf-8", errors="ignore").splitlines()
        has_hash = any(
            isinstance(json.loads(line), dict) and json.loads(line).get("hash")
            for line in lines
            if line.strip()
        )
        return bool(has_hash) and not _state_core.verify_log_chain(log_path)
    except (ImportError, OSError, ValueError, RuntimeError):
        return False


def _parse_state_log_events(lines: list[str]) -> list[dict]:
    """Parsea una bitácora ya validada una sola vez para una consulta."""
    events: list[dict] = []
    for line in lines:
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(event, dict):
            events.append(event)
    return events


def _legacy_identity_map_from_events(events: list[dict]) -> dict[tuple[str, str, str], str]:
    """Deriva identidades legacy sin volver a abrir ni verificar el log."""
    identities: dict[tuple[str, str, str], str] = {}
    ambiguous: set[tuple[str, str, str]] = set()
    sequence = 0
    for event in events:
        if event.get("action") != "add_closure_rejection":
            continue
        payload = event.get("payload")
        if not isinstance(payload, dict):
            continue
        module = payload.get("module_id")
        reason = payload.get("reason")
        timestamp = event.get("ts")
        if not all(isinstance(value, str) and value.strip() for value in (module, reason, timestamp)):
            continue
        sequence += 1
        identifier = payload.get("id") or f"CRR-{sequence:04d}"
        key = (module, timestamp, reason)
        if key in identities:
            ambiguous.add(key)
        else:
            identities[key] = identifier
    for key in ambiguous:
        identities.pop(key, None)
    return identities


def resolution_log_audit(workspace_root: Path | None) -> dict[str, object]:
    """Carga y valida ``state_log.jsonl`` una vez por consulta.

    El resultado es deliberadamente efímero: no se persiste en el estado ni se
    expone en el payload. Los consumidores que revisan varios rechazos de una
    misma consulta comparten los eventos y la identidad legacy ya calculados.
    """
    audit: dict[str, object] = {
        "valid": False,
        "events": [],
        "legacy_identities": {},
    }
    if workspace_root is None:
        return audit
    log_path = workspace_root / "_project" / "state_log.jsonl"
    if not log_path.exists() or not _state_log_chain_is_valid(log_path):
        return audit
    try:
        lines = log_path.read_text(encoding="utf-8", errors="ignore").splitlines()
    except OSError:
        return audit
    events = _parse_state_log_events(lines)
    audit.update({
        "valid": True,
        "events": events,
        "legacy_identities": _legacy_identity_map_from_events(events),
    })
    return audit


def _resolution_event_is_backed(
    rejection: dict,
    module: str,
    workspace_root: Path | None,
    *,
    audit: dict[str, object] | None = None,
) -> bool:
    """Comprueba que una resolución tiene un evento de bitácora trazable.

    Los rechazos históricos anteriores al sello de IDs no llevan
    ``resolution_event``; para ellos se acepta únicamente un evento legacy
    con el mismo módulo y timestamp. Las resoluciones nuevas deben incluir el
    ID en ``resolved_ids``. Las migraciones legacy no reutilizan esta función
    para aceptar IDs arbitrarios: deben pasar por
    ``legacy_rejection_identity_map`` con referencias históricas exactas.
    """
    if workspace_root is None:
        return _resolution_marker_is_valid(rejection)
    audit = audit if audit is not None else resolution_log_audit(workspace_root)
    if audit.get("valid") is not True:
        return False
    marker_valid = _resolution_marker_is_valid(rejection)
    events = audit.get("events", [])
    if not isinstance(events, list):
        return False
    for event in events:
        payload = event.get("payload") or {}
        if not isinstance(payload, dict):
            payload = {}
        if payload.get("module_id") != module:
            continue
        if event.get("action") == "backfill_closure_rejection_provenance":
            # A migration marker is valid only when its own append-only event
            # names the exact rejection ID and the original resolution time.
            # The marker alone must never be enough to evade the death gate.
            provenance_ids = payload.get("provenance_ids") or payload.get("resolved_ids")
            legacy_refs = payload.get("legacy_refs")
            if (
                marker_valid
                and payload.get("resolution_event_ts") == rejection.get("resolved_at")
                and isinstance(provenance_ids, list)
                and rejection.get("id") in provenance_ids
                and isinstance(legacy_refs, dict)
                and set(legacy_refs) == set(provenance_ids)
            ):
                reference = legacy_refs.get(rejection.get("id"))
                if (
                    not isinstance(reference, dict)
                    or set(reference) != {"timestamp", "reason"}
                    or reference.get("timestamp") != rejection.get("timestamp")
                    or reference.get("reason") != rejection.get("reason")
                    or reference.get("reason") not in VALID_CLOSURE_REASONS
                ):
                    continue
                identities = audit.get("legacy_identities", {})
                if not isinstance(identities, dict):
                    continue
                if identities.get(
                    (module, reference["timestamp"], reference["reason"])
                ) == rejection.get("id"):
                    return True
            continue
        if event.get("action") != "resolve_closure_rejections":
            continue
        if event.get("ts") != rejection.get("resolved_at"):
            continue
        resolved_ids = payload.get("resolved_ids")
        if isinstance(resolved_ids, list):
            return rejection.get("id") in resolved_ids
        # Backward-compatible audit is allowed only for truly legacy entries
        # that never had an ID. Once an ID exists, a legacy event without the
        # exact ID list cannot prove which rejection was resolved. Continue
        # scanning: a later explicit provenance-backfill event may seal that
        # same historical resolution by ID.
        if not rejection.get("id"):
            return True
    return False


def legacy_rejection_identity_map(workspace_root: Path | None) -> dict[tuple[str, str, str], str]:
    """Deriva identidades legacy desde eventos ``add_closure_rejection``.

    Antes de que el motor sellara IDs, los eventos de alta no llevaban el ID.
    Para permitir únicamente el backfill de esa historia, se asigna el ID
    determinista por orden global de alta. Las claves ambiguas se eliminan:
    una migración no puede resolver dos rechazos indistinguibles por accidente.
    """
    audit = resolution_log_audit(workspace_root)
    identities = audit.get("legacy_identities", {})
    return identities if isinstance(identities, dict) else {}


def legacy_resolution_event_exists(
    workspace_root: Path | None,
    module: str,
    resolved_at: str,
) -> bool:
    """Comprueba el evento legacy de resolución sin atribuirle IDs nuevos."""
    audit = resolution_log_audit(workspace_root)
    events = audit.get("events", [])
    if audit.get("valid") is not True or not isinstance(events, list):
        return False
    for event in events:
        if event.get("action") != "resolve_closure_rejections":
            continue
        payload = event.get("payload") or {}
        if not isinstance(payload, dict):
            continue
        if (
            event.get("ts") == resolved_at
            and payload.get("module_id") == module
            and not isinstance(payload.get("resolved_ids"), list)
        ):
            return True
    return False


def death_problems(
    state: dict,
    module: str,
    workspace_root: Path | None = None,
    resolution_backed: dict[str, bool] | None = None,
) -> list[str]:
    """DEATH CRITERION — cierre-modulo step 5, fail-closed ante resoluciones fabricadas."""
    rejections = state.get("closure_rejections")
    if not isinstance(rejections, list):
        return []
    count = sum(
        1
        for r in rejections
        if isinstance(r, dict)
        and r.get("module_id") == module
        and r.get("reason") in VALID_CLOSURE_REASONS
        # Resolved entries remain in the active projection until
        # complete_module archives them. A restored archive entry is active
        # again even though it retains its original resolution history. A
        # truthy resolved_at without a matching event is still active.
        and _state_core.closure_rejection_is_active(
            r,
            resolution_backed=(
                resolution_backed.get(
                    str(r.get("id")) if isinstance(r.get("id"), str) and r.get("id")
                    else "|".join(str(r.get(field, "")) for field in ("module_id", "timestamp", "reason")),
                    False,
                )
                if resolution_backed is not None
                else _resolution_event_is_backed(r, module, workspace_root)
            ),
        )
    )
    if count >= DEATH_THRESHOLD:
        return [
            f"death criterion: {count} closure_rejections por gates duros para '{module}' (≥{DEATH_THRESHOLD}); "
            f"abrir un módulo 'Estabilización' en vez de cerrar (paso 5)"
        ]
    return []


TIER2_CHECKPOINTS = ("domain_sweep", "expert_audit")


def checkpoint_gate_problems(state: dict, module: str) -> list[str]:
    """EXPERT GATE — cierre-modulo paso 3.9, en el motor (FW-276).

    Hasta 5.2.1 solo el doctor del pre-commit exigía estos checkpoints, con el
    módulo ya `completed` (TL FW-238/261, PA FW-135). Se reutiliza
    `workflow_receipts.checkpoint_problems`, que acepta las entradas
    `grandfathered` del upgrade (FW-221) porque llevan evidencia y referencia.
    """
    work_tiers = state.get("work_tiers")
    entry = work_tiers.get(module) if isinstance(work_tiers, dict) else None
    if not isinstance(entry, dict) or entry.get("tier") != "tier_2":
        return []
    return [
        problem
        for kind in TIER2_CHECKPOINTS
        for problem in _wr.checkpoint_problems(state, kind, module)
    ]


def closure_gate(
    state: dict,
    module: str,
    current_diff_hash: str | None = None,
    workspace_root: Path | None = None,
    current_approval_basis: dict[str, str] | None = None,
    resolution_backed: dict[str, bool] | None = None,
) -> list[str]:
    """All state-only block reasons for closing `module` (empty == gate passes).

    Pure function of the state dict, with no disk I/O, so it remains unit-testable
    with synthetic states. AC coverage needs 08_module_acceptance.md and is
    adjudicated separately in `main()` via `ac_coverage_problems`.
    """
    return (
        tier_problems(state, module)
        + spec_problems(state, module)
        + data_safety_problems(state, module)
        + death_problems(state, module, workspace_root, resolution_backed)
        + executor_problems(state, module)
        + approval_receipt_problems(
            state, module, workspace_root, current_basis=current_approval_basis,
        )
        + review_receipt_problems(state, module, current_diff_hash)
        + checkpoint_gate_problems(state, module)
        + clarification_problems(state, module)
        + harvest_problems(state, module)
    )


def ac_coverage_problems(acceptance_text: str, module: str) -> list[str]:
    """AC COVERAGE — cierre-modulo step 1.c, machine-decidable subset."""
    acs = _va.module_ac_lines(acceptance_text, module)
    if acs is None:
        return [f"sin sección de ACs para '{module}' en 08_module_acceptance.md (cierre-modulo paso 1.c)"]
    total = len(acs)
    checked = sum(1 for ch, _ in acs if ch)
    if total == 0:
        return [f"módulo '{module}' sin ACs definidos en 08_module_acceptance.md (cierre-modulo paso 1.c)"]
    if checked < total:
        return [
            f"cobertura de ACs < 100%: {checked}/{total} marcados [x] "
            f"(cierre-modulo paso 1.c → ac_incomplete)"
        ]
    return []


def closure_warnings(
    state: dict,
    module: str,
    current_diff_hash: str | None = None,
    workspace_root: Path | None = None,
) -> list[str]:
    """Avisos no bloqueantes de recibos (p. ej. deriva documental o derivación aditiva)."""
    return _wr.receipt_warnings(state, module, current_diff_hash, root=workspace_root)


def quality_warnings(root: Path, config: Path | None = None) -> list[str]:
    """Calidad en aviso (5.3.1 L7, S6): nunca bloquea ni cambia el veredicto del cierre.

    `_project/quality.yaml` es opcional: bajo `commands:`, `nombre: comando` por herramienta
    del stack. Se ejecuta sin shell, en la raíz; si una herramienta falta, se dice y se sigue.
    Con `quality_baseline_<nombre>.txt` al lado, lo que no estaba sale NUEVO (L7.2).
    """
    config = config or root / "_project" / "quality.yaml"
    if not config.is_file():
        return ["calidad: no configurada"]
    try:
        commands = re.findall(r"^[ \t]+([\w.-]+):[ \t]*([^\s#].*?)[ \t]*$", config.read_text(encoding="utf-8"), re.M)
    except (OSError, ValueError) as exc:
        return [f"calidad: {config.name} ilegible ({exc}); se sigue"]
    out = [] if commands else [f"calidad: {config.name} sin comandos («nombre: comando» bajo commands:)"]
    for name, command in commands:
        baseline = config.with_name(f"quality_baseline_{name}.txt")
        try:
            run = subprocess.run(shlex.split(command, comments=True), cwd=root, capture_output=True,
                                 stdin=subprocess.DEVNULL, encoding="utf-8", errors="replace", timeout=600)
            known = Counter(_DIAGNOSTIC.sub(r"\1:", line) for line in (
                baseline.read_text(encoding="utf-8-sig").splitlines() if baseline.is_file() else []))
        except (OSError, ValueError, IndexError, subprocess.SubprocessError) as exc:
            out.append(f"calidad: {name}: no se pudo ejecutar ({exc}); se sigue")
            continue
        lines = (run.stdout + "\n" + run.stderr).splitlines()
        found, fresh = [line for line in lines if _DIAGNOSTIC.match(line)], []
        for line in found:  # el trinquete de TL: sin línea ni columna, con multiplicidad
            key = _DIAGNOSTIC.sub(r"\1:", line)
            known[key] -= 1
            if known[key] < 0:
                fresh.append(line)
        mark = "NUEVO " if baseline.is_file() else ""
        out.append(f"calidad: {name} (rc {run.returncode}): {len(found)} diagnóstico(s), "
                   + (f"{len(fresh)} nuevo(s) frente a {baseline.name}" if mark else "sin línea base"))
        shown = [mark + line for line in fresh] if found else [line for line in lines if line.strip()][-5:]
        out += [f"calidad: {name}: {line}" for line in shown[:20]]
    return out


def main(argv: list[str]) -> int:
    if len(argv) == 3 and argv[1] == "--calidad":  # L7.4: medir sin cerrar un módulo
        print("\n".join(quality_warnings(ROOT, Path(argv[2]).resolve())))
        return 0
    if len(argv) != 2 or not argv[1].strip():
        print("uso: validate_closure.py <module_id> | --calidad <quality.yaml>")
        return 2
    module = argv[1].strip()
    if not STATE.exists():
        print(f"FALTA: {STATE}")
        return 2
    try:
        state = json.loads(STATE.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        print(f"JSON inválido en {STATE}: {exc}")
        return 2
    # Bot FW-006: tras el cierre, el workspace sigue cambiando (doctor, git,
    # nuevos módulos) y comparar el diff actual contra el hash del review es
    # ruido permanente, no información — el módulo ya está sellado. El check
    # de staleness solo aplica ANTES de completar.
    already_completed = state.get("module_statuses", {}).get(module) == "completed"
    current_hash = None if already_completed else _wr.workspace_diff_hash(ROOT)
    problems = closure_gate(state, module, current_hash, workspace_root=ROOT)
    if ACCEPTANCE.exists():
        acceptance_text = ACCEPTANCE.read_text(encoding="utf-8", errors="ignore")
        problems = problems + ac_coverage_problems(acceptance_text, module)
        # FW-147: el sello del builder lo exige el motor al cerrar. Si esta CLI
        # no lo comprobara, seguiría existiendo una previsualización que dice OK
        # sobre un cierre que va a bloquear — que es justo la distancia entre
        # "el gate existe" y "el agente se entera a tiempo".
        problems = problems + client_seal_problems(acceptance_text, module)
    else:
        problems = problems + [f"FALTA: {ACCEPTANCE} (no se puede verificar cobertura de ACs)"]
    # FW-144/FW-145: mismos gates que `complete_module`, para que este comando
    # sea una previsualización FIEL del veredicto de cierre y no una versión
    # optimista suya. Un módulo ya `completed` no se re-juzga (Bot FW-006).
    if not already_completed:
        problems = problems + _wr.execution_problems(state, module, current_hash)
        try:
            import validate_no_stubs as _vns
            problems = problems + _vns.stub_problems(module, ROOT)
        except ImportError as exc:
            problems = problems + [
                f"STUB GATE no disponible: no se pudo importar validate_no_stubs.py ({exc})"
            ]
    warnings = [] if already_completed else (
        closure_warnings(state, module, current_hash, workspace_root=ROOT) + quality_warnings(ROOT))
    if problems:
        print(f"CLOSURE GATE: FAIL ({module})")
        for problem in problems:
            print(f"- {problem}")
        for warning in warnings:
            print(f"- AVISO: {warning}")
        return 1
    print(f"CLOSURE GATE: checks mecánicos + cobertura de ACs OK ({module})")
    for warning in warnings:
        print(f"- AVISO: {warning}")
    print(
        "- NOTA: NO valida evidencia semántica ni gate de seguridad/anti-regresión "
        "(pasos 3.5-4). /cierre-modulo los exige antes de marcar 'completed'."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
