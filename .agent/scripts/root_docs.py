#!/usr/bin/env python3
"""Bloque gestionado de los archivos de raíz (ADR-073, 5.2.3 L4).

``AGENTS.md`` y ``CLAUDE.md`` de un proyecto son del proyecto: el framework solo
es dueño del bloque entre marcadores ``GESTIONADO: framework``. Lo de fuera
(Stack, Convenciones, Arquitectura, Restricciones, reglas de dominio) no se toca
nunca, ni al propagar ni al migrar.

El marcador de apertura lleva una huella (``sha256=``) del contenido del bloque
con los sub-bloques ``GENERADO`` normalizados (esos los regenera
``sync_claude_commands.py`` desde el manifest, así que regenerarlos no cambia la
huella). Si el texto de dentro cambia sin pasar por el propagador, la huella deja
de coincidir y el doctor avisa de que se editó a mano. La huella detecta
ediciones accidentales; **no es una firma**: no prueba autenticidad (no lleva
clave) y no cubre el interior de los sub-bloques GENERADO.

Los marcadores se reconocen solo en línea completa y debe haber exactamente un
par: un marcador citado o duplicado en el texto del proyecto hace que el bloque
se considere malformado (``BlockError``) y nadie escribe, en vez de adivinar
dónde acaba.
"""

from __future__ import annotations

import hashlib
import re
from typing import NamedTuple

TAG = "framework"
BEGIN_TMPL = "<!-- BEGIN GESTIONADO: {tag} sha256={digest} — propagate_framework.py; no editar a mano -->"
END_TMPL = "<!-- FIN GESTIONADO: {tag} -->"
_BEGIN_LINE_TMPL = r"(?m)^<!-- BEGIN GESTIONADO: {tag}(?P<attrs>[^>\n]*)-->[ \t]*$"
_END_LINE_TMPL = r"(?m)^<!-- FIN GESTIONADO: {tag} -->[ \t]*$"
_DIGEST_RE = re.compile(r"sha256=([0-9a-f]{12,64})")
_GENERATED_INNER_RE = re.compile(
    r"(<!-- BEGIN GENERADO: [^>]*-->\n).*?(\n<!-- FIN GENERADO: [^>]*-->)", re.DOTALL
)

# Órdenes retiradas: cargar el contexto completo al arrancar o mandar a otro
# arnés a leer CLAUDE.md. La lista es cerrada a propósito (D5): solo estas frases.
# «relevo fresco» y «límite duro» son español corriente: solo cuentan en una línea
# que además habla del presupuesto de contexto de Codex.
DEAD_PHRASES = (
    "BLOCKED_HANDOFF",
    "relevo fresco",
    "límite duro",
    "Leer primero `GUARDRAILS_MASTER.md`",
    "Luego leer `CLAUDE.md` aunque",
)
_BUDGET_CONTEXT_RE = re.compile(r"(?i)\b(codex|presupuesto|contexto|handoff)\b")
# Lo que el doctor trata como orden de lectura prohibida en una raíz.
FORBIDDEN_ORDER_PATTERNS = (
    (re.compile(r"(?i)\bleer\s+`?CLAUDE\.md`?\s+aunque"), "ordena leer CLAUDE.md aunque el arnés no sea Claude"),
    (
        re.compile(r"(?i)\bleer\s+primero\s+`?GUARDRAILS_MASTER\.md`?"),
        "ordena cargar GUARDRAILS_MASTER.md completo al arrancar",
    ),
    (
        re.compile(r"(?i)cat\s+GUARDRAILS_MASTER\.md\s+CLAUDE\.md"),
        "ordena cargar GUARDRAILS_MASTER.md y CLAUDE.md completos al arrancar",
    ),
)


class BlockError(ValueError):
    """Marcadores GESTIONADO duplicados, desparejados o en desorden."""


class Block(NamedTuple):
    start: int
    end: int
    attrs: str
    body: str


