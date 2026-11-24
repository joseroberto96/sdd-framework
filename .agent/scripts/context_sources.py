#!/usr/bin/env python3
"""Lectura acotada y segura de fuentes documentales para las vistas."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

try:
    import yaml
except ImportError:
    yaml = None

import state_queries
from state_queries import QueryError

# Reexportado aquí para que selector y validadores compartan el mismo contrato
# sin introducir una dependencia circular entre ambos módulos.
VisibleLine = state_queries.VisibleLine
visible_markdown_lines = state_queries.visible_markdown_lines
scan_markdown = state_queries.scan_markdown


OBLIGATION_MATRIX = ".agent/docs/specs/progressive_context_obligations.json"

# La clase de fuente es parte del contrato: solo ``module`` usa el selector por
# módulo. ``shared`` y ``global`` nunca buscan el nombre del módulo en el texto.
SOURCE_DEFINITIONS = {
    "scope": {"path": "_project/04_active_scope.md", "selector": "module_scope", "source_kind": "module"},
    "acceptance": {"path": "_project/08_module_acceptance.md", "selector": "module_acceptance", "source_kind": "module"},
    "preserve": {"path": ".agent/workflows/cambio-seguro.md", "selector": "preserve_inventory", "source_kind": "shared"},
    "security": {"path": ".agent/workflows/cambio-seguro.md", "selector": "security_surface", "source_kind": "shared"},
    "dependencies": {"path": ".agent/rules/architecture_constraints.md", "selector": "dependencies", "source_kind": "shared"},
    "invariants": {"path": "_project/03_invariants.md", "selector": "invariants", "source_kind": "global"},
    "contracts": {"path": "_project/03_contracts.md", "selector": "contracts", "source_kind": "global"},
    "approval": {"path": ".agent/workflows/tdd.md", "selector": "approval_receipt", "source_kind": "shared"},
    "destructive": {"path": "AGENTS.md", "selector": "destructive_guard", "source_kind": "shared"},
    "specs": {"path": ".agent/skills/spec_contracts.md", "selector": "module_specs", "source_kind": "shared"},
    "ui_states": {"path": ".agent/workflows/enriquecer-ticket.md", "selector": "ui_states", "source_kind": "shared"},
    "nfr": {"path": "_project/10_arch_constraints.md", "selector": "nfr", "source_kind": "shared"},
    "execution": {"path": ".agent/workflows/verificar-ejecutable.md", "selector": "runtime_execution", "source_kind": "shared"},
    "review": {"path": ".agent/workflows/preflight-review.md", "selector": "independent_review", "source_kind": "shared"},
    "closure": {"path": ".agent/workflows/cierre-modulo.md", "selector": "closure_preconditions", "source_kind": "shared"},
    "recovery": {"path": ".agent/workflows/reconciliar.md", "selector": "recovery_audit", "source_kind": "shared"},
    "journal": {"path": "_project/13_session_journal.md", "selector": "session_journal", "source_kind": "global"},
    "publish": {"path": ".agent/workflows/publicar-github.md", "selector": "publish_gate", "source_kind": "shared"},
    "preserve_evidence": {"path": "_project/11_file_dependencies.md", "selector": "preserve_evidence", "source_kind": "module", "evidence_field": "preserva"},
    "ui_evidence": {"path": "_project/08_module_acceptance.md", "selector": "ui_evidence", "source_kind": "module", "evidence_heading": "ui"},
    "nfr_evidence": {"path": "_project/10_arch_constraints.md", "selector": "nfr_evidence", "source_kind": "module", "evidence_heading": "nfr"},
    "data_model_spec": {"path": "_project/02_technical_plan/data_model.md", "selector": "module_data_model", "source_kind": "global"},
    "api_contract_spec": {"path": "_project/02_technical_plan/api_contract.yaml", "selector": "module_api_contract", "source_kind": "global"},
}

# Compatibilidad con el nombre histórico. La selección efectiva se deriva de
# la matriz y de la clasificación UI/NFR, pero esta tabla sigue siendo útil
# para introspección de adopters y para la documentación del adapter.
_SOURCE_PLANS = {
    "startup": (),
    "implementation": tuple((name, spec["path"], spec["selector"]) for name, spec in SOURCE_DEFINITIONS.items()
                             if name not in {"execution", "review", "closure", "recovery", "journal", "publish"}),
    "closure": tuple((name, spec["path"], spec["selector"]) for name, spec in SOURCE_DEFINITIONS.items()
                     if name not in {"recovery", "journal", "publish"}),
    "recovery": (("recovery", SOURCE_DEFINITIONS["recovery"]["path"], SOURCE_DEFINITIONS["recovery"]["selector"]),
                 ("journal", SOURCE_DEFINITIONS["journal"]["path"], SOURCE_DEFINITIONS["journal"]["selector"])),
}

_MARKER_START = "<!-- CONTEXT_SOURCE: {selector} -->"
_MARKER_END = "<!-- END_CONTEXT_SOURCE: {selector} -->"
_PLACEHOLDER_RE = re.compile(r"\[[^\]]*\]")
_STABLE_HEADINGS = {
    "dependencies": re.compile(r"^dependencias$", re.IGNORECASE),
    "invariants": re.compile(
        r"^invariantes(?:\s+del\s+sistema|\s+de\s+negocio)?(?:\s+[—-]\s*.*)?$", re.IGNORECASE
    ),
    "contracts": re.compile(
        r"^contratos?(?:\s+e\s+interfaces\s+públicas|\s+públicos)?(?:\s+[—-]\s*.*)?$",
        re.IGNORECASE,
    ),
    "recovery_audit": re.compile(r"^(?:reconciliar|reconciliación|auditoría de respaldo)$", re.IGNORECASE),
    "publish_gate": re.compile(r"^(?:publicar|publicación|gate de publicación)$", re.IGNORECASE),
}


def resolve_source_path(root: Path, relative: str) -> Path:
    """Resuelve solo rutas relativas confinadas al workspace."""
    if not isinstance(relative, str) or not relative or Path(relative).is_absolute():
        raise QueryError("SOURCE_MISSING", "La fuente debe ser una ruta relativa")
    candidate = (root / relative).resolve()
    root_resolved = root.resolve()
    if not candidate.is_relative_to(root_resolved) or ".." in Path(relative).parts:
        raise QueryError("SOURCE_MISSING", "La fuente sale del workspace")
    return candidate


def _read_stable(path: Path) -> tuple[str, str]:
    try:
        before = path.stat()
        content = path.read_text(encoding="utf-8")
        after = path.stat()
    except OSError as exc:
        raise QueryError("SOURCE_MISSING", f"No se pudo leer la fuente {path}: {exc}") from exc
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise QueryError("SOURCE_CHANGED", f"La fuente cambió durante la lectura: {path}")
    return content, hashlib.sha256(content.encode("utf-8")).hexdigest()


def _heading_records(content: str) -> list[tuple[int, int, str, str]]:
    """Cabeceras visibles del texto; un texto se indexa una vez por consulta (L8)."""
    return state_queries.query_derived("headings", content, _compute_heading_records)


def _compute_heading_records(content: str) -> list[tuple[int, int, str, str]]:
    """Devuelve headings visibles como (índice, nivel, texto, raw).

    ``visible_markdown_lines`` conserva la línea original para que la sección
    emitida siga incluyendo el texto documental completo, incluidos ejemplos
    que luego serán ignorados por los validadores.
    """
    records: list[tuple[int, int, str, str]] = []
    original_lines = content.splitlines(keepends=True)
    pattern = re.compile(r"^ {0,3}(#{1,6})[ \t]+(.+?)[ \t]*$")
    for visible in state_queries.visible_markdown_lines(content):
        match = pattern.match(visible.text)
        if match:
            index = visible.number - 1
            records.append((index, len(match.group(1)), match.group(2).strip(), original_lines[index]))
    return records


def _heading_block(content: str, record: tuple[int, int, str, str]) -> str:
    lines = content.splitlines(keepends=True)
    start, level, _, _ = record
    end = len(lines)
    for candidate in _heading_records(content):
        if candidate[0] <= start:
            continue
        if candidate[1] <= level:
            end = candidate[0]
            break
    return "".join(lines[start:end]).strip() + "\n"


def _heading_direct_content(content: str, record: tuple[int, int, str, str]) -> str:
    lines = content.splitlines(keepends=True)
    start, _, _, _ = record
    end = len(lines)
    for candidate in _heading_records(content):
        if candidate[0] <= start:
            continue
        end = candidate[0]
        break
    return "".join(lines[start:end]).strip() + "\n"


def _normalise_declared_module(title: str) -> str | None:
    match = re.search(r"(?:módulo|modulo)\s+actual\s*:\s*(.+)$", title, re.IGNORECASE)
    if not match:
        return None
    value = _PLACEHOLDER_RE.sub("", match.group(1)).strip(" -—:")
    if not value or value.lower() in {"nombre del módulo", "nombre del modulo", "módulo", "modulo"}:
        return None
    return value


def declared_scope_module(content: str | None) -> str | None:
    """Módulo declarado por la cabecera «Módulo actual: X» del scope (None si no hay)."""
    if not isinstance(content, str) or not content.strip():
        return None
    for record in _heading_records(content):
        declared = _normalise_declared_module(record[2])
        if declared:
            return declared
    return None


def _title_matches_module(title: str, module_id: str) -> bool:
    return bool(re.search(rf"(?<![A-Za-z0-9_]){re.escape(module_id)}(?![A-Za-z0-9_])", title))


def _module_section(content: str, module_id: str | None, selector: str) -> tuple[str | None, str | None, str | None]:
    """Selecciona solo scopes/ACs de módulo y devuelve el ID declarado."""
    if not module_id:
        return None, f"{selector}:missing", None
    records = _heading_records(content)
    declared = [(_normalise_declared_module(record[2]), record) for record in records]
    declared = [(value, record) for value, record in declared if value]
    matches = [record for record in records if _title_matches_module(record[2], module_id)]
    # FW-226 (Example Tool FW-224): `/enriquecer-ticket` prescribe `# ALCANCE ACTIVO —
    # M1: …` seguido de `## Módulo: M1`. Un heading que cae DENTRO del bloque de
    # otro que también nombra el módulo es la misma sección, no una segunda:
    # se conserva el más externo. Dos secciones hermanas siguen siendo ambiguas.
    if len(matches) > 1:
        outer = matches[0]
        outer_end = outer[0] + len(_heading_block(content, outer).splitlines())
        if all(outer[0] < record[0] < outer_end and record[1] > outer[1] for record in matches[1:]):
            matches = [outer]
    # PA-FW-008 (5.3.1 L3.5): las entradas de preservación de un mismo módulo
    # se leen como una sola, en orden (una ampliación al final del fichero).
    if len(matches) > 1 and selector == "preserve_evidence":
        kept: list[tuple[int, int, str, str]] = []
        for record in matches:
            if not any(k[0] < record[0] < k[0] + len(_heading_block(content, k).splitlines()) for k in kept):
                kept.append(record)
        return "".join(_heading_block(content, record) for record in kept), f"{selector}:{module_id}", module_id
    # El heading de scope ``Módulo actual: M1`` ya es la sección canónica. Para
    # acceptance se acepta ``## M1 — Nombre``; en ambos casos se exige una sola.
    if len(matches) > 1:
        raise QueryError("SOURCE_AMBIGUOUS", f"La fuente {selector} tiene varias secciones para {module_id}")
    if matches:
        record = matches[0]
        actual = _normalise_declared_module(record[2]) or module_id
        return _heading_block(content, record), f"{selector}:{actual}", actual
    # Si el caller pide otro módulo, exponer el único módulo documentado
    # permite al gate devolver ambos IDs en vez de confundir ausencia con PASS.
    if len(declared) == 1:
        actual, record = declared[0]
        return _heading_block(content, record), f"{selector}:{actual}", actual
    module_records = [record for record in records if re.fullmatch(r"(?:Módulo|Modulo)\s+[^:]+", record[2], re.IGNORECASE)]
    if len(module_records) == 1:
        actual = _normalise_declared_module(module_records[0][2])
        if actual:
            return _heading_block(content, module_records[0]), f"{selector}:{actual}", actual
    return None, f"{selector}:{module_id}", None


def _scope_section_or_empty(content: str, module_id: str) -> tuple[str | None, str | None, str | None]:
    """Sección de scope del módulo; si es ambigua, ningún texto (load_one la reporta)."""
    try:
        return _module_section(content, module_id, "scope")
    except QueryError as exc:
        if exc.code != "SOURCE_AMBIGUOUS":
            raise
        return "", None, module_id


def _module_evidence_section(
    content: str, module_id: str | None, selector: str, heading_kind: str | None = None,
) -> tuple[str | None, str | None, str | None]:
    """Selecciona la entrada del módulo y, si existe, su subsección de evidencia."""
    selected, selected_selector, actual_module = _module_section(content, module_id, selector)
    if selected is None or not heading_kind:
        return selected, selected_selector, actual_module
    patterns = {
        "ui": re.compile(r"^(?:estados?\s+de\s+ui|ui_states?|estados?\s+de\s+interfaz)(?:\s+[—-].*)?$", re.IGNORECASE),
        "nfr": re.compile(
            r"^(?:nfr|nfrs|restricciones?\s+no\s+funcionales?)"
            r"(?:\s+(?:del\s+)?módulo\s*:\s*.+|\s+[—-].*)?$",
            re.IGNORECASE,
        ),
    }
    pattern = patterns.get(heading_kind)
    if pattern is None:
        return selected, selected_selector, actual_module
    records = _heading_records(selected)
    matches = [record for record in records if pattern.fullmatch(record[2])]
    if len(matches) > 1:
        raise QueryError("SOURCE_AMBIGUOUS", f"La fuente tiene varias subsecciones {selector}")
    if matches:
        return _heading_block(selected, matches[0]), f"{selector}:{actual_module}", actual_module
    # A module evidence source must be its own subsection. Returning the whole
    # module block here would let lines hidden behind a generic normative
    # marker (or unrelated prose) satisfy the evidence validator.
    return None, f"{selector}:{actual_module or module_id}:missing", actual_module


def _marker_section(content: str, selector: str) -> tuple[str | None, str | None]:
    events = [event for event in state_queries.memoized_scan(content).context_markers if event.selector == selector]
    if not events:
        return None, None
    lines = content.splitlines(keepends=True)
    pairs: list[tuple[int, int]] = []
    opening: int | None = None
    for event in events:
        if event.closing:
            if opening is None:
                raise QueryError("SOURCE_MISSING", f"Bloque {selector} con cierre huérfano")
            pairs.append((opening, event.number))
            opening = None
        elif opening is not None:
            raise QueryError("SOURCE_AMBIGUOUS", f"La fuente tiene varios bloques {selector}")
        else:
            opening = event.number
    if opening is not None:
        raise QueryError("SOURCE_MISSING", f"Bloque {selector} sin cierre")
    if len(pairs) > 1:
        raise QueryError("SOURCE_AMBIGUOUS", f"La fuente tiene varios bloques {selector}")
    start, end = pairs[0]
    return "".join(lines[start:end - 1]), selector


def _shared_section(content: str, selector: str) -> tuple[str | None, str | None]:
    marked, marked_selector = _marker_section(content, selector)
    if marked_selector is not None:
        return marked, marked_selector
    heading_pattern = _STABLE_HEADINGS.get(selector)
    if heading_pattern is not None:
        matches = [record for record in _heading_records(content) if heading_pattern.fullmatch(record[2])]
        # Como FW-226: coincidencias anidadas dentro de la más externa son la misma sección.
        if len(matches) > 1:
            outer = matches[0]
            outer_end = outer[0] + len(_heading_block(content, outer).splitlines())
            if all(outer[0] < record[0] < outer_end and record[1] > outer[1] for record in matches[1:]):
                matches = [outer]
        if len(matches) > 1:
            raise QueryError("SOURCE_AMBIGUOUS", f"La fuente tiene varias secciones {selector}")
        if matches:
            return _heading_block(content, matches[0]), selector
    # A shared rule without an explicit marker or a stable heading is not
    # selectable. Returning the whole document would make an unrelated
    # paragraph look like evidence for the obligation and could produce a
    # false PASS. The caller turns this into a NEEDS_INPUT reference.
    return None, selector


_CONTRACT_OWNER_RE = re.compile(
    r"[*_`]{0,2}(?:módulo\s+propietario|modulo\s+propietario|propietario|módulo|modulo)[*_`]{0,2}\s*:\s*"
    r"[*_`]{0,2}\s*[*_`\[]{0,2}\s*([A-Za-z0-9_-]+)\s*[*_`\]]{0,2}",
    re.IGNORECASE,
)
_CONTRACT_FILE_RE = re.compile(r"[*_`]{0,2}archivo[*_`]{0,2}\s*:\s*[*_`]{0,2}\s*`?([^`\n]+?)`?\s*$", re.IGNORECASE | re.MULTILINE)
_CONTRACT_NAME_RE = re.compile(r"^[`\s]*([A-Za-z_][A-Za-z0-9_]*)")
_DEPENDS_LINE_RE = re.compile(r"(?im)^[\s>*_-]*(?:depende(?:ncias)?|dependencies|depends\s+on)\b[^\n:]*:\s*(.+)$")
_MAX_OMITTED_LISTED = 40


def _contract_units(content: str) -> list[dict]:
    """Entradas de ``03_contracts.md`` (heading, bloque y propietario declarado).

    La unidad es el nivel de heading más alto que declara un propietario
    (``**Módulo:**``); los bloques de nivel superior sin entradas dentro cuentan
    como unidad propia. El preámbulo (título y texto antes del primer heading
    de nivel ≥ 2) no es una entrada.
    """
    lines = content.splitlines(keepends=True)
    records = [record for record in _heading_records(content) if record[1] >= 2]
    if not records:
        return []

    def block_end(position: int) -> int:
        start, level = records[position][0], records[position][1]
        for later in records[position + 1:]:
            if later[1] <= level:
                return later[0]
        return len(lines)

    def owner_of(position: int) -> str | None:
        start = records[position][0]
        end = records[position + 1][0] if position + 1 < len(records) else len(lines)
        match = _CONTRACT_OWNER_RE.search("".join(lines[start:end]))
        return match.group(1).strip() if match else None

    owners = [owner_of(position) for position in range(len(records))]
    owned_levels = [records[position][1] for position, owner in enumerate(owners) if owner]
    if not owned_levels:
        return []
    partition_level = min(owned_levels)
    units: list[dict] = []
    cursor = -1
    for position, record in enumerate(records):
        start, level, title, _ = record
        if start < cursor:
            continue
        end = block_end(position)
        if level < partition_level and any(
            other[1] == partition_level and start < other[0] < end for other in records[position + 1:]
        ):
            continue
        units.append({
            "title": title, "text": "".join(lines[start:end]).strip() + "\n",
            "owner": owners[position],
        })
        cursor = end
    return units


def _is_technical_identifier(name: str) -> bool:
    """snake_case, con dígito o CamelCase interior (una mayúscula tras minúscula)."""
    return "_" in name or any(ch.isdigit() for ch in name) or bool(re.search(r"[a-z][A-Z]", name))


def _unit_is_relevant(unit: dict, module_id: str, tokens: set[str], dependency_modules: set[str], text: str) -> bool:
    owner = unit["owner"]
    if _title_matches_module(unit["title"], module_id):
        return True
    if owner and (owner.lower() == module_id.lower() or _title_matches_module(owner, module_id)):
        return True
    if owner and owner.lower() in dependency_modules:
        return True
    name = _CONTRACT_NAME_RE.match(unit["title"])
    if name and len(name.group(1)) >= 4 and name.group(1) in tokens:
        # L8: sin propietario, una palabra común (Estado, Phase, HTTP) no basta;
        # hace falta un identificador técnico citado en el texto del módulo.
        if owner or _is_technical_identifier(name.group(1)):
            return True
    file_match = _CONTRACT_FILE_RE.search(unit["text"])
    return bool(file_match and file_match.group(1).strip() and file_match.group(1).strip() in text)


def select_contracts(content: str, module_id: str | None, module_text: str = "") -> tuple[str, list[str]]:
    """Contratos pertinentes al módulo y los títulos exactos de los que no se entregan.

    Pertinente: lo que declara el módulo (propietario o título), lo que consume
    (interfaces nombradas en su scope o sus dependencias declaradas, su archivo) y
    lo de los módulos de los que declara depender. Sin partición por módulo en el
    archivo (texto libre) se entrega entero, como antes: no hay nada que acotar.
    Nada se descarta en silencio: lo omitido sale como título exacto para la ref.
    """
    units = _contract_units(content) if module_id else []
    if not units:
        return content, []
    tokens = set(re.findall(r"[A-Za-z_][A-Za-z0-9_]{3,}", module_text))
    dependency_modules = {
        token.lower()
        for match in _DEPENDS_LINE_RE.finditer(module_text)
        for token in re.findall(r"[A-Za-z0-9_-]+", match.group(1))
    }
    chosen = [unit for unit in units if _unit_is_relevant(unit, module_id, tokens, dependency_modules, module_text)]
    omitted = [unit["title"] for unit in units if unit not in chosen]
    if not chosen:
        return (
            f"Sin contratos declarados para {module_id} en `_project/03_contracts.md`: "
            f"{len(omitted)} entradas de otros módulos quedan como referencia exacta (ver refs).\n"
        ), omitted
    header = (
        f"Contratos de {module_id} (selección de `_project/03_contracts.md`; "
        f"{len(omitted)} entradas de otros módulos quedan como referencia exacta).\n\n"
    )
    return header + "\n".join(unit["text"] for unit in chosen), omitted


def select_invariants(
    content: str, module_id: str | None, module_ids, module_text: str = "",
) -> tuple[str, list[str]]:
    """Invariantes sin las reglas que nombran solo a otros módulos.

    La unidad es la cabecera más profunda (hoja); una cabecera con hijos conserva
    su texto previo a los hijos. Se omite una hoja solo si su título nombra un id
    de ``module_ids`` que no es el objetivo ni una dependencia declarada y no
    nombra al objetivo. Sin omisiones se devuelve ``content`` tal cual.
    """
    if not module_id or not isinstance(content, str):
        return content, []
    records = _heading_records(content)
    if not records:
        return content, []
    dependency_modules = {
        token.lower()
        for match in _DEPENDS_LINE_RE.finditer(module_text or "")
        for token in re.findall(r"[A-Za-z0-9_-]+", match.group(1))
    }
    others = [
        other for other in module_ids
        if isinstance(other, str) and other and other != module_id and other.lower() not in dependency_modules
    ]
    if not others:
        return content, []
    lines = content.splitlines(keepends=True)
    omitted: list[str] = []
    kept: list[str] = [] if records[0][0] == 0 else ["".join(lines[:records[0][0]])]
    for position, (start, level, title, _) in enumerate(records):
        end = records[position + 1][0] if position + 1 < len(records) else len(lines)
        is_leaf = position + 1 >= len(records) or records[position + 1][1] <= level
        if (
            is_leaf and not _title_matches_module(title, module_id)
            and any(_title_matches_module(title, other) for other in others)
        ):
            omitted.append(title)
            continue
        kept.append("".join(lines[start:end]))
    if not omitted:
        return content, []
    header = (
        f"Invariantes de {module_id} (selección de `_project/03_invariants.md`; "
        f"{len(omitted)} reglas de otros módulos quedan como referencia exacta).\n\n"
    )
    return header + "".join(kept), omitted


def _module_context_text(module_id: str | None, sources_content: dict[str, str | None]) -> str:
    """Texto del scope y de las dependencias del módulo: de aquí salen los contratos que consume."""
    if not module_id:
        return ""
    chunks: list[str] = []
    for relative, selector in (
        ("_project/04_active_scope.md", "scope"),
        ("_project/11_file_dependencies.md", "preserve_evidence"),
    ):
        text = sources_content.get(relative)
        if not text:
            continue
        try:
            section, _, _ = _module_section(text, module_id, selector)
        except QueryError:
            section = None
        if section:
            chunks.append(section)
    return "\n".join(chunks)


def _is_module_identifier(val: str) -> bool:
    if not val:
        return False
    return bool(re.fullmatch(r"[a-z]\d+[a-z0-9_-]*", val.strip(), re.IGNORECASE))


def _declared_endpoints_from_sources(
    module_id: str | None,
    sources_content: dict[str, str | None],
) -> set[tuple[str, str]]:
    """Extrae tuplas (método, ruta) declaradas para el módulo en scope, contratos o dependencias."""
    if not module_id:
        return set()
    endpoints: set[tuple[str, str]] = set()
    methods = {"get", "post", "put", "delete", "patch", "options", "head", "trace"}

    # 1. 11_file_dependencies.md
    dep_content = sources_content.get("_project/11_file_dependencies.md") or sources_content.get("preserve_evidence")
    if dep_content:
        dep_section, _, actual_mod = _module_section(dep_content, module_id, "preserve_evidence")
        if actual_mod:
            if actual_mod.lower() == module_id.lower() or _title_matches_module(actual_mod, module_id):
                target_text = dep_section or ""
            else:
                target_text = ""
        elif not any(
            re.search(r"\b[A-Za-z0-9_-]*\d+[A-Za-z0-9_-]*\b", r[2])
            or _normalise_declared_module(r[2])
            or re.search(r"(?:módulo|modulo)\s+", r[2], re.IGNORECASE)
            for r in _heading_records(dep_content)
            if r[1] >= 2
        ):
            target_text = dep_content
        else:
            target_text = ""
        for match in re.finditer(r"api_contract\s*:\s*(?:([A-Za-z]+)\s+)?(/[^#\s\n]+)", target_text):
            m, p = match.groups()
            method = m.lower() if m and m.lower() in methods else "*"
            endpoints.add((method, p.rstrip("/")))
        for match in re.finditer(r"\b(GET|POST|PUT|DELETE|PATCH|OPTIONS|HEAD|TRACE)\s+(/[^#\s\n]+)", target_text, re.IGNORECASE):
            m, p = match.groups()
            endpoints.add((m.lower(), p.rstrip("/")))

    # 2. 04_active_scope.md
    scope_content = sources_content.get("_project/04_active_scope.md") or sources_content.get("scope")
    if scope_content:
        scope_section, _, actual_mod = _scope_section_or_empty(scope_content, module_id)
        if actual_mod:
            if actual_mod.lower() == module_id.lower() or _title_matches_module(actual_mod, module_id):
                target_text = scope_section or ""
            else:
                target_text = ""
        elif not any(
            re.search(r"\b[A-Za-z0-9_-]*\d+[A-Za-z0-9_-]*\b", r[2])
            or _normalise_declared_module(r[2])
            or re.search(r"(?:módulo|modulo)\s+", r[2], re.IGNORECASE)
            for r in _heading_records(scope_content)
            if r[1] >= 2
        ):
            target_text = scope_content
        else:
            target_text = ""
        for match in re.finditer(r"\b(GET|POST|PUT|DELETE|PATCH|OPTIONS|HEAD|TRACE)\s+(/[A-Za-z0-9_.~/{}-]+)", target_text, re.IGNORECASE):
            m, p = match.groups()
            endpoints.add((m.lower(), p.rstrip("/")))
        for match in re.finditer(r"api_contract\.yaml#paths\.([A-Za-z0-9_.~/{}-]+)", target_text):
            p = match.group(1)
            if not p.startswith("/"):
                p = "/" + p
            endpoints.add(("*", p.rstrip("/")))

    # 3. 03_contracts.md
    contracts_content = sources_content.get("_project/03_contracts.md") or sources_content.get("contracts")
    if contracts_content:
        contract_records = _heading_records(contracts_content)
        matching_blocks: list[str] = []
        has_any_module_partition = False
        for rec in contract_records:
            if rec[1] >= 2:
                direct_text = _heading_direct_content(contracts_content, rec)
                block = _heading_block(contracts_content, rec)
                title = rec[2].strip()
                owner_match = re.search(
                    r"[*_`]{0,2}(?:módulo\s+propietario|modulo\s+propietario|propietario|módulo|modulo)[*_`]{0,2}\s*:\s*[*_`]{0,2}\s*[*_`\[]{0,2}\s*([A-Za-z0-9_-]+)\s*[*_`\]]{0,2}",
                    direct_text,
                    re.IGNORECASE,
                )
                owner = owner_match.group(1).strip() if owner_match else None
                if (
                    owner
                    or re.search(r"\b[A-Za-z0-9_-]*\d+[A-Za-z0-9_-]*\b", title)
                    or re.search(r"(?:módulo|modulo)\s+", title, re.IGNORECASE)
                    or _normalise_declared_module(title)
                ):
                    has_any_module_partition = True
                if (owner and (owner.lower() == module_id.lower() or _title_matches_module(owner, module_id))) or _title_matches_module(title, module_id):
                    matching_blocks.append(block)

        if not matching_blocks and not has_any_module_partition:
            matching_blocks.append(contracts_content)

        for block in matching_blocks:
            for match in re.finditer(r"api_contract\.yaml#paths\.([A-Za-z0-9_.~/{}-]+)", block):
                p = match.group(1)
                if not p.startswith("/"):
                    p = "/" + p
                endpoints.add(("*", p.rstrip("/")))
            for match in re.finditer(r"\b(GET|POST|PUT|DELETE|PATCH|OPTIONS|HEAD|TRACE)\s+(/[A-Za-z0-9_.~/{}-]+)", block, re.IGNORECASE):
                m, p = match.groups()
                endpoints.add((m.lower(), p.rstrip("/")))

    return endpoints


def _declared_entities_from_sources(
    module_id: str | None,
    sources_content: dict[str, str | None],
) -> set[str]:
    """Extrae nombres de entidades declaradas para el módulo en dependencias, scope o contratos."""
    if not module_id:
        return set()
    entities: set[str] = set()

    dep_content = sources_content.get("_project/11_file_dependencies.md") or sources_content.get("preserve_evidence")
    if dep_content:
        dep_section, _, actual_mod = _module_section(dep_content, module_id, "preserve_evidence")
        if actual_mod:
            if actual_mod.lower() == module_id.lower() or _title_matches_module(actual_mod, module_id):
                target_text = dep_section or ""
            else:
                target_text = ""
        elif not any(
            re.search(r"\b[A-Za-z0-9_-]*\d+[A-Za-z0-9_-]*\b", r[2])
            or _normalise_declared_module(r[2])
            or re.search(r"(?:módulo|modulo)\s+", r[2], re.IGNORECASE)
            for r in _heading_records(dep_content)
            if r[1] >= 2
        ):
            target_text = dep_content
        else:
            target_text = ""
        for match in re.finditer(r"[*_]{0,2}data_model[*_]{0,2}\s*:\s*[*_]{0,2}\s*([A-Za-z0-9_]+)", target_text):
            entities.add(match.group(1).strip())
        for match in re.finditer(r"interfaces_requeridas\s*:\s*\n((?:\s*-\s*[^\n]+\n)+)", target_text):
            for item in re.finditer(r"-\s*([A-Za-z0-9_]+)", match.group(1)):
                entities.add(item.group(1).strip())
        for match in re.finditer(r"::([A-Za-z0-9_]+)", target_text):
            entities.add(match.group(1).strip())

    scope_content = sources_content.get("_project/04_active_scope.md") or sources_content.get("scope")
    if scope_content:
        scope_section, _, actual_mod = _scope_section_or_empty(scope_content, module_id)
        if actual_mod:
            if actual_mod.lower() == module_id.lower() or _title_matches_module(actual_mod, module_id):
                target_text = scope_section or ""
            else:
                target_text = ""
        elif not any(
            re.search(r"\b[A-Za-z0-9_-]*\d+[A-Za-z0-9_-]*\b", r[2])
            or _normalise_declared_module(r[2])
            or re.search(r"(?:módulo|modulo)\s+", r[2], re.IGNORECASE)
            for r in _heading_records(scope_content)
            if r[1] >= 2
        ):
            target_text = scope_content
        else:
            target_text = ""
        for match in re.finditer(r"(?:data_model|Entidad)\s*:\s*([A-Za-z0-9_]+)", target_text, re.IGNORECASE):
            entities.add(match.group(1).strip())

    contracts_content = sources_content.get("_project/03_contracts.md") or sources_content.get("contracts")
    if contracts_content:
        contract_records = _heading_records(contracts_content)
        matching_blocks: list[str] = []
        has_any_module_partition = False
        for rec in contract_records:
            if rec[1] >= 2:
                direct_text = _heading_direct_content(contracts_content, rec)
                block = _heading_block(contracts_content, rec)
                title = rec[2].strip()
                owner_match = re.search(
                    r"[*_`]{0,2}(?:módulo\s+propietario|modulo\s+propietario|propietario|módulo|modulo)[*_`]{0,2}\s*:\s*[*_`]{0,2}\s*[*_`\[]{0,2}\s*([A-Za-z0-9_-]+)\s*[*_`\]]{0,2}",
                    direct_text,
                    re.IGNORECASE,
                )
                owner = owner_match.group(1).strip() if owner_match else None
                if (
                    owner
                    or re.search(r"\b[A-Za-z0-9_-]*\d+[A-Za-z0-9_-]*\b", title)
                    or re.search(r"(?:módulo|modulo)\s+", title, re.IGNORECASE)
                    or _normalise_declared_module(title)
                ):
                    has_any_module_partition = True
                if (owner and (owner.lower() == module_id.lower() or _title_matches_module(owner, module_id))) or _title_matches_module(title, module_id):
                    matching_blocks.append(block)

        if not matching_blocks and not has_any_module_partition:
            matching_blocks.append(contracts_content)

        for block in matching_blocks:
            for match in re.finditer(r"data_model\s*:\s*([A-Za-z0-9_]+)", block):
                entities.add(match.group(1).strip())

    return entities


def _data_model_section(
    content: str,
    module_id: str | None,
    *,
    connected_entities: set[str] | None = None,
) -> tuple[str | None, str, list[str]]:
    """Extrae selectivamente el modelo de datos para el módulo activo."""
    selector = f"module_data_model:{module_id}" if module_id else "module_data_model"
    if not content or not content.strip():
        return "", selector, []
    if not module_id:
        return content, selector, []

    # 1. Parsear relaciones en diagrama ER de Mermaid si existe
    er_graph: dict[str, set[str]] = {}
    er_match = re.search(r"```mermaid\s*\n\s*erDiagram\s*\n(.*?)\n```", content, re.DOTALL)
    er_diagram_block = er_match.group(0) if er_match else ""
    if er_match:
        er_text = er_match.group(1)
        for line in er_text.splitlines():
            line = line.strip()
            if not line or line.startswith("%%"):
                continue
            rel_match = re.search(r"([A-Za-z0-9_]+)\s+[|o}{.-]+\s+([A-Za-z0-9_]+)", line)
            if rel_match:
                e1, e2 = rel_match.group(1).casefold(), rel_match.group(2).casefold()
                er_graph.setdefault(e1, set()).add(e2)
                er_graph.setdefault(e2, set()).add(e1)

    # 2. Extraer 'Restricciones e invariantes del modelo' si existe
    invariants_block = ""
    inv_match = re.search(r"(##\s+Restricciones\s+e\s+invariantes\s+del\s+modelo.*)", content, re.DOTALL | re.IGNORECASE)
    if inv_match:
        inv_text = inv_match.group(1)
        next_sec = re.search(r"\n##\s+(?!Restricciones\s+e\s+invariantes\s+del\s+modelo)", inv_text, re.IGNORECASE)
        invariants_block = inv_text[:next_sec.start()].strip() if next_sec else inv_text.strip()

    # 3. Clasificar secciones de entidades
    records = _heading_records(content)
    structural_re = re.compile(
        r"^(?:modelo\s+de\s+datos|estado\s+de\s+plantilla|diagrama\s+er|entidades|"
        r"restricciones\s+e\s+invariantes\s+del\s+modelo|no\s+aplica).*$",
        re.IGNORECASE,
    )
    entity_records = [r for r in records if r[1] >= 2 and not structural_re.match(r[2].strip())]

    owned_entities: set[str] = set()
    entity_blocks: list[tuple[str, str, str, str | None]] = []

    for rec in entity_records:
        block = _heading_block(content, rec)
        direct_text = _heading_direct_content(content, rec)
        title = rec[2].strip()
        ent_name = re.sub(r"^(?:entidad|entity)\s+", "", title, flags=re.IGNORECASE).strip()
        ent_name = re.sub(r"[#*_`]", "", ent_name).strip()

        owner_match = re.search(
            r"[*_`]{0,2}(?:módulo\s+propietario|modulo\s+propietario|propietario|módulo|modulo)[*_`]{0,2}\s*:\s*[*_`]{0,2}\s*[*_`\[]{0,2}\s*([A-Za-z0-9_-]+)\s*[*_`\]]{0,2}",
            direct_text,
            re.IGNORECASE,
        )
        owner = owner_match.group(1).strip() if owner_match else None
        if owner and _title_matches_module(owner, module_id):
            owned_entities.add(ent_name.casefold())

        entity_blocks.append((ent_name, title, block, owner))

    # 4. Resolver clausura de entidades objetivo (propias + conectadas)
    target_entities: set[str] = set(owned_entities)
    if connected_entities:
        for ce in connected_entities:
            target_entities.add(ce.casefold())

    # Una capa transitiva en el erDiagram
    for te in list(target_entities):
        if te in er_graph:
            target_entities.update(er_graph[te])

    # 5. Filtrar bloques de entidades
    included_blocks: list[str] = []
    for ent_name, title, block, owner in entity_blocks:
        is_owned = bool(owner and _title_matches_module(owner, module_id))
        is_connected = ent_name.casefold() in target_entities
        if is_owned or is_connected:
            included_blocks.append(block.strip())

    if not included_blocks and not owned_entities:
        return "", selector, []

    # 6. Reconstruir documento filtrado
    output_parts = ["# Modelo de Datos — Selección por Módulo\n"]
    if er_diagram_block:
        output_parts.append(f"## Diagrama ER\n\n{er_diagram_block}\n")
    if included_blocks:
        output_parts.append("## Entidades\n\n" + "\n\n".join(included_blocks) + "\n")
    if invariants_block:
        output_parts.append(f"{invariants_block}\n")

    return "\n".join(output_parts).strip() + "\n", selector, []


def _api_contract_section(
    content: str,
    module_id: str | None,
    *,
    declared_endpoints: set[tuple[str, str]] | None = None,
) -> tuple[str | None, str, list[str]]:
    """Extrae selectivamente el contrato OpenAPI para el módulo activo y resuelve clausura de schemas."""
    selector = f"module_api_contract:{module_id}" if module_id else "module_api_contract"
    if not content or not content.strip():
        return "", selector, []

    spec_problems: list[str] = []

    if yaml is None:
        raise QueryError("DEPENDENCY_MISSING", "PyYAML es requerido para procesar api_contract.yaml")

    try:
        doc = yaml.safe_load(content)
    except yaml.YAMLError as exc:
        return "", selector, [f"api_contract.yaml no es YAML válido: {exc}"]

    if not isinstance(doc, dict):
        return "", selector, ["api_contract.yaml debe ser un objeto YAML"]

    if "webhooks" in doc and doc["webhooks"]:
        spec_problems.append("api_contract contiene construcción no soportada: webhooks")

    if not module_id:
        return content, selector, spec_problems

    openapi = doc.get("openapi", "3.1.0")
    info = doc.get("info", {})
    servers = doc.get("servers", [])
    security = doc.get("security", [])

    filtered: dict = {
        "openapi": openapi,
        "info": info,
    }
    if servers:
        filtered["servers"] = servers
    if security:
        filtered["security"] = security

    filtered["paths"] = {}
    paths = doc.get("paths", {})
    if not isinstance(paths, dict):
        paths = {}

    http_methods = {"get", "post", "put", "delete", "patch", "options", "head", "trace"}

    norm_declared: set[tuple[str, str]] = set()
    if declared_endpoints:
        for m, p in declared_endpoints:
            norm_declared.add((m.lower(), p.rstrip("/")))

    for path_str, path_item in paths.items():
        if not isinstance(path_item, dict):
            continue
        clean_path = path_str.rstrip("/")
        common_parameters = path_item.get("parameters", [])
        path_operations: dict[str, dict] = {}

        for method_key, op in path_item.items():
            method_lower = method_key.lower()
            if method_lower not in http_methods or not isinstance(op, dict):
                continue

            op_tags = [str(t).strip() for t in op.get("tags", []) if str(t).strip()]
            op_x_module = str(op.get("x-module", "")).strip() if op.get("x-module") else None

            # Detectar conflicto entre tags y x-module
            if op_x_module and op_tags:
                for t in op_tags:
                    if _is_module_identifier(t) and _is_module_identifier(op_x_module) and t.lower() != op_x_module.lower():
                        raise QueryError(
                            "SOURCE_AMBIGUOUS",
                            f"Operación {method_key.upper()} {path_str} tiene conflicto entre tags={op_tags} y x-module={op_x_module}",
                        )
                    if (t.lower() == module_id.lower() and op_x_module.lower() != module_id.lower()) or (
                        op_x_module.lower() == module_id.lower() and _is_module_identifier(t) and t.lower() != module_id.lower()
                    ):
                        raise QueryError(
                            "SOURCE_AMBIGUOUS",
                            f"Operación {method_key.upper()} {path_str} tiene conflicto entre tags={op_tags} y x-module={op_x_module}",
                        )

            # Evaluar pertenencia al módulo
            belongs = False
            explicit_other = False

            if op_x_module:
                if op_x_module.lower() == module_id.lower() or _title_matches_module(op_x_module, module_id):
                    belongs = True
                else:
                    explicit_other = True

            for t in op_tags:
                if t.lower() == module_id.lower() or _title_matches_module(t, module_id):
                    belongs = True
                elif _is_module_identifier(t) and t.lower() != module_id.lower():
                    explicit_other = True

            if not belongs and not explicit_other:
                for dec_m, dec_p in norm_declared:
                    if dec_p == clean_path and dec_m in {"*", method_lower}:
                        belongs = True
                        break

            if belongs and not explicit_other:
                path_operations[method_key] = op

        if path_operations:
            filtered_item = dict(path_operations)
            if common_parameters:
                filtered_item["parameters"] = common_parameters
            if "servers" in path_item:
                filtered_item["servers"] = path_item["servers"]
            if "summary" in path_item:
                filtered_item["summary"] = path_item["summary"]
            if "description" in path_item:
                filtered_item["description"] = path_item["description"]
            filtered["paths"][path_str] = filtered_item

    if not filtered["paths"]:
        return "", selector, spec_problems

    # Clausura transitiva de componentes vía $ref
    visited_refs: set[str] = set()
    pending_refs: list[str] = []

    def collect_refs(obj: object):
        if isinstance(obj, dict):
            for k, v in obj.items():
                if k == "$ref" and isinstance(v, str):
                    pending_refs.append(v)
                else:
                    collect_refs(v)
        elif isinstance(obj, list):
            for item in obj:
                collect_refs(item)

    collect_refs(filtered["paths"])
    collect_refs(filtered.get("security", []))

    # Esquemas de seguridad requeridos por global security y por operaciones individuales
    all_security_reqs: list[dict] = []
    if isinstance(filtered.get("security"), list):
        all_security_reqs.extend([s for s in filtered["security"] if isinstance(s, dict)])
    for path_obj in filtered.get("paths", {}).values():
        if isinstance(path_obj, dict):
            for method_key, op_obj in path_obj.items():
                if isinstance(op_obj, dict) and isinstance(op_obj.get("security"), list):
                    all_security_reqs.extend([s for s in op_obj["security"] if isinstance(s, dict)])

    components_in_doc = doc.get("components", {}) if isinstance(doc.get("components"), dict) else {}
    doc_security_schemes = components_in_doc.get("securitySchemes", {}) if isinstance(components_in_doc.get("securitySchemes"), dict) else {}

    for sec_req in all_security_reqs:
        for scheme_name in sec_req:
            if scheme_name in doc_security_schemes:
                filtered.setdefault("components", {}).setdefault("securitySchemes", {})[scheme_name] = doc_security_schemes[scheme_name]
                collect_refs(doc_security_schemes[scheme_name])
            else:
                err = f"api_contract contiene esquema de seguridad no definido: {scheme_name}"
                if err not in spec_problems:
                    spec_problems.append(err)

    while pending_refs:
        ref = pending_refs.pop(0)
        if ref in visited_refs:
            continue
        visited_refs.add(ref)

        if ref.startswith("http://") or ref.startswith("https://") or ("#" in ref and not ref.startswith("#/")) or (not ref.startswith("#") and (".yaml" in ref or ".json" in ref or ".yml" in ref)):
            spec_problems.append(f"api_contract contiene referencia externa no resuelta en entorno hermético: {ref}")
            continue
        if not ref.startswith("#/"):
            spec_problems.append(f"api_contract contiene referencia rota: {ref}")
            continue

        parts = ref[2:].split("/")
        curr = doc
        broken = False
        for p in parts:
            decoded = p.replace("~1", "/").replace("~0", "~")
            if isinstance(curr, dict) and decoded in curr:
                curr = curr[decoded]
            elif isinstance(curr, list) and decoded.isdigit() and int(decoded) < len(curr):
                curr = curr[int(decoded)]
            else:
                broken = True
                break
        if broken:
            spec_problems.append(f"api_contract contiene referencia rota: {ref}")
            continue

        if len(parts) >= 3 and parts[0] == "components":
            comp_type = parts[1]
            comp_name = parts[2].replace("~1", "/").replace("~0", "~")
            if len(parts) == 3:
                filtered.setdefault("components", {}).setdefault(comp_type, {})[comp_name] = curr
            else:
                top_comp = doc.get("components", {}).get(comp_type, {}).get(comp_name)
                if top_comp is not None:
                    filtered.setdefault("components", {}).setdefault(comp_type, {})[comp_name] = top_comp
                    collect_refs(top_comp)

        collect_refs(curr)

    dumped = yaml.safe_dump(filtered, sort_keys=False, allow_unicode=True)
    return dumped, selector, spec_problems


def _load_obligations(root: Path) -> tuple[list[dict], str]:
    path = resolve_source_path(root, OBLIGATION_MATRIX)
    if not path.is_file():
        raise QueryError(
            "SOURCE_MISSING",
            f"Matriz de obligaciones ausente: {path}",
        )
    content, digest = _read_stable(path)
    try:
        document = json.loads(content)
    except json.JSONDecodeError as exc:
        raise QueryError("SOURCE_MISSING", f"Matriz de obligaciones inválida: {path}") from exc
    obligations = document.get("obligations") if isinstance(document, dict) else None
    if not isinstance(obligations, list) or not all(isinstance(item, dict) for item in obligations):
        raise QueryError("SOURCE_MISSING", f"Matriz de obligaciones inválida: {path}")
    # Fail before opening any document if the matrix would weaken a control.
    state_queries._normalise_sources({"hashes": {}, "sections": {}, "obligations": obligations})
    return [dict(item) for item in obligations], digest


def _is_active_for_view(raw: dict, view: str) -> bool:
    activation = raw.get("activate_before")
    if view == "recovery":
        return activation in {"startup", "recovery"}
    return VIEW_ORDER.get(activation, 99) <= VIEW_ORDER.get(view, -1)


VIEW_ORDER = state_queries.VIEW_ORDER


def _section_names(
    view: str, target: str | None, state: dict | None, obligations: list[dict],
    preliminary: dict[str, dict], explicit: bool,
) -> list[str]:
    if view == "startup":
        return ["scope"] if explicit and target else []
    if view == "recovery":
        return ["recovery", "journal"]
    kind = state_queries.target_kind(target)
    source_view = {"sections": preliminary, "obligations": obligations,
                   "applicability": state_queries.parse_applicability(preliminary.get("acceptance", {}).get("content"))}
    plan = state_queries.context_plan(view, target, kind, state or {}, source_view)
    return plan["required_sections"]


def _preliminary_section_names(view: str, target: str | None, explicit: bool) -> list[str]:
    """Devuelve solo las lecturas necesarias para calcular aplicabilidad."""
    if view in {"startup", "recovery"}:
        return ["scope"] if explicit and target else []
    if view in {"implementation", "closure"} and target:
        if state_queries.target_kind(target) == "module":
            return ["scope", "acceptance"]
    return []


def load_sources(
    root: Path,
    view: str,
    module_id: str | None,
    *,
    explicit: bool = False,
    state: dict | None = None,
) -> dict:
    """Carga fuentes activadas, leyendo cada archivo como máximo una vez."""
    with state_queries.query_memo():
        return _load_sources(root, view, module_id, explicit=explicit, state=state)


def _load_sources(
    root: Path,
    view: str,
    module_id: str | None,
    *,
    explicit: bool = False,
    state: dict | None = None,
) -> dict:
    if view not in state_queries.VIEWS:
        raise QueryError("INVALID_VIEW", f"Vista no soportada: {view}")
    obligations, obligations_digest = _load_obligations(root)
    cache: dict[str, tuple[str | None, str]] = {}
    sections: dict[str, dict] = {}

    def read(relative: str) -> tuple[str | None, str]:
        if relative in cache:
            return cache[relative]
        path = resolve_source_path(root, relative)
        if not path.exists():
            cache[relative] = (None, "MISSING")
        else:
            cache[relative] = _read_stable(path)
        return cache[relative]

    # Pre-cache selection sources to ensure they are available for extraction
    # and their digests are part of the query fingerprint:
    for sel_path in (
        "_project/04_active_scope.md",
        "_project/03_contracts.md",
        "_project/11_file_dependencies.md",
    ):
        read(sel_path)

    sel_sources_content = {
        "_project/04_active_scope.md": cache.get("_project/04_active_scope.md", (None, ""))[0],
        "_project/03_contracts.md": cache.get("_project/03_contracts.md", (None, ""))[0],
        "_project/11_file_dependencies.md": cache.get("_project/11_file_dependencies.md", (None, ""))[0],
    }
    declared_endpoints = _declared_endpoints_from_sources(module_id, sel_sources_content)
    connected_entities = _declared_entities_from_sources(module_id, sel_sources_content)

    def load_one(name: str) -> None:
        definition = SOURCE_DEFINITIONS[name]
        content, digest = read(definition["path"])
        selected = content
        selector = definition["selector"]
        actual_module = None
        spec_problems: list[str] = []
        omitted_contracts: list[str] = []
        ambiguous = False
        if content is not None:
            if definition["source_kind"] == "module":
                # PA-FW-011: una fuente de módulo ambigua es un motivo más de la
                # respuesta (ambiguous_source), no un error que oculte el resto.
                try:
                    selected, selector, actual_module = _module_evidence_section(
                        content, module_id, definition["selector"], definition.get("evidence_heading")
                    )
                except QueryError as exc:
                    if exc.code != "SOURCE_AMBIGUOUS":
                        raise
                    selected, selector, ambiguous = None, f"{definition['selector']}:ambiguous", True
            elif name == "journal":
                # The session journal is a global continuity artifact, not a
                # normative subsection. Its canonical selector is the whole
                # file; unlike shared rules it does not need a marker.
                selected, selector = content, definition["selector"]
            elif name == "data_model_spec":
                selected, selector, spec_problems = _data_model_section(
                    content, module_id, connected_entities=connected_entities
                )
            elif name == "api_contract_spec":
                selected, selector, spec_problems = _api_contract_section(
                    content, module_id, declared_endpoints=declared_endpoints
                )
            elif definition.get("full_document"):
                selected, selector = content, definition["selector"]
            else:
                selected, selector = _shared_section(content, definition["selector"])
                if name == "contracts" and selected:
                    selected, omitted_contracts = select_contracts(
                        selected, module_id,
                        _module_context_text(module_id, sel_sources_content),
                    )
                elif name == "invariants" and selected and module_id:
                    selected, omitted_contracts = select_invariants(
                        selected, module_id, list(((state or {}).get("module_statuses") or {}).keys()),
                        _module_context_text(module_id, sel_sources_content),
                    )
        obligation_ids = [
            item["id"] for item in obligations
            if state_queries.OBLIGATION_SECTION.get(item.get("id")) == name
        ]
        sections[name] = {
            "path": definition["path"], "selector": selector,
            "content": selected, "source_kind": definition["source_kind"],
            "obligation_ids": obligation_ids,
            **({"module_id": actual_module} if actual_module else {}),
            **({"spec_problems": spec_problems} if spec_problems else {}),
            **({"omitted": omitted_contracts} if omitted_contracts else {}),
            **({"ambiguous": True} if ambiguous else {}),
        }

    # First load the minimal scope/acceptance pair needed to determine
    # applicability. The complete plan is then derived exactly once and its
    # required sections drive every remaining read.
    for name in _preliminary_section_names(view, module_id, explicit):
        load_one(name)
    applicability = state_queries.parse_applicability(
        sections.get("acceptance", {}).get("content")
    )
    plan_sources = {
        "sections": sections,
        "obligations": obligations,
        "applicability": applicability,
    }
    plan = state_queries.context_plan(
        view, module_id, state_queries.target_kind(module_id), state or {}, plan_sources
    )
    # Sin estado no se puede reutilizar de forma segura un plan cuya política
    # depende de tier, receipts o specs. Se conserva como diagnóstico de la
    # carga, pero el consumidor puro lo deriva con su estado real.
    plan["state_bound"] = state is not None
    for name in plan["required_sections"]:
        if name not in sections:
            load_one(name)

    # Applicability is the same input used by the one plan above; do not parse
    # or derive a second policy after loading the remaining documents.
    # If a caller gives a fully populated state, a classification may be
    # represented in the acceptance source only; never infer n/a from absence.
    hashes = {OBLIGATION_MATRIX: obligations_digest}
    for relative, (_, digest) in cache.items():
        hashes[relative] = digest
    return {
        "hashes": hashes, "sections": sections,
        "obligations": obligations, "applicability": applicability,
        "plan": plan,
    }
