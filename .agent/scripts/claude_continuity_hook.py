#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Hook de continuidad de Claude Code (Framework 4.5 — Lote 3).

Adaptador pequeño de solo lectura para eventos SessionStart en Claude Code.
Entrega la primera página de contexto (startup o recovery) respetando los límites
estrictos de tiempo (3 s consulta), tamaño (12 KiB salida) y degradación segura
sin truncar JSON ni escribir en disco.

Contrato formal:
  .agent/docs/specs/progressive_context.md (ADR-065, Framework 4.5 — Lote 3)

Uso como hook:
  python3 "$CLAUDE_PROJECT_DIR"/.agent/scripts/claude_continuity_hook.py

Uso CLI / inspección:
  python3 .agent/scripts/claude_continuity_hook.py --source startup --raw
  python3 .agent/scripts/claude_continuity_hook.py --source resume --root /path/to/project
"""
from __future__ import annotations

import argparse
import concurrent.futures
import json
import os
import select
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))

import state_core
import state_manager
import state_queries

QUERY_TIMEOUT_SECONDS = 3.0
HOOK_MAX_OUTPUT_BYTES = 12 * 1024  # 12 KiB

STARTUP_SOURCES = frozenset({"startup", "clear"})
RECOVERY_SOURCES = frozenset({"resume", "compact", "fork"})


def _set_workspace_root(root: Path) -> None:
    """Configura las rutas canónicas si el hook corre en otro proyecto o fixture."""
    resolved = root.resolve()
    state_core.WORKSPACE_ROOT = resolved
    state_core.STATE_FILE_PATH = resolved / "_project" / "03_project_state.json"
    state_core.CORE_FILE_PATH = resolved / "_project" / "core.md"
    state_core.BUGS_FILE_PATH = resolved / "_project" / "06_bug_fixes.md"
    state_core.CLARIFICATIONS_FILE_PATH = resolved / "_project" / "07_pending_clarifications.md"
    state_core.DECISIONS_FILE_PATH = resolved / "_project" / "03_arch_decisions.md"
    state_core.SESSION_BRIEF_PATH = resolved / "_project" / "00_session_brief.json"


STDIN_WAIT_SECONDS = 0.2


def _read_stdin_event() -> dict[str, Any]:
    """Lee el evento JSON provisto por Claude Code en stdin, sin bloquear nunca.

    Si en ``STDIN_WAIT_SECONDS`` no hay datos disponibles (entrada abierta que
    nadie cierra), devuelve un evento vacío y sigue.
    """
    try:
        if sys.stdin is None or sys.stdin.isatty():
            return {}
        fd = sys.stdin.fileno()
        deadline = time.monotonic() + STDIN_WAIT_SECONDS
        chunks: list[bytes] = []
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            ready, _, _ = select.select([fd], [], [], remaining)
            if not ready:
                break
            chunk = os.read(fd, 65536)
            if not chunk:
                break
            chunks.append(chunk)
        raw = b"".join(chunks).decode("utf-8").strip()
        if not raw:
            return {}
        data = json.loads(raw)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError, UnicodeError):  # ValueError cubre fileno() y JSON
        return {}


def _resolve_recovery_path(result: dict[str, Any], view: str) -> str:
    gate_status = result.get("gate_status", "NEEDS_INPUT")
    blocking_count = result.get("blocking_count", 0)
    complete = result.get("complete", False)
    next_cursor = result.get("next_cursor")
    refs = result.get("refs", [])

    if gate_status == "PASS" and complete:
        return "Estado nominal. Continuar con el workflow de la etapa."
    if gate_status == "BLOCK":
        return (
            f"Bloqueos activos detectados ({blocking_count}). Resolver incidencias antes de implementar: "
            "un bug se resuelve con resolve_bug o se arregla en su unidad (Estabilización "
            "estabilizacion-BUG-NNN o bug:BUG-NNN)."
        )
    if not complete and next_cursor:
        return f"Paginación pendiente. Continuar con state_manager.get_work_context(view='{view}', cursor={next_cursor!r}) o /reconciliar."
    if refs:
        return f"Referencias pendientes ({len(refs)}). Revisar fuentes documentales o ejecutar /reconciliar."
    return f"Contexto requiere atención ({gate_status}). Ejecutar /reconciliar si hay inconsistencia."


def _execute_timed_query(manager: state_manager.StateManager, view: str, timeout: float) -> dict[str, Any]:
    """Ejecuta la consulta get_work_context bajo un límite estricto de tiempo."""
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(state_manager.get_work_context, manager, view=view)
        try:
            return future.result(timeout=timeout)
        except concurrent.futures.TimeoutError as exc:
            raise TimeoutError(f"Consulta de contexto excedió el límite de {timeout:.1f} s") from exc


def build_continuity_context(
    root: Path | None = None,
    event: dict[str, Any] | None = None,
    *,
    query_timeout: float = QUERY_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    """Construye el sobre de continuidad respetando el contrato formal de 5 campos."""
    effective_root = (root or ROOT).resolve()
    _set_workspace_root(effective_root)

    event_data = event or {}
    session_id = str(event_data.get("session_id") or "sesion_activa")
    source = str(event_data.get("source") or "startup").lower()
    now_iso = datetime.now(timezone.utc).isoformat()

    # 1. Comprobación de modo plantilla
    state_file = effective_root / "_project" / "03_project_state.json"
    if not state_file.exists():
        return {
            "hook": "claude_continuity_hook",
            "version": "1.0",
            "status": "TEMPLATE_MODE",
            "vigencia": {
                "session_id": session_id,
                "source": source,
                "view": None,
                "source_fingerprint": "",
                "timestamp": now_iso,
                "module_id": None,
            },
            "completitud": {
                "complete": True,
                "required_context_complete": True,
                "gate_status": "PASS",
                "blocking_count": 0,
            },
            "cursor": None,
            "referencias": [],
            "via_de_recuperacion": "Framework en modo plantilla. Ejecutar /nuevo-proyecto o /migrar-proyecto para inicializar.",
            "data": {
                "summary": {
                    "framework_mode": "template",
                    "orientation": "Framework en modo plantilla. Para arrancar usa /nuevo-proyecto o /migrar-proyecto.",
                },
                "items": [],
            },
        }

    try:
        raw_state_text = state_file.read_text(encoding="utf-8")
        raw_state = json.loads(raw_state_text)
        if isinstance(raw_state, dict) and raw_state.get("framework_mode") == "template":
            return {
                "hook": "claude_continuity_hook",
                "version": "1.0",
                "status": "TEMPLATE_MODE",
                "vigencia": {
                    "session_id": session_id,
                    "source": source,
                    "view": None,
                    "source_fingerprint": "",
                    "timestamp": now_iso,
                    "module_id": None,
                },
                "completitud": {
                    "complete": True,
                    "required_context_complete": True,
                    "gate_status": "PASS",
                    "blocking_count": 0,
                },
                "cursor": None,
                "referencias": [],
                "via_de_recuperacion": "Framework en modo plantilla. Ejecutar /nuevo-proyecto o /migrar-proyecto para inicializar.",
                "data": {
                    "summary": {
                        "framework_mode": "template",
                        "orientation": "Framework en modo plantilla. Para arrancar usa /nuevo-proyecto o /migrar-proyecto.",
                    },
                    "items": [],
                },
            }
    except Exception as exc:
        return {
            "hook": "claude_continuity_hook",
            "version": "1.0",
            "status": "ERROR",
            "vigencia": {
                "session_id": session_id,
                "source": source,
                "view": None,
                "source_fingerprint": "",
                "timestamp": now_iso,
                "module_id": None,
            },
            "completitud": {
                "complete": False,
                "required_context_complete": False,
                "gate_status": "NEEDS_INPUT",
                "blocking_count": 0,
            },
            "cursor": None,
            "referencias": [],
            "via_de_recuperacion": f"Estado ilegible ({exc}). Restaurar _project/03_project_state.json desde git o ejecutar /reconciliar.",
            "data": {
                "summary": {"error": f"Error leyendo archivo de estado: {exc}"},
                "items": [],
            },
        }

    # 2. Selección de vista según el evento SessionStart.source
    if source in STARTUP_SOURCES:
        target_view = "startup"
    elif source in RECOVERY_SOURCES:
        target_view = "recovery"
    else:
        # Evento desconocido: aviso de origen no reconocido y consulta explícita
        return {
            "hook": "claude_continuity_hook",
            "version": "1.0",
            "status": "UNKNOWN_SOURCE",
            "vigencia": {
                "session_id": session_id,
                "source": source,
                "view": None,
                "source_fingerprint": "",
                "timestamp": now_iso,
                "module_id": None,
            },
            "completitud": {
                "complete": False,
                "required_context_complete": False,
                "gate_status": "NEEDS_INPUT",
                "blocking_count": 0,
            },
            "cursor": None,
            "referencias": [],
            "via_de_recuperacion": f"Origen de SessionStart desconocido ({source!r}). Se recomienda ejecutar /reconciliar o state_manager.get_work_context(view='recovery').",
            "data": {
                "summary": {
                    "warning": f"Origen de sesión no reconocido: {source!r}. Ejecuta /reconciliar.",
                },
                "items": [],
            },
        }

    # 3. Consulta de contexto bajo timeout estricto
    try:
        manager = state_core.StateManager()
        query_result = _execute_timed_query(manager, target_view, query_timeout)
    except TimeoutError as exc:
        return {
            "hook": "claude_continuity_hook",
            "version": "1.0",
            "status": "TIMEOUT",
            "vigencia": {
                "session_id": session_id,
                "source": source,
                "view": target_view,
                "source_fingerprint": "",
                "timestamp": now_iso,
                "module_id": None,
            },
            "completitud": {
                "complete": False,
                "required_context_complete": False,
                "gate_status": "NEEDS_INPUT",
                "blocking_count": 0,
            },
            "cursor": None,
            "referencias": [],
            "via_de_recuperacion": (
                f"La consulta de contexto excedió el límite de {query_timeout:g} s. Repetirla a mano: "
                f"consulta MCP state_manager.get_work_context(view='{target_view}') o, por CLI, "
                f"python3 .agent/scripts/state_manager.py query --view {target_view} "
                "(o ejecutar /reconciliar)."
            ),
            "data": {
                "summary": {"error": str(exc), "target_view": target_view},
                "items": [],
            },
        }
    except Exception as exc:
        return {
            "hook": "claude_continuity_hook",
            "version": "1.0",
            "status": "ERROR",
            "vigencia": {
                "session_id": session_id,
                "source": source,
                "view": target_view,
                "source_fingerprint": "",
                "timestamp": now_iso,
                "module_id": None,
            },
            "completitud": {
                "complete": False,
                "required_context_complete": False,
                "gate_status": "NEEDS_INPUT",
                "blocking_count": 0,
            },
            "cursor": None,
            "referencias": [],
            "via_de_recuperacion": f"Fallo al consultar contexto ({exc}). Ejecutar /reconciliar o restaurar estado canónico.",
            "data": {
                "summary": {"error": str(exc), "target_view": target_view},
                "items": [],
            },
        }

    # 4. Construcción del sobre nominal
    gate_status = query_result.get("gate_status", "NEEDS_INPUT")
    envelope_status = "OK" if gate_status == "PASS" else gate_status

    return {
        "hook": "claude_continuity_hook",
        "version": "1.0",
        "status": envelope_status,
        "vigencia": {
            "session_id": session_id,
            "source": source,
            "view": target_view,
            "source_fingerprint": query_result.get("source_fingerprint", ""),
            "timestamp": now_iso,
            "module_id": query_result.get("module_id"),
        },
        "completitud": {
            "complete": query_result.get("complete", False),
            "required_context_complete": query_result.get("required_context_complete", False),
            "gate_status": gate_status,
            "blocking_count": query_result.get("blocking_count", 0),
        },
        "cursor": query_result.get("next_cursor"),
        "referencias": query_result.get("refs", []),
        "via_de_recuperacion": _resolve_recovery_path(query_result, target_view),
        "data": query_result.get("data", {}),
    }


def render_hook_output(envelope: dict[str, Any], *, max_bytes: int = HOOK_MAX_OUTPUT_BYTES) -> str:
    """Serializa el sobre para Claude Code en `hookSpecificOutput.additionalContext`, con aviso conciso en `systemMessage`, degradando a compacto si excede 12 KiB."""
    status = envelope.get("status", "OK")
    view = envelope.get("vigencia", {}).get("view") or "startup"
    via = envelope.get("via_de_recuperacion", "")
    summary_line = f"Continuidad: {status} ({view}) — {via}".strip()

    envelope_json = json.dumps(envelope, ensure_ascii=False)
    payload = {
        "systemMessage": summary_line,
        "hookSpecificOutput": {
            "hookEventName": "SessionStart",
            "additionalContext": envelope_json,
        },
    }
    rendered = json.dumps(payload, ensure_ascii=False)

    if len(rendered.encode("utf-8")) <= max_bytes:
        return rendered

    # Degradar de forma segura a versión compacta sin truncar JSON
    cursor = envelope.get("cursor")
    recovery_cmd = (
        f"state_manager.get_work_context(view='{view}', cursor={cursor!r})"
        if cursor
        else f"state_manager.get_work_context(view='{view}')"
    )
    compact_envelope = {
        "hook": "claude_continuity_hook",
        "version": "1.0",
        "status": "COMPACT_OVERFLOW",
        "vigencia": envelope.get("vigencia", {}),
        "completitud": {
            "complete": False,
            "required_context_complete": False,
            "gate_status": "NEEDS_INPUT",
            "blocking_count": envelope.get("completitud", {}).get("blocking_count", 0),
        },
        "cursor": cursor,
        "referencias": envelope.get("referencias", []),
        "via_de_recuperacion": f"Salida excedió el límite de {max_bytes // 1024} KiB. Ejecutar consulta paginada: {recovery_cmd} o /reconciliar.",
        "data": {
            "summary": envelope.get("data", {}).get("summary", {}),
            "warning": "Detalle de items omitido para respetar límite de 12 KiB sin truncar JSON.",
            "items": [],
        },
    }
    compact_json = json.dumps(compact_envelope, ensure_ascii=False)
    compact_summary = f"Continuidad: COMPACT_OVERFLOW ({view}) — {compact_envelope['via_de_recuperacion']}".strip()
    compact_payload = {
        "systemMessage": compact_summary,
        "hookSpecificOutput": {
            "hookEventName": "SessionStart",
            "additionalContext": compact_json,
        },
    }
    return json.dumps(compact_payload, ensure_ascii=False)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Hook de continuidad de Claude Code (SessionStart)")
    parser.add_argument("--root", type=Path, default=None, help="Raíz del proyecto")
    parser.add_argument("--source", type=str, default=None, help="Sobrescribe SessionStart.source")
    parser.add_argument("--session-id", type=str, default=None, help="Sobrescribe session_id")
    parser.add_argument("--raw", action="store_true", help="Imprime el envelope JSON directo sin wrapper de transporte")
    args = parser.parse_args(argv)

    root = args.root
    if root is None and "CLAUDE_PROJECT_DIR" in os.environ:
        root = Path(os.environ["CLAUDE_PROJECT_DIR"])

    event = _read_stdin_event()
    if args.source:
        event["source"] = args.source
    if args.session_id:
        event["session_id"] = args.session_id

    envelope = build_continuity_context(root=root, event=event)

    if args.raw:
        print(json.dumps(envelope, indent=2, ensure_ascii=False))
    else:
        print(render_hook_output(envelope))

    return 0


if __name__ == "__main__":
    sys.exit(main())
