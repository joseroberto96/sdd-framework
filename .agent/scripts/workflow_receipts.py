#!/usr/bin/env python3
"""Recibos mecánicos para APPROVE, revisión independiente y bootstrap local."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
import re
import subprocess
import sys
import unicodedata
from pathlib import Path

_SCRIPTS_DIR = str(Path(__file__).resolve().parent)
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)
import validate_acceptance as _va


ROOT = Path(__file__).resolve().parents[2]
STATE = ROOT / "_project" / "03_project_state.json"

APPROVAL_CRITICAL_KEYS = {
    "prd", "phase_plan", "decisions", "tier", "current_phase", "dependencies"
}
APPROVAL_DERIVED_KEYS = {
    "active_scope", "acceptance", "data_model", "api_contract", "contracts", "module_spec"
}
REVIEW_MODES = {
    "READ_ONLY_SUBAGENTS",
    "INDEPENDENT_SUBAGENT_NO_ISOLATION",
    "HUMAN_INDEPENDENT_REVIEW",
}
# FW-144: resultados que `verificar-ejecutable` puede declarar. Son los mismos
# tres que la prosa del paso 3.8 ya aceptaba ("fallida"/"diferida" no llegan
# aquí: bloquean antes y no producen receipt).
EXECUTION_KINDS = {"ok_automatizada", "ok_manual", "no_aplica"}
MIN_EXECUTION_EVIDENCE = 20
RECEIPT_EXCLUDED_PATHS = {
    "_project/00_session_brief.json",
    "_project/03_arch_decisions.md",
    "_project/03_project_state.json",
    "_project/06_bug_fixes.md",
    "_project/07_pending_clarifications.md",
    # Manifiesto de transacción del motor: es andamiaje de la publicación
    # multiarchivo, no trabajo del producto. Sin excluirlo, abrir una
    # transacción cambia el hash del diff e invalida todo execution receipt
    # vigente (FW-174).
    "_project/.state_transaction.json",
    "_project/core.md",
    "_project/state_log.jsonl",
    "_project/24_change_log.md",
}

# S4 (Example Tool FW-T07): registrar/refrescar un APPROVE_RECEIPT exigía adivinar
# el payload por ensayo y error — cada fallo solo revelaba el SIGUIENTE
# campo ausente. Estas plantillas se anexan a TODO ValueError de validación
# de payload en record_approval()/refresh_derived_approval() para que un
# solo fallo alcance a escribir la invocación correcta (contrato completo +
# ejemplo CLI invocable).
APPROVAL_RECEIPT_CONTRACT = (
    "Contrato de record_approval_receipt (los primeros 4 campos son obligatorios):\n"
    "  - module (string): módulo con work_tier ya clasificado en "
    "03_project_state.json.work_tiers\n"
    "  - approved_by (string): debe ser exactamente 'builder'\n"
    "  - evidence (string no vacío): evidencia de la aprobación explícita\n"
    "  - risk_summary (string no vacío): resumen del riesgo asumido\n"
    "  - adr_refs (list[string], OPCIONAL en tier_0/tier_1): IDs de ADR ya registradas en _decisions; "
    "obligatorio y no vacío si el tier del módulo es 'tier_2'\n"
    "  - covers_dependencies (list[string], OPCIONAL): dependencias externas "
    "fijadas que cubre esta aprobación\n"
    "  - covers_changes (list[string], OPCIONAL): lo que el plan prevé tocar y no caduca el recibo: "
    "'decisions:aditivas', 'module_spec:clasificar', 'prd:## <encabezado>', 'data_model:## <encabezado>', "
    "'contracts:## <encabezado>' (H1 o H2 que exista una sola vez) y 'api_contract:/<ruta>'. "
    "En la PRD solo se toleran casillas y '— evidencia:'. Tu scope y tus ACs no se declaran\n"
    "Ejemplo mínimo de invocación válida:\n"
    "  python3 .agent/scripts/state_manager.py mutate --action record_approval_receipt "
    "--payload '{\"module\": \"M1\", \"approved_by\": \"builder\", "
    "\"evidence\": \"aprobación explícita del builder\", "
    "\"risk_summary\": \"cambio aditivo, riesgo bajo\", \"adr_refs\": [\"ADR-001\"]}'"
)
REFRESH_APPROVAL_RECEIPT_CONTRACT = (
    "Contrato de refresh_approval_receipt (los primeros 4 campos son obligatorios):\n"
    "  - module (string): módulo con APPROVE_RECEIPT vigente\n"
    "  - parent_fingerprint (string): fingerprint del APPROVE_RECEIPT vigente que se deriva\n"
    "  - derived_from (list[string] no vacía, cada elemento string no vacío): "
    "fuentes trazables de la derivación\n"
    "  - evidence (string no vacío): evidencia de que la derivación es aditiva\n"
    "  - additive_rationale (string, OPCIONAL): solo cuando la derivación reescribe "
    "líneas ya aprobadas (no las agrega al final) — justifica que es una reescritura "
    "narrativa de una sección ya evidenciada, sin cambiar compromisos ni ACs. NO "
    "bypassa el bloqueo de cambio de contrato (prd/phase_plan/decisions/tier/"
    "current_phase/dependencies): eso sigue exigiendo una aprobación nueva siempre.\n"
    "Ejemplo mínimo de invocación válida:\n"
    "  python3 .agent/scripts/state_manager.py mutate --action refresh_approval_receipt "
    "--payload '{\"module\": \"M1\", \"parent_fingerprint\": "
    "\"<fingerprint del APPROVE_RECEIPT vigente>\", \"derived_from\": [\"04_active_scope.md\"], "
    "\"evidence\": \"AC añadido, nada removido\"}'"
)


def _approval_contract_error(message: str) -> ValueError:
    return ValueError(f"{message}\n\n{APPROVAL_RECEIPT_CONTRACT}")


def _refresh_contract_error(message: str) -> ValueError:
    return ValueError(f"{message}\n\n{REFRESH_APPROVAL_RECEIPT_CONTRACT}")


# Listas ÚNICAS de manifests/lockfiles Python — las consume el gate de
# dependencias de este módulo y el WARN temprano del doctor
# (validate_dependency_lockfile_presence, TL FW-010). Si divergieran, el doctor
# avisaría de lockfiles que el gate no acepta (o callaría sobre los que exige).
PYTHON_MANIFESTS = ("requirements.txt", "pyproject.toml")
PYTHON_LOCKFILES = ("requirements.lock", "uv.lock", "poetry.lock")


def _receipt_excluded(rel: str) -> bool:
    """Predicado ÚNICO de exclusión del hash de diff, compartido por la rama
    git y el fallback rglob de `_tracked_product_paths` — si divergen, el hash
    difiere entre entornos y los receipts vuelven a romperse (TL FW-008 /
    PA FW-047 / Bot FW-006: `_project/.state.lock` existe con timestamp
    variable solo DURANTE una mutación, así que si entra al hash, el inicio y
    el cierre de un review nunca pueden coincidir).

    4.11.2 (evidencia de campo PA): exclusión por RUTA EXACTA, no por sufijo —
    el lock del motor vive siempre en `_project/.state.lock`; un archivo de
    PRODUCTO llamado `.state.lock` en otro directorio (p. ej. un fixture) debe
    seguir dentro del hash de integridad del second review. La regresión local
    de Research App defendía exactamente esta semántica.

    FW-185: `_project/approval_snapshots/` es el store content-addressed del
    motor — se escribe al registrar un APPROVE_RECEIPT. Sin excluirlo, aprobar
    durante un cierre cambia el hash del workspace e invalida el execution
    receipt vigente (mismo efecto que el manifiesto de transacción en FW-174).
    Exclusión por PREFIJO DE DIRECTORIO, no ruta exacta, porque los nombres de
    fichero son digests."""
    return (
        rel in RECEIPT_EXCLUDED_PATHS
        or rel == "_project/.state.lock"
        or rel.startswith(f"_project/{APPROVAL_SNAPSHOT_DIR}/")
    )


def _stable_hash(value: object) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


# ADR-139/FW-201: las huellas de línea del derived snapshot (ver
# approval_derived_snapshot más abajo) solo necesitan servir de comparación de
# igualdad/aditividad — no son un compromiso criptográfico como el `fingerprint`
# del recibo. 64 hex por línea, en un proyecto con miles de líneas sustantivas
# y cientos de ciclos de aprobación, es lo que infló state_log.jsonl a 62 MB y
# approval_snapshots/ a 40 MB (FW-168/FW-201). 16 hex (64 bits) deja la
# probabilidad de colisión en ~10⁻⁹ por snapshot — irrelevante frente al coste
# real (una colisión solo debilitaría UN chequeo de aditividad, que de todos
# modos exige `additive_rationale` narrado si no puede probarse aditivo.
#
# NUNCA usar este truncado en approval_basis()/approval_fingerprint() (el hash
# agregado que compone el `fingerprint` del recibo vigente) ni en
# store_derived_snapshot()/load_derived_snapshot() (el digest del propio
# sidecar, calculado SOBRE el snapshot ya truncado): esos siguen SHA-256
# completo — truncarlos invalidaría/rompería recibos ya aprobados.
_SNAPSHOT_FINGERPRINT_LEN = 16


def _truncated_hash(value: object, *, length: int = _SNAPSHOT_FINGERPRINT_LEN) -> str:
    """Huella de línea truncada para el derived snapshot. Ver nota en
    _SNAPSHOT_FINGERPRINT_LEN sobre dónde SÍ y dónde NUNCA usar esto."""
    return _stable_hash(value)[:length]


def _normalize_snapshot_format(snapshot: dict | None) -> dict | None:
    """Normaliza un derived snapshot legacy (v1, huellas de 64 hex, sin
    `"fmt"`) al formato v2 (16 hex) truncando sus huellas por slicing —
    `_truncated_hash(x) == _stable_hash(x)[:16]`, y una huella v1 YA es
    `_stable_hash(x)` completo, así que truncar reproduce EXACTAMENTE lo que
    `_line_fingerprints` habría producido si el receipt se hubiera creado con
    v2 desde el principio. Solo normaliza el valor en memoria para poder
    COMPARAR — no re-persiste ni toca el sidecar ni su digest.

    Sin esto, `_is_additive_snapshot` compara huellas v1 (64 hex) contra el
    `current_snapshot` siempre-v2 (16 hex) de `refresh_derived_approval` por
    igualdad de string: nunca coinciden por longitud, así que cualquier
    recibo de aprobación previo a ADR-139 rompía la detección de aditividad
    (todo se reportaba como cambio no-aditivo, exigiendo nueva aprobación
    explícita sin cambios reales) hasta correr
    `shrink_state_log_snapshots.py --write`."""
    if snapshot is None or snapshot.get("fmt") == 2:
        return snapshot
    normalized: dict = {"fmt": 2}
    for key in ("active_scope", "acceptance", "data_model", "api_contract", "contracts", "decisions"):
        values = snapshot.get(key, [])
        normalized[key] = (
            [h[:_SNAPSHOT_FINGERPRINT_LEN] if isinstance(h, str) else h for h in values]
            if isinstance(values, list) else values
        )
    module_spec = snapshot.get("module_spec", "")
    normalized["module_spec"] = (
        module_spec[:_SNAPSHOT_FINGERPRINT_LEN] if isinstance(module_spec, str) else module_spec
    )
    return normalized


def _file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes() if path.exists() else b"<MISSING>").hexdigest()


def _file_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="ignore") if path.exists() else "<MISSING>"


_INLINE_EVIDENCE_RE = re.compile(r"\s*\((?:evidencia|evidence)\s*:[^)\n]*\)", re.IGNORECASE)


def _normalize_progress_markdown(text: str, *, strip_inline_evidence: bool = False) -> str:
    """Quita progreso operativo sin borrar el contenido que fue aprobado.

    ``strip_inline_evidence`` (FW-298) solo se usa para tolerar la evidencia
    anotada como ``(evidencia: …)``; la huella sellada no cambia: un recibo
    compara primero con la normalización de siempre."""
    normalized: list[str] = []
    for raw in text.splitlines():
        line = re.sub(r"^(\s*[-*]\s*)\[[xX ]\]", r"\1[ ]", raw.rstrip())
        if strip_inline_evidence:
            line = _INLINE_EVIDENCE_RE.sub("", line)
        if re.match(r"^\s*Estado\s*:", line, flags=re.IGNORECASE):
            continue
        line = re.split(r"\s+[—-]\s*evidencia\s*:", line, maxsplit=1, flags=re.IGNORECASE)[0]
        normalized.append(line.rstrip())
    return "\n".join(normalized).strip() + "\n"


_H1_OR_H2_RE = re.compile(r"^#{1,2}\s+\S")


def _registered_modules(state: dict) -> list[str]:
    modules_raw = state.get("modules")
    if not isinstance(modules_raw, dict):
        return []
    result: list[str] = []
    seen: set[str] = set()
    for val in modules_raw.values():
        if isinstance(val, str):
            if val and val not in seen:
                seen.add(val)
                result.append(val)
        elif isinstance(val, (list, tuple, set)):
            for item in val:
                if isinstance(item, str) and item and item not in seen:
                    seen.add(item)
                    result.append(item)
    return result


# Cuarta ronda de L7 (L6b): una cabecera cuyo título lleva HTML, una entidad, un
# carácter de formato o combinante, o una letra o cifra no latina no deja decidir
# su dueño: lo que se ve no es lo que se lee (`_foreign_section_headers`).
_HEADING_MARKUP_RE = re.compile(r"<[A-Za-z/!?]|&(?:#[0-9]+|#[xX][0-9A-Fa-f]+|[A-Za-z][A-Za-z0-9]*);")
_LATIN1_LETTER_RE = re.compile("[ªºÀ-ÖØ-öø-ÿ]")


def _heading_doubt(title: str) -> str | None:
    """Motivo por el que el título de una cabecera no deja decidir su dueño: lo que
    se ve no es lo que se lee. Las tildes, la ñ y la ü (también en NFD) no lo son."""
    if _HEADING_MARKUP_RE.search(title):
        return "HTML o entidad"
    for char in unicodedata.normalize("NFC", title):
        category = unicodedata.category(char)
        if category == "Cf" or category.startswith("M"):
            return f"carácter invisible o combinante (U+{ord(char):04X})"
        if char.isalnum() and not char.isascii() and not _LATIN1_LETTER_RE.match(char):
            return f"letra o cifra no latina (U+{ord(char):04X})"
    return None


# Lo que `str.splitlines()` toma por fin de línea y Markdown no (revisión del
# corte de L7): `…\x1e## M3` se ve como un párrafo, no como una cabecera.
_NON_MARKDOWN_LINE_BREAK_RE = re.compile("[\x0b\x0c\x1c\x1d\x1e\x85\u2028\u2029]")


def _foreign_section_headers(text: str, module: str, registered) -> list[str]:
    """Las cabeceras de L6b que son sección de otro módulo, sin las que no dejan
    decidir su dueño (`_heading_doubt`): `## Módulo M\u200b1 — Pagos (M2)` se lee
    como compartida y cuenta (cuarta ronda de L7). Si el texto tiene un salto de
    línea que Markdown no reconoce, no se aparta nada: todo cuenta."""
    if _NON_MARKDOWN_LINE_BREAK_RE.search(text):
        return []
    return [
        header for header in _va.foreign_section_headers(text, module, registered)
        if _heading_doubt(_va.H2_RE.match(header).group("title")) is None
    ]


def _compute_foreign_sections(root: Path, state: dict, module: str) -> dict[str, list[str]]:
    registered = _registered_modules(state)
    project = root / "_project"
    scope_text = _file_text(project / "04_active_scope.md")
    acc_text = _file_text(project / "08_module_acceptance.md")
    return {
        "active_scope": _foreign_section_headers(scope_text, module, registered),
        "acceptance": _foreign_section_headers(acc_text, module, registered),
    }


def _effective_foreign_sections(
    root: Path, state: dict, module: str, receipt: dict,
) -> dict[str, list[str]] | None:
    """Cabeceras que la base de un recibo sellado descuenta al validarlo.

    5.3.3 (L7.3, TL-FW-327): las selladas como ajenas y, además, las secciones de
    otro módulo registrado que no contaban al sellar —su cabecera no está en el
    snapshot del recibo—: la sección que otro módulo añade, cambia o quita después
    del sello no toca la base. Lo que contó al sellar sigue contando (preámbulo,
    bloques comunes, la sección de un módulo entonces sin registrar: contrato de
    L6b), y mover una línea propia bajo una cabecera ajena nueva la saca de la base,
    así que caduca. Sin snapshot legible, solo las selladas, como antes. La lista
    sellada sigue siendo la de la huella: esto solo cambia lo que se lee.
    """
    raw = receipt.get("foreign_sections")
    sealed = raw if receipt.get("basis_version", 1) >= 3 and isinstance(raw, dict) else None
    snapshot = _normalize_snapshot_format(load_derived_snapshot(root, receipt))
    if not isinstance(snapshot, dict):
        return sealed
    project = root / "_project"
    registered: list[str] | None = None
    result: dict[str, list[str]] = {}
    for dim, name in (("active_scope", "04_active_scope.md"), ("acceptance", "08_module_acceptance.md")):
        sealed_dim = sealed.get(dim) if isinstance(sealed, dict) else None
        kept = [h for h in sealed_dim if isinstance(h, str)] if isinstance(sealed_dim, list) else []
        counted = snapshot.get(dim)
        text = _file_text(project / name)
        if not isinstance(counted, list) or not text:
            result[dim] = kept
            continue
        counted_set = set(counted)
        candidates = []
        for line in text.splitlines():
            if not _va.H2_RE.match(line) or line in kept:
                continue
            fps = _line_fingerprints(_normalize_progress_markdown(line))
            if len(fps) == 1 and fps[0] not in counted_set:
                candidates.append(line)
        if candidates:
            if registered is None:
                registered = _registered_modules(state)
            # Solo los ids que aparecen en una candidata: el resultado para esas
            # cabeceras es el mismo y no se recorre el fichero por cada módulo.
            ids = [m for m in registered if any(m in line for line in candidates)]
            foreign_now = set(_foreign_section_headers(text, module, ids))
            kept = kept + [h for h in dict.fromkeys(candidates) if h in foreign_now]
        result[dim] = kept
    return result


def _strip_foreign_sections(text: str, foreign_headers: list[str] | None) -> str:
    """Quita de la base las secciones ajenas: desde su cabecera hasta el próximo H1 o H2.

    Tercera revisión de L7 (hallazgo 5, hueco de L6b): el límite es el de CommonMark,
    contado por exceso: una ATX `#`/`##` con hasta 3 espacios delante y el subrayado
    `=`/`-` que sigue a una línea de texto (setext, o un separador tras un ítem: falla
    cerrado). El texto de la setext es la cabecera de lo que sigue y también cuenta.
    Un `---` tras una línea en blanco es un separador y no cierra la sección."""
    if not foreign_headers:
        return text
    foreign_set = {h for h in foreign_headers if isinstance(h, str)}
    if not foreign_set:
        return text
    kept: list[str] = []
    skipping = False
    paragraph: list[str] = []  # las líneas de texto seguidas que se han apartado
    for raw in text.splitlines(keepends=True):
        line_no_nl = raw.rstrip("\r\n")
        if line_no_nl in foreign_set:
            skipping, paragraph = True, []
            continue
        if skipping and _MD_BOUNDARY_RE.match(line_no_nl):
            if line_no_nl.lstrip(" ").startswith("#"):
                skipping = False
            elif paragraph:  # subrayado tras texto
                skipping = False
                kept.extend(paragraph)
        if not skipping:
            kept.append(raw)
        else:
            paragraph = paragraph + [raw] if line_no_nl.strip() else []
    return "".join(kept)


# L4.2 (5.3.1, D1-A; TL-FW-318): lo que el plan aprobado declara que va a tocar
# fuera de su scope y sus ACs. El encabezado declarado sigue en la base (renombrarlo,
# quitarlo o repetirlo caduca). En la PRD, dimensión crítica, el cuerpo también, con
# la normalización de progreso del scope (casillas, `Estado:`, `— evidencia:`); en
# el modelo, los contratos y la API sale de la base, y su digest sellado avisa si
# cambia (revisión `security` de L4, hallazgo 1).
_COVERS_DOCS = {
    "prd": "01_product_requirements.md", "data_model": "02_technical_plan/data_model.md",
    "contracts": "03_contracts.md", "api_contract": "02_technical_plan/api_contract.yaml",
}
_COVERS_FIXED = {"decisions:aditivas", "module_spec:clasificar"}
# Límite de sección como en CommonMark: ATX con hasta 3 espacios y subrayado setext.
_MD_BOUNDARY_RE = re.compile(r"^ {0,3}(#{1,2}(\s|$)|=+\s*$|-+\s*$)")


def _covered_line(dim: str, selector: str, line: str) -> bool:
    if dim == "api_contract":
        return selector.startswith("/") and line.strip() in {f"{selector}:", f"'{selector}':", f'"{selector}":'}
    return bool(_H1_OR_H2_RE.match(selector)) and line.rstrip() == selector


def _split_covered(text: str, dim: str, selectors: list[str]) -> tuple[str, dict[str, str]]:
    """(texto para la base, cuerpo de cada sección declarada)."""
    kept: list[str] = []
    bodies: dict[str, str] = {}
    current: str | None = None
    indent = 0
    for raw in text.splitlines(keepends=True) + [""]:
        line = raw.rstrip("\r\n")
        if current is not None:
            if dim == "api_contract":
                inside = raw != "" and (not line.strip() or len(line) - len(line.lstrip()) > indent)
            else:
                inside = raw != "" and not _MD_BOUNDARY_RE.match(line)
            if inside:
                bodies[current] += raw
                continue
            if dim == "prd":
                kept.append(_normalize_progress_markdown(bodies[current]))
        current = next((s for s in selectors if _covered_line(dim, s, line)), None)
        if current is not None:
            bodies[current], indent = "", len(line) - len(line.lstrip())
        kept.append(raw)
    return "".join(kept), bodies


def _outside_fences(lines: list[str]):
    fenced = False
    for line in lines:
        if re.match(r"^ {0,3}(```|~~~)", line):
            fenced = not fenced
        elif not fenced:
            yield line


def _covers_by_dim(covers: list[str] | None) -> dict[str, list[str]]:
    by_dim: dict[str, list[str]] = {}
    for item in covers or []:
        dim, _, selector = item.partition(":")
        if dim in _COVERS_DOCS:
            by_dim.setdefault(dim, []).append(selector)
    return by_dim


def _covered_text(root: Path, dim: str, covered: dict[str, list[str]]) -> str:
    text = _file_text(root / "_project" / _COVERS_DOCS[dim])
    return _split_covered(text, dim, covered[dim])[0] if dim in covered else text


def _covered_digests(root: Path, covers: list[str]) -> dict[str, str]:
    """Digest del cuerpo de cada sección declarada: se sella y avisa si cambia."""
    digests: dict[str, str] = {}
    for dim, selectors in _covers_by_dim(covers).items():
        bodies = _split_covered(_file_text(root / "_project" / _COVERS_DOCS[dim]), dim, selectors)[1]
        digests.update({f"{dim}:{sel}": _truncated_hash(bodies.get(sel)) for sel in selectors})
    return digests


def _clean_covers_changes(root: Path, raw: object) -> list[str]:
    if raw is None:
        return []
    if not isinstance(raw, list) or not all(isinstance(x, str) for x in raw):
        raise _approval_contract_error("APPROVE_RECEIPT covers_changes debe ser una lista de strings 'dimensión:selector'")
    clean: set[str] = set()
    for item in raw:
        dim, _, selector = item.partition(":")
        dim, selector = dim.strip(), selector.strip()
        item = f"{dim}:{selector}"
        lines = _file_text(root / "_project" / _COVERS_DOCS.get(dim, "-")).splitlines()
        hits = [line for line in lines if dim in _COVERS_DOCS and _covered_line(dim, selector, line)]
        outside = [line for line in _outside_fences(lines) if line in hits]
        if item not in _COVERS_FIXED and not (len(hits) == 1 and len(outside) == 1):
            raise _approval_contract_error(
                f"APPROVE_RECEIPT covers_changes {item!r} no es declarable: la dimensión no admite deltas "
                "o el encabezado (H1 o H2) o la ruta no existe una sola vez fuera de un bloque de código"
            )
        clean.add(item)
    for dim, selectors in _covers_by_dim(sorted(clean)).items():
        kept = _split_covered(_file_text(root / "_project" / _COVERS_DOCS[dim]), dim, selectors)[0]
        if dim != "prd" and not any(
            line.strip() and not _MD_BOUNDARY_RE.match(line) and not any(_covered_line(dim, s, line) for s in selectors)
            for line in kept.splitlines()
        ):
            raise _approval_contract_error(f"APPROVE_RECEIPT covers_changes cubriría todo {_COVERS_DOCS[dim]}: declara secciones, no el documento")
    return sorted(clean)


def _receipt_covers(receipt: dict) -> list[str]:
    raw = receipt.get("covers_changes")
    valid = isinstance(raw, list) and all(isinstance(x, str) for x in raw)
    return list(raw) if valid and receipt.get("basis_version", 1) >= 3 else []


def _normalized_progress_text(text: str, *, basis_version: int = 2, strip_inline_evidence: bool = False) -> str:
    normalized = _normalize_progress_markdown(text, strip_inline_evidence=strip_inline_evidence)
    if basis_version >= 2:
        return re.sub(r"\n{2,}", "\n", normalized)
    return normalized


def _normalized_progress_hash(
    path: Path,
    *,
    basis_version: int = 2,
    strip_inline_evidence: bool = False,
    foreign_headers: list[str] | None = None,
) -> str:
    raw_text = _strip_foreign_sections(_file_text(path), foreign_headers)
    norm_text = _normalized_progress_text(
        raw_text, basis_version=basis_version, strip_inline_evidence=strip_inline_evidence
    )
    return hashlib.sha256(norm_text.encode("utf-8")).hexdigest()


def spec_applicability(state: dict, module: str) -> dict:
    """Lo aprobado de las specs sin su texto: ``n/a`` frente a «aplica» (required o done).

    FW-298 (TL FW-262/FW-288): pasar una spec de ``required`` a ``done`` y
    reescribir el texto de su exención al cerrar es lo que el propio plan
    prevé; cambiar ``n/a`` ↔ ``required`` sí cambia lo aprobado."""
    entry = state.get("module_specs", {}).get(module, {})
    return {
        key: ("applies" if entry.get(key) in {"required", "done"} else entry.get(key))
        for key in ("data_model", "api_contract")
    }


# Dimensiones de la base cuyo cambio es una transición prevista por el plan.
_PLANNED_PROGRESS_DIMS = ("acceptance", "active_scope")


def _specs_as_planned(receipt: dict, state: dict, module: str) -> bool:
    """FW-298: ``required`` → ``done`` no cambia lo aprobado. L4.2: con
    ``module_spec:clasificar``, clasificar lo que se selló sin clasificar tampoco."""
    stored = receipt.get("spec_applicability")
    current = spec_applicability(state, module)
    return isinstance(stored, dict) and (stored == current or (
        "module_spec:clasificar" in _receipt_covers(receipt)
        and all(stored.get(key) in (None, value) for key, value in current.items())
    ))


def _declared_additive_adrs(root: Path, state: dict, module: str, receipt: dict) -> list[str]:
    """L4.2: ADRs nuevas declaradas con ``decisions:aditivas``, con las reglas del refresco
    (las aprobadas siguen intactas y ``documents_decision`` es válido); [] si no lo son."""
    approved = _normalize_snapshot_format(load_derived_snapshot(root, receipt))
    if not _decisions_change_is_additive(approved, {"decisions": _decision_fingerprints(state, module)}):
        return []
    added = _added_adr_ids(state, module, approved)
    known = {d.get("id"): d for d in state.get("_decisions", []) if isinstance(d, dict)}
    if not added or not all(added) or any(_documents_decision(known, adr, module)[1] for adr in added):
        return []
    return added


def _planned_transition_dims(
    root: Path, state: dict, module: str, receipt: dict, changed_dims: list[str], basis_version: int,
    notes: list[str] | None = None, *, foreign_sections: dict | None = None,
) -> list[str]:
    """Dimensiones que cambiaron solo por pasos previstos: specs a ``done``, evidencia anotada
    y lo declarado en ``covers_changes`` (L4.2)."""
    planned: list[str] = []
    stored_basis = receipt.get("basis") if isinstance(receipt.get("basis"), dict) else {}
    rec_fs = foreign_sections if foreign_sections is not None else _effective_foreign_sections(
        root, state, module, receipt
    )
    project = root / "_project"
    for dim in changed_dims:
        if dim == "module_spec":
            if _specs_as_planned(receipt, state, module):
                planned.append(dim)
        elif dim == "decisions" and "decisions:aditivas" in _receipt_covers(receipt):
            added = _declared_additive_adrs(root, state, module, receipt)
            if added:
                planned.append(dim)
                if notes is not None:
                    notes.append(f"ADR aditiva declarada en covers_changes: {', '.join(added)}")
        elif dim in _PLANNED_PROGRESS_DIMS:
            name = "04_active_scope.md" if dim == "active_scope" else "08_module_acceptance.md"
            headers = rec_fs.get(dim) if isinstance(rec_fs, dict) else None
            alternative = _normalized_progress_hash(
                project / name,
                basis_version=basis_version,
                strip_inline_evidence=True,
                foreign_headers=headers if isinstance(headers, list) else None,
            )
            if alternative == stored_basis.get(dim):
                planned.append(dim)
    return planned


def _module_spec_basis(state: dict, module: str) -> dict:
    entry = state.get("module_specs", {}).get(module, {})

    def applicability(value: object) -> object:
        return "applies" if value in {"required", "done"} else value

    return {
        "data_model": applicability(entry.get("data_model")),
        "api_contract": applicability(entry.get("api_contract")),
        "justification": entry.get("justification") if (
            entry.get("data_model") == "n/a" or entry.get("api_contract") == "n/a"
        ) else "",
    }


def _line_fingerprints(text: str) -> list[str]:
    return [_truncated_hash(line) for line in _substantive_lines(text)]


def approval_derived_snapshot(
    root: Path,
    state: dict,
    module: str,
    *,
    basis_version: int = 3,
    foreign_sections: dict[str, list[str]] | None = None,
    covers_changes: list[str] | None = None,
) -> dict[str, object]:
    """Snapshot sin contenido: solo hashes ordenados de líneas sustantivas.

    Permite probar que una derivación conserva lo aprobado sin duplicar scope,
    contratos o posibles datos sensibles dentro del estado y su bitácora.

    ADR-139/FW-201: formato v2 — huellas truncadas a 16 hex (ver
    _SNAPSHOT_FINGERPRINT_LEN). `"fmt": 2` marca el formato para detección de
    idempotencia en shrink_state_log_snapshots.py y para distinguir de
    snapshots v1 legacy (huellas de 64 hex, sin esta clave).
    """
    project = root / "_project"
    scope_headers = (
        foreign_sections.get("active_scope")
        if isinstance(foreign_sections, dict) and isinstance(foreign_sections.get("active_scope"), list)
        else None
    )
    acc_headers = (
        foreign_sections.get("acceptance")
        if isinstance(foreign_sections, dict) and isinstance(foreign_sections.get("acceptance"), list)
        else None
    )
    scope_text = _strip_foreign_sections(_file_text(project / "04_active_scope.md"), scope_headers)
    acc_text = _strip_foreign_sections(_file_text(project / "08_module_acceptance.md"), acc_headers)
    covered = _covers_by_dim(covers_changes) if basis_version >= 3 else {}
    return {
        "fmt": 2,
        "active_scope": _line_fingerprints(
            _normalize_progress_markdown(scope_text)
        ),
        "acceptance": _line_fingerprints(
            _normalize_progress_markdown(acc_text)
        ),
        **{dim: _line_fingerprints(_covered_text(root, dim, covered))
           for dim in ("data_model", "api_contract", "contracts")},
        # FW-124: huella POR ADR (no un hash agregado) para poder distinguir
        # "se añadió una ADR nueva" de "se alteró o se quitó una ya aprobada".
        # El agregado de `basis["decisions"]` no permite esa distinción.
        "decisions": _decision_fingerprints(state, module),
        "module_spec": _truncated_hash(_module_spec_basis(state, module)),
    }


def _decision_fingerprints(state: dict, module: str) -> list[str]:
    return [
        _truncated_hash(d) for d in state.get("_decisions", [])
        if isinstance(d, dict) and _adr_applies_to_module(d.get("module_id"), module)
    ]


def _added_adr_ids(state: dict, module: str, approved_snapshot: dict) -> list[str]:
    aprobadas = set(approved_snapshot.get("decisions", []))
    return [
        str(d.get("id") or "") for d in state.get("_decisions", [])
        if isinstance(d, dict) and _adr_applies_to_module(d.get("module_id"), module)
        and _truncated_hash(d) not in aprobadas
    ]


def _documents_decision(known: dict, adr_id: str, module: str) -> tuple[object, str | None]:
    """(ADR citada, problema) del ``documents_decision`` de una ADR añadida (FW-124)."""
    doc_val = known.get(adr_id, {}).get("documents_decision")
    if doc_val is None or str(doc_val).strip() == "":
        return None, None
    cited = str(doc_val).strip() if isinstance(doc_val, str) else doc_val
    if not isinstance(doc_val, str) or cited not in known:
        return cited, f" referencia documents_decision inexistente: {cited}"
    if cited == adr_id:
        return cited, f": la ADR '{adr_id}' no puede citarse a sí misma en documents_decision ({cited})"
    if not _adr_applies_to_module(known[cited].get("module_id"), module):
        return cited, f" referencia documents_decision de otro módulo: {cited}"
    return cited, None


# --- FW-185: store content-addressed del snapshot derivado -----------------
#
# El cuerpo de `approval_derived_snapshot` (listas de huellas línea-a-línea de 6
# documentos: ~400 KB por receipt en un proyecto maduro, y subiendo) vivía
# incrustado en el receipt — y por tanto en `03_project_state.json` y en CADA
# evento `record_approval_receipt`/`refresh_approval_receipt` de
# `state_log.jsonl`, que en un proyecto real con muchos ciclos de aprobación
# infla el estado y la bitácora hasta disparar el aviso GH001 de GitHub (medido
# en Example Lab: 12 MB de estado, 61 MB de log). Nadie lee ese cuerpo salvo
# el chequeo de aditividad de `refresh_derived_approval`, y ese solo mira el
# receipt vigente.
#
# Se mueve a un store content-addressed: el receipt guarda `derived_snapshot_ref`
# (= el digest del snapshot). La cadena de hashes del log sigue sellando esa
# referencia, así que editar un sidecar es detectable (su contenido deja de
# cuadrar con el digest — `load_derived_snapshot` lo verifica y degrada a
# aprobación estricta).
APPROVAL_SNAPSHOT_DIR = "approval_snapshots"


def _approval_snapshot_store(root: Path) -> Path:
    return root / "_project" / APPROVAL_SNAPSHOT_DIR


def store_derived_snapshot(root: Path, snapshot: dict) -> str:
    """Persiste `snapshot` en el store content-addressed y devuelve su digest.
    FW-357 (L4.3): guarda también los mapas de identidades de test.

    Idempotente: dos receipts con snapshot idéntico comparten fichero. El
    fichero es inmutable una vez escrito (escritura atómica vía tmp + replace).
    """
    digest = _stable_hash(snapshot)
    store = _approval_snapshot_store(root)
    store.mkdir(parents=True, exist_ok=True)
    target = store / f"{digest}.json"
    if _load_blob(root, digest) is None:  # ausente, o plantado o corrupto (revisión de L4)
        payload = json.dumps(
            snapshot, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
        tmp = store / f".{digest}.json.tmp"
        tmp.write_text(payload, encoding="utf-8")
        tmp.replace(target)
    return digest


def load_derived_snapshot(root: Path, receipt: dict) -> dict | None:
    """Recupera el snapshot de un receipt: inline (legacy) o vía sidecar.

    Devuelve None si el receipt no tiene snapshot derivable, si el sidecar
    referenciado falta, o si su contenido no cuadra con el digest (sidecar
    manipulado o corrupto). El llamador degrada entonces al comportamiento
    estricto de siempre: exigir aprobación explícita.
    """
    inline = receipt.get("derived_snapshot")
    if isinstance(inline, dict):
        return inline
    return _load_blob(root, receipt.get("derived_snapshot_ref"))


def _load_blob(root: Path, ref: object) -> dict | None:
    """Un fichero del almacén por su digest; None si falta o no cuadra con él."""
    if not isinstance(ref, str) or not re.fullmatch(r"[0-9a-f]{64}", ref):
        return None
    target = _approval_snapshot_store(root) / f"{ref}.json"
    if not target.exists():
        return None
    try:
        blob = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return blob if isinstance(blob, dict) and _stable_hash(blob) == ref else None


def _approval_identities(root: Path, approval: dict) -> dict | None:
    """Mapa de tests de la aprobación (N16: el de origen primero). FW-357 (L4.3):
    inline en los recibos anteriores, por referencia al almacén en los nuevos.
    None si no hay mapa o si una referencia no carga: el gate cae a git."""
    value = None
    for key in ("test_identities_origin", "test_identities"):
        value = approval.get(key)
        ref = approval.get(f"{key}_ref")
        if not isinstance(value, dict) and ref is not None:
            value = _load_blob(root, ref)
            if value is None:
                return None
        if value:
            return value if isinstance(value, dict) else None
    return value if isinstance(value, dict) else None


def _substantive_lines(text: str) -> list[str]:
    """Líneas que una derivación aditiva no puede reescribir ni eliminar."""
    result: list[str] = []
    for raw in text.splitlines():
        line = raw.rstrip()
        compact = line.strip().lower()
        if not compact:
            continue
        if "[completar]" in compact:
            continue
        if compact.endswith("{}") or compact.endswith("[]"):
            continue
        result.append(line)
    return result


def _is_additive_snapshot(previous: object, current: object) -> bool:
    """Cada fingerprint previo debe sobrevivir, en el mismo orden."""
    if not isinstance(previous, list) or not isinstance(current, list):
        return False
    old = previous
    new = current
    cursor = 0
    for line in new:
        if cursor < len(old) and line == old[cursor]:
            cursor += 1
    return cursor == len(old)


def _decisions_change_is_additive(previous_snapshot: object, current_snapshot: dict) -> bool:
    """True si TODAS las ADRs aprobadas siguen presentes e intactas.

    FW-124: añadir una ADR no cambia las decisiones sobre las que se aprobó;
    alterarlas o quitarlas, sí. Un recibo legacy sin la clave `decisions` en su
    snapshot no puede probar aditividad — degrada al comportamiento estricto
    de siempre (exigir aprobación explícita), nunca a permitir.
    """
    if not isinstance(previous_snapshot, dict):
        return False
    previous = previous_snapshot.get("decisions")
    current = current_snapshot.get("decisions")
    if not isinstance(previous, list) or not isinstance(current, list):
        return False
    return _is_additive_snapshot(previous, current)


def _decisions_change_reason(
    state: dict, module: str, previous: dict, previous_snapshot: object, current_snapshot: dict,
) -> str:
    """Por qué el cambio de `decisions` no es aditivo, en lenguaje accionable.

    FW-278 (TL FW-258): el rechazo decía solo «decisions» y el agente lo leyó
    como incoherencia del motor. Sustituir una ADR ya aprobada exige
    aprobación nueva A PROPÓSITO (ADR-056); aquí solo se nombra la causa."""
    if not isinstance(previous_snapshot, dict):
        ref = previous.get("derived_snapshot_ref") or "(sin referencia)"
        return (
            f"no se pudo leer el snapshot aprobado `_project/approval_snapshots/{ref}.json` "
            "(¿sin commitear, en otro worktree o manipulado?): sin él no se puede probar que "
            "las ADRs aprobadas siguen intactas"
        )
    before = previous_snapshot.get("decisions")
    after = current_snapshot.get("decisions")
    if not isinstance(before, list) or not isinstance(after, list):
        return "recibo sin huellas de ADRs (legado): no se puede probar aditividad"
    if not (set(before) - set(after)):
        return "cambió el orden de las ADRs aprobadas"
    superseded = [
        d.get("id") for d in state.get("_decisions", [])
        if isinstance(d, dict)
        and _adr_applies_to_module(d.get("module_id"), module)
        and (d.get("superseded_by") or d.get("status") == "superseded")
    ]
    if superseded:
        return (
            f"se sustituyó (supersede) una ADR ya aprobada ({', '.join(str(x) for x in superseded)}): "
            "eso exige aprobación nueva del builder a propósito (ADR-056), no es un fallo del motor"
        )
    return "se modificó o eliminó una ADR ya aprobada (ADR-056)"


def _phase_plan(root: Path, phase: str) -> Path:
    return root / "_project" / "02_technical_plan" / f"{phase}.md"


def approval_basis(
    root: Path,
    state: dict,
    module: str,
    *,
    basis_version: int | None = None,
    foreign_sections: dict[str, list[str]] | None = None,
    covers_changes: list[str] | None = None,
) -> dict[str, str]:
    if basis_version is None:
        receipt = state.get("workflow_receipts", {}).get("approvals", {}).get(module)
        if isinstance(receipt, dict):
            basis_version = receipt.get("basis_version", 1)
            if foreign_sections is None:
                foreign_sections = _effective_foreign_sections(root, state, module, receipt)
            if covers_changes is None:
                covers_changes = _receipt_covers(receipt)
        else:
            basis_version = 2
    covered = _covers_by_dim(covers_changes) if basis_version >= 3 else {}

    def doc_hash(dim: str) -> str:
        if dim not in covered:
            return _file_hash(root / "_project" / _COVERS_DOCS[dim])
        return hashlib.sha256(_covered_text(root, dim, covered).encode("utf-8")).hexdigest()

    project = root / "_project"
    tier_entry = state.get("work_tiers", {}).get(module, {})
    if basis_version >= 3:
        tier = {"tier": tier_entry.get("tier")}
    else:
        tier = {
            "tier": tier_entry.get("tier"),
            "rationale": tier_entry.get("rationale"),
        }
    scope_headers = (
        foreign_sections.get("active_scope")
        if isinstance(foreign_sections, dict) and isinstance(foreign_sections.get("active_scope"), list)
        else None
    )
    acc_headers = (
        foreign_sections.get("acceptance")
        if isinstance(foreign_sections, dict) and isinstance(foreign_sections.get("acceptance"), list)
        else None
    )
    active_scope_hash = _normalized_progress_hash(
        project / "04_active_scope.md", basis_version=basis_version, foreign_headers=scope_headers
    )
    acceptance_hash = _normalized_progress_hash(
        project / "08_module_acceptance.md", basis_version=basis_version, foreign_headers=acc_headers
    )
    decisions = [
        d for d in state.get("_decisions", [])
        if isinstance(d, dict) and _adr_applies_to_module(d.get("module_id"), module)
    ]
    phase = str(state.get("current_phase") or "")
    return {
        "prd": doc_hash("prd"),
        "phase_plan": _normalized_progress_hash(_phase_plan(root, phase), basis_version=basis_version),
        "active_scope": active_scope_hash,
        "acceptance": acceptance_hash,
        "data_model": doc_hash("data_model"),
        "api_contract": doc_hash("api_contract"),
        "contracts": doc_hash("contracts"),
        "decisions": _stable_hash(decisions),
        "tier": _stable_hash(tier),
        "module_spec": _stable_hash(_module_spec_basis(state, module)),
        "current_phase": _stable_hash(phase),
        "dependencies": _stable_hash(state.get("dependencies", [])),
    }


def approval_fingerprint(
    basis: dict[str, str], *, tier: str | None = None, approved_by: str = "",
    approval_evidence: str = "", risk_summary: str = "", adr_refs: list[str] | None = None,
    basis_version: int = 1,
    covers_dependencies: list[str] | None = None,
    approved_dependencies: list[str] | None = None,
    foreign_sections: dict[str, list[str]] | None = None,
    covers_changes: list[str] | None = None,
    covers_extra: dict | None = None,
) -> str:
    payload: dict[str, object] = {
        "basis": basis,
        "tier": tier,
        "approved_by": approved_by,
        "approval_evidence": approval_evidence,
        "risk_summary": risk_summary,
        "adr_refs": adr_refs or [],
    }
    if basis_version >= 3:
        payload["covers_dependencies"] = list(covers_dependencies or [])
        payload["approved_dependencies"] = list(approved_dependencies or [])
        if foreign_sections is not None:
            payload["foreign_sections"] = {
                "active_scope": list(foreign_sections.get("active_scope", [])),
                "acceptance": list(foreign_sections.get("acceptance", [])),
            }
        if covers_changes:  # L4.2: solo si se declaró algo; sin la clave, la huella de siempre
            payload["covers_changes"] = list(covers_changes)
            payload.update(covers_extra or {})  # digests de lo declarado y specs selladas
    return _stable_hash(payload)


def _receipts(state: dict) -> dict:
    root = state.setdefault("workflow_receipts", {})
    root.setdefault("approvals", {})
    root.setdefault("reviews", {})
    root.setdefault("executions", {})
    root.setdefault("checkpoints", {})
    return root


def _adr_applies_to_module(adr_module_id: object, module: str) -> bool:
    """TL FW-019 / BOTÓN FW-115/FW-113: un ADR aplica a `module` si su
    `module_id` es exactamente ese módulo, es "framework" (paraguas global,
    ya existía), o es una LISTA que incluye ese módulo (paraguas de
    iniciativa multi-módulo, nuevo — evita crear un ADR casi duplicado por
    módulo solo para que el receipt tier_2 acepte la cita)."""
    if adr_module_id == "framework":
        return True
    if isinstance(adr_module_id, str):
        return adr_module_id == module
    if isinstance(adr_module_id, (list, tuple, set)):
        return module in adr_module_id
    return False


def record_approval(
    state: dict,
    root: Path,
    module: str,
    *,
    approved_by: str,
    evidence: str,
    risk_summary: str,
    adr_refs: list[str],
    now_iso: str,
    covers_dependencies: list[str] | None = None,
    covers_changes: list[str] | None = None,
) -> dict:
    if approved_by != "builder":
        raise _approval_contract_error("APPROVE_RECEIPT solo puede registrar approved_by='builder'")
    if not evidence.strip() or not risk_summary.strip():
        raise _approval_contract_error("APPROVE_RECEIPT requiere evidence y risk_summary no vacíos")
    tier = state.get("work_tiers", {}).get(module, {}).get("tier")
    if tier == "tier_2" and not adr_refs:
        raise _approval_contract_error("APPROVE_RECEIPT tier_2 requiere adr_refs")
    known_adrs = {
        d.get("id"): d for d in state.get("_decisions", []) if isinstance(d, dict)
    }
    missing = sorted(set(adr_refs) - set(known_adrs))
    if missing:
        raise _approval_contract_error(f"APPROVE_RECEIPT referencia ADRs inexistentes: {missing}")
    irrelevant = sorted(
        ref for ref in adr_refs
        if not _adr_applies_to_module(known_adrs[ref].get("module_id"), module)
    )
    if irrelevant:
        raise _approval_contract_error(f"APPROVE_RECEIPT referencia ADRs de otro módulo: {irrelevant}")
    if covers_dependencies is not None and not isinstance(covers_dependencies, list):
        raise _approval_contract_error("APPROVE_RECEIPT covers_dependencies debe ser una lista de strings")
    covers_deps_clean = (
        sorted(str(d).strip() for d in covers_dependencies if str(d).strip())
        if covers_dependencies is not None else []
    )
    approved_deps_clean = list(state.get("dependencies", []))
    covers = _clean_covers_changes(root, covers_changes)
    foreign_sections = _compute_foreign_sections(root, state, module)
    basis = approval_basis(
        root, state, module, basis_version=3, foreign_sections=foreign_sections, covers_changes=covers
    )
    baseline = _test_baseline(root)
    # FW-357 (L4.3): el mapa se guarda una vez en el almacén y el recibo lo cita.
    identities = baseline.pop("test_identities", None)
    if isinstance(identities, dict):
        baseline["test_identities_ref"] = store_derived_snapshot(root, identities)
    # FW-277: commit de apertura del módulo. Base git preferente cuando el mapa
    # de identidades no es utilizable (el merge-base con main arrastra el
    # trabajo de otros módulos de la misma rama).
    baseline["head_at_approval"] = _git_out(root, "rev-parse", "HEAD")

    # N16: conservar el mapa ORIGINAL (test_identities_origin) si existe. Se
    # encadena: una tercera aprobación no debe sustituirlo por el segundo mapa.
    # Una referencia que no carga se arrastra tal cual: el gate caerá a git.
    previous = _receipts(state)["approvals"].get(module)
    approved_at_origin: str | None = None
    if isinstance(previous, dict):
        origin = _approval_identities(root, previous)
        origin_ref = previous.get("test_identities_origin_ref") or previous.get("test_identities_ref")
        if isinstance(origin, dict):
            origin_ref = store_derived_snapshot(root, origin)
        if isinstance(origin_ref, str) and origin_ref:
            baseline["test_identities_origin_ref"] = origin_ref
        digest = previous.get("prod_digest_origin") or previous.get("prod_digest")
        if isinstance(digest, str):
            baseline["prod_digest_origin"] = digest
        head = previous.get("head_at_approval_origin") or previous.get("head_at_approval")
        if isinstance(head, str) and head:
            baseline["head_at_approval_origin"] = head
        prev_approved_at = previous.get("approved_at_origin") or previous.get("approved_at")
        if isinstance(prev_approved_at, str) and prev_approved_at:
            approved_at_origin = prev_approved_at

    receipt = {
        "status": "approved",
        "kind": "explicit",
        "module": module,
        "tier": tier,
        "approved_by": approved_by,
        "approval_evidence": evidence.strip(),
        "risk_summary": risk_summary.strip(),
        "adr_refs": list(adr_refs),
        "covers_dependencies": covers_deps_clean,
        "approved_dependencies": approved_deps_clean,
        "foreign_sections": foreign_sections,
        "basis": basis,
        "basis_version": 3,
        "spec_applicability": spec_applicability(state, module),
        **baseline,
        "derived_snapshot_ref": store_derived_snapshot(
            root, approval_derived_snapshot(
                root, state, module, foreign_sections=foreign_sections, covers_changes=covers
            )
        ),
        "approved_at": now_iso,
    }
    if covers:
        receipt.update(covers_changes=covers, covers_digests=_covered_digests(root, covers))
    receipt["fingerprint"] = _sealed_fingerprint(receipt, basis, 3, foreign_sections, covers)
    if approved_at_origin is not None:
        receipt["approved_at_origin"] = approved_at_origin
    _receipts(state)["approvals"][module] = receipt
    return receipt


def refresh_derived_approval(
    state: dict,
    root: Path,
    module: str,
    *,
    parent_fingerprint: str,
    derived_from: list[str],
    evidence: str,
    now_iso: str,
    additive_rationale: str | None = None,
) -> dict:
    previous = _receipts(state)["approvals"].get(module)
    if not isinstance(previous, dict) or previous.get("fingerprint") != parent_fingerprint:
        raise _refresh_contract_error(
            f"parent_fingerprint no coincide con APPROVE_RECEIPT vigente; consulta el vigente con lookup_state kind=receipts module_id={module}"
        )
    if not derived_from or not all(isinstance(x, str) and x.strip() for x in derived_from):
        raise _refresh_contract_error("refresh derivado requiere derived_from trazable")
    if not evidence.strip():
        raise _refresh_contract_error("refresh derivado requiere evidence")
    prev_version = previous.get("basis_version", 1)
    prev_foreign_sections = (
        previous.get("foreign_sections")
        if isinstance(previous.get("foreign_sections"), dict)
        else None
    )
    covers = _receipt_covers(previous)
    if covers and previous.get("fingerprint") != _sealed_fingerprint(
        previous, previous.get("basis") or {}, prev_version, prev_foreign_sections, covers
    ):
        raise _refresh_contract_error(
            "covers_changes no es el que se selló con el recibo vigente; requiere nueva aprobación del builder"
        )
    # 5.3.3 (L7.3): se compara con lo que se descuenta al leer; la huella, con la sellada.
    prev_read_foreign = _effective_foreign_sections(root, state, module, previous)
    comparable = approval_basis(
        root, state, module, basis_version=prev_version, foreign_sections=prev_read_foreign,
        covers_changes=covers,
    )
    # FW-185: el snapshot del receipt vigente puede estar inline (legacy) o en
    # el store content-addressed. None → sidecar ausente/manipulado → el chequeo
    # de `not isinstance(previous_snapshot, dict)` de abajo degrada a estricto.
    # ADR-139 (fix): `load_derived_snapshot` devuelve el contenido RAW tal cual
    # está persistido (necesario para verificar su digest) — puede seguir en
    # formato v1 (64 hex) si el receipt es previo a este hotfix. Normalizar
    # aquí, antes de comparar contra `current_snapshot` (siempre v2).
    previous_snapshot = _normalize_snapshot_format(load_derived_snapshot(root, previous))
    comparable_snapshot = approval_derived_snapshot(
        root, state, module, basis_version=prev_version, foreign_sections=prev_read_foreign,
        covers_changes=covers,
    )
    new_foreign_sections = _compute_foreign_sections(root, state, module)
    current_snapshot = approval_derived_snapshot(
        root, state, module, basis_version=3, foreign_sections=new_foreign_sections, covers_changes=covers
    )
    added_decisions: list[str] = []
    changed_critical: list[str] = []
    for key in sorted(APPROVAL_CRITICAL_KEYS):
        if comparable.get(key) == previous.get("basis", {}).get(key):
            continue
        # FW-124: `decisions` es la única clave crítica cuyo cambio puede
        # probarse aditivo con evidencia (huella por ADR en el snapshot).
        if key == "decisions" and _decisions_change_is_additive(
            previous_snapshot, comparable_snapshot
        ):
            aprobadas = set(previous_snapshot.get("decisions", []))
            added_decisions = [
                d["id"]
                for d in state.get("_decisions", [])
                if isinstance(d, dict)
                and _adr_applies_to_module(d.get("module_id"), module)
                # ADR-139: `aprobadas` viene del derived snapshot (huellas
                # truncadas a 16 hex desde ADR-139) — comparar con el hash
                # completo de 64 hex nunca haría match y trataría cualquier
                # ADR ya aprobada como recién añadida en cada refresh.
                and _truncated_hash(d) not in aprobadas
            ]
            continue
        # FW-337 (L6): dependencia ya cubierta en covers_dependencies se tolera en refresh
        if key == "dependencies":
            raw_appr = previous.get("approved_dependencies") or []
            raw_curr = state.get("dependencies", [])
            raw_cov = previous.get("covers_dependencies", [])
            appr = {str(d) for d in raw_appr if d is not None}
            curr = {str(d) for d in raw_curr if d is not None}
            cov = {str(d) for d in raw_cov if d is not None}
            added = curr - appr
            if appr.issubset(curr) and added and added.issubset(cov):
                continue
        changed_critical.append(key)
    if changed_critical:
        detail = ""
        if "decisions" in changed_critical:
            detail = " — decisions: " + _decisions_change_reason(
                state, module, previous, previous_snapshot, comparable_snapshot,
            )
        raise _refresh_contract_error(
            "base aprobada cambió; se requiere nueva aprobación explícita: "
            + ", ".join(changed_critical) + detail
        )
    changed = sorted(key for key in APPROVAL_DERIVED_KEYS if comparable.get(key) != previous["basis"].get(key))
    if not changed and not added_decisions:
        raise _refresh_contract_error("refresh derivado sin cambios en superficies derivables")
    if not isinstance(previous_snapshot, dict):
        raise _refresh_contract_error("APPROVE_RECEIPT legacy sin snapshot derivable; requiere aprobación explícita")
    non_additive = sorted(
        key
        for key in changed
        if (
            key == "module_spec"
            and previous_snapshot.get(key) != comparable_snapshot.get(key)
            and not ("module_spec:clasificar" in covers and _specs_as_planned(previous, state, module))
        ) or (
            key != "module_spec"
            and not _is_additive_snapshot(
                previous_snapshot.get(key), comparable_snapshot.get(key)
            )
        )
    )
    rationale = (additive_rationale or "").strip()
    if non_additive and not rationale:
        raise _refresh_contract_error(
            "derivación reescribe o elimina contenido aprobado; requiere nueva aprobación, "
            "o additive_rationale que justifique que es una reescritura narrativa de "
            "secciones ya evidenciadas: " + ", ".join(non_additive)
        )
    current = approval_basis(
        root, state, module, basis_version=3, foreign_sections=new_foreign_sections, covers_changes=covers
    )
    prev_added = [
        str(x) for x in (previous.get("added_decisions") or [])
        if isinstance(x, str) and x.strip()
    ]
    accumulated_added: list[str] = list(prev_added)
    for adr_id in added_decisions:
        if adr_id not in accumulated_added:
            accumulated_added.append(adr_id)
    docs_map = dict(previous.get("documents_decisions") or {})
    known_decisions = {
        d["id"]: d for d in state.get("_decisions", []) if isinstance(d, dict) and "id" in d
    }
    for adr_id in accumulated_added:
        cited_id, problem = _documents_decision(known_decisions, adr_id, module)
        if problem:
            raise _refresh_contract_error(f"refresh derivado{problem}")
        docs_map[adr_id] = cited_id

    covers_deps_refresh = list(previous.get("covers_dependencies", []))
    approved_deps_refresh = (
        list(previous.get("approved_dependencies"))
        if "approved_dependencies" in previous
        else list(state.get("dependencies", []))
    )

    receipt = dict(previous)
    # FW-185: si `previous` era legacy con el snapshot inline, no dejarlo colgado
    # — `load_derived_snapshot` prefiere el inline y un futuro refresh compararía
    # contra el snapshot viejo.
    receipt.pop("derived_snapshot", None)
    receipt.update({
        "kind": "derived_additive",
        "parent_fingerprint": parent_fingerprint,
        "derived_from": list(derived_from),
        "derivation_evidence": evidence.strip(),
        "derived_surfaces": changed,
        "non_additive_surfaces": non_additive,
        "additive_rationale": rationale or None,
        "added_decisions": accumulated_added,
        "documents_decisions": docs_map,
        "covers_dependencies": covers_deps_refresh,
        "approved_dependencies": approved_deps_refresh,
        "foreign_sections": new_foreign_sections,
        "basis": current,
        "basis_version": 3,
        "spec_applicability": spec_applicability(state, module),
        "derived_snapshot_ref": store_derived_snapshot(root, current_snapshot),
        "refreshed_at": now_iso,
    })
    receipt["fingerprint"] = _sealed_fingerprint(receipt, current, 3, new_foreign_sections, covers)
    _receipts(state)["approvals"][module] = receipt
    return receipt


class ProblemsList(list):
    """Lista de problemas que expone warnings asociados sin romper callers de list."""

    def __init__(self, iterable=(), warnings: list[str] | None = None):
        super().__init__(iterable)
        self.warnings: list[str] = list(warnings or [])


# Dimensiones cuyo cambio SOLO aditivo (todas las líneas previas siguen, en
# orden) no deja el APPROVE_RECEIPT stale. FW-278 (5.2.2 L2): documentar en
# data_model/contratos lo que un AC del propio plan pide invalidaba la
# aprobación a mitad del módulo (TL FW-257 ×3). Quitar o cambiar una línea
# sigue exigiendo aprobación; las claves críticas no entran nunca aquí.
APPROVAL_ADDITIVE_TOLERATED = frozenset(
    {"acceptance", "active_scope", "data_model", "api_contract", "contracts"}
)


def _sealed_fingerprint(
    receipt: dict, basis: dict, basis_version: int, foreign_sections: object, covers_changes: list[str],
) -> str:
    """La huella del recibo recalculada con sus propios campos sobre ``basis``."""
    return approval_fingerprint(
        basis, tier=receipt.get("tier"), approved_by=receipt.get("approved_by", ""),
        approval_evidence=receipt.get("approval_evidence", ""), risk_summary=receipt.get("risk_summary", ""),
        adr_refs=receipt.get("adr_refs", []), basis_version=basis_version,
        covers_dependencies=receipt.get("covers_dependencies", []),
        approved_dependencies=receipt.get("approved_dependencies", []),
        foreign_sections=foreign_sections, covers_changes=covers_changes,
        covers_extra={key: receipt.get(key) for key in ("covers_digests", "spec_applicability")},
    )


def approval_problems(
    root: Path,
    state: dict,
    module: str,
    *,
    current_basis: dict[str, str] | None = None,
    warnings: list[str] | None = None,
) -> list[str]:
    """Valida el receipt contra una única base de aprobación de la consulta.

    ``current_basis`` permite que el transporte reutilice la misma lectura que
    conserva en sus dependencias/fingerprint. Los callers históricos que no lo
    pasan mantienen exactamente el cálculo anterior.
    """
    receipt = state.get("workflow_receipts", {}).get("approvals", {}).get(module)
    if not isinstance(receipt, dict):
        return ProblemsList([f"APPROVE_RECEIPT ausente para '{module}'"])
    problems: list[str] = approval_receipt_state_problems(state, module)
    if receipt.get("status") != "approved" or receipt.get("approved_by") != "builder":
        problems.append("APPROVE_RECEIPT no acredita aprobación explícita del builder")
    basis_version = receipt.get("basis_version", 1)
    has_foreign_sections = "foreign_sections" in receipt
    raw_foreign_sections = (
        receipt.get("foreign_sections")
        if isinstance(receipt.get("foreign_sections"), dict)
        else None
    )
    rec_foreign_sections = raw_foreign_sections if basis_version >= 3 else None
    # 5.3.3 (L7.3): lo que se descuenta al leer; la huella sigue con la lista sellada.
    read_foreign_sections = _effective_foreign_sections(root, state, module, receipt)
    rec_covers = _receipt_covers(receipt)
    current = (
        current_basis
        if current_basis is not None
        else approval_basis(
            root, state, module, basis_version=basis_version, foreign_sections=read_foreign_sections,
            covers_changes=rec_covers,
        )
    )

    def sealed(basis: dict, version: int = basis_version) -> str:
        return _sealed_fingerprint(receipt, basis, version, rec_foreign_sections, rec_covers)

    expected = sealed(current)
    expected_without_deps = sealed(current, 1)
    raw_approved_deps = receipt.get("approved_dependencies") or []
    raw_curr_deps = state.get("dependencies", [])
    raw_covers = receipt.get("covers_dependencies", [])
    approved_deps = {str(d) for d in raw_approved_deps if d is not None}
    curr_deps = {str(d) for d in raw_curr_deps if d is not None}
    covers = {str(d) for d in raw_covers if d is not None}
    deps_untouched = (not covers and approved_deps == curr_deps)
    foreign_sections_empty = not rec_foreign_sections or not any(rec_foreign_sections.values())
    legacy_form_ok = deps_untouched and foreign_sections_empty and not rec_covers

    rec_basis = receipt.get("basis") if isinstance(receipt.get("basis"), dict) else {}
    if has_foreign_sections and (
        raw_foreign_sections is None
        or (basis_version < 3 and any(raw_foreign_sections.values()))
    ) or receipt.get("covers_changes", rec_covers) != rec_covers:
        receipt_self_coherent = False
    else:
        receipt_self_coherent = (
            receipt.get("fingerprint") == sealed(rec_basis)
            or (legacy_form_ok and receipt.get("fingerprint") == sealed(rec_basis, 1))
        )

    fingerprint_match = receipt_self_coherent and (
        receipt.get("fingerprint") == expected
        or (legacy_form_ok and receipt.get("fingerprint") == expected_without_deps)
    )
    if not fingerprint_match and receipt_self_coherent:
        # FW-337 (L6): si la única discrepancia es por dependencias cubiertas,
        # validar recalculando la huella con las dependencias aprobadas y exigir coincidencia.
        added_deps = curr_deps - approved_deps
        if approved_deps.issubset(curr_deps) and added_deps and added_deps.issubset(covers):
            covered_basis = dict(current)
            covered_basis["dependencies"] = rec_basis.get("dependencies", "")
            expected_covered = sealed(covered_basis)
            if receipt.get("fingerprint") == expected_covered:
                fingerprint_match = True

    detected_warnings: list[str] = []
    sealed_digests = receipt.get("covers_digests") if isinstance(receipt.get("covers_digests"), dict) else {}
    touched = sorted(k for k, v in _covered_digests(root, rec_covers).items() if sealed_digests.get(k) != v)
    if touched:  # L4.2: lo declarado no caduca, pero se ve (revisión `security`, hallazgo 1)
        detected_warnings.append(f"cambió lo declarado en covers_changes desde el sello: {', '.join(touched)}")
        if warnings is not None:
            warnings.append(detected_warnings[-1])
    if not fingerprint_match:
        changed_dims = sorted(k for k in current if rec_basis.get(k) != current.get(k))
        changed_other = []
        if receipt.get("tier") != state.get("work_tiers", {}).get(module, {}).get("tier"):
            changed_other.append("tier")
        if receipt.get("approved_by") != "builder":
            changed_other.append("approved_by")
        if not receipt_self_coherent:
            changed_other.append("fingerprint")

        # Quitar dependencies de changed_dims SOLO si están cubiertas y las aprobadas siguen intactas
        added_deps = curr_deps - approved_deps
        covered_ok = bool(
            receipt_self_coherent
            and approved_deps.issubset(curr_deps)
            and added_deps
            and added_deps.issubset(covers)
        )
        if covered_ok:
            changed_dims = [d for d in changed_dims if d != "dependencies"]

        # FW-298: pasos que el propio plan prevé (specs a done, evidencia
        # anotada) no son un cambio de lo aprobado; no piden refresh ceremonial.
        planned: list[str] = []
        if changed_dims and not changed_other:
            notes: list[str] = []
            planned = _planned_transition_dims(
                root, state, module, receipt, changed_dims, basis_version, notes,
                foreign_sections=read_foreign_sections,
            )
            if planned:
                msg = f"transición prevista por el plan sin re-sellar: {', '.join(planned)}"
                msg += f" ({'; '.join(notes)})" if notes else ""
                detected_warnings.append(msg)
                if warnings is not None:
                    warnings.append(msg)
                changed_dims = [dim for dim in changed_dims if dim not in planned]

        is_additive = False
        if planned and not changed_dims and not changed_other:
            is_additive = True
        elif (
            changed_dims
            and set(changed_dims).issubset(APPROVAL_ADDITIVE_TOLERATED)
            and not changed_other
        ):
            stored_snapshot = load_derived_snapshot(root, receipt)
            current_snapshot = approval_derived_snapshot(
                root, state, module, basis_version=basis_version, foreign_sections=read_foreign_sections,
                covers_changes=rec_covers,
            )
            if (
                isinstance(stored_snapshot, dict)
                and stored_snapshot.get("fmt") == current_snapshot.get("fmt")
            ):
                all_dims_additive = True
                for dim in changed_dims:
                    if not _is_additive_snapshot(
                        stored_snapshot.get(dim), current_snapshot.get(dim)
                    ):
                        all_dims_additive = False
                        break
                if all_dims_additive:
                    is_additive = True
                    msg = f"derivación aditiva sin re-sellar: {', '.join(changed_dims)}"
                    detected_warnings.append(msg)
                    if warnings is not None:
                        warnings.append(msg)

        if not is_additive:
            if not changed_dims:
                changed_dims = changed_other or ["fingerprint"]
            enriched_dims = []
            for dim in changed_dims:
                if dim == "decisions":
                    stored_snapshot = _normalize_snapshot_format(load_derived_snapshot(root, receipt))
                    current_snapshot = approval_derived_snapshot(
                        root, state, module, basis_version=basis_version, foreign_sections=read_foreign_sections,
                        covers_changes=rec_covers,
                    )
                    curr_adrs = [
                        d for d in state.get("_decisions", [])
                        if isinstance(d, dict) and _adr_applies_to_module(d.get("module_id"), module)
                    ]
                    if _decisions_change_is_additive(stored_snapshot, current_snapshot):
                        added_adrs = [adr for adr in _added_adr_ids(state, module, stored_snapshot) if adr]
                        enriched_dims.append(f"decisions: {', '.join(added_adrs)}; usa refresh_approval_receipt")
                    else:
                        affected: list[str] = []
                        if isinstance(stored_snapshot, dict):
                            aprobadas = set(stored_snapshot.get("decisions", []))
                            for d in curr_adrs:
                                adr_id = str(d.get("id") or "").strip()
                                if adr_id and _truncated_hash(d) not in aprobadas:
                                    affected.append(adr_id)
                            for ref in receipt.get("adr_refs", []):
                                ref_str = str(ref).strip()
                                if ref_str and ref_str not in affected:
                                    if not any(str(d.get("id")) == ref_str and _truncated_hash(d) in aprobadas for d in curr_adrs):
                                        affected.append(ref_str)
                        if not affected:
                            affected = [str(ref) for ref in receipt.get("adr_refs", []) if ref]
                        if affected:
                            enriched_dims.append(
                                f"decisions: {', '.join(affected)} alterada o eliminada; requiere nueva aprobación del builder"
                            )
                        else:
                            enriched_dims.append("decisions: base aprobada alterada; requiere nueva aprobación del builder")
                elif dim == "dependencies":
                    if "approved_dependencies" in receipt:
                        added_non_covered = curr_deps - approved_deps - covers
                        if added_non_covered:
                            enriched_dims.append(
                                f"dependencies: {', '.join(sorted(added_non_covered))}; requiere nueva aprobación del builder"
                            )
                        else:
                            enriched_dims.append(
                                "dependencies: dependencias aprobadas alteradas o eliminadas; requiere nueva aprobación del builder"
                            )
                    else:
                        enriched_dims.append(
                            "dependencies: lista de dependencias modificada; requiere nueva aprobación del builder"
                        )
                elif dim in {"active_scope", "acceptance"}:
                    fs_dim = (
                        read_foreign_sections.get(dim)
                        if isinstance(read_foreign_sections, dict)
                        else None
                    )
                    n_foreign = len(fs_dim) if isinstance(fs_dim, list) else 0
                    if n_foreign == 1:
                        fs_note = " (1 sección ajena descontada)"
                    elif n_foreign > 1:
                        fs_note = f" ({n_foreign} secciones ajenas descontadas)"
                    else:
                        fs_note = ""
                    label_txt = "alcance modificado" if dim == "active_scope" else "criterios modificados"
                    enriched_dims.append(
                        f"{dim}: {label_txt}{fs_note}; requiere nueva aprobación o refresh_approval_receipt"
                    )
                elif dim == "tier":
                    curr_tier = state.get("work_tiers", {}).get(module, {}).get("tier")
                    rec_tier = receipt.get("tier")
                    if rec_tier == curr_tier:
                        enriched_dims.append(
                            f"tier: cambió la justificación de {curr_tier}; requiere nueva aprobación del builder"
                        )
                    else:
                        enriched_dims.append(
                            f"tier: cambió de {rec_tier} a {curr_tier}; actualiza work_tiers o requiere nueva aprobación del builder"
                        )
                elif dim in {"prd", "phase_plan", "current_phase"}:
                    enriched_dims.append(f"{dim}: base modificada; requiere nueva aprobación del builder")
                else:
                    enriched_dims.append(f"{dim}: base modificada; requiere nueva aprobación o refresh_approval_receipt")
            problems.append(
                f"APPROVE_RECEIPT stale: cambiaron dimensiones ({', '.join(enriched_dims)})"
            )
    current_tier = state.get("work_tiers", {}).get(module, {}).get("tier")
    if receipt.get("tier") != current_tier:
        problems.append("APPROVE_RECEIPT stale: cambió el tier")
    known_adrs = {
        d.get("id"): d for d in state.get("_decisions", []) if isinstance(d, dict)
    }
    if any(ref not in known_adrs for ref in receipt.get("adr_refs", [])):
        problems.append("APPROVE_RECEIPT referencia ADR ausente")
    elif any(
        not _adr_applies_to_module(known_adrs[ref].get("module_id"), module)
        for ref in receipt.get("adr_refs", [])
    ):
        problems.append("APPROVE_RECEIPT referencia ADR de otro módulo")
    if not str(receipt.get("approval_evidence") or "").strip():
        problems.append("APPROVE_RECEIPT sin evidencia")
    return ProblemsList(problems, warnings=detected_warnings)


def approval_warnings(
    root: Path,
    state: dict,
    module: str,
    *,
    current_basis: dict[str, str] | None = None,
) -> list[str]:
    """Recupera los avisos no bloqueantes de la base de aprobación (derivación aditiva)."""
    warns: list[str] = []
    approval_problems(root, state, module, current_basis=current_basis, warnings=warns)
    return warns


def approval_receipt_state_problems(state: dict, module: str) -> list[str]:
    """Valida la parte autocontenida del recibo, sin leer el workspace.

    `closure_gate()` se usa también como función pura en calibración. En el
    camino real (`complete_module`/CLI) se añade `approval_problems()` para
    comprobar además que el fingerprint sigue ligado a los archivos actuales.
    """
    receipt = state.get("workflow_receipts", {}).get("approvals", {}).get(module)
    if not isinstance(receipt, dict):
        return [f"APPROVE_RECEIPT ausente para '{module}'"]
    problems: list[str] = []
    tier = state.get("work_tiers", {}).get(module, {}).get("tier")
    if receipt.get("status") != "approved" or receipt.get("approved_by") != "builder":
        problems.append("APPROVE_RECEIPT no acredita aprobación explícita del builder")
    if receipt.get("tier") != tier:
        problems.append("APPROVE_RECEIPT stale: cambió el tier")
    if not isinstance(receipt.get("basis"), dict) or not str(receipt.get("fingerprint") or "").strip():
        problems.append("APPROVE_RECEIPT incompleto: falta basis/fingerprint")
    if not str(receipt.get("approval_evidence") or "").strip():
        problems.append("APPROVE_RECEIPT sin evidencia")
    if not str(receipt.get("risk_summary") or "").strip():
        problems.append("APPROVE_RECEIPT sin resumen de riesgo")
    adr_refs = receipt.get("adr_refs")
    if tier == "tier_2" and not (isinstance(adr_refs, list) and adr_refs):
        problems.append("APPROVE_RECEIPT tier_2 sin ADR vinculada")
    known_adrs = {
        d.get("id"): d for d in state.get("_decisions", []) if isinstance(d, dict)
    }
    if isinstance(adr_refs, list) and any(ref not in known_adrs for ref in adr_refs):
        problems.append("APPROVE_RECEIPT referencia ADR ausente")
    elif isinstance(adr_refs, list) and any(
        not _adr_applies_to_module(known_adrs[ref].get("module_id"), module)
        for ref in adr_refs
    ):
        problems.append("APPROVE_RECEIPT referencia ADR de otro módulo")
    return problems


def _tracked_product_paths(root: Path) -> list[str]:
    try:
        probe = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "--is-inside-work-tree"],
            capture_output=True, text=True, timeout=5, check=False,
        )
        if probe.returncode == 0:
            changed = subprocess.run(
                ["git", "-C", str(root), "diff", "--name-only", "HEAD"],
                capture_output=True, text=True, timeout=10, check=False,
            ).stdout.splitlines()
            untracked = subprocess.run(
                ["git", "-C", str(root), "ls-files", "--others", "--exclude-standard"],
                capture_output=True, text=True, timeout=10, check=False,
            ).stdout.splitlines()
            return sorted(
                path
                for path in set(changed + untracked)
                if not _receipt_excluded(path)
            )
    except (OSError, subprocess.SubprocessError):
        pass
    paths = []
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        rel = path.relative_to(root).as_posix()
        if _receipt_excluded(rel) or ".git/" in rel or "__pycache__/" in rel:
            continue
        paths.append(rel)
    return sorted(paths)


def _compute_workspace_diff_hash(root: Path) -> str:
    """Cálculo puro in-process del hash de receipts — nunca lanza subprocesos.

    Nombre privado y estable a propósito: es la única fuente de verdad para
    el hash real, y sirve de fallback dentro de `workspace_diff_hash_fresh()`
    (S3, TL FW-008 residuo). Un llamador de vida larga (state_manager.py)
    puede reasignar en tiempo de ejecución el nombre público
    `workspace_diff_hash` para forzar el cálculo por subproceso fresco; el
    fallback de `workspace_diff_hash_fresh()` llama a ESTA función, nunca al
    nombre público, para no arriesgar recursión si alguna vez quedara
    reasignado a sí mismo.
    """
    digest = hashlib.sha256()
    for rel in _tracked_product_paths(root):
        path = root / rel
        digest.update(rel.encode("utf-8")); digest.update(b"\0")
        digest.update(str(path.stat().st_mode if path.exists() else 0).encode("ascii")); digest.update(b"\0")
        digest.update(path.read_bytes() if path.exists() else b"<DELETED>")
    return digest.hexdigest()


# --- FW-226: hash de contenido v2 ---------------------------------------------
# El hash legado (`_compute_workspace_diff_hash`) solo mira lo NO commiteado
# (`git diff HEAD` + untracked): commitear el diff recién revisado cambia el
# conjunto y caduca el recibo (PA/TL FW-126), y marcar un AC o anotar su
# evidencia en `08_module_acceptance.md` también lo caduca (21 instancias en TL,
# PA FW-131, Example Service FW-132). El v2 identifica el ÁRBOL por contenido — igual
# antes y después de un commit — normaliza el progreso de scope/aceptación con
# la misma regla que el APPROVE_RECEIPT y deja fuera las bitácoras narrativas.
DIFF_HASH_V2_PREFIX = "v2:"
DIFF_HASH_V3_PREFIX = "v3:"
DOCS_DRIFT_WARNING = "deriva documental posterior al sello (cambió la huella de documentación)"
V3_HASH_PATTERN = re.compile(r"^v3:code=([0-9a-f]{64});docs=([0-9a-f]{64})$")
_PROGRESS_NORMALIZED_PATHS = frozenset({
    "_project/04_active_scope.md",
    "_project/08_module_acceptance.md",
})
_V2_EXCLUDED_PATHS = frozenset({
    "_project/05_test_history.md",
    "_project/13_session_journal.md",
    "_project/22_lessons.md",
    "_project/22_lessons_digest.md",
})
_V2_EXCLUDED_PREFIXES = ("_feedback/", ".git/")
_DOCS_ROOT_EXACT = frozenset({
    "README.md",
    "CHANGELOG.md",
    ".agent/docs/field_report.md",
    ".agent/docs/friction_buffer.md",
    "_project/04_active_scope.md",
    "_project/04_scope_archive.md",
    "_project/08_module_acceptance.md",
})


def _is_documentation(rel: str) -> bool:
    """Allowlist cerrada de documentación: devuelve True únicamente para:
    - docs/**/*.md (solo .md)
    - README.md y CHANGELOG.md en la raíz
    - .agent/docs/field_report.md
    - .agent/docs/friction_buffer.md
    - _project/04_active_scope.md
    - _project/04_scope_archive.md
    - _project/08_module_acceptance.md
    - _project/08_module_acceptance_phase_*.md

    Todo lo demás es código, incluidos los ficheros que no existan hoy.
    """
    norm = rel.replace("\\", "/").strip("/")
    if norm in _DOCS_ROOT_EXACT:
        return True
    if norm.startswith("docs/") and norm.endswith(".md"):
        return True
    if norm.startswith("_project/08_module_acceptance_phase_") and norm.endswith(".md"):
        return "/" not in norm[len("_project/"):]
    return False


def _progress_identity(path: Path) -> str:
    """Identidad de scope/aceptación para el hash v2/v3: la normalización de
    progreso del APPROVE_RECEIPT más el colapso de líneas en blanco. El sello
    `Estado: ✅ Verificado con cliente` que añade el paso 6 de /cierre-modulo
    llega con su línea en blanco; sin colapsarla, el propio sello caducaba el
    recibo (Example Service FW-132). Solo aquí: tocar la normalización compartida
    cambiaría la huella de los recibos de aprobación ya sellados."""
    return "norm:" + _normalized_progress_hash(path, basis_version=2)


def _v2_excluded(rel: str) -> bool:
    return (
        _receipt_excluded(rel)
        or rel in _V2_EXCLUDED_PATHS
        or rel.startswith(_V2_EXCLUDED_PREFIXES)
        or "__pycache__/" in rel
    )


def _git_z(root: Path, *args: str) -> list[str]:
    out = subprocess.run(
        ["git", "-C", str(root), *args], capture_output=True, timeout=30, check=False,
    )
    if out.returncode != 0:
        raise subprocess.SubprocessError(f"git {' '.join(args)} rc={out.returncode}")
    return [p for p in out.stdout.decode("utf-8", "surrogateescape").split("\0") if p]


def _content_identities(root: Path) -> dict[str, str]:
    """ruta → identidad de contenido. Con git, los ficheros intactos reutilizan
    el id del índice y los modificados/nuevos se calculan con `git hash-object`
    (aplica los mismos filtros que `git add`, así que el id coincide antes y
    después del commit). Sin git, sha256 de los bytes."""
    identities: dict[str, str] = {}
    try:
        index: dict[str, str] = {}
        for line in _git_z(root, "ls-files", "-s", "-z"):
            meta, path = line.split("\t", 1)
            index[path] = meta.split()[1]
        dirty = set(_git_z(root, "diff", "--name-only", "-z"))
        dirty |= set(_git_z(root, "ls-files", "--others", "--exclude-standard", "-z"))
        paths = sorted(
            p for p in set(index) | dirty
            if not _v2_excluded(p) and (root / p).is_file()
        )
        to_hash = [
            p for p in paths
            if p not in _PROGRESS_NORMALIZED_PATHS and (p in dirty or p not in index) and "\n" not in p
        ]
        hashed: list[str] = []
        if to_hash:
            out = subprocess.run(
                ["git", "-C", str(root), "hash-object", "--stdin-paths"],
                input="\n".join(to_hash), capture_output=True, text=True, timeout=60, check=False,
            )
            if out.returncode != 0:
                raise subprocess.SubprocessError("git hash-object falló")
            hashed = out.stdout.split()
            if len(hashed) != len(to_hash):
                raise subprocess.SubprocessError("git hash-object devolvió un recuento distinto")
        fresh = dict(zip(to_hash, hashed))
        for p in paths:
            if p in _PROGRESS_NORMALIZED_PATHS:
                identities[p] = _progress_identity(root / p)
            elif p in fresh:
                identities[p] = fresh[p]
            elif p in index:
                identities[p] = index[p]
        return identities
    except (OSError, subprocess.SubprocessError, ValueError, IndexError):
        identities.clear()
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        rel = path.relative_to(root).as_posix()
        if _v2_excluded(rel):
            continue
        if rel in _PROGRESS_NORMALIZED_PATHS:
            identities[rel] = _progress_identity(path)
        else:
            identities[rel] = hashlib.sha256(path.read_bytes()).hexdigest()
    return identities


_DECLARED_ALLOWED_EXTENSIONS = frozenset({".md", ".json", ".csv", ".txt"})
_CONSTITUTIONAL_CODE_PATHS = frozenset({
    "AGENTS.md",
    "CLAUDE.md",
    "GEMINI.md",
    "GUARDRAILS_MASTER.md",
    "PRODUCT.md",
    "DESIGN.md",
    "_project/01_product_requirements.md",
    "_project/03_contracts.md",
    "_project/03_invariants.md",
    "_project/10_arch_constraints.md",
    "_project/11_file_dependencies.md",
    "_project/02_technical_plan/data_model.md",
})


def _is_declarable_path(rel: str) -> bool:
    """Regla constitucional de qué puede declararse como documentación (L6 / FW-337).

    - Debe estar bajo _project/
    - Extensión permitida: .md, .json, .csv, .txt
    - Fuera _project/approval_snapshots/
    - Ficheros bajo _project/02_technical_plan/: solo si su nombre termina en _result.md o _memo.md, o si están en memos/
    - Quedan fuera de lo declarable:
        * Ficheros numerados bajo _project/ (ej: 01_prd, 10_arch, 20_profile)
        * Ficheros estructurales de _project/ (ej: core.md)
        * Plan de fase (02_technical_plan/phase_*.md)
        * Archivos constitucionales fijados como código
    """
    norm = rel.replace("\\", "/").strip("/")
    if not norm.startswith("_project/"):
        return False
    if norm.startswith("_project/approval_snapshots/"):
        return False
    ext = os.path.splitext(norm)[1].lower()
    if ext not in {".md", ".json", ".csv", ".txt"}:
        return False
    if norm in _CONSTITUTIONAL_CODE_PATHS:
        return False
    if norm.startswith("_project/02_technical_plan/"):
        if norm.startswith("_project/02_technical_plan/phase_"):
            return False
        filename = os.path.basename(norm).lower()
        sub_tp = norm[len("_project/02_technical_plan/"):]
        return bool(filename.endswith(("_result.md", "_memo.md")) or sub_tp.startswith("memos/"))
    sub = norm[len("_project/"):]
    if re.match(r"^\d{2}_", sub) or sub == "core.md":
        return False
    return True


def _collect_declared_evidence_paths(root: Path) -> set[str]:
    """Rutas de memos/evidencias declaradas bajo _project/ (FW-337 L6)."""
    declared = set()
    texts: list[str] = []
    acc_path = root / "_project" / "08_module_acceptance.md"
    if acc_path.is_file():
        try:
            texts.append(acc_path.read_text(encoding="utf-8", errors="ignore"))
        except OSError:
            pass
    state_path = root / "_project" / "03_project_state.json"
    if state_path.is_file():
        try:
            state_data = json.loads(state_path.read_text(encoding="utf-8", errors="ignore"))
            specs = state_data.get("module_specs", {})
            if isinstance(specs, dict):
                texts.append(json.dumps(specs, ensure_ascii=False))
        except (OSError, json.JSONDecodeError):
            pass

    path_pattern = re.compile(r"(_project/[A-Za-z0-9_./\-]+)")
    for text in texts:
        for match in path_pattern.findall(text):
            p = match.strip().rstrip(".,;:)'\"")
            if _is_declarable_path(p):
                declared.add(p)
    return declared


def _compute_workspace_content_hashes(root: Path) -> tuple[str, str, str]:
    """Calcula los hashes v3, v2 y v3_legacy del workspace en una sola pasada sobre
    las identidades de contenido repartidas por el clasificador.
    Devuelve (v3_hash, v2_hash, v3_legacy_hash).
    """
    code_digest = hashlib.sha256()
    docs_digest = hashlib.sha256()
    legacy_code_digest = hashlib.sha256()
    legacy_docs_digest = hashlib.sha256()
    v2_digest = hashlib.sha256()
    declared_evidence = _collect_declared_evidence_paths(root)
    for rel, identity in sorted(_content_identities(root).items()):
        path = root / rel
        executable = "x" if os.access(path, os.X_OK) else "-"
        is_doc_base = _is_documentation(rel)
        is_doc = is_doc_base or (rel in declared_evidence)
        target = docs_digest if is_doc else code_digest
        legacy_target = legacy_docs_digest if is_doc_base else legacy_code_digest
        for part in (rel, executable, identity):
            encoded = part.encode("utf-8", "surrogateescape")
            target.update(encoded)
            target.update(b"\0")
            legacy_target.update(encoded)
            legacy_target.update(b"\0")
            v2_digest.update(encoded)
            v2_digest.update(b"\0")
    v3_hash = f"v3:code={code_digest.hexdigest()};docs={docs_digest.hexdigest()}"
    v3_legacy_hash = f"v3:code={legacy_code_digest.hexdigest()};docs={legacy_docs_digest.hexdigest()}"
    v2_hash = DIFF_HASH_V2_PREFIX + v2_digest.hexdigest()
    return v3_hash, v2_hash, v3_legacy_hash


def _compute_workspace_content_hash(root: Path) -> str:
    """Hash v3 canónico del workspace: v3:code=<sha256>;docs=<sha256>."""
    return _compute_workspace_content_hashes(root)[0]


def _compute_workspace_content_hash_v2(root: Path) -> str:
    """Hash v2 del workspace: v2:<sha256>."""
    return _compute_workspace_content_hashes(root)[1]


class DiffHash(str):
    """Hash v3 del workspace con acceso explícito al hash v2, v3_legacy y legado."""

    def __new__(
        cls,
        value: str,
        root: Path | None = None,
        *,
        v2: str | None = None,
        v3_legacy: str | None = None,
        legacy: str | None = None,
    ):
        obj = super().__new__(cls, value)
        obj._root = root
        obj._v2 = v2
        obj._v3_legacy = v3_legacy
        obj._legacy = legacy
        return obj

    @property
    def v2(self) -> str | None:
        if self._v2 is None and self._root is not None:
            self._v2 = _compute_workspace_content_hash_v2(self._root)
        return self._v2

    @property
    def v3_legacy(self) -> str | None:
        if self._v3_legacy is None and self._root is not None:
            self._v3_legacy = _compute_workspace_content_hashes(self._root)[2]
        return self._v3_legacy

    @property
    def legacy(self) -> str | None:
        if self._legacy is None and self._root is not None:
            self._legacy = _compute_workspace_diff_hash(self._root)
        return self._legacy


def _parse_v3_hash(val: str) -> tuple[str, str] | None:
    m = V3_HASH_PATTERN.match(val)
    if m:
        return m.group(1), m.group(2)
    return None


class DiffHashMatch(int):
    """Resultado de diff_hash_matches con evaluación booleana (solo code)
    y flag explicativo de cambio de documentación.
    """

    def __new__(cls, matches: bool, docs_changed: bool = False):
        obj = super().__new__(cls, 1 if matches else 0)
        obj.matches = bool(matches)
        obj.docs_changed = bool(docs_changed)
        obj.docs_differ = bool(docs_changed)
        return obj

    def __iter__(self):
        return iter((self.matches, self.docs_changed))


def diff_hash_matches(stored: object, current: object) -> DiffHashMatch:
    """¿El `diff_hash` sellado corresponde al workspace actual?

    - Para v3: compara SOLO `code`. Si difiere del actual, comprueba contra
      v3_legacy (preserva recibos sellados antes de la regla de declarables).
    - Para v2: compara contra el hash v2 del workspace actual.
    - Para legacy: compara contra el hash legacy del workspace actual.
    """
    if not isinstance(stored, str) or current is None:
        return DiffHashMatch(False, False)

    if stored.startswith(DIFF_HASH_V3_PREFIX):
        stored_parts = _parse_v3_hash(stored)
        current_parts = _parse_v3_hash(str(current))
        if stored_parts is None or current_parts is None:
            return DiffHashMatch(False, False)
        stored_code, stored_docs = stored_parts
        current_code, current_docs = current_parts
        if stored_code == current_code:
            return DiffHashMatch(True, stored_docs != current_docs)
        v3_leg = getattr(current, "v3_legacy", None)
        if v3_leg is not None:
            leg_parts = _parse_v3_hash(v3_leg)
            if leg_parts is not None and stored_code == leg_parts[0]:
                return DiffHashMatch(True, stored_docs != leg_parts[1])
        return DiffHashMatch(False, stored_docs != current_docs)

    if stored.startswith(DIFF_HASH_V2_PREFIX):
        v2_current = getattr(current, "v2", None)
        target = v2_current if v2_current is not None else str(current)
        return DiffHashMatch(stored == target, False)

    legacy = getattr(current, "legacy", None)
    target = legacy if legacy is not None else str(current)
    return DiffHashMatch(stored == target, False)


def workspace_diff_hash(root: Path) -> DiffHash:
    v3_hash, v2_hash, v3_legacy_hash = _compute_workspace_content_hashes(root)
    return DiffHash(v3_hash, root, v2=v2_hash, v3_legacy=v3_legacy_hash)


def workspace_diff_hash_fresh(root: Path, *, timeout: float = 30.0) -> tuple[DiffHash, str | None]:
    """S3 (TL FW-008 residuo): calcula el diff_hash lanzando un subproceso
    Python fresco (`workflow_receipts.py hash`) que SIEMPRE importa este
    módulo desde disco — inmune a que un proceso servidor de larga vida
    (state_manager.py corriendo como servidor MCP durante una sesión) tenga
    en memoria una versión vieja de este archivo tras un upgrade de
    framework en sesión viva. `reload_if_changed()` (state_core.py) solo
    recarga el ESTADO JSON; Python nunca re-importa código ya cargado en el
    proceso.

    Si el subproceso falla por cualquier motivo (timeout, exit!=0, salida no
    reconocible como hash sha256, excepción de `subprocess.run`) cae al
    cálculo in-process (`_compute_workspace_diff_hash`) y devuelve un
    warning explicando el fallback.

    Devuelve (hash, warning): `warning` es `None` en el camino feliz.
    """
    try:
        proc = subprocess.run(
            [sys.executable, str(Path(__file__).resolve()), "hash"],
            cwd=str(root), capture_output=True, text=True, timeout=timeout, check=False,
        )
        candidate = proc.stdout.strip()
        if proc.returncode == 0 and re.fullmatch(
            r"^(?:v2:[0-9a-f]{64}|v3:code=[0-9a-f]{64};docs=[0-9a-f]{64}|[0-9a-f]{64})$",
            candidate,
        ):
            return DiffHash(candidate, root), None
    except (OSError, subprocess.SubprocessError):
        pass
    v3_hash, v2_hash, v3_legacy_hash = _compute_workspace_content_hashes(root)
    return (
        DiffHash(v3_hash, root, v2=v2_hash, v3_legacy=v3_legacy_hash),
        "hash calculado in-process — subproceso fresco falló",
    )


def receipt_warnings(
    state: dict,
    module: str,
    current_diff_hash: str | None = None,
    root: Path | None = None,
) -> list[str]:
    """Avisos no bloqueantes de recibos (p. ej. deriva documental o derivación aditiva)."""
    warnings: list[str] = []
    if root is not None:
        warnings.extend(approval_warnings(root, state, module))
    if current_diff_hash is not None:
        history = state.get("workflow_receipts", {}).get("reviews", {}).get(module)
        review_receipt = history[-1] if isinstance(history, list) and history else None
        if isinstance(review_receipt, dict):
            diff_h = review_receipt.get("diff_hash")
            if diff_h:
                match = diff_hash_matches(diff_h, current_diff_hash)
                if match and getattr(match, "docs_changed", False):
                    warnings.append(DOCS_DRIFT_WARNING)
        exec_receipt = state.get("workflow_receipts", {}).get("executions", {}).get(module)
        if isinstance(exec_receipt, dict):
            diff_h = exec_receipt.get("diff_hash")
            if diff_h:
                match = diff_hash_matches(diff_h, current_diff_hash)
                if match and getattr(match, "docs_changed", False):
                    if DOCS_DRIFT_WARNING not in warnings:
                        warnings.append(DOCS_DRIFT_WARNING)
    return warnings


def start_review(
    state: dict,
    root: Path,
    module: str,
    *,
    reviewer_id: str,
    implementer_id: str,
    mode: str,
    now_iso: str,
) -> dict:
    """FW-163b: `reviews[module]` es historial append-only, no un recibo único.

    Cada llamada añade una ronda nueva a la lista en vez de sobreescribir la
    anterior — el motivo real fue que perder el historial de rondas previas
    obligaba al agente a llevarlo a mano fuera del estado, y ese historial
    manual divergía del real (field report FW-165). Solo se rechaza si la
    última ronda sigue `pending` sin cerrar: no hay overwrite silencioso de un
    review en curso."""
    if not reviewer_id.strip() or not implementer_id.strip() or reviewer_id == implementer_id:
        raise ValueError("reviewer_id debe ser distinto de implementer_id")
    if mode not in REVIEW_MODES:
        raise ValueError(f"modo de review inválido: {mode}. Modos válidos: {', '.join(sorted(REVIEW_MODES))}")
    history = _receipts(state)["reviews"].setdefault(module, [])
    if history and history[-1].get("status") == "pending":
        raise ValueError(
            f"ya hay un review pendiente para '{module}'; complétalo con "
            "record_review_receipt antes de iniciar uno nuevo"
        )
    gate_problems = test_gate_review_problems(state, module)
    if gate_problems:
        raise ValueError(gate_problems[0])
    receipt = {
        "status": "pending",
        "module": module,
        "reviewer_id": reviewer_id,
        "implementer_id": implementer_id,
        "mode": mode,
        "diff_hash": workspace_diff_hash(root),
        "started_at": now_iso,
    }
    history.append(receipt)
    return receipt


def finish_review(
    state: dict,
    root: Path,
    module: str,
    *,
    reviewer_id: str,
    result: str,
    evidence: str,
    now_iso: str,
) -> dict:
    history = _receipts(state)["reviews"].get(module)
    receipt = history[-1] if isinstance(history, list) and history else None
    if not isinstance(receipt, dict) or receipt.get("status") != "pending":
        raise ValueError("no hay review pendiente para completar")
    if reviewer_id != receipt.get("reviewer_id"):
        raise ValueError("reviewer_id no coincide con el review iniciado")
    if result not in {"pass", "fail"} or not evidence.strip():
        raise ValueError("review requiere result pass|fail y evidence")
    current_hash = workspace_diff_hash(root)
    corrected_after_fail = False
    if not diff_hash_matches(receipt.get("diff_hash"), current_hash):
        # FW-298 (TL FW-289): un `fail` no certifica el código, así que sellarlo
        # con el diff ya corregido no exige el mismo hash que un `pass`. Queda
        # anotado el hash actual; la ronda siguiente se abre y se sella aparte.
        if result != "fail":
            raise ValueError("el diff cambió durante el review; reiniciar revisión")
        corrected_after_fail = True
    receipt.update({"status": result, "result": result, "evidence": evidence.strip(), "completed_at": now_iso})
    if corrected_after_fail:
        receipt.update({"corrected_after_fail": True, "diff_hash_at_seal": str(current_hash)})
    return receipt


def invalidate_stale_review(
    state: dict,
    root: Path,
    module: str,
    *,
    approved_by: str,
    reason: str,
    now_iso: str,
) -> dict:
    """Invalidate only a pending review whose workspace hash is stale.

    This is a recovery operation for an interrupted review window. It never
    turns a current review into a new round and records the invalidation as a
    terminal failed historical receipt so the next review must be started and
    sealed independently.
    """
    if approved_by != "builder":
        raise ValueError("invalidate_stale_review requiere approved_by='builder'")
    reason = reason.strip()
    if len(reason) < 20:
        raise ValueError("invalidate_stale_review requiere un reason de >= 20 caracteres")
    history = _receipts(state)["reviews"].get(module)
    receipt = history[-1] if isinstance(history, list) and history else None
    if not isinstance(receipt, dict) or receipt.get("status") != "pending":
        raise ValueError("no hay review pendiente para invalidar")
    current_hash = workspace_diff_hash(root)
    if diff_hash_matches(receipt.get("diff_hash"), current_hash):
        raise ValueError("el review pendiente no está stale; no se puede invalidar")
    receipt.update({
        "status": "fail",
        "result": "fail",
        "evidence": (
            "Review pending stale invalidado por reparación canónica: "
            f"{reason}"
        ),
        "completed_at": now_iso,
        "invalidated": True,
        "invalidated_by": approved_by,
        "invalidation_reason": reason,
        "invalidated_at": now_iso,
    })
    return receipt


def review_problems(state: dict, module: str, current_diff_hash: str | None = None) -> list[str]:
    history = state.get("workflow_receipts", {}).get("reviews", {}).get(module)
    receipt = history[-1] if isinstance(history, list) and history else None
    if not isinstance(receipt, dict):
        return [f"review receipt ausente para '{module}'"]
    problems: list[str] = []
    if receipt.get("status") != "pass" or receipt.get("result") != "pass":
        problems.append("review independiente no está en pass")
    if receipt.get("reviewer_id") == receipt.get("implementer_id"):
        problems.append("reviewer e implementer deben ser distintos")
    if receipt.get("mode") not in REVIEW_MODES:
        problems.append(f"modo de review no reconocido: {receipt.get('mode')}. Modos válidos: {', '.join(sorted(REVIEW_MODES))}")
    if not str(receipt.get("evidence") or "").strip():
        problems.append("review receipt sin evidencia")
    if current_diff_hash is not None and not diff_hash_matches(receipt.get("diff_hash"), current_diff_hash):
        problems.append("review receipt stale: code_diff: el diff cambió después del review; abre una ronda nueva de revisión independiente en /cierre-modulo")
    return problems


# --- FW-250 (5.2 L9): declaración de tests modificados ------------------------
# Un ejecutor podía cambiar un test para poner la suite en verde sin declararlo.
# Si el diff contra su base MODIFICA/BORRA tests existentes y además toca código
# de producción, el execution receipt exige `test_changes`. Clasifica con la
# misma allowlist cerrada de documentación del hash v3 (L2): producción es todo
# lo que no es documentación ni test.
_TEST_NAME_RE = re.compile(r"(^test_.*|.*_test\..*|.*\.spec\..*)$")


def _is_test_path(rel: str) -> bool:
    norm = rel.replace("\\", "/").strip("/")
    parts = norm.split("/")
    return bool(parts) and (
        any(seg in {"tests", "test"} for seg in parts[:-1])
        or bool(_TEST_NAME_RE.match(parts[-1]))
    )


def _is_production_code(rel: str) -> bool:
    return not _is_documentation(rel) and not _is_test_path(rel)


def _git_out(root: Path, *args: str) -> str | None:
    try:
        out = subprocess.run(
            ["git", "-C", str(root), *args], capture_output=True, text=True,
            timeout=30, check=False, stdin=subprocess.DEVNULL,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout.strip() if out.returncode == 0 else None


def _resolve_base(root: Path, base_ref: str | None) -> tuple[str | None, str]:
    """(sha, motivo). Orden: `base_ref` explícito; merge-base con origin/HEAD;
    con `main` o `master` locales. Sin resolver → (None, motivo legible)."""
    if _git_out(root, "rev-parse", "--is-inside-work-tree") != "true":
        return None, "el workspace no es un repositorio git"
    if _git_out(root, "rev-parse", "--verify", "-q", "HEAD") is None:
        return None, "el repositorio no tiene commits"
    if base_ref:
        sha = _git_out(root, "rev-parse", "--verify", "-q", f"{base_ref}^{{commit}}")
        return (sha, "base_ref explícito") if sha else (None, f"base_ref {base_ref!r} no existe")
    for cand in ("origin/HEAD", "main", "master"):
        sha = _git_out(root, "merge-base", "HEAD", cand)
        if sha:
            return sha, f"merge-base con {cand}"
    return None, "no se resolvió una rama base (origin/HEAD, main o master)"


def _changes_vs_base(root: Path, base: str) -> dict[str, str] | None:
    """ruta → estado (A/M/D/R) del árbol de trabajo contra `base`, incluidos los
    ficheros sin seguimiento (como A). Reutiliza el filtro de exclusión del hash."""
    try:
        raw = _git_z(root, "diff", "--name-status", "-M", "-z", base)
        changes: dict[str, str] = {}
        i = 0
        while i < len(raw):
            st = raw[i][:1]
            if st in {"R", "C"} and i + 2 < len(raw):
                changes[raw[i + 2]] = "R"
                if st == "R":
                    changes[raw[i + 1]] = "D"
                i += 3
            elif i + 1 < len(raw):
                changes[raw[i + 1]] = st
                i += 2
            else:
                break
        for p in _git_z(root, "ls-files", "--others", "--exclude-standard", "-z"):
            changes.setdefault(p, "A")
    except (OSError, subprocess.SubprocessError):
        return None
    return {p: s for p, s in changes.items() if not _v2_excluded(p)}


def _clean_test_changes(declared: object) -> list[dict]:
    if declared in (None, ""):
        return []
    if not isinstance(declared, list):
        raise ValueError("test_changes debe ser una lista de {path, reason, authorized_by}")
    clean: list[dict] = []
    for item in declared:
        if not isinstance(item, dict):
            raise ValueError("cada test_changes debe ser {path, reason, authorized_by}")
        entry = {k: str(item.get(k) or "").strip() for k in ("path", "reason", "authorized_by")}
        empty = [k for k, v in entry.items() if not v]
        if empty:
            raise ValueError(
                f"test_changes incompleto ({entry['path'] or '?'}): falta {', '.join(empty)}"
            )
        clean.append(entry)
    return clean


def _base_description(gate: dict) -> str:
    """Contra qué comparó el gate, legible (FW-277)."""
    base = gate.get("base")
    sha = str(base)[:12] if base else ""
    source = gate.get("base_source") or "sin base git"
    return f"{sha} ({source})" if sha else source


def _undeclared_message(module: str, gate: dict, missing: list[str]) -> str:
    prod = gate.get("prod_files") or []
    if prod:
        prod_txt = ", ".join(prod[:3]) + (f" (+{len(prod) - 3} más)" if len(prod) > 3 else "")
    else:
        prod_txt = "sí (el digest de producción cambió desde la aprobación)"
    status = gate.get("test_status", {})
    sin = ", ".join(f"{p} ({status.get(p, 'M')})" for p in missing)
    covered = gate.get("covered_tests") or []
    covered_line = ""
    if isinstance(covered, list) and covered:
        cov_items = [
            f"{item['path']} (por {', '.join(str(m) for m in (item.get('by') or []))})"
            for item in covered
            if isinstance(item, dict) and item.get("path")
        ]
        if cov_items:
            label = "Prueba cubierta" if len(cov_items) == 1 else "Pruebas cubiertas"
            covered_line = f"  {label}: {', '.join(cov_items)}\n"
    aviso = ""
    if gate.get("base_kind") == "merge_base":
        aviso = (
            "  Aviso: la base es un merge-base con la rama principal y puede incluir tests "
            "tocados por OTROS módulos. Si alguno no es de este módulo, no lo declares: "
            "pasa base_ref=<commit de apertura del módulo>.\n"
        )
    decl_snippet = json.dumps(
        [{"path": p, "reason": "<por qué cambia>", "authorized_by": "<quién autorizó>"} for p in missing],
        ensure_ascii=False,
    )
    return (
        f"execution receipt rechazado para '{module}': tests: contra "
        f"{_base_description(gate)} hay tests existentes modificados ({sin}) y código de "
        "producción tocado, y falta declararlo (FW-250).\n"
        f"  Sin declarar: {sin}\n"
        f"{covered_line}"
        f"  Producción tocada: {prod_txt}\n"
        f"{aviso}"
        "Para declarar en bloque, añade al payload de record_execution_receipt:\n"
        f'  "test_changes": {decl_snippet}\n'
        "Si el cambio de test no era necesario, revierte el test en lugar de declararlo."
    )


def _undeclared(gate: dict) -> list[str]:
    declared = {t.get("path") for t in gate.get("test_changes") or [] if isinstance(t, dict)}
    return [p for p in gate.get("modified_tests") or [] if p not in declared]


def _identity_kind(identities: dict[str, str]) -> str:
    """`git` (id de objeto, 40 hex) o `sha256` (64 hex): con git ausente al
    aprobar o al sellar las identidades no son comparables y el mapa se ignora."""
    for value in identities.values():
        return "git" if len(str(value)) == 40 else "sha256"
    return "git"


def _test_baseline(root: Path) -> dict:
    """Base independiente de git (N15). Identidades de contenido de los tests y un
    digest de la producción: no dependen de si el trabajo está commiteado, porque
    `_content_identities` da la misma identidad antes y después del commit."""
    identities = _content_identities(root)
    prod = hashlib.sha256()
    for rel, identity in sorted(identities.items()):
        if _is_production_code(rel):
            for part in (rel, identity):
                prod.update(part.encode("utf-8", "surrogateescape"))
                prod.update(b"\0")
    return {
        "test_identities": {p: i for p, i in sorted(identities.items()) if _is_test_path(p)},
        "prod_digest": prod.hexdigest(),
        "identity_kind": _identity_kind(identities),
    }


def _changes_vs_baseline(root: Path, approval: object) -> tuple[dict[str, str], bool] | None:
    """(ruta → M/D/A de los tests, ¿producción tocada?) contra el mapa guardado al
    aprobar, o None si el recibo es antiguo (sin mapa) o no es comparable.

    N16: si existe test_identities_origin (de una re-aprobación), úsalo como base
    preferente en lugar del test_identities actual."""
    if not isinstance(approval, dict):
        return None
    saved = _approval_identities(root, approval)
    digest = approval.get("prod_digest_origin") or approval.get("prod_digest")
    if not isinstance(saved, dict) or not isinstance(digest, str):
        return None
    now = _test_baseline(root)
    if approval.get("identity_kind", "git") != now["identity_kind"] and saved:
        return None
    current = now["test_identities"]
    changes: dict[str, str] = {}
    for path, identity in saved.items():
        if path not in current:
            changes[path] = "D"
        elif current[path] != identity:
            changes[path] = "M"
    for path in current:
        if path not in saved:
            changes[path] = "A"
    return changes, now["prod_digest"] != digest


def _parse_iso_datetime(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    raw = value.strip()
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(raw)
    except (ValueError, TypeError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _find_coverage_chain(
    state: dict | None,
    module: str,
    approval: dict,
    path: str,
    start_id: str | None,
    target_id: str | None,
) -> list[str] | None:
    if not isinstance(state, dict) or not start_id or start_id == target_id:
        return None
    origin_iso = approval.get("approved_at_origin") or approval.get("approved_at")
    origin_dt = _parse_iso_datetime(origin_iso)
    if origin_dt is None:
        return None
    modules_map = state.get("modules")
    completed_raw = modules_map.get("completed", []) if isinstance(modules_map, dict) else []
    if not isinstance(completed_raw, list):
        return None
    completed = {m for m in completed_raw if isinstance(m, str) and m != module}
    if not completed:
        return None
    executions = state.get("workflow_receipts", {}).get("executions", {})
    if not isinstance(executions, dict):
        return None
    links: list[tuple[datetime, str, str, str | None]] = []
    for other_mod in sorted(completed):
        exec_rec = executions.get(other_mod)
        if not isinstance(exec_rec, dict):
            continue
        rec_dt = _parse_iso_datetime(exec_rec.get("recorded_at"))
        if rec_dt is None or not (rec_dt > origin_dt):
            continue
        other_gate = exec_rec.get("test_gate")
        if not isinstance(other_gate, dict):
            continue
        declared = {
            t.get("path")
            for t in (other_gate.get("test_changes") or [])
            if isinstance(t, dict)
        }
        if path not in declared:
            continue
        sealed = other_gate.get("sealed_changes")
        if not isinstance(sealed, dict):
            continue
        entry = sealed.get(path)
        if not isinstance(entry, dict) or "from" not in entry or "to" not in entry:
            continue
        frm = entry.get("from")
        to = entry.get("to")
        if not isinstance(frm, str) or not frm:
            continue
        if to is not None and (not isinstance(to, str) or not to):
            continue
        links.append((rec_dt, other_mod, frm, to))
    if not links:
        return None
    links.sort(key=lambda item: (item[0], item[1]))
    queue: list[tuple[str | None, list[str], set[str | None]]] = [
        (start_id, [], {start_id})
    ]
    while queue:
        curr_id, chain_mods, visited_ids = queue.pop(0)
        for _dt, mod_id, frm, to in links:
            if frm != curr_id or mod_id in chain_mods:
                continue
            new_chain = chain_mods + [mod_id]
            if to == target_id:
                return new_chain
            if to is not None and to not in visited_ids:
                queue.append((to, new_chain, visited_ids | {to}))
    return None


def _build_test_gate(
    root: Path, module: str, base_ref: str | None, declared: object,
    approval: object = None, state: dict | None = None,
) -> dict:
    test_changes = _clean_test_changes(declared)
    mapped = _changes_vs_baseline(root, approval)
    if not base_ref and mapped is not None:
        return _gate_from_map(root, module, approval, mapped, test_changes, state=state)
    base_kind = "explicit" if base_ref else "merge_base"
    # FW-357: si el recibo cita un mapa que no carga, base_ref no basta (escondería
    # un cambio propio commiteado antes): manda el commit de apertura.
    map_missing = isinstance(approval, dict) and bool(
        approval.get("test_identities_ref") or approval.get("test_identities_origin_ref")
    ) and _approval_identities(root, approval) is None
    head = None
    if (not base_ref or map_missing) and isinstance(approval, dict):
        head = approval.get("head_at_approval_origin") or approval.get("head_at_approval")
    if head and _git_out(root, "rev-parse", "--verify", "-q", f"{head}^{{commit}}"):
        base, why, base_kind = head, "commit de apertura del módulo (aprobación)", "approval_head"
    else:
        base, why = _resolve_base(root, base_ref)
    if map_missing:
        why += "; mapa de identidades no disponible en _project/approval_snapshots/ (¿sin commitear o manipulado?)"
    git_changes = _changes_vs_base(root, base) if base else None
    if git_changes is None and mapped is None:
        return {
            "status": "sin_base",
            "reason": why if base is None else "no se pudo calcular el diff contra la base",
            "requires_declaration": False,
            "test_changes": test_changes,
        }
    changes: dict[str, str] = {}
    prod: list[str] = []
    sources: list[str] = []
    if git_changes is not None:
        sources.append("git")
        changes.update(git_changes)
        prod = sorted(p for p, s in git_changes.items() if _is_production_code(p))
    prod_changed = bool(prod)
    if mapped is not None:
        sources.append("test_identities")
        for path, status in mapped[0].items():
            if status in {"M", "D"} or path not in changes:
                changes[path] = status
        prod_changed = prod_changed or mapped[1]
    modified = sorted(p for p, s in changes.items() if _is_test_path(p) and s in {"M", "D", "R"})
    covered_tests: list[dict[str, object]] = []
    only_map = [p for p in modified if mapped is not None and p not in (git_changes or {})]
    if only_map:  # PA-FW-013: lo que git no ve contra la base se atribuye como sin ella
        saved = _approval_identities(root, approval) or {}
        now = _test_baseline(root)["test_identities"]
        for p in only_map:
            chain = _find_coverage_chain(state, module, approval, p, saved.get(p), now.get(p))
            if chain:
                covered_tests.append({"path": p, "by": chain})
                modified.remove(p)
    gate = {
        "status": "ok",
        "base": base,
        "base_source": why if base else "sin base git",
        "base_kind": base_kind if base else "none",
        "base_is_head": bool(base) and base == _git_out(root, "rev-parse", "HEAD"),
        "identity_source": "+".join(sources),
        "modified_tests": modified,
        "prod_files": prod[:20],
        "prod_changed": prod_changed,
        "test_status": {p: changes[p] for p in modified},
        "requires_declaration": bool(modified and prod_changed),
        "test_changes": test_changes,
    }
    if covered_tests:
        gate["covered_tests"] = covered_tests
    if map_missing:
        gate["identity_map_missing"] = True
    if gate["requires_declaration"]:
        missing = _undeclared(gate)
        if missing:
            raise ValueError(_undeclared_message(module, gate, missing))
    return gate


def _gate_from_map(
    root: Path, module: str, approval: dict, mapped: tuple[dict[str, str], bool],
    test_changes: list[dict], state: dict | None = None,
) -> dict:
    """FW-277: con mapa de identidades de la aprobación, el mapa manda.

    Hasta 5.2.1 se SUMABA el diff de git contra el merge-base con main, que
    arrastraba tests tocados por otros módulos de la misma rama (TL FW-255/259,
    PA FW-139: hasta 19 tests ajenos). El mapa ya ve todo lo que este módulo
    cambió desde su aprobación, commiteado o no (N15). Git solo aporta los
    NOMBRES de producción para el mensaje, contra el commit de apertura."""
    changes, prod_changed = mapped
    head = approval.get("head_at_approval_origin") or approval.get("head_at_approval")
    prod: list[str] = []
    if head and _git_out(root, "rev-parse", "--verify", "-q", f"{head}^{{commit}}"):
        git_changes = _changes_vs_base(root, head) or {}
        prod = sorted(p for p in git_changes if _is_production_code(p))
    modified_raw = sorted(p for p, s in changes.items() if _is_test_path(p) and s in {"M", "D"})
    saved = _approval_identities(root, approval) or {}
    current_identities = _test_baseline(root)["test_identities"]
    modified: list[str] = []
    covered_tests: list[dict[str, object]] = []
    for p in modified_raw:
        chain = _find_coverage_chain(
            state, module, approval, p, saved.get(p), current_identities.get(p)
        )
        if chain:
            covered_tests.append({"path": p, "by": chain})
        else:
            modified.append(p)
    declared_paths = {t.get("path") for t in test_changes if isinstance(t, dict)}
    sealed_changes = {
        p: {"from": saved.get(p), "to": current_identities.get(p)}
        for p in modified
        if p in declared_paths
    }
    approved_at = str(approval.get("approved_at") or "")[:10]
    gate = {
        "status": "ok",
        "base": head or None,
        "base_source": f"mapa de tests de la aprobación{' del ' + approved_at if approved_at else ''}",
        "base_kind": "approval_map",
        "base_is_head": False,
        "identity_source": "test_identities",
        "modified_tests": modified,
        "covered_tests": covered_tests,
        "sealed_changes": sealed_changes,
        "prod_files": prod[:20],
        "prod_changed": prod_changed,
        "test_status": {p: changes[p] for p in modified},
        "requires_declaration": bool(modified and prod_changed),
        "test_changes": test_changes,
    }
    if gate["requires_declaration"]:
        missing = _undeclared(gate)
        if missing:
            raise ValueError(_undeclared_message(module, gate, missing))
    return gate


def test_gate_review_problems(state: dict, module: str) -> list[str]:
    """Problemas del gate FW-250 sobre el execution receipt. Un receipt sin
    bloque `test_gate` (sellado antes de L9) es legado y no se juzga (§2.10)."""
    receipt = state.get("workflow_receipts", {}).get("executions", {}).get(module)
    gate = receipt.get("test_gate") if isinstance(receipt, dict) else None
    if not isinstance(gate, dict) or not gate.get("requires_declaration"):
        return []
    missing = _undeclared(gate)
    return [_undeclared_message(module, gate, missing)] if missing else []


def test_gate_warnings(state: dict, module: str) -> list[str]:
    """WARN visible cuando el gate no pudo verificar (nunca silencioso)."""
    receipt = state.get("workflow_receipts", {}).get("executions", {}).get(module)
    gate = receipt.get("test_gate") if isinstance(receipt, dict) else None
    if isinstance(gate, dict) and gate.get("status") == "sin_base":
        return [
            f"WARN FW-250: el gate de tests modificados no pudo verificar '{module}': "
            f"{gate.get('reason')}. Pasa base_ref para que compare contra una base."
        ]
    if isinstance(gate, dict) and gate.get("identity_map_missing"):
        return [
            f"WARN FW-357: el mapa de tests de la aprobación de '{module}' no se pudo leer y el gate "
            "comparó contra el commit de apertura. Restaura _project/approval_snapshots/ desde git "
            "o pide una aprobación nueva."
        ]
    return []


def record_execution(
    state: dict,
    root: Path,
    module: str,
    *,
    kind: str,
    command: str,
    evidence: str,
    # Por defecto ausente: solo `ok_automatizada` lo exige, y la prosa del paso
    # 4.bis dice literalmente que en `ok_manual` se omite el campo.
    exit_code: object = None,
    now_iso: str,
    test_changes: object = None,
    base_ref: str | None = None,
    tests_run: object = None,
) -> dict:
    """FW-144 — recibo del EJECUTABLE GATE (cierre-modulo paso 3.8).

    Hasta 4.21.0 ese gate era el único sin rastro: `verificar-ejecutable`
    declaraba `verificacion: ok_automatizada` en el turno y nadie lo contrastaba
    al cerrar. Un modelo que narra el smoke en vez de ejecutarlo producía
    exactamente el mismo artefacto que uno que lo ejecuta — y en Example Service los
    tres módulos de phase_05 cerraron así sobre un producto que no arranca.

    El receipt fija el `diff_hash` del momento, igual que el de review: si el
    código cambia después, la verificación deja de valer.
    """
    if kind not in EXECUTION_KINDS:
        raise ValueError(
            f"kind de verificación inválido: {kind!r} (uno de {sorted(EXECUTION_KINDS)})"
        )
    evidence = (evidence or "").strip()
    if len(evidence) < MIN_EXECUTION_EVIDENCE:
        raise ValueError(
            f"la verificación ejecutable requiere evidence de >= {MIN_EXECUTION_EVIDENCE} "
            "caracteres (comando + salida real, o la razón del no_aplica)"
        )
    command = (command or "").strip()
    if kind != "no_aplica" and not command:
        raise ValueError(
            "una verificación ok_automatizada/ok_manual requiere el 'command' que "
            "se ejecutó realmente (FW-144: sin comando no hay ejecución que sellar)"
        )
    # `exit_code` se NORMALIZA a int aquí. Guardarlo tal cual dejaba pasar el
    # string "1": `execution_problems` comprueba `isinstance(code, int)`, así que
    # un smoke fallido sellado como texto habría cerrado el módulo — el mismo
    # patrón "parece verificación, no lo es" que motiva todo FW-144.
    if exit_code is not None:
        if isinstance(exit_code, bool) or not isinstance(exit_code, (int, str)):
            raise ValueError(f"exit_code debe ser un entero; recibido {exit_code!r}")
        if isinstance(exit_code, str):
            texto = exit_code.strip()
            if not re.fullmatch(r"-?\d+", texto):
                raise ValueError(f"exit_code debe ser un entero; recibido {exit_code!r}")
            exit_code = int(texto)
    elif kind == "ok_automatizada":
        raise ValueError(
            "una verificación ok_automatizada requiere 'exit_code' (el código de "
            "salida real del comando)"
        )
    # `ok_manual` NO lo exige a propósito: un fallback confirmado por el builder
    # no tiene código de salida, y obligar a rellenar un 0 sería enseñar a
    # inventar un valor para satisfacer el gate — exactamente lo que FW-144
    # existe para impedir. Ahí la evidencia es la confirmación, no el código.
    if tests_run is not None and (
        isinstance(tests_run, bool) or not isinstance(tests_run, int) or tests_run < 0
    ):
        raise ValueError(f"tests_run debe ser un entero >= 0; recibido {tests_run!r}")
    receipt = {
        "status": "recorded",
        "module": module,
        "kind": kind,
        "command": command,
        "exit_code": exit_code,
        "evidence": evidence,
        "diff_hash": workspace_diff_hash(root),
        "recorded_at": now_iso,
        "test_gate": _build_test_gate(
            root, module, base_ref, test_changes,
            _receipts(state)["approvals"].get(module),
            state=state,
        ),
    }
    if tests_run is not None:  # FW-299 (D4-A): opcional; 0 es FAIL al cerrar
        receipt["tests_run"] = tests_run
    _receipts(state)["executions"][module] = receipt
    return receipt


# FW-299: una herramienta de verificación que no está instalada no es un PASS.
_MISSING_TOOL_RE = re.compile(
    r"command not found|No module named|is not recognized as an internal or external command"
)


def execution_problems(
    state: dict, module: str, current_diff_hash: str | None = None
) -> list[str]:
    """Razones por las que el EJECUTABLE GATE no está sellado para `module`."""
    receipt = state.get("workflow_receipts", {}).get("executions", {}).get(module)
    if not isinstance(receipt, dict):
        return [
            f"execution receipt ausente para '{module}': ejecutar el paso 3.8 "
            "(verificar-ejecutable) y sellarlo con mutate_state "
            "record_execution_receipt antes de cerrar (FW-144)"
        ]
    problems: list[str] = []
    kind = receipt.get("kind")
    if kind not in EXECUTION_KINDS:
        problems.append(f"kind de verificación no reconocido: {kind!r}")
    if len(str(receipt.get("evidence") or "").strip()) < MIN_EXECUTION_EVIDENCE:
        problems.append("execution receipt sin evidencia sustantiva")
    if kind in {"ok_automatizada", "ok_manual"}:
        if not str(receipt.get("command") or "").strip():
            problems.append("execution receipt sin el comando ejecutado")
        code = receipt.get("exit_code")
        if isinstance(code, int) and code != 0:
            problems.append(
                f"la verificación ejecutable terminó con exit_code {code}: "
                "un smoke que falla no cierra el módulo"
            )
    if kind == "ok_automatizada":
        missing_tool = _MISSING_TOOL_RE.search(str(receipt.get("evidence") or ""))
        if missing_tool:
            problems.append(
                f"herramienta de verificación ausente: «{missing_tool.group(0)}» en la evidencia; "
                "instálala y repite el paso 3.8 (FW-299)"
            )
        if receipt.get("tests_run") == 0 and not isinstance(receipt.get("tests_run"), bool):
            problems.append("la verificación no ejecutó ningún test (tests_run 0): repite el paso 3.8 (FW-299)")
    if kind == "no_aplica":
        # Mismo contrato que la prosa del paso 3.8: 'no_aplica' solo vale para
        # doc_config puro. Si el módulo declara data_model o api_contract, tiene
        # superficie ejecutable y debe demostrarla.
        todas = state.get("module_specs")
        specs = todas.get(module) if isinstance(todas, dict) else None
        declaradas = [
            k for k in ("data_model", "api_contract", "integration_real")
            if isinstance(specs, dict) and specs.get(k) not in (None, "n/a")
        ]
        if declaradas:
            problems.append(
                f"verificación 'no_aplica' con module_specs {declaradas} distintas de "
                "'n/a': solo un módulo doc_config puro puede cerrarse sin evidencia "
                "ejecutable (paso 3.8)"
            )
    if current_diff_hash is not None and not diff_hash_matches(receipt.get("diff_hash"), current_diff_hash):
        problems.append(
            "execution receipt stale: el diff cambió después de la verificación; "
            "re-ejecutar el paso 3.8 sobre el código actual"
        )
    return problems


CHECKPOINT_KINDS = {"domain_sweep", "expert_audit"}
MIN_CHECKPOINT_EVIDENCE = MIN_EXECUTION_EVIDENCE  # mismo umbral, no se inventa uno nuevo


def record_checkpoint(
    state: dict,
    module: str,
    *,
    kind: str,
    scope_id: str,
    evidence: str,
    reference: str,
    recorded_by: str,
    now_iso: str,
) -> dict:
    """FW-134 — sella una obligación endurecida en prosa (FW-131 barrido de
    sub-dominios, FW-132 auditoría de experto) con un rastro mecánico
    verificable. Mismo idioma que record_harvest: determinista del payload,
    sin diff_hash — la obligación no se invalida por un commit posterior de
    código, se re-satisface en la próxima invocación del mismo workflow.

    `module` es solo trazabilidad de quién invocó; `scope_id` es la clave real
    bajo la que se guarda (normalmente el mismo module_id, pero deja la puerta
    abierta a un scope distinto sin acoplar el esquema a esa igualdad)."""
    if kind not in CHECKPOINT_KINDS:
        raise ValueError(f"kind de checkpoint inválido: {kind!r} (uno de {sorted(CHECKPOINT_KINDS)})")
    scope_id = (scope_id or "").strip()
    if not scope_id:
        raise ValueError("record_checkpoint requiere 'scope_id' no vacío")
    evidence = (evidence or "").strip()
    if len(evidence) < MIN_CHECKPOINT_EVIDENCE:
        raise ValueError(
            f"record_checkpoint requiere 'evidence' de >= {MIN_CHECKPOINT_EVIDENCE} caracteres"
        )
    reference = (reference or "").strip()
    if not reference:
        raise ValueError(
            "record_checkpoint requiere 'reference' (fila de 09_domain_profile.md "
            "o cita del experto/AC correspondiente) — anti-fabricación: sin cita "
            "no hay checkpoint"
        )
    receipt = {
        "kind": kind,
        "scope_id": scope_id,
        "evidence": evidence,
        "reference": reference,
        "recorded_by": recorded_by or "agent",
        "recorded_at": now_iso,
    }
    _receipts(state).setdefault("checkpoints", {}).setdefault(kind, {})[scope_id] = receipt
    return receipt


def grandfather_checkpoints(
    state: dict,
    *,
    modules: list[str],
    from_version: str,
    reason: str,
    now_iso: str,
) -> list[str]:
    """FW-221 — sella domain_sweep/expert_audit para módulos tier_2 que ya
    estaban en modules.completed ANTES de que el proyecto tuviera acceso al
    gate FW-134 (introducido en 4.37.0). Solo el migrador de compatibilidad
    invoca esto (ver state_core.mutate 'grandfather_checkpoint_gate'); no es
    un checkpoint real, es la constancia explícita de que exigirlo
    retroactivamente no tiene remedio honesto — el barrido/auditoría que
    hubiera producido esa evidencia nunca pudo ejecutarse porque el paso no
    existía todavía.

    Devuelve la lista de 'kind:module' efectivamente sellados (idempotente:
    un módulo ya sellado, real o previamente grandfathered, no se toca)."""
    work_tiers = state.get("work_tiers", {})
    if not isinstance(work_tiers, dict):
        work_tiers = {}
    sealed: list[str] = []
    for module in modules:
        entry = work_tiers.get(module, {})
        if not (isinstance(entry, dict) and entry.get("tier") == "tier_2"):
            continue
        for kind in CHECKPOINT_KINDS:
            bucket = _receipts(state).setdefault("checkpoints", {}).setdefault(kind, {})
            if module in bucket:
                continue
            bucket[module] = {
                "kind": kind,
                "scope_id": module,
                "grandfathered": True,
                "grandfathered_from_version": from_version,
                "evidence": reason,
                "reference": "migrate_checkpoint_gate_grandfather.py (FW-134/FW-221)",
                "recorded_by": "framework_upgrade_migration",
                "recorded_at": now_iso,
            }
            sealed.append(f"{kind}:{module}")
    return sealed


def checkpoint_problems(state: dict, kind: str, scope_id: str) -> list[str]:
    """Razones por las que el checkpoint `kind` de `scope_id` no está sellado."""
    receipt = (
        state.get("workflow_receipts", {}).get("checkpoints", {}).get(kind, {}).get(scope_id)
    )
    if not isinstance(receipt, dict):
        return [
            f"checkpoint '{kind}' ausente para '{scope_id}': ejecutar el barrido/"
            "auditoría correspondiente y sellarlo con mutate_state record_checkpoint "
            "antes de continuar (FW-134)"
        ]
    problems: list[str] = []
    if not str(receipt.get("evidence") or "").strip():
        problems.append(f"checkpoint '{kind}' de '{scope_id}' sin evidencia")
    if not str(receipt.get("reference") or "").strip():
        problems.append(f"checkpoint '{kind}' de '{scope_id}' sin referencia citable")
    return problems


def _split_pinned_dependency(spec: str) -> tuple[str, str] | None:
    spec = spec.strip()
    match = re.fullmatch(r"([A-Za-z0-9_.-]+)==([A-Za-z0-9_.+-]+)", spec)
    if match:
        return match.group(1).lower().replace("_", "-"), match.group(2)
    match = re.fullmatch(r"(@?[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)?)@([0-9][A-Za-z0-9_.+-]*)", spec)
    if match:
        return match.group(1).lower(), match.group(2)
    return None


def _requirements_file_has_pin(path: Path, name: str, version: str) -> bool:
    for raw in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        candidate = raw.split("#", 1)[0].split(";", 1)[0].strip()
        parsed = _split_pinned_dependency(candidate)
        if parsed == (name, version):
            return True
    return False


def _package_json_has_pin(path: Path, name: str, version: str) -> bool:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    for section in ("dependencies", "devDependencies", "optionalDependencies", "peerDependencies"):
        declared = data.get(section, {})
        if isinstance(declared, dict) and str(declared.get(name, "")) == version:
            return True
    return False


def _pyproject_has_pin(path: Path, name: str, version: str) -> bool:
    text = path.read_text(encoding="utf-8", errors="ignore")
    section = ""
    dependency_array_open = False

    def quoted_pin(fragment: str) -> bool:
        for quoted in re.findall(r'["\']([^"\']+)["\']', fragment):
            parsed = _split_pinned_dependency(quoted.split(";", 1)[0].strip())
            if parsed == (name, version):
                return True
        return False

    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip().rstrip(",")
        header = re.fullmatch(r"\[([^]]+)\]", line)
        if header:
            section = header.group(1).strip().lower()
            dependency_array_open = False
            continue

        poetry_dependencies = (
            section == "tool.poetry.dependencies"
            or bool(re.fullmatch(r"tool\.poetry\.group\.[^.]+\.dependencies", section))
        )
        if poetry_dependencies:
            assignment = re.fullmatch(
                r'["\']?([A-Za-z0-9_.-]+)["\']?\s*=\s*["\']([^"\']+)["\']',
                line,
            )
            if assignment:
                key = assignment.group(1).lower().replace("_", "-")
                if key == name and assignment.group(2) == version:
                    return True
            continue

        array_section = section in {
            "project", "project.optional-dependencies", "dependency-groups"
        }
        if not array_section:
            continue
        if dependency_array_open:
            if quoted_pin(line):
                return True
            if "]" in line:
                dependency_array_open = False
            continue
        assignment = re.match(r"^([A-Za-z0-9_.-]+)\s*=\s*\[(.*)$", line)
        if not assignment:
            continue
        if section == "project" and assignment.group(1) != "dependencies":
            continue
        remainder = assignment.group(2)
        if quoted_pin(remainder):
            return True
        dependency_array_open = "]" not in remainder
    return False


def _package_lock_has_pin(path: Path, name: str, version: str) -> bool:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    packages = data.get("packages", {})
    installed = packages.get(f"node_modules/{name}", {}) if isinstance(packages, dict) else {}
    if isinstance(installed, dict) and str(installed.get("version", "")) == version:
        return True
    dependencies = data.get("dependencies", {})
    legacy = dependencies.get(name, {}) if isinstance(dependencies, dict) else {}
    return isinstance(legacy, dict) and str(legacy.get("version", "")) == version


def _toml_lock_has_pin(path: Path, name: str, version: str) -> bool:
    text = path.read_text(encoding="utf-8", errors="ignore")
    for block in re.split(r"(?m)^\s*\[\[package\]\]\s*$", text)[1:]:
        package = re.search(r'(?m)^\s*name\s*=\s*["\']([^"\']+)["\']\s*$', block)
        pinned = re.search(r'(?m)^\s*version\s*=\s*["\']([^"\']+)["\']\s*$', block)
        if not package or not pinned:
            continue
        package_name = package.group(1).lower().replace("_", "-")
        if package_name == name and pinned.group(1) == version:
            return True
    return False


def _yaml_or_yarn_lock_has_pin(path: Path, name: str, version: str) -> bool:
    text = path.read_text(encoding="utf-8", errors="ignore")
    direct_key = re.compile(
        rf'(?m)^\s*["\']?/?{re.escape(name)}@(?:npm:)?{re.escape(version)}'
        r'(?:\([^\n)]*\))?["\']?:\s*$'
    )
    return bool(direct_key.search(text))


def _manifest_has_exact_pin(path: Path, name: str, version: str) -> bool:
    if path.name == "requirements.txt":
        return _requirements_file_has_pin(path, name, version)
    if path.name == "package.json":
        return _package_json_has_pin(path, name, version)
    return _pyproject_has_pin(path, name, version)


def _lock_has_exact_pin(path: Path, name: str, version: str) -> bool:
    if path.name == "requirements.lock":
        return _requirements_file_has_pin(path, name, version)
    if path.name == "package-lock.json":
        return _package_lock_has_pin(path, name, version)
    if path.name in {"uv.lock", "poetry.lock"}:
        return _toml_lock_has_pin(path, name, version)
    return _yaml_or_yarn_lock_has_pin(path, name, version)


def dependency_bootstrap_problems(
    root: Path, state: dict, dependency: str, *, target: str = "workspace"
) -> list[str]:
    problems: list[str] = []
    if target not in {"workspace", ".venv", "local"}:
        problems.append("bootstrap solo permitido dentro del workspace")
    if dependency not in state.get("dependencies", []):
        problems.append("dependencia ausente de 03_project_state.json.dependencies")
    parsed = _split_pinned_dependency(dependency)
    if not parsed:
        problems.append("dependencia no fijada a versión exacta")
        return problems
    name, version = parsed
    is_python = "==" in dependency
    if is_python:
        manifests = [root / name for name in PYTHON_MANIFESTS]
        locks = [root / name for name in PYTHON_LOCKFILES]
    else:
        manifests = [root / "package.json"]
        locks = [root / "package-lock.json", root / "pnpm-lock.yaml", root / "yarn.lock"]
    manifest_paths = [p for p in manifests if p.exists()]
    lock_paths = [p for p in locks if p.exists()]
    if not manifest_paths or not any(
        _manifest_has_exact_pin(path, name, version) for path in manifest_paths
    ):
        problems.append("manifest no contiene la dependencia fijada")
    # FW-084 / FW-010 pt2: si el proyecto Python no declara NINGÚN lockfile
    # (requirements.lock/uv.lock/poetry.lock), el pin exacto en el manifest
    # (requirements.txt/pyproject.toml) ya alcanza — exigir un lockfile
    # inexistente bloqueaba Botón y TL sin que hubiera nada que pudiera
    # coincidir. El ecosistema JS sigue exigiendo lockfile siempre (npm/pnpm/
    # yarn no tienen convención sin lockfile); si SÍ hay lockfile(s) en
    # cualquier ecosistema, la coincidencia sigue siendo obligatoria (sin
    # ablandar).
    lockfile_required = bool(lock_paths) or not is_python
    if lockfile_required and not any(
        _lock_has_exact_pin(path, name, version) for path in lock_paths
    ):
        problems.append("lockfile no coincide con la dependencia aprobada")
    return problems


def main(argv: list[str]) -> int:
    # S3 (TL FW-008 residuo): subcomando `hash` — imprime el diff_hash puro
    # de ROOT y sale 0. No requiere 03_project_state.json (workspace_diff_hash
    # no lee estado); un subproceso `python3 workflow_receipts.py hash`
    # SIEMPRE importa este módulo desde disco, así que es inmune a que un
    # proceso servidor de larga vida (state_manager.py) tenga en memoria una
    # versión vieja de este archivo. Ver workspace_diff_hash_fresh().
    if len(argv) >= 2 and argv[1] == "hash":
        print(_compute_workspace_content_hash(ROOT))
        return 0
    if len(argv) < 3 or argv[1] not in {"approval", "review", "dependency", "execution"}:
        print("uso: workflow_receipts.py hash | approval|review|execution|dependency <módulo|dependencia>")
        return 2
    # Un validador que lee estado o proyecciones mientras hay una publicación
    # multiarchivo abierta emite un veredicto sobre una vista a medio escribir.
    # Recupera la transacción pendiente antes de mirar nada (FW-174).
    try:
        import state_core as _state_core
        _state_core.StateManager.ensure_transaction_clean()
    except (ImportError, OSError, ValueError, RuntimeError) as exc:
        print(f"TRANSACCIÓN PENDIENTE: no se puede leer el estado canónico: {exc}")
        return 2
    state = json.loads(STATE.read_text(encoding="utf-8"))
    if argv[1] == "approval":
        problems = approval_problems(ROOT, state, argv[2])
    elif argv[1] == "review":
        problems = review_problems(state, argv[2], workspace_diff_hash(ROOT))
    elif argv[1] == "execution":
        problems = execution_problems(state, argv[2], workspace_diff_hash(ROOT))
    else:
        target = argv[3] if len(argv) > 3 else "workspace"
        problems = dependency_bootstrap_problems(ROOT, state, argv[2], target=target)
    if problems:
        print("RECEIPT GATE: FAIL")
        for problem in problems:
            print(f"- {problem}")
        return 1
    print("RECEIPT GATE: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
