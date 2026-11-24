#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Validador de tamaño de archivo — WARN, nunca bloqueo.

FW-108 (Lote A, refactor de modularidad de UI). La descomposición modular no
tenía ningún check mecánico: un archivo de UI podía crecer sin límite (caso
real: `ui.js` con 1168 líneas) y nada lo señalaba, mientras el backend del
mismo proyecto sí quedaba bien descompuesto. Este validador AVISA (nunca
falla) cuando un archivo de producto trackeado por git supera el umbral de
`GUARDRAILS_MASTER.md → §Chunking` (objetivo < 300 líneas, dividir si supera
500).

Decisión de arquitecto (A5, `plan_refactor_modularidad_ui.md`): WARN
informativo, nunca gate — los field reports son una crónica de gates
ruidosos que entrenan `--no-verify`. No se añade al pre-commit; se invoca de
forma informativa desde `/cierre-modulo`.

Uso: python3 .agent/scripts/validate_file_size.py
Exit code: SIEMPRE 0.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

# Objetivo/umbral de líneas — reflejan GUARDRAILS_MASTER.md → §Chunking.
SOFT = 300
HARD = 500

EXCLUDED_PREFIXES = ("_project/", "_feedback/", ".agent/", "tests/")
BINARY_SUFFIXES = {
    ".png", ".jpg", ".jpeg", ".gif", ".ico", ".pdf", ".woff", ".woff2",
    ".ttf", ".eot", ".zip", ".gz", ".lock",
}


def _git_tracked_files(root: Path) -> list[str]:
    """Rutas trackeadas por git (relativas a root, sep='/'). [] si no hay git repo."""
    try:
        out = subprocess.run(
            ["git", "ls-files", "-z"], cwd=root, capture_output=True, timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return []
    if out.returncode != 0:
        return []
    return [p for p in out.stdout.decode("utf-8", "ignore").split("\0") if p]


def _is_probably_binary(sample: bytes) -> bool:
    return b"\0" in sample


def product_files(root: Path) -> list[str]:
    """Archivos de producto trackeados por git, excluyendo artefactos de
    sesión/framework y binarios evidentes."""
    result = []
    for rel_path in _git_tracked_files(root):
        if rel_path.startswith(EXCLUDED_PREFIXES):
            continue
        path = root / rel_path
        if not path.is_file() or path.suffix in BINARY_SUFFIXES:
            continue
        result.append(rel_path)
    return result


def find_oversized(root: Path) -> list[tuple[str, int]]:
    findings = []
    for rel_path in sorted(product_files(root)):
        path = root / rel_path
        try:
            raw = path.read_bytes()
        except OSError:
            continue
        if _is_probably_binary(raw[:4096]):
            continue
        text = raw.decode("utf-8", errors="ignore")
        n_lines = text.count("\n") + (1 if text and not text.endswith("\n") else 0)
        if n_lines > HARD:
            findings.append((rel_path, n_lines))
    return findings


def main(root: Path | None = None) -> int:
    root = root or ROOT
    findings = find_oversized(root)
    for rel_path, n_lines in findings:
        print(
            f"WARN: {rel_path} tiene {n_lines} líneas (> 500) — revisar si "
            f"debe dividirse (GUARDRAILS §Chunking)."
        )
    if not findings:
        print("validate_file_size: OK — ningún archivo de producto supera el umbral.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
