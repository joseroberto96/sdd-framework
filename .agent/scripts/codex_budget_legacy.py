#!/usr/bin/env python3
"""Compatibilidad minima para historia Codex anterior a la retirada.

Este modulo no planifica ejecuciones, no estima tokens y no despacha procesos.
Solo valida la forma conocida del puntero antiguo y reproduce su evento durante
un replay o una migracion explicitamente controlada.
"""
from __future__ import annotations

import copy
from pathlib import Path


POINTER_STATUSES = {"planned", "running", "handoff", "blocked", "completed"}
REQUIRED_POINTER_FIELDS = {
    "run_id", "module_id", "unit_id", "unit_index", "unit_total", "status",
    "estimate_ratio", "profile_id", "checkpoint_path", "checkpoint_revision",
    "plan_fingerprint", "last_event_id", "updated_at",
}


def validate_pointer(pointer: object) -> None:
    """Valida exclusivamente el formato historico conocido."""
    if pointer is None:
        return
    if not isinstance(pointer, dict):
        raise ValueError("codex_execution pointer debe ser objeto o null")
    missing = sorted(REQUIRED_POINTER_FIELDS - set(pointer))
    if missing:
        raise ValueError(f"codex_execution pointer incompleto: faltan {missing}")
    for field in ("run_id", "module_id", "profile_id", "checkpoint_path", "updated_at"):
        value = pointer.get(field)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"codex_execution.{field} debe ser string no vacio")
    unit_id = pointer.get("unit_id")
    if unit_id is not None and (not isinstance(unit_id, str) or not unit_id.strip()):
        raise ValueError("codex_execution.unit_id debe ser string o null")
    event_id = pointer.get("last_event_id")
    if event_id is not None and (not isinstance(event_id, str) or not event_id.strip()):
        raise ValueError("codex_execution.last_event_id debe ser string o null")
    for field in ("unit_index", "unit_total", "checkpoint_revision"):
        value = pointer.get(field)
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ValueError(f"codex_execution.{field} debe ser entero no negativo")
    path = pointer["checkpoint_path"]
    parts = Path(path).parts
    if (
        not path.startswith("_project/")
        or Path(path).is_absolute()
        or ".." in parts
        or "." in parts
        or "\\" in path
    ):
        raise ValueError("codex_execution.checkpoint_path debe vivir bajo _project/")
    if pointer.get("status") not in POINTER_STATUSES:
        raise ValueError(
            "codex_execution.status invalido: use planned|running|handoff|blocked|completed"
        )
    if pointer["unit_total"] == 0:
        if pointer["unit_index"] != 0 or unit_id is not None or pointer.get("status") != "blocked":
            raise ValueError(
                "codex_execution sin slices requiere unit_id=null, unit_index=0 y status=blocked"
            )
    elif pointer["unit_index"] < 1:
        raise ValueError("codex_execution.unit_index debe ser positivo cuando hay slices")
    if pointer["unit_index"] > pointer["unit_total"]:
        raise ValueError("codex_execution.unit_index no puede superar unit_total")
    ratio = pointer.get("estimate_ratio")
    if isinstance(ratio, bool) or not isinstance(ratio, (int, float)) or not 0 <= ratio <= 1:
        raise ValueError("codex_execution.estimate_ratio debe estar entre 0 y 1")


def apply_historical_pointer(state: dict, payload: dict) -> str:
    """Reproduce un evento `set_codex_execution_pointer` sin efectos externos."""
    pointer = payload.get("pointer")
    validate_pointer(pointer)
    session = state.setdefault("session", {})
    if "expected_pointer" in payload and session.get("codex_execution") != payload["expected_pointer"]:
        raise ValueError(
            "set_codex_execution_pointer: precondicion fallida; el puntero canonico cambio"
        )
    if pointer is None:
        session.pop("codex_execution", None)
        return "Puntero de ejecucion Codex limpiado (legacy replay)."
    session["codex_execution"] = copy.deepcopy(pointer)
    return "Puntero de ejecucion Codex reproducido (legacy replay)."