def find_block(text: str, tag: str = TAG) -> Block | None:
    """El bloque gestionado, ``None`` si no hay marcadores, ``BlockError`` si están mal."""
    begins = list(re.finditer(_BEGIN_LINE_TMPL.format(tag=re.escape(tag)), text))
    ends = list(re.finditer(_END_LINE_TMPL.format(tag=re.escape(tag)), text))
    if not begins and not ends:
        return None
    if len(begins) != 1 or len(ends) != 1 or ends[0].start() < begins[0].end():
        raise BlockError(
            f"marcadores GESTIONADO: {tag} malformados ({len(begins)} BEGIN, {len(ends)} FIN); "
            "debe haber exactamente un par, cada marcador en su propia línea"
        )
    begin, end = begins[0], ends[0]
    body = text[begin.end() + 1 : end.start() - 1] if end.start() > begin.end() else ""
    return Block(begin.start(), end.end(), begin.group("attrs"), body)


def normalized_body(body: str) -> str:
    """Cuerpo sin el contenido de los sub-bloques GENERADO (los regenera el sync)."""
    return _GENERATED_INNER_RE.sub(lambda m: m.group(1) + "…" + m.group(2), body)


def fingerprint(body: str) -> str:
    return hashlib.sha256(normalized_body(body).encode("utf-8")).hexdigest()[:16]


def render_block(body: str, tag: str = TAG) -> str:
    return BEGIN_TMPL.format(tag=tag, digest=fingerprint(body)) + "\n" + body + "\n" + END_TMPL.format(tag=tag)


def declared_digest(block: Block) -> str | None:
    match = _DIGEST_RE.search(block.attrs)
    return match.group(1) if match else None


def edited_by_hand(block: Block) -> bool:
    """True si la huella declarada no coincide con el cuerpo (o falta)."""
    declared = declared_digest(block)
    return declared is None or declared != fingerprint(block.body)


def replace_block(text: str, body: str, tag: str = TAG) -> str:
    """Sustituye SOLO el bloque gestionado; lo de fuera queda byte a byte igual."""
    block = find_block(text, tag)
    if block is None:
        raise ValueError(f"bloque GESTIONADO '{tag}' no encontrado")
    return text[: block.start] + render_block(body, tag) + text[block.end :]


def forbidden_orders(text: str) -> list[str]:
    return [reason for pattern, reason in FORBIDDEN_ORDER_PATTERNS if pattern.search(text)]


def _is_dead(line: str) -> bool:
    if any(pattern.search(line) for pattern, _ in FORBIDDEN_ORDER_PATTERNS):
        return True
    if "BLOCKED_HANDOFF" in line or "Leer primero `GUARDRAILS_MASTER.md`" in line or "Luego leer `CLAUDE.md` aunque" in line:
        return True
    return ("relevo fresco" in line or "límite duro" in line) and bool(_BUDGET_CONTEXT_RE.search(line))


def strip_dead_lines(text: str) -> tuple[str, list[str]]:
    """Quita las líneas de la lista cerrada; devuelve (texto, líneas quitadas).

    Preserva el final de línea original (CRLF incluido) del resto del texto."""
    kept: list[str] = []
    removed: list[str] = []
    for line in text.splitlines(keepends=True):
        if _is_dead(line):
            removed.append(line.rstrip("\r\n"))
        else:
            kept.append(line)
    return "".join(kept), removed


def read_raw(path) -> tuple[str, str]:
    """(texto con LF, final de línea original): el CRLF de un proyecto se conserva al reescribir."""
    with open(path, encoding="utf-8", newline="") as handle:
        raw = handle.read()
    eol = "\r\n" if "\r\n" in raw else "\n"
    return raw.replace("\r\n", "\n"), eol


def restore_eol(text: str, eol: str) -> str:
    return text.replace("\n", eol) if eol != "\n" else text
