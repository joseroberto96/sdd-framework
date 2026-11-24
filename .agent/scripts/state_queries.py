#!/usr/bin/env python3
"""Selección y paginación pura del contexto de trabajo.

El transporte resuelve las fuentes y ejecuta las validaciones que requieren
filesystem. Este módulo recibe esa instantánea y decide qué obligaciones están
activas, qué falta y cómo empaquetar una respuesta acotada y reanudable.
"""

from __future__ import annotations

import base64
import binascii
import contextlib
import copy
import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any


# Un corchete marcaba plantilla SIEMPRE, así que `datos[0]`, `[RFC 9110]` o
# `[test](tests/real.py)` se rechazaban igual que `[completar]`. Un gate que
# rechaza la escritura normal se desactiva. Ahora la plantilla es una lista
# cerrada de palabras dentro del corchete, no "cualquier corchete que no sea
# una de las tres formas sintácticas legítimas".
_PLACEHOLDER_RE = re.compile(
    r"\[[ \t]*(?:completar|pendiente|por\s+definir|ejemplos?|t[ií]tulo|nombre|"
    r"tbd|tba|fixme|xxx)\b[^\]]*\]",
    re.IGNORECASE,
)
_GENERIC_CONTEXT_MARKER_RE = re.compile(
    r"<!--[ \t]*(?:END_)?CONTEXT_SOURCE\s*:", re.IGNORECASE
)


VIEWS = {"startup", "implementation", "closure", "recovery"}
VIEW_LIMITS = {
    "startup": 12 * 1024,
    "implementation": 24 * 1024,
    "closure": 24 * 1024,
    "recovery": 24 * 1024,
}
CURSOR_MAX_BYTES = 2048
# 5.2.3 L5: títulos de contratos omitidos que viajan en la ref; el resto se cuenta, no se lista.
OMITTED_LISTED_MAX = 30

# Compatibilidad para consumidores que importaban esta constante. La lista
# efectiva se deriva de la matriz y de las condiciones de la consulta.
REQUIRED_SECTIONS = {
    "startup": (),
    "implementation": (
        "scope", "acceptance", "preserve", "dependencies", "invariants",
        "contracts", "approval", "destructive", "specs", "security",
    ),
    "closure": (
        "scope", "acceptance", "preserve", "dependencies", "invariants",
        "contracts", "approval", "destructive", "specs", "security",
        "execution", "review", "closure",
    ),
    "recovery": ("recovery", "journal"),
}

# These are structural requirements that are not represented as a separate
# matrix row.  They are intentionally consumed only by ``context_plan`` below;
# loader, refs, fragments and completeness therefore share one derived plan
# instead of maintaining independent per-view source lists.  Module scope/ACs
# are applicable only to a module target; invariants/contracts remain global
# requirements for detailed work on any explicit target.
STRUCTURAL_SECTIONS = {
    "implementation": ("invariants", "contracts"),
    "closure": ("invariants", "contracts"),
    "recovery": ("recovery", "journal"),
}

SOURCE_REFERENCES = {
    "scope": ("_project/04_active_scope.md", "module_scope"),
    "acceptance": ("_project/08_module_acceptance.md", "module_acceptance"),
    "preserve": (".agent/workflows/cambio-seguro.md", "preserve_inventory"),
    "contracts": ("_project/03_contracts.md", "contracts"),
    "dependencies": (".agent/rules/architecture_constraints.md", "dependencies"),
    "invariants": ("_project/03_invariants.md", "invariants"),
    "journal": ("_project/13_session_journal.md", "recovery_journal"),
    "recovery": (".agent/workflows/reconciliar.md", "recovery_audit"),
    "approval": (".agent/workflows/tdd.md", "approval_receipt"),
    "destructive": ("AGENTS.md", "destructive_guard"),
    "specs": (".agent/skills/spec_contracts.md", "module_specs"),
    "security": (".agent/workflows/cambio-seguro.md", "security_surface"),
    "ui_states": (".agent/workflows/enriquecer-ticket.md", "ui_states"),
    "nfr": ("_project/10_arch_constraints.md", "nfr"),
    "execution": (".agent/workflows/verificar-ejecutable.md", "runtime_execution"),
    "review": (".agent/workflows/preflight-review.md", "independent_review"),
    "closure": (".agent/workflows/cierre-modulo.md", "closure_preconditions"),
    "publish": (".agent/workflows/publicar-github.md", "publish_gate"),
    "preserve_evidence": ("_project/11_file_dependencies.md", "preserve_evidence"),
    "ui_evidence": ("_project/08_module_acceptance.md", "ui_evidence"),
    "nfr_evidence": ("_project/10_arch_constraints.md", "nfr_evidence"),
    "data_model_spec": ("_project/02_technical_plan/data_model.md", "module_data_model"),
    "api_contract_spec": ("_project/02_technical_plan/api_contract.yaml", "module_api_contract"),
}

# Fallback para llamadas puras directas. El transporte carga la misma matriz
# desde `.agent/docs/specs/progressive_context_obligations.json`.
DEFAULT_OBLIGATIONS = (
    {"id": "STATE", "activate_before": "startup", "delivery": "summary",
     "source": "AGENTS.md", "selector": "canonical_state", "evidence": "state_fingerprint"},
    {"id": "SCOPE", "activate_before": "implementation", "delivery": "required_section",
     "source": "_project/04_active_scope.md", "selector": "module_scope", "evidence": "scope_ref"},
    {"id": "TIER", "activate_before": "implementation", "delivery": "summary",
     "source": "GUARDRAILS_MASTER.md", "selector": "work_tier", "evidence": "tier"},
    {"id": "APPROVAL", "activate_before": "implementation", "delivery": "required_section",
     "source": ".agent/workflows/tdd.md", "selector": "approval_receipt", "evidence": "receipt_ref"},
    {"id": "PRESERVE", "activate_before": "implementation", "delivery": "required_section",
     "source": ".agent/workflows/cambio-seguro.md", "selector": "preserve_inventory", "evidence": "preserve_ref"},
    {"id": "DESTRUCTIVE", "activate_before": "implementation", "delivery": "required_section",
     "source": "AGENTS.md", "selector": "destructive_guard", "evidence": "guard_result"},
    {"id": "DEPENDENCIES", "activate_before": "implementation", "delivery": "required_section",
     "source": ".agent/rules/architecture_constraints.md", "selector": "dependencies", "evidence": "dependency_list"},
    {"id": "SPECS", "activate_before": "implementation", "delivery": "required_section",
     "source": ".agent/skills/spec_contracts.md", "selector": "module_specs", "evidence": "spec_ref"},
    {"id": "SECURITY", "activate_before": "implementation", "delivery": "required_section",
     "source": ".agent/workflows/cambio-seguro.md", "selector": "security_surface", "evidence": "security_ref"},
    {"id": "UI_STATES", "activate_before": "implementation", "delivery": "conditional",
     "source": ".agent/workflows/enriquecer-ticket.md", "selector": "ui_states", "evidence": "ui_state_ref"},
    {"id": "NFR", "activate_before": "implementation", "delivery": "conditional",
     "source": "_project/10_arch_constraints.md", "selector": "nfr", "evidence": "nfr_ref"},
    {"id": "EXECUTION", "activate_before": "closure", "delivery": "required_section",
     "source": ".agent/workflows/verificar-ejecutable.md", "selector": "runtime_execution", "evidence": "execution_ref"},
    {"id": "REVIEW", "activate_before": "closure", "delivery": "required_section",
     "source": ".agent/workflows/preflight-review.md", "selector": "independent_review", "evidence": "review_ref"},
    {"id": "CLOSURE", "activate_before": "closure", "delivery": "required_section",
     "source": ".agent/workflows/cierre-modulo.md", "selector": "closure_preconditions", "evidence": "closure_ref"},
    {"id": "RECOVERY", "activate_before": "recovery", "delivery": "required_section",
     "source": ".agent/workflows/reconciliar.md", "selector": "recovery_audit", "evidence": "audit_ref"},
    {"id": "PUBLISH", "activate_before": "publish", "delivery": "conditional",
     "source": ".agent/workflows/publicar-github.md", "selector": "publish_gate", "evidence": "publish_ref"},
)

OBLIGATION_SECTION = {
    "SCOPE": "scope", "APPROVAL": "approval", "PRESERVE": "preserve",
    "DESTRUCTIVE": "destructive", "DEPENDENCIES": "dependencies",
    "INVARIANTS": "invariants", "CONTRACTS": "contracts", "SPECS": "specs",
    "SECURITY": "security", "UI_STATES": "ui_states", "NFR": "nfr",
    "EXECUTION": "execution", "REVIEW": "review", "CLOSURE": "closure",
    "RECOVERY": "recovery", "PUBLISH": "publish",
}
EVIDENCE_SECTION = {
    "PRESERVE": "preserve_evidence",
    "UI_STATES": "ui_evidence",
    "NFR": "nfr_evidence",
}
SPEC_SOURCE_SECTION = {
    "data_model": "data_model_spec",
    "api_contract": "api_contract_spec",
}
CANONICAL_SOURCE_KINDS = {
    "scope": "module", "acceptance": "module",
    "invariants": "global", "contracts": "global", "journal": "global",
    "preserve_evidence": "module", "ui_evidence": "module", "nfr_evidence": "module",
    "data_model_spec": "global", "api_contract_spec": "global",
}
MODULE_OBLIGATIONS = {
    "TIER", "APPROVAL", "PRESERVE", "DESTRUCTIVE", "DEPENDENCIES", "SPECS",
    "SECURITY", "UI_STATES", "NFR", "EXECUTION", "REVIEW", "CLOSURE",
}
VIEW_ORDER = {"startup": 0, "implementation": 1, "closure": 2, "recovery": 3, "publish": 4}
VALID_ACTIVATIONS = frozenset(VIEW_ORDER)
VALID_DELIVERIES = frozenset({"summary", "required_section", "conditional"})
KNOWN_SELECTORS = frozenset(selector for _, selector in SOURCE_REFERENCES.values()) | {
    "canonical_state", "work_tier", "active_scope", "module_acceptance",
}
UI_EVIDENCE_STATES = ("Ideal", "Vacío", "Cargando", "Error", "Parcial")
EVIDENCE_LABELS = {"PRESERVE": "PRESERVAR", "UI_STATES": "de estados UI", "NFR": "NFR"}
BASELINE_BY_ID = {item["id"]: item for item in DEFAULT_OBLIGATIONS}


@dataclass(frozen=True)
class VisibleLine:
    """Línea Markdown visible y su posición original (numeración 1-based)."""

    number: int
    text: str


@dataclass(frozen=True)
class ContextMarker:
    """Marcador normativo activo y su posición original."""

    number: int
    selector: str
    closing: bool


@dataclass(frozen=True)
class MarkdownScan:
    """Vista visible y eventos de marcadores de un documento Markdown."""

    visible_lines: list[VisibleLine]
    context_markers: list[ContextMarker]


@dataclass(frozen=True)
class UIStateEvidence:
    """Evidencia normalizada: estado, comportamiento y referencia."""

    state: str
    behavior: str
    reference: str | None
    is_na: bool = False


_FENCE_OPEN_RE = re.compile(r"^ {0,3}(`{3,}|~{3,})(.*)$")
_CONTEXT_MARKER_RE = re.compile(
    r"^ {0,3}<!--[ \t]*(END_)?CONTEXT_SOURCE[ \t]*:[ \t]*"
    r"([A-Za-z0-9][A-Za-z0-9_-]*)[ \t]*-->$",
    re.IGNORECASE,
)


def _strip_html_comments(line: str, in_comment: bool) -> tuple[str, bool]:
    """Retira comentarios HTML incluso cuando cruzan varias líneas."""
    visible: list[str] = []
    cursor = 0
    while cursor < len(line):
        if in_comment:
            end = line.find("-->", cursor)
            if end < 0:
                return "".join(visible), True
            cursor = end + len("-->")
            in_comment = False
            continue
        start = line.find("<!--", cursor)
        if start < 0:
            visible.append(line[cursor:])
            return "".join(visible), False
        visible.append(line[cursor:start])
        cursor = start + len("<!--")
        in_comment = True
    return "".join(visible), in_comment


def _fence_open(line: str) -> tuple[str, int] | None:
    match = _FENCE_OPEN_RE.match(line)
    if not match:
        return None
    run, info = match.groups()
    # CommonMark does not allow a backtick in the info string of a backtick
    # fence. Applying that rule here prevents a prose line from becoming a
    # hidden fence accidentally.
    if run[0] == "`" and "`" in info:
        return None
    return run[0], len(run)


def _fence_closes(line: str, fence: tuple[str, int]) -> bool:
    character, minimum_length = fence
    pattern = re.compile(rf"^ {{0,3}}{re.escape(character)}{{{minimum_length},}}[ \t]*$")
    return bool(pattern.fullmatch(line))


