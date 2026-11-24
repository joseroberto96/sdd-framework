#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""state_manager.py - MCP transport (JSON-RPC over stdio) + CLI.

State model: state_core.py. Projections: projections.py. Transitions: _transitions.py.
"""
from __future__ import annotations
import json
import hashlib
import os
import sys
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import state_core
import context_sources
import context_measurement
import state_queries
import workflow_receipts
import action_contracts
import codex_profile
from state_core import StateManager, log_err

FRAMEWORK_VERSION = state_core.FRAMEWORK_VERSION


def _context_validation(state: dict, *, view: str, target: str | None, kind: str, root: Path) -> dict:
    """Ejecuta validadores canónicos bajo el lock de lectura del contexto.

    ``state_queries`` permanece puro: aquí se comprueba el filesystem real,
    los receipts y el respaldo de la bitácora, y se entrega solo el resultado
    estructurado junto con sus dependencias para invalidar cursores.
    """
    validation: dict = {"problems": {}, "active_rejection_ids": [], "hard_blockers": [], "source_errors": []}
    dependencies: dict = {}
    current_approval_basis: dict[str, str] | None = None
    resolution_backed: dict[str, bool] = {}
    try:
        import validate_closure

        has_rejections = any(
            isinstance(rejection, dict)
            and isinstance(rejection.get("module_id"), str)
            for rejection in state.get("closure_rejections", []) or []
        )
        resolution_audit = (
            validate_closure.resolution_log_audit(root)
            if has_rejections
            else {"valid": False, "events": [], "legacy_identities": {}}
        )
        for rejection in state.get("closure_rejections", []) or []:
            if not isinstance(rejection, dict):
                continue
            rejection_module = rejection.get("module_id")
            if not isinstance(rejection_module, str):
                continue
            backed = validate_closure._resolution_event_is_backed(
                rejection, rejection_module, root, audit=resolution_audit,
            )
            active = state_core.closure_rejection_is_active(
                rejection, resolution_backed=backed
            )
            rejection_key = (
                rejection.get("id")
                if isinstance(rejection.get("id"), str) and rejection.get("id")
                else "|".join(str(rejection.get(field, "")) for field in ("module_id", "timestamp", "reason"))
            )
            resolution_backed[rejection_key] = backed
            if active:
                identifier = rejection.get("id")
                if isinstance(identifier, str) and identifier.strip():
                    validation["active_rejection_ids"].append(identifier)
                else:
                    validation["active_rejection_ids"].append(
                        "|".join(str(rejection.get(field, "")) for field in ("module_id", "timestamp", "reason"))
                    )
        log_path = root / "_project" / "state_log.jsonl"
        dependencies["state_log_sha256"] = (
            hashlib.sha256(log_path.read_bytes()).hexdigest() if log_path.exists() else "MISSING"
        )
    except (ImportError, OSError, ValueError, RuntimeError) as exc:
        validation["source_errors"].append(f"closure validator: {exc}")

    if kind == "module" and target:
        tier_entry = state.get("work_tiers", {}).get(target, {}) if isinstance(state.get("work_tiers"), dict) else {}
        tier = tier_entry.get("tier") if isinstance(tier_entry, dict) else None
        if tier == "tier_2":
            try:
                current_approval_basis = workflow_receipts.approval_basis(root, state, target)
                problems = workflow_receipts.approval_problems(
                    root, state, target, current_basis=current_approval_basis,
                )
                if problems:
                    validation["problems"]["APPROVAL"] = problems
                if getattr(problems, "warnings", None):
                    for w in problems.warnings:
                        if w not in validation.setdefault("warnings", []):
                            validation["warnings"].append(w)
                dependencies["approval_basis"] = current_approval_basis
            except (OSError, ValueError, RuntimeError, KeyError) as exc:
                validation["source_errors"].append(f"approval validator: {exc}")

        if view == "closure":
            try:
                current_diff_hash = workflow_receipts.workspace_diff_hash(root)
                dependencies["current_diff_hash"] = current_diff_hash
                # El hash de receipts puede resolverse en un subproceso fresco
                # para evitar módulos stale en el servidor MCP. La traza del
                # proceso actual no puede observar ese subproceso; lo
                # declaramos en el reporte en vez de presentarlo como coste
                # medido. Solo aplica cuando el cálculo realmente se ejecutó.
                if getattr(workflow_receipts.workspace_diff_hash, "_is_fresh_patch", False):
                    context_measurement.note_unobserved(
                        "workflow_receipts.workspace_diff_hash_fresh",
                        "el hash del workspace se calcula en un subproceso separado",
                    )
                if state_queries._review_required(state, target):
                    problems = workflow_receipts.review_problems(state, target, current_diff_hash)
                    if problems:
                        validation["problems"]["REVIEW"] = problems
                problems = workflow_receipts.execution_problems(state, target, current_diff_hash)
                if problems:
                    validation["problems"]["EXECUTION"] = problems
                warnings = workflow_receipts.receipt_warnings(state, target, current_diff_hash, root=root)
                if warnings:
                    for w in warnings:
                        if w not in validation.setdefault("warnings", []):
                            validation["warnings"].append(w)
                # Reutilizar el gate canónico de cierre mantiene las mismas
                # reglas que complete_module; aquí solo se proyectan sus
                # razones como contexto pendiente, nunca se autoriza el cierre.
                closure_problems = validate_closure.closure_gate(
                    state, target, current_diff_hash=current_diff_hash,
                    workspace_root=root,
                    current_approval_basis=current_approval_basis,
                    resolution_backed=resolution_backed,
                )
                acceptance_path = root / "_project" / "08_module_acceptance.md"
                if not acceptance_path.exists():
                    closure_problems.append(
                        "falta _project/08_module_acceptance.md (cobertura de ACs y sello no verificables)"
                    )
                else:
                    acceptance_text = acceptance_path.read_text(
                        encoding="utf-8", errors="ignore"
                    )
                    closure_problems.extend(
                        validate_closure.ac_coverage_problems(acceptance_text, target)
                    )
                    closure_problems.extend(
                        validate_closure.client_seal_problems(acceptance_text, target)
                    )
                if closure_problems:
                    validation["problems"]["CLOSURE"] = closure_problems
                    # El death criterion es un bloqueo duro; los demás gates
                    # conservan NEEDS_INPUT para que el agente pueda preparar y
                    # reconsultar el contexto sin confundirlo con autorización.
                    validation["hard_blockers"].extend(
                        problem for problem in closure_problems
                        if str(problem).startswith("death criterion:")
                    )
            except (OSError, ValueError, RuntimeError, KeyError) as exc:
                validation["source_errors"].append(f"closure validator: {exc}")

    dependencies["problems"] = validation["problems"]
    dependencies["active_rejection_ids"] = validation["active_rejection_ids"]
    validation["dependencies"] = dependencies
    validation["fingerprint"] = hashlib.sha256(
        json.dumps(dependencies, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return validation


def resolve_source_path(root: Path, relative: str) -> Path:
    """Compatibilidad de transporte para validar rutas de fuentes."""
    return context_sources.resolve_source_path(root, relative)


_CYCLE_LOG_WINDOW = 64 * 1024


def _active_cycle_module(root: Path) -> str | None:
    """Módulo de un ``ciclo_started`` vigente, leyendo solo el final del log (L8).

    Ventana de 64 KiB hacia atrás; tolera la primera línea cortada y líneas que
    no son JSON. Vigente = sin ``ciclo_finished`` del módulo, ``ciclo_paused``
    (sin módulo: cierra el ciclo en curso) ni ``complete_module`` del módulo
    posteriores. Sin log o sin evento en la ventana, no hay pista. Solo lectura.
    """
    log_path = root / "_project" / "state_log.jsonl"
    try:
        resolved = log_path.resolve()
        if not resolved.is_relative_to(root.resolve()) or not resolved.is_file():
            return None
        with resolved.open("rb") as handle:
            handle.seek(0, os.SEEK_END)
            size = handle.tell()
            start = max(0, size - _CYCLE_LOG_WINDOW)
            handle.seek(start)
            chunk = handle.read()
    except OSError:
        return None
    lines = chunk.split(b"\n")
    if start > 0 and lines:
        lines = lines[1:]
    closed: set[str] = set()
    reorder_to: str | None = None
    for raw in reversed(lines):
        if not raw.strip():
            continue
        try:
            entry = json.loads(raw.decode("utf-8", errors="replace"))
        except ValueError:
            continue
        if not isinstance(entry, dict):
            continue
        action = entry.get("action")
        payload = entry.get("payload")
        payload = payload if isinstance(payload, dict) else {}
        if action == "ciclo_paused":
            return None
        if action == "set_session_pointer":
            # Un reordenado posterior del builder (next_module) gana a ciclos anteriores de otro módulo.
            reordered = payload.get("next_module")
            if reorder_to is None and isinstance(reordered, str) and reordered.strip():
                reorder_to = reordered.strip()
            continue
        if action == "ciclo_finished" and isinstance(payload.get("module"), str):
            closed.add(payload["module"].strip())
        elif action == "complete_module" and isinstance(payload.get("id"), str):
            closed.add(payload["id"].strip())
        elif action == "ciclo_started" and isinstance(payload.get("module"), str):
            module = payload["module"].strip()
            if module and module not in closed and (reorder_to is None or module == reorder_to):
                return module
    return None


def _get_work_context_once(manager: StateManager, *, view: str, module_id: str | None,
                           cursor: str | None) -> dict:
    """Camino real de una consulta dentro de un snapshot de lecturas."""
    with state_core.state_write_lock(), state_queries.query_memo():
        manager.reload_if_changed(lock_held=True)
        # Resolver la unidad antes de abrir documentos evita que una consulta
        # implícita cargue el scope/acceptance completos y luego filtre tarde.
        active_cycle = _active_cycle_module(state_core.WORKSPACE_ROOT) if module_id is None else None
        resolved_target, _, explicit = state_queries.resolve_context_target(
            manager.state_data, module_id, active_cycle
        )
        sources = context_sources.load_sources(
            state_core.WORKSPACE_ROOT, view, resolved_target, explicit=explicit,
            state=manager.state_data,
        )
        validation = _context_validation(
            manager.state_data, view=view, target=resolved_target,
            kind=state_queries.target_kind(resolved_target), root=state_core.WORKSPACE_ROOT,
        )
        result = state_queries.query_context(
            manager.state_data,
            view=view,
            module_id=module_id,
            cursor=cursor,
            sources=sources,
            workspace_root=state_core.WORKSPACE_ROOT,
            validation=validation,
            active_cycle_module=active_cycle,
        )
        return _flag_legacy_delegation(result, view=view)


def _flag_legacy_delegation(result: dict, *, view: str) -> dict:
    """ADR-072: en `startup`, señala el interruptor `codex_delegation: legacy`.

    Solo se añade cuando está activo, así que la respuesta por defecto no cambia.
    """
    if view != "startup":
        return result
    if codex_profile.codex_delegation_mode(state_core.WORKSPACE_ROOT) != "legacy":
        return result
    summary = (result.get("data") or {}).get("summary")
    if isinstance(summary, dict):
        summary["codex_delegation"] = "legacy"
        summary["codex_delegation_spec"] = codex_profile.DELEGATION_LEGACY_SPEC
    return result


def _measured_query(manager: StateManager, *, view: str, module_id: str | None,
                    cursor: str | None) -> dict:
    """Ejecuta la consulta con snapshot compartido y, si aplica, persiste traza."""
    trace_dir = os.environ.get(context_measurement.TRACE_ENV)
    trace = None
    try:
        # El snapshot se aplica también fuera del modo diagnóstico: evita que
        # loader y validadores observen versiones mezcladas dentro de una misma
        # consulta. Solo se serializa la traza cuando el caller la solicita.
        with context_measurement.capture_reads(state_core.WORKSPACE_ROOT, phase="query") as current:
            trace = current
            result = _get_work_context_once(
                manager, view=view, module_id=module_id, cursor=cursor,
            )
            current.verify_snapshot()
            return result
    except context_measurement.SnapshotChanged as exc:
        raise state_queries.QueryError("SOURCE_CHANGED", str(exc)) from exc
    finally:
        if trace is not None and trace_dir:
            context_measurement.write_trace(Path(trace_dir), trace)


def get_work_context(manager: StateManager, *, view: str, module_id: str | None = None,
                     cursor: str | None = None) -> dict:
    """Obtiene una vista selectiva bajo lock y snapshot de lectura por consulta."""
    return _measured_query(manager, view=view, module_id=module_id, cursor=cursor)


def _query_error_response(req_id, error: state_queries.QueryError) -> dict:
    payload = json.dumps({"error": {"code": error.code, "message": error.message}}, ensure_ascii=False)
    return {
        "jsonrpc": "2.0",
        "id": req_id,
        "result": {"isError": True, "content": [{"type": "text", "text": payload}]},
    }


def _run_query_cli(manager: StateManager, argv: list[str]) -> int:
    """CLI JSON-only para una vista selectiva."""
    values: dict[str, str] = {}
    index = 0
    allowed = {"--view": "view", "--module-id": "module_id", "--cursor": "cursor"}
    while index < len(argv):
        flag = argv[index]
        key = allowed.get(flag)
        if key is None or index + 1 >= len(argv):
            print(json.dumps({"error": {"code": "INVALID_ARGUMENT", "message": "Uso: query --view VIEW [--module-id ID] [--cursor CURSOR]"}}, ensure_ascii=False))
            return 2
        values[key] = argv[index + 1]
        index += 2
    if "view" not in values:
        print(json.dumps({"error": {"code": "INVALID_ARGUMENT", "message": "Falta --view"}}, ensure_ascii=False))
        return 2
    try:
        result = get_work_context(
            manager,
            view=values["view"],
            module_id=values.get("module_id"),
            cursor=values.get("cursor"),
        )
    except state_queries.QueryError as exc:
        print(json.dumps({"error": {"code": exc.code, "message": exc.message}}, ensure_ascii=False))
        return 2 if exc.code in {"INVALID_VIEW", "INVALID_TARGET", "INVALID_CURSOR"} else 3
    print(state_queries.serialize_context(result))
    return 0


FULL_STATE_SUMMARY_THRESHOLD = 256 * 1024


def _full_state_summary(state: dict, size_bytes: int) -> dict:
    """Resumen de get_full_state cuando el volcado supera el umbral (N8)."""
    def count(value: object) -> object:
        return len(value) if isinstance(value, (list, dict)) else type(value).__name__

    modules = state.get("modules") if isinstance(state.get("modules"), dict) else {}
    bugs = state.get("_bugs") if isinstance(state.get("_bugs"), list) else []
    return {
        "summary_only": True,
        "size_bytes": size_bytes,
        "limit_bytes": FULL_STATE_SUMMARY_THRESHOLD,
        "sections": {key: count(value) for key, value in sorted(state.items())},
        "counts": {
            "bugs": len(bugs),
            "bugs_not_resolved": sum(1 for b in bugs if isinstance(b, dict) and b.get("status") != "Resuelto"),
            "clarifications": count(state.get("_clarifications")),
            "decisions": count(state.get("_decisions")),
            "modules": count(state.get("module_statuses")),
        },
        "active_modules": modules.get("in_progress", []),
        "next_calls": {
            "full_dump": {"tool": "get_full_state", "arguments": {"force": True}},
            "one_module": {"tool": "lookup_state", "arguments": {"kind": "module", "module_id": "<id>"}},
            "bugs_of_module": {"tool": "lookup_state", "arguments": {"kind": "bugs", "module_id": "<id>"}},
            "kinds": list(state_queries.LOOKUP_KINDS),
        },
    }


def _run_lookup_cli(manager: StateManager, argv: list[str]) -> int:
    """CLI JSON-only para consulta puntual del estado."""
    values: dict[str, str] = {}
    index = 0
    allowed = {"--kind": "kind", "--module-id": "module_id", "--status": "status", "--offset": "offset"}
    while index < len(argv):
        flag = argv[index]
        key = allowed.get(flag)
        if key is None or index + 1 >= len(argv):
            print(json.dumps({"error": {"code": "INVALID_ARGUMENT", "message": "Uso: lookup --kind KIND [--module-id ID] [--status STATUS] [--offset N]"}}, ensure_ascii=False))
            return 2
        values[key] = argv[index + 1]
        index += 2
    if "kind" not in values:
        print(json.dumps({"error": {"code": "INVALID_ARGUMENT", "message": "Falta --kind"}}, ensure_ascii=False))
        return 2
    offset = None
    if "offset" in values:
        try:
            offset = int(values["offset"])
        except ValueError:
            print(json.dumps({"error": {"code": "INVALID_TARGET", "message": "offset debe ser un entero >= 0"}}, ensure_ascii=False))
            return 2
    try:
        with state_core.state_write_lock():
            manager.reload_if_changed(lock_held=True)
            result = state_queries.query_state_point(
                manager.state_data,
                kind=values["kind"],
                module_id=values.get("module_id"),
                status=values.get("status"),
                offset=offset,
            )
    except state_queries.QueryError as exc:
        print(json.dumps({"error": {"code": exc.code, "message": exc.message}}, ensure_ascii=False))
        return 2 if exc.code in {"INVALID_VIEW", "INVALID_TARGET"} else 3
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


SUPPORTED_PROTOCOL_VERSIONS = ("2024-11-05", "2025-03-26", "2025-06-18")
DEFAULT_PROTOCOL_VERSION = "2024-11-05"

# =====================================================================
# S3 (TL FW-008 residuo): hash de receipts inmune al servidor stale
# =====================================================================
# state_core.py resuelve `workflow_receipts.workspace_diff_hash(...)` en cada
# handler de review (start_independent_review / record_review_receipt /
# complete_module) SIEMPRE vía `import workflow_receipts as _wr` seguido de
# `_wr.workspace_diff_hash(...)` — nunca importan la función suelta. Ese
# import queda cacheado en sys.modules durante toda la vida del proceso
# servidor MCP (run_mcp_server, más abajo): si el framework se actualiza
# mientras la sesión sigue viva, el proceso conserva en memoria la versión
# vieja de workflow_receipts.py — reload_if_changed() (state_core.py) solo
# recarga el ESTADO JSON, nunca los MÓDULOS Python.
#
# Implementado durante el desarrollo en paralelo del lote de campo 2026-07-23,
# cuando state_core.py estaba fuera de alcance (otro agente trabajaba ahí
# simultáneamente). Tras la integración se evaluó mover la llamada
# directamente a state_core.py (eliminando el monkeypatch); se decidió NO
# hacerlo: el mecanismo ya está verificado (28/28 tests, incluida
# confirmación explícita de que no rompe el transport MCP), es idempotente
# y se instala solo en los dos entry points reales — reescribirlo solo por
# estética arriesgaba la suite verde sin un beneficio real. El enganche
# reasigna el atributo `workspace_diff_hash` del módulo YA cacheado en
# sys.modules. Como Python resuelve nombres de función en tiempo de llamada
# (no de definición), esto cubre tanto `_wr.workspace_diff_hash(...)` como
# las llamadas internas por nombre suelto que ya tuviera workflow_receipts.py
# (p. ej. dentro de start_review/finish_review). El fallback de
# `workspace_diff_hash_fresh()` nunca reentra por este nombre (ver
# `_compute_workspace_diff_hash` en workflow_receipts.py), así que no hay
# riesgo de recursión aunque el parche quede instalado.
#
# El parche se instala explícitamente en main() (servidor MCP y CLI
# `mutate`), NUNCA al importar este módulo — así que un test que solo hace
# `import state_manager` no muta el estado compartido de `workflow_receipts`
# para el resto del proceso de test.
_LAST_HASH_FALLBACK_WARNING: str | None = None


def _fresh_or_fallback_diff_hash(root):
    global _LAST_HASH_FALLBACK_WARNING
    value, warning = workflow_receipts.workspace_diff_hash_fresh(root)
    _LAST_HASH_FALLBACK_WARNING = warning
    return value


def install_fresh_hash_patch() -> None:
    """Idempotente: puede llamarse varias veces sin apilar wrappers."""
    if getattr(workflow_receipts.workspace_diff_hash, "_is_fresh_patch", False):
        return
    _fresh_or_fallback_diff_hash._is_fresh_patch = True  # type: ignore[attr-defined]
    workflow_receipts.workspace_diff_hash = _fresh_or_fallback_diff_hash


def pop_hash_fallback_warning() -> str | None:
    """Consume (lee y limpia) el warning del último cálculo de hash, si lo hubo."""
    global _LAST_HASH_FALLBACK_WARNING
    warning, _LAST_HASH_FALLBACK_WARNING = _LAST_HASH_FALLBACK_WARNING, None
    return warning


def _append_hash_warning(message: str) -> str:
    warning = pop_hash_fallback_warning()
    if not warning:
        return message
    return f"{message}\nADVERTENCIA: {warning}"


def _new_manager() -> StateManager:
    """Carga el estado y, en modo diagnóstico, separa el coste de arranque."""
    trace_dir = os.environ.get(context_measurement.TRACE_ENV)
    if not trace_dir:
        return StateManager()
    trace = None
    try:
        with context_measurement.capture_reads(state_core.WORKSPACE_ROOT, phase="initialization") as current:
            trace = current
            manager = StateManager()
            current.verify_snapshot()
            return manager
    finally:
        if trace is not None:
            context_measurement.write_trace(Path(trace_dir), trace)

# =====================================================================
# SERVIDOR MCP (JSON-RPC 2.0 over stdio)
# =====================================================================

def _invalid_request(req_id, detail: str) -> dict:
    return {"jsonrpc": "2.0", "id": req_id, "error": {"code": -32600, "message": f"Invalid Request: {detail}"}}


def handle_mcp_message(manager: StateManager, message: object) -> dict | list | None:
    """Mensaje JSON-RPC ya parseado: una petición o un lote (FW-234).

    MCP 2025-03-26, que este servidor negocia, exige aceptar lotes. Un lote se
    responde con un array de las respuestas de sus peticiones; si solo trae
    notificaciones no se emite nada."""
    if isinstance(message, list):
        if not message:
            return _invalid_request(None, "lote vacío")
        responses = [resp for resp in (handle_mcp_request(manager, item) for item in message) if resp is not None]
        return responses or None
    return handle_mcp_request(manager, message)


def handle_mcp_request(manager: StateManager, req: object) -> dict | None:
    # FW-234: todo lo que llega con `id` recibe respuesta; una notificación
    # (sin `id`) nunca, y tampoco ejecuta nada: ningún método de este servidor
    # tiene sentido sin que el cliente vea el resultado.
    if not isinstance(req, dict):
        return _invalid_request(None, "la petición debe ser un objeto JSON")
    method = req.get("method")
    req_id = req.get("id")
    is_notification = "id" not in req
    if is_notification:
        return None
    if not isinstance(method, str):
        return _invalid_request(req_id, "falta 'method' o no es texto")

    try:
        if method == "ping":
            return {"jsonrpc": "2.0", "id": req_id, "result": {}}

        elif method == "initialize":
            requested = (req.get("params") or {}).get("protocolVersion")
            if requested is None:
                negotiated = DEFAULT_PROTOCOL_VERSION
            else:
                # MCP convention: respond with the highest supported version ≤ requested.
                # If requested is newer than all supported, return the newest supported so
                # the client can decide. Lexicographic sort == chronological for ISO dates.
                candidates = [v for v in SUPPORTED_PROTOCOL_VERSIONS if v <= requested]
                negotiated = max(candidates) if candidates else max(SUPPORTED_PROTOCOL_VERSIONS)
            return {
                "jsonrpc": "2.0", "id": req_id,
                "result": {
                    "protocolVersion": negotiated,
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "mcp-state-manager", "version": FRAMEWORK_VERSION}
                }
            }

        elif method == "tools/list":
            return {
                "jsonrpc": "2.0", "id": req_id,
                "result": {
                    "tools": [
                        {
                            "name": "get_session_brief",
                            "annotations": {"readOnlyHint": True},
                            "description": "Retorna un JSON de contexto mínimo del proyecto (módulo activo, bugs, blockers).",
                            "inputSchema": {"type": "object", "properties": {}}
                        },
                        {
                            "name": "get_full_state",
                            "annotations": {"readOnlyHint": True},
                            "description": "Retorna el contenido JSON completo del estado del proyecto en disco. USO EXCLUSIVAMENTE DIAGNÓSTICO ante inconsistencia, corrupción o replay; para consultas rutinarias de trabajo usar get_work_context. Para un dato puntual usa lookup_state. Si el estado supera 256 KiB devuelve un resumen con la llamada exacta para pedir cada parte; el volcado completo exige {\"force\": true}.",
                            "inputSchema": {
                                "type": "object",
                                "properties": {
                                    "force": {
                                        "type": "boolean",
                                        "description": "true para volcar el estado completo aunque supere 256 KiB (diagnóstico explícito).",
                                    },
                                },
                            },
                        },
                        {
                            "name": "lookup_state",
                            "annotations": {"readOnlyHint": True},
                            "description": "Consulta puntual del estado (módulo, módulos por estado, recibos, bugs, clarificaciones, decisiones) sin cargar el estado completo. No evalúa la vigencia de los recibos contra el disco. Los kinds bugs/clarifications/decisions responden en <= 12 KiB con campos mínimos y texto recortado a 200 caracteres; si no caben todas las filas devuelven truncated:true, total, returned y next_call con la llamada exacta (mismos filtros y offset siguiente). offset (entero >= 0) es el índice de la primera fila a devolver, sobre las filas ya filtradas; nunca se devuelven cero filas por el tope (la primera se recorta y row_clipped:true).",
                            "inputSchema": {
                                "type": "object",
                                "properties": {
                                    "kind": {
                                        "type": "string",
                                        "enum": list(state_queries.LOOKUP_KINDS),
                                        "description": "Tipo de consulta puntual: 'module' (datos de un módulo), 'modules_by_status' (módulos agrupados por estado), 'receipts' (recibos del módulo), 'bugs', 'clarifications' o 'decisions' (listas filtrables, paginadas con offset).",
                                    },
                                    "module_id": {
                                        "type": "string",
                                        "description": "Identificador del módulo (requerido para kind='module' y kind='receipts'; filtro opcional en bugs, clarifications —INVALID_TARGET si el módulo no existe— y decisions —filtro literal, sin validar).",
                                    },
                                    "status": {
                                        "type": "string",
                                        "description": "Filtro opcional de estado. modules_by_status: not_started, in_progress, ready_for_validation, blocked, completed o dropped. bugs/clarifications/decisions: texto literal del estado del registro (p. ej. Abierto, Resuelto, Aceptada).",
                                    },
                                    "offset": {
                                        "type": "integer",
                                        "minimum": 0,
                                        "description": "Solo bugs/clarifications/decisions: índice de la primera fila a devolver (copia el next_call de la respuesta truncada).",
                                    },
                                },
                                "required": ["kind"],
                            },
                        },
                        {
                            "name": "get_work_context",
                            "annotations": {"readOnlyHint": True},
                            "description": "Retorna contexto selectivo y paginado de una etapa de trabajo.",
                            "inputSchema": {
                                "type": "object",
                                "properties": {
                                    "view": {"type": "string", "enum": sorted(state_queries.VIEWS)},
                                    "module_id": {"type": "string"},
                                    "cursor": {"type": "string"},
                                    "format": {"type": "string", "enum": ["summary", "markdown", "json"], "default": "summary"},
                                },
                                "required": ["view"],
                            },
                        },
                        {
                            "name": "describe_action",
                            "annotations": {"readOnlyHint": True},
                            "description": "Describe el contrato de una acción del motor (campos requeridos, opcionales, tipos, valores válidos, condiciones, defaults, precondiciones y ejemplo).",
                            "inputSchema": {
                                "type": "object",
                                "properties": {
                                    "action": {
                                        "type": "string",
                                        "description": "La acción a describir ("
                                        + ", ".join(sorted(state_core.BUILTIN_ACTIONS))
                                        + ")."
                                    }
                                },
                                "required": ["action"]
                            }
                        },
                        {
                            "name": "mutate_state",
                            "annotations": {"readOnlyHint": False},
                            "description": "Ejecuta una acción de mutación del estado del proyecto, valida la integridad y regenera las proyecciones Markdown.",
                            "inputSchema": {
                                "type": "object",
                                "properties": {
                                    "action": {
                                        "type": "string",
                                        "description": "La acción a realizar ("
                                        + ", ".join(sorted(state_core.BUILTIN_ACTIONS))
                                        + ")."
                                    },
                                    "payload": {
                                        "type": "object",
                                        "description": "El payload JSON con los parámetros específicos de la acción."
                                    }
                                },
                                "required": ["action", "payload"]
                            }
                        }
                    ]
                }
            }

        elif method == "tools/call":
            params = req.get("params", {})
            if not isinstance(params, dict):
                return _query_error_response(
                    req_id,
                    state_queries.QueryError(
                        "INVALID_ARGUMENT", "tools/call.params debe ser un objeto"
                    ),
                )
            name = params.get("name")
            arguments = params.get("arguments", {})
            if arguments is None:
                arguments = {}
            if not isinstance(arguments, dict):
                return _query_error_response(
                    req_id,
                    state_queries.QueryError(
                        "INVALID_ARGUMENT", "tools/call.arguments debe ser un objeto"
                    ),
                )

            if name == "get_session_brief":
                # El lock de escritor se sostiene durante TODA la respuesta:
                # comprobar el manifiesto y luego servir una copia cacheada sin
                # lock deja una ventana TOCTOU antes de que un escritor publique
                # la siguiente transacción multiarchivo (FW-174).
                with state_core.state_write_lock():
                    manager.reload_if_changed(lock_held=True)  # FW-041
                    try:
                        state_core.validate_runtime_state(
                            manager.state_data, root=state_core.WORKSPACE_ROOT
                        )
                    except ValueError as exc:
                        message = str(exc)
                        code = (
                            "LEGACY_MIGRATION_REQUIRED"
                            if message.startswith("LEGACY_MIGRATION_REQUIRED:")
                            else "INVALID_STATE"
                        )
                        return _query_error_response(
                            req_id, state_queries.QueryError(code, message)
                        )
                    # Función compartida (triaje 2026-07-09): el brief cumple
                    # su propia descripción — bugs y blockers incluidos.
                    brief = state_core.build_session_context(manager.state_data)
                    brief_text = json.dumps(brief, indent=2, sort_keys=True)
                return {
                    "jsonrpc": "2.0", "id": req_id,
                    "result": {"content": [{"type": "text", "text": brief_text}]}
                }

            elif name == "get_full_state":
                with state_core.state_write_lock():
                    manager.reload_if_changed(lock_held=True)  # FW-041
                    state_text = json.dumps(manager.state_data, indent=2, sort_keys=True)
                    if (
                        len(state_text.encode("utf-8")) > FULL_STATE_SUMMARY_THRESHOLD
                        and arguments.get("force") is not True
                    ):
                        state_text = json.dumps(
                            _full_state_summary(manager.state_data, len(state_text.encode("utf-8"))),
                            indent=2, ensure_ascii=False,
                        )
                return {
                    "jsonrpc": "2.0", "id": req_id,
                    "result": {"content": [{"type": "text", "text": state_text}]}
                }

            elif name == "lookup_state":
                with state_core.state_write_lock():
                    manager.reload_if_changed(lock_held=True)
                    try:
                        result = state_queries.query_state_point(
                            manager.state_data,
                            kind=arguments.get("kind"),
                            module_id=arguments.get("module_id"),
                            status=arguments.get("status"),
                            offset=arguments.get("offset"),
                        )
                    except state_queries.QueryError as exc:
                        return _query_error_response(req_id, exc)
                return {
                    "jsonrpc": "2.0", "id": req_id,
                    "result": {"content": [{
                        "type": "text",
                        "text": json.dumps(result, indent=2, ensure_ascii=False),
                    }]}
                }

            elif name == "get_work_context":
                try:
                    result = get_work_context(
                        manager,
                        view=arguments.get("view"),
                        module_id=arguments.get("module_id"),
                        cursor=arguments.get("cursor"),
                    )
                except state_queries.QueryError as exc:
                    return _query_error_response(req_id, exc)
                return {
                    "jsonrpc": "2.0", "id": req_id,
                    "result": {"content": [{
                        "type": "text",
                        "text": state_queries.serialize_context(result),
                    }]}
                }

            elif name == "describe_action":
                action = arguments.get("action")
                if not action or not isinstance(action, str):
                    return _query_error_response(req_id, state_queries.QueryError(
                        "INVALID_ARGUMENT", "Se requiere el argumento 'action' (string)."
                    ))
                contract = action_contracts.describe_action(action)
                if contract is None:
                    return _query_error_response(req_id, state_queries.QueryError(
                        "UNKNOWN_ACTION",
                        f"Acción desconocida: '{action}'. Acciones válidas: {sorted(state_core.BUILTIN_ACTIONS)}"
                    ))
                return {
                    "jsonrpc": "2.0", "id": req_id,
                    "result": {"content": [{"type": "text", "text": json.dumps(contract, indent=2, ensure_ascii=False)}]}
                }

            elif name == "mutate_state":
                action = arguments.get("action")
                payload = arguments.get("payload")
                if not action or not isinstance(payload, dict):
                    return _query_error_response(req_id, state_queries.QueryError(
                        "INVALID_ARGUMENT", "Se requieren los argumentos 'action' (string) y 'payload' (object)."
                    ))
                # FW-234: un rechazo del motor (validación, transición ilegal,
                # lock ocupado) es un error de la herramienta que el agente
                # debe leer, no un fallo interno del protocolo.
                try:
                    result_msg = manager.mutate(action, payload)
                except (ValueError, RuntimeError) as exc:
                    log_err(f"mutate_state rechazado ({action}): {exc}")
                    err_msg = str(exc)
                    if err_msg.startswith("Acción desconocida:") or (action not in state_core.BUILTIN_ACTIONS and action not in state_core._load_extension_actions() and action not in {"import_markdown_entities", "retire_codex_budget", "restore_codex_budget", "grandfather_checkpoint_gate"}):
                        code = "UNKNOWN_ACTION"
                        full_msg = f"Acción desconocida: '{action}'. Acciones válidas: {sorted(state_core.BUILTIN_ACTIONS)}"
                    else:
                        code = "MUTATION_REJECTED" if isinstance(exc, ValueError) else "MUTATION_FAILED"
                        contract = action_contracts.describe_action(action)
                        contract_str = f"\n\nContrato de '{action}':\n" + json.dumps(contract, indent=2, ensure_ascii=False) if contract else ""
                        full_msg = f"{err_msg}{contract_str}"
                    return _query_error_response(req_id, state_queries.QueryError(code, full_msg))
                # S3 (TL FW-008 residuo): si algún handler de review disparó el
                # cálculo de diff_hash y el subproceso fresco falló, avisar en
                # la propia respuesta MCP en vez de fallar silenciosamente al
                # cálculo in-process.
                result_msg = _append_hash_warning(result_msg)
                return {
                    "jsonrpc": "2.0", "id": req_id,
                    "result": {"content": [{"type": "text", "text": result_msg}]}
                }
            else:
                return {"jsonrpc": "2.0", "id": req_id, "error": {"code": -32601, "message": f"Tool not found: {name}"}}

        else:
            return {"jsonrpc": "2.0", "id": req_id, "error": {"code": -32601, "message": f"Method not supported: {method}"}}

    except Exception as e:
        log_err(f"Excepción al procesar petición {method}: {e}")
        log_err(traceback.format_exc())
        return {"jsonrpc": "2.0", "id": req_id, "error": {"code": -32603, "message": f"Internal error: {str(e)}"}}

def run_mcp_server(manager: StateManager) -> None:
    # S3 (TL FW-008 residuo): este es el proceso de vida larga que puede
    # atender mutaciones durante una sesión completa — instalar el parche
    # aquí, justo antes de entrar al loop, no al importar el módulo.
    install_fresh_hash_patch()
    log_err("Iniciando servidor MCP...")
    while True:
        try:
            line = sys.stdin.readline()
            if not line:
                log_err("Canal stdin cerrado (EOF). Exiting...")
                break
            line = line.strip()
            if not line:
                continue

            try:
                req = json.loads(line)
            except json.JSONDecodeError as je:
                log_err(f"Error parseando JSON-RPC request: {je}")
                resp = {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Parse error"}}
                sys.stdout.write(json.dumps(resp) + "\n")
                sys.stdout.flush()
                continue

            resp = handle_mcp_message(manager, req)
            if resp is not None:
                sys.stdout.write(json.dumps(resp) + "\n")
                sys.stdout.flush()
        except KeyboardInterrupt:
            break
        except Exception as e:
            log_err(f"Error en bucle principal de MCP: {e}")

def main() -> None:
    try:
        manager = _new_manager()
    except RuntimeError as e:
        # Estado canónico corrupto: mensaje guiado en vez de traceback crudo.
        sys.stderr.write(
            f"\n[STATE_MANAGER] No se pudo cargar el estado: {e}\n\n"
            "Recuperación:\n"
            "  1. Restaurar desde git:   git checkout -- _project/03_project_state.json\n"
            "  2. O reconstruir desde la bitácora: revisar _project/state_log.jsonl\n"
            "     (cada línea es una mutación {ts, action, payload, result}).\n"
            "No se ejecutó ninguna acción.\n"
        )
        sys.exit(2)
    if len(sys.argv) > 1:
        cmd = sys.argv[1]
        if cmd == "describe":
            if len(sys.argv) < 3 or not sys.argv[2].strip():
                print(json.dumps({"error": {"code": "INVALID_ARGUMENT", "message": "Uso: state_manager.py describe <acción>"}}, ensure_ascii=False), file=sys.stderr)
                sys.exit(2)
            action = sys.argv[2].strip()
            contract = action_contracts.describe_action(action)
            if contract is None:
                err = {"error": {"code": "UNKNOWN_ACTION", "message": f"Acción desconocida: '{action}'. Acciones válidas: {sorted(state_core.BUILTIN_ACTIONS)}"}}
                print(json.dumps(err, ensure_ascii=False, indent=2), file=sys.stderr)
                sys.exit(2)
            print(json.dumps(contract, ensure_ascii=False, indent=2))
            sys.exit(0)
        elif cmd == "mutate":
            try:
                install_fresh_hash_patch()
                action, payload_str = None, None
                for i in range(2, len(sys.argv), 2):
                    if i + 1 < len(sys.argv):
                        if sys.argv[i] == "--action": action = sys.argv[i+1]
                        elif sys.argv[i] == "--payload": payload_str = sys.argv[i+1]
                if not action or not payload_str:
                    print("Uso: state_manager.py mutate --action <action> --payload '<json>'", file=sys.stderr)
                    sys.exit(2)
                try:
                    payload = json.loads(payload_str)
                except Exception as e:
                    print(f"Error: payload JSON inválido: {e}", file=sys.stderr)
                    sys.exit(2)
                if action not in state_core.BUILTIN_ACTIONS and action not in state_core._load_extension_actions() and action not in {"import_markdown_entities", "retire_codex_budget", "restore_codex_budget", "grandfather_checkpoint_gate"}:
                    err_msg = f"Acción desconocida: '{action}'. Acciones válidas: {sorted(state_core.BUILTIN_ACTIONS)}"
                    print(f"Error: {err_msg}", file=sys.stderr)
                    sys.exit(2)
                print(_append_hash_warning(manager.mutate(action, payload)))
            except SystemExit:
                raise
            except Exception as e:
                contract = action_contracts.describe_action(action) if action else None
                contract_str = f"\n\nContrato de '{action}':\n" + json.dumps(contract, indent=2, ensure_ascii=False) if contract else ""
                print(f"Error: {e}{contract_str}", file=sys.stderr)
                sys.exit(2)
        elif cmd == "render":
            import projections
            # A1: el CLI render/import/bootstrap ahora adquiere el write lock
            # para evitar race con un mutate en curso vía MCP. El lock es
            # advisory y stale-aware (state_core.state_write_lock), así que
            # no introduce deadlock nuevo.
            try:
                with state_core.state_write_lock():
                    manager.reload_if_changed(lock_held=True)  # re-leer tras el lock
                    projections.render(manager.state_data)
                print("Proyecciones regeneradas.")
            except Exception as e:
                print(f"Error: {e}", file=sys.stderr)
                sys.exit(1)
        elif cmd == "import":
            import projections
            try:
                with state_core.state_write_lock():
                    manager.reload_if_changed(lock_held=True)
                    payload = projections.collect_markdown_entities(manager.state_data)
                    msg = manager._mutate_locked("import_markdown_entities", payload)
                print(f"{msg} "
                      f"Ahora ejecuta 'render' para regenerar las vistas con banner.")
            except Exception as e:
                print(f"Error: {e}", file=sys.stderr)
                sys.exit(1)
        elif cmd == "bootstrap":
            try:
                with state_core.state_write_lock():
                    manager.reload_if_changed(lock_held=True)
                    manager.validate_state(manager.state_data)
                    import projections
                    projections.render(manager.state_data)
                print("Estado validado y proyecciones iniciales generadas.")
            except Exception as e:
                print(f"Error: {e}", file=sys.stderr)
                sys.exit(1)
        elif cmd == "query":
            sys.exit(_run_query_cli(manager, sys.argv[2:]))
        elif cmd == "lookup":
            sys.exit(_run_lookup_cli(manager, sys.argv[2:]))
        else:
            sys.exit(1)
    else:
        run_mcp_server(manager)

if __name__ == "__main__":
    main()
