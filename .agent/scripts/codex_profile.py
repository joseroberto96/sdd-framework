#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Detección única y sin efectos del harness principal de un proyecto.

Los proyectos antiguos usaban ``Herramienta:`` y los artefactos actuales
generan ``Entorno:``. Codex no debe activarse por una heurística ambigua:
declaraciones contradictorias se devuelven como ``conflict`` para que el
doctor y el sincronizador puedan detenerse de forma observable.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any


PROFILE_REL = Path("_project/15_environment_profile.md")
PROFILE_LABELS = ("Herramienta", "Entorno principal", "Entorno")
PLACEHOLDER_RE = re.compile(r"^\s*\[[^\]]+\]\s*$")


def _value_pattern(label: str) -> re.Pattern[str]:
    return re.compile(
        rf"(?mi)^\s*(?:[-*]\s*)?(?:\*\*)?{re.escape(label)}(?:\*\*)?\s*:\s*([^\n]+?)\s*$"
    )


def _clean_value(value: str) -> str | None:
    value = value.strip().strip("`* ")
    if not value or PLACEHOLDER_RE.fullmatch(value):
        return None
    return value


def _harness_family(value: str) -> str:
    """Normaliza variantes del mismo harness sin ocultar conflictos reales."""
    normalized = value.casefold().strip(" .")
    if re.match(r"^codex(?:$|[\s/_-])", normalized):
        return "codex"
    return normalized


def detect_primary_harness_text(text: str) -> dict[str, Any]:
    """Analiza el texto de perfil y devuelve status, valor y declaraciones."""
    declarations: dict[str, str] = {}
    for label in PROFILE_LABELS:
        match = _value_pattern(label).search(text)
        if match:
            value = _clean_value(match.group(1))
            if value is not None:
                declarations[label] = value

    normalized = {_harness_family(value) for value in declarations.values()}
    if not normalized:
        status = "missing"
        value = None
    elif len(normalized) > 1:
        status = "conflict"
        value = None
    elif "codex" in normalized:
        status = "codex"
        value = "Codex"
    else:
        status = "other"
        value = next(iter(declarations.values()))

    return {
        "status": status,
        "value": value,
        "declarations": declarations,
        "artifacts_present": False,
    }


DELEGATION_LEGACY_SPEC = ".agent/docs/specs/codex_delegation_legacy.md"
_DELEGATION_RE = re.compile(
    r"(?mi)^[ \t]*(?:[-*][ \t]*)?(?:\*\*)?codex_delegation(?:\*\*)?[ \t]*:[ \t]*([^\n]*)$"
)


def codex_delegation_mode(root: Path) -> str:
    """``legacy`` si el perfil lo pide (interruptor de ADR-072); si no, ``default``.

    Solo una línea cuyo valor sea exactamente ``legacy`` (con comillas, negritas
    o un comentario ``#`` finales) lo activa: una plantilla ``default | legacy``
    o ``legacy | default``, o un valor desconocido, no cambia el comportamiento.
    """
    profile = Path(root) / PROFILE_REL
    try:
        text = profile.read_text(encoding="utf-8", errors="ignore") if profile.is_file() else ""
    except OSError:
        return "default"
    for match in _DELEGATION_RE.finditer(text):
        value = match.group(1).split("#", 1)[0].strip().strip("`* ").strip()
        if value.lower() == "legacy":
            return "legacy"
    return "default"


def codex_artifacts_present(root: Path) -> bool:
    codex_dir = Path(root) / ".codex"
    return any((codex_dir / name).is_file() for name in ("hooks.json", "config.toml"))


def detect_primary_harness(root: Path) -> dict[str, Any]:
    root = Path(root).resolve()
    profile = root / PROFILE_REL
    result = detect_primary_harness_text(
        profile.read_text(encoding="utf-8", errors="ignore") if profile.is_file() else ""
    )
    result["profile_path"] = str(PROFILE_REL)
    result["artifacts_present"] = codex_artifacts_present(root)
    return result