def scan_markdown(text: str) -> MarkdownScan:
    """Devuelve solo líneas Markdown visibles, conservando su línea original.

    Se ocultan comentarios HTML, fences de backticks o tildes y ejemplos de
    código indentados. Un comentario o fence sin cierre permanece oculto hasta
    EOF. Esta representación es deliberadamente distinta del texto original:
    el loader sigue extrayendo la sección original para conservar evidencia,
    mientras los selectores y validadores operan sobre esta vista visible.
    """
    if not isinstance(text, str):
        return MarkdownScan([], [])
    visible_lines: list[VisibleLine] = []
    context_markers: list[ContextMarker] = []
    in_comment = False
    fence: tuple[str, int] | None = None
    for number, raw_line in enumerate(text.splitlines(keepends=True), start=1):
        line = raw_line.rstrip("\r\n")
        if fence is not None:
            if _fence_closes(line, fence):
                fence = None
            continue
        started_in_comment = in_comment
        if in_comment:
            visible, in_comment = _strip_html_comments(line, True)
        else:
            if not line.startswith("\t") and not re.match(r"^ {4,}", line):
                marker = _CONTEXT_MARKER_RE.fullmatch(line)
                if marker is not None:
                    context_markers.append(ContextMarker(number, marker.group(2), bool(marker.group(1))))
                    continue
            # Recognize a fence only from the original beginning of the
            # Markdown line.  Stripping an ordinary HTML comment first would
            # turn ``<!-- note --> ```text`` into a false fence and hide all
            # following evidence until EOF.
            opened = _fence_open(line)
            if opened is not None:
                fence = opened
                continue
            visible, in_comment = _strip_html_comments(line, False)
        if not visible.strip() and in_comment:
            continue
        if visible.startswith("\t") or re.match(r"^ {4,}", visible):
            continue
        # A comment that began on a previous line can close before a suffix
        # on this line, but that suffix did not begin the original Markdown
        # line and therefore cannot open a block fence.
        if started_in_comment:
            visible_lines.append(VisibleLine(number, visible))
            continue
        visible_lines.append(VisibleLine(number, visible))
    return MarkdownScan(visible_lines, context_markers)


# L8: índice en memoria por consulta. Mientras hay una consulta en curso
# (`query_memo`) cada texto distinto se escanea una sola vez; fuera de ella no
# se recuerda nada. Nunca se persiste.
_QUERY_MEMO: dict | None = None


@contextlib.contextmanager
def query_memo():
    """Abre (o reutiliza) el índice en memoria de la consulta en curso."""
    global _QUERY_MEMO
    if _QUERY_MEMO is not None:
        yield _QUERY_MEMO
        return
    _QUERY_MEMO = {"scan": {}, "derived": {}}
    try:
        yield _QUERY_MEMO
    finally:
        _QUERY_MEMO = None


def memoized_scan(text: str) -> MarkdownScan:
    """`scan_markdown` con memoria dentro de una consulta (llama al atributo del módulo)."""
    memo = _QUERY_MEMO
    if memo is None or not isinstance(text, str):
        return scan_markdown(text)
    cache = memo["scan"]
    found = cache.get(text)
    if found is None:
        found = cache[text] = scan_markdown(text)
    return found


def query_derived(kind: str, text: str, compute):
    """Resultado derivado de un texto, calculado una vez por consulta."""
    memo = _QUERY_MEMO
    if memo is None:
        return compute(text)
    cache = memo["derived"]
    key = (kind, text)
    if key not in cache:
        cache[key] = compute(text)
    return cache[key]


def visible_markdown_lines(text: str) -> list[VisibleLine]:
    """Compatibilidad: devuelve únicamente las líneas Markdown visibles."""
    return memoized_scan(text).visible_lines


