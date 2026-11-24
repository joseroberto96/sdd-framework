#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Adaptador de contexto dinámico para Claude Code (Framework 4.5 — Lote 5B).

Provee inyección dinámica de estado en etapas intermedias (implementation, closure)
mediante la directiva `!` de Claude Code, atacando las consultas repetidas de estado
fuera del arranque y asegurando un resultado inequívoco (éxito o contexto incompleto
con causa y acción de recuperación, sin || true generalizado).

Contrato formal:
  .agent/docs/specs/progressive_context.md (ADR-065, Framework 4.5 — Lote 5B)

Uso en wrapper Claude Code:
  !`python3 "${CLAUDE_PROJECT_DIR:-.}/.agent/scripts/claude_dynamic_context.py --view implementation"`

Uso CLI / inspección:
  python3 .agent/scripts/claude_dynamic_context.py --view implementation
  python3 .agent/scripts/claude_dynamic_context.py --view closure --raw
  python3 .agent/scripts/claude_dynamic_context.py --view implementation --root /path/to/project
"""
from __future__ import annotations

import argparse
import concurrent.futures
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))

import state_core
import state_manager
import state_queries

QUERY_TIMEOUT_SECONDS = 3.0
DEFAULT_MAX_OUTPUT_BYTES = 8 * 1024  # 8 KiB
ALLOWED_VIEWS = frozenset({"implementation", "closure", "recovery", "startup"})


def _set_workspace_root(root: Path) -> None:
    """Configura las rutas canónicas del motor si se ejecuta en fixture o ruta externa."""
    resolved = root.resolve()
    state_core.WORKSPACE_ROOT = resolved
    state_core.STATE_FILE_PATH = resolved / "_project" / "03_project_state.json"
    state_core.CORE_FILE_PATH = resolved / "_project" / "core.md"
    state_core.BUGS_FILE_PATH = resolved / "_project" / "06_bug_fixes.md"
    state_core.CLARIFICATIONS_FILE_PATH = resolved / "_project" / "07_pending_clarifications.md"
    state_core.DECISIONS_FILE_PATH = resolved / "_project" / "03_arch_decisions.md"
    state_core.SESSION_BRIEF_PATH = resolved / "_project" / "00_session_brief.json"


def _resolve_recovery_path(result: dict[str, Any], view: str) -> str:
    """Determina la instrucción inequívoca de recuperación ante inconsistencia o bloqueos."""
    gate_status = result.get("gate_status", "NEEDS_INPUT")
    blocking_count = result.get("blocking_count", 0)
    complete = result.get("complete", False)
    next_cursor = result.get("next_cursor")
    refs = result.get("refs", [])

    if gate_status == "PASS" and complete:
        return f"Estado nominal. Si sospechas desincronización, ejecuta la consulta explícita: state_manager.get_work_context(view='{view}') o /reconciliar."
    if gate_status == "BLOCK":
        return (
            f"Bloqueos activos detectados ({blocking_count}). Resolver incidencias antes de continuar: un bug "
            "se resuelve con resolve_bug o se arregla en su unidad (Estabilización estabilizacion-BUG-NNN o "
            f"bug:BUG-NNN). Consulta explícita: state_manager.get_work_context(view='{view}') o /reconciliar."
        )
    if not complete and next_cursor:
        return f"Paginación pendiente. Continuar con state_manager.get_work_context(view='{view}', cursor={next_cursor!r}) o /reconciliar."
    if refs:
        return f"Referencias documentales pendientes ({len(refs)}). Revisar fuentes o ejecutar consulta explícita: state_manager.get_work_context(view='{view}') o /reconciliar."
    return f"Contexto requiere atención ({gate_status}). Ejecutar /reconciliar o state_manager.get_work_context(view='{view}')."


def _execute_timed_query(
    manager: state_manager.StateManager,
    view: str,
    module_id: str | None,
    cursor: str | None,
    timeout: float,
) -> dict[str, Any]:
    """Ejecuta get_work_context bajo un límite estricto de tiempo."""
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(
            state_manager.get_work_context,
            manager,
            view=view,
            module_id=module_id,
            cursor=cursor,
        )
        try:
            return future.result(timeout=timeout)
        except concurrent.futures.TimeoutError as exc:
            raise TimeoutError(f"Consulta de contexto excedió el límite de {timeout:.1f} s") from exc


def build_dynamic_context(
    view: str,
    module_id: str | None = None,
    cursor: str | None = None,
    root: Path | None = None,
    *,
    query_timeout: float = QUERY_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    """Construye el sobre de contexto dinámico con resultado inequívoco (éxito o contexto incompleto)."""
    effective_root = (root or ROOT).resolve()
    saved_state_core = None
    if root is not None:
        saved_state_core = {
            "root": state_core.WORKSPACE_ROOT,
            "state": state_core.STATE_FILE_PATH,
            "core": state_core.CORE_FILE_PATH,
            "bugs": state_core.BUGS_FILE_PATH,
            "clars": state_core.CLARIFICATIONS_FILE_PATH,
            "decisions": state_core.DECISIONS_FILE_PATH,
            "brief": state_core.SESSION_BRIEF_PATH,
        }
    try:
        _set_workspace_root(effective_root)
        return _build_dynamic_context_inner(
            effective_root=effective_root,
            view=view,
            module_id=module_id,
            cursor=cursor,
            query_timeout=query_timeout,
        )
    finally:
        if saved_state_core is not None:
            state_core.WORKSPACE_ROOT = saved_state_core["root"]
            state_core.STATE_FILE_PATH = saved_state_core["state"]
            state_core.CORE_FILE_PATH = saved_state_core["core"]
            state_core.BUGS_FILE_PATH = saved_state_core["bugs"]
            state_core.CLARIFICATIONS_FILE_PATH = saved_state_core["clars"]
            state_core.DECISIONS_FILE_PATH = saved_state_core["decisions"]
            state_core.SESSION_BRIEF_PATH = saved_state_core["brief"]


def _build_dynamic_context_inner(
    effective_root: Path,
    view: str,
    module_id: str | None,
    cursor: str | None,
    query_timeout: float,
) -> dict[str, Any]:
    now_iso = datetime.now(timezone.utc).isoformat()

    # Validar vista permitida
    if view not in ALLOWED_VIEWS:
        return {
            "adapter": "claude_dynamic_context",
            "version": "1.0",
            "status": "INVALID_VIEW",
            "view": view,
            "module_id": module_id,
            "gate_status": "NEEDS_INPUT",
            "blocking_count": 0,
            "completitud": {
                "complete": False,
                "required_context_complete": False,
                "gate_status": "NEEDS_INPUT",
            },
            "error": f"Vista no permitida: {view!r}. Vistas válidas: {sorted(ALLOWED_VIEWS)}",
            "via_de_recuperacion": f"Especificar una vista válida ({', '.join(sorted(ALLOWED_VIEWS))}) o ejecutar /reconciliar.",
            "data": {},
            "timestamp": now_iso,
        }

    # 1. Comprobación de modo plantilla y existencia de estado
    state_file = effective_root / "_project" / "03_project_state.json"
    if not state_file.exists():
        return {
            "adapter": "claude_dynamic_context",
            "version": "1.0",
            "status": "TEMPLATE_MODE",
            "view": view,
            "module_id": module_id,
            "gate_status": "PASS",
            "blocking_count": 0,
            "completitud": {
                "complete": True,
                "required_context_complete": True,
                "gate_status": "PASS",
            },
            "orientation": "Framework en modo plantilla. Para arrancar un proyecto real usa /nuevo-proyecto o /migrar-proyecto.",
            "via_de_recuperacion": "Framework en modo plantilla. Ejecutar /nuevo-proyecto o /migrar-proyecto para inicializar.",
            "data": {"summary": {"framework_mode": "template"}},
            "timestamp": now_iso,
        }

    # 2. Lectura y validación preliminar de estado
    try:
        raw_state_text = state_file.read_text(encoding="utf-8")
        raw_state = json.loads(raw_state_text)
        if isinstance(raw_state, dict) and raw_state.get("framework_mode") == "template":
            return {
                "adapter": "claude_dynamic_context",
                "version": "1.0",
                "status": "TEMPLATE_MODE",
                "view": view,
                "module_id": module_id,
                "gate_status": "PASS",
                "blocking_count": 0,
                "completitud": {
                    "complete": True,
                    "required_context_complete": True,
                    "gate_status": "PASS",
                },
                "orientation": "Framework en modo plantilla. Para arrancar un proyecto real usa /nuevo-proyecto o /migrar-proyecto.",
                "via_de_recuperacion": "Framework en modo plantilla. Ejecutar /nuevo-proyecto o /migrar-proyecto para inicializar.",
                "data": {"summary": {"framework_mode": "template"}},
                "timestamp": now_iso,
            }
    except PermissionError as exc:
        return {
            "adapter": "claude_dynamic_context",
            "version": "1.0",
            "status": "PERMISSION_DENIED",
            "view": view,
            "module_id": module_id,
            "gate_status": "NEEDS_INPUT",
            "blocking_count": 0,
            "completitud": {
                "complete": False,
                "required_context_complete": False,
                "gate_status": "NEEDS_INPUT",
            },
            "error": f"Permiso denegado al leer _project/03_project_state.json: {exc}",
            "via_de_recuperacion": f"Permiso denegado al acceder al estado. Ejecutar consulta explícita: state_manager.get_work_context(view='{view}') o /reconciliar.",
            "data": {},
            "timestamp": now_iso,
        }
    except Exception as exc:
        return {
            "adapter": "claude_dynamic_context",
            "version": "1.0",
            "status": "ERROR",
            "view": view,
            "module_id": module_id,
            "gate_status": "NEEDS_INPUT",
            "blocking_count": 0,
            "completitud": {
                "complete": False,
                "required_context_complete": False,
                "gate_status": "NEEDS_INPUT",
            },
            "error": f"Error leyendo archivo de estado: {exc}",
            "via_de_recuperacion": f"Estado ilegible o corrupto ({exc}). Restaurar _project/03_project_state.json desde git o ejecutar /reconciliar.",
            "data": {},
            "timestamp": now_iso,
        }

    # 3. Consulta de contexto bajo timeout estricto
    try:
        manager = state_core.StateManager()
        query_result = _execute_timed_query(
            manager,
            view=view,
            module_id=module_id,
            cursor=cursor,
            timeout=query_timeout,
        )
    except TimeoutError as exc:
        return {
            "adapter": "claude_dynamic_context",
            "version": "1.0",
            "status": "TIMEOUT",
            "view": view,
            "module_id": module_id,
            "gate_status": "NEEDS_INPUT",
            "blocking_count": 0,
            "completitud": {
                "complete": False,
                "required_context_complete": False,
                "gate_status": "NEEDS_INPUT",
            },
            "error": str(exc),
            "via_de_recuperacion": f"La consulta de contexto excedió el límite de {query_timeout:.1f} s. Ejecutar manualmente state_manager.get_work_context(view='{view}') o /reconciliar.",
            "data": {},
            "timestamp": now_iso,
        }
    except PermissionError as exc:
        return {
            "adapter": "claude_dynamic_context",
            "version": "1.0",
            "status": "PERMISSION_DENIED",
            "view": view,
            "module_id": module_id,
            "gate_status": "NEEDS_INPUT",
            "blocking_count": 0,
            "completitud": {
                "complete": False,
                "required_context_complete": False,
                "gate_status": "NEEDS_INPUT",
            },
            "error": str(exc),
            "via_de_recuperacion": f"Permiso denegado ({exc}). Ejecutar la consulta explícita vía MCP: state_manager.get_work_context(view='{view}') o /reconciliar.",
            "data": {},
            "timestamp": now_iso,
        }
    except Exception as exc:
        return {
            "adapter": "claude_dynamic_context",
            "version": "1.0",
            "status": "ERROR",
            "view": view,
            "module_id": module_id,
            "gate_status": "NEEDS_INPUT",
            "blocking_count": 0,
            "completitud": {
                "complete": False,
                "required_context_complete": False,
                "gate_status": "NEEDS_INPUT",
            },
            "error": str(exc),
            "via_de_recuperacion": f"Fallo al consultar contexto ({exc}). Ejecutar /reconciliar o restaurar estado canónico.",
            "data": {},
            "timestamp": now_iso,
        }

    # 4. Construcción del sobre nominal
    gate_status = query_result.get("gate_status", "NEEDS_INPUT")
    effective_status = "OK" if gate_status == "PASS" else gate_status
    resolved_mod_id = query_result.get("module_id") or query_result.get("data", {}).get("summary", {}).get("target_id")

    return {
        "adapter": "claude_dynamic_context",
        "version": "1.0",
        "status": effective_status,
        "view": view,
        "module_id": resolved_mod_id,
        "gate_status": gate_status,
        "blocking_count": query_result.get("blocking_count", 0),
        "source_fingerprint": query_result.get("source_fingerprint", ""),
        "timestamp": now_iso,
        "completitud": {
            "complete": query_result.get("complete", False),
            "required_context_complete": query_result.get("required_context_complete", False),
            "gate_status": gate_status,
            "blocking_count": query_result.get("blocking_count", 0),
        },
        "cursor": query_result.get("next_cursor"),
        "referencias": query_result.get("refs", []),
        "via_de_recuperacion": _resolve_recovery_path(query_result, view),
        "data": query_result.get("data", {}),
    }


def render_dynamic_context_markdown(
    envelope: dict[str, Any],
    *,
    max_bytes: int = DEFAULT_MAX_OUTPUT_BYTES,
) -> str:
    """Renderiza el sobre de contexto dinámico como bloque Markdown seguro para Claude Code."""
    status = envelope.get("status", "NEEDS_INPUT")
    view = envelope.get("view", "unknown")
    via_recuperacion = envelope.get("via_de_recuperacion", "/reconciliar")

    if status == "TEMPLATE_MODE":
        orientation = envelope.get("orientation", "Framework en modo plantilla.")
        return f"""<!-- CONTEXTO_DINAMICO: claude_dynamic_context v1.0 (status: TEMPLATE_MODE, view: {view}) -->
