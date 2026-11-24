# -*- coding: utf-8 -*-
"""Single source of truth for module state-transition legality.

Imported by both state_core.py (runtime mutations) and validate_transition.py
(git-diff gate) so the rule cannot drift between the two enforcers.
"""
from __future__ import annotations

VALID_MODULE_STATUSES = {
    "not_started", "in_progress", "blocked", "ready_for_validation", "completed", "dropped"
}

# PA-FW-037 (5.3.3): `dropped` es terminal y distinto de `completed`. Solo se
# alcanza por `abandon_module` (motivo + evidencia), nunca por
# `update_module_status`: por eso ningún estado previo lo autoriza aquí y el
# gate de git solo lo acepta respaldado por ese evento en state_log.jsonl.
# Ningún otro estado admite `dropped` como previo: no se sale de él.
TERMINAL_MODULE_STATUSES = {"completed", "dropped"}

# Allowed PREVIOUS statuses for each TARGET status. None == module not yet tracked.
LEGAL_PREV: dict[str, set] = {
    "not_started": {None, "not_started"},
    "in_progress": {None, "not_started", "in_progress", "blocked", "ready_for_validation"},
    "blocked": {None, "not_started", "in_progress", "blocked"},
    "ready_for_validation": {"in_progress", "ready_for_validation"},
    "completed": {"ready_for_validation", "completed"},
    "dropped": {"dropped"},
}


def is_legal(prev_status: str | None, new_status: str | None) -> bool:
    """True if transitioning prev_status -> new_status is authorized."""
    if new_status not in LEGAL_PREV:
        return False
    return prev_status in LEGAL_PREV[new_status]
