#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Gate de estructura del checkpoint de builder (3.19.0, FW-074, ADR-046).

Valida que una pregunta A/B al builder cumpla la plantilla obligatoria de
`decision_translation_core` — la parte MECÁNICA del contrato: que la
estructura exista (decisión llana, impacto, analogía, recomendación y 2-3
opciones con su trade-off). La CALIDAD de la analogía y de la recomendación
sigue siendo juicio del modelo; este gate impide la degradación silenciosa
del formato, no garantiza brillantez.

Uso:
    python3 .agent/scripts/validate_checkpoint.py <archivo>
    ... | python3 .agent/scripts/validate_checkpoint.py -

Exit 0 = estructura OK; exit 1 = lista de problemas accionables.
Zero-dependency (stdlib), mismo patrón que validate_acceptance.py.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

# Etiquetas obligatorias de la plantilla, con el mínimo de contenido real
# tras la etiqueta (evita "Analogía: —" como cumplimiento vacío).
_REQUIRED_LABELS: tuple[tuple[str, int, str], ...] = (
    ("Decisión:", 10, "falta 'Decisión:' con la decisión en una frase llana"),
    ("Qué significa para ti:", 15, "falta 'Qué significa para ti:' con el impacto en lenguaje cotidiano"),
    ("Analogía:", 15, "falta 'Analogía:' con una analogía cotidiana que cualquiera entienda"),
    ("Recomendación:", 15, "falta 'Recomendación:' con la opción recomendada y su porqué"),
)

_OPTION_RE = re.compile(r"(?m)^\s*(?:[A-Ea-e][\)\.]|\d[\)\.])\s+(.*)$")
_MIN_OPTIONS = 2
_MAX_OPTIONS = 3


def checkpoint_problems(text: str) -> list[str]:
    """Devuelve la lista de problemas de estructura (vacía == checkpoint OK)."""
    problems: list[str] = []
    lower = text.lower()

    for label, min_len, message in _REQUIRED_LABELS:
        idx = lower.find(label.lower())
        if idx == -1:
            problems.append(message)
            continue
        rest = text[idx + len(label):].splitlines()[0].strip() if idx + len(label) < len(text) else ""
        # El contenido puede continuar en la línea siguiente; aceptar si la
        # primera línea no vacía tras la etiqueta alcanza el mínimo.
        if len(rest) < min_len:
            following = text[idx + len(label):].strip().splitlines()
            rest = following[0].strip() if following else ""
        if len(rest) < min_len:
            problems.append(f"'{label}' presente pero sin contenido real (mínimo {min_len} caracteres)")

    options = _OPTION_RE.findall(text)
    if len(options) < _MIN_OPTIONS:
        problems.append(
            f"faltan opciones: se requieren {_MIN_OPTIONS}-{_MAX_OPTIONS} opciones "
            f"marcadas 'A)' / 'B)' (encontradas: {len(options)})"
        )
    elif len(options) > _MAX_OPTIONS:
        problems.append(
            f"demasiadas opciones ({len(options)}): máximo {_MAX_OPTIONS} — "
            "más opciones diluyen la decisión del builder"
        )
    for i, opt in enumerate(options[:_MAX_OPTIONS], 1):
        if "—" not in opt or len(opt.split("—", 1)[-1].strip()) < 10:
            problems.append(
                f"opción {i} sin trade-off: cada opción declara su consecuencia "
                "en términos del usuario tras '—' (ej. 'A) X — si eliges esto, ...')"
            )

    # Anti-patrón: pregunta técnica cruda sin traducción (el known-bad canónico
    # '¿Postgres o SQLite?' cae aquí porque no cumple ninguna etiqueta).
    return problems


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("Uso: validate_checkpoint.py <archivo|->", file=sys.stderr)
        return 2
    text = sys.stdin.read() if argv[1] == "-" else Path(argv[1]).read_text(encoding="utf-8", errors="ignore")
    problems = checkpoint_problems(text)
    if problems:
        print("CHECKPOINT GATE: FAIL")
        for p in problems:
            print(f"- {p}")
        return 1
    print("CHECKPOINT GATE: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