### Contexto de etapa (inyección dinámica) — Modo plantilla
- **Estado**: `TEMPLATE_MODE`
- **Vista solicitada**: `{view}`
- **Orientación**: {orientation}
- **Recuperación**: {via_recuperacion}
<!-- FIN_CONTEXTO_DINAMICO -->"""

    if status == "TIMEOUT":
        error = envelope.get("error", "Tiempo de consulta excedido.")
        return f"""<!-- CONTEXTO_DINAMICO: claude_dynamic_context v1.0 (status: TIMEOUT, view: {view}) -->
### Contexto de etapa (inyección dinámica) — Timeout de consulta
- **Estado**: `TIMEOUT`
- **Vista solicitada**: `{view}`
- **Causa**: {error}
- **Acción requerida**: Contexto incompleto por demora en consulta local. No asumir estado ni omitir gates.
- **Recuperación**: {via_recuperacion}
<!-- FIN_CONTEXTO_DINAMICO -->"""

    if status == "PERMISSION_DENIED":
        error = envelope.get("error", "Permiso denegado.")
        return f"""<!-- CONTEXTO_DINAMICO: claude_dynamic_context v1.0 (status: PERMISSION_DENIED, view: {view}) -->
### Contexto de etapa (inyección dinámica) — Permiso denegado
- **Estado**: `PERMISSION_DENIED`
- **Vista solicitada**: `{view}`
- **Causa**: {error}
- **Acción requerida**: Contexto incompleto por restricciones de permisos en el host o filesystem.
- **Recuperación**: {via_recuperacion}
<!-- FIN_CONTEXTO_DINAMICO -->"""

    if status in ("ERROR", "INVALID_VIEW"):
        error = envelope.get("error", "Error desconocido consultando estado.")
        return f"""<!-- CONTEXTO_DINAMICO: claude_dynamic_context v1.0 (status: {status}, view: {view}) -->