class QueryError(ValueError):
    """Error de consulta serializable por MCP y CLI."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(message)


def canonical_json(value: object) -> bytes:
    """Serializa de forma estable y medible en UTF-8."""
    try:
        return json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise QueryError("INVALID_STATE", f"Valor no serializable en la consulta: {exc}") from exc


def serialize_context(value: dict) -> str:
    """Devuelve exactamente el JSON que deben transportar CLI y MCP."""
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise QueryError("INVALID_STATE", f"Contexto no serializable: {exc}") from exc


def serialized_context_size(value: dict) -> int:
    """Tamaño UTF-8 del contexto tal como se emite por CLI/MCP."""
    return len(serialize_context(value).encode("utf-8"))


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _plan_basis_fingerprint(
    view: str, target: str | None, kind: str, state: dict, sources: dict,
) -> str:
    """Huella efímera de las entradas que determinan la política del plan."""
    applicability = sources.get("applicability")
    if applicability is None:
        applicability = parse_applicability(None)
    return _sha256(canonical_json({
        "view": view,
        "target": target,
        "kind": kind,
        "state": state,
        "obligations": sources.get("obligations", DEFAULT_OBLIGATIONS),
        "applicability": applicability,
    }))


def source_fingerprint(state: dict, sources: dict[str, str]) -> str:
    """Calcula la huella de estado, fuentes y validaciones usadas."""
    return _sha256(canonical_json({"state": state, "sources": sources}))


def parse_applicability(content: str | None) -> dict[str, dict]:
    """Lee la clasificación explícita UI/NFR del ticket/AC.

    La ausencia no equivale a ``n/a``. El formato canónico es, por ejemplo,
    ``UI_STATES: required`` o ``NFR: n/a — no hay superficie adicional``.
    """
    result = {
        "UI_STATES": {"status": "missing", "reason": "falta clasificación explícita"},
        "NFR": {"status": "missing", "reason": "falta clasificación explícita"},
    }
    if not isinstance(content, str):
        return result
    pattern = re.compile(
        r"^\s*(?:[-*]\s*)?(UI_STATES|NFR)\s*(?::|=)\s*"
        r"(required|n/a)\b\s*(?:[—-]\s*(.*))?$", re.IGNORECASE
    )
    seen: dict[str, int] = {}
    for visible in visible_markdown_lines(content):
        match = pattern.match(visible.text)
        if not match:
            continue
        identifier = match.group(1).upper()
        status = match.group(2).lower()
        reason = (match.group(3) or "").strip()
        seen[identifier] = seen.get(identifier, 0) + 1
        if seen[identifier] > 1:
            result[identifier] = {"status": "invalid", "reason": "clasificación UI/NFR duplicada"}
        elif status == "n/a" and not reason:
            result[identifier] = {"status": "invalid", "reason": "n/a requiere justificación"}
        else:
            result[identifier] = {"status": status, "reason": reason}
    return result


def _validate_canonical_state(state: dict, *, root: object = None) -> None:
    """Reutiliza el validador canónico sin convertir la consulta en mutación."""
    try:
        from state_core import validate_runtime_state
        validate_runtime_state(state, root=root)
    except ValueError as exc:
        message = str(exc)
        code = "LEGACY_MIGRATION_REQUIRED" if message.startswith("LEGACY_MIGRATION_REQUIRED:") else "INVALID_STATE"
        raise QueryError(code, message) from exc
    except (ImportError, AttributeError, TypeError, KeyError) as exc:
        raise QueryError("INVALID_STATE", f"No se pudo cargar el validador canónico: {exc}") from exc


def _normalise_sources(sources: dict | None) -> dict[str, dict]:
    """Valida la instantánea recibida sin leer archivos.

    La matriz es un contrato, no una sugerencia: una carga que omite, duplica o
    relaja una obligación canónica se rechaza antes de decidir ``PASS``.
    """
    if sources is None:
        return {
            "hashes": {}, "sections": {},
            "obligations": [dict(item) for item in DEFAULT_OBLIGATIONS],
            "applicability": parse_applicability(None),
        }
    if not isinstance(sources, dict):
        raise QueryError("SOURCE_MISSING", "sources debe ser un objeto")
    hashes = sources.get("hashes", {})
    sections = sources.get("sections", {})
    if not isinstance(hashes, dict) or not all(
        isinstance(key, str) and isinstance(value, str) for key, value in hashes.items()
    ):
        raise QueryError("SOURCE_MISSING", "sources.hashes debe ser mapa de strings")
    if not isinstance(sections, dict):
        raise QueryError("SOURCE_MISSING", "sources.sections debe ser objeto")

    obligations = sources.get("obligations", DEFAULT_OBLIGATIONS)
    if not isinstance(obligations, (list, tuple)) or not all(isinstance(item, dict) for item in obligations):
        raise QueryError("SOURCE_MISSING", "sources.obligations debe ser lista de objetos")
    normalised_obligations: list[dict] = []
    identifiers: set[str] = set()
    for raw in obligations:
        identifier = raw.get("id")
        if not isinstance(identifier, str) or not identifier.strip():
            raise QueryError("SOURCE_MISSING", "Cada obligación debe tener un id")
        identifier = identifier.strip()
        if identifier in identifiers:
            raise QueryError("SOURCE_MISSING", f"La obligación {identifier!r} está duplicada")
        if identifier not in BASELINE_BY_ID:
            raise QueryError(
                "SOURCE_MISSING",
                f"La obligación {identifier!r} no tiene contrato implementado",
            )
        identifiers.add(identifier)
        activation = raw.get("activate_before")
        delivery = raw.get("delivery")
        source = raw.get("source")
        selector = raw.get("selector")
        if activation not in VALID_ACTIVATIONS:
            raise QueryError("SOURCE_MISSING", f"La obligación {identifier!r} tiene activación inválida")
        if delivery not in VALID_DELIVERIES:
            raise QueryError("SOURCE_MISSING", f"La obligación {identifier!r} tiene delivery inválido")
        if not isinstance(source, str) or not source.strip() or not isinstance(selector, str) or not selector.strip():
            raise QueryError("SOURCE_MISSING", f"La obligación {identifier!r} no tiene source/selector válidos")
        if selector not in KNOWN_SELECTORS:
            raise QueryError("SOURCE_MISSING", f"Selector desconocido para obligación {identifier!r}: {selector!r}")
        normalised_obligations.append({
            key: copy.deepcopy(value) for key, value in raw.items() if isinstance(key, str)
        })

    supplied_by_id = {item["id"]: item for item in normalised_obligations}
    missing_ids = sorted(set(BASELINE_BY_ID) - set(supplied_by_id))
    if missing_ids:
        raise QueryError(
            "SOURCE_MISSING",
            "sources.obligations no puede omitir obligaciones canónicas: " + ", ".join(missing_ids),
        )
    protected_keys = ("source", "selector", "activate_before", "delivery")
    for identifier, expected in BASELINE_BY_ID.items():
        supplied = supplied_by_id[identifier]
        if any(supplied.get(key) != expected.get(key) for key in protected_keys):
            raise QueryError(
                "SOURCE_MISSING",
                f"La obligación canónica {identifier!r} fue alterada y no puede desactivar controles",
            )

    normalised_sections: dict[str, dict] = {}
    for name, section in sections.items():
        if not isinstance(name, str) or not name.strip() or not isinstance(section, dict):
            raise QueryError("SOURCE_MISSING", "Cada sección debe ser un objeto identificado")
        content = section.get("content")
        if content is not None and not isinstance(content, str):
            raise QueryError("SOURCE_MISSING", f"La sección {name!r} no contiene texto")
        path = section.get("path")
        selector = section.get("selector")
        if not isinstance(path, str) or not path.strip() or not isinstance(selector, str) or not selector.strip():
            raise QueryError("SOURCE_MISSING", f"La sección {name!r} no tiene ruta/selector")
        module_id = section.get("module_id")
        if module_id is not None and (not isinstance(module_id, str) or not module_id.strip()):
            raise QueryError("SOURCE_MISSING", f"La sección {name!r} tiene module_id inválido")
        source_kind = section.get(
            "source_kind", CANONICAL_SOURCE_KINDS.get(name, "shared")
        )
        if source_kind not in {"module", "shared", "global"}:
            raise QueryError("SOURCE_MISSING", f"La sección {name!r} tiene source_kind inválido")
        expected_source_kind = CANONICAL_SOURCE_KINDS.get(name)
        if expected_source_kind is not None and source_kind != expected_source_kind:
            raise QueryError(
                "SOURCE_MISSING",
                f"La sección {name!r} debe conservar source_kind={expected_source_kind!r}",
            )
        obligation_ids = section.get("obligation_ids", [])
        if (
            not isinstance(obligation_ids, list)
            or not all(isinstance(identifier, str) and identifier in supplied_by_id for identifier in obligation_ids)
            or len(set(obligation_ids)) != len(obligation_ids)
        ):
            raise QueryError("SOURCE_MISSING", f"La sección {name!r} tiene obligation_ids inválidos")
        spec_problems = section.get("spec_problems")
        if spec_problems is not None and (not isinstance(spec_problems, list) or not all(isinstance(p, str) for p in spec_problems)):
            raise QueryError("SOURCE_MISSING", f"La sección {name!r} tiene spec_problems inválido")
        omitted = section.get("omitted")
        if omitted is not None and (not isinstance(omitted, list) or not all(isinstance(t, str) for t in omitted)):
            raise QueryError("SOURCE_MISSING", f"La sección {name!r} tiene omitted inválido")
        normalised_sections[name.strip()] = {
            "path": path, "selector": selector, "content": content,
            "source_kind": source_kind, "obligation_ids": list(obligation_ids),
            **({"module_id": module_id.strip()} if isinstance(module_id, str) else {}),
            **({"spec_problems": list(spec_problems)} if isinstance(spec_problems, list) else {}),
            **({"omitted": list(omitted)} if omitted else {}),
            **({"ambiguous": True} if section.get("ambiguous") is True and content is None else {}),
        }

    applicability = sources.get("applicability")
    if applicability is None:
        applicability = parse_applicability(normalised_sections.get("acceptance", {}).get("content"))
    if not isinstance(applicability, dict):
        raise QueryError("SOURCE_MISSING", "sources.applicability debe ser objeto")
    normalised_applicability: dict[str, dict] = {}
    for identifier in ("UI_STATES", "NFR"):
        value = applicability.get(identifier)
        if value is None:
            value = {"status": "missing", "reason": "falta clasificación explícita"}
        elif isinstance(value, str):
            value = {"status": value, "reason": ""}
        if not isinstance(value, dict) or value.get("status") not in {"required", "n/a", "missing", "invalid"}:
            raise QueryError("SOURCE_MISSING", f"Clasificación {identifier} inválida")
        reason = value.get("reason", "")
        if not isinstance(reason, str):
            raise QueryError("SOURCE_MISSING", f"Justificación {identifier} inválida")
        if value.get("status") == "n/a" and not reason.strip():
            value = {"status": "invalid", "reason": "n/a requiere justificación"}
        normalised_applicability[identifier] = {"status": value["status"], "reason": reason.strip()}

    provided_plan = sources.get("plan")
    if provided_plan is not None and not isinstance(provided_plan, dict):
        raise QueryError("SOURCE_MISSING", "sources.plan debe ser un objeto interno")
    result = {
        "hashes": dict(hashes), "sections": normalised_sections,
        "obligations": normalised_obligations, "applicability": normalised_applicability,
    }
    if provided_plan is not None:
        result["plan"] = copy.deepcopy(provided_plan)
    return result


def _target_kind(target: str | None) -> str:
    if target is None:
        return "none"
    if not isinstance(target, str):
        raise QueryError("INVALID_TARGET", "El objetivo debe ser un string")
    if target.startswith("task:") and len(target) > len("task:"):
        return "task"
    if target.startswith("bug:") and len(target) > len("bug:"):
        return "bug"
    return "module"


target_kind = _target_kind


def _implicit_target(in_progress: list[str], statuses: dict, active_cycle_module: str | None) -> str | None:
    """Sin módulo explícito: ciclo vigente, primer in_progress real, o in_progress[0] (5.2.3)."""
    if not in_progress:
        return None
    if isinstance(active_cycle_module, str) and active_cycle_module in in_progress:
        return active_cycle_module
    for candidate in in_progress:
        if statuses.get(candidate) == "in_progress":
            return candidate
    return in_progress[0]


def _select_target(
    state: dict, module_id: str | None, active_cycle_module: str | None = None,
) -> tuple[str | None, str, bool]:
    modules = state.get("modules")
    statuses = state.get("module_statuses")
    if not isinstance(modules, dict) or not isinstance(statuses, dict):
        raise QueryError("INVALID_STATE", "modules y module_statuses son obligatorios")
    in_progress = modules.get("in_progress", [])
    if not isinstance(in_progress, list) or not all(isinstance(value, str) for value in in_progress):
        raise QueryError("INVALID_STATE", "modules.in_progress debe ser lista de strings")
    if module_id is not None and not isinstance(module_id, str):
        raise QueryError("INVALID_TARGET", "El objetivo debe ser un string")
    explicit = module_id is not None
    target = module_id if explicit else _implicit_target(in_progress, statuses, active_cycle_module)
    kind = _target_kind(target)
    if target is not None and kind == "module":
        if target not in statuses:
            raise QueryError("INVALID_TARGET", f"Módulo no declarado: {target}")
        if statuses[target] not in {"in_progress", "ready_for_validation", "blocked", "not_started", "completed"}:
            raise QueryError("INVALID_TARGET", f"Estado de módulo inválido: {target}")
    if target is not None and not target.strip():
        raise QueryError("INVALID_TARGET", "El objetivo no puede estar vacío")
    return target, kind, explicit


def resolve_context_target(
    state: dict, module_id: str | None = None, active_cycle_module: str | None = None,
) -> tuple[str | None, str, bool]:
    """Resuelve y valida el objetivo sin leer fuentes documentales.

    ``active_cycle_module`` es la pista (ya leída por el transporte) del módulo
    de un ``ciclo_started`` vigente; esta función sigue siendo pura.
    """
    if not isinstance(state, dict):
        raise QueryError("INVALID_STATE", "El estado debe ser un objeto JSON")
    return _select_target(state, module_id, active_cycle_module)


def _scope_conflict(target: str | None, explicit: bool, sources: dict[str, dict]) -> bool:
    if not explicit or target is None or _target_kind(target) != "module":
        return False
    scope = sources.get("sections", {}).get("scope")
    if not isinstance(scope, dict):
        return False
    declared = scope.get("module_id")
    return isinstance(declared, str) and bool(declared.strip()) and declared != target


def _scope_missing(target: str | None, explicit: bool, sources: dict[str, dict]) -> bool:
    if not explicit or target is None or _target_kind(target) != "module":
        return False
    scope = sources.get("sections", {}).get("scope")
    if isinstance(scope, dict) and scope.get("ambiguous"):
        return False  # ya sale como ambiguous_source
    return not isinstance(scope, dict) or not isinstance(scope.get("content"), str) or not scope.get("content", "").strip()


def _closure_rejection_is_active(rejection: dict, active_rejection_ids: set[str] | None = None) -> bool:
    """Usa el resultado transportado por el log; el marcador solo es fallback puro."""
    identifier = rejection.get("id")
    if active_rejection_ids is not None:
        legacy_key = "|".join(str(rejection.get(field, "")) for field in ("module_id", "timestamp", "reason"))
        return (
            (isinstance(identifier, str) and identifier in active_rejection_ids)
            or legacy_key in active_rejection_ids
        )
    try:
        from state_core import closure_rejection_is_active
    except ImportError:
        return bool(rejection.get("restored_at")) or not rejection.get("resolved_at")
    marker = rejection.get("resolution_event")
    backed = (
        isinstance(marker, dict)
        and marker.get("action") == "resolve_closure_rejections"
        and marker.get("id") == rejection.get("id")
        and marker.get("timestamp") == rejection.get("resolved_at")
    )
    return closure_rejection_is_active(rejection, resolution_backed=backed)


def _compact(value: Any, limit: int = 512) -> Any:
    if isinstance(value, str):
        return value if len(value) <= limit else value[: limit - 1] + "…"
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    return str(value)[:limit]


def _bounded(value: Any, *, depth: int = 0) -> Any:
    """Conserva decisiones sin permitir que el resumen crezca con el historial."""
    if depth >= 3:
        return _compact(value, 512)
    if isinstance(value, dict):
        return {
            str(key): _bounded(item, depth=depth + 1)
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
            if len(str(key)) <= 128
        }
    if isinstance(value, list):
        bounded = [_bounded(item, depth=depth + 1) for item in value[:64]]
        if len(value) > 64:
            bounded.append(f"… ({len(value) - 64} elementos más)")
        return bounded
    return _compact(value, 1024)


def _review_required(state: dict, target: str | None) -> bool:
    if not target or _target_kind(target) != "module":
        return False
    tier = state.get("work_tiers", {}).get(target, {}) if isinstance(state.get("work_tiers"), dict) else {}
    profile = state.get("execution_profile", {})
    return isinstance(tier, dict) and (
        tier.get("tier") == "tier_2"
        or tier.get("requires_second_review") is True
        or (isinstance(profile, dict) and profile.get("capability_tier") == "limited")
    )


def active_obligations(
    view: str, target: str | None, kind: str, state: dict, sources: dict[str, dict]
) -> list[dict]:
    """Única lista de obligaciones; la reutilizan refs, orden y completitud."""
    current_order = VIEW_ORDER.get(view)
    if current_order is None:
        return []
    result: list[dict] = []
    applicability = sources.get("applicability", {})
    for raw in sources.get("obligations", DEFAULT_OBLIGATIONS):
        identifier = raw.get("id")
        activation = raw.get("activate_before")
        if not isinstance(identifier, str) or not isinstance(activation, str):
            continue
        if view == "recovery":
            active_by_view = activation in {"startup", "recovery"}
        else:
            activation_order = VIEW_ORDER.get(activation)
            active_by_view = activation_order is not None and activation_order <= current_order
        if not active_by_view:
            continue
        if identifier in MODULE_OBLIGATIONS and kind != "module":
            continue
        if identifier == "REVIEW" and not _review_required(state, target):
            continue
        # n/a remains an explicit active classification, but it does not ask
        # for a document section. required/missing/invalid remain pending.
        result.append(raw)
    return result


def context_plan(
    view: str, target: str | None, kind: str, state: dict, sources: dict[str, dict]
) -> dict[str, Any]:
    """Deriva una única selección de obligaciones y secciones para una vista.

    La matriz decide las obligaciones activas y sus secciones; únicamente las
    estructuras globales y el par scope/acceptance de un módulo se añaden como
    precondiciones del contexto detallado. No se usa ``REQUIRED_SECTIONS``:
    esa constante queda solo por compatibilidad con integradores antiguos.
    """
    active = active_obligations(view, target, kind, state, sources)
    structural_requirements = set(STRUCTURAL_SECTIONS.get(view, ()))
    required: set[str] = set(structural_requirements)
    section_obligations: dict[str, list[str]] = {}

    def add_required(section_name: str, obligation_id: str | None = None) -> None:
        required.add(section_name)
        if obligation_id is not None:
            section_obligations.setdefault(section_name, []).append(obligation_id)

    if view in {"implementation", "closure"} and target and kind == "module":
        structural_requirements.update(("scope", "acceptance"))
        required.update(("scope", "acceptance"))
    if kind in {"module", "task"} and target:
        module_specs = state.get("module_specs")
        if isinstance(module_specs, dict):
            target_spec = module_specs.get(target)
            if isinstance(target_spec, dict):
                if target_spec.get("api_contract") == "n/a" and target_spec.get("data_model") == "n/a":
                    structural_requirements.discard("contracts")
                    required.discard("contracts")
    for obligation in active:
        identifier = obligation.get("id")
        section_name = OBLIGATION_SECTION.get(identifier)
        classification = sources.get("applicability", {}).get(identifier, {})
        if section_name and (
            obligation.get("delivery") == "required_section"
            or (
                obligation.get("delivery") == "conditional"
                and classification.get("status") == "required"
            )
        ):
            add_required(section_name, identifier)
        evidence_name = EVIDENCE_SECTION.get(identifier)
        evidence_required = identifier == "PRESERVE" or (
            identifier in {"UI_STATES", "NFR"}
            and classification.get("status") == "required"
        )
        if evidence_name and evidence_required:
            add_required(evidence_name, identifier)
        if identifier == "SPECS" and kind == "module" and target:
            spec_entry = state.get("module_specs", {}).get(target, {})
            if isinstance(spec_entry, dict):
                for field, source_name in SPEC_SOURCE_SECTION.items():
                    if spec_entry.get(field) in {"required", "done"}:
                        add_required(source_name, identifier)
    for section_name, identifiers in section_obligations.items():
        section_obligations[section_name] = list(dict.fromkeys(identifiers))
    ordered_required = sorted(required)
    return {
        "view": view, "target": target, "kind": kind,
        "active": active,
        "required_sections": ordered_required,
        "required_obligations": [item["id"] for item in active],
        "required_normative_sections": sorted(
            section for section in ordered_required
            if section not in set(EVIDENCE_SECTION.values())
        ),
        "required_evidence_sections": sorted(
            section for section in ordered_required
            if section in set(EVIDENCE_SECTION.values())
        ),
        "section_obligations": {
            section: section_obligations[section]
            for section in sorted(section_obligations)
        },
        "structural_requirements": sorted(structural_requirements),
        "basis_fingerprint": _plan_basis_fingerprint(view, target, kind, state, sources),
    }


# Compatibilidad con tests/adopters que usaban el helper privado.
_active_obligations = active_obligations


def _validation_reasons(validation: dict | None, identifier: str) -> list[str]:
    if not isinstance(validation, dict):
        return []
    problems = validation.get("problems", {})
    if isinstance(problems, dict):
        value = problems.get(identifier, [])
        if isinstance(value, list):
            return [str(reason) for reason in value if str(reason).strip()]
    value = validation.get(identifier, [])
    return [str(reason) for reason in value] if isinstance(value, list) else []


def _evidence_lines(content: str) -> list[str]:
    """Devuelve texto documental, no ejemplos dentro de comentarios/fences."""
    return [line.text for line in visible_markdown_lines(content)]


# ── Validación de evidencia documental ───────────────────────────────────────
#
# Este validador se recortó deliberadamente tras seis rondas de falsos positivos
# y cuatro revisiones independientes, todas FAIL. Lo que fallaba no era una
# regla concreta: era la ambición de JUZGAR LA ESTRUCTURA de prosa Markdown que
# escribe la misma persona interesada en que pase. El contenido y el separador
# viajan por el mismo canal, y ese canal tiene infinitas formas; cada arreglo
# cerraba una y dejaba abierta la siguiente.
#
# De las 16 obligaciones del contexto progresivo, 13 se validan contra el estado
# JSON —estructurado, inequívoco— y ninguna ha fallado nunca. Las tres que
# fallaban (PRESERVE, UI_STATES, NFR) son las que leían prosa. Aquí se quedan
# con lo que SÍ es mecánicamente verificable:
#
#   1. la sección existe y tiene contenido real, no plantilla;
#   2. cita al menos una referencia de evidencia;
#   3. las referencias se resuelven: la evidencia es lo que está citado dentro
#      de una etiqueta de evidencia —`— evidencia:` o, en NFR, el campo
#      `Verificación:`, que cuenta como etiqueta—. Sin fallback: todo lo
#      etiquetado se verifica contra el workspace; la prosa fuera de una
#      etiqueta no se interpreta como referencia, ni para bloquear ni para
#      pasar. Límite declarado, no resuelto: un identificador solo se
#      considera declarado en su documento canónico, pero el gate no puede
#      distinguir ahí un identificador recién añadido de uno inventado por
#      quien escribe la evidencia — esa distinción es de la revisión humana.
#
# Lo que no se comprueba ya —que estén los cinco estados UI, que cada NFR tenga
# sus cuatro campos, que una entrada no tome campos de otra— pasa a la revisión
# humana de `/cierre-modulo`, que es lo único que detectó estos defectos las
# seis veces. Un gate que promete un juicio que no puede sostener es peor que
# uno que declara su alcance: da un PASS en el que nadie debería confiar.
#
# Reglas de diseño, destiladas de los seis intentos. Cualquier cambio aquí debe
# respetarlas o volverá a fallar por el mismo sitio:
#
#   A. UNA SOLA VISTA. Todo lee `visible_markdown_lines`. La última evasión
#      consistió en esconder contenido donde el parser miraba crudo y el
#      scanner no.
#   B. ACEPTAR Y VERIFICAR SON LA MISMA EXTRACCIÓN, sin transformar el texto
#      antes de extraer: enmascarar identificadores borró rutas que ya se
#      habían aceptado.
#   C. NINGUNA EXENCIÓN POR CONTEXTO ADYACENTE. "Está precedido de `GET `" o
#      "empieza por `/`" fueron cuatro rondas del mismo bypass. Lo que no se
#      puede resolver no cuenta como evidencia; no se le busca una excusa.
#   D. CITAR NO ES DECLARAR. El documento de evidencia vive bajo `_project/`,
#      así que buscar "aparece en algún sitio" es circular.
#   E. MEDIR EL EFECTO INVERSO: cada regla necesita su caso de prosa legítima
#      que debe seguir pasando, no solo el caso que debe bloquear.

_TEMPLATE_PHRASE_RE = re.compile(
    r"^\s*(?:completar|ejemplos?)\b"
    r"|\[\s*(?:completar|por\s+definir)\b"          # corchete sin cerrar
    r"|\b(?:por|sin|pendiente\s+de)\s+(?:completar|definir)\b"
    r"|#\s*pendiente"
    r"|\b(?:tbd|tba|fixme|xxx)\b",
    re.IGNORECASE | re.MULTILINE,
)


def _placeholder_or_pending(value: str) -> bool:
    """Instrucción de plantilla. Mencionar la palabra en una frase no lo es."""
    return bool(_PLACEHOLDER_RE.search(value) or _TEMPLATE_PHRASE_RE.search(value))


# Regla B: una sola extracción alimenta aceptación y verificación.
_EVIDENCE_PATH_RE = re.compile(
    r"(?<![A-Za-z0-9_])(?:[A-Za-z0-9_.\-]+/)+[A-Za-z0-9_.\-]+"
    r"|(?<![A-Za-z0-9_/])[A-Za-z0-9_\-]+\.[A-Za-z][A-Za-z0-9]{0,31}(?![A-Za-z0-9])"
)
# L8: «ACn» (sin guion), también en listas «AC2/AC10» o «AC2, AC10», cuenta como AC-n.
_EVIDENCE_AC_LIST_RE = re.compile(
    r"(?<![A-Za-z0-9_./\-])AC-?\d+(?:\s*[/,]\s*AC-?\d+)*(?![A-Za-z0-9_/\-]|\.[A-Za-z0-9])",
    re.IGNORECASE,
)
_EVIDENCE_FORMAT_HINT = (
    "Formato aceptado: AC-n, una ruta existente, «(AC-n, …)» al final de la línea o «n/a — razón»"
)
_PENDING_TDD_RE = re.compile(r"^\s*[`*_]*pendiente-tdd[`*_]*\s*:\s*(.+)$", re.IGNORECASE)
_EVIDENCE_ID_RE = re.compile(r"\b(?:AC|RN|ADR|BUG)-[A-Za-z0-9_-]+\b", re.IGNORECASE)
# PA-FW-011 (5.3.1 L3.5): una lista de AC entre paréntesis al final de la línea
# también es una etiqueta; lo etiquetado se extrae y verifica igual (reglas B y D).
_EVIDENCE_TAG_RE = re.compile(
    r"(?:[—–-]\s*evidencia|^\s*(?:[-*+]|\d+[.)])?\s*(?:\*\*)?verificaci[óo]n(?:\*\*)?)\s*:\s*(?:\*\*)?\s*(.+)$"
    r"|\((AC-?\d+(?:\s*[,;/]\s*AC-?\d+)*)\)\s*$",
    re.IGNORECASE,
)
# Exención formal de sección: cuando una sección no cita evidencia, solo pasa si
# declara formalmente no-aplicabilidad con justificación concreta no vacía (\S+).
# El lookahead negativo (?![A-Za-z0-9_\-/]) evita que identificadores o palabras
# compuestas (como `n/a-storage` o `plan-n/a.md`) casen como exención.
_SECTION_NOT_APPLICABLE_RE = re.compile(
    r"^\s*(?:>\s*)?(?:[-*+]|\d+[.)])?\s*(?:(?:preserva\s*:\s*)|(?:[^—–\n]*[—–]\s*))?(?:n\s*/\s*a|no\s+aplica)(?![A-Za-z0-9_\-/])(?:\s*[—–:]\s*|\s+-\s+|\s*\(\s*|\s+)[^\s—–:\-]+",
    re.IGNORECASE | re.MULTILINE,
)
# Regla D: una declaración ocupa una posición, no aparece en cualquier sitio.
_ID_DECLARATION_PATTERNS = (
    # Casilla MARCADA: un criterio pendiente (`- [ ] AC-9`) no declara nada.
    r"^\s*[-*]\s*\[[xX]\]\s*{identifier}\b",
    r"^\s*#{{1,6}}\s+{identifier}\b",
)

# Regla D: una declaración vive en su documento canónico, no en cualquier
# fichero de `_project/`. Sin esto, un identificador podía declararse a sí
# mismo añadiendo `- [x] AC-999` en el mismo fichero donde se cita.
_CANONICAL_DECLARATION = {
    "AC": "08_module_acceptance.md",
    "BUG": "06_bug_fixes.md",
    "RN": "03_invariants.md",
    "ADR": "03_arch_decisions.md",
}


def _evidence_references(value: str) -> tuple[list[str], list[str]]:
    """Rutas e identificadores citados. Sin exenciones (regla C).

    Las rutas se extraen primero; los identificadores se buscan solo sobre el
    texto restante, enmascarando los tramos ya consumidos por rutas. Sin esto,
    `docs/ADR-007-cache.md` exigía además declarar `ADR-007` como identificador
    suelto, aunque el fichero citado existiera.
    """
    ac_ids: list[str] = []
    scanned = value
    for list_match in _EVIDENCE_AC_LIST_RE.finditer(value):
        ac_ids.extend(f"AC-{number}" for number in re.findall(r"(?i)AC-?(\d+)", list_match.group(0)))
        scanned = scanned[:list_match.start()] + " " * (list_match.end() - list_match.start()) + scanned[list_match.end():]
    value = scanned
    path_matches = list(_EVIDENCE_PATH_RE.finditer(value))
    paths = [match.group(0).rstrip(".,;:") for match in path_matches]
    remaining = list(value)
    for match in path_matches:
        for index in range(match.start(), match.end()):
            remaining[index] = " "
    ids = list(dict.fromkeys(
        [*ac_ids, *(identifier.upper() for identifier in _EVIDENCE_ID_RE.findall("".join(remaining)))]
    ))
    return [path for path in paths if path], ids


def _has_evidence_reference(value: str) -> bool:
    paths, ids = _evidence_references(value)
    return bool(paths or ids)


def _declared_identifiers(root: object) -> set[str]:
    from pathlib import Path
    declared: set[str] = set()
    project = Path(root) / "_project"
    if not project.is_dir():
        return declared
    for prefix, canonical_name in _CANONICAL_DECLARATION.items():
        canonical_path = project / canonical_name
        if canonical_path.is_symlink() or not canonical_path.is_file():
            continue
        try:
            text = canonical_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for line in (v.text for v in visible_markdown_lines(text)):   # regla A
            for match in _EVIDENCE_ID_RE.finditer(line):
                identifier = match.group(0).upper()
                if not identifier.startswith(f"{prefix}-"):
                    continue
                quoted = re.escape(match.group(0))
                if any(
                    re.match(pattern.format(identifier=quoted), line, re.IGNORECASE)
                    for pattern in _ID_DECLARATION_PATTERNS
                ):
                    declared.add(identifier)
    return declared


def _reference_path_problems(
    reference: str, root: object = None, declared: set[str] | None = None,
    view: str | None = None,
) -> list[str]:
    """Resuelve lo citado contra el workspace. Lo irresoluble no es evidencia."""
    if root is None:
        return []
    from pathlib import Path
    root_path = Path(root).resolve()
    paths, ids = _evidence_references(reference)
    # «pendiente-tdd: <ruta>»: en implementation la ruta aún puede no existir
    # (el test se escribe después); en closure o sin vista debe existir.
    pending_tdd = view == "implementation" and bool(_PENDING_TDD_RE.match(reference))
    problems: list[str] = []
    for candidate in paths:
        candidate_path = Path(candidate)
        if candidate_path.is_absolute() or ".." in candidate_path.parts:
            problems.append(f"la referencia de evidencia sale del workspace: {candidate}")
            continue
        try:
            resolved = (root_path / candidate_path).resolve()
            if not resolved.is_relative_to(root_path):
                problems.append(f"la referencia de evidencia sale del workspace: {candidate}")
            elif not resolved.exists() and not pending_tdd:
                problems.append(f"la referencia de evidencia no existe: {candidate}")
        except (OSError, ValueError):
            problems.append(f"la referencia de evidencia no se puede verificar: {candidate}")
    if ids:
        known = _declared_identifiers(root) if declared is None else declared
        problems.extend(
            f"la referencia de evidencia no está declarada en _project/: {identifier}"
            for identifier in ids if identifier not in known
        )
    return list(dict.fromkeys(problems))


def _documentary_evidence_problems(
    label: str, content: str, root: object = None, view: str | None = None,
) -> list[str]:
    """Alcance declarado: contenido real y referencias que se resuelven.

    No juzga la estructura interna ni la completitud de inventarios de la sección;
    eso corresponde a la revisión humana de /cierre-modulo — ver
    `.agent/docs/specs/progressive_context.md`.
    """
    lines = [line.text for line in visible_markdown_lines(content)]   # regla A
    visible = "\n".join(lines).strip()
    if not visible:
        return [f"la evidencia {label} no tiene contenido visible"]
    if _placeholder_or_pending(visible):
        return [f"la evidencia {label} conserva contenido de plantilla"]
    tagged = [(match.group(1) or match.group(2)).strip() for match in
              (_EVIDENCE_TAG_RE.search(line) for line in lines) if match]
    if not tagged:
        if _SECTION_NOT_APPLICABLE_RE.search(visible):
            return []
        return [
            f"la evidencia {label} no cita ninguna referencia verificable ni declara n/a: "
            "necesita `— evidencia: <ruta o AC-n>` o `Verificación: <ruta>`. "
            f"{_EVIDENCE_FORMAT_HINT}"
        ]
    declared = _declared_identifiers(root) if root is not None else set()
    problems: list[str] = []
    for reference in tagged:
        if _placeholder_or_pending(reference) or not _has_evidence_reference(reference):
            problems.append(
                f"la evidencia {label} tiene una referencia no verificable. {_EVIDENCE_FORMAT_HINT}"
            )
            continue
        problems.extend(_reference_path_problems(reference, root, declared, view))
    return list(dict.fromkeys(problems))


def _spec_content_problems(field: str, content: str | None) -> list[str]:
    if not isinstance(content, str) or not content.strip():
        return [f"falta contenido real de {field}"]
    visible = "\n".join(_evidence_lines(content))
    if _placeholder_or_pending(visible) or "modo: plantilla" in visible.casefold():
        return [f"{field} conserva contenido de plantilla"]
    if field == "data_model":
        if not re.search(r"^#{2,6}\s+[^#\n]+$", visible, re.MULTILINE):
            return ["data_model requiere al menos una entidad declarada"]
    elif field == "api_contract":
        if not re.search(r"^\s{0,4}/[^:#\n]+\s*:", visible, re.MULTILINE):
            return ["api_contract requiere al menos un path declarado"]
    return []


def _evidence_problems(
    identifier: str, content: str | None, root: object = None, view: str | None = None,
) -> list[str]:
    if not isinstance(content, str) or not content.strip():
        return ["fuente de evidencia ausente o vacía"]
    if memoized_scan(content).context_markers:
        return ["la evidencia no puede usar un marcador normativo genérico"]
    label = EVIDENCE_LABELS.get(identifier)
    if label is None:
        return []
    return _documentary_evidence_problems(label, content, root, view)


def _state_obligation_delivered(
    identifier: str, state: dict, target: str | None, kind: str,
    sources: dict[str, dict] | None = None, validation: dict | None = None,
) -> bool:
    if identifier == "STATE":
        return True
    if kind != "module" or not target:
        return True
    if identifier == "TIER":
        entry = state.get("work_tiers", {}).get(target) if isinstance(state.get("work_tiers"), dict) else None
        return isinstance(entry, dict) and entry.get("tier") in {"tier_0", "tier_1", "tier_2"}
    if identifier == "SPECS":
        specs = state.get("module_specs")
        entry = specs.get(target) if isinstance(specs, dict) else None
        if not isinstance(entry, dict) or not all(
            isinstance(entry.get(field), str) and entry.get(field).strip()
            for field in ("data_model", "api_contract", "classified_at", "classified_by")
        ):
            return False
        for field, source_name in SPEC_SOURCE_SECTION.items():
            if entry.get(field) in {"required", "done"}:
                section_obj = (sources or {}).get("sections", {}).get(source_name, {}) if isinstance((sources or {}).get("sections"), dict) else {}
                content = section_obj.get("content") if isinstance(section_obj, dict) else None
                problems = section_obj.get("spec_problems", []) if isinstance(section_obj, dict) else []
                if _spec_content_problems(field, content) or problems:
                    return False
        return True
    if identifier == "APPROVAL":
        tier = state.get("work_tiers", {}).get(target, {}).get("tier")
        if tier != "tier_2":
            return True
        if _validation_reasons(validation, identifier):
            return False
        try:
            import workflow_receipts
            return not workflow_receipts.approval_receipt_state_problems(state, target)
        except (ImportError, AttributeError):
            return False
    if identifier == "EXECUTION":
        if _validation_reasons(validation, identifier):
            return False
        try:
            import workflow_receipts
            return not workflow_receipts.execution_problems(state, target)
        except (ImportError, AttributeError):
            return False
    if identifier == "REVIEW":
        if _validation_reasons(validation, identifier):
            return False
        try:
            import workflow_receipts
            return not workflow_receipts.review_problems(state, target)
        except (ImportError, AttributeError):
            return False
    if identifier in {"UI_STATES", "NFR"}:
        classification = (sources or {}).get("applicability", {}).get(identifier, {})
        return classification.get("status") in {"n/a", "required"}
    if identifier == "CLOSURE":
        return not _validation_reasons(validation, identifier)
    return True


def _obligation_reference(obligation: dict, section_name: str | None) -> tuple[str, str]:
    if section_name in SOURCE_REFERENCES:
        return SOURCE_REFERENCES[section_name]
    return str(obligation.get("source") or obligation.get("id")), str(obligation.get("selector") or obligation.get("id"))


def _obligation_requirements(
    view: str, target: str | None, kind: str, state: dict, sources: dict[str, dict],
    validation: dict | None = None, active: list[dict] | None = None,
    workspace_root: object = None,
) -> tuple[list[str], list[dict]]:
    active = active if active is not None else active_obligations(view, target, kind, state, sources)
    required_ids = [item["id"] for item in active]
    missing: list[dict] = []
    sections = sources.get("sections", {})
    applicability = sources.get("applicability", {})
    for obligation in active:
        identifier = obligation["id"]
        section_name = OBLIGATION_SECTION.get(identifier)
        section = sections.get(section_name) if section_name else None
        classification = applicability.get(identifier, {}) if identifier in {"UI_STATES", "NFR"} else {}
        needs_section = obligation.get("delivery") == "required_section" or (
            obligation.get("delivery") == "conditional" and classification.get("status") == "required"
        )
        reasons: list[str] = []
        validation_details: list[str] = []
        if needs_section:
            if isinstance(section, dict) and section.get("ambiguous"):
                reasons.append("ambiguous_source")
            elif not isinstance(section, dict) or not isinstance(section.get("content"), str):
                reasons.append("missing_source")
            elif not section.get("content", "").strip():
                reasons.append("empty_source")
            elif (
                kind == "module" and target and section.get("source_kind") == "module"
                and section.get("module_id") and section.get("module_id") != target
            ):
                reasons.append("wrong_module")
        evidence_name = EVIDENCE_SECTION.get(identifier)
        evidence_required = identifier == "PRESERVE" or (
            identifier in {"UI_STATES", "NFR"}
            and classification.get("status") == "required"
        )
        evidence_section = sections.get(evidence_name) if evidence_name else None
        evidence_issues: list[str] = []
        if evidence_name and evidence_required:
            if isinstance(evidence_section, dict) and evidence_section.get("ambiguous"):
                reasons.append("ambiguous_source")
            elif (
                not isinstance(evidence_section, dict)
                or not isinstance(evidence_section.get("content"), str)
                or not evidence_section.get("content", "").strip()
            ):
                reasons.append("missing_evidence")
            else:
                if (
                    kind == "module" and target
                    and evidence_section.get("source_kind") == "module"
                    and evidence_section.get("module_id")
                    and evidence_section.get("module_id") != target
                ):
                    reasons.append("wrong_module")
                evidence_issues = _evidence_problems(
                    identifier, evidence_section.get("content"), workspace_root, view=view
                )
                if evidence_issues:
                    reasons.append("invalid_evidence")
        if identifier in {"UI_STATES", "NFR"} and classification.get("status") in {"missing", "invalid"}:
            reasons.append("missing_applicability" if classification.get("status") == "missing" else "invalid_applicability")
        if not _state_obligation_delivered(identifier, state, target, kind, sources, validation):
            validation_reasons = _validation_reasons(validation, identifier)
            if validation_reasons:
                validation_details = [_compact(reason, 512) for reason in validation_reasons]
                for validation_reason in validation_reasons:
                    lowered = validation_reason.lower()
                    if "stale" in lowered:
                        stable_reason = "stale_receipt"
                    elif identifier == "APPROVAL" and ("ausente" in lowered or "missing" in lowered):
                        stable_reason = "missing_receipt"
                    elif identifier in {"EXECUTION", "REVIEW"} and ("ausente" in lowered or "missing" in lowered):
                        stable_reason = "missing_evidence"
                    else:
                        stable_reason = "invalid_evidence"
                    if stable_reason not in reasons:
                        reasons.append(stable_reason)
            elif identifier == "APPROVAL":
                reasons.append("missing_receipt")
            elif identifier in {"EXECUTION", "REVIEW"}:
                reasons.append("missing_evidence")
            elif identifier == "SPECS":
                reasons.append("missing_evidence")
                validation_details = []
                specs = state.get("module_specs", {})
                entry = specs.get(target, {}) if isinstance(specs, dict) else {}
                if isinstance(entry, dict):
                    for field, source_name in SPEC_SOURCE_SECTION.items():
                        if entry.get(field) in {"required", "done"}:
                            section_dict = sections.get(source_name, {}) if isinstance(sections.get(source_name), dict) else {}
                            content = section_dict.get("content")
                            problems = section_dict.get("spec_problems", [])
                            validation_details.extend(_spec_content_problems(field, content))
                            if isinstance(problems, list):
                                validation_details.extend([str(p) for p in problems])
            elif identifier == "TIER":
                reasons.append("missing_state")
        if not reasons:
            continue
        reference_section_name = evidence_name if evidence_issues or "missing_evidence" in reasons and evidence_name else section_name
        reference_section = sections.get(reference_section_name) if reference_section_name else None
        path, selector = _obligation_reference(obligation, reference_section_name)
        ref = {
            "section": reference_section_name or identifier.lower(),
            "path": reference_section.get("path", path) if isinstance(reference_section, dict) else path,
            "selector": reference_section.get("selector", selector) if isinstance(reference_section, dict) else selector,
            "obligation": identifier, "reason": reasons[0],
        }
        if len(reasons) > 1:
            ref["reasons"] = reasons
        if validation_details:
            ref["validation_reasons"] = validation_details
        if evidence_issues:
            ref["validation_reasons"] = [
                _compact(reason, 512) for reason in evidence_issues
            ]
        if identifier in {"UI_STATES", "NFR"} and classification.get("reason"):
            ref["applicability_reason"] = classification["reason"]
        missing.append(ref)
    return required_ids, missing


def _merge_refs(refs: list[dict], extra: list[dict]) -> list[dict]:
    """Deduplica referencias documentales sin perder obligaciones ni razones."""
    merged: list[dict] = []
    for ref in [*refs, *extra]:
        candidate = copy.deepcopy(ref)
        same = next(
            (
                existing for existing in merged
                if existing.get("section") == candidate.get("section")
                and existing.get("path") == candidate.get("path")
                and existing.get("selector") == candidate.get("selector")
                and existing.get("obligation") == candidate.get("obligation")
                and existing.get("reason") == candidate.get("reason")
            ),
            None,
        )
        if same is None:
            merged.append(candidate)
            continue
        for key in ("reasons", "obligation_ids", "validation_reasons"):
            values = list(same.get(key, [])) if isinstance(same.get(key), list) else []
            incoming = candidate.get(key, [])
            if isinstance(incoming, list):
                values.extend(item for item in incoming if item not in values)
            if values:
                same[key] = values
    return sorted(
        merged,
        key=lambda ref: (
            str(ref.get("section")), str(ref.get("obligation", "")),
            str(ref.get("reason", "")), str(ref.get("path", "")),
        ),
    )


def _required_sections(
    view: str, target: str | None, kind: str, state: dict, sources: dict[str, dict],
    active: list[dict] | None = None, planned_sections: list[str] | None = None,
) -> tuple[list[str], list[str]]:
    if active is None or planned_sections is None:
        plan = context_plan(view, target, kind, state, sources)
        active = plan["active"]
        ordered = plan["required_sections"]
    else:
        ordered = list(planned_sections)
    sections = sources.get("sections", {})
    missing: list[str] = []
    for name in ordered:
        section = sections.get(name)
        if not isinstance(section, dict) or not isinstance(section.get("content"), str):
            missing.append(name)
            continue
        if not section.get("content", "").strip():
            missing.append(name)
            continue
        if (
            kind == "module" and target and section.get("source_kind") == "module"
            and section.get("module_id") and section.get("module_id") != target
        ):
            missing.append(name)
    return ordered, missing


def _reusable_context_plan(
    plan: object, view: str, target: str | None, kind: str,
    state: dict, sources: dict,
) -> bool:
    """Comprueba que el plan pertenece a esta consulta antes de reutilizarlo."""
    if not isinstance(plan, dict):
        return False
    if plan.get("state_bound") is not True:
        return False
    if plan.get("view") != view or plan.get("target") != target or plan.get("kind") != kind:
        return False
    if plan.get("basis_fingerprint") != _plan_basis_fingerprint(
        view, target, kind, state, sources
    ):
        return False
    active = plan.get("active")
    required_sections = plan.get("required_sections")
    required_obligations = plan.get("required_obligations")
    if not isinstance(active, list) or not all(
        isinstance(item, dict) and isinstance(item.get("id"), str) and item.get("id")
        for item in active
    ):
        return False
    if not isinstance(required_sections, list) or not all(
        isinstance(item, str) for item in required_sections
    ):
        return False
    if not isinstance(required_obligations, list) or required_obligations != [
        item.get("id") for item in active
    ]:
        return False
    section_obligations = plan.get("section_obligations")
    if not isinstance(section_obligations, dict) or not all(
        isinstance(section, str)
        and isinstance(ids, list)
        and all(isinstance(identifier, str) for identifier in ids)
        for section, ids in section_obligations.items()
    ):
        return False
    return True


def _source_items(
    sources: dict[str, dict], view: str, target: str | None, kind: str = "module",
    state: dict | None = None, required: list[str] | None = None,
    active: list[dict] | None = None,
) -> tuple[list[dict], set[str], list[dict]]:
    state = state or {}
    required = required or []
    sections = sources.get("sections", {})
    active = active if active is not None else active_obligations(view, target, kind, state, sources)
    by_section: dict[str, list[str]] = {}
    for obligation in active:
        identifier = obligation.get("id")
        name = OBLIGATION_SECTION.get(identifier)
        classification = sources.get("applicability", {}).get(identifier, {})
        names: list[str] = []
        if name and (
            obligation.get("delivery") == "required_section"
            or (obligation.get("delivery") == "conditional" and classification.get("status") == "required")
        ):
            names.append(name)
        evidence_name = EVIDENCE_SECTION.get(identifier)
        if evidence_name and (
            identifier == "PRESERVE"
            or (identifier in {"UI_STATES", "NFR"} and classification.get("status") == "required")
        ):
            names.append(evidence_name)
        if identifier == "SPECS" and kind == "module" and target:
            spec_entry = state.get("module_specs", {}).get(target, {})
            if isinstance(spec_entry, dict):
                names.extend(
                    source_name for field, source_name in SPEC_SOURCE_SECTION.items()
                    if spec_entry.get(field) in {"required", "done"}
                )
        for section_name in names:
            by_section.setdefault(section_name, []).append(obligation["id"])

    items: list[dict] = []
    required_ids: set[str] = set()
    refs: list[dict] = []
    seen_documents: dict[tuple[str, str, str, str], dict] = {}
    for name in sorted(sections):
        section = sections[name]
        if not isinstance(section, dict):
            continue
        section_required = name in required
        section_obligations = list(dict.fromkeys(by_section.get(name, []) + section.get("obligation_ids", [])))
        module_mismatch = (
            kind == "module" and target and section.get("source_kind") == "module"
            and section.get("module_id") and section.get("module_id") != target
        )
        content = section.get("content")
        base_ref = {
            "section": name, "path": section.get("path"), "selector": section.get("selector"),
            **({"obligation_ids": section_obligations} if section_obligations else {}),
        }
        if module_mismatch:
            if section_required or section_obligations:
                refs.append({**base_ref, "reason": "wrong_module"})
            continue
        if not isinstance(content, str) or not content.strip():
            if section_required or section_obligations:
                missing_reason = (
                    "ambiguous_source" if section.get("ambiguous")
                    else "missing_evidence" if name in set(EVIDENCE_SECTION.values())
                    else "missing_source" if content is None else "empty_source"
                )
                refs.append({**base_ref, "reason": missing_reason})
            continue
        omitted = section.get("omitted")
        if omitted:
            listed = [_compact(title, 60) for title in omitted[:OMITTED_LISTED_MAX]]
            refs.append({
                **base_ref, "reason": "scoped_to_module", "omitted_count": len(omitted),
                "omitted_headings": listed,
                **({"omitted_truncated": len(omitted) - len(listed)} if len(omitted) > len(listed) else {}),
                "how_to_read": "abrir la ruta y localizar el heading exacto de omitted_headings",
            })
        key = (
            str(section.get("path")), str(section.get("selector")), content,
            str(section.get("module_id", "")),
        )
        first = seen_documents.get(key)
        if first is not None:
            existing_ids = first.setdefault("obligation_ids", [])
            for identifier in section_obligations:
                if identifier not in existing_ids:
                    existing_ids.append(identifier)
            for emitted in first.setdefault("items", []):
                emitted["obligation_ids"] = list(existing_ids)
                if section_required:
                    emitted["required"] = True
                    emitted["_required_section"] = True
                    required_ids.add(emitted["id"])
            continue
        document = {"obligation_ids": section_obligations, "items": []}
        seen_documents[key] = document
        parts = [content[index:index + 1024] for index in range(0, len(content), 1024)] or [""]
        for index, part in enumerate(parts):
            item_id = f"{name}:{index:06d}"
            item = {
                "id": item_id,
                "kind": "section" if len(parts) == 1 else "section_fragment",
                "content": part,
                "fragment_index": index,
                "fragment_count": len(parts),
                "path": section.get("path"),
                "selector": section.get("selector"),
                "obligation_ids": document["obligation_ids"],
                "required": section_required,
                "_required_section": section_required,
            }
            items.append(item)
            document["items"].append(item)
            if section_required:
                required_ids.add(item_id)

    for name in required:
        if name in sections:
            continue
        path, selector = SOURCE_REFERENCES.get(name, (name, name))
        refs.append({"section": name, "path": path, "selector": selector, "reason": "missing_source"})
    return items, required_ids, _merge_refs([], refs)


def _state_item_fragments(field: str, key: str, value: Any) -> list[dict]:
    content = {"field": field, "key": key, "value": copy.deepcopy(value)}
    if serialized_context_size({"content": content}) <= 1024:
        return [content]
    encoded = canonical_json(value).decode("utf-8")
    parts = [encoded[index:index + 768] for index in range(0, len(encoded), 768)] or [""]
    return [
        {
            "field": field, "key": key, "encoding": "json_fragment", "fragment": part,
            "fragment_index": index, "fragment_count": len(parts),
        }
        for index, part in enumerate(parts)
    ]


def _decision_state_items(
    state: dict, target: str | None, kind: str, view: str
) -> tuple[list[dict], set[str]]:
    if view not in {"implementation", "closure"}:
        return [], set()
    values: list[tuple[str, Any]] = []
    if kind in {"module", "task"} and isinstance(state.get("module_specs"), dict) and target in state["module_specs"]:
        values.append(("module_spec", state["module_specs"][target]))
    for field in ("stack", "dependencies", "architecture_constraints"):
        if field in state:
            values.append((field, state[field]))
    items: list[dict] = []
    required_ids: set[str] = set()
    for field, value in values:
        if isinstance(value, dict):
            entries = sorted(value.items(), key=lambda pair: str(pair[0])) or [("", {})]
        elif isinstance(value, list):
            entries = [(str(index), item) for index, item in enumerate(value)] or [("", [])]
        else:
            entries = [("", value)]
        for key, entry in entries:
            fragments = _state_item_fragments(field, key, entry)
            for fragment_index, fragment in enumerate(fragments):
                suffix = key or "empty"
                if len(fragments) > 1:
                    suffix = f"{suffix}:{fragment_index:06d}"
                item = {
                    "id": f"state:{field}:{suffix}", "kind": "state_field", "content": fragment,
                    "required": True, "_required_state": True,
                }
                items.append(item)
                required_ids.add(item["id"])
    return items, required_ids


_BUG_ID_TOKEN_RE = re.compile(r"(?<![A-Za-z0-9])BUG-\d{3,}(?!\d)")


def _bug_module(bug: dict) -> str | None:
    """Módulo del bug; vacío, de solo espacios o no string = sin módulo (bloquea siempre)."""
    module = bug.get("module_id")
    return module if isinstance(module, str) and module.strip() else None


def _bug_scope(state: dict, view: str, target: str | None) -> tuple[set[str] | None, set[str]]:
    """S4 + FW-359: (módulos en los que un Mayor+AC bloquea, bugs que la unidad
    declara arreglar con su id). `None` = sin acotar: startup/recovery, sin
    objetivo, o `bug:` sin un bug abierto con módulo."""
    kind = _target_kind(target)
    if view not in {"implementation", "closure"} or kind == "none":
        return None, set()
    bugs = {
        bug["id"]: bug for bug in state.get("_bugs", []) or []
        if isinstance(bug, dict) and isinstance(bug.get("id"), str)
    }
    named =[bugs[bug_id] for bug_id in _BUG_ID_TOKEN_RE.findall(target) if bug_id in bugs]
    if kind == "bug":
        bug = bugs.get(target[len("bug:"):])
        if not bug or bug.get("status") != "Abierto" or not _bug_module(bug):
            return None, set()
        modules, subjects = set(), [bug]
    elif kind == "task":
        name = target[len("task:"):]
        modules = {target, name}
        for change in state.get("changes", []) or []:
            if isinstance(change, dict) and change.get("id") == name:
                modules.update(m for m in change.get("affected_modules") or [] if isinstance(m, str))
        modules.update(b["module_id"] for b in named if _bug_module(b))
        return modules, set()
    else:
        spec = (state.get("module_specs") or {}).get(target)
        spec = spec if isinstance(spec, dict) else {}
        deps = spec.get("depends_on")
        modules = {target} | ({d for d in deps if isinstance(d, str)} if isinstance(deps, list) else set())
        subjects = [
            b for b in named
            if spec.get("stabilization") is True and b.get("status") == "Abierto"
            and _bug_module(b) and b.get("module_id") != target
        ]
    modules.update(b["module_id"] for b in subjects)  # los demás Mayores del dueño siguen contando
    return modules, ({b["id"] for b in subjects} if view == "implementation" else set())


def _blocking_items(
    state: dict, view: str, target: str | None, *,
    active_rejection_ids: set[str] | None = None, validation: dict | None = None,
) -> list[dict]:
    """Predicado compartido por vistas. Un Crítico bloquea todo salvo, en
    `implementation`, el bug que la propia unidad declara arreglar con su id
    (FW-359: `bug:BUG-NNN` o Estabilización `estabilizacion-BUG-NNN`). Un
    Mayor+AC bloquea su ámbito en `implementation`/`closure` (S4, PA-FW-004):
    el módulo objetivo y sus `depends_on`, el módulo de la tarea, los módulos
    de su cambio y los de los bugs que nombra, y el dueño de cada sujeto. Un
    Mayor sin módulo bloquea siempre. `startup` y `recovery` no cambian."""
    items: list[dict] = []
    modules, subjects = _bug_scope(state, view, target)
    for bug in state.get("_bugs", []) or []:
        if not isinstance(bug, dict) or bug.get("status") != "Abierto":
            continue
        if bug.get("severity") not in {"Crítico", "Mayor"}:
            continue
        if bug.get("severity") == "Mayor" and not bug.get("affects_ac"):
            continue
        bug_module = _bug_module(bug)
        if isinstance(bug.get("id"), str) and bug["id"] in subjects:
            continue
        if (
            modules is not None and bug.get("severity") == "Mayor"
            and bug_module and bug_module not in modules
        ):
            continue
        items.append({
            "id": str(bug.get("id", "bug:unknown")), "kind": "blocker",
            "content": {
                "type": "bug", "module_id": bug.get("module_id"), "severity": bug.get("severity"),
                "description": _compact(bug.get("description", "")),
            },
        })
    for clarification in state.get("_clarifications", []) or []:
        if not isinstance(clarification, dict) or clarification.get("status") != "Abierta":
            continue
        flag = {
            "startup": clarification.get("blocks_start"),
            "implementation": clarification.get("blocks_implementation"),
            "closure": clarification.get("blocks_closure"), "recovery": True,
        }.get(view)
        if flag:
            items.append({
                "id": str(clarification.get("id", "clarification:unknown")), "kind": "blocker",
                "content": {
                    "type": "clarification", "blocked_scope": clarification.get("blocked_scope"),
                    "question": _compact(clarification.get("question", "")),
                },
            })
    if view in {"implementation", "closure"} and _target_kind(target) == "module":
        statuses = state.get("module_statuses", {})
        if isinstance(statuses, dict) and statuses.get(target) == "blocked":
            items.append({
                "id": f"module:{target}:blocked", "kind": "blocker",
                "content": {
                    "type": "module_status", "module_id": target, "status": "blocked",
                    "description": "El módulo está bloqueado y no puede recibir autorización de ejecución.",
                },
            })
    rejections = [
        rejection for rejection in state.get("closure_rejections", []) or []
        if isinstance(rejection, dict) and _closure_rejection_is_active(rejection, active_rejection_ids)
    ]
    if view == "startup" and len(rejections) >= 2:
        selected = rejections
    elif view == "closure":
        selected = [rejection for rejection in rejections if rejection.get("module_id") == target]
    else:
        selected = []
    for rejection in selected:
        items.append({
            "id": str(rejection.get("id", rejection.get("module_id", "rejection:unknown"))),
            "kind": "closure_rejection",
            "content": {"module_id": rejection.get("module_id"), "reason": rejection.get("reason")},
        })
    if isinstance(validation, dict):
        for index, reason in enumerate(validation.get("hard_blockers", []) or []):
            items.append({
                "id": f"validation:block:{index:04d}", "kind": "validation_blocker",
                "content": {"reason": _compact(reason)},
            })
    return sorted(items, key=lambda item: (item["kind"], item["id"]))


def _keyed(mapping: dict, target: str | None, kind: str):
    """Entrada de `module_statuses`/`work_tiers` del objetivo (TL FW-239): los
    `task:` viven en los mismos mapas, con o sin el prefijo."""
    if kind not in ("module", "task") or not target or not isinstance(mapping, dict):
        return None
    if target in mapping:
        return mapping[target]
    if kind == "task":
        return mapping.get(target[len("task:"):])
    return None


def _approval_projection(approval: Any) -> Any:
    """Proyección de solo lectura del recibo de aprobación (L8): los mapas de
    identidades de test (cientos de hashes) viajan como recuento, no enteros."""
    if not isinstance(approval, dict):
        return approval
    projected = {
        key: value for key, value in approval.items()
        if key not in ("test_identities", "test_identities_origin")
    }
    for key in ("test_identities", "test_identities_origin"):
        value = approval.get(key)
        if isinstance(value, (dict, list)):
            projected[f"{key}_count"] = len(value)
    return projected


def _summary(state: dict, target: str | None, kind: str, view: str, blockers: list[dict]) -> dict:
    statuses = state.get("module_statuses", {})
    tiers = state.get("work_tiers", {})
    sess = state.get("session", {}) if isinstance(state.get("session"), dict) else {}
    tier_entry = _keyed(tiers, target, kind)
    summary = {
        "project": _compact(state.get("project", "[NOMBRE_PROYECTO]")),
        "framework_mode": state.get("framework_mode", "template"),
        "framework_version": state.get("framework_version", ""),
        "current_phase": state.get("current_phase", ""), "current_step": _compact(state.get("current_step", "")),
        "target_kind": kind, "target_id": target,
        "module_status": _keyed(statuses, target, kind),
        "tier": tier_entry.get("tier") if isinstance(tier_entry, dict) else None,
        "next_command": _compact(sess.get("next_command", "")), "next_action": _compact(sess.get("next_action", "")),
        "active_focus": state.get("active_focus"), "blocking_count": len(blockers),
        "critical_bug_count": sum(
            1 for bug in state.get("_bugs", []) or []
            if isinstance(bug, dict) and bug.get("status") == "Abierto" and bug.get("severity") == "Crítico"
        ),
        "closure_rejection_count": len(state.get("closure_rejections", []) or []), "view": view,
    }
    if view == "startup":
        # FW-275 (5.2.2): recuento honesto; sin trabajo declarado no significa «terminado».
        import projections as _projections

        counts = _projections.module_counts(state)
        modules = state.get("modules") if isinstance(state.get("modules"), dict) else {}
        summary.update({
            "completed_count": counts["completed"],
            "builder_used_count": counts["builder_used"],
            "pending_count": counts["pending"],
            "no_pending_work": not (modules.get("pending") or modules.get("in_progress")),
        })
    if view in {"implementation", "closure"}:
        execution_profile = state.get("execution_profile")
        summary.update({
            "capability_tier": execution_profile.get("capability_tier") if isinstance(execution_profile, dict) else None,
            "module_spec": _bounded(
                (state.get("module_specs") or {}).get(target)
                if isinstance(state.get("module_specs"), dict) and kind in {"module", "task"} else None
            ),
            "stack": _bounded(state.get("stack", {})), "dependencies": _bounded(state.get("dependencies", [])),
            "architecture_constraints": _bounded(state.get("architecture_constraints", [])),
        })
    if view == "closure" and kind == "module":
        receipts = state.get("workflow_receipts")
        if isinstance(receipts, dict):
            reviews = receipts.get("reviews", {})
            history = reviews.get(target, []) if isinstance(reviews, dict) else []
            summary["receipts"] = _bounded({
                "approval": _approval_projection(
                    (receipts.get("approvals", {}) or {}).get(target)
                    if isinstance(receipts.get("approvals", {}), dict) else None
                ),
                "review": history[-1] if isinstance(history, list) and history else None,
                "execution": (receipts.get("executions", {}) or {}).get(target)
                if isinstance(receipts.get("executions", {}), dict) else None,
            })
    return summary


VALID_MODULE_STATUSES = ("not_started", "in_progress", "ready_for_validation", "blocked", "completed", "dropped")

LOOKUP_KINDS = ("module", "modules_by_status", "receipts", "bugs", "clarifications", "decisions")
LOOKUP_MAX_BYTES = 12 * 1024
_LOOKUP_MARGIN = 256
_LOOKUP_TEXT_LIMIT = 200


def _clip(value: object, limit: int) -> str:
    text = value if isinstance(value, str) else ""
    return text if len(text) <= limit else text[: max(limit - 1, 0)] + "…"


# kind -> (clave del estado, campo que enlaza con el módulo, validar módulo, proyección(fila, límite de texto))
_RECORD_KINDS = {
    "bugs": (
        "_bugs", "module_id", True,
        lambda r, n: {
            "id": r.get("id"), "severity": r.get("severity"), "status": r.get("status"),
            "module_id": r.get("module_id"), "description": _clip(r.get("description"), n),
        },
    ),
    "clarifications": (
        "_clarifications", "blocked_scope", True,
        lambda r, n: {
            "id": r.get("id"), "status": r.get("status"),
            "blocks_start": r.get("blocks_start"),
            "blocks_implementation": r.get("blocks_implementation"),
            "blocks_closure": r.get("blocks_closure"),
            "question": _clip(r.get("question"), n),
        },
    ),
    "decisions": (
        "_decisions", "module_id", False,
        lambda r, n: {"id": r.get("id"), "title": _clip(r.get("title"), n), "status": r.get("status")},
    ),
}


def _lookup_size(payload: dict) -> int:
    return len(json.dumps(payload, indent=2, ensure_ascii=False).encode("utf-8"))


def _query_records(state, *, kind, module_id, status, offset, max_bytes) -> dict:
    key, link, validate_module, project = _RECORD_KINDS[kind]
    if module_id is not None:
        if not isinstance(module_id, str) or not module_id.strip():
            raise QueryError("INVALID_TARGET", "El objetivo debe ser un string no vacío")
        statuses = state.get("module_statuses")
        if validate_module and (not isinstance(statuses, dict) or module_id not in statuses):
            raise QueryError("INVALID_TARGET", f"Módulo no declarado: {module_id}")
    if status is not None and not isinstance(status, str):
        raise QueryError("INVALID_TARGET", "El estado debe ser un string")
    if offset is None:
        offset = 0
    if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
        raise QueryError("INVALID_TARGET", "offset debe ser un entero >= 0")

    def matches(row: object) -> bool:
        if not isinstance(row, dict):
            return False
        if status is not None and row.get("status") != status:
            return False
        if module_id is None:
            return True
        ref = row.get(link)
        return module_id in ref if isinstance(ref, list) else ref == module_id

    rows = [r for r in (state.get(key) or []) if matches(r)]
    total = len(rows)
    if offset > total:
        raise QueryError("INVALID_TARGET", f"offset {offset} excede el total ({total})")

    budget = max_bytes - _LOOKUP_MARGIN
    payload: dict = {"kind": kind, "total": total, "offset": offset, "returned": 0, "items": []}
    for row in rows[offset:]:
        payload["items"].append(project(row, _LOOKUP_TEXT_LIMIT))
        if _lookup_size(payload) > budget:
            payload["items"].pop()
            if not payload["items"]:
                # Nunca cero resultados por el tope: la primera fila se recorta hasta caber.
                limit = _LOOKUP_TEXT_LIMIT
                while True:
                    limit //= 2
                    item = project(row, limit)
                    payload["items"].append(item)
                    if _lookup_size(payload) <= budget or limit == 0:
                        break
                    payload["items"].pop()
                payload["row_clipped"] = True
            break
    payload["returned"] = len(payload["items"])
    next_offset = offset + payload["returned"]
    if next_offset < total or payload.get("row_clipped"):
        payload["truncated"] = True
    if next_offset < total:
        args = {"kind": kind, "offset": next_offset}
        if module_id is not None:
            args["module_id"] = module_id
        if status is not None:
            args["status"] = status
        payload["next_call"] = {"tool": "lookup_state", "arguments": args}
    return payload


def query_state_point(
    state: dict,
    *,
    kind: str,
    module_id: str | None = None,
    status: str | None = None,
    offset: int | None = None,
    max_bytes: int = LOOKUP_MAX_BYTES,
) -> dict:
    """Consulta puntual del estado (módulo, módulos por estado, recibos, bugs, clarificaciones, decisiones) sin cargar el estado completo."""
    if not isinstance(state, dict):
        raise QueryError("INVALID_STATE", "El estado debe ser un objeto JSON")
    if not isinstance(kind, str) or kind not in LOOKUP_KINDS:
        raise QueryError("INVALID_TARGET", f"Kind no soportado: {kind!r}")

    if kind in _RECORD_KINDS:
        return _query_records(
            state, kind=kind, module_id=module_id, status=status, offset=offset, max_bytes=max_bytes
        )

    if kind == "module":
        if not isinstance(module_id, str) or not module_id.strip():
            raise QueryError("INVALID_TARGET", "El objetivo debe ser un string no vacío")
        statuses = state.get("module_statuses")
        if not isinstance(statuses, dict) or module_id not in statuses:
            raise QueryError("INVALID_TARGET", f"Módulo no declarado: {module_id}")
        mod_status = statuses[module_id]
        tiers = state.get("work_tiers") if isinstance(state.get("work_tiers"), dict) else {}
        tier = (tiers.get(module_id) or {}).get("tier") if isinstance(tiers.get(module_id), dict) else None
        modules = state.get("modules") if isinstance(state.get("modules"), dict) else {}
        bucket = None
        for b in ("completed", "in_progress", "pending", "dropped"):
            if isinstance(modules.get(b), list) and module_id in modules[b]:
                bucket = b
                break
        return {
            "module_id": module_id,
            "status": mod_status,
            "tier": tier,
            "bucket": bucket,
        }

    if kind == "modules_by_status":
        statuses = state.get("module_statuses") if isinstance(state.get("module_statuses"), dict) else {}
        if status is None:
            return {
                st: sorted(m for m, s in statuses.items() if s == st)
                for st in VALID_MODULE_STATUSES
            }
        if not isinstance(status, str) or status not in VALID_MODULE_STATUSES:
            raise QueryError("INVALID_TARGET", f"Estado de módulo inválido: {status}")
        return {
            status: sorted(m for m, s in statuses.items() if s == status)
        }

    if kind == "receipts":
        if not isinstance(module_id, str) or not module_id.strip():
            raise QueryError("INVALID_TARGET", "El objetivo debe ser un string no vacío")
        statuses = state.get("module_statuses")
        if not isinstance(statuses, dict) or module_id not in statuses:
            raise QueryError("INVALID_TARGET", f"Módulo no declarado: {module_id}")

        import workflow_receipts

        receipts_data = state.get("workflow_receipts") if isinstance(state.get("workflow_receipts"), dict) else {}

        # APPROVAL
        approvals = receipts_data.get("approvals") if isinstance(receipts_data.get("approvals"), dict) else {}
        approval_receipt = approvals.get(module_id) if isinstance(approvals.get(module_id), dict) else None
        approval_problems_list = workflow_receipts.approval_receipt_state_problems(state, module_id)
        if approval_receipt is None:
            app_state = "missing"
        elif approval_problems_list:
            app_state = "invalid"
        else:
            app_state = "sealed"

        approval_dict = {
            "state": app_state,
            "problems": approval_problems_list,
            "fingerprint": approval_receipt.get("fingerprint") if isinstance(approval_receipt, dict) else None,
            "approved_at": approval_receipt.get("approved_at") if isinstance(approval_receipt, dict) else None,
            "tier": approval_receipt.get("tier") if isinstance(approval_receipt, dict) else None,
            "kind": approval_receipt.get("kind") if isinstance(approval_receipt, dict) else None,
        }

        # REVIEW
        reviews = receipts_data.get("reviews") if isinstance(receipts_data.get("reviews"), dict) else {}
        review_history = reviews.get(module_id) if isinstance(reviews.get(module_id), list) else []
        review_receipt = review_history[-1] if review_history and isinstance(review_history[-1], dict) else None
        review_problems_list = workflow_receipts.review_problems(state, module_id, current_diff_hash=None)
        if review_receipt is None:
            rev_state = "missing"
        elif review_problems_list:
            rev_state = "invalid"
        else:
            rev_state = "sealed"

        review_dict = {
            "state": rev_state,
            "problems": review_problems_list,
        }

        # EXECUTION
        executions = receipts_data.get("executions") if isinstance(receipts_data.get("executions"), dict) else {}
        exec_receipt = executions.get(module_id) if isinstance(executions.get(module_id), dict) else None
        exec_problems_list = workflow_receipts.execution_problems(state, module_id, current_diff_hash=None)
        if exec_receipt is None:
            exe_state = "missing"
        elif exec_problems_list:
            exe_state = "invalid"
        else:
            exe_state = "sealed"

        exec_dict = {
            "state": exe_state,
            "problems": exec_problems_list,
        }

        return {
            "module_id": module_id,
            "approval": approval_dict,
            "review": review_dict,
            "execution": exec_dict,
            "freshness": "not_checked",
            "note": "sellado = coherente en el estado; la vigencia contra el disco la comprueba complete_module",
        }


def _encode_cursor(fingerprint: str, view: str, module_id: str | None, offset: int) -> str:
    payload = {"v": 1, "fingerprint": fingerprint, "view": view, "module_id": module_id, "offset": offset}
    encoded = base64.urlsafe_b64encode(canonical_json(payload)).decode("ascii").rstrip("=")
    if len(encoded.encode("ascii")) > CURSOR_MAX_BYTES:
        raise QueryError("INVALID_CURSOR", "El cursor excede el límite permitido")
    return encoded


def _decode_cursor(cursor: str, fingerprint: str, view: str, module_id: str | None) -> int:
    if not isinstance(cursor, str) or not cursor or len(cursor.encode("utf-8")) > CURSOR_MAX_BYTES:
        raise QueryError("INVALID_CURSOR", "Cursor ausente, inválido o demasiado grande")
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded.encode("ascii")))
    except (ValueError, UnicodeError, json.JSONDecodeError, binascii.Error) as exc:
        raise QueryError("INVALID_CURSOR", "Cursor no es base64url JSON válido") from exc
    if not isinstance(payload, dict) or payload.get("v") != 1:
        raise QueryError("INVALID_CURSOR", "Versión de cursor no soportada")
    if payload.get("fingerprint") != fingerprint:
        raise QueryError("STALE_CURSOR", "La fuente cambió desde la página anterior")
    if payload.get("view") != view or payload.get("module_id") != module_id:
        raise QueryError("INVALID_CURSOR", "El cursor no corresponde a esta consulta")
    offset = payload.get("offset")
    if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
        raise QueryError("INVALID_CURSOR", "offset de cursor inválido")
    return offset


def _public_item(item: dict) -> dict:
    return {key: copy.deepcopy(value) for key, value in item.items() if not key.startswith("_")}


def _response(
    *, view: str, module_id: str | None, kind: str, fingerprint: str, gate_status: str,
    blockers: list[dict], required_sections: list[str], required_obligations: list[str],
    required_complete: bool, items: list[dict], refs: list[dict], next_cursor: str | None,
    complete: bool,
) -> dict:
    return {
        "schema_version": 1, "view": view, "module_id": module_id, "target_kind": kind,
        "source_fingerprint": fingerprint, "gate_status": gate_status, "gate_scope": view,
        "blocking_count": len(blockers), "required_sections": required_sections,
        "required_obligations": required_obligations, "required_context_complete": required_complete,
        "data": {"summary": {}, "items": [_public_item(item) for item in items]},
        "refs": refs, "next_cursor": next_cursor, "complete": complete,
    }


_CLIP_ITEMS = 3
_HEADER_RESERVE = 4 * 1024
_CLIP_TEXT = 120


def _clip_summary_value(value: Any) -> Any:
    """Resumen corto de un campo del resumen: recuento y primeros elementos compactados."""
    if isinstance(value, list):
        short: list[Any] = [_compact(item if isinstance(item, str) else str(item), _CLIP_TEXT) for item in value[:_CLIP_ITEMS]]
        if len(value) > _CLIP_ITEMS:
            short.append(f"… ({len(value) - _CLIP_ITEMS} elementos más)")
        return short
    if isinstance(value, dict):
        keys = sorted(str(key) for key in value)
        return {"clipped": True, "count": len(value), "keys": keys[:_CLIP_ITEMS * 4]}
    if isinstance(value, str):
        return _compact(value, _CLIP_TEXT)
    return value


_CLIP_ORDER = (
    ("warnings",), ("receipts", "warnings"),
    ("receipts", "approval"), ("receipts", "review"), ("receipts", "execution"), ("receipts",),
    ("module_spec",), ("architecture_constraints",), ("dependencies",), ("stack",),
)


def _clip_next_field(summary: dict, clipped: list[str]) -> bool:
    """Recorta el siguiente campo grande del resumen; False si no queda ninguno."""
    for path in _CLIP_ORDER:
        dotted = ".".join(path)
        if dotted in clipped:
            continue
        container: Any = summary
        for key in path[:-1]:
            container = container.get(key) if isinstance(container, dict) else None
        if not isinstance(container, dict) or path[-1] not in container:
            continue
        current = container[path[-1]]
        if not current:
            continue
        shrunk = _clip_summary_value(current)
        if shrunk == current:
            continue
        container[path[-1]] = shrunk
        clipped.append(dotted)
        return True
    return False


def query_context(
    state: dict, *, view: str, module_id: str | None = None, cursor: str | None = None,
    sources: dict | None = None, workspace_root: object = None, validation: dict | None = None,
    active_cycle_module: str | None = None,
) -> dict:
    """Devuelve una página estable de contexto sin mutar ``state`` ni fuentes."""
    with query_memo():
        return _query_context(
            state, view=view, module_id=module_id, cursor=cursor, sources=sources,
            workspace_root=workspace_root, validation=validation,
            active_cycle_module=active_cycle_module,
        )


def _query_context(
    state: dict, *, view: str, module_id: str | None = None, cursor: str | None = None,
    sources: dict | None = None, workspace_root: object = None, validation: dict | None = None,
    active_cycle_module: str | None = None,
) -> dict:
    if view not in VIEWS:
        raise QueryError("INVALID_VIEW", f"Vista no soportada: {view}")
    if not isinstance(state, dict):
        raise QueryError("INVALID_STATE", "El estado debe ser un objeto JSON")
    _validate_canonical_state(state, root=workspace_root)
    source_data = _normalise_sources(sources)
    target, kind, explicit = resolve_context_target(state, module_id, active_cycle_module)
    provided_plan = source_data.get("plan")
    if provided_plan is None:
        plan = context_plan(view, target, kind, state, source_data)
    elif _reusable_context_plan(provided_plan, view, target, kind, state, source_data):
        plan = provided_plan
    elif isinstance(provided_plan, dict) and provided_plan.get("state_bound") is not True:
        plan = context_plan(view, target, kind, state, source_data)
    else:
        raise QueryError("SOURCE_MISSING", "sources.plan no corresponde a la consulta")
    active = plan["active"]
    required_sections, missing_required = _required_sections(
        view, target, kind, state, source_data, active, plan["required_sections"]
    )
    fingerprint_sources = dict(source_data["hashes"])
    fingerprint_sources["__obligations__"] = _sha256(canonical_json(source_data["obligations"]))
    fingerprint_sources["__applicability__"] = _sha256(canonical_json(source_data["applicability"]))
    if isinstance(validation, dict) and isinstance(validation.get("fingerprint"), str):
        fingerprint_sources["__validation__"] = validation["fingerprint"]
    fingerprint = source_fingerprint(state, fingerprint_sources)
    offset = _decode_cursor(cursor, fingerprint, view, target) if cursor is not None else 0

    active_rejection_ids = None
    if isinstance(validation, dict) and isinstance(validation.get("active_rejection_ids"), list):
        active_rejection_ids = set(validation["active_rejection_ids"])
    blockers = _blocking_items(
        state, view, target, active_rejection_ids=active_rejection_ids, validation=validation
    )
    source_items, required_ids, refs = _source_items(
        source_data, view, target, kind, state, required_sections, active
    )
    required_obligations, missing_obligations = _obligation_requirements(
        view, target, kind, state, source_data, validation, active, workspace_root
    )
    refs = _merge_refs(refs, missing_obligations)
    decision_items, decision_required_ids = _decision_state_items(state, target, kind, view)
    required_ids.update(decision_required_ids)

    scope_conflict = _scope_conflict(target, explicit, source_data)
    scope_missing = _scope_missing(target, explicit, source_data)
    if scope_conflict:
        scope = source_data.get("sections", {}).get("scope", {})
        refs = _merge_refs(refs, [{
            "section": "scope", "path": scope.get("path", SOURCE_REFERENCES["scope"][0]),
            "selector": scope.get("selector", SOURCE_REFERENCES["scope"][1]),
            "reason": "scope_conflict", "requested_module_id": target,
            "declared_module_id": scope.get("module_id"),
        }])
    elif scope_missing:
        refs = _merge_refs(refs, [{
            "section": "scope", "path": SOURCE_REFERENCES["scope"][0],
            "selector": SOURCE_REFERENCES["scope"][1], "reason": "missing_source",
            "requested_module_id": target,
        }])
    # PA-FW-011: una fuente ambigua es un motivo más, nunca un PASS, en cualquier vista.
    ambiguous = [
        (name, section) for name, section in source_data.get("sections", {}).items()
        if isinstance(section, dict) and section.get("ambiguous")
    ]
    refs = _merge_refs(refs, [{
        "section": name, "path": section.get("path"), "selector": section.get("selector"),
        "reason": "ambiguous_source",
    } for name, section in ambiguous])
    if isinstance(validation, dict):
        for reason in validation.get("source_errors", []) or []:
            refs = _merge_refs(refs, [{
                "section": "validation", "path": "state_manager", "selector": "filesystem_validator",
                "reason": "validation_error", "detail": _compact(reason),
            }])
        for warning in validation.get("warnings", []) or []:
            refs = _merge_refs(refs, [{
                "section": "receipts", "path": "workflow_receipts", "selector": "drift_warning",
                "reason": "docs_drift", "detail": _compact(warning),
            }])

    state_items = blockers
    if view in {"startup", "recovery"} and isinstance(state.get("last_completed_step"), str):
        state_items = [{
            "id": "continuity:last_completed_step", "kind": "continuity",
            "content": state.get("last_completed_step", ""), "required": False,
        }] + state_items
    all_items = source_items + decision_items + state_items
    all_items.sort(
        key=lambda item: (
            0 if item.get("_required_section") or item.get("_required_state") else 1,
            item["kind"], item["id"],
        )
    )

    summary = _summary(state, target, kind, view, blockers)
    if isinstance(validation, dict) and validation.get("warnings"):
        summary["warnings"] = list(dict.fromkeys(validation["warnings"]))
        if isinstance(summary.get("receipts"), dict):
            summary["receipts"]["warnings"] = list(dict.fromkeys(validation["warnings"]))
    hard_block = bool(blockers)
    pending = bool(
        missing_required or missing_obligations or scope_conflict or scope_missing or ambiguous
        or (isinstance(validation, dict) and bool(validation.get("source_errors")))
        or (view in {"implementation", "closure"} and target is None)
    )
    # BLOCK > NEEDS_INPUT > PASS. Validators report stale/missing evidence as
    # pending input; only their explicit hard blockers can produce BLOCK.
    gate_status = "BLOCK" if hard_block else ("NEEDS_INPUT" if pending else "PASS")

    limit = VIEW_LIMITS[view]
    clipped: list[str] = []

    def header_size() -> int:
        probe = _response(
            view=view, module_id=target, kind=kind, fingerprint=fingerprint,
            gate_status=gate_status, blockers=blockers, required_sections=required_sections,
            required_obligations=required_obligations, required_complete=False,
            items=[], refs=refs, next_cursor=_encode_cursor(fingerprint, view, target, len(all_items) + 1),
            complete=False,
        )
        probe["data"]["summary"] = summary
        if clipped:
            probe["row_clipped"] = True
            probe["data"]["summary"] = {**summary, "clipped_fields": list(clipped)}
        return serialized_context_size(probe)

    # L8: una cabecera que desborda se recorta en lectura (nunca se guarda) antes de fallar.
    # Margen: la cabecera recortada deja sitio al menos para un fragmento.
    if header_size() > limit:
        while header_size() > limit - _HEADER_RESERVE and _clip_next_field(summary, clipped):
            pass

    def paginate() -> dict | None:
        page: list[dict] = []
        index = offset
        while index < len(all_items):
            candidate = page + [all_items[index]]
            more = index + 1 < len(all_items)
            candidate_cursor = _encode_cursor(fingerprint, view, target, index + 1) if more else None
            delivered = {item["id"] for item in all_items[:index + 1]}
            complete_required = not pending and required_ids.issubset(delivered)
            result = _response(
                view=view, module_id=target, kind=kind, fingerprint=fingerprint,
                gate_status=gate_status, blockers=blockers, required_sections=required_sections,
                required_obligations=required_obligations, required_complete=complete_required,
                items=candidate, refs=refs, next_cursor=candidate_cursor, complete=not more,
            )
            result["data"]["summary"] = _with_marks(summary)
            if clipped:
                result["row_clipped"] = True
            if serialized_context_size(result) > limit:
                if page:
                    break
                return None
            page.append(all_items[index])
            index += 1

        complete = index >= len(all_items)
        next_cursor = None if complete else _encode_cursor(fingerprint, view, target, index)
        delivered = {item["id"] for item in all_items[:index]}
        complete_required = not pending and required_ids.issubset(delivered)
        result = _response(
            view=view, module_id=target, kind=kind, fingerprint=fingerprint,
            gate_status=gate_status, blockers=blockers, required_sections=required_sections,
            required_obligations=required_obligations, required_complete=complete_required,
            items=page, refs=refs, next_cursor=next_cursor, complete=complete,
        )
        result["data"]["summary"] = _with_marks(summary)
        if clipped:
            result["row_clipped"] = True
        if serialized_context_size(result) > limit:
            return None
        return result

    def _with_marks(base: dict) -> dict:
        return {**base, "clipped_fields": list(clipped)} if clipped else base

    while True:
        result = paginate()
        if result is not None:
            return result
        if not _clip_next_field(summary, clipped):
            header = _response(
                view=view, module_id=target, kind=kind, fingerprint=fingerprint,
                gate_status=gate_status, blockers=blockers, required_sections=required_sections,
                required_obligations=required_obligations, required_complete=False,
                items=[], refs=refs, next_cursor=None, complete=False,
            )
            header["data"]["summary"] = _with_marks(summary)
            if serialized_context_size(header) > limit:
                raise QueryError("SOURCE_MISSING", "El encabezado de la consulta excede el límite")
            raise QueryError("SOURCE_MISSING", "Un elemento no puede empaquetarse en la página")
