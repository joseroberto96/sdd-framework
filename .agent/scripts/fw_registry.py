#!/usr/bin/env python3
"""Higiene del registro de fricción (5.2.3 L8, FW-303).

La plantilla numera sus FW sin prefijo; un adoptante debe usar ``<PREFIJO>-FW-NNN``
(prefijo corto declarado como ``fw_prefix`` en ``_project/15_environment_profile.md``)
para no chocar con esa numeración. Solo lectura: aquí no se renumera nada.
"""

from __future__ import annotations

import re
from pathlib import Path

PROFILE_REL = Path("_project/15_environment_profile.md")
# Entradas nuevas de un adoptante: las anteriores a esta fecha no se renumeran.
PREFIX_ENFORCED_FROM = "2026-10-01"
# 5.3.0 L9 (FW-342): desde esta fecha, una entrada sin prefijo en un adoptante es un error.
PREFIX_REQUIRED_FROM = "2026-10-13"
# IDs repetidos que ya existían al introducir el control; no se renumera la historia
# (commits y tests los citan). Un duplicado nuevo sí se avisa.
KNOWN_TEMPLATE_DUPLICATES = frozenset({"150", "195"})

_PREFIX_RE = re.compile(r"(?mi)^[ \t]*(?:[-*][ \t]*)?(?:\*\*)?`?fw_prefix`?(?:\*\*)?[ \t]*:[ \t]*([^\n]*)$")
_PREFIX_VALUE_RE = re.compile(r"^[A-Za-z][A-Za-z0-9]{0,11}$")
# Solo secciones de un ID (los rangos `FW-228…235` agrupan filas de tabla, no son duplicados).
_SECTION_RE = re.compile(r"(?m)^#{2,3}[ \t]+(?:([A-Za-z][A-Za-z0-9]{0,11})-)?FW-(\d+)[ \t]+[—-]")
_ROW_RE = re.compile(r"(?m)^\|[ \t]*\*\*(?:([A-Za-z][A-Za-z0-9]{0,11})-)?FW-(\d+)\*\*[ \t]*\|")
_DATE_RE = re.compile(r"\b(\d{4}-\d{2}-\d{2})\b")


def fw_prefix(root: Path) -> str | None:
    """Prefijo declarado en el perfil, o ``None`` si falta, está vacío o es el marcador de plantilla."""
    try:
        text = (Path(root) / PROFILE_REL).read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return None
    match = _PREFIX_RE.search(text)
    if not match:
        return None
    value = match.group(1).strip().strip("`*").strip()
    return value if _PREFIX_VALUE_RE.match(value) else None


def duplicate_ids(text: str) -> list[str]:
    """IDs definidos más de una vez (sección de un ID o fila de tabla) sin estar permitidos."""
    seen: dict[tuple[str, str], int] = {}
    for regex in (_SECTION_RE, _ROW_RE):
        for match in regex.finditer(text):
            key = (match.group(1) or "", match.group(2))
            seen[key] = seen.get(key, 0) + 1
    return sorted(
        f"{prefix + '-' if prefix else ''}FW-{number}"
        for (prefix, number), count in seen.items()
        if count > 1 and (prefix or number not in KNOWN_TEMPLATE_DUPLICATES)
    )


def _section_date(lines: list[str], index: int) -> str | None:
    """Fecha de la sección: la del título o el ``**Fecha:**`` de sus 8 primeras líneas,
    sin entrar en la sección siguiente (L9: antes heredaba la fecha de la vecina)."""
    window = [lines[index]]
    for line in lines[index + 1:index + 8]:
        if line.startswith("#"):
            break
        window.append(line)
    date = _DATE_RE.search(lines[index]) or re.search(
        r"\*\*Fecha:\*\*[ \t]*(\d{4}-\d{2}-\d{2})", "\n".join(window))
    return date.group(1) if date else None


def unprefixed_new_entries(text: str, *, since: str = PREFIX_ENFORCED_FROM) -> list[str]:
    """Secciones ``## FW-NNN`` sin prefijo con fecha igual o posterior a ``since``."""
    lines = text.splitlines()
    found: list[str] = []
    for index, line in enumerate(lines):
        match = _SECTION_RE.match(line)
        if not match or match.group(1):
            continue
        date = _section_date(lines, index)
        if date and date >= since:
            found.append(f"FW-{match.group(2)} (línea {index + 1})")
    return found


def unprefixed_required_entries(text: str, *, since: str = PREFIX_REQUIRED_FROM) -> list[str]:
    """FW nuevas sin prefijo que el doctor trata como error (L9, FW-342).

    Solo cuentan las secciones ``##`` cuyo número no aparece en ninguna otra sección
    sin prefijo, antes o después (hay registros de lo más nuevo a lo más viejo): una
    subsección ``###`` o una ``##`` que repite el número de otra FW es la reincidencia
    de una entrada antigua, que no se renumera (sigue siendo aviso).
    """
    lines = text.splitlines()
    sections = [(index, match.group(2)) for index, line in enumerate(lines)
                if (match := _SECTION_RE.match(line)) and not match.group(1)]
    counts: dict[str, int] = {}
    for _, number in sections:
        counts[number] = counts.get(number, 0) + 1
    found: list[str] = []
    for index, number in sections:
        if not lines[index].startswith("## ") or counts[number] > 1:
            continue
        date = _section_date(lines, index)
        if date and date >= since:
            found.append(f"FW-{number} (línea {index + 1})")
    return found