### Contexto de etapa (inyección dinámica) — Error de lectura
- **Estado**: `{status}`
- **Vista solicitada**: `{view}`
- **Causa**: {error}
- **Acción requerida**: Contexto incompleto. Estado posiblemente corrupto o desincronizado.
- **Recuperación**: {via_recuperacion}
<!-- FIN_CONTEXTO_DINAMICO -->"""

    # Nominal o Block/Needs_Input
    mod_id = envelope.get("module_id") or "ninguno"
    gate_status = envelope.get("gate_status", "NEEDS_INPUT")
    blocking_count = envelope.get("blocking_count", 0)
    fingerprint = envelope.get("source_fingerprint") or "n/a"
    timestamp = envelope.get("timestamp") or "n/a"

    summary = envelope.get("data", {}).get("summary", {})
    current_phase = summary.get("current_phase") or "phase_01"
    # TL FW-239: con objetivo activo, un dato ausente es desconocido, no «no activo».
    unknown = "desconocido (consulta lookup_state)" if envelope.get("module_id") else None
    module_status = summary.get("module_status") or unknown or "no_activo"
    tier = summary.get("tier") or unknown or "no_clasificado"
    cap_tier = summary.get("capability_tier") or "standard"
    critical_bugs = summary.get("critical_bug_count", 0)
    rejections = summary.get("closure_rejection_count", 0)
    module_spec = summary.get("module_spec")

    spec_line = ""
    if module_spec:
        spec_line = f"- **Spec canónica**: DB={module_spec.get('has_persistence')}, API={module_spec.get('has_public_api')}\n"

    block_warning = ""
    if blocking_count > 0 or gate_status == "BLOCK":
        block_warning = f"- ⚠️ **ATENCIÓN**: {blocking_count} bloqueo(s) activos impiden continuar nominalmente.\n"

    rendered = f"""<!-- CONTEXTO_DINAMICO: claude_dynamic_context v1.0 (status: {status}, view: {view}) -->
### Contexto de etapa (inyección dinámica)
- **Vista**: `{view}` | **Módulo activo**: `{mod_id}` (`{module_status}`, tier `{tier}`)
- **Gate status**: `{gate_status}` | **Bloqueos**: {blocking_count} | **Capacidad**: `{cap_tier}`
- **Vigencia**: fingerprint `{fingerprint[:16]}...` | timestamp `{timestamp}`

#### Resumen de etapa
- **Fase actual**: `{current_phase}`
- **Bugs críticos**: {critical_bugs} | **Rechazos de cierre**: {rejections}
{spec_line}{block_warning}- **Recuperación**: {via_recuperacion}
<!-- FIN_CONTEXTO_DINAMICO -->"""

    if len(rendered.encode("utf-8")) <= max_bytes:
        return rendered

    # Degradar de forma compacta y segura sin truncar markdown abruptamente
    return f"""<!-- CONTEXTO_DINAMICO: claude_dynamic_context v1.0 (status: COMPACT_OVERFLOW, view: {view}) -->
### Contexto de etapa (inyección dinámica) — Resumen compacto (límite excedido)
- **Estado**: `COMPACT_OVERFLOW`
- **Vista**: `{view}` | **Módulo activo**: `{mod_id}`
- **Gate status**: `{gate_status}` | **Bloqueos**: {blocking_count}
- **Aviso**: Salida condensada para respetar el límite estricto de {max_bytes // 1024} KiB.
- **Recuperación**: state_manager.get_work_context(view='{view}') o /reconciliar.
<!-- FIN_CONTEXTO_DINAMICO -->"""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Adaptador de contexto dinámico para Claude Code (Lote 5B)")
    parser.add_argument("--view", type=str, required=True, choices=sorted(ALLOWED_VIEWS), help="Vista de contexto a consultar")
    parser.add_argument("--module-id", type=str, default=None, help="Módulo específico a consultar")
    parser.add_argument("--cursor", type=str, default=None, help="Cursor de paginación")
    parser.add_argument("--root", type=Path, default=None, help="Raíz del proyecto")
    parser.add_argument("--timeout", type=float, default=QUERY_TIMEOUT_SECONDS, help="Timeout estricto en segundos")
    parser.add_argument("--max-bytes", type=int, default=DEFAULT_MAX_OUTPUT_BYTES, help="Límite máximo de salida en bytes")
    parser.add_argument("--format", type=str, choices=["markdown", "json"], default="markdown", help="Formato de salida")
    parser.add_argument("--raw", action="store_true", help="Emite JSON crudo sin formato markdown")
    parser.add_argument("--strict-exit-code", action="store_true", help="Sale con código 1 si el status no es OK")
    args = parser.parse_args(argv)

    root = args.root
    if root is None and "CLAUDE_PROJECT_DIR" in os.environ:
        root = Path(os.environ["CLAUDE_PROJECT_DIR"])

    envelope = build_dynamic_context(
        view=args.view,
        module_id=args.module_id,
        cursor=args.cursor,
        root=root,
        query_timeout=args.timeout,
    )

    if args.raw or args.format == "json":
        print(json.dumps(envelope, indent=2, ensure_ascii=False))
    else:
        print(render_dynamic_context_markdown(envelope, max_bytes=args.max_bytes))

    if args.strict_exit_code and envelope.get("status") not in ("OK", "PASS", "TEMPLATE_MODE"):
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
