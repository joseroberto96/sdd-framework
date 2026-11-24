#!/usr/bin/env python3
"""Validate Framework v3 structure, state, adapter sync and hygiene."""

from __future__ import annotations

import json
import math
import os
import re
import shutil
import subprocess
import sys
import unicodedata
from pathlib import Path
from typing import Any

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python 3.11+ es el runtime soportado
    tomllib = None


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))
from _transitions import VALID_MODULE_STATUSES  # noqa: E402
import codex_profile  # noqa: E402
import detect_stale_field_report  # noqa: E402
import projections  # noqa: E402
import turn_close  # noqa: E402
from validation_summary import summarize_result  # noqa: E402
GENERATED = "<!-- GENERATED: framework wrapper; edit manifest/source, then run sync_claude_commands.py -->"
# Marcador legado (pre-4.3.1): reconocerlo evita huérfanos invisibles en upgrades.
GENERATED_LEGACY = "<!-- GENERATED: framework v3 wrapper; edit manifest/source, then run sync_claude_commands.py -->"
VALID_WORK_TIERS = {"tier_0", "tier_1", "tier_2"}
VALID_CAPABILITY_TIERS = {"strong", "standard", "limited"}
VALID_FRAMEWORK_MODES = {"template", "initialized"}
VALID_SPEC_STATUS = {"required", "n/a", "done"}
VALID_SPEC_CLASSIFIER = {"scope-siguiente", "enriquecer-ticket", "migrar-proyecto", "manual"}
VALID_CLOSURE_REASONS = {
    "critical_bug_exists",
    "clar_pending",
    "ac_incomplete",
    "ac_evidence_missing",
    "module_specs_missing",
    "data_model_missing",
    "api_contract_missing",
    "data_safety_missing",
    "domain_sweep_missing",
    "expert_audit_missing",
}
VALID_ADAPTER_STATUS = {"verified", "experimental"}
ADAPTER_STATUS_RE = re.compile(r"\*\*Soporte:\*\*\s*(verified|experimental)\b")
# FW-128 (Tarea 2.b): la disponibilidad de subagentes read-only pasa de ser una
# evaluación del modelo en el momento a un hecho declarado en el adapter.
ADAPTER_SUBAGENTS_RE = re.compile(
    r"\*\*Subagentes read-only:\*\*\s*(sí|no|según-versión-verificar-una-vez)\b"
)
HARNESS_ADAPTER_STEMS = {"claude-code", "codex", "opencode", "antigravity", "generic"}
FLOW_LEGACY_MARKERS = ("Siguiente comando:", "Prompt sugerido:")
FLOW_EMBEDDED_NEXT_COMMAND_RE = re.compile(
    r"/[^\s`\"'–—]+(?:\s+(?:fase|--paralelo))?"
    r"(?:\s+[-–—](?!-)\s*|[–—](?!-)\s*)\S"
)
OPENAPI_INPUT_SCHEMA_RE = re.compile(r"(?:Input|Create|Update)$")
SERVER_GENERATED_FIELDS = {
    "id",
    "created_at",
    "updated_at",
    "deleted_at",
    "timestamp",
    "fecha_creacion",
    "fecha_actualizacion",
    "fecha_anadido",
    "precio_actual",
    "estado_tesis",
}
GITIGNORE_REQUIRED_PRIVATE_PATTERNS = {
    "*.db-wal",
    "*.db-shm",
    "investor-profile.md",
    "tax-context.md",
    "portfolio.md",
    "watchlist.md",
    "tasks/",
    "outputs/",
    "alerts/",
    "sources/",
    "_scratch/referentes/",
}
SECONDARY_PLACEHOLDER_FILES = [
    "_project/02_technical_plan/data_model.md",
    "_project/02_technical_plan/api_contract.yaml",
    "_project/03_arch_decisions.md",
    "_project/03_contracts.md",
    "_project/04_scope_archive.md",
    "_project/05_test_history.md",
    "_project/06_bug_fixes.md",
    "_project/07_pending_clarifications.md",
    "_project/09_domain_profile.md",
    "_project/12_design_profile.md",
]
INVARIANT_DRIFT_SCAN_FILES = [
    "_project/02_technical_plan/data_model.md",
    "_project/02_technical_plan/api_contract.yaml",
    "_project/03_contracts.md",
    "_project/03_arch_decisions.md",
    "_project/04_active_scope.md",
    "_project/08_module_acceptance.md",
    "_project/10_arch_constraints.md",
]
CLARIFICATION_STRUCTURED_FIELDS = [
    "blocks_start",
    "blocks_implementation",
    "blocks_closure",
    "blocked_scope",
    "blocking_condition",
]
STATE_NARRATIVE_FILES = [
    "_project/04_active_scope.md",
    "_project/core.md",
    "_project/08_module_acceptance.md",
    "_project/16_release_profile.md",
]
BAD_MCP_VERSION_RE = re.compile(r"@(?:latest|next|beta)\b", re.IGNORECASE)
MCP_SEMVER_PIN_RE = re.compile(r"@\d+\.\d+\.\d+(?:[-+][A-Za-z0-9_.-]+)?\b")
COMMIT_PIN_RE = re.compile(r"\b[0-9a-f]{40}\b", re.IGNORECASE)
LOCAL_FRAMEWORK_SERVER = "state_manager"
REQUIREMENT_NAME_RE = re.compile(r"^\s*([A-Za-z0-9_.-]+)(?:\[[^\]]+\])?\s*(?:[<>=!~;@#\s]|$)")
PROTECTED_TERM_RE = re.compile(r"(?m)^-\s*\*\*([^*\[\]\n]+)\*\*\s*:")
CLAR_BLOCK_RE = re.compile(r"(?ms)^###\s+(CLAR-\d+)\b.*?(?=^###\s+CLAR-\d+\b|\Z)")
MODULE_STATUS_CITATION_RE = re.compile(
    rf"module_statuses\.([A-Za-z0-9_.-]+)\s*[:=]\s*({'|'.join(sorted(VALID_MODULE_STATUSES))})"
)
INVARIANT_VARIANT_ALLOWLIST_RE = re.compile(
    r"(?i)(alias\s+interno|internal\s+alias|alias\s+python|pydantic|compatibilidad|mapeo)"
)
PLACEHOLDER_RE = re.compile(
    r"\[(?:NOMBRE_PROYECTO|PROJECT_NAME|COMPLETAR[^\]]*|Describe[^\]]*|Define[^\]]*|fecha|Tarea[^\]]*|Nombre[^\]]*|ej:[^\]]*|URL|pendiente)\]",
    re.IGNORECASE,
)
# Secret-scan guards (FW-040): files above this size, or detected as binary,
# are skipped. Credentials live in small text/config files; reading multi-MB
# blobs (ML/audio models like Whisper MLX) hangs the scan >60s and trains
# --no-verify. 2 MB is well above any legitimate source/config file.
SECRET_SCAN_MAX_BYTES = 2_000_000


def _is_probably_binary(sample: bytes) -> bool:
    """A NUL byte in the leading bytes marks the file as binary (not scannable text)."""
    return b"\x00" in sample


# Secret detection (5.3.1 L2.1, TL-FW-321, PA-FW-012 y su familia): regla
# invertida. Se marca (a) un formato de credencial conocido, (b) la asignación
# entrecomillada de la regla 1 o (c) un valor de alta entropía asignado a una
# clave que nombra un secreto. Un hexadecimal suelto (huella, commit,
# identificador derivado de hash) no es un secreto, sin excepción por caso.
_SECRET_ASSIGNMENT_PATTERN = (
    # 1) Generic assignments: key/secret/token/password = "..." with ≥ 12 chars
    r"(api[_-]?key|secret|token|password|private[_-]?key|access[_-]?key)\s*[:=]\s*['\"][^'\"\s\[\]{}<>]{12,}['\"]"
)
_SECRET_FORMATTED_PATTERN = (
    # 2) PEM blocks. Exige los cinco guiones de la cabecera real: la prosa que
    # CITA el patrón («cita `BEGIN PRIVATE KEY` como ejemplo») no se marca.
    r"-----BEGIN [A-Z ]*PRIVATE KEY"
    # 3) AWS Access Key ID
    r"|\bAKIA[0-9A-Z]{16}\b"
    # 4) AWS Session/Temporary tokens
    r"|\bASIA[0-9A-Z]{16}\b"
    # 5) GitHub tokens (PAT, OAuth, server, refresh, user-to-server)
    r"|\bgh[pousr]_[A-Za-z0-9]{36,255}\b"
    # 6) GitHub fine-grained PAT
    r"|\bgithub_pat_[A-Za-z0-9_]{82}\b"
    # 7) Stripe live keys
    r"|\b(sk|pk|rk)_live_[A-Za-z0-9]{24,}\b"
    # 8) Slack tokens
    r"|\bxox[abprs]-[A-Za-z0-9-]{10,}\b"
    # 9) OpenAI keys
    r"|\bsk-(?:proj-)?[A-Za-z0-9_-]{20,}\b"
    # 10) Anthropic keys
    r"|\bsk-ant-(?:api|admin)[0-9]{2}-[A-Za-z0-9_-]{20,}\b"
    # 11) Google API keys
    r"|\bAIza[0-9A-Za-z_-]{35}\b"
    # 12) JWT (header.payload.signature, base64url)
    r"|\beyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b"
    # 14) Credentials in connection URLs: scheme://user:password@host (N9)
    # Excludes passwords with placeholders (:password@, :<pass>@, :***@, :${VAR}@)
    r"|\b[a-zA-Z][a-zA-Z0-9+.-]{0,31}://[^/\s:@]+:(?!(?:password|pass|placeholder|contrase[ñn]a|clave|<[^>]+>|\[[^\]]+\]|\$\{[^}]+\}|\$[A-Za-z0-9_]+|\*+|xxxx+|0000+)@)[^/\s:@]+@[a-zA-Z0-9_.-]+"
)
SECRET_RE = re.compile(
    "|".join((_SECRET_ASSIGNMENT_PATTERN, _SECRET_FORMATTED_PATTERN)),
    re.IGNORECASE,  # como el `(?i)` inicial de antes: aplica a todas las reglas
)
_FORMATTED_SECRET_RE = re.compile(_SECRET_FORMATTED_PATTERN, re.IGNORECASE)

# Marcador explícito de valor ficticio: lo único que tolera una asignación
# (regla 1) o un valor con clave; una credencial con formato no se tolera nunca.
SECRET_ASSIGNMENT_PLACEHOLDER_RE = re.compile(
    r"(?i)(example|ejemplo|placeholder|<your[_-]?|fake|dummy|falso|ficticio"
    r"|prueba|ensayo|test[_-]?key)"
)
# Marcadores que solo cuentan DENTRO del valor (revisión security de L2).
_PLACEHOLDER_VALUE_RE = re.compile(r"(?i)(xxxx|0{8,}|changeme)")

# (c) Valor con clave. La clave nombra un secreto si una de sus partes (por
# camelCase y por separadores) es una palabra de secreto o un par como
# `api key`, y ninguna nombra una huella o un dato público (`token_sha256`,
# `auth_id`, `source_head`). El valor: 16 o más caracteres, letras y dígitos y
# entropía de Shannon de 3,0 bits por carácter o más.
_SECRET_KEY_WORDS = frozenset({
    "secret", "secrets", "secreto", "token", "password", "passwd", "pwd", "apikey", "bearer",
    "credential", "credentials", "credencial", "contrasena", "contraseña", "auth",
    "secretkey", "privatekey", "accesskey",
})
_SECRET_KEY_SUFFIXES = ("token", "secret", "password", "passwd", "apikey")
# «clave» sola no basta: en español nombra también claves de idempotencia o de
# agrupación (Boton); solo cuenta en pareja.
_SECRET_KEY_PAIRS = frozenset({
    ("api", "key"), ("private", "key"), ("access", "key"), ("secret", "key"),
    ("clave", "api"), ("api", "clave"), ("clave", "secreta"), ("clave", "privada"), ("clave", "acceso"),
})
_DIGEST_KEY_WORDS = frozenset({
    "hash", "sha", "sha1", "sha256", "sha512", "md5", "digest", "checksum", "fingerprint",
    "huella", "id", "ids", "ref", "commit", "head", "name", "type", "url", "uri", "path",
    "file", "count", "len", "length", "ttl", "expires", "expiry", "prefix", "kind", "mode",
    "version", "field", "max", "min", "limit", "budget", "usage",
})
_SECRET_VALUE_RE = re.compile(r"[A-Za-z0-9+/_\-.=~]{16,}")
# Clave acotada y anclada (lineal en líneas largas); admite subíndice
# (`environ["API_TOKEN"] =`) y anotación de tipo (`API_TOKEN: str =`).
_KEYED_VALUE_RE = re.compile(
    r"(?<![\w.])(?P<key>[^\W\d][\w.\-]{0,63})['\"]?\]?\s*(?::\s*[\w\[\]., |<>]{1,40}?\s*)?"
    r"(?::=|=>|[:=])\s*['\"]?(?P<val>[A-Za-z0-9+/_\-.=~]{16,})(?=$|[\s,;)}\]#&\"'<>`])"
)
_FILE_EXTENSIONS = frozenset(
    "md json jsonl py txt csv tsv yml yaml toml js ts tsx jsx sh html css lock cfg ini xml pdf png jpg svg "
    "patch diff log sql db zip gz tar whl ipynb rs go java rb env".split()
)
_BEARER_RE = re.compile(r"(?i)\bbearer\s+(?P<val>[A-Za-z0-9._~+/\-]{16,}=*)")
_SECRET_RULE_TEXT = (
    "Se marca un formato de credencial conocido (ghp_, github_pat_, sk-, AKIA, xox…, PEM, JWT, URL con "
    "contraseña) o un valor de alta entropía asignado a una clave que nombra un secreto (token, secret, "
    "password, api_key…); un hexadecimal suelto (huella, commit) no lo es. Si es real, sácalo a una variable "
    "de entorno o a un fichero ignorado; si es un valor ficticio con clave, márcalo junto al valor (example, "
    "placeholder, dummy, ficticio, prueba). Una credencial con formato no se tolera nunca."
)
# D5: con `gitleaks` en el PATH, el modo preparado lo usa además del escáner
# propio. Código de salida propio para los hallazgos: gitleaks sale con 1 tanto
# si encuentra algo como si falla, y así se distinguen.
_GITLEAKS_LEAK_RC = 99


# FW-238: `tomllib` exige 3.11; la CI prueba 3.12–3.14. Mantener alineado con
# README §1.5 y `.github/workflows/framework-ci.yml`.
MIN_PYTHON = (3, 11)
TESTED_MIN_PYTHON = (3, 12)
MIN_PYTHON_TEXT = "3.11 o superior"


def _python_version_text() -> str:
    return ".".join(str(part) for part in tuple(sys.version_info)[:3])


def validate_python_version(errors: list[str], warnings: list[str]) -> None:
    """Python con el que corre el doctor (el mismo `python3` que usan hooks y MCP
    si el PATH coincide). Por debajo del mínimo, partes del motor fallan con
    síntomas que apuntan a otro sitio (FW-238)."""
    version = tuple(sys.version_info)[:2]
    if version < MIN_PYTHON:
        fail(errors, f"Python {_python_version_text()} no soportado: el framework requiere Python {MIN_PYTHON_TEXT} "
                     "(p. ej. el /usr/bin/python3 de macOS es 3.9); instalar uno reciente y que `python3` lo resuelva")
    elif version < TESTED_MIN_PYTHON:
        warn(warnings, f"Python {_python_version_text()} funciona pero no está probado: la CI cubre 3.12–3.14")


def fail(errors: list[str], message: str) -> None:
    errors.append(message)


# Patrón B (cosecha 2026-07: Example CLI FW-001, PA FW-038, TL FW-004):
# el drift de wrappers/menú es regenerable con sync_claude_commands.py y no
# habla de la salud del producto — se marca aparte para que un gate de cierre
# de módulo pueda distinguirlo sin juicio manual. Namespace interno del doctor:
# DOCTOR-SYNC-NNN (no FW-NNN, que colisiona con los ledgers de los proyectos).
SYNC_ERROR_PREFIX = "[SYNC] "

SYNC_REGEN_HINT = (
    "regenerable: python3 .agent/scripts/sync_claude_commands.py y re-ejecutar el doctor"
)


def sync_fail(errors: list[str], message: str) -> None:
    """Registra un error de sincronización de menú/wrappers (drift regenerable)."""
    errors.append(f"{SYNC_ERROR_PREFIX}{message}")


def partition_errors(errors: list[str]) -> tuple[list[str], list[str]]:
    """Separa (errores de producto/plantilla, errores de sincronización)."""
    producto = [e for e in errors if not e.startswith(SYNC_ERROR_PREFIX)]
    sync = [e[len(SYNC_ERROR_PREFIX):] for e in errors if e.startswith(SYNC_ERROR_PREFIX)]
    return producto, sync


def load_json(path: Path, errors: list[str]) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001 - diagnostic script
        fail(errors, f"JSON inválido {path.relative_to(ROOT)}: {exc}")
        return {}


def warn(warnings: list[str], message: str) -> None:
    warnings.append(message)


def rel(path: Path) -> str:
    return str(path.relative_to(ROOT))


def require_type(errors: list[str], obj: dict[str, Any], key: str, expected: type, path: str) -> Any:
    value = obj.get(key)
    if not isinstance(value, expected):
        fail(errors, f"{path}.{key} debe ser {expected.__name__}")
    return value


def validate_session_pointer_state(state: dict[str, Any], errors: list[str]) -> None:
    """Reuse the state-core pointer contract in the diagnostic surface."""
    if "session" not in state:
        return
    session = state.get("session")
    if not isinstance(session, dict):
        fail(errors, "03_project_state.json.session debe ser objeto")
        return
    try:
        from state_core import validate_session_pointer

        validate_session_pointer(session, root=ROOT)
    except ValueError as exc:
        fail(errors, f"03_project_state.json.session inválida: {exc}")


def embedded_next_command_prompt_lines(text: str) -> list[tuple[int, str]]:
    """Find writer guidance that puts a prompt after ``next_command``.

    The scan is intentionally local to the next few lines: historical prose
    elsewhere in a document may mention the old format, but an active writer
    must not teach the invalid persisted shape.
    """
    lines = text.splitlines()
    findings: list[tuple[int, str]] = []
    for index, line in enumerate(lines):
        if "next_command" not in line.casefold():
            continue
        window = "\n".join(lines[index:index + 8])
        match = FLOW_EMBEDDED_NEXT_COMMAND_RE.search(window)
        if match:
            findings.append((index + 1, match.group(0)))
    return findings


def _strip_fenced_blocks(text: str) -> str:
    """Elimina los bloques ``` de Markdown.

    Cosechado de Research App (PA FW-105). Una proyección secundaria que
    ILUSTRA el formato de plantilla dentro de un bloque de código no tiene un
    placeholder sin resolver: tiene documentación. Marcarla obliga a mutilar el
    ejemplo para callar al doctor.
    """
    return re.sub(r"(?ms)^```.*?^```\s*$", "", text)


def _is_template_line(line: str, match: "re.Match[str]") -> bool:
    """Una línea cuenta como plantilla si el token ES su contenido primario.

    Cosechado de Example Lab (TL FW-115/116). Complementario al anterior:
    aquel ignora bloques de código, este distingue `[COMPLETAR]` (plantilla
    real, la línea no dice nada más) de una mención en prosa como "sustituye
    `[NOMBRE_PROYECTO]` por el tuyo". Los dos proyectos atacaron el mismo falso
    positivo por caminos distintos y ninguno llegó al framework (FW-143).
    """
    # Se quitan TODOS los tokens, no solo el que disparó: una fila de tabla de
    # plantilla (`| [COMPLETAR] | [COMPLETAR] |`) dejaba el segundo token en el
    # resto, este parecía prosa y la fila no se detectaba. Juzgar lo que queda
    # tras vaciar la línea de placeholders es más simple y cubre más casos que
    # el `replace(token, "", 1)` original.
    del match  # el veredicto no depende de cuál de los tokens disparó
    remainder = PLACEHOLDER_RE.sub("", line).strip()
    if not remainder:
        return True
    if len(remainder) > 30:
        return False
    # `|` incluido: una fila de tabla vaciada de tokens queda en "|  |  |", que
    # es decoración pura y por tanto plantilla.
    if re.match(r"""^["'`#\-*\.\s:,\(\)\[\]/<>|]*$""", remainder):
        return True
    if re.match(r"^[A-Za-z_][A-Za-z0-9_\-]*\s*:\s*$", remainder):
        return True
    # Etiqueta de campo con adornos de lista o énfasis Markdown ("- **Stack:**",
    # "**Fase:**", "* Nombre:"). El heurístico original de TL v2 solo aceptaba
    # la etiqueta desnuda, así que un campo de plantilla escrito como ítem de
    # lista dejaba de detectarse: la fusión habría AFLOJADO el gate en vez de
    # afinarlo, que es el error contrario y peor.
    # Prefijo: viñetas, cita, celda de tabla o almohadillas de encabezado.
    # Sufijo: dos puntos, guiones o rayas. Un encabezado de plantilla
    # ("# Historial — [NOMBRE_PROYECTO]") es tan plantilla como un campo, y la
    # primera versión de esta fusión lo dejaba escapar.
    if re.match(r"^[-*+>|#\s]*\**\s*[A-Za-z_][A-Za-z0-9 _\-]*\s*\**\s*[:\-—–|*\s]*$", remainder):
        return True
    return False


def file_has_unresolved_placeholder(text: str) -> bool:
    """Placeholder REAL sin resolver, sin marcar menciones legítimas en prosa.

    FW-158: fusiona los dos parches locales. Se aplica en los dos puntos donde
    el doctor juzgaba con un `PLACEHOLDER_RE.search()` desnudo sobre el texto
    entero.
    """
    for line in _strip_fenced_blocks(text).splitlines():
        stripped = line.strip()
        if not stripped or len(stripped) > 200:
            continue
        match = PLACEHOLDER_RE.search(stripped)
        if match and _is_template_line(stripped, match):
            return True
    return False


def contains_placeholder(path: Path) -> bool:
    if not path.exists() or path.suffix not in {".md", ".json", ".txt"}:
        return False
    text = path.read_text(encoding="utf-8", errors="ignore")
    return file_has_unresolved_placeholder(text)


def validate_manifest(manifest: dict[str, Any], errors: list[str], warnings: list[str]) -> set[str]:
    commands = manifest.get("commands", [])
    if not isinstance(commands, list):
        fail(errors, "manifest.json: commands debe ser una lista")
        return set()

    names: list[str] = []
    all_slugs: list[str] = []
    all_slugs_nfc: list[str] = []
    expected_claude: set[str] = set()
    for command in commands:
        if not isinstance(command, dict):
            fail(errors, f"Entrada de manifest inválida: {command!r}")
            continue
        name = command.get("name")
        source = command.get("source")
        aliases = command.get("aliases", [])
        if not name or not isinstance(name, str) or not source or not isinstance(source, str):
            fail(errors, f"Entrada incompleta en manifest: {command}")
            continue
        has_ascii_alias = any(isinstance(alias, str) and alias.isascii() for alias in aliases)
        if name != unicodedata.normalize("NFC", name) and not has_ascii_alias:
            warn(warnings, f"/{name} no está normalizado NFC; considera alias ASCII o renombrar")
        # FW-100: los nombres no-ASCII ya no son un riesgo de portabilidad — la
        # superficie Codex emite automáticamente el slug ASCII (codex_slug).
        # Solo advertir si por alguna razón el slug ASCII quedara vacío.
        if not name.isascii() and not has_ascii_alias:
            from sync_claude_commands import codex_slug as _cslug
            if not _cslug(name).isascii():
                warn(warnings, f"/{name} contiene caracteres poco portables sin slug ASCII derivable")
        if aliases and not isinstance(aliases, list):
            fail(errors, f"aliases de /{name} debe ser una lista")
            aliases = []

        names.append(name)
        all_slugs.append(name)
        all_slugs_nfc.append(unicodedata.normalize("NFC", name))
        all_slugs.extend(alias for alias in aliases if isinstance(alias, str))
        all_slugs_nfc.extend(
            unicodedata.normalize("NFC", alias)
            for alias in aliases
            if isinstance(alias, str)
        )

        source_path = ROOT / source
        if not source_path.exists():
            fail(errors, f"Fuente inexistente para /{name}: {source}")
        if command.get("claude_command", False):
            expected_claude.add(f"{name}.md")
            for alias in aliases:
                if isinstance(alias, str):
                    expected_claude.add(f"{alias}.md")

    if len(names) != len(set(names)):
        fail(errors, "Hay comandos duplicados en manifest.json")
    if len(all_slugs) != len(set(all_slugs)):
        fail(errors, "Hay nombres o aliases duplicados en manifest.json")
    if len(all_slugs_nfc) != len(set(all_slugs_nfc)):
        fail(errors, "Hay nombres o aliases duplicados tras normalización Unicode NFC en manifest.json")
    for required in ["descubrir-producto", "prd-mvp", "tech-design-mvp"]:
        if required not in names:
            fail(errors, f"Comando onboarding v3 faltante en manifest.json: /{required}")
    return expected_claude


def validate_progressive_context_manifest(manifest: dict[str, Any], errors: list[str]) -> None:
    """Every routed command must declare its minimal context contract.

    The metadata is intentionally validated outside ``validate_manifest`` so
    older fixture manifests used by unit tests can still exercise unrelated
    checks. The real framework manifest, however, cannot silently omit a
    routing entry: omission would reintroduce full-conversation loading by
    default.
    """
    commands = manifest.get("commands", []) if isinstance(manifest, dict) else []
    if not isinstance(commands, list):
        return
    names = {item.get("name") for item in commands if isinstance(item, dict)}
    allowed_views = {"startup", "implementation", "closure", "recovery"}
    allowed_conditions = {
        "always", "interruption_or_inconsistency", "implementation", "closure",
        "recovery", "publish",
    }
    for command in commands:
        if not isinstance(command, dict) or not isinstance(command.get("name"), str):
            continue
        name = command["name"]
        context = command.get("context")
        if not isinstance(context, dict):
            fail(errors, f"manifest.json: /{name} carece de metadata context")
            continue
        view = context.get("entry_view")
        if view not in allowed_views:
            fail(errors, f"manifest.json: /{name}.context.entry_view inválido: {view!r}")
        required_now = context.get("required_now")
        if not isinstance(required_now, list) or not required_now:
            fail(errors, f"manifest.json: /{name}.context.required_now debe ser lista no vacía")
        else:
            for source in required_now:
                if not isinstance(source, str) or not source or not (ROOT / source).is_file():
                    fail(errors, f"manifest.json: /{name} requiere fuente inexistente: {source!r}")
        triggers = context.get("activate_when", [])
        if not isinstance(triggers, list):
            fail(errors, f"manifest.json: /{name}.context.activate_when debe ser lista")
            continue
        for trigger in triggers:
            if not isinstance(trigger, dict):
                fail(errors, f"manifest.json: /{name} tiene activate_when inválido: {trigger!r}")
                continue
            condition = trigger.get("condition")
            target = trigger.get("command")
            if condition not in allowed_conditions:
                fail(errors, f"manifest.json: /{name} usa condición no soportada: {condition!r}")
            if target not in names:
                fail(errors, f"manifest.json: /{name} activa comando no declarado: {target!r}")


def validate_claude_wrappers(expected_claude: set[str], errors: list[str], warnings: list[str]) -> None:
    claude_dir = ROOT / ".claude" / "commands"
    if not claude_dir.exists():
        warn(warnings, ".claude/commands no existe; se omite validación de wrappers Claude")
        return
    actual_by_nfc = {unicodedata.normalize("NFC", path.name): path for path in claude_dir.glob("*.md")}
    for filename in expected_claude:
        path = actual_by_nfc.get(unicodedata.normalize("NFC", filename))
        if path is None or not path.exists():
            sync_fail(errors, f"Wrapper Claude faltante: .claude/commands/{filename}")
            continue
        text = path.read_text(encoding="utf-8")
        if GENERATED not in text and GENERATED_LEGACY not in text:
            sync_fail(errors, f"Wrapper Claude no generado por sync: .claude/commands/{filename}")
    for path in claude_dir.glob("*.md"):
        normalized_name = unicodedata.normalize("NFC", path.name)
        if normalized_name not in expected_claude:
            if normalized_name == "compactar.md":
                fail(errors, "compactar.md reapareció como comando Claude; debe quedar solo como fallback portable")
            else:
                warn(warnings, f"Comando Claude no declarado en manifest: {rel(path)}")


def validate_claude_wrapper_contracts(manifest: dict[str, Any], errors: list[str]) -> None:
    """Valida los contratos de wrappers generados en Claude Code (Lote 5A y 5B).

    Verifica que:
    - Comandos con claude_inline_body: true contengan el cuerpo del workflow (no solo el puntero).
    - Comandos con disable_model_invocation: true lleven disable-model-invocation: true en frontmatter.
    - Procedimientos internos (tdd, cierre-modulo, cambio-seguro, scope-siguiente) NO lleven disable-model-invocation.
    - Comandos con claude_dynamic_injection: true:
      * NO sean /arrancar (reservado al hook de continuidad Lote 3).
      * Invoquen claude_dynamic_context.py con la vista correspondiente.
      * NO contengan '|| true' (prohibido por Plan 4.5 Lote 5B).
      * Incluyan la guía de recuperación explícita (RECOVERY_GUIDE).
    - Comandos sin claude_dynamic_injection NO contengan claude_dynamic_context.py.
    - El script claude_dynamic_context.py exista en .agent/scripts/.
    """
    claude_dir = ROOT / ".claude" / "commands"
    if not claude_dir.exists():
        return

    has_dynamic_injection = False

    for cmd in manifest.get("commands", []):
        if not cmd.get("claude_command", False):
            continue
        name = cmd.get("name")
        target = claude_dir / f"{name}.md"
        if not target.exists():
            continue
        text = target.read_text(encoding="utf-8", errors="ignore")

        # Check disable-model-invocation
        if cmd.get("disable_model_invocation", False):
            if "disable-model-invocation: true" not in text:
                sync_fail(errors, f"Wrapper Claude /{name} debe tener 'disable-model-invocation: true' en frontmatter")
        else:
            if "disable-model-invocation: true" in text:
                sync_fail(errors, f"Wrapper Claude /{name} no debe tener 'disable-model-invocation: true' (rompería invocación por driver)")

        # Check inline body
        if cmd.get("claude_inline_body", False):
            if "No dupliques lógica en este wrapper" in text:
                sync_fail(errors, f"Wrapper Claude /{name} tiene puntero en vez de cuerpo inline generado")

        # Check dynamic injection (Lote 5B)
        if cmd.get("claude_dynamic_injection", False):
            has_dynamic_injection = True
            if name == "arrancar":
                sync_fail(errors, "Comando /arrancar no puede tener claude_dynamic_injection (reservado para hook de continuidad Lote 3)")
            expected_view = cmd.get("claude_dynamic_context_view") or cmd.get("context", {}).get("entry_view", "implementation")
            if "claude_dynamic_context.py" not in text:
                sync_fail(errors, f"Wrapper Claude /{name} debe tener directiva de inyección con claude_dynamic_context.py")
            if f"--view {expected_view}" not in text:
                sync_fail(errors, f"Wrapper Claude /{name} debe invocar claude_dynamic_context.py con '--view {expected_view}'")
            if "|| true" in text:
                sync_fail(errors, f"Wrapper Claude /{name} contiene '|| true' en inyección dinámica (prohibido por Plan 4.5 Lote 5B)")
            if "RECOVERY_GUIDE" not in text:
                sync_fail(errors, f"Wrapper Claude /{name} debe incluir guía de recuperación RECOVERY_GUIDE para inyección dinámica")
        else:
            if "claude_dynamic_context.py" in text:
                sync_fail(errors, f"Wrapper Claude /{name} no debe tener inyección dinámica (no declarada en manifest)")

    if has_dynamic_injection:
        dynamic_script = ROOT / ".agent" / "scripts" / "claude_dynamic_context.py"
        if not dynamic_script.exists():
            sync_fail(errors, "Script .agent/scripts/claude_dynamic_context.py no encontrado")



def validate_opencode_skills(errors: list[str], warnings: list[str]) -> None:
    """Verify generated OpenCode skills stay in sync with the canonical ones."""
    opencode_skills_dir = ROOT / ".opencode" / "skills"
    if not opencode_skills_dir.exists():
        return

    from sync_claude_commands import _extract_skill_metadata, opencode_skill_text, normalize_slug

    expected_skills = {}
    agent_skills_dir = ROOT / ".agent" / "skills"
    if agent_skills_dir.exists():
        for path in agent_skills_dir.glob("*.md"):
            if path.name != "README.md":
                title, desc, body = _extract_skill_metadata(path)
                slug = normalize_slug(path.stem).replace('_', '-')
                expected_skills[slug] = opencode_skill_text(slug, title, desc, body)
        imported_dir = agent_skills_dir / "imported"
        if imported_dir.exists():
            for path in imported_dir.glob("*.md"):
                if path.name != "README.md":
                    title, desc, body = _extract_skill_metadata(path)
                    slug = normalize_slug(path.stem).replace('_', '-')
                    expected_skills[slug] = opencode_skill_text(slug, title, desc, body)

    actual_skills = {path.name for path in opencode_skills_dir.iterdir() if path.is_dir()}

    for expected_slug, expected_text in expected_skills.items():
        if expected_slug not in actual_skills:
            sync_fail(errors, f"Skill OpenCode faltante: .opencode/skills/{expected_slug}/SKILL.md")
            continue
        skill_file = opencode_skills_dir / expected_slug / "SKILL.md"
        if not skill_file.exists():
            sync_fail(errors, f"Skill OpenCode sin SKILL.md: {rel(skill_file.parent)}")
            continue
        actual_text = skill_file.read_text(encoding="utf-8", errors="ignore")
        if actual_text != expected_text:
            sync_fail(errors, f"Skill OpenCode desactualizada o corrupta: {rel(skill_file)} — ejecutar python3 .agent/scripts/sync_claude_commands.py")

    for actual in actual_skills:
        if actual not in expected_skills:
            sync_fail(errors, f"Skill OpenCode generada pero no declarada: .opencode/skills/{actual}/SKILL.md — ejecutar python3 .agent/scripts/sync_claude_commands.py para podarla")

def validate_opencode_wrappers(expected_opencode: set[str], errors: list[str], warnings: list[str]) -> None:
    """Verify generated OpenCode wrappers stay in sync with the manifest.

    OpenCode mirrors the Claude surface (commands flagged `claude_command`), so the
    expected filename set is the same one computed for Claude. If `.opencode/commands`
    is absent the project simply does not target OpenCode — warn and skip.
    """
    opencode_dir = ROOT / ".opencode" / "commands"
    if not opencode_dir.exists():
        warn(warnings, ".opencode/commands no existe; se omite validación de wrappers OpenCode")
        return
    actual_by_nfc = {unicodedata.normalize("NFC", path.name): path for path in opencode_dir.glob("*.md")}
    for filename in expected_opencode:
        path = actual_by_nfc.get(unicodedata.normalize("NFC", filename))
        if path is None or not path.exists():
            sync_fail(errors, f"Wrapper OpenCode faltante: .opencode/commands/{filename}")
            continue
        text = path.read_text(encoding="utf-8")
        if GENERATED not in text and GENERATED_LEGACY not in text:
            sync_fail(errors, f"Wrapper OpenCode no generado por sync: .opencode/commands/{filename}")
    for path in opencode_dir.glob("*.md"):
        normalized_name = unicodedata.normalize("NFC", path.name)
        if normalized_name not in expected_opencode:
            if normalized_name == "compactar.md":
                fail(errors, "compactar.md reapareció como comando OpenCode; debe quedar solo como fallback portable")
            else:
                warn(warnings, f"Comando OpenCode no declarado en manifest: {rel(path)}")


def validate_claude_agents(errors: list[str], warnings: list[str]) -> None:
    """`.claude/agents/*.md` — subagentes read-only de Claude Code (FW-206/D1).

    Dos garantías: (1) están los 6 contratos de `SUBAGENTS` y llevan marcador
    GENERATED; (2) su `tools:` NO incluye `Edit`/`Write`/`Bash` — la propiedad de
    seguridad (lección FW-014). El chequeo (2) aplica también a un fichero editado
    a mano: un subagente de revisión con permiso de escritura es el agujero."""
    agents_dir = ROOT / ".claude" / "agents"
    if not agents_dir.exists():
        warn(warnings, ".claude/agents no existe; se omite validación de subagentes Claude (ejecutar sync_claude_commands.py)")
        return
    from sync_claude_commands import SUBAGENTS

    expected = {f"{slug}.md" for slug, _title, _desc in SUBAGENTS}
    present = {p.name for p in agents_dir.glob("*.md")}
    for filename in expected - present:
        sync_fail(errors, f"Subagente Claude faltante: .claude/agents/{filename} — ejecutar python3 .agent/scripts/sync_claude_commands.py")

    forbidden = re.compile(r"\btools\s*:.*\b(Edit|Write|Bash|MultiEdit)\b", re.IGNORECASE)
    for path in agents_dir.glob("*.md"):
        text = path.read_text(encoding="utf-8", errors="ignore")
        if path.name in expected and GENERATED not in text and GENERATED_LEGACY not in text:
            warn(warnings, f".claude/agents/{path.name} sin marcador GENERATED — sync no lo mantiene")
        if forbidden.search(text):
            fail(errors, f".claude/agents/{path.name}: `tools:` incluye una herramienta de escritura — un subagente de revisión read-only no debe poder mutar el workspace (FW-014)")
        if path.name not in expected:
            warn(warnings, f"Subagente Claude no declarado en SUBAGENTS: {rel(path)}")


def validate_claude_rules(manifest: dict[str, Any], errors: list[str], warnings: list[str]) -> None:
    """Valida las reglas por ruta de Claude Code generadas en .claude/rules/ (Lote 5C).

    Verifica que:
    - Toda regla declarada en manifest.claude_rules tenga:
      * nombre válido.
      * fuente existente en .agent/ que contenga '<!-- CONTEXT_SOURCE: file_obligations -->'.
      * lista paths no vacía con patrones válidos.
      * archivo .claude/rules/<name>.md existente con marcador GENERATED.
      * frontmatter paths coincidente con el manifest.
      * header con referencia a la fuente canónica.
    - Cero exclusividad: la fuente canónica vive en .agent/ (regla de portabilidad §2).
    - Cero huérfanos generados en .claude/rules/.
    """
    rules_dir = ROOT / ".claude" / "rules"
    declared_rules = manifest.get("claude_rules", [])
    if not declared_rules:
        return

    if not rules_dir.exists():
        sync_fail(errors, "Directorio .claude/rules/ faltante habiendo reglas declaradas en manifest")
        return

    from sync_claude_commands import normalize_slug, extract_file_obligations

    expected_names = set()
    for rule in declared_rules:
        name = rule.get("name")
        if not name or not isinstance(name, str):
            fail(errors, f"manifest.claude_rules: regla sin nombre válido: {rule}")
            continue
        slug = normalize_slug(name)
        expected_names.add(f"{slug}.md")

        source_rel = rule.get("source")
        if not source_rel or not isinstance(source_rel, str):
            fail(errors, f"manifest.claude_rules: regla '{name}' sin source válido")
            continue
        source_path = ROOT / source_rel
        if not source_path.exists():
            fail(errors, f"manifest.claude_rules: regla '{name}' apunta a fuente inexistente: {source_rel}")
            continue

        # Portabilidad (§2): la fuente debe residir en .agent/
        if not source_path.is_relative_to(ROOT / ".agent"):
            fail(errors, f"Regla '{name}' viola portabilidad (§2): fuente {source_rel} debe vivir en .agent/")

        try:
            body = extract_file_obligations(source_path)
            if not body.strip():
                fail(errors, f"Fuente {source_rel} tiene bloque file_obligations vacío para regla '{name}'")
        except Exception as exc:
            fail(errors, f"Error extrayendo obligaciones de fichero para regla '{name}' desde {source_rel}: {exc}")

        paths = rule.get("paths", [])
        if not isinstance(paths, list) or not paths or not all(isinstance(p, str) and p.strip() for p in paths):
            fail(errors, f"manifest.claude_rules: regla '{name}' debe declarar una lista 'paths' no vacía")

        rule_file = rules_dir / f"{slug}.md"
        if not rule_file.exists():
            sync_fail(errors, f"Regla Claude faltante: .claude/rules/{slug}.md — ejecutar python3 .agent/scripts/sync_claude_commands.py")
            continue

        text = rule_file.read_text(encoding="utf-8", errors="ignore")
        if GENERATED not in text and GENERATED_LEGACY not in text:
            sync_fail(errors, f"Regla Claude no generada por sync: .claude/rules/{slug}.md")

        if not text.startswith("---\n"):
            sync_fail(errors, f"Regla Claude .claude/rules/{slug}.md falta frontmatter YAML")
        if "paths:" not in text:
            sync_fail(errors, f"Regla Claude .claude/rules/{slug}.md falta bloque 'paths:' en frontmatter")
        for p in paths:
            if f'"{p}"' not in text and f"'{p}'" not in text and p not in text:
                sync_fail(errors, f"Regla Claude .claude/rules/{slug}.md no incluye path '{p}' en frontmatter")

        if source_rel not in text:
            sync_fail(errors, f"Regla Claude .claude/rules/{slug}.md no cita la fuente canónica '{source_rel}'")

    # Check for orphan generated rules
    for path in rules_dir.glob("*.md"):
        if path.name not in expected_names:
            text = path.read_text(encoding="utf-8", errors="ignore")
            if GENERATED in text or GENERATED_LEGACY in text:
                sync_fail(errors, f"Regla Claude huérfana generada: .claude/rules/{path.name}")


def validate_codex_wrappers(manifest: dict[str, Any], errors: list[str], warnings: list[str]) -> None:
    """Verify generated Codex skills stay in sync with the workflow manifest."""
    commands = manifest.get("commands", [])
    if not isinstance(commands, list):
        return

    # FW-100: la superficie Codex emite slugs ASCII deterministas — el doctor
    # calcula los esperados con la MISMA función que el sync (una sola verdad).
    from sync_claude_commands import codex_slug
    expected: set[str] = set()
    for command in commands:
        if not isinstance(command, dict) or command.get("native_preferred", False):
            continue
        name = command.get("name")
        if isinstance(name, str):
            expected.add(f"source-command-{codex_slug(name)}")
        aliases = command.get("aliases", [])
        if isinstance(aliases, list):
            expected.update(f"source-command-{codex_slug(alias)}" for alias in aliases if isinstance(alias, str))

    skills_dir = ROOT / ".agents" / "skills"
    if not skills_dir.exists():
        fail(errors, ".agents/skills faltante; wrappers Codex no generados")
        return

    actual_by_nfc = {
        unicodedata.normalize("NFC", path.name): path
        for path in skills_dir.iterdir()
        if path.is_dir() and path.name.startswith("source-command-")
    }

    for dirname in expected:
        path = actual_by_nfc.get(unicodedata.normalize("NFC", dirname))
        if path is None:
            sync_fail(errors, f"Wrapper Codex faltante: .agents/skills/{dirname}/SKILL.md")
            continue
        skill_file = path / "SKILL.md"
        if not skill_file.exists():
            sync_fail(errors, f"Wrapper Codex sin SKILL.md: {rel(path)}")
            continue
        skill_text = skill_file.read_text(encoding="utf-8", errors="ignore")
        if not any(m in skill_text for m in (GENERATED, GENERATED_LEGACY)):
            sync_fail(errors, f"Wrapper Codex no generado por sync: {rel(skill_file)}")
            continue
        if not skill_text.startswith("---\n") or "\n---" not in skill_text[4:]:
            sync_fail(errors, f"Wrapper Codex sin frontmatter YAML válido: {rel(skill_file)}")
            continue
        frontmatter = skill_text[4 : skill_text.find("\n---", 4)]
        fields: dict[str, str] = {}
        for line in frontmatter.splitlines():
            if ":" not in line:
                continue
            key, value = line.split(":", 1)
            fields[key.strip()] = value.strip().strip('"').strip("'")
        if fields.get("name") != path.name:
            sync_fail(
                errors,
                f"Wrapper Codex con name inconsistente: {rel(skill_file)} declara {fields.get('name')!r}, "
                f"esperado {path.name!r}",
            )
        if not fields.get("description"):
            sync_fail(errors, f"Wrapper Codex sin description utilizable: {rel(skill_file)}")

    for dirname, path in actual_by_nfc.items():
        if dirname not in {unicodedata.normalize("NFC", item) for item in expected}:
            skill_file = path / "SKILL.md"
            if skill_file.exists() and any(m in skill_file.read_text(encoding="utf-8", errors="ignore") for m in (GENERATED, GENERATED_LEGACY)):
                sync_fail(errors, f"Wrapper Codex generado pero no declarado en manifest: {rel(skill_file)} — ejecutar python3 .agent/scripts/sync_claude_commands.py para podarlo (DOCTOR-SYNC-001)")
            else:
                warn(warnings, f"Skill Codex no declarada en manifest: {rel(path)}")


def validate_adapter_surface_collision(errors: list[str], warnings: list[str]) -> None:
    """OpenCode escanea .agents/skills/ (superficie compartida Codex + Antigravity,
    verificado 2026-08-25) por diseño fijo del núcleo. Si el proyecto targetea
    OpenCode (opencode.json) y existe esa superficie `source-command-*`,
    opencode.json DEBE denegarla vía permission.skill; si no, 40 skills
    duplicadas/internas se filtran al namespace de OpenCode (FW-043)."""
    config_path = ROOT / "opencode.json"
    skills_dir = ROOT / ".agents" / "skills"
    if not config_path.exists() or not skills_dir.exists():
        return
    has_codex = any(
        p.is_dir() and p.name.startswith("source-command-")
        for p in skills_dir.iterdir()
    )
    if not has_codex:
        return
    try:
        config = json.loads(config_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        fail(errors, f"opencode.json ilegible: {e}")
        return
    skill_perms = (config.get("permission") or {}).get("skill") or {}
    denied = any(
        str(pat).startswith("source-command") and action == "deny"
        for pat, action in skill_perms.items()
    )
    if not denied:
        fail(
            errors,
            "opencode.json no aísla la superficie compartida Codex+Antigravity: .agents/skills/source-command-* "
            'se filtra a OpenCode. Añadir permission.skill {"source-command-*": "deny"} (FW-043).',
        )


def validate_claude_codex_parity(manifest: dict[str, Any], errors: list[str]) -> None:
    """MVP multi-modelo: el ciclo de sesión que un usuario corre en Claude debe ser
    ejecutable también en Codex (ChatGPT). Toda command pública de Claude
    (`claude_command: true`, + aliases) debe tener su wrapper Codex generado. Es
    una garantía estructural ya implícita en el sync, pero la hacemos verificable:
    si un cambio futuro marcara una command pública como native_preferred (sin
    wrapper Codex), la paridad se rompería en silencio. Modelos fuera de
    Claude/Codex usan el adapter genérico (experimental)."""
    commands = manifest.get("commands", [])
    if not isinstance(commands, list):
        return  # validate_manifest already reported this
    skills_dir = ROOT / ".agents" / "skills"
    if not skills_dir.exists():
        return  # validate_codex_wrappers already reported the missing dir
    codex_present = {
        unicodedata.normalize("NFC", path.name)
        for path in skills_dir.iterdir()
        if path.is_dir() and path.name.startswith("source-command-")
    }
    for command in commands:
        if not isinstance(command, dict) or command.get("claude_command", False) is not True:
            continue
        names = [command.get("name")] + [
            a for a in command.get("aliases", []) if isinstance(a, str)
        ]
        for name in names:
            if not isinstance(name, str):
                continue
            # FW-100: la superficie Codex usa slugs ASCII deterministas.
            from sync_claude_commands import codex_slug as _codex_slug
            expected = unicodedata.normalize("NFC", f"source-command-{_codex_slug(name)}")
            if expected not in codex_present:
                fail(errors, f"Paridad Claude↔Codex rota: /{name} es pública en Claude pero no tiene wrapper Codex (.agents/skills/{expected}/)")


TRIGGER_CLAUSE_RE = re.compile(r"usar cuando", re.IGNORECASE)


def validate_manifest_trigger_descriptions(manifest: dict[str, Any], errors: list[str]) -> None:
    """FW-137: Claude, Codex y Antigravity deciden cargar una skill comparando
    la petición del usuario contra su `description` (frontmatter SKILL.md /
    slash-command). Una descripción que solo etiqueta ("Gate anti-regresión
    para cambios sobre código existente") no sirve a ese matcher; necesita una
    cláusula de disparo ("Usar cuando: ..."). Sin este gate, un comando nuevo
    en el manifest puede quedar sin disparador y degradar el ruteo cross-harness
    en silencio — el mismo patrón de FW-112 pero en el campo que los tres
    entornos leen para auto-cargar, no en la prosa de sugerencia del agente."""
    commands = manifest.get("commands", [])
    if not isinstance(commands, list):
        return  # validate_manifest ya reportó esto
    for command in commands:
        if not isinstance(command, dict):
            continue
        name = command.get("name")
        description = command.get("description", "")
        if not isinstance(description, str) or not TRIGGER_CLAUSE_RE.search(description):
            fail(
                errors,
                f"manifest.json: /{name} sin cláusula de disparo (\"Usar cuando: ...\") en "
                "description — Claude/Codex/Antigravity auto-cargan skills comparando la "
                "petición del usuario contra este campo (FW-137); una etiqueta sola no basta",
            )


def validate_no_menu_bloat(errors: list[str], warnings: list[str]) -> None:
    """Detect menu bloat: two wrappers pointing to the same source mean a single
    intention is exposed twice in the user-facing `/` menu (e.g. an alias generated
    a separate wrapper from its canonical command). Fail closed so the sync
    script + manifest combo can't drift back into the bug discovered in 3.10.0."""
    sources_by_label: dict[str, list[str]] = {
        "Claude": [],
        "OpenCode": [],
        "Codex": [],
    }

    def collect(target_dir: Path, label: str, kind: str) -> None:
        if not target_dir.exists():
            return
        if kind == "codex":
            entries = [
                path for path in target_dir.iterdir()
                if path.is_dir() and path.name.startswith("source-command-")
            ]
            for entry in entries:
                skill = entry / "SKILL.md"
                if not skill.exists():
                    continue
                text = skill.read_text(encoding="utf-8", errors="ignore")
                if GENERATED not in text and GENERATED_LEGACY not in text:
                    continue
                match = re.search(r"Fuente canónica: `([^`]+)`", text)
                if match:
                    sources_by_label[label].append((entry.name, match.group(1)))
        else:
            for path in sorted(target_dir.glob("*.md")):
                text = path.read_text(encoding="utf-8", errors="ignore")
                if GENERATED not in text and GENERATED_LEGACY not in text:
                    continue
                match = re.search(r"Fuente canónica: `([^`]+)`", text)
                if match:
                    sources_by_label[label].append((path.name, match.group(1)))

    collect(ROOT / ".claude" / "commands", "Claude", "claude")
    collect(ROOT / ".opencode" / "commands", "OpenCode", "opencode")
    collect(ROOT / ".agents" / "skills", "Codex", "codex")

    for label, items in sources_by_label.items():
        by_source: dict[str, list[str]] = {}
        for wrapper, source in items:
            by_source.setdefault(source, []).append(wrapper)
        for source, wrappers in by_source.items():
            if len(wrappers) < 2:
                continue
            sync_fail(
                errors,
                f"Menu bloat en {label}: {', '.join(sorted(wrappers))} apuntan al "
                f"mismo source {source!r} — la misma intención aparece N veces en el menú /. "
                f"Quita el alias del manifest o marca el comando como native_preferred.",
            )


def validate_state(state: dict[str, Any], errors: list[str], warnings: list[str]) -> str:
    mode = state.get("framework_mode", "initialized")
    if mode not in VALID_FRAMEWORK_MODES:
        fail(errors, "03_project_state.json.framework_mode debe ser template o initialized")
        mode = "initialized"

    for key in ["project", "framework_version", "current_phase", "current_step", "last_completed_step"]:
        require_type(errors, state, key, str, "03_project_state.json")
    modules = require_type(errors, state, "modules", dict, "03_project_state.json") or {}
    module_statuses = require_type(errors, state, "module_statuses", dict, "03_project_state.json") or {}
    work_tiers = require_type(errors, state, "work_tiers", dict, "03_project_state.json") or {}
    closure_rejections = require_type(errors, state, "closure_rejections", list, "03_project_state.json") or []
    require_type(errors, state, "dependencies", list, "03_project_state.json")
    require_type(errors, state, "active_mcps", list, "03_project_state.json")
    validate_session_pointer_state(state, errors)

    module_lists: dict[str, list[str]] = {}
    for bucket in ["completed", "in_progress", "pending"]:
        value = modules.get(bucket)
        if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
            fail(errors, f"03_project_state.json.modules.{bucket} debe ser lista de strings")
            value = []
        module_lists[bucket] = value
    # PA-FW-037 (5.3.3): `modules.dropped` es opcional (solo existe tras un
    # abandon_module); ausente equivale a lista vacía.
    dropped_value = modules.get("dropped", [])
    if not isinstance(dropped_value, list) or not all(isinstance(item, str) for item in dropped_value):
        fail(errors, "03_project_state.json.modules.dropped debe ser lista de strings")
        dropped_value = []
    module_lists["dropped"] = dropped_value

    all_modules = [module for values in module_lists.values() for module in values]
    if len(all_modules) != len(set(all_modules)):
        fail(errors, "Un módulo aparece en más de una lista modules.*")

    for module, status in module_statuses.items():
        if status not in VALID_MODULE_STATUSES:
            fail(errors, f"Estado inválido para module_statuses.{module}: {status}")
    for module in module_lists["completed"]:
        if module_statuses.get(module) != "completed":
            fail(errors, f"modules.completed contiene {module}, pero module_statuses no es completed")
    for module in module_lists["in_progress"]:
        if module_statuses.get(module) not in {"in_progress", "ready_for_validation", "blocked"}:
            fail(errors, f"modules.in_progress contiene {module}, pero module_statuses no está activo/bloqueado")
    for module in module_lists["pending"]:
        if module_statuses.get(module, "not_started") not in {"not_started", "blocked"}:
            fail(errors, f"modules.pending contiene {module}, pero module_statuses no es not_started/blocked")
    for module in module_lists["dropped"]:
        if module_statuses.get(module) != "dropped":
            fail(errors, f"modules.dropped contiene {module}, pero module_statuses no es dropped")
    for module, status in module_statuses.items():
        if status == "dropped" and module not in module_lists["dropped"]:
            fail(errors, f"module_statuses.{module} es dropped, pero no aparece en modules.dropped")
    for module in module_statuses:
        if module not in all_modules and mode == "initialized":
            warn(warnings, f"module_statuses contiene {module}, pero no aparece en modules.*")

    for unit, entry in work_tiers.items():
        if not isinstance(entry, dict):
            fail(errors, f"work_tiers.{unit} debe ser objeto")
            continue
        tier = entry.get("tier")
        if tier not in VALID_WORK_TIERS:
            fail(errors, f"work_tiers.{unit}.tier inválido: {tier}")
        for key in ["rationale", "classified_at", "classified_by"]:
            if not isinstance(entry.get(key), str) or not entry.get(key):
                fail(errors, f"work_tiers.{unit}.{key} debe ser string no vacío")
    if mode == "initialized":
        for module in module_lists["completed"] + module_lists["in_progress"]:
            if module not in work_tiers:
                fail(errors, f"03_project_state.json.work_tiers no declara tier para módulo activo/completado: {module}")
                continue
            module_tier = work_tiers[module].get("tier") if isinstance(work_tiers[module], dict) else None
            if module_tier == "tier_0":
                fail(errors, f"03_project_state.json.work_tiers.{module} es tier_0, pero un módulo activo/completado no puede ser efímero")
            if module_tier == "tier_2" and work_tiers[module].get("requires_second_review") is not True:
                fail(errors, f"03_project_state.json.work_tiers.{module} es tier_2 y debe declarar requires_second_review=true")

    module_specs = require_type(errors, state, "module_specs", dict, "03_project_state.json") or {}
    for module_id, entry in module_specs.items():
        if not isinstance(entry, dict):
            fail(errors, f"module_specs.{module_id} debe ser objeto")
            continue
        data_model = entry.get("data_model")
        api_contract = entry.get("api_contract")
        if data_model not in VALID_SPEC_STATUS:
            fail(errors, f"module_specs.{module_id}.data_model inválido: {data_model}")
        if api_contract not in VALID_SPEC_STATUS:
            fail(errors, f"module_specs.{module_id}.api_contract inválido: {api_contract}")
        if (data_model == "n/a" or api_contract == "n/a") and not entry.get("justification"):
            fail(errors, f"module_specs.{module_id} marca 'n/a' pero no declara justification")
        classified_by = entry.get("classified_by")
        if classified_by not in VALID_SPEC_CLASSIFIER:
            fail(errors, f"module_specs.{module_id}.classified_by inválido: {classified_by}")
        classified_at = entry.get("classified_at")
        if not isinstance(classified_at, str) or not classified_at:
            fail(errors, f"module_specs.{module_id}.classified_at debe ser string ISO8601 no vacío")

    for rejection in closure_rejections:
        if not isinstance(rejection, dict):
            fail(errors, f"closure_rejections contiene entrada no dict: {rejection!r}")
            continue
        for key in ["module_id", "reason", "timestamp"]:
            if key not in rejection:
                fail(errors, f"closure_rejections entry sin {key}: {rejection!r}")
        reason = rejection.get("reason")
        if reason not in VALID_CLOSURE_REASONS:
            fail(errors, f"closure_rejections.reason inválido: {reason}")
        timestamp = rejection.get("timestamp")
        if not isinstance(timestamp, str) or not re.match(r"^\d{4}-\d{2}-\d{2}", timestamp):
            fail(errors, f"closure_rejections.timestamp debe ser ISO8601/date-like: {timestamp}")

    refs = require_type(errors, state, "_refs", dict, "03_project_state.json") or {}
    for key, ref in refs.items():
        if not isinstance(ref, str):
            fail(errors, f"_refs.{key} debe ser string")
        elif not (ROOT / ref).exists():
            fail(errors, f"_refs.{key} apunta a archivo inexistente: {ref}")

    execution_profile = state.get("execution_profile")
    if execution_profile is not None:
        if not isinstance(execution_profile, dict):
            fail(errors, "03_project_state.json.execution_profile debe ser objeto")
        elif execution_profile.get("capability_tier") not in VALID_CAPABILITY_TIERS:
            fail(errors, f"execution_profile.capability_tier inválido: {execution_profile.get('capability_tier')} (strong|standard|limited)")
    elif mode == "initialized":
        fail(
            errors,
            "03_project_state.json no declara execution_profile; en modo initialized "
            "es obligatorio (constitution rule 4 + GUARDRAILS_MASTER → graduación de "
            "rigor por capacidad del ejecutor). Declarar capability_tier "
            "(strong|standard|limited) en execution_profile.",
        )

    if mode != "template":
        # Chequeo ESTRUCTURADO de placeholders en el state (TL FW-012): el scan
        # de texto completo se retiró de este archivo (la prosa de auditoría
        # puede citar [COMPLETAR] legítimamente), así que los campos cortos de
        # identidad — donde un placeholder solo puede significar bootstrap a
        # medias — se verifican campo por campo.
        if state.get("project") == "[NOMBRE_PROYECTO]":
            fail(errors, "Proyecto inicializado no puede conservar [NOMBRE_PROYECTO]")
        elif isinstance(state.get("project"), str) and PLACEHOLDER_RE.search(state["project"]):
            fail(errors, f"Placeholder sin resolver en 03_project_state.json.project: {state['project']!r}")
        stack = state.get("stack")
        if isinstance(stack, dict):
            for field, value in stack.items():
                if isinstance(value, str) and PLACEHOLDER_RE.search(value):
                    fail(
                        errors,
                        f"Placeholder sin resolver en 03_project_state.json.stack.{field}: {value!r}",
                    )

    return mode


def validate_placeholders(mode: str, errors: list[str], warnings: list[str]) -> None:
    # _project/03_project_state.json queda fuera a propósito (TL FW-012): sus
    # campos de prosa libre (workflow_receipts.evidence, _decisions) pueden CITAR
    # legítimamente el token [COMPLETAR] al documentar una auditoría. Los campos
    # de identidad (project, stack.*) se verifican campo por campo en
    # validate_state, donde un placeholder sí es señal dura de bootstrap a medias.
    critical_files = [
        "CLAUDE.md",
        "_project/04_active_scope.md",
        "_project/08_module_acceptance.md",
        "_project/10_arch_constraints.md",
    ]
    for filename in critical_files:
        path = ROOT / filename
        if contains_placeholder(path):
            if mode != "template":
                fail(errors, f"Placeholder sin resolver en proyecto inicializado: {filename}")


def validate_secondary_placeholders(mode: str, warnings: list[str]) -> None:
    """Warn about unresolved template placeholders in non-critical project docs.

    Critical state/scope files remain hard failures via validate_placeholders().
    These secondary files may be empty or intentionally generic in early adoption,
    so initialized projects get a warning rather than a blocking error.
    """
    if mode != "initialized":
        return
    for filename in SECONDARY_PLACEHOLDER_FILES:
        path = ROOT / filename
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        if file_has_unresolved_placeholder(text):
            warn(warnings, f"Placeholder secundario sin resolver en proyecto inicializado: {filename}")


def normalize_dependency_name(name: str) -> str:
    return name.strip().lower().replace("_", "-")


def parse_requirement_name(line: str) -> str | None:
    stripped = line.strip()
    if not stripped or stripped.startswith("#"):
        return None
    if stripped.startswith(("-r ", "--requirement", "-c ", "--constraint", "-e ")):
        return None
    if re.match(r"^(git\+|https?://|file:)", stripped, re.IGNORECASE):
        return None
    match = REQUIREMENT_NAME_RE.match(stripped)
    if not match:
        return None
    return normalize_dependency_name(match.group(1))


def state_dependency_names(state: dict[str, Any]) -> set[str]:
    names: set[str] = set()
    dependencies = state.get("dependencies")
    if not isinstance(dependencies, list):
        return names
    for item in dependencies:
        if isinstance(item, str):
            parsed = parse_requirement_name(item) or normalize_dependency_name(item)
            if parsed:
                names.add(parsed)
        elif isinstance(item, dict):
            name = item.get("name") or item.get("package")
            if isinstance(name, str) and name.strip():
                names.add(normalize_dependency_name(name))
    return names


def validate_dependencies_requirements_alignment(
    mode: str,
    state: dict[str, Any],
    warnings: list[str],
) -> None:
    """Warn when requirements.txt (or pyproject.toml, TL-FW-314) and 03_project_state.dependencies drift.

    The state field is the framework's durable dependency intent. requirements.txt
    is executable input. In initialized projects, runtime packages present in the
    file should be reflected in state so migrations/reviews do not miss them.
    """
    if mode != "initialized":
        return
    # TL-FW-314: sin requirements.txt, las dependencias de ejecución de pyproject.toml.
    source = "requirements.txt" if (ROOT / "requirements.txt").exists() else "pyproject.toml"
    try:
        if source == "requirements.txt":
            requirement_names = {parse_requirement_name(line) for line in
                                 (ROOT / source).read_text(encoding="utf-8", errors="ignore").splitlines()}
        else:  # PEP 621 o Poetry; ni extras ni grupos de desarrollo
            data = tomllib.loads((ROOT / source).read_text(encoding="utf-8"))
            requirement_names = {parse_requirement_name(spec) for spec in
                                 (data.get("project") or {}).get("dependencies") or [] if isinstance(spec, str)}
            poetry = (data.get("tool") or {}).get("poetry", {}).get("dependencies") or {}
            requirement_names |= {normalize_dependency_name(k) for k in poetry if k.lower() != "python"}
    except (OSError, ValueError, AttributeError, TypeError):
        requirement_names = set()
    missing = sorted(requirement_names - {None} - state_dependency_names(state))
    if missing:
        warn(
            warnings,
            f"{source} declara dependencias no reflejadas en "
            f"03_project_state.json.dependencies: {', '.join(missing)}",
        )
    _warn_state_dependency_pins(state, warnings)


PIN_RE = re.compile(r"^\s*([A-Za-z0-9_.-]+)(?:\[[^\]]+\])?\s*==\s*([A-Za-z0-9_.+!-]+)")


def _warn_state_dependency_pins(state: dict[str, Any], warnings: list[str]) -> None:
    """TL-FW-314: un paquete con versiones distintas en el estado, o un pin que no coincide con requirements.lock."""
    pins: dict[str, set[str]] = {}
    deps = state.get("dependencies")
    for match in (PIN_RE.match(d) for d in (deps if isinstance(deps, list) else []) if isinstance(d, str)):
        if match:
            pins.setdefault(normalize_dependency_name(match.group(1)), set()).add(match.group(2))
    try:
        lock_lines = (ROOT / "requirements.lock").read_text(encoding="utf-8", errors="ignore").splitlines()
    except OSError:
        lock_lines = []
    lock = {normalize_dependency_name(m.group(1)): m.group(2) for m in map(PIN_RE.match, lock_lines) if m}
    repeated = sorted(name for name, versions in pins.items() if len(versions) > 1)
    if repeated:
        locked = ", ".join(f"{name}=={lock[name]}" for name in repeated if name in lock)
        warn(warnings, "03_project_state.json.dependencies repite paquetes con versiones distintas: "
             + ", ".join(f"{name} ({', '.join(sorted(pins[name]))})" for name in repeated)
             + (f"; requirements.lock fija {locked}" if locked else "")
             + ". La entrada antigua no se puede retirar todavía con el motor (TL-FW-314, enero)")
    stale = [f"{name}=={v} (lock {lock[name]})" for name, (v, *more) in sorted(pins.items())
             if not more and name in lock and v != lock[name]]
    if stale:
        warn(warnings, f"pins de 03_project_state.json.dependencies que no coinciden con requirements.lock: {', '.join(stale)}")


def validate_dependency_lockfile_presence(
    mode: str,
    state: dict[str, Any],
    warnings: list[str],
) -> None:
    """Warn early when pinned deps exist but no lockfile does (TL FW-010).

    workflow_receipts.py's dependency gate requires the exact pin in BOTH the
    manifest and a lockfile. A project that never adopted a lockfile only
    discovers this at the Nth dependency approval, mid-module. Surfacing it as
    a doctor WARN moves the friction to the first doctor run.
    """
    if mode != "initialized":
        return
    deps = state.get("dependencies") or []
    if not isinstance(deps, list) or not deps:
        return
    # FW-159 (cosechado de Research App, PA FW-105): el aviso solo es útil
    # cuando ya se adoptaron pins exactos. Antes de eso, crear un lockfile es
    # una decisión de supply-chain aparte, y el propio receipt de dependencias
    # permite explícitamente un manifiesto sin pinear — así que el WARN salía en
    # proyectos que no estaban haciendo nada mal, que es como se enseña a
    # ignorar los avisos del doctor.
    exact_pin_re = re.compile(r"^[A-Za-z0-9_.-]+==[A-Za-z0-9_.+-]+$")
    if not any(isinstance(dep, str) and exact_pin_re.fullmatch(dep.strip()) for dep in deps):
        return
    import workflow_receipts as _wr
    if not any((ROOT / name).exists() for name in _wr.PYTHON_MANIFESTS):
        return
    if any((ROOT / name).exists() for name in _wr.PYTHON_LOCKFILES):
        return
    warn(
        warnings,
        "03_project_state.json declara dependencies pero no existe ningún lockfile "
        "(requirements.lock/uv.lock/poetry.lock); el receipt gate de dependencias "
        "fallará al aprobar la siguiente. Crear uno (p. ej. `pip freeze > requirements.lock`).",
    )


# PA-FW-007: ficheros de test versionados fuera de las carpetas del framework y de sus hosts.
PRODUCT_TEST_FILE_RE = re.compile(
    r"^(?!\.agent/|_project/|\.claude/|\.codex/|\.agents/|\.opencode/)"
    r"(?:.*/)?(?:(?:tests?|__tests__|spec)/.*|test_[^/]*|[^/]*_test|[^/]*\.(?:test|spec))\.(?:py|[cm]?[jt]sx?)$")
STATE_READ_RE = re.compile(
    r"""(?:Path|open|readFileSync)\(\s*['"](?:\./)?_project/(?:03_project_state\.json|state_log\.jsonl)"""
    r"""|\b[A-Z][A-Z0-9_]*\s*/\s*['"]_project['"]\s*/\s*['"](?:03_project_state\.json|state_log\.jsonl)""")


def validate_product_tests_read_state(mode: str, warnings: list[str]) -> None:
    """PA-FW-007: tests de producto que leen el estado canónico real. Se saltan los que nombran
    `.agent` (tests del framework hospedados, FW-154, o del motor)."""
    readers = []
    for rel in sorted((_git_tracked_set() or set()) if mode == "initialized" else ()):
        if not PRODUCT_TEST_FILE_RE.match(rel):
            continue
        try:
            text = (ROOT / rel).read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        if not re.search(r"""['"/]\.agent\b""", text) and any(STATE_READ_RE.search(line) for line in text.splitlines()
                                        if not line.lstrip().startswith(("#", "//"))):
            readers.append(rel)
    if readers:
        warn(warnings, f"tests de producto leen el estado canónico real: {', '.join(readers)}. Si dependen del "
             "estado de un módulo, un bug que lo bloquee rompe la suite y el pre-commit rechaza el propio arreglo "
             "(PA-FW-007). Pásales un estado de prueba en una carpeta temporal")


def camel_to_snake(value: str) -> str:
    first = re.sub(r"(.)([A-Z][a-z]+)", r"\1_\2", value)
    second = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", first)
    return second.lower()


def snake_to_camel(value: str) -> str:
    parts = value.split("_")
    if len(parts) < 2:
        return value
    return parts[0] + "".join(part[:1].upper() + part[1:] for part in parts[1:])


def protected_terms() -> list[str]:
    invariants = ROOT / "_project" / "03_invariants.md"
    if not invariants.exists():
        return []
    terms: list[str] = []
    for match in PROTECTED_TERM_RE.finditer(invariants.read_text(encoding="utf-8", errors="ignore")):
        term = match.group(1).strip()
        if not term or "[" in term or "]" in term:
            continue
        terms.append(term)
    return terms


def protected_term_variants(term: str) -> set[str]:
    variants: set[str] = set()
    snake = camel_to_snake(term)
    if snake != term:
        variants.add(snake)
    camel = snake_to_camel(term)
    if camel != term:
        variants.add(camel)
    if "-" in term:
        variants.add(term.replace("-", "_"))
    if "_" in term:
        variants.add(term.replace("_", "-"))
    # PA FW-044: una variante que solo difiere en casing ("tesis" vs "Tesis")
    # es el mismo término en prosa, no un renombre semántico — reportarla
    # enterraba la señal útil del doctor bajo decenas de warnings por línea.
    return {
        variant
        for variant in variants
        if variant and variant.casefold() != term.casefold()
    }


def contains_token(text: str, token: str) -> bool:
    return re.search(rf"(?<![A-Za-z0-9_]){re.escape(token)}(?![A-Za-z0-9_])", text) is not None


def validate_invariant_term_drift(mode: str, warnings: list[str]) -> None:
    """Warn when technical docs use a common variant of a protected domain term."""
    if mode != "initialized":
        return
    for term in protected_terms():
        variants = protected_term_variants(term)
        if not variants:
            continue
        for filename in INVARIANT_DRIFT_SCAN_FILES:
            path = ROOT / filename
            if not path.exists():
                continue
            for line_no, line in enumerate(path.read_text(encoding="utf-8", errors="ignore").splitlines(), 1):
                if INVARIANT_VARIANT_ALLOWLIST_RE.search(line):
                    continue
                for variant in sorted(variants):
                    if contains_token(line, variant):
                        warn(
                            warnings,
                            f"Posible drift de término protegido: {filename}:{line_no} "
                            f"usa {variant!r}; 03_invariants.md protege {term!r}",
                        )
                        break


def validate_clarification_schema_policy(errors: list[str]) -> None:
    """Require clarifications to distinguish start/implementation/closure blocking."""
    paths = [
        ROOT / "_project" / "07_pending_clarifications.md",
        ROOT / ".agent" / "workflows" / "clarificar.md",
        ROOT / ".agent" / "workflows" / "scope-siguiente.md",
        ROOT / ".agent" / "workflows" / "cierre-modulo.md",
    ]
    for path in paths:
        if not path.exists():
            fail(errors, f"{rel(path)} faltante para política de clarificaciones estructuradas")
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        for field in CLARIFICATION_STRUCTURED_FIELDS:
            if field not in text:
                fail(errors, f"{rel(path)} no declara campo de clarificación estructurada: {field}")


def validate_clarification_entries(mode: str, warnings: list[str]) -> None:
    """Warn when initialized projects keep open CLARs in the legacy ambiguous shape."""
    if mode != "initialized":
        return
    path = ROOT / "_project" / "07_pending_clarifications.md"
    if not path.exists():
        return
    text = path.read_text(encoding="utf-8", errors="ignore")
    for match in CLAR_BLOCK_RE.finditer(text):
        block = match.group(0)
        clar_id = match.group(1)
        if not re.search(r"\*\*Estado:\*\*\s*Abierta\b", block, re.IGNORECASE):
            continue
        missing = [field for field in CLARIFICATION_STRUCTURED_FIELDS if field not in block]
        if missing:
            warn(
                warnings,
                f"{clar_id} abierta sin campos estructurados ({', '.join(missing)}); "
                "no se puede distinguir si bloquea apertura, implementación o cierre",
            )


def validate_state_narrative_consistency(
    mode: str,
    state: dict[str, Any],
    warnings: list[str],
) -> None:
    """Warn when docs cite a module status that contradicts 03_project_state.json.

    This intentionally stays narrow: it only validates explicit citations like
    `module_statuses.foo: completed`. Broader prose ("pendiente", "en progreso")
    can describe historical snapshots and would be noisy to lint mechanically.
    """
    if mode != "initialized":
        return
    statuses = state.get("module_statuses")
    if not isinstance(statuses, dict):
        return
    for filename in STATE_NARRATIVE_FILES:
        path = ROOT / filename
        if not path.exists():
            continue
        for line_no, line in enumerate(path.read_text(encoding="utf-8", errors="ignore").splitlines(), 1):
            for match in MODULE_STATUS_CITATION_RE.finditer(line):
                module, cited_status = match.group(1), match.group(2)
                actual_status = statuses.get(module)
                if actual_status in VALID_MODULE_STATUSES and cited_status != actual_status:
                    warn(
                        warnings,
                        f"Posible evidencia narrativa obsoleta: {filename}:{line_no} cita "
                        f"module_statuses.{module}: {cited_status}, pero "
                        f"03_project_state.json declara {actual_status}",
                    )


def remove_html_comments(text: str) -> str:
    return re.sub(r"<!--.*?-->", "", text, flags=re.DOTALL)


def remove_fenced_code_blocks(text: str) -> str:
    return re.sub(r"(?ms)^```.*?^```", "", text)


def validate_template_cleanliness(mode: str, errors: list[str]) -> None:
    if mode != "template":
        return

    forbidden_project_history = ROOT / "_project" / "framework_history.md"
    if forbidden_project_history.exists():
        fail(errors, "_project/framework_history.md no debe existir en modo template; el historial interno vive en .agent/docs/")

    state = ROOT / "_project" / "03_project_state.json"
    if state.exists():
        state_text = state.read_text(encoding="utf-8", errors="ignore")
        if "Framework v3.0 enriquecido" in state_text:
            fail(errors, "03_project_state.json contiene historial real del framework; en template debe describir solo el estado base")

    brief = ROOT / "_project" / "00_session_brief.json"
    if brief.exists():
        brief_text = brief.read_text(encoding="utf-8", errors="ignore")
        if "Framework v3.0 enriquecido" in brief_text:
            fail(errors, "00_session_brief.json contiene historial real del framework; en template debe describir solo el estado base")
        try:
            state_data = json.loads(state.read_text(encoding="utf-8")) if state.exists() else None
            brief_data = json.loads(brief_text)
            if isinstance(state_data, dict):
                semantic_diff = projections.projection_semantic_diff(state_data, brief_data)
                if semantic_diff:
                    fail(
                        errors,
                        "00_session_brief.json está desfasado semánticamente respecto a "
                        f"03_project_state.json: {', '.join(sorted(semantic_diff))}",
                    )
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
            fail(errors, f"00_session_brief.json no se puede contrastar con el estado canónico: {exc}")

    journal = ROOT / "_project" / "13_session_journal.md"
    if journal.exists():
        journal_text = remove_html_comments(journal.read_text(encoding="utf-8", errors="ignore"))
        if re.search(r"(?m)^-\s+\[\d{4}-\d{2}-\d{2}\]", journal_text) or "[framework]" in journal_text:
            fail(errors, "13_session_journal.md contiene entradas reales; en framework_mode=template debe estar limpio")

    arch_decisions = ROOT / "_project" / "03_arch_decisions.md"
    if arch_decisions.exists():
        adr_text = remove_html_comments(arch_decisions.read_text(encoding="utf-8", errors="ignore"))
        if re.search(r"(?m)^### ADR-\d+", adr_text) or "**Módulo afectado:** framework" in adr_text:
            fail(errors, "03_arch_decisions.md contiene ADRs reales del framework; en template debe estar limpio")

    core = ROOT / "_project" / "core.md"
    if core.exists():
        core_text = remove_html_comments(core.read_text(encoding="utf-8", errors="ignore"))
        if re.search(r"\*\*Último evento\*\*:[ \t]+\S", core_text):
            fail(errors, "_project/core.md tiene 'Último evento' con contenido; en template debe estar vacío hasta que un proyecto lo adopte")
        if re.search(r"\*\*Fase\*\*:\s*phase_0[2-9]", core_text):
            fail(errors, "_project/core.md muestra una fase posterior a phase_01; en template solo phase_01 es válido")


def validate_technical_plan_artifacts(mode: str, errors: list[str]) -> None:
    """Verify the formal SDD specs (ADR-012/ADR-013) exist and stay clean in template mode."""
    data_model = ROOT / "_project" / "02_technical_plan" / "data_model.md"
    api_contract = ROOT / "_project" / "02_technical_plan" / "api_contract.yaml"

    if not data_model.exists():
        fail(errors, "_project/02_technical_plan/data_model.md faltante (plantilla SDD)")
    if not api_contract.exists():
        fail(errors, "_project/02_technical_plan/api_contract.yaml faltante (plantilla SDD)")
    else:
        text = api_contract.read_text(encoding="utf-8", errors="ignore")
        for needle in ["openapi:", "info:", "paths:", "components:"]:
            if needle not in text:
                fail(errors, f"api_contract.yaml no contiene clave OpenAPI mínima: {needle}")
                break

    if mode == "template" and data_model.exists():
        text = data_model.read_text(encoding="utf-8", errors="ignore")
        match = re.search(r"```mermaid\s+erDiagram(.*?)```", text, re.DOTALL)
        if match:
            body = match.group(1)
            real_lines = [
                ln.strip()
                for ln in body.splitlines()
                if ln.strip() and not ln.strip().startswith("%%")
            ]
            if real_lines:
                fail(errors, "data_model.md contiene entidades Mermaid en modo template (debe estar limpio)")

    if mode == "template" and api_contract.exists():
        text = api_contract.read_text(encoding="utf-8", errors="ignore")
        if not re.search(r"(?m)^paths:\s*\{\}\s*$", text):
            fail(errors, "api_contract.yaml en modo template debe declarar paths: {} sin endpoints")
        if not re.search(r"(?m)^\s*schemas:\s*\{\}\s*$", text):
            fail(errors, "api_contract.yaml en modo template debe declarar components.schemas: {} sin schemas")


def extract_openapi_paths(api_text: str) -> set[str]:
    if re.search(r"(?m)^paths:\s*\{\}\s*$", api_text):
        return set()
    paths: set[str] = set()
    in_paths = False
    for line in api_text.splitlines():
        if re.match(r"^paths:\s*$", line):
            in_paths = True
            continue
        if in_paths and re.match(r"^[A-Za-z0-9_.-]+:\s*", line):
            break
        if in_paths:
            match = re.match(r"^\s{2}['\"]?(/[^:'\"]+)['\"]?:\s*$", line)
            if match:
                paths.add(match.group(1).strip())
    return paths


def _extract_core_value(text: str, label: str) -> str | None:
    pattern = re.compile(
        rf"(?im)^\s*-\s*\*\*{re.escape(label)}\*\*\s*:\s*`?([^`\n]+?)`?\s*$"
    )
    match = pattern.search(text)
    if not match:
        return None
    return match.group(1).strip()


def validate_core_tier_consistency(mode: str, state: dict[str, Any], warnings: list[str]) -> None:
    """Warn when core.md active module tier drifts from 03_project_state.json."""
    if mode != "initialized":
        return
    core = ROOT / "_project" / "core.md"
    if not core.exists():
        return
    text = core.read_text(encoding="utf-8", errors="ignore")
    module_id = _extract_core_value(text, "ID")
    core_tier = _extract_core_value(text, "Tier de rigor")
    if not module_id or module_id in {"ninguno", "[module_id o \"ninguno\"]"}:
        return
    if not core_tier or core_tier == "no clasificado":
        return
    work_tiers = state.get("work_tiers")
    if not isinstance(work_tiers, dict):
        return
    entry = work_tiers.get(module_id)
    if not isinstance(entry, dict):
        return
    state_tier = entry.get("tier")
    if state_tier in VALID_WORK_TIERS and core_tier in VALID_WORK_TIERS and state_tier != core_tier:
        warn(
            warnings,
            f"Posible drift de tier: core.md declara {module_id} como {core_tier}, "
            f"pero 03_project_state.json.work_tiers['{module_id}'].tier es {state_tier}",
        )


def validate_core_markdown_consistency(mode: str, state: dict[str, Any], warnings: list[str]) -> None:
    """N6 / FW-260: Warn if core.md on disk drifts from the in-memory render of canonical state."""
    if mode != "initialized":
        return
    core = ROOT / "_project" / "core.md"
    if not core.exists():
        return
    if not isinstance(state, dict):
        return

    try:
        disk_text = core.read_text(encoding="utf-8", errors="ignore")
    except OSError as exc:
        warn(warnings, f"No se pudo leer _project/core.md para verificar coherencia: {exc}")
        return

    try:
        rendered_text = projections.render_core_markdown(state, workspace_root=ROOT)
    except Exception as exc:
        warn(warnings, f"No se pudo renderizar _project/core.md en memoria: {exc}")
        return

    def _normalize(text: str) -> str:
        unified = text.replace("\r\n", "\n")
        without_ts = re.sub(
            r"(?m)^-\s+\*\*Última actualización\*\*:\s+.*$",
            "- **Última actualización**: <TIMESTAMP>",
            unified,
        )
        return "\n".join(line.rstrip() for line in without_ts.splitlines()).strip()

    if _normalize(disk_text) != _normalize(rendered_text):
        warn(
            warnings,
            "_project/core.md no coincide con el estado (¿editado a mano?); "
            "regenerar con python3 .agent/scripts/state_manager.py render",
        )


def validate_phase_consistency(mode: str, state: dict[str, Any], warnings: list[str]) -> None:
    """Warn when current_phase drifts from the active phase declaration.

    `/scope-siguiente` can open the first module of a new phase yet forget to bump
    current_phase. The current phase token must appear in the `**Fase**` line of
    04_active_scope.md and core.md. We assert presence rather than first-token
    equality, so transition phrasing like "phase_00 cerrada; phase_01 ..." stays
    valid while stale headers still surface.
    """
    if mode != "initialized":
        return
    current = state.get("current_phase")
    if not isinstance(current, str) or not re.match(r"^phase_\d{2}$", current):
        return
    for rel_path in ["_project/04_active_scope.md", "_project/core.md"]:
        path = ROOT / rel_path
        if not path.exists():
            continue
        fase_line = None
        for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
            if "**Fase" in line:
                fase_line = line
                break
        if fase_line is None:
            continue
        if re.search(r"phase_\d{2}", fase_line) and current not in fase_line:
            warn(
                warnings,
                f"Posible drift de fase: {rel_path} declara una fase distinta de "
                f"current_phase='{current}' (03_project_state.json). Línea Fase: "
                f"«{fase_line.strip()[:90]}»",
            )


def validate_contracts_sync(mode: str, warnings: list[str]) -> None:
    """Warn when the human index and formal OpenAPI contract drift in initialized projects."""
    if mode != "initialized":
        return
    contracts = ROOT / "_project" / "03_contracts.md"
    api_contract = ROOT / "_project" / "02_technical_plan" / "api_contract.yaml"
    if not contracts.exists() or not api_contract.exists():
        return

    contracts_text = remove_fenced_code_blocks(
        contracts.read_text(encoding="utf-8", errors="ignore")
    )
    api_text = api_contract.read_text(encoding="utf-8", errors="ignore")
    paths = extract_openapi_paths(api_text)
    pointers = {
        pointer.rstrip(".,;)")
        for pointer in re.findall(r"api_contract\.yaml#paths\.([^\s`)]+)", contracts_text)
    }

    for path in sorted(paths - pointers):
        warn(warnings, f"03_contracts.md no contiene puntero a api_contract.yaml#paths.{path}")
    for pointer in sorted(pointers - paths):
        warn(warnings, f"03_contracts.md apunta a endpoint inexistente en api_contract.yaml: {pointer}")

    for line_no, line in enumerate(contracts_text.splitlines(), 1):
        has_http_signature = re.search(r"\b(GET|POST|PUT|PATCH|DELETE)\s+/", line, re.IGNORECASE)
        has_pointer = "api_contract.yaml#paths." in line
        has_schema_detail = re.search(r"\b(request|response|schema|body|payload|status)\b", line, re.IGNORECASE)
        if has_http_signature and has_schema_detail and not has_pointer:
            warn(warnings, f"03_contracts.md parece duplicar contrato HTTP en línea {line_no}; usar solo puntero al YAML")


def openapi_schema_blocks(api_text: str) -> dict[str, str]:
    """Very small OpenAPI schema-block extractor for doctor linting.

    The framework avoids a PyYAML runtime dependency, so this intentionally covers
    the common emitted shape: schemas declared two levels under `components`.
    """
    headers: list[tuple[str, int, int]] = []
    for match in re.finditer(r"(?m)^ {4}([A-Za-z0-9_]+):\s*$", api_text):
        headers.append((match.group(1), match.start(), match.end()))
    blocks: dict[str, str] = {}
    for index, (name, _start, end) in enumerate(headers):
        next_start = headers[index + 1][1] if index + 1 < len(headers) else len(api_text)
        blocks[name] = api_text[end:next_start]
    return blocks


def openapi_required_fields(schema_block: str) -> set[str]:
    fields: set[str] = set()
    in_required = False
    for line in schema_block.splitlines():
        if re.match(r"^ {6}required:\s*$", line):
            in_required = True
            continue
        if in_required:
            item = re.match(r"^ {8}-\s*([A-Za-z0-9_]+)\s*$", line)
            if item:
                fields.add(item.group(1))
                continue
            if line.strip() and not line.startswith(" " * 8):
                in_required = False
    return fields


def openapi_allof_refs(schema_block: str) -> list[str]:
    return re.findall(r"\$ref:\s*['\"]?#/components/schemas/([A-Za-z0-9_]+)['\"]?", schema_block)


def validate_openapi_input_inheritance(mode: str, warnings: list[str]) -> None:
    """Warn when request schemas inherit output schemas with generated fields."""
    if mode != "initialized":
        return
    api_contract = ROOT / "_project" / "02_technical_plan" / "api_contract.yaml"
    if not api_contract.exists():
        return
    api_text = api_contract.read_text(encoding="utf-8", errors="ignore")
    blocks = openapi_schema_blocks(api_text)
    required_by_schema = {
        name: openapi_required_fields(block)
        for name, block in blocks.items()
    }
    for schema_name, block in blocks.items():
        if not OPENAPI_INPUT_SCHEMA_RE.search(schema_name):
            continue
        for ref in openapi_allof_refs(block):
            generated = sorted(required_by_schema.get(ref, set()) & SERVER_GENERATED_FIELDS)
            if generated:
                warn(
                    warnings,
                    f"api_contract.yaml: schema de entrada {schema_name} usa allOf sobre "
                    f"{ref}, que requiere campos generados/derivados ({', '.join(generated)}). "
                    "Separar input de output.",
                )


def _has_pyyaml() -> bool:
    try:
        import yaml  # noqa: F401
        return yaml is not None
    except ImportError:
        return False


def validate_api_contract_runtime_dependencies(mode: str, warnings: list[str]) -> None:
    """Advierte si api_contract.yaml define endpoints pero PyYAML no está instalado.

    Por qué se acepta PyYAML en context_sources.py y no en el doctor:
    La tarea de context_sources.py (resolución de closure de $ref + reserialización
    de un subconjunto YAML filtrado y válido) es cualitativamente distinta de las
    comprobaciones estáticas de existencia de campos que hace framework_doctor.py
    con expresiones regulares. No es una inconsistencia, sino un alcance técnico
    distinto: generar YAML válido filtrado requiere un parser/dumper real.
    """
    if mode == "template":
        return
    api_contract = ROOT / "_project" / "02_technical_plan" / "api_contract.yaml"
    if not api_contract.exists():
        return
    api_text = api_contract.read_text(encoding="utf-8", errors="ignore")
    if re.search(r"(?m)^paths:\s*\{\}\s*$", api_text):
        return
    if not _has_pyyaml():
        warn(
            warnings,
            "api_contract.yaml contiene endpoints declarados pero PyYAML no está disponible en el entorno (`import yaml` falló); "
            "context_sources.py requiere PyYAML para resolver contratos OpenAPI en vistas de módulo. "
            "Instalar con `pip install pyyaml` antes de que un workflow que consulte contexto de módulo "
            "con contrato choque con DEPENDENCY_MISSING a mitad de tarea.",
        )



def validate_publicar_github_workflow_gates(errors: list[str]) -> None:
    workflow = ROOT / ".agent" / "workflows" / "publicar-github.md"
    if not workflow.exists():
        fail(errors, ".agent/workflows/publicar-github.md faltante")
        return
    text = workflow.read_text(encoding="utf-8", errors="ignore")
    lower = text.lower()
    required = [
        ("repo identity", "gate repo identity faltante en /publicar-github"),
        ("git identity", "gate Git identity faltante en /publicar-github"),
        ("verde local", "gate de verde local antes de red faltante en /publicar-github"),
        ("current_phase", "reconciliación de fase/tag faltante en /publicar-github"),
        ("git init", "decisión explícita de git init faltante en /publicar-github"),
    ]
    for needle, message in required:
        if needle.lower() not in lower:
            fail(errors, message)


def validate_arrancar_post_interruption_wiring(errors: list[str]) -> None:
    workflow = ROOT / ".agent" / "workflows" / "arrancar.md"
    if not workflow.exists():
        fail(errors, ".agent/workflows/arrancar.md faltante")
        return
    text = workflow.read_text(encoding="utf-8", errors="ignore")
    required = [
        ("Post-interruption audit", "arrancar.md no declara bloque Post-interruption audit"),
        ("doctor/tests", "arrancar.md no exige doctor/tests en Post-interruption audit"),
        ("diff de ACs", "arrancar.md no exige diff de ACs en Post-interruption audit"),
        ("no escribir", "arrancar.md no bloquea escritura durante Post-interruption audit"),
        ("no red", "arrancar.md no bloquea no red durante Post-interruption audit"),
    ]
    for needle, message in required:
        if needle.lower() not in text.lower():
            fail(errors, message)


def validate_reconciliar_workflow_wiring(manifest: dict[str, Any], errors: list[str]) -> None:
    commands = manifest.get("commands", []) if isinstance(manifest, dict) else []
    reconciliar = None
    if isinstance(commands, list):
        for command in commands:
            if isinstance(command, dict) and command.get("name") == "reconciliar":
                reconciliar = command
                break
    else:
        fail(errors, "manifest.json: commands debe ser una lista para validar /reconciliar")

    if reconciliar is None:
        fail(errors, "reconciliar no está declarado en manifest.json")
    else:
        if reconciliar.get("source") != ".agent/workflows/reconciliar.md":
            fail(errors, "reconciliar.source debe ser .agent/workflows/reconciliar.md en manifest.json")
        if reconciliar.get("claude_command") is not True:
            fail(errors, "reconciliar debe ser comando público (claude_command:true) en manifest.json")

    workflow = ROOT / ".agent" / "workflows" / "reconciliar.md"
    if not workflow.exists():
        fail(errors, ".agent/workflows/reconciliar.md faltante")
    else:
        text = workflow.read_text(encoding="utf-8", errors="ignore").lower()
        required = [
            ("no escribir", "reconciliar.md no bloquea escritura durante reconciliación"),
            ("no red", "reconciliar.md no bloquea red durante reconciliación"),
            ("cambio de intención", "reconciliar.md no cubre cambio de intención"),
            ("recap", "reconciliar.md no cubre recap inconsistente"),
            ("doctor/tests", "reconciliar.md no exige doctor/tests"),
            ("siguiente:", "reconciliar.md no declara orientación de flujo"),
            ("tabla de autoridad", "reconciliar.md no declara precedencia disco-vs-disco entre artefactos"),
        ]
        for needle, message in required:
            if needle.lower() not in text:
                fail(errors, message)

    arrancar = ROOT / ".agent" / "workflows" / "arrancar.md"
    if not arrancar.exists():
        fail(errors, ".agent/workflows/arrancar.md faltante para enlazar /reconciliar")
    else:
        text = arrancar.read_text(encoding="utf-8", errors="ignore")
        if ".agent/workflows/reconciliar.md" not in text:
            fail(errors, "arrancar.md no delega la auditoría no lineal a .agent/workflows/reconciliar.md")
        if "cambio de intención" not in text.lower():
            fail(errors, "arrancar.md no activa reconciliación ante cambio de intención")

    docs = [
        # FW-071 resumió la tabla de comandos builder-facing sin columna de
        # ruta por comando: exigir la ruta literal aquí bloqueaba cualquier
        # AGENTS.md regenerado con el formato nuevo, aunque documente /reconciliar.
        ("AGENTS.md", ROOT / "AGENTS.md", ["/reconciliar"]),
        ("CLAUDE.md", ROOT / "CLAUDE.md", ["/reconciliar", "conversación no lineal"]),
    ]
    for label, path, needles in docs:
        if not path.exists():
            fail(errors, f"{label} faltante para documentar /reconciliar")
            continue
        text = path.read_text(encoding="utf-8", errors="ignore").lower()
        for needle in needles:
            if needle.lower() not in text:
                fail(errors, f"{label} no documenta {needle} para /reconciliar")


def validate_compact_output_policy(errors: list[str]) -> None:
    workflow = ROOT / ".agent" / "workflows" / "nuevo-proyecto.md"
    if not workflow.exists():
        fail(errors, ".agent/workflows/nuevo-proyecto.md faltante")
        return
    text = workflow.read_text(encoding="utf-8", errors="ignore").lower()
    required = ["salida compacta", "ruta + resumen", "no pegar", "solicitud expresa"]
    for needle in required:
        if needle not in text:
            fail(errors, f"nuevo-proyecto.md no declara política de salida compacta ({needle})")


def validate_readme_version_parity(mode: str, errors: list[str]) -> None:
    """README must declare the canonical framework_version from the manifest.

    SOLO en template: ahí README.md es del framework. En `initialized` el README
    pertenece al producto adoptante y no debe acoplar la versión del framework
    (FW-033, misma clase que FW-013). En proyectos inicializados la paridad de
    versión la cubre validate_framework_version_consistency vía
    03_project_state.json.

    Regression guard: the 3.0/3.1 version drift already happened twice
    (see CHANGELOG entry for 3.2)."""
    if mode != "template":
        return
    canonical = manifest_framework_version(errors)
    if canonical is None:
        return
    readme = ROOT / "README.md"
    if not readme.exists():
        return  # la ausencia de README ya falla en main()
    text = readme.read_text(encoding="utf-8", errors="ignore")
    if f"Framework v{canonical}" not in text:
        fail(
            errors,
            f"README.md no declara la versión canónica 'Framework v{canonical}' (manifest.framework_version); deriva de versión",
        )


def validate_guardrails_version_parity(mode: str, errors: list[str]) -> None:
    """README and CLAUDE.md must reference the same Guardrails version that
    GUARDRAILS_MASTER.md declares in its title (same drift class as the
    framework_version 3.0/3.1 drift).

    SOLO en template: README.md/CLAUDE.md son del framework. En `initialized`
    son del producto adoptante y no deben acoplar la versión de Guardrails
    (FW-033)."""
    if mode != "template":
        return
    guardrails = ROOT / "GUARDRAILS_MASTER.md"
    if not guardrails.exists():
        return  # la ausencia ya falla en main()
    first_line = guardrails.read_text(encoding="utf-8", errors="ignore").splitlines()[0]
    match = re.search(r"Guardrails v(\d+\.\d+(?:\.\d+)?)", first_line)
    if not match:
        fail(errors, "GUARDRAILS_MASTER.md no declara 'Guardrails vX.Y' en su título")
        return
    version = match.group(1)
    for label, path in [("README.md", ROOT / "README.md"), ("CLAUDE.md", ROOT / "CLAUDE.md")]:
        if not path.exists():
            continue
        if f"Guardrails v{version}" not in path.read_text(encoding="utf-8", errors="ignore"):
            fail(errors, f"{label} no menciona 'Guardrails v{version}' (versión declarada por GUARDRAILS_MASTER.md); deriva de versión")


def validate_field_report_mechanism(mode: str, errors: list[str]) -> None:
    """El mecanismo de captura del field report debe estar cableado: la regla en
    CLAUDE.md y AGENTS.md, y el recordatorio en /gycd. Anti-regresión de prosa.

    SOLO en template: ahí CLAUDE.md/AGENTS.md son del framework. En `initialized`
    son del producto adoptante y pueden no llevar la regla (el mecanismo es
    opcional para el producto); forzarla reintroduciría el acoplamiento de
    FW-033."""
    if mode != "template":
        return
    required = {
        "framework_mode": "enruta el field report según framework_mode",
        ".agent/docs/field_report.md": "declara el destino interno del template",
        "_feedback/field_report.md": "declara el destino de proyectos inicializados",
    }
    for label in ("CLAUDE.md", "AGENTS.md"):
        path = ROOT / label
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        for needle, reason in required.items():
            if needle not in text:
                fail(errors, f"{label} no {reason} ({needle})")
    gycd = ROOT / ".agent" / "workflows" / "gycd.md"
    if gycd.exists():
        text = gycd.read_text(encoding="utf-8", errors="ignore")
        for needle, reason in required.items():
            if needle not in text:
                fail(errors, f"gycd.md no {reason} ({needle})")


def validate_evolution_policy(errors: list[str]) -> None:
    """La Política de Evolución debe existir y declarar sus secciones canónicas:
    feature freeze y criterios de retroporte (desde v3.5.0), más cadencia por
    lotes, frontera núcleo-eterno/capa-volátil, vigilancia trimestral,
    delegación al harness y regla de resta (v2 "Evolución Sustractiva",
    4.12.0). Es el documento que gobierna qué cambios entran al framework,
    por qué y a qué ritmo."""
    path = ROOT / ".agent" / "docs" / "framework_evolution_policy.md"
    if not path.exists():
        fail(errors, ".agent/docs/framework_evolution_policy.md faltante (política de evolución — v2 Evolución Sustractiva)")
        return
    text = path.read_text(encoding="utf-8", errors="ignore").lower()
    for needle, msg in [
        ("feature freeze", "framework_evolution_policy.md no declara 'feature freeze'"),
        ("retroporte", "framework_evolution_policy.md no declara criterios de retroporte"),
        ("cadencia por lotes", "framework_evolution_policy.md no declara la cadencia por lotes (v2)"),
        ("núcleo eterno", "framework_evolution_policy.md no declara la frontera núcleo eterno / capa volátil (v2)"),
        ("capa volátil", "framework_evolution_policy.md no declara la frontera núcleo eterno / capa volátil (v2)"),
        ("vigilancia trimestral", "framework_evolution_policy.md no declara la vigilancia trimestral de paradigma (v2)"),
        ("delegación al harness", "framework_evolution_policy.md no declara el principio de delegación al harness (v2)"),
        ("regla de resta", "framework_evolution_policy.md no declara la regla de resta (v2)"),
    ]:
        if needle not in text:
            fail(errors, msg)


def validate_native_compaction_policy(errors: list[str]) -> None:
    """User-facing docs and /gycd must not recommend the portable compaction
    fallback. Native compaction is the only user-visible path; the fallback
    lives only in the degradation layer (manifest `native_preferred`,
    `.agent/adapters/_capability_matrix.md` COMPACT_FALLBACK)."""
    banned = ("compactar.md", "/compactar")
    targets = [
        ROOT / "README.md",
        ROOT / "CLAUDE.md",
        ROOT / "AGENTS.md",
        ROOT / "llms.txt",
        ROOT / ".agent" / "workflows" / "gycd.md",
    ]
    for path in targets:
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        for needle in banned:
            if needle in text:
                fail(
                    errors,
                    f"{rel(path)} recomienda el fallback de compactación ({needle}); usar solo la compactación nativa del entorno",
                )


def validate_gitignore_template(errors: list[str]) -> None:
    gitignore = ROOT / ".agent" / "templates" / "gitignore.base"
    if not gitignore.exists():
        fail(errors, ".agent/templates/gitignore.base faltante")
        return
    patterns = {
        line.strip()
        for line in gitignore.read_text(encoding="utf-8", errors="ignore").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }
    for pattern in sorted(GITIGNORE_REQUIRED_PRIVATE_PATTERNS - patterns):
        fail(errors, f".agent/templates/gitignore.base no cubre patrón sensible requerido: {pattern}")
    if not ({".codex/", ".codex/*", ".codex/hooks.json"} & patterns):
        fail(errors, ".agent/templates/gitignore.base no cubre la configuración local de hooks Codex")


def validate_no_headless_claude_wiring(errors: list[str]) -> None:
    script = ROOT / ".agent" / "scripts" / "validate_no_headless_claude.py"
    if not script.exists():
        fail(errors, ".agent/scripts/validate_no_headless_claude.py faltante")
        return
    if not script.stat().st_mode & 0o111:
        fail(errors, ".agent/scripts/validate_no_headless_claude.py no es ejecutable")
    text = script.read_text(encoding="utf-8", errors="ignore")
    for needle in ["claude", "--print", "-p", "NO APLICA"]:
        if needle not in text:
            fail(errors, f"validate_no_headless_claude.py no cubre {needle!r}")


def mcp_entry_has_pin(entry: dict[str, Any], allow_version_marker: bool) -> bool:
    args = entry.get("args", [])
    args_text = " ".join(a for a in args if isinstance(a, str)) if isinstance(args, list) else ""
    has_version_marker = allow_version_marker and "<VERSION>" in args_text
    has_explicit_semver = MCP_SEMVER_PIN_RE.search(args_text) is not None
    has_commit_pin = COMMIT_PIN_RE.search(args_text) is not None
    # Triaje 2026-07-09: "source no vacío" era eludible con `source: x`. Una
    # fuente aprobada debe tener FORMA verificable: URL https/git o ruta local
    # del repo — y ojalá con ref/digest, pero al menos procedencia real.
    source = entry.get("source", "")
    has_approved_source = isinstance(source, str) and bool(
        re.match(r"^(https://|git@|github\.com/|\./|\.agent/)", source.strip())
    )
    return bool(has_version_marker or has_explicit_semver or has_commit_pin or has_approved_source)


def validate_mcp_entry_supply_chain(
    entry: dict[str, Any],
    context: str,
    errors: list[str],
    *,
    allow_version_marker: bool,
) -> None:
    command = entry.get("command", "")
    args = entry.get("args", [])
    args_text = " ".join(a for a in args if isinstance(a, str)) if isinstance(args, list) else ""

    if not allow_version_marker and "<VERSION>" in args_text:
        fail(errors, f"{context}: contiene marcador <VERSION> activo; reemplazar por versión exacta antes de activar")
    if BAD_MCP_VERSION_RE.search(args_text):
        fail(errors, f"{context}: usa tag móvil latest/next/beta; fijar versión exacta o source aprobado")

    command_name = Path(str(command)).name
    # Un MCP no debe arrancar via shell que descarga y ejecuta código remoto
    # (`bash -c "curl ... | sh"`): eso elude todo el pinning (triaje 2026-07-09).
    if command_name in {"sh", "bash", "zsh", "dash"} and re.search(
        r"(curl|wget|https?://)", args_text, re.IGNORECASE
    ):
        fail(errors, f"{context}: shell con descarga remota en el arranque del MCP — prohibido; declarar el paquete pineado o ruta local")
    installs_remote_package = command_name in {"npx", "npm", "pnpm", "yarn", "uvx", "pipx"}
    if installs_remote_package and not mcp_entry_has_pin(entry, allow_version_marker=allow_version_marker):
        fail(errors, f"{context}: instala paquete sin versión explícita, commit hash ni source aprobado")


def validate_mcp_template(errors: list[str]) -> None:
    """Verify .agent/templates/mcp.json.base exists and is valid JSON (ADR-015)."""
    mcp_template = ROOT / ".agent" / "templates" / "mcp.json.base"
    if not mcp_template.exists():
        fail(errors, ".agent/templates/mcp.json.base faltante (plantilla MCP opt-in del framework)")
        return
    try:
        data = json.loads(mcp_template.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        fail(errors, f".agent/templates/mcp.json.base no es JSON válido: {exc}")
        return
    if not isinstance(data.get("mcpServers"), dict):
        fail(errors, ".agent/templates/mcp.json.base debe declarar 'mcpServers' como objeto (incluso vacío)")
    servers = data.get("mcpServers", {})
    extra = {k: v for k, v in servers.items() if k != LOCAL_FRAMEWORK_SERVER}
    if extra:
        fail(errors, ".agent/templates/mcp.json.base solo puede declarar el servidor local "
                     f"'{LOCAL_FRAMEWORK_SERVER}' por defecto; mover el resto a '_disabled_examples': {sorted(extra)}")
    if LOCAL_FRAMEWORK_SERVER not in servers:
        fail(errors, f".agent/templates/mcp.json.base debe registrar el servidor local '{LOCAL_FRAMEWORK_SERVER}' "
                     "(motor de estado del framework)")
    else:
        sm = servers[LOCAL_FRAMEWORK_SERVER]
        if sm.get("command") != "python3" or ".agent/scripts/state_manager.py" not in " ".join(sm.get("args", [])):
            fail(errors, f"mcpServers.{LOCAL_FRAMEWORK_SERVER} debe ejecutar 'python3 .agent/scripts/state_manager.py'")
    # Supply-chain: cada ejemplo desactivado debe declarar versión explícita via marcador <VERSION>
    # (que se reemplaza al activar) o, equivalentemente, declarar 'source' con fuente aprobada.
    examples = data.get("_disabled_examples", {})
    if isinstance(examples, dict):
        for name, entry in examples.items():
            if name.startswith("$") or not isinstance(entry, dict):
                continue
            validate_mcp_entry_supply_chain(
                entry,
                f".agent/templates/mcp.json.base:_disabled_examples.{name}",
                errors,
                allow_version_marker=True,
            )


def validate_active_mcp_config(errors: list[str]) -> None:
    """Verify an activated .mcp.json, when present, does not use floating package sources."""
    mcp_config = ROOT / ".mcp.json"
    if not mcp_config.exists():
        return
    try:
        data = json.loads(mcp_config.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        fail(errors, f".mcp.json no es JSON válido: {exc}")
        return
    servers = data.get("mcpServers", {})
    if not isinstance(servers, dict):
        fail(errors, ".mcp.json debe declarar 'mcpServers' como objeto")
        return
    for name, entry in servers.items():
        if not isinstance(entry, dict):
            fail(errors, f".mcp.json:mcpServers.{name} debe ser objeto")
            continue
        # The local framework server runs repo-local code (python3 .agent/scripts/state_manager.py)
        # and is exempt from supply-chain pinning — it installs no remote package.
        # Exención por argv EXACTO (triaje 2026-07-09): la coincidencia por
        # substring aceptaba `python3 malicious.py ...state_manager.py`.
        _args = entry.get("args", [])
        if name == LOCAL_FRAMEWORK_SERVER and entry.get("command") == "python3" \
                and isinstance(_args, list) and len(_args) >= 1 \
                and _args[0] == ".agent/scripts/state_manager.py":
            continue
        validate_mcp_entry_supply_chain(
            entry,
            f".mcp.json:mcpServers.{name}",
            errors,
            allow_version_marker=False,
        )


def validate_post_edit_hook(errors: list[str], warnings: list[str]) -> None:
    """Verify the optional PostToolUse format hook is consistent (SDD v3.x — Bloque D)."""
    hook_script = ROOT / ".agent" / "scripts" / "post_edit_format.sh"
    settings_path = ROOT / ".claude" / "settings.json"

    if not hook_script.exists():
        fail(errors, ".agent/scripts/post_edit_format.sh faltante (hook PostToolUse del framework)")
    else:
        import os as _os
        if not _os.access(hook_script, _os.X_OK):
            fail(errors, ".agent/scripts/post_edit_format.sh existe pero no es ejecutable (chmod +x)")

    if not settings_path.exists():
        warn(warnings, ".claude/settings.json faltante; no se puede validar el registro del hook PostToolUse")
        return
    try:
        settings = json.loads(settings_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        fail(errors, f".claude/settings.json no es JSON válido: {exc}")
        return
    post_tool_use = settings.get("hooks", {}).get("PostToolUse", [])
    referenced = any(
        "post_edit_format.sh" in (h.get("command", "") if isinstance(h, dict) else "")
        for item in post_tool_use if isinstance(item, dict)
        for h in item.get("hooks", [])
    )
    if not referenced:
        warn(warnings, ".claude/settings.json no referencia post_edit_format.sh; el hook está disponible pero no registrado")


CODEX_SUPPORT_VALUES = {"pass", "fail", "unavailable"}
CODEX_RUNTIME_VALUES = {"pass", "fail", "unavailable"}
CODEX_SURFACES = {"cli", "desktop"}


def _codex_surface_list(value: str | None) -> list[str] | None:
    if value is None:
        return None
    values = [item.strip() for item in value.split(",") if item.strip()]
    if not values or any(item not in CODEX_SURFACES for item in values):
        return []
    return values if len(values) == len(set(values)) else []


def _environment_profile_value(text: str, label: str) -> str | None:
    match = re.search(
        rf"(?mi)^\s*(?:-\s*)?(?:\*\*)?{re.escape(label)}(?:\*\*)?(?:\s*\([^:\n]*\))?(?:\*\*)?\s*:\s*(?:\*\*)?\s*([^\n]+?)\s*$",
        text,
    )
    return match.group(1).strip().lower() if match else None


def _codex_primary_harness() -> bool:
    return codex_profile.detect_primary_harness(ROOT)["status"] == "codex"


STOP_TIMEOUT_MAX_S = 60


def _stop_timeout_problem(hook: dict[str, Any]) -> str | None:
    """None si el hook gestionado de Stop declara un `timeout` válido; si no, el motivo accionable."""
    try:
        import turn_close_hook

        floor = turn_close_hook.HOOK_TIMEOUT_S
    except Exception:  # noqa: BLE001 - sin el módulo se usa el valor por defecto del hook
        floor = 8
    value = hook.get("timeout")
    if type(value) in (int, float) and floor < value <= STOP_TIMEOUT_MAX_S:
        return None
    return (f"el hook gestionado stop debe declarar un `timeout` numérico mayor que {floor} (el tiempo máximo "
            f"del hook) y de {STOP_TIMEOUT_MAX_S} como mucho, en segundos; sin él el límite de Codex es de 600 "
            "segundos. Se regenera con `python3 .agent/scripts/sync_codex_hooks.py`")


def _codex_config_root() -> Path:
    """Raíz donde vive `.codex/` (L9, PA:FW-283).

    `.codex/` está en `.gitignore`: un worktree secundario nunca lo trae. Si ROOT es el
    top-level de un worktree secundario, se usa el checkout principal (el padre del
    common dir cuando este se llama `.git`). Git ausente, no es repo o caso dudoso → ROOT.
    Se quitan las variables GIT_* heredadas (el pre-commit exporta GIT_DIR e índice):
    la respuesta debe salir de ROOT, no de un repositorio ajeno.
    """
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    try:
        result = subprocess.run(
            ["git", "-C", str(ROOT), "rev-parse", "--path-format=absolute",
             "--git-dir", "--git-common-dir", "--show-toplevel"],
            capture_output=True, text=True, errors="replace", stdin=subprocess.DEVNULL,
            timeout=10, check=False, env=env,
        )
    except (OSError, ValueError, subprocess.SubprocessError):
        return ROOT
    lines = result.stdout.splitlines()
    if result.returncode != 0 or len(lines) != 3:
        return ROOT
    git_dir, common_dir, toplevel = (Path(line) for line in lines)
    try:
        if toplevel.resolve() != ROOT.resolve() or git_dir.resolve() == common_dir.resolve():
            return ROOT
    except OSError:
        return ROOT
    if common_dir.name != ".git":
        return ROOT
    main_root = common_dir.parent
    return main_root if main_root.is_dir() else ROOT


def validate_codex_integration(
    mode: str,
    state: dict[str, Any],
    errors: list[str],
    warnings: list[str],
) -> None:
    """Valida Codex solo cuando el proyecto declara Codex como harness principal.

    La plantilla permanece portable: sin una declaración real de Codex no se
    exige `.codex/hooks.json` ni `.codex/config.toml`, evitando imponer cambios
    a Claude. En Codex, `.codex/config.toml` es la fuente nativa; `.mcp.json`
    solo se diagnostica como espejo opcional.
    """
    profile = codex_profile.detect_primary_harness(ROOT)
    if profile["status"] == "conflict":
        fail(
            errors,
            "perfil de entorno contradictorio: declarar un único harness principal "
            f"en {profile['profile_path']} ({profile['declarations']})",
        )
        return
    if profile["status"] != "codex":
        if mode != "template" and profile["artifacts_present"]:
            warn(
                warnings,
                "hay configuración Codex (.codex/hooks.json o .codex/config.toml) "
                "pero el perfil no declara Codex como harness principal; ejecutar "
                "ensure_codex_integration.py --from-profile tras corregir el perfil",
            )
        return

    codex_root = _codex_config_root()
    hooks_path = codex_root / ".codex" / "hooks.json"
    native_config_path = codex_root / ".codex" / "config.toml"
    if not hooks_path.exists():
        fail(errors, ".codex/hooks.json faltante para un proyecto cuyo harness principal es Codex")
    else:
        try:
            hooks = json.loads(hooks_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            fail(errors, f".codex/hooks.json no es JSON válido: {exc}")
            hooks = {}
        if not isinstance(hooks, dict) or not isinstance(hooks.get("hooks"), dict):
            fail(errors, ".codex/hooks.json debe declarar hooks como objeto")
        else:
            try:
                import sync_codex_hooks

                drift = sync_codex_hooks.sync_hooks(codex_root, check=True)
                if drift["changed"]:
                    sync_fail(errors, ".codex/hooks.json desactualizado; ejecutar python3 .agent/scripts/sync_codex_hooks.py"
                              + (f" --root '{codex_root}'" if codex_root != ROOT else ""))
                required_events = {
                    "guard": "PreToolUse",
                    "state": "PostToolUse",
                    "format": "PostToolUse",
                    "stop": "Stop",
                }
                for hook_id, event_name in required_events.items():
                    groups = hooks["hooks"].get(event_name, [])
                    found = [
                        hook
                        for group in (groups if isinstance(groups, list) else [])
                        if isinstance(group, dict) and isinstance(group.get("hooks"), list)
                        for hook in group["hooks"]
                        if isinstance(hook, dict)
                        and hook.get("type") == "command"
                        and str(hook.get("statusMessage", "")).startswith(f"[FW-CODEX:{hook_id}]")
                        and f"--hook-id {hook_id}" in str(hook.get("command", ""))
                    ]
                    if not found:
                        fail(errors, f".codex/hooks.json no contiene el hook gestionado {hook_id} en {event_name}")
                    elif hook_id == "stop":
                        problem = _stop_timeout_problem(found[0])
                        if problem:
                            fail(errors, f".codex/hooks.json: {problem}")
                supported_events = set(required_events.values())
                for event_name, groups in hooks["hooks"].items():
                    if event_name in supported_events or not isinstance(groups, list):
                        continue
                    if any("[FW-CODEX:" in json.dumps(group, ensure_ascii=False) for group in groups):
                        fail(errors, f".codex/hooks.json declara un hook gestionado en evento no soportado: {event_name}")
            except (ImportError, SystemExit) as exc:
                fail(errors, f"no se pudo validar el sincronizador Codex: {exc}")

    if not native_config_path.exists():
        fail(errors, ".codex/config.toml faltante: Codex requiere mcp_servers.state_manager nativo")
    else:
        try:
            import tomllib

            native_config = tomllib.loads(native_config_path.read_text(encoding="utf-8"))
        except ImportError:
            # FW-238: sin `tomllib` (Python < 3.11) el TOML no se puede leer, no es inválido.
            fail(errors, f"no se pudo leer .codex/config.toml: falta tomllib, que requiere Python {MIN_PYTHON_TEXT} "
                         f"(este doctor corre con {_python_version_text()})")
            native_config = None
        except (OSError, ValueError) as exc:
            fail(errors, f".codex/config.toml no es TOML válido: {exc}")
            native_config = {}
        native_servers = native_config.get("mcp_servers") if isinstance(native_config, dict) else None
        native_state_manager = native_servers.get(LOCAL_FRAMEWORK_SERVER) if isinstance(native_servers, dict) else None
        if native_config is not None and (not isinstance(native_state_manager, dict) or native_state_manager.get("command") != "python3" or native_state_manager.get("args") != [".agent/scripts/state_manager.py"]):
            fail(errors, ".codex/config.toml debe registrar mcp_servers.state_manager con python3 .agent/scripts/state_manager.py")

    policy_path = ROOT / ".agent" / "adapters" / "codex_compatibility.json"
    if not policy_path.exists():
        warn(warnings, ".agent/adapters/codex_compatibility.json ausente; no hay contrato versionado de superficies Codex")
    else:
        try:
            policy = json.loads(policy_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            fail(errors, f".agent/adapters/codex_compatibility.json no es JSON válido: {exc}")
            policy = {}
        if isinstance(policy, dict):
            if policy.get("schema_version") != 1 or policy.get("surfaces") != ["cli", "desktop"]:
                fail(errors, ".agent/adapters/codex_compatibility.json debe declarar schema_version=1 y las superficies cli,desktop")
            if policy.get("default_required_surfaces") != ["cli"]:
                fail(errors, ".agent/adapters/codex_compatibility.json debe exigir CLI por defecto")
            minimums = policy.get("minimum_tested_versions")
            capabilities = policy.get("required_capabilities")
            if not isinstance(minimums, dict) or any(not isinstance(minimums.get(surface), str) or not minimums[surface] for surface in CODEX_SURFACES):
                fail(errors, ".agent/adapters/codex_compatibility.json debe registrar un mínimo probado por superficie")
            if not isinstance(capabilities, dict) or any(not isinstance(capabilities.get(surface), list) or not capabilities[surface] for surface in CODEX_SURFACES):
                fail(errors, ".agent/adapters/codex_compatibility.json debe registrar capacidades requeridas por superficie")

    mcp_path = ROOT / ".mcp.json"
    if not mcp_path.exists():
        warn(warnings, ".mcp.json ausente; Codex-native usa .codex/config.toml y conserva fallback manual")
    else:
        try:
            mcp = json.loads(mcp_path.read_text(encoding="utf-8"))
            servers = mcp.get("mcpServers") if isinstance(mcp, dict) else None
            state_manager = servers.get(LOCAL_FRAMEWORK_SERVER) if isinstance(servers, dict) else None
            mirror_valid = (
                isinstance(state_manager, dict)
                and state_manager.get("command") == "python3"
                and state_manager.get("args") == [".agent/scripts/state_manager.py"]
            )
            if mirror_valid:
                warn(warnings, ".mcp.json presente como espejo opcional; la evidencia Codex debe venir de .codex/config.toml y del host real")
            else:
                warn(warnings, ".mcp.json presente pero no contiene un espejo state_manager válido; no afecta Codex-native")
        except (OSError, json.JSONDecodeError):
            warn(warnings, ".mcp.json no es un espejo JSON válido; no afecta Codex-native")

    profile_path = ROOT / "_project" / "15_environment_profile.md"
    profile_text = profile_path.read_text(encoding="utf-8", errors="ignore") if profile_path.exists() else ""
    structural = _environment_profile_value(profile_text, "Soporte estructural Codex")
    runtime = _environment_profile_value(profile_text, "Smoke runtime Codex")
    if structural not in CODEX_SUPPORT_VALUES:
        fail(errors, "15_environment_profile.md debe declarar 'Soporte estructural Codex: pass|fail|unavailable'")
    elif structural == "fail":
        fail(errors, "Soporte estructural Codex está en fail; no se puede declarar integración lista")
    if runtime not in CODEX_RUNTIME_VALUES:
        fail(errors, "15_environment_profile.md debe declarar 'Smoke runtime Codex: pass|fail|unavailable'")
    elif runtime == "fail":
        fail(errors, "Smoke runtime Codex está en fail; revisar el smoke antes de continuar")
    elif runtime == "unavailable":
        warn(warnings, "Smoke runtime Codex: unavailable; el soporte estructural no equivale a runtime verificado")
    if runtime == "pass" and structural != "pass":
        fail(errors, "Smoke runtime Codex=pass requiere Soporte estructural Codex=pass")

    profile_fields = {
        "Hooks Codex": ("Hooks Codex", {"gestionados", "manuales", "no disponibles"}),
        "MCP local `state_manager`": (
            (
                "MCP local `state_manager` (Codex `.codex/config.toml` / Claude `.mcp.json`)",
                "MCP local `state_manager` (Codex `.codex/config.toml` native; `.mcp.json` espejo opcional)",
            ),
            {"conectado", "fallback", "no disponible"},
        ),
    }
    for field_name, (labels, accepted) in profile_fields.items():
        if isinstance(labels, str):
            labels = (labels,)
        value = next(
            (
                candidate
                for label in labels
                if (candidate := _environment_profile_value(profile_text, label)) is not None
            ),
            None,
        )
        if value is None or value.startswith("[") or value not in accepted:
            fail(errors, f"15_environment_profile.md debe declarar {field_name}: {' | '.join(sorted(accepted))}")
    codex_version = _environment_profile_value(profile_text, "Versión de Codex")
    if codex_version is None or codex_version.startswith("[") or not codex_version:
        fail(errors, "15_environment_profile.md debe declarar Versión de Codex (versión o no disponible)")
    known_differences = _environment_profile_value(profile_text, "Diferencias Codex conocidas")
    if known_differences is None or known_differences.startswith("[") or not known_differences:
        fail(errors, "15_environment_profile.md debe declarar Diferencias Codex conocidas (lista o ninguna)")

    declared_required = _environment_profile_value(profile_text, "Superficies Codex requeridas")
    required_surfaces = _codex_surface_list(declared_required)
    if declared_required is None:
        warn(warnings, "15_environment_profile.md no separa todavía las superficies Codex; ejecutar el smoke nuevo para registrar cli y desktop/app-server")
    elif not required_surfaces:
        fail(errors, "15_environment_profile.md debe declarar Superficies Codex requeridas como cli o cli,desktop")
    else:
        surface_labels = {
            "cli": "Smoke runtime Codex CLI",
            "desktop": "Smoke runtime Codex Desktop/app-server",
        }
        observed: dict[str, str | None] = {}
        for surface, label in surface_labels.items():
            value = _environment_profile_value(profile_text, label)
            observed[surface] = value
            if value is None:
                if surface in required_surfaces:
                    fail(errors, f"15_environment_profile.md debe declarar {label}: pass | fail | unavailable")
                else:
                    warn(warnings, f"15_environment_profile.md no registra {label}; superficie opcional no certificada")
                continue
            if value not in CODEX_RUNTIME_VALUES:
                fail(errors, f"15_environment_profile.md debe declarar {label}: pass | fail | unavailable")
            elif value == "fail":
                if surface in required_surfaces:
                    fail(errors, f"{label} está en fail; revisar el smoke de la superficie requerida")
                else:
                    warn(warnings, f"{label} está en fail; la superficie no es requerida por el perfil")
            elif value == "unavailable":
                warn(warnings, f"{label}: unavailable; no se convierte en pass")
        if runtime == "pass" and any(observed.get(surface) != "pass" for surface in required_surfaces):
            fail(errors, "Smoke runtime Codex=pass requiere pass explícito en cada superficie Codex requerida")

        cli_version = _environment_profile_value(profile_text, "Versión de Codex CLI")
        desktop_version = _environment_profile_value(profile_text, "Versión de Codex Desktop/app-server")
        for surface, value in (("cli", cli_version), ("desktop", desktop_version)):
            if value is None or value.startswith("[") or not value:
                if surface in required_surfaces:
                    warn(warnings, f"15_environment_profile.md no registra Versión de Codex {surface}; la versión real debe capturarse en el smoke")
        model = _environment_profile_value(profile_text, "Modelo Codex smoke")
        effort = _environment_profile_value(profile_text, "Esfuerzo Codex smoke")
        if model is None or model.startswith("[") or not model:
            warn(warnings, "15_environment_profile.md no registra Modelo Codex smoke; la certificación reproducible exige --model explícito")
        if effort is None or effort.startswith("[") or not effort:
            warn(warnings, "15_environment_profile.md no registra Esfuerzo Codex smoke; la certificación reproducible exige --reasoning-effort explícito")
        elif effort not in {"none", "minimal", "low", "medium", "high", "xhigh", "max", "ultra"}:
            fail(errors, "Esfuerzo Codex smoke no pertenece al enum soportado")


def validate_lessons_template_cleanliness(mode: str, errors: list[str]) -> None:
    """Verify _project/22_lessons.md does not exist with real content in template mode (ADR-016)."""
    lessons = ROOT / "_project" / "22_lessons.md"
    if mode != "template" or not lessons.exists():
        return
    text = lessons.read_text(encoding="utf-8", errors="ignore")
    if re.search(r"(?m)^## Lección \d+", text):
        fail(errors, "_project/22_lessons.md contiene lecciones reales en modo template; debe estar vacío o no existir hasta que el proyecto se inicialice y /aprender-de-error lo cree (ADR-016)")


def validate_lessons_digest_template_cleanliness(mode: str, errors: list[str]) -> None:
    """El digest de lecciones es una proyección autogenerada de 22_lessons.md
    (lessons_reflect.py); en modo template no debe existir, mismo criterio que
    22_lessons.md."""
    digest = ROOT / "_project" / "22_lessons_digest.md"
    if mode == "template" and digest.exists():
        fail(errors, "_project/22_lessons_digest.md existe en modo template; es una proyección de lessons_reflect.py y no debe distribuirse en la plantilla")


def validate_state_extensions_template_cleanliness(mode: str, errors: list[str]) -> None:
    """Las extensiones del motor (ADR-052) son del producto: la plantilla no
    debe distribuir ninguna. En initialized, si existe el archivo, verificar
    que carga (un state_actions.py roto rompería toda mutación)."""
    ext = ROOT / ".agent" / "extensions" / "state_actions.py"
    if mode == "template" and ext.exists():
        fail(errors, ".agent/extensions/state_actions.py existe en modo template; las extensiones del motor son del producto y no se distribuyen en la plantilla (ADR-052)")


def validate_state_log_template_cleanliness(mode: str, errors: list[str]) -> None:
    """Verify _project/state_log.jsonl does not ship real mutation history in template mode.

    The mutation log is created lazily by state_core.mutate on initialized
    projects; the template must stay clean of framework-development history."""
    log_path = ROOT / "_project" / "state_log.jsonl"
    if mode != "template" or not log_path.exists():
        return
    if log_path.read_text(encoding="utf-8", errors="ignore").strip():
        fail(errors, "_project/state_log.jsonl contiene historial real de mutaciones en modo template; borrarlo antes de distribuir la plantilla")


def validate_state_log_chain(mode: str, errors: list[str], warnings: list[str]) -> None:
    """La bitácora sella cada evento con el hash del anterior: si la cadena está
    rota, alguien la editó después de escribirla.

    Es ERROR, no WARN: a diferencia de las entidades sin respaldo — legítimas en
    proyectos migrados, cuya historia es anterior al log — una cadena rota no
    tiene lectura inocente. Los eventos previos a la cadena no se auditan, así
    que los logs que ya existían no se invalidan (sabotaje controlado
    2026-07-24)."""
    log_path = ROOT / "_project" / "state_log.jsonl"
    if mode != "initialized" or not log_path.exists():
        return
    try:
        sys.path.insert(0, str(ROOT / ".agent" / "scripts"))
        import state_core as _sc
    except ImportError:
        return  # instalación parcial: el resto del doctor sigue aplicando
    problemas = _sc.verify_log_chain(log_path)
    for p in problemas[:5]:
        fail(errors, f"Cadena de state_log.jsonl rota — {p}")
    if len(problemas) > 5:
        fail(errors, f"Cadena de state_log.jsonl: {len(problemas) - 5} inconsistencias más; recuperar el log desde git")


STATE_LOG_WARN_BYTES = 50 * 1024 * 1024


def validate_state_log_size(mode: str, warnings: list[str]) -> None:
    """GH001 de GitHub: state_log.jsonl >= 50 MB dispara el aviso en cada
    push (FW-168, recurrencia verificada 2026-09-01). WARN, no FAIL — el
    archivo es infra crítica del motor (recuperación ante desastre vía
    replay_state_log.py) y no puede borrarse ni gitignorearse; el operador
    decide cuándo correr el shrink (ADR-139/FW-201)."""
    log_path = ROOT / "_project" / "state_log.jsonl"
    if mode != "initialized" or not log_path.exists():
        return
    size = log_path.stat().st_size
    if size >= STATE_LOG_WARN_BYTES:
        warn(
            warnings,
            f"_project/state_log.jsonl pesa {size / 1024 / 1024:.1f} MB (>= 50 MB, "
            "umbral recomendado de GitHub, dispara GH001 en cada push). Correr "
            "python3 .agent/scripts/shrink_state_log_snapshots.py --write "
            "(ADR-139/FW-201) o instalar el pre-push hook para bloquear el "
            "crecimiento futuro: bash .agent/scripts/pre_push.sh install"
        )


# N2 (FW-361, 5.3.1 L6.4): peso de `_project/`, en aviso. Rotar el log, el scope y el diario es de la 5.4.
PROJECT_STATE_WARN_BYTES = 6 * 1024 * 1024  # D1: TL queda en 4,8 MB tras migrate_test_identities.py
ACTIVE_SCOPE_WARN_BYTES = 32 * 1024  # D2
SESSION_JOURNAL_WARN_BYTES = 60 * 1024
PROJECT_FILE_WARN_BYTES = 1024 * 1024
PROJECT_DUPLICATE_MIN_BYTES = 64 * 1024  # D3
# D3: fuera de la regla de 1 MiB (no de las copias): el estado y el log tienen su propio aviso y el resto
# lo genera el motor. `git add -N` deja el blob vacío en el índice (SHA-1 y SHA-256): no son copias.
EMPTY_BLOBS = {"e69de29bb2d1d6434b8b29ae775ad8c2e48c5391",
               "473a0f4c3be8a93681a267e3b1e9a7dcda1185436fe141f7749120a303721813"}
PROJECT_WEIGHT_EXEMPT = frozenset(f"_project/{name}" for name in (
    "03_project_state.json", "state_log.jsonl", "core.md", "06_bug_fixes.md", "07_pending_clarifications.md",
    "03_arch_decisions.md", "00_session_brief.json", "24_change_log.md"))


def _size_text(size: float) -> str:
    return f"{size / 1024 / 1024:.1f} MiB" if size >= 1024 * 1024 else f"{size / 1024:.1f} KiB"


def validate_project_weight(mode: str, warnings: list[str]) -> None:
    """N2 (FW-361): estado, scope y diario por tamaño en disco; ficheros pesados y copias, por lo versionado."""
    if mode != "initialized":
        return
    for name, limit, action in (
        ("03_project_state.json", PROJECT_STATE_WARN_BYTES, "si guarda mapas de identidades de test en línea, "
         "python3 .agent/scripts/migrate_test_identities.py --write los saca (FW-357); compactarlo es de la 5.4"),
        ("04_active_scope.md", ACTIVE_SCOPE_WARN_BYTES, "pasa lo cerrado a _project/04_scope_archive.md"),
        ("13_session_journal.md", SESSION_JOURNAL_WARN_BYTES, "archiva las entradas antiguas; rotarlo es de la 5.4"),
    ):
        path = ROOT / "_project" / name
        if path.is_file() and path.stat().st_size > limit:
            warn(warnings, f"_project/{name} pesa {_size_text(path.stat().st_size)} (más de {_size_text(limit)}): {action}")
    try:
        out = subprocess.run(["git", "ls-files", "-s", "-z", "--", "_project"], cwd=ROOT, capture_output=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return
    heavy, by_blob = [], {}
    for meta, _, rel in (e.partition("\t") for e in out.stdout.decode("utf-8", "ignore").split("\0") if out.returncode == 0):
        fields = meta.split()  # modo, blob y stage; en un conflicto no hay stage 0 y la ruta se omite
        size = (ROOT / rel).stat().st_size if fields[2:] == ["0"] and (ROOT / rel).is_file() else 0
        if size > PROJECT_FILE_WARN_BYTES and rel not in PROJECT_WEIGHT_EXEMPT:
            heavy.append((size, rel))
        if size >= PROJECT_DUPLICATE_MIN_BYTES and fields[1] not in EMPTY_BLOBS:
            by_blob.setdefault(fields[1], []).append(rel)
    if heavy:
        heavy.sort(reverse=True)
        warn(warnings, f"_project/ versiona {len(heavy)} fichero(s) de más de 1 MiB ({_size_text(sum(s for s, _ in heavy))} "
             "en total): " + ", ".join(f"{rel} ({_size_text(s)})" for s, rel in heavy[:3]) + ". La evidencia pesada va "
             "a backups/ (ignorado por git; añádelo a .gitignore si aún no está) y en el documento queda su ruta y su sha256")
    copies = [" = ".join(paths) for paths in by_blob.values() if len(paths) > 1]
    if copies:
        warn(warnings, f"_project/ versiona copias idénticas: {'; '.join(copies[:3])}. Deja una y cita su huella")


def validate_feedback_template_cleanliness(mode: str, errors: list[str]) -> None:
    """_feedback/ guarda la fricción del framework de un proyecto real; no debe
    distribuirse en la plantilla (análogo a state_log y 22_lessons)."""
    feedback = ROOT / "_feedback"
    if mode != "template" or not feedback.exists():
        return
    report = feedback / "field_report.md"
    if report.exists() and report.read_text(encoding="utf-8", errors="ignore").strip():
        fail(errors, "_feedback/field_report.md contiene fricción real en modo template; borrarlo antes de distribuir la plantilla")


def validate_local_settings_safety(errors: list[str]) -> None:
    """Scan local Claude settings for secrets without including them in the distributed secret scan."""
    settings_local = ROOT / ".claude" / "settings.local.json"
    if not settings_local.exists():
        return
    text = settings_local.read_text(encoding="utf-8", errors="ignore")
    found = _first_secret_in_text(text, allow_bare_hex=False, in_tests_dir=False)
    if found is not None:
        fail(errors, f".claude/settings.local.json contiene posible secreto local (línea {found[0]}: `{found[1].strip()[:120]}`). Sanitizar antes de copiar/distribuir la plantilla")


def validate_verificar_ejecutable_wiring(manifest: dict[str, Any], errors: list[str]) -> None:
    """Verify that verificar-ejecutable is registered as internal workflow and that
    /cierre-modulo references it textually (ADR-017 hardening)."""
    commands = manifest.get("commands", [])
    if not isinstance(commands, list):
        return  # validate_manifest already reported this

    entry = next((c for c in commands if isinstance(c, dict) and c.get("name") == "verificar-ejecutable"), None)
    if entry is None:
        fail(errors, "verificar-ejecutable no está declarado en manifest.json (requerido por /cierre-modulo paso 3.8)")
        return
    if entry.get("claude_command") is not False:
        fail(errors, "verificar-ejecutable debe tener claude_command:false en manifest.json (workflow interno, sin wrapper)")
    source = entry.get("source")
    if source != ".agent/workflows/verificar-ejecutable.md":
        fail(errors, f"verificar-ejecutable.source inesperado en manifest.json: {source}")
    elif not (ROOT / source).exists():
        fail(errors, f"verificar-ejecutable.source declarado pero archivo inexistente: {source}")

    cierre_path = ROOT / ".agent" / "workflows" / "cierre-modulo.md"
    if not cierre_path.exists():
        fail(errors, "cierre-modulo.md faltante; no se puede validar el wiring de verificar-ejecutable")
        return
    cierre_text = cierre_path.read_text(encoding="utf-8", errors="ignore")
    if ".agent/workflows/verificar-ejecutable.md" not in cierre_text:
        fail(errors, "cierre-modulo.md no referencia .agent/workflows/verificar-ejecutable.md; paso 3.8 EJECUTABLE GATE desconectado")


def validate_acceptance_gate_wiring(errors: list[str]) -> None:
    """The evidence gate logic is extracted to validate_acceptance.py; keep it present,
    executable and referenced by /cierre-modulo so the deterministic gate cannot rot
    back into prose-only (enforcement hardening)."""
    script = ROOT / ".agent" / "scripts" / "validate_acceptance.py"
    if not script.exists():
        fail(errors, ".agent/scripts/validate_acceptance.py faltante (evidence gate ejecutable, paso 1.c)")
        return
    import os as _os
    if not _os.access(script, _os.X_OK):
        fail(errors, ".agent/scripts/validate_acceptance.py existe pero no es ejecutable (chmod +x)")
    cierre = ROOT / ".agent" / "workflows" / "cierre-modulo.md"
    if cierre.exists() and "validate_acceptance.py" not in cierre.read_text(encoding="utf-8", errors="ignore"):
        fail(errors, "cierre-modulo.md no referencia validate_acceptance.py; evidence gate ejecutable desconectado del paso 1.c")


def validate_closure_gate_wiring(errors: list[str]) -> None:
    """The closure-decision gate (death criterion, SPEC presence, tier rules) is
    extracted to validate_closure.py so those deterministic gates of /cierre-modulo
    cannot rot back into prose-only. Keep it present, executable and referenced by
    /cierre-modulo (enforcement hardening, same contract as validate_acceptance.py)."""
    script = ROOT / ".agent" / "scripts" / "validate_closure.py"
    if not script.exists():
        fail(errors, ".agent/scripts/validate_closure.py faltante (closure gate ejecutable: death criterion + presencia SPEC + tier)")
        return
    import os as _os
    if not _os.access(script, _os.X_OK):
        fail(errors, ".agent/scripts/validate_closure.py existe pero no es ejecutable (chmod +x)")
    cierre = ROOT / ".agent" / "workflows" / "cierre-modulo.md"
    if cierre.exists() and "validate_closure.py" not in cierre.read_text(encoding="utf-8", errors="ignore"):
        fail(errors, "cierre-modulo.md no referencia validate_closure.py; closure gate ejecutable desconectado (pasos 0/3.7/5)")


def validate_transition_gate_wiring(errors: list[str]) -> None:
    """The state-transition legality gate is extracted to validate_transition.py and
    wired into pre-commit so an illegal jump to `completed` blocks the commit (the
    closure integrity check). Keep it present, executable and referenced by
    pre_commit.sh so the diff-based gate cannot rot back into prose-only."""
    script = ROOT / ".agent" / "scripts" / "validate_transition.py"
    if not script.exists():
        fail(errors, ".agent/scripts/validate_transition.py faltante (closure integrity check: legalidad de transiciones por diff)")
        return
    import os as _os
    if not _os.access(script, _os.X_OK):
        fail(errors, ".agent/scripts/validate_transition.py existe pero no es ejecutable (chmod +x)")
    pre_commit = ROOT / ".agent" / "scripts" / "pre_commit.sh"
    if pre_commit.exists() and "validate_transition.py" not in pre_commit.read_text(encoding="utf-8", errors="ignore"):
        fail(errors, "pre_commit.sh no invoca validate_transition.py; closure integrity check desconectado del commit")


CHANGELOG_VERSION_RE = re.compile(r"(?m)^##\s*\[?v?(\d+\.\d+(?:\.\d+)?)\]?")
ENGINE_VERSION_RE = re.compile(r"""(?m)^FRAMEWORK_VERSION\s*=\s*["'](\d+\.\d+(?:\.\d+)?)["']""")


def validate_framework_version_consistency(
    mode: str, state: dict[str, Any], errors: list[str], warnings: list[str]
) -> None:
    """L6: the canonical framework version must be declared consistently across
    manifest.json, the template state, the CHANGELOG and the state engine, so
    template↔real-project drift is governed rather than silent. In an initialized
    project that lags the framework, the drift is surfaced as a warning (not
    silenced, not fatal).

    The engine (`state_core.FRAMEWORK_VERSION`) is checked in both modes: it
    ships in the same copy as the manifest, so a mismatch means the copy is
    inconsistent, not that the project is behind. Field regression 2026-07-24:
    the engine stayed at 4.12.0 through the whole 4.13.0 release and reached all
    7 downstream projects, because parity only covered the other three."""
    manifest_path = ROOT / ".agent" / "workflows" / "manifest.json"
    manifest_v = None
    if manifest_path.exists():
        try:
            manifest_v = json.loads(manifest_path.read_text(encoding="utf-8")).get("framework_version")
        except json.JSONDecodeError:
            return  # validate_manifest/load_json already reported the JSON error

    changelog_path = ROOT / ".agent" / "docs" / "CHANGELOG.md"
    changelog_v = None
    if not changelog_path.exists():
        fail(errors, ".agent/docs/CHANGELOG.md faltante (procedencia y versión canónica del framework — L6)")
    else:
        match = CHANGELOG_VERSION_RE.search(changelog_path.read_text(encoding="utf-8", errors="ignore"))
        if match:
            changelog_v = match.group(1)
        else:
            fail(errors, ".agent/docs/CHANGELOG.md no declara una versión con encabezado '## [x.y]'")

    state_v = state.get("framework_version") if isinstance(state, dict) else None

    engine_path = ROOT / ".agent" / "scripts" / "state_core.py"
    engine_v = None
    if engine_path.exists():
        engine_match = ENGINE_VERSION_RE.search(engine_path.read_text(encoding="utf-8", errors="ignore"))
        if engine_match:
            engine_v = engine_match.group(1)

    if manifest_v and changelog_v and manifest_v != changelog_v:
        fail(errors, f"Deriva de versión del framework: manifest.json={manifest_v} ≠ CHANGELOG.md={changelog_v}; declarar una única versión canónica")
    if manifest_v and engine_v and manifest_v != engine_v:
        fail(errors, f"Deriva de versión del framework: .agent/scripts/state_core.py FRAMEWORK_VERSION={engine_v} ≠ manifest.json={manifest_v}; el motor viaja en la misma copia que el manifest y debe declarar la misma versión")
    if mode == "template" and state_v and manifest_v and state_v != manifest_v:
        fail(errors, f"Deriva de versión en plantilla: _project/03_project_state.json={state_v} ≠ manifest.json={manifest_v}; la plantilla debe declarar la versión canónica")
    if mode == "initialized" and state_v and manifest_v and state_v != manifest_v:
        warn(warnings, f"Proyecto en framework_version={state_v} pero la plantilla del framework está en {manifest_v}; revisar .agent/docs/CHANGELOG.md y considerar upgrade")


def _committed_manifest_framework_version(root: Path) -> str | None:
    """framework_version que HEAD ya tiene commiteado en manifest.json, o
    None si no se puede leer (repo sin HEAD todavía, git ausente, JSON roto
    en esa revisión)."""
    try:
        out = subprocess.run(
            ["git", "show", "HEAD:.agent/workflows/manifest.json"],
            cwd=root, capture_output=True, timeout=10, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if out.returncode != 0:
        return None
    try:
        return json.loads(out.stdout.decode("utf-8", "ignore")).get("framework_version")
    except json.JSONDecodeError:
        return None


def _is_framework_version_bump(root: Path, working_tree_version: str | None) -> bool:
    """True si el manifest del árbol de trabajo declara una versión distinta
    a la que HEAD ya tiene commiteada — o sea, ESTE commit es el que sube la
    versión. Sin HEAD legible (primer commit del repo) se trata como
    'no es bump' para no producir ruido en la inicialización de un proyecto
    nuevo."""
    if not working_tree_version:
        return False
    committed = _committed_manifest_framework_version(root)
    if committed is None:
        return False
    return committed != working_tree_version


def validate_field_report_staleness(
    mode: str, manifest: dict[str, Any], warnings: list[str]
) -> None:
    """FW-220: en un commit que sube framework_version (mismo criterio que
    validate_framework_version_consistency), corre el detector de filas
    stale de field_report.md y vuelca los hallazgos a `warnings` —
    EXCLUSIVAMENTE warnings, nunca errors. El matching es heurístico (FW-NNN
    en subject de commit) con falsos negativos confirmados; convertirlo en
    bloqueo repetiría el villano histórico "falso positivo -> --no-verify"
    (FW-034/037/038/040).

    Ruta por modo (mismo mapeo que validate_field_report_mechanism):
    `.agent/docs/field_report.md` en template, `_feedback/field_report.md`
    en initialized. Se salta en silencio si el fichero correspondiente no
    existe (proyecto initialized sin fricción registrada aún)."""
    working_tree_version = manifest.get("framework_version") if isinstance(manifest, dict) else None
    if not _is_framework_version_bump(ROOT, working_tree_version):
        return
    target = (
        ROOT / ".agent" / "docs" / "field_report.md"
        if mode == "template"
        else ROOT / "_feedback" / "field_report.md"
    )
    if not target.exists():
        return
    try:
        findings = detect_stale_field_report.stale_field_report_candidates(target, ROOT)
    except Exception as exc:  # noqa: BLE001 - detector de diagnóstico, nunca debe tumbar el doctor
        warn(warnings, f"FIELD REPORT STALENESS: detector falló ({exc}) — revisar manualmente (FW-220)")
        return
    if not findings:
        return
    detalle = "; ".join(findings[:5])
    extra = f" (+{len(findings) - 5} más)" if len(findings) > 5 else ""
    warn(
        warnings,
        "FIELD REPORT STALENESS: bump de versión detectado — filas abiertas en "
        f"field_report.md con commits posteriores que las mencionan (FW-220): "
        f"{detalle}{extra}. Revisar si siguen abiertas.",
    )


def manifest_framework_version(errors: list[str]) -> str | None:
    manifest_path = ROOT / ".agent" / "workflows" / "manifest.json"
    if not manifest_path.exists():
        fail(errors, ".agent/workflows/manifest.json faltante; no se puede validar framework_version en workflows")
        return None
    try:
        value = json.loads(manifest_path.read_text(encoding="utf-8")).get("framework_version")
    except json.JSONDecodeError as exc:
        fail(errors, f".agent/workflows/manifest.json no es JSON válido: {exc}")
        return None
    if not isinstance(value, str) or not value.strip():
        fail(errors, "manifest.json.framework_version debe ser string no vacío")
        return None
    return value


def validate_initialization_workflow_version_references(errors: list[str]) -> None:
    """Keep project creation/migration instructions aligned with manifest version."""
    canonical = manifest_framework_version(errors)
    if canonical is None:
        return
    for workflow_name in ["nuevo-proyecto.md", "migrar-proyecto.md"]:
        path = ROOT / ".agent" / "workflows" / workflow_name
        if not path.exists():
            fail(errors, f".agent/workflows/{workflow_name} faltante")
            continue
        lines = [
            line.strip()
            for line in path.read_text(encoding="utf-8", errors="ignore").splitlines()
            if "framework_version" in line
        ]
        if not lines:
            fail(errors, f"{workflow_name} no instruye cómo fijar framework_version")
            continue
        if not any(canonical in line for line in lines):
            fail(errors, f"{workflow_name} no usa framework_version canónico {canonical}")
        for line in lines:
            explicit_versions = re.findall(r'["`](\d+\.\d+(?:\.\d+)?)["`]', line)
            stale = sorted(version for version in explicit_versions if version != canonical)
            if stale:
                fail(
                    errors,
                    f"{workflow_name} contiene framework_version obsoleto "
                    f"({', '.join(stale)}); usar {canonical}",
                )


def validate_phase0_module_specs_policy(errors: list[str]) -> None:
    """Phase 0 GitHub bootstrap must create a real tracked module, not only a phase flag."""
    workflow = ROOT / ".agent" / "workflows" / "nuevo-proyecto.md"
    if not workflow.exists():
        fail(errors, ".agent/workflows/nuevo-proyecto.md faltante")
        return
    text = workflow.read_text(encoding="utf-8", errors="ignore")
    required = {
        "phase_00_esqueleto_publicable": "fase 0 no declara módulo phase_00_esqueleto_publicable",
        "module_specs": "fase 0 no declara module_specs para su módulo inicial",
        "work_tiers": "fase 0 no declara work_tiers para su módulo inicial",
        "module_statuses": "fase 0 no declara module_statuses para su módulo inicial",
        '"in_progress"': "fase 0 no deja el módulo inicial en in_progress",
        '"data_model": "n/a"': "fase 0 no clasifica data_model como n/a",
        '"api_contract": "n/a"': "fase 0 no clasifica api_contract como n/a",
        "justification": "fase 0 no exige justification para specs n/a",
        '"classified_by": "manual"': "fase 0 no declara classified_by manual para specs iniciales",
    }
    for needle, message in required.items():
        if needle not in text:
            fail(errors, f"nuevo-proyecto.md: {message}")


# Secciones de GUARDRAILS_MASTER.md citadas por nombre literal desde workflows y
# scripts (`/tdd`, `/cambio-seguro`, `/cierre-modulo`, `/scope-siguiente`,
# `/tech-design-mvp`, `validate_file_size.py`, `validate_closure.py`). Si una
# desaparece en un refactor de la constitución, el consumidor apunta a la nada en
# silencio: 8 workflows rotos sin un solo error. FW-168.
GUARDRAILS_ANCHORS = {
    "Protocolo de 3 Fases": "concepto fundacional",
    "Graduación de Rigor por Tier": "citado por tdd, cambio-seguro, cierre-modulo, scope-siguiente y validate_closure",
    "Graduación por capacidad del ejecutor": "citado por validate_closure",
    "Gate THINK": "citado por tech-design-mvp",
    "Gate APPROVE": "citado por los checklists pre-code",
    "Gate CODE + LOG": "citado por cierre-modulo",
    "Chunking y Tamaño de Archivos": "citado por validate_file_size.py como §Chunking",
    "Carga obligatoria al inicio de sesión": "leído por validate_guardrails_startup_source (sin el literal, esa validación se vuelve no-op)",
}


def validate_guardrails_anchors(errors: list[str]) -> None:
    """Las anclas que los workflows citan de la constitución deben existir.

    Sin filtro por `mode`: a diferencia de `validate_guardrails_version_parity`
    (exenta en `initialized` por FW-033, porque README/CLAUDE son del producto),
    las anclas son el contrato de GUARDRAILS_MASTER.md con los workflows que el
    framework sí distribuye en `.agent/`. Si un proyecto adoptante borró un
    ancla, su `/tdd` está genuinamente roto y debe fallar."""
    guardrails = ROOT / "GUARDRAILS_MASTER.md"
    if not guardrails.exists():
        return  # la ausencia ya falla en main()
    text = guardrails.read_text(encoding="utf-8", errors="ignore")
    for anchor, reason in GUARDRAILS_ANCHORS.items():
        if anchor not in text:
            fail(errors, f"GUARDRAILS_MASTER.md no contiene el ancla '{anchor}' ({reason})")


def validate_guardrails_startup_source(errors: list[str]) -> None:
    """Keep the constitutional startup source aligned with the v3 minimal context layer."""
    guardrails_path = ROOT / "GUARDRAILS_MASTER.md"
    if not guardrails_path.exists():
        return
    text = guardrails_path.read_text(encoding="utf-8", errors="ignore")
    startup_match = re.search(
        r"\*\*Carga obligatoria al inicio de sesión:\*\*(.*?)(?:\n\n|$)",
        text,
        flags=re.DOTALL,
    )
    if not startup_match:
        return
    startup_line = startup_match.group(1)
    if "_project/core.md" not in startup_line:
        fail(errors, "GUARDRAILS_MASTER.md debe declarar _project/core.md como fuente primaria de arranque")
    if "_project/00_session_brief.json" in startup_line and "fallback" not in startup_line.lower():
        fail(errors, "GUARDRAILS_MASTER.md debe tratar 00_session_brief.json como fallback legacy, no como carga primaria")


def validate_workflow_step_numbering(errors: list[str]) -> None:
    """FW-201: un número de paso duplicado en `.agent/workflows/*.md` es la
    clase de defecto que dejó a `cierre-modulo.md` con dos `7.` (uno real,
    "COMMIT RECOMENDADO", y uno arrastrado de una edición anterior) — invisible
    a un grep porque el string sí estaba ahí, solo que dos veces.

    Regla calibrada contra los datos reales, no ingenua: dentro de cada sección
    `## ` del workflow, un token de paso (`5`, `5.5`, `12`…) que se repite ES
    un defecto — salvo que el token sea exactamente `1`, que SIEMPRE reinicia
    el historial de la sección. Sin esa excepción, `skills.md`,
    `update-state.md` y `ciclo.md` — que reinician listas legítimamente varias
    veces bajo un mismo `## ` — darían falso positivo (ruido que FW-181 ya
    identificó como inaceptable). `5` y `5.5` son tokens DISTINTOS: no
    colisionan entre sí, solo consigo mismos."""
    workflows_dir = ROOT / ".agent" / "workflows"
    step_re = re.compile(r"(?m)^(\d+(?:\.\d+)?)\. [A-ZÁÉÍÓÚ]")
    for path in sorted(workflows_dir.glob("*.md")):
        if path.name == "_README.md":
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        for section in re.split(r"(?m)^## ", text):
            steps = step_re.findall(section)
            last_seen: dict[str, int] = {}
            for i, tok in enumerate(steps):
                if tok == "1":
                    last_seen = {"1": i}
                    continue
                if tok in last_seen:
                    between = steps[last_seen[tok] + 1 : i]
                    if "1" not in between:
                        fail(
                            errors,
                            f"{rel(path)}: paso '{tok}.' repetido sin reinicio "
                            "a '1.' entre las dos apariciones — probable "
                            "arrastre de una edición anterior (FW-201)",
                        )
                last_seen[tok] = i


# Lote 4 (Plan 4.5): Gate de tamaño de workflows (≤ 5.000 tokens renderizados)
WORKFLOW_TOKEN_BUDGETS = {
    "ciclo.md": 5000,
    "cierre-modulo.md": 5000,
}


def estimate_workflow_tokens(text: str) -> int:
    """Estimación canónica de tokens de texto UTF-8 (c/4 o w*1.33)."""
    chars = len(text)
    words = len(text.split())
    return max((chars + 3) // 4, int(words * 1.33))


def validate_workflow_rendered_token_budget(errors: list[str]) -> None:
    """Gate del Plan 4.5 (Lote 4): workflows clave no deben exceder 5.000 tokens."""
    workflows_dir = ROOT / ".agent" / "workflows"
    for name, limit in WORKFLOW_TOKEN_BUDGETS.items():
        path = workflows_dir / name
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        toks = estimate_workflow_tokens(text)
        if toks > limit:
            fail(
                errors,
                f"{rel(path)} excede el límite de {limit} tokens renderizados: "
                f"{toks} tokens estimados ({len(text)} chars)",
            )


def warn_workflow_size_threshold(warnings: list[str]) -> None:
    """L12 (Plan 5.2): WARN para workflows > 5.000 tokens sin límite ERROR establecido.

    Ciclo.md y cierre-modulo.md tienen límites ERROR; los demás se advierten sin bloquear."""
    workflows_dir = ROOT / ".agent" / "workflows"
    large_workflows = []
    THRESHOLD = 5000
    for path in workflows_dir.glob("*.md"):
        if path.name in ("_README.md", "ciclo.md", "cierre-modulo.md"):
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        toks = estimate_workflow_tokens(text)
        if toks > THRESHOLD:
            large_workflows.append((rel(path), toks))
    if large_workflows:
        msg = f"Workflows que superan {THRESHOLD} tokens (se recortan en L16): "
        for name, toks in sorted(large_workflows):
            msg += f"{name} ({toks}), "
        msg = msg.rstrip(", ")
        warn(warnings, msg)


def validate_flow_guidance(errors: list[str]) -> None:
    """Every workflow must guide the user to one next framework command.

    FW-215 (4.35.5): todas las fuentes activas deben emitir el bloque único
    `Siguiente: comando instrucción`. El formato heredado queda prohibido para
    que una respuesta no presente dos instrucciones de continuación.

    5.3.0 (L3): cada plantilla de cierre de un workflow pasa por el validador
    único `turn_close.scan_templates` (modo plantilla, más bloque de código y
    línea de modelo); un workflow cumple si lleva al menos una plantilla, una
    mención en prosa de `Siguiente:` o `INTERNAL_RESULT` (los internos que solo
    invoca un driver devuelven su resultado, no un cierre)."""
    workflows_dir = ROOT / ".agent" / "workflows"
    manifest_path = workflows_dir / "manifest.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if not isinstance(manifest, dict):
            raise ValueError("el manifest debe ser un objeto JSON")
    except (OSError, ValueError) as exc:  # JSONDecodeError y UnicodeDecodeError son ValueError
        fail(
            errors,
            f"No se pueden validar las plantillas de cierre de los workflows: "
            f"{rel(manifest_path)} ilegible ({exc})",
        )
        return
    for path in sorted(workflows_dir.glob("*.md")):
        if path.name == "_README.md":
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        folded = text.casefold()
        for marker in FLOW_LEGACY_MARKERS:
            if marker.casefold() in folded:
                fail(
                    errors,
                    f"{rel(path)} usa el formato de flujo heredado ('{marker}') — "
                    "usar una única línea `Siguiente: <comando> <prompt>`",
                )
        findings = turn_close.scan_templates(text, manifest=manifest)
        for finding in findings:
            if not finding.defective:
                continue
            fail(
                errors,
                f"{rel(path)}:{finding.line} plantilla de cierre inválida "
                f"({', '.join(finding.codes)}): {finding.errors[0].fix}",
            )
        if not (findings or turn_close.template_mentions(text) or "INTERNAL_RESULT" in text):
            fail(
                errors,
                "Workflow sin orientación canónica (plantilla `Siguiente: comando prompt`, "
                f"mención en prosa de `Siguiente:` o INTERNAL_RESULT): {rel(path)}",
            )


def validate_flow_output_contract(mode: str, errors: list[str]) -> None:
    """FW-215 (4.35.5): ninguna superficie activa debe ordenar ni emitir el
    formato de flujo heredado; todas deben conservar comando y prompt en una
    única línea `Siguiente:`.

    `projections.py` se chequea SIEMPRE: es código de framework, sincronizado
    verbatim (`.agent/scripts/` está en `overwrite_dirs()`), nunca personalizado
    por el producto.

    `CLAUDE.md`/`AGENTS.md` se chequean SOLO en `mode == "template"`. Son
    ficheros del producto (`actualizar-framework.md:19` — "pueden tener
    ediciones del producto: no sobrescribir en silencio") y
    `propagate_framework.py` los deja fuera de la sincronización automática por
    diseño (`seed_agents_contract` solo añade el ancla Codex, nunca reescribe la
    constitución). Exigirlo en `initialized` habría hecho FALLAR a los 9
    adoptantes existentes en la primera propagación de 4.33.2 — descubierto al
    propagar, no en teoría: `0/9 actualizados` con este mismo error. El camino
    para un adoptante ya inicializado es el diff manual de
    `/actualizar-framework` paso 96, no este guard."""

    dead_surface_markers = FLOW_LEGACY_MARKERS + ("## Siguiente comando",)
    checks = [
        (ROOT / ".agent" / "scripts" / "projections.py", dead_surface_markers),
        (ROOT / ".agent" / "docs" / "extended_protocol.md", FLOW_LEGACY_MARKERS),
        (ROOT / ".agent" / "skills" / "decision_translation_core.md", FLOW_LEGACY_MARKERS),
        (ROOT / ".opencode" / "skills" / "decision-translation-core" / "SKILL.md", FLOW_LEGACY_MARKERS),
    ]
    if mode == "template":
        checks.append((ROOT / "CLAUDE.md", FLOW_LEGACY_MARKERS))
        checks.append((ROOT / "AGENTS.md", FLOW_LEGACY_MARKERS))
    workflows_dir = ROOT / ".agent" / "workflows"
    checks.extend((path, FLOW_LEGACY_MARKERS) for path in workflows_dir.glob("*.md") if path.name != "_README.md")
    for path, needles in checks:
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        folded = text.casefold()
        for needle in needles:
            if needle.casefold() in folded:
                fail(
                    errors,
                    f"{rel(path)} ordena o emite el formato de flujo muerto "
                    f"('{needle}') — usar una única línea "
                    "`Siguiente: <comando> <prompt>` (FW-215)",
                )
        for line_number, fragment in embedded_next_command_prompt_lines(text):
            fail(
                errors,
                f"{rel(path)}:{line_number} guarda prompt dentro de "
                f"next_command ({fragment!r}); separar el comando de next_action "
                "y dejar que la proyección componga `Siguiente:` (FW-215)",
            )


def validate_enriquecer_ticket_adr_gate(errors: list[str]) -> None:
    """enriquecer-ticket.md must declare the ADR gate (FW-027): a ticket that
    introduces an ADR-worthy decision blocks /tdd until the ADR is canonized
    via register_decision. Needle-check (anti-regresión de prosa)."""
    path = ROOT / ".agent" / "workflows" / "enriquecer-ticket.md"
    if not path.exists():
        fail(errors, ".agent/workflows/enriquecer-ticket.md faltante")
        return
    text = path.read_text(encoding="utf-8", errors="ignore").lower()
    if "register_decision" not in text or "adr" not in text:
        fail(errors, "enriquecer-ticket.md no declara el gate de decisión ADR (FW-027): debe bloquear /tdd y canonizar vía register_decision")


def validate_adapters(errors: list[str], warnings: list[str]) -> None:
    """Each environment adapter must declare an honest support status so the
    framework does not advertise unverified portability as if it were tested.
    `verified` = generated wrappers validated by the doctor; `experimental` =
    documented only. Keeps portability claims aligned with evidence."""
    adapters_dir = ROOT / ".agent" / "adapters"
    if not adapters_dir.exists():
        return
    matrix = adapters_dir / "_capability_matrix.md"
    if not matrix.exists():
        fail(errors, ".agent/adapters/_capability_matrix.md faltante (matriz de degradación por capacidad)")
    readme = adapters_dir / "README.md"
    readme_text = readme.read_text(encoding="utf-8", errors="ignore") if readme.exists() else ""
    # Skip non-adapter docs: README and `_`-prefixed meta files (e.g. _capability_matrix.md).
    for path in sorted(adapters_dir.glob("*.md")):
        if path.name == "README.md" or path.name.startswith("_"):
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        if not ADAPTER_STATUS_RE.search(text):
            fail(errors, f"Adapter sin estado de soporte (**Soporte:** verified|experimental): {rel(path)}")
        if "**Degradación:**" not in text:
            fail(errors, f"Adapter sin conformidad de degradación (**Degradación:** ver _capability_matrix.md): {rel(path)}")
        # Solo los adapters de harness (dispatchan subagentes) declaran esta línea;
        # adapters de capacidad opcional como code_graph.md quedan fuera de alcance.
        if path.stem in HARNESS_ADAPTER_STEMS and not ADAPTER_SUBAGENTS_RE.search(text):
            fail(errors, f"Adapter sin declaración de subagentes read-only (**Subagentes read-only:** sí|no|según-versión-verificar-una-vez): {rel(path)}")
        if readme_text and path.name not in readme_text:
            warn(warnings, f"Adapter no listado en adapters/README.md: {rel(path)}")


def validate_code_graph_adapter_wiring(errors: list[str]) -> None:
    """Wiring declarativo: ver doctor_wiring.json → code_graph_adapter."""
    validate_wiring_feature("code_graph_adapter", None, errors)


def validate_cambio_seguro_code_graph_wiring(errors: list[str]) -> None:
    """Wiring declarativo: ver doctor_wiring.json → cambio_seguro_code_graph."""
    validate_wiring_feature("cambio_seguro_code_graph", None, errors)


def validate_ci_workflow(errors: list[str]) -> None:
    """Portability (F1): a CI workflow must run the tool contract on a clean
    checkout — `clean clone → doctor OK + tests` — so portability is proven on
    every push, not just asserted. The contract may be delegated to ci_smoke.sh.

    Only the framework development repo is required to carry CI: it is the only
    tree that ships its own test suite (`tests/test_framework_doctor.py`), which
    the README adoption block deliberately does NOT copy. Adopter copies (and the
    F1 clean-copy smoke itself) have neither tests/ nor .github/, so the check is
    skipped for them — CI is a framework-maintenance concern, not an adopter one."""
    if not (ROOT / "tests" / "test_framework_doctor.py").exists():
        return
    workflows_dir = ROOT / ".github" / "workflows"
    if not workflows_dir.exists():
        fail(errors, ".github/workflows faltante (CI de clon limpio que corre el contrato F1)")
        return
    yamls = list(workflows_dir.glob("*.yml")) + list(workflows_dir.glob("*.yaml"))
    if not yamls:
        fail(errors, ".github/workflows sin workflow .yml/.yaml (CI de clon limpio — F1)")
        return
    combined = "\n".join(p.read_text(encoding="utf-8", errors="ignore") for p in yamls)
    runs_contract = "framework_doctor.py" in combined or "ci_smoke.sh" in combined
    if not runs_contract:
        fail(errors, "Ningún workflow de CI ejecuta el contrato (framework_doctor.py o ci_smoke.sh)")
    if "ci_smoke.sh" in combined:
        smoke = ROOT / ".agent" / "scripts" / "ci_smoke.sh"
        if not smoke.exists():
            fail(errors, "El CI invoca ci_smoke.sh pero .agent/scripts/ci_smoke.sh no existe")
        else:
            smoke_text = smoke.read_text(encoding="utf-8", errors="ignore")
            if "framework_doctor.py" not in smoke_text or "unittest discover -s tests" not in smoke_text:
                fail(errors, "ci_smoke.sh no ejecuta doctor + tests; el contrato F1 quedaría incompleto")


# Directorios de entorno/dependencias/cache que el doctor poda al buscar
# artefactos locales: están gitignored y no son parte de la superficie del
# framework. Sin esto, un .venv del producto generaba cientos de WARN por sus
# .pyc/__pycache__ y entrenaba al operador a ignorar al doctor (FW-029).
HYGIENE_PRUNE_DIRS = {
    ".git", ".venv", "venv", "env", ".tox", "node_modules",
    ".mypy_cache", ".pytest_cache", ".ruff_cache", ".next", ".nuxt", "site-packages",
}


def _git_tracked_set() -> set[str] | None:
    """Primitive compartido: rutas tracked por git (relativas a ROOT, sep='/').
    Devuelve None fuera de git, sin git, o ante cualquier error.
    Usa decode("utf-8","ignore") para robustez con rutas no-ASCII (FW-042/FW-047)."""
    try:
        out = subprocess.run(
            ["git", "ls-files", "-z"], cwd=ROOT, capture_output=True, timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if out.returncode != 0:
        return None
    return {p for p in out.stdout.decode("utf-8", "ignore").split("\0") if p}


def validate_hygiene(warnings: list[str]) -> None:
    tracked = _git_tracked_set()
    for path in ROOT.rglob("*"):
        if any(part in HYGIENE_PRUNE_DIRS for part in path.parts):
            continue
        if path.name == ".DS_Store" or path.suffix == ".pyc" or path.name == "__pycache__":
            # FW-047: un artefacto gitignored+untracked es ruido inofensivo; solo
            # advertir si está realmente tracked (o estamos fuera de git).
            if tracked is not None and rel(path) not in tracked:
                continue
            warn(warnings, f"Artefacto local/cache presente: {rel(path)}")


def validate_git_skip_worktree_bits(errors: list[str]) -> None:
    """FW-T06 (Example Tool, fallo objetivo): archivos bajo `.agent/` con el bit
    `skip-worktree`/`assume-unchanged` hacen que git IGNORE cambios ahí — un
    `/actualizar-framework` puede dejar el disco con contenido nuevo, HEAD sin
    avanzar, y `git status` reportando limpio de todos modos. "Working tree
    limpio" no basta si git ignora rutas; el doctor debe verlo, no solo la
    precondición de `/actualizar-framework`.

    `git ls-files -v` marca cada ruta con una letra: mayúscula = normal
    ('H' cached); minúscula = tiene el bit assume-unchanged o skip-worktree
    puesto encima de ese estado. Filtrar por `^H ` (como usa el propio field
    report) detecta ambos casos con un solo grep."""
    try:
        out = subprocess.run(
            ["git", "ls-files", "-v", "--", ".agent/"],
            cwd=ROOT, capture_output=True, timeout=30, text=True,
        )
    except (OSError, subprocess.SubprocessError):
        return
    if out.returncode != 0:
        return
    flagged = sorted(
        line.split(" ", 1)[1]
        for line in out.stdout.splitlines()
        if line and not line.startswith("H ")
    )
    if flagged:
        fail(
            errors,
            "Archivos bajo .agent/ con bit skip-worktree/assume-unchanged "
            "(git ignora cambios ahí; un upgrade puede quedar a medias sin que "
            "'git status' lo detecte): " + ", ".join(flagged) + ". Reparar con: "
            "git update-index --no-skip-worktree --no-assume-unchanged <ruta>",
        )


# Patrones de auto-modificación de la capa constitucional: una skill importada
# (que el LLM lee como instrucciones autorizadas) NO debe instruir reescribir
# los guardarraíles, la constitución ni los invariantes.
CONSTITUTION_TARGETS = ("GUARDRAILS_MASTER.md", "CLAUDE.md", "03_invariants.md", "AGENTS.md")
SELF_MODIFY_VERB_RE = re.compile(
    r"(?i)\b(modif\w+|edit\w+|sobre[- ]?escrib\w+|overwrit\w+|reescrib\w+|"
    r"rewrit\w+|borr\w+|delet\w+|append|añad\w+|inject\w+|inyect\w+|replace|reemplaz\w+)\b"
)


def validate_invariant_acceptance_parity(mode: str, state: dict, warnings: list[str]) -> None:
    """Si `03_invariants.md` cambió respecto a HEAD y hay módulos `completed`,
    advertir que deben re-validarse: un invariante nuevo puede invalidar
    retroactivamente un módulo ya aceptado. Vector V3.2 del red team.

    Pragmático (3.3): detección por git-diff, sin vínculo estructurado
    invariante→módulo (diferido). No-op fuera de git, en template o sin
    módulos completos."""
    if mode != "initialized":
        return
    completed = []
    statuses = state.get("module_statuses", {}) if isinstance(state, dict) else {}
    if isinstance(statuses, dict):
        completed = [m for m, s in statuses.items() if s == "completed"]
    if not completed:
        return
    if not (ROOT / ".git").exists():
        return
    try:
        result = subprocess.run(
            ["git", "-C", str(ROOT), "diff", "--quiet", "HEAD", "--", "_project/03_invariants.md"],
            capture_output=True, timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return  # git ausente o estado raro: no-op
    if result.returncode == 1:  # difiere de HEAD
        warn(warnings, f"03_invariants.md cambió respecto a HEAD y hay {len(completed)} módulo(s) completed; re-validar que siguen cumpliendo los invariantes (/actualizar-invariante paso 5)")


def _parse_doc_status(line_val: str) -> str | None:
    if "|" in line_val:
        return None
    val = line_val.strip()
    if "✅" in val or "verificado con cliente" in val.lower() or "completado" in val.lower() or "completed" in val.lower():
        return "completed"
    if "🔄" in val or "en progreso" in val.lower() or "in_progress" in val.lower() or "ready_for_validation" in val.lower():
        return "in_progress"
    if "🛑" in val or "bloqueado" in val.lower() or "blocked" in val.lower():
        return "blocked"
    if "⬜" in val or "pendiente" in val.lower() or "not_started" in val.lower():
        return "not_started"
    return None


def _status_contradicts(doc_status: str, canonical: str) -> bool:
    if doc_status == canonical:
        return False
    if canonical == "ready_for_validation" and doc_status == "in_progress":
        return False
    if canonical == "blocked" and doc_status in ("in_progress", "not_started", "blocked"):
        return False
    return True


def validate_module_acceptance_status_alignment(
    mode: str, state: dict[str, Any], warnings: list[str]
) -> None:
    """TL FW-234: aviso cosmético cuando la línea Estado: en 08_module_acceptance.md
    contradice el estado canónico en module_statuses (no dispara /reconciliar)."""
    acceptance_path = ROOT / "_project" / "08_module_acceptance.md"
    if not acceptance_path.exists():
        return
    text = acceptance_path.read_text(encoding="utf-8", errors="ignore")
    module_statuses = state.get("module_statuses", {}) if isinstance(state, dict) else {}
    if not isinstance(module_statuses, dict):
        return
    import validate_acceptance

    for module_id, canonical in module_statuses.items():
        if not isinstance(canonical, str):
            continue
        section = validate_acceptance.module_section_lines(text, module_id)
        if not section:
            continue
        for line in section[1:]:
            m = re.match(r"^(?:\*\*)?Estado:(?:\*\*)?\s*(.+)$", line.strip(), re.IGNORECASE)
            if not m:
                continue
            raw_val = m.group(1).strip()
            doc_status = _parse_doc_status(raw_val)
            if doc_status is None:
                continue
            if _status_contradicts(doc_status, canonical):
                warn(
                    warnings,
                    f"08_module_acceptance.md: línea 'Estado:' del módulo '{module_id}' ({raw_val}) "
                    f"contradice el estado canónico '{canonical}' de module_statuses "
                    f"(aviso cosmético — la fuente canónica es 03_project_state.json)",
                )
            break


_BUG_FIX_DONE_RE = re.compile(
    r"(?i)\b(corregid[oa]s?|arreglad[oa]s?|resuelt[oa]s?|solucionad[oa]s?|cerrad[oa]s?|fixed|tests? en verde)\b"
)
_BUG_FIX_PARTIAL_RE = re.compile(
    r"(?i)\b(parcial\w*|solo|sólo|pendiente\w*|falta\w*"
    r"|no\s+(?:est[áa]\s+)?(?:resuelt|corregid|arreglad|integrad|cerrad)\w*"
    r"|sigue[n]?\s+sin|sin\s+(?:corregir|resolver|arreglar)|reabiert\w*)\b"
)


def validate_bugs_fixed_but_open(state: Any, warnings: list[str]) -> None:
    """TL-FW-313: aviso (nunca error) de bugs `Abierto` o `Deuda Técnica` cuyo
    `fix` dice que están corregidos y no que lo estén solo en parte."""
    bugs = state.get("_bugs") if isinstance(state, dict) else None
    flagged = [
        bug.get("id") for bug in bugs or []
        if isinstance(bug, dict) and bug.get("status") in {"Abierto", "Deuda Técnica"}
        and isinstance(bug.get("fix"), str) and _BUG_FIX_DONE_RE.search(bug["fix"])
        and not _BUG_FIX_PARTIAL_RE.search(bug["fix"])
    ] if isinstance(bugs, list) else []
    if flagged:
        shown = ", ".join(str(bug_id) for bug_id in flagged[:10])
        more = f" y {len(flagged) - 10} más" if len(flagged) > 10 else ""
        warn(
            warnings,
            f"Bugs abiertos cuyo fix dice que están corregidos (TL-FW-313): {shown}{more}. "
            "Resuélvelos con resolve_bug (no reabre módulos) o quita la frase con amend_bug.",
        )


def validate_precommit_hook_installed(warnings: list[str]) -> None:
    """Los gates de cierre/transición solo se aplican si el pre-commit del
    framework está instalado. Sin él, un cierre fabricado (in_progress→completed)
    no se bloquea en el commit. WARN (no FAIL): no todos los entornos son git ni
    todos quieren el hook. Vector V3.1 del red team.

    FW-207 (cierra FW-107): el hook se resuelve con `git rev-parse --git-path`, no
    con `ROOT/.git/hooks`. En un `git worktree` `ROOT/.git` es un archivo puntero
    y la comprobación se saltaba en silencio — justo el entorno de los ejecutores
    de `/ciclo fase --paralelo`, que corren en worktrees."""
    source = ROOT / ".agent" / "scripts" / "pre_commit.sh"
    if not source.exists():
        return
    try:
        rel = subprocess.run(
            ["git", "-C", str(ROOT), "rev-parse", "--git-path", "hooks/pre-commit"],
            capture_output=True, text=True, timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return  # git ausente: no-op
    if rel.returncode != 0 or not rel.stdout.strip():
        return  # fuera de un repo git: no-op
    hook_path = rel.stdout.strip()
    hook = Path(hook_path) if os.path.isabs(hook_path) else ROOT / hook_path
    if not hook.exists():
        warn(warnings, "pre-commit del framework no instalado; los gates de cierre/transición no se aplican en commit. Instalar: bash .agent/scripts/pre_commit.sh install")
        return
    # TL-FW-320 (5.3.1 L2.3): el hook instalado debe ser el lanzador que
    # `pre_commit.sh install` escribe; se lee del script, sin ejecutarlo.
    launcher = _PRECOMMIT_LAUNCHER_RE.search(source.read_text(encoding="utf-8", errors="ignore"))
    installed = hook.read_text(encoding="utf-8", errors="ignore")
    if launcher is None:
        warn(warnings, "no se pudo obtener el lanzador de .agent/scripts/pre_commit.sh; no se comprueba el hook instalado")
    elif installed == launcher.group(1):
        return
    elif _PRECOMMIT_COPY_HEADER_RE.search(installed):
        warn(warnings, ".git/hooks/pre-commit es una copia de pre_commit.sh, no el lanzador: los cambios del script no le llegan (TL-FW-320). Reinstala con: bash .agent/scripts/pre_commit.sh install")
    else:
        warn(warnings, ".git/hooks/pre-commit difiere del framework; los gates de cierre/transición podrían no aplicarse. Revisar o reinstalar con: bash .agent/scripts/pre_commit.sh install")


_PRECOMMIT_LAUNCHER_RE = re.compile(r"cat <<'LAUNCHER'\n(.*?\n)LAUNCHER\n", re.DOTALL)
_PRECOMMIT_COPY_HEADER_RE = re.compile(r"^# Framework v[0-9]+ pre-commit hook", re.MULTILINE)


def validate_prepush_hook_installed(warnings: list[str]) -> None:
    """ADR-139/FW-201: sin el pre-push instalado, el aviso GH001 de tamaño
    solo se ve DESPUÉS del push (o nunca, si el operador lo ignora — FW-168,
    recurrencia verificada 2026-09-01). WARN, no FAIL: mismo motivo que
    validate_precommit_hook_installed — no todos los entornos son git, y
    ninguna barrera local es infalible contra --no-verify."""
    source = ROOT / ".agent" / "scripts" / "pre_push.sh"
    if not source.exists():
        return
    try:
        rel = subprocess.run(
            ["git", "-C", str(ROOT), "rev-parse", "--git-path", "hooks/pre-push"],
            capture_output=True, text=True, timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return
    if rel.returncode != 0 or not rel.stdout.strip():
        return
    hook_path = rel.stdout.strip()
    hook = Path(hook_path) if os.path.isabs(hook_path) else ROOT / hook_path
    if not hook.exists():
        warn(warnings, "pre-push del framework no instalado; el aviso GH001 de tamaño no se detecta antes de publicar. Instalar: bash .agent/scripts/pre_push.sh install")
        return
    if hook.read_text(encoding="utf-8", errors="ignore") != source.read_text(encoding="utf-8", errors="ignore"):
        warn(warnings, ".git/hooks/pre-push difiere del framework; el gate de tamaño podría no aplicarse. Reinstalar: bash .agent/scripts/pre_push.sh install")


def validate_imported_skills(errors: list[str]) -> None:
    """Toda skill importada debe tener procedencia registrada y no contener
    instrucciones de auto-modificación de la capa constitucional.

    Vector V5.1 del red team: las skills .md son prompts que el LLM trata como
    instrucciones autorizadas — sin separación datos/instrucciones. No-op si la
    carpeta no existe (template)."""
    imported_dir = ROOT / ".agent" / "skills" / "imported"
    if not imported_dir.exists():
        return
    provenance_path = ROOT / "_project" / "14_skill_provenance.md"
    provenance = provenance_path.read_text(encoding="utf-8", errors="ignore") if provenance_path.exists() else ""

    # Una skill importada = archivo .md suelto o subdirectorio (con SKILL.md o .md dentro).
    # README.md es documentación de la carpeta, no una skill.
    skill_names: set[str] = set()
    skill_files: list[Path] = []
    for entry in imported_dir.iterdir():
        if entry.is_file() and entry.suffix == ".md":
            if entry.name == "README.md":
                continue
            skill_names.add(entry.stem)
            skill_files.append(entry)
        elif entry.is_dir():
            skill_names.add(entry.name)
            skill_files.extend(entry.rglob("*.md"))

    for name in sorted(skill_names):
        if name not in provenance:
            fail(errors, f"Skill importada sin procedencia registrada en 14_skill_provenance.md: '{name}'")

    for skill_file in skill_files:
        text = skill_file.read_text(encoding="utf-8", errors="ignore")
        for line in text.splitlines():
            if any(target in line for target in CONSTITUTION_TARGETS) and SELF_MODIFY_VERB_RE.search(line):
                fail(errors, f"Skill importada con instrucción de auto-modificación de la constitución: {rel(skill_file)} (línea: {line.strip()[:60]}…)")
                break


# ---------------------------------------------------------------------------
# Wiring declarativo (3.18.0, FW-069): los punteros requeridos por cada
# feature viven en doctor_wiring.json (junto a este script — el spec es del
# validador, no del repo validado). validate_wiring_feature() los adjudica
# con un solo engine. Una feature nueva se cablea con una entrada JSON
# (+ calibración known-good/known-bad), no con un validador artesanal.
# ---------------------------------------------------------------------------
_WIRING_SPEC_PATH = Path(__file__).resolve().parent / "doctor_wiring.json"
_WIRING_SPEC_CACHE: dict[str, Any] | None = None


def _wiring_spec() -> dict[str, Any]:
    global _WIRING_SPEC_CACHE
    if _WIRING_SPEC_CACHE is None:
        _WIRING_SPEC_CACHE = json.loads(_WIRING_SPEC_PATH.read_text(encoding="utf-8"))
    return _WIRING_SPEC_CACHE


def _wiring_re_flags(spec_flags: str) -> int:
    flags = 0
    if "i" in spec_flags:
        flags |= re.IGNORECASE
    if "s" in spec_flags:
        flags |= re.DOTALL
    return flags


def _wiring_framework_mode() -> str | None:
    """Read the mode needed by conditional wiring checks without emitting errors."""
    state_path = ROOT / "_project" / "03_project_state.json"
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    mode = state.get("framework_mode") if isinstance(state, dict) else None
    return mode if isinstance(mode, str) else None


def validate_wiring_feature(
    feature: str,
    manifest: dict[str, Any] | None,
    errors: list[str],
    mode: str | None = None,
) -> None:
    """Engine genérico de wiring: adjudica la entrada `feature` de
    doctor_wiring.json contra el árbol en ROOT. Preserva los mensajes
    exactos de los validadores artesanales que reemplaza."""
    spec = _wiring_spec()["features"][feature]

    mc = spec.get("manifest_command")
    if mc is not None:
        commands = manifest.get("commands", []) if isinstance(manifest, dict) else []
        entry = next((c for c in commands if isinstance(c, dict) and c.get("name") == mc["name"]), None)
        if entry is None:
            fail(errors, mc["missing_message"])
        else:
            if "claude_command" in mc and entry.get("claude_command") is not mc["claude_command"]:
                fail(errors, mc["claude_command_message"])
            source = entry.get("source")
            if source != mc["source"]:
                fail(errors, mc["source_mismatch_message"].replace("{source}", str(source)))
            elif "source_missing_message" in mc and not (ROOT / source).exists():
                fail(errors, mc["source_missing_message"].replace("{source}", str(source)))

    effective_mode = mode if mode is not None else _wiring_framework_mode()
    for fc in spec.get("files", []):
        if fc.get("template_only") and effective_mode == "initialized":
            continue
        path = ROOT / fc["path"]
        if not path.exists():
            if not fc.get("optional", False):
                fail(errors, fc["missing_message"])
            continue
        raw = path.read_text(encoding="utf-8", errors="ignore")
        low = raw.lower()

        def _has(needle: str, case_sensitive: bool) -> bool:
            return (needle in raw) if case_sensitive else (needle.lower() in low)

        for nd in fc.get("needles", []):
            cs = nd.get("case_sensitive", False)
            if "needle" in nd:
                ok = _has(nd["needle"], cs)
            elif "all" in nd:
                ok = all(_has(n, cs) for n in nd["all"])
            else:
                ok = any(_has(n, cs) for n in nd["any"])
            if not ok:
                fail(errors, nd["message"])
        for fr in fc.get("forbidden_regex", []):
            target = low if fr.get("on_lowercase", False) else raw
            if re.search(fr["pattern"], target, _wiring_re_flags(fr.get("flags", ""))):
                fail(errors, fr["message"])
        for rr in fc.get("required_regex", []):
            if not re.search(rr["pattern"], raw, _wiring_re_flags(rr.get("flags", ""))):
                fail(errors, rr["message"])
        for ar in fc.get("all_regex", []):
            if not all(re.search(pat, raw) for pat in ar["patterns"]):
                fail(errors, ar["message"])
        for ob in fc.get("order_before", []):
            if ob["first"] in raw and ob["then"] in raw and raw.index(ob["first"]) > raw.index(ob["then"]):
                fail(errors, ob["message"])


def validate_root_docs_contract(mode: str, errors: list[str], warnings: list[str]) -> None:
    """ADR-073 (5.2.3 L4): `AGENTS.md` es la fuente única y el framework solo es
    dueño del bloque `GESTIONADO: framework`.

    - ERROR si una raíz de la PLANTILLA ordena leer `CLAUDE.md` desde otro arnés
      o cargar `GUARDRAILS_MASTER.md` completo al arrancar. En un proyecto
      `initialized` la raíz es del proyecto, así que es WARN con el comando de
      migración: un ERROR haría fallar el doctor del upgrade y revertiría la
      propagación de un adoptante por una línea que el framework no escribió
      (FW-202; la revisión de seguridad de L4 lo confirmó).
    - WARN si el bloque falta, está mal formado o su huella no coincide."""
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    try:
        import root_docs  # noqa: E402
    except Exception as exc:  # noqa: BLE001
        fail(errors, f"no se pudo importar root_docs.py para verificar las raíces: {exc!r}")
        return
    for rel in ("AGENTS.md", "CLAUDE.md"):
        path = ROOT / rel
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        malformed = False
        try:
            block = root_docs.find_block(text)
        except root_docs.BlockError as exc:
            warn(warnings, f"{rel}: {exc}")
            block, malformed = None, True
        orders = root_docs.forbidden_orders(text)
        for reason in orders:
            message = f"{rel} {reason} (ADR-073: AGENTS.md es la fuente única y se abre bajo demanda)"
            if mode == "template":
                fail(errors, message)
            else:
                warn(warnings, f"{message}; ejecutar `python3 .agent/scripts/migrate_root_docs.py --project .` desde la plantilla")
        if malformed:
            continue
        # Sin bloque, la raíz entera es heredada: ya se avisa de migrarla y migrate_root_docs.py
        # necesita sus anclas antiguas («## Framework v4»), así que no se aconseja tocarlas.
        own = "" if block is None else text[:block.start] + text[block.end:]
        for term, use in turn_close.RETIRED_VOCABULARY.items():
            if term in own:
                warn(warnings, f"{rel}: vocabulario retirado fuera del bloque GESTIONADO: «{term}» → {use} (FW-313)")
        if block is None:
            warn(
                warnings,
                f"{rel} no tiene el bloque GESTIONADO: framework; el framework no puede actualizarlo "
                "(ejecutar migrate_root_docs.py desde la plantilla, con diff y aprobación)",
            )
        elif root_docs.edited_by_hand(block):
            warn(
                warnings,
                f"{rel}: el bloque GESTIONADO: framework no coincide con su huella (¿editado a mano?); "
                "lo de dentro lo gestiona propagate_framework.py, el contenido propio va fuera del bloque",
            )


# FW-294 (5.2.3 L7): lo que el host guarda fuera del proyecto y compite con el framework.
HOST_MEMORY_NEEDLES = ("slow Codex subagent", "small self-contained tasks", "BLOCKED_HANDOFF")
HOST_MEMORY_FILES = ("memory_summary.md", "MEMORY.md")


def _superpowers_plugin_enabled(plugins: Any) -> bool:
    """FW-306: solo cuenta un plugin habilitado (`enabledPlugins`, `[plugins.*]`); los catálogos no."""
    return isinstance(plugins, dict) and any(
        "superpowers" in str(key).lower()
        and (value is True or (isinstance(value, dict) and value.get("enabled", True) is not False))
        for key, value in plugins.items())


def _host_memory_rules(text: str) -> str:
    """FW-307: fuera las secciones «keywords» que genera Codex y las líneas que son solo listas de términos."""
    kept, in_keywords = [], False
    for line in text.splitlines():
        heading = re.match(r"\s*#+\s*(.*?)\s*$", line)
        in_keywords = heading.group(1).lower() == "keywords" if heading else in_keywords
        items = [item.strip() for item in line.strip().lstrip("-* ").split(",")]
        if heading or not (in_keywords or (len(items) >= 6 and all(0 < len(i) <= 40 for i in items[1:]))):
            kept.append(line)
    return "\n".join(kept)


def validate_host_conflicts(mode: str, warnings: list[str], home: Path | None = None) -> None:
    """Solo avisos (WARN), y solo si los archivos del host existen y se pueden leer.

    - superpowers activo en `~/.claude/settings.json` o `~/.codex/config.toml` junto
      al framework: dos métodos compiten y el framework manda. En la plantilla se
      omite: quien mantiene el framework puede usarlo a propósito.
    - reglas del adaptador copiadas en la memoria global de Codex: se inyectan en
      todas las sesiones de todos los proyectos como si fueran preferencias del builder."""
    if home is None:
        override = os.environ.get("FRAMEWORK_HOST_HOME")
        home = Path(override) if override else Path.home()
    if mode != "template":
        claude_settings = home / ".claude" / "settings.json"
        try:
            if claude_settings.is_file() and _superpowers_plugin_enabled(
                json.loads(claude_settings.read_text(encoding="utf-8")).get("enabledPlugins")
            ):
                warn(warnings, "superpowers está activo en ~/.claude/settings.json junto al framework: dos métodos "
                     "compiten; el framework manda (desactivar superpowers en este proyecto o ignorar sus flujos)")
        except (OSError, ValueError, AttributeError):
            pass
        codex_config = home / ".codex" / "config.toml"
        try:
            if codex_config.is_file() and _superpowers_plugin_enabled(
                tomllib.loads(codex_config.read_text(encoding="utf-8")).get("plugins")
            ):
                warn(warnings, "superpowers está activo en ~/.codex/config.toml junto al framework: dos métodos "
                     "compiten; el framework manda")
        except (OSError, ValueError, AttributeError):
            pass
    for name in HOST_MEMORY_FILES:
        memory = home / ".codex" / "memories" / name
        try:
            text = _host_memory_rules(memory.read_text(encoding="utf-8", errors="ignore")) if memory.is_file() else ""
        except OSError:
            continue
        found = [needle for needle in HOST_MEMORY_NEEDLES if needle in text]
        if found:
            warn(warnings, f"~/.codex/memories/{name} contiene reglas del adaptador Codex ({', '.join(found)}) "
                 "como si fueran preferencias del builder; se inyectan en todos los proyectos. Bórralas a mano "
                 "(FW-294): las reglas del framework no se guardan en la memoria del host")


def validate_fw_registry(mode: str, warnings: list[str], errors: list[str] | None = None) -> None:
    """FW-303 (5.2.3 L8) y FW-342 (5.3.0 L9). Plantilla: IDs de FW duplicados (aviso).
    Adoptante: prefijo sin declarar (aviso); entradas sin `<PREFIJO>-FW-NNN` con fecha entre
    PREFIX_ENFORCED_FROM y PREFIX_REQUIRED_FROM (aviso) y desde PREFIX_REQUIRED_FROM (error,
    solo si el llamador pasa `errors`). Lo antiguo no se renumera."""
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import fw_registry  # noqa: E402

    try:
        if mode == "template":
            report = ROOT / ".agent" / "docs" / "field_report.md"
            if report.is_file():
                duplicated = fw_registry.duplicate_ids(report.read_text(encoding="utf-8", errors="ignore"))
                if duplicated:
                    warn(warnings, "field_report.md tiene IDs de FW repetidos: " + ", ".join(duplicated)
                         + " (cada FW nuevo toma el siguiente número libre)")
            return
        report = ROOT / "_feedback" / "field_report.md"
        if not report.is_file():
            return
        text = report.read_text(encoding="utf-8", errors="ignore")
        prefix = fw_registry.fw_prefix(ROOT)
        if prefix is None:
            warn(warnings, "falta `fw_prefix` en _project/15_environment_profile.md: las FW nuevas del proyecto "
                 "usan `<PREFIJO>-FW-NNN` para no chocar con la numeración de la plantilla")
        required = fw_registry.unprefixed_required_entries(text)
        if errors is not None and required:
            names = ", ".join(required[:5]) + ("…" if len(required) > 5 else "")
            if prefix is None:
                fail(errors, f"_feedback/field_report.md: entradas sin prefijo ({names}) y falta `fw_prefix`: "
                     "declararlo en _project/15_environment_profile.md (p. ej. `fw_prefix: TL`) y renombrar esas "
                     "entradas a `<PREFIJO>-FW-NNN`")
            else:
                fail(errors, f"_feedback/field_report.md: entradas sin el prefijo `{prefix}-` ({names}); "
                     f"renombrarlas a `{prefix}-FW-NNN` (p. ej. `## {prefix}-FW-NNN — título`); "
                     f"las anteriores a {fw_registry.PREFIX_REQUIRED_FROM} no se renumeran")
        if prefix is None:
            return
        window = [entry for entry in fw_registry.unprefixed_new_entries(text) if entry not in required]
        if errors is None:
            window = fw_registry.unprefixed_new_entries(text)
        if window:
            warn(warnings, f"_feedback/field_report.md: entradas nuevas sin el prefijo `{prefix}-` "
                 f"({', '.join(window[:5])}{'…' if len(window) > 5 else ''}); las antiguas no se renumeran")
    except OSError:
        return


def validate_root_docs_freshness(manifest: dict[str, Any], errors: list[str]) -> None:
    """Docs generados desde manifest (3.18.0, FW-070): los bloques GENERADO de
    CLAUDE.md y AGENTS.md deben coincidir con lo que sync_claude_commands.py
    derivaría del manifest actual; y toda referencia a `.agent/workflows/*.md`
    en los 4 docs raíz debe apuntar a un archivo existente (anti-drift de poda)."""
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    try:
        import sync_claude_commands as scc  # noqa: E402
    except Exception as exc:  # noqa: BLE001
        fail(errors, f"no se pudo importar sync_claude_commands.py para verificar frescura: {exc!r}")
        return

    blocks = [
        ("CLAUDE.md", "maquinaria-interna",
         lambda text: scc.render_claude_internal_body(manifest)),
        ("AGENTS.md", "tabla-comandos",
         lambda text: scc.render_agents_table_body(manifest)),
    ]
    for rel, tag, render in blocks:
        path = ROOT / rel
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        pattern = scc.GENERATED_BLOCK_RE_TMPL.format(tag=re.escape(tag))
        match = re.search(pattern, text, re.DOTALL)
        if match is None:
            fail(errors, f"{rel}: bloque GENERADO '{tag}' ausente; restaurar marcadores y ejecutar sync_claude_commands.py")
            continue
        if match.group(1) != render(text):
            fail(errors, f"{rel}: bloque GENERADO '{tag}' desactualizado respecto al manifest; ejecutar python3 .agent/scripts/sync_claude_commands.py")

    for cmd in (manifest.get("commands", []) if isinstance(manifest, dict) else []):
        if not isinstance(cmd, dict):
            continue
        if not isinstance(cmd.get("builder_facing"), bool):
            fail(errors, f"manifest.json: comando '{cmd.get('name', '?')}' sin builder_facing booleano (4.0.0, FW-071)")

    for rel in ("README.md", "CLAUDE.md", "AGENTS.md", "llms.txt"):
        path = ROOT / rel
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        for ref in sorted(set(re.findall(r"\.agent/workflows/([\w\-áéíóúüñ]+\.md)", text))):
            if ref in ("manifest.json",):
                continue
            if not (ROOT / ".agent" / "workflows" / ref).exists():
                fail(errors, f"{rel} referencia .agent/workflows/{ref} que no existe (drift de poda; actualizar el doc)")


def validate_checkpoint_template_wiring(errors: list[str]) -> None:
    """Wiring declarativo: ver doctor_wiring.json → checkpoint_template (3.19.0, FW-074)."""
    validate_wiring_feature("checkpoint_template", None, errors)


def validate_proactive_experts_wiring(errors: list[str]) -> None:
    """Wiring declarativo: ver doctor_wiring.json → proactive_experts (4.1.0, FW-072)."""
    validate_wiring_feature("proactive_experts", None, errors)


def validate_stack_escalado_wiring(errors: list[str]) -> None:
    """Wiring declarativo: ver doctor_wiring.json → stack_escalado (FW-133,
    2026-08-25). La decisión de stack en /tech-design-mvp debe declarar
    costura de escape (o puerta de una sola dirección explícita), y
    /nuevo-proyecto debe proponer stack antes de pedírselo al builder sin
    criterio."""
    validate_wiring_feature("stack_escalado", None, errors)


def validate_domain_sweep_receipts(state: dict[str, Any], mode: str, errors: list[str]) -> None:
    """FW-134: en un proyecto initialized, todo módulo tier_2 en
    modules.completed debe tener un checkpoint domain_sweep sellado (FW-131) —
    no solo la prosa de sugerir-skills.md documentando el paso.
    validate_proactive_experts_wiring ya cubre que la PLANTILLA documenta el
    barrido; esto lee 03_project_state.json real para saber si un proyecto
    concreto lo ejecutó. No-op en la propia plantilla (modules.completed=[])."""
    if mode != "initialized":
        return
    work_tiers = state.get("work_tiers")
    if not isinstance(work_tiers, dict):
        work_tiers = {}
    checkpoints = (
        state.get("workflow_receipts", {}).get("checkpoints", {}).get("domain_sweep", {})
    )
    completed = state.get("modules", {}).get("completed", [])
    if not isinstance(completed, list):
        return
    for mod in completed:
        entry = work_tiers.get(mod, {})
        if isinstance(entry, dict) and entry.get("tier") == "tier_2" and mod not in checkpoints:
            fail(
                errors,
                f"módulo tier_2 '{mod}' completado sin checkpoint domain_sweep "
                "(FW-131/FW-134): sellar con mutate_state record_checkpoint",
            )


def validate_expert_audit_receipts(state: dict[str, Any], mode: str, errors: list[str]) -> None:
    """FW-134: análogo a validate_domain_sweep_receipts para el checkpoint
    expert_audit (FW-132) — todo módulo tier_2 completado debe tener la
    auditoría de experto sellada, no solo el gate suave en prosa del paso 3.9
    de cierre-modulo.md."""
    if mode != "initialized":
        return
    work_tiers = state.get("work_tiers")
    if not isinstance(work_tiers, dict):
        work_tiers = {}
    checkpoints = (
        state.get("workflow_receipts", {}).get("checkpoints", {}).get("expert_audit", {})
    )
    completed = state.get("modules", {}).get("completed", [])
    if not isinstance(completed, list):
        return
    for mod in completed:
        entry = work_tiers.get(mod, {})
        if isinstance(entry, dict) and entry.get("tier") == "tier_2" and mod not in checkpoints:
            fail(
                errors,
                f"módulo tier_2 '{mod}' completado sin checkpoint expert_audit "
                "(FW-132/FW-134): sellar con mutate_state record_checkpoint",
            )


def validate_antigravity_menu_duplicado_wiring(errors: list[str]) -> None:
    """Wiring declarativo: ver doctor_wiring.json → antigravity_menu_duplicado
    (FW-135/136, 2026-08-25). Antigravity lee `.claude/commands/` y
    `.agents/skills/` a la vez sin mecanismo de ignore conocido — mientras esa
    degradación no se cierre con código, tiene que quedar declarada y no
    regresar en silencio a "sin wrappers generados"."""
    validate_wiring_feature("antigravity_menu_duplicado", None, errors)


FAIL_OPEN_DECLARATION = "fail-open-declarado:"


def validate_gate_failure_semantics(errors: list[str]) -> None:
    """FW-194: una verificación no puede afirmar más de lo que midió.

    Seis instancias en tres sesiones independientes de la misma clase — un gate
    que reporta éxito sin haber comprobado nada: `git grep … || true` tragándose
    un `fatal:` de pathspec, una tubería que devuelve el exit de `tail` en vez
    del de la herramienta, `rc=0` con cobertura incompleta.

    Dos reglas mecánicas sobre los scripts de `.agent/scripts/`:

    1. Declarar `pipefail`. Sin él, `cmd | filtro` devuelve el exit del filtro:
       una herramienta ausente o reventada pasa por verde. Un script que
       deliberadamente no propaga errores lo declara con `set +e` y su razón en
       la misma línea.
    2. Todo `|| true` lleva `# fail-open-declarado: <razón>`. Enmascarar el exit
       code es legítimo cuando el veredicto lo establece una comprobación
       posterior e independiente — pero eso hay que decirlo. Sin declaración es
       indistinguible de un fail-open accidental, que es exactamente lo que
       FW-194 destapó."""
    scripts_dir = ROOT / ".agent" / "scripts"
    if not scripts_dir.is_dir():
        return
    for script in sorted(scripts_dir.glob("*.sh")):
        rel = script.relative_to(ROOT)
        lines = script.read_text(encoding="utf-8", errors="ignore").splitlines()
        texto = "\n".join(lines)
        declara_sin_errores = any(
            line.strip().startswith("set +e") and "#" in line for line in lines
        )
        if "pipefail" not in texto and not declara_sin_errores:
            fail(errors, f"{rel} no declara 'pipefail': una tubería devuelve el exit del último "
                         f"eslabón, así que una herramienta ausente o rota pasa por verde (FW-194). "
                         f"Si el script no debe propagar errores, declararlo con 'set +e  # razón'")
        for numero, line in enumerate(lines, start=1):
            if "|| true" in line and FAIL_OPEN_DECLARATION not in line:
                fail(errors, f"{rel}:{numero} usa '|| true' sin declarar por qué; añadir "
                             f"'# {FAIL_OPEN_DECLARATION} <razón>' en la misma línea. Enmascarar el "
                             f"exit code solo es válido si el veredicto lo da una comprobación "
                             f"posterior e independiente (FW-194)")


def validate_modo_mantenimiento_wiring(errors: list[str]) -> None:
    """Wiring declarativo: ver doctor_wiring.json → modo_mantenimiento (FW-225)."""
    validate_wiring_feature("modo_mantenimiento", None, errors)


def validate_cosecha_sesion_wiring(errors: list[str]) -> None:
    """Wiring declarativo: ver doctor_wiring.json → cosecha_sesion (4.31.0, FW-194)."""
    validate_wiring_feature("cosecha_sesion", None, errors)


def validate_production_command_guard_wiring(errors: list[str]) -> None:
    """Wiring declarativo: ver doctor_wiring.json → production_command_guard (FW-082, ADR-051)."""
    validate_wiring_feature("production_command_guard", None, errors)


MIN_CLAUDE_GUARD_ONFAILURE_VERSION = (2, 1, 295)


def _probe_guard_startup() -> str:
    """Ejecuta `guard_destructive_command.py` en seco con entrada `{}` para
    verificar que el intérprete y el script arrancan sin error."""
    guard_path = Path(__file__).resolve().parent / "guard_destructive_command.py"
    if not guard_path.is_file():
        return f"no existe {guard_path}"
    try:
        res = subprocess.run(
            [sys.executable, str(guard_path)],
            input="{}",
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return str(exc)
    if res.returncode != 0:
        detail = (res.stderr or res.stdout or "").strip()
        return f"exit {res.returncode}" + (f": {detail}" if detail else "")
    return ""


def validate_claude_guard_coverage(errors: list[str], warnings: list[str] | None = None) -> None:
    """Comprueba que el guardia de Claude se aplica realmente a Bash.

    El wiring textual confirma que el script aparece en settings, pero no que
    el matcher del grupo cubra la herramienta protegida. Esta validación lee la
    estructura JSON y conserva los errores de configuración como fallos del
    doctor, sin ejecutar hooks del proyecto.
    """
    path = ROOT / ".claude" / "settings.json"
    if not path.exists():
        return  # validate_wiring_feature emite el error de fichero faltante.
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        fail(errors, f".claude/settings.json no es JSON válido para validar cobertura Bash: {exc}")
        return
    hooks = data.get("hooks") if isinstance(data, dict) else None
    pre_tool_use = hooks.get("PreToolUse") if isinstance(hooks, dict) else None
    if not isinstance(pre_tool_use, list):
        fail(errors, ".claude/settings.json debe declarar hooks.PreToolUse para cubrir Bash")
        return

    covered = False
    has_onfailure_block = False
    invalid_matchers: list[str] = []
    for group in pre_tool_use:
        if not isinstance(group, dict) or not isinstance(group.get("hooks"), list):
            continue
        matcher = group.get("matcher")
        applies_to_bash = matcher is None
        if isinstance(matcher, str):
            try:
                applies_to_bash = re.search(matcher, "Bash") is not None
            except re.error:
                invalid_matchers.append(str(matcher))
                continue
        elif matcher is not None:
            invalid_matchers.append(repr(matcher))
            continue
        if not applies_to_bash:
            continue
        for hook in group["hooks"]:
            if not isinstance(hook, dict) or hook.get("type") != "command":
                continue
            command = hook.get("command")
            if isinstance(command, str) and "guard_destructive_command.py" in command:
                covered = True
                if hook.get("onFailure") == "block":
                    has_onfailure_block = True
                break
        if covered:
            break
    if invalid_matchers:
        fail(errors, f".claude/settings.json contiene matcher PreToolUse inválido: {', '.join(invalid_matchers)}")
    if not covered:
        fail(errors, ".claude/settings.json no tiene un hook PreToolUse del guardia aplicable a Bash")
        return
    if (ROOT / ".agent" / "templates" / "settings.json.base").exists() and not has_onfailure_block:
        fail(errors, '.claude/settings.json debe declarar "onFailure": "block" en el hook PreToolUse de guard_destructive_command.py')
    probe_error = _probe_guard_startup()
    if probe_error:
        fail(errors, f"guard_destructive_command.py no pudo arrancar en seco: {probe_error}")
    if warnings is not None:
        claude_ver = _harness_version("claude")
        if claude_ver is not None and claude_ver < MIN_CLAUDE_GUARD_ONFAILURE_VERSION:
            ver_txt = ".".join(str(p) for p in claude_ver)
            warn(
                warnings,
                f"Claude Code {ver_txt} < 2.1.295: versiones anteriores a 2.1.295 no aplican onFailure: block "
                "cuando el proceso del hook falla al arrancar (D19); actualiza Claude Code a >= 2.1.295",
            )


# --- 5.3.1 L1.5 (S7): límites duros por arnés --------------------------------
# Los límites duros están garantizados en Claude Code y Codex. En OpenCode y en
# Gemini/Antigravity no: el doctor lo dice (TL-FW-316). También avisa de
# extensiones de Claude Code anteriores a 2.1.295 en los editores (FW-364) y de
# lo que deja a Codex sin veredicto o sin reglas (FW-363, dictamen 3(f)).
_EDITOR_EXTENSION_DIRS = ("~/.vscode/extensions", "~/.antigravity/extensions",
                          "~/.antigravity-ide/extensions", "~/.cursor/extensions")
_CLAUDE_EXTENSION_RE = re.compile(r"^anthropic\.claude-code-(\d+)\.(\d+)\.(\d+)")
CODEX_RULES_TESTED_VERSION = "0.163.0"
_CODEX_FIX = "python3 .agent/scripts/ensure_codex_integration.py --primary-harness codex --native-only"


def _old_claude_extensions() -> list[str]:
    raw = os.environ.get("FRAMEWORK_EDITOR_EXTENSION_DIRS")
    old: list[str] = []
    for folder in (raw.split(os.pathsep) if raw else _EDITOR_EXTENSION_DIRS):
        base = Path(folder).expanduser()
        try:
            names = sorted(entry.name for entry in base.iterdir())
        except OSError:
            continue
        for name in names:
            match = _CLAUDE_EXTENSION_RE.match(name)
            if match and tuple(int(part) for part in match.groups()) < MIN_CLAUDE_GUARD_ONFAILURE_VERSION:
                old.append(str(base / name))
    return old


def _codex_trust(root: Path) -> tuple[bool | None, bool | None]:
    """(¿proyecto de confianza?, ¿hook `guard` confiado en /hooks?); None si no se sabe."""
    home = os.environ.get("CODEX_HOME")
    path = (Path(home) if home else Path.home() / ".codex") / "config.toml"
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8")) if tomllib else None
    except (OSError, ValueError):
        return None, None
    if not isinstance(data, dict):
        return None, None
    real = root.resolve()
    projects = data.get("projects") if isinstance(data.get("projects"), dict) else {}
    trusted = any(
        isinstance(cfg, dict) and cfg.get("trust_level") == "trusted"
        and (Path(key).expanduser() == real or Path(key).expanduser() in real.parents)
        for key, cfg in projects.items()
    )
    hooks = data.get("hooks") if isinstance(data.get("hooks"), dict) else {}
    state = hooks.get("state") if isinstance(hooks.get("state"), dict) else {}
    hook = any(str(key).startswith(f"{real}/.codex/hooks.json:pre_tool_use:") for key in state)
    return trusted, hook


def validate_hard_limits_hosts(mode: str, warnings: list[str]) -> None:
    old = _old_claude_extensions()
    if old:
        warn(warnings, "Claude Code en los editores por debajo de 2.1.295 (no aplica onFailure: block si el guard no "
                       f"arranca; FW-364): {', '.join(old)}; desinstálalas o actualízalas")
    if mode != "template":
        if (ROOT / "opencode.json").exists():
            warn(warnings, "OpenCode: los límites duros no están garantizados (el guard no se ejecuta; solo hay globs "
                           "ask en opencode.json, TL-FW-316); para operaciones con datos reales usa Claude Code o Codex")
        if (ROOT / "GEMINI.md").exists() or (ROOT / ".gemini").is_dir():
            warn(warnings, "Gemini/Antigravity: los límites duros no están garantizados (S7 de la 5.3.1); para "
                           "operaciones con datos reales usa Claude Code o Codex")
    codex_bin = shutil.which("codex")
    if not (ROOT / ".codex").is_dir() and (mode == "template" or not codex_bin):
        return
    import ensure_codex_integration as eci
    from sync_codex_hooks import sync_hooks
    try:
        if sync_hooks(ROOT, check=True)["changed"]:
            warn(warnings, f"Codex: .codex/hooks.json sin generar o desfasado: el hook guard no protege como espera el "
                           f"framework; ejecuta {_CODEX_FIX} y vuelve a confiar los hooks en /hooks")
        if eci._ensure_codex_rules(ROOT)[0]:
            warn(warnings, f"Codex: reglas de .codex/rules/ sin generar o desfasadas (núcleo irreversible); ejecuta {_CODEX_FIX}")
    except SystemExit as exc:
        warn(warnings, f"Codex: no se pudo comprobar la configuración de hooks y reglas: {exc}")
    if shutil.which("python3") is None:
        warn(warnings, "Codex: python3 no está en el PATH; los hooks del framework no arrancan (salida 127) y Codex "
                       "sigue sin veredicto del guard")
    if not codex_bin:
        return
    rules = ROOT / eci.CODEX_DESTRUCTIVE_RULES_REL
    if rules.is_file():
        import shlex
        example = next((spec["match"][0] for rule in eci.load_hard_limits(ROOT)["reglas"]
                        for spec in rule.get("codex", []) if spec.get("match")), "")
        try:
            proc = subprocess.run([codex_bin, "execpolicy", "check", "--rules", str(rules), "--", *shlex.split(example)],
                                  capture_output=True, text=True, timeout=15, stdin=subprocess.DEVNULL, check=False)
            decision = json.loads(proc.stdout or "{}").get("decision") if proc.returncode == 0 else None
        except (OSError, ValueError, subprocess.SubprocessError):
            decision = None
        if decision != "forbidden":
            warn(warnings, f"Codex: execpolicy check sobre {eci.CODEX_DESTRUCTIVE_RULES_REL} no da forbidden (probado "
                           f"con Codex {CODEX_RULES_TESTED_VERSION}); un ejemplo erróneo deja sin cargar todas sus reglas")
    trusted, hook = _codex_trust(ROOT)
    if trusted is False:
        warn(warnings, "Codex: el proyecto no es de confianza en ~/.codex/config.toml; las reglas de .codex/rules/ no cargan")
    if hook is False and (ROOT / ".codex" / "hooks.json").exists():
        warn(warnings, "Codex: el hook guard de .codex/hooks.json no consta como confiado; revísalo y confíalo en /hooks")


def validate_codex_integration_wiring(errors: list[str], mode: str | None = None) -> None:
    """Wiring declarativo de la integración Codex, sin tocar los adaptadores Claude."""
    validate_wiring_feature("codex_integration", None, errors, mode=mode)


def validate_ciclo_paralelo_wiring(errors: list[str]) -> None:
    """Wiring declarativo: ver doctor_wiring.json → ciclo_paralelo (4.2.0, FW-073)."""
    validate_wiring_feature("ciclo_paralelo", None, errors)


def validate_subagent_wait_lifecycle_wiring(errors: list[str]) -> None:
    """Wiring declarativo: ver doctor_wiring.json → subagent_wait_lifecycle."""
    validate_wiring_feature("subagent_wait_lifecycle", None, errors)


def validate_friccion_autonoma_wiring(errors: list[str]) -> None:
    """Wiring declarativo: ver doctor_wiring.json → friccion_autonoma (4.2.1, FW-075)."""
    validate_wiring_feature("friccion_autonoma", None, errors)


def validate_esfuerzo_por_tier_wiring(errors: list[str]) -> None:
    """Wiring declarativo: ver doctor_wiring.json → esfuerzo_por_tier (4.3.0, FW-077/078)."""
    validate_wiring_feature("esfuerzo_por_tier", None, errors)


def validate_cierre_de_turno_wiring(errors: list[str]) -> None:
    """Wiring declarativo: ver doctor_wiring.json → cierre_de_turno (5.3.0, FW-314)."""
    validate_wiring_feature("cierre_de_turno", None, errors)


def validate_politica_de_sesion_wiring(errors: list[str]) -> None:
    """Wiring declarativo: ver doctor_wiring.json → politica_de_sesion (5.3.0, FW-318)."""
    validate_wiring_feature("politica_de_sesion", None, errors)


def _harness_version(binary: str) -> tuple[int, ...] | None:
    """Versión instalada de un arnés, o None si no está o no responde."""
    path = shutil.which(binary)
    if not path:
        return None
    try:
        result = subprocess.run([path, "--version"], capture_output=True, text=True, errors="replace",
                                stdin=subprocess.DEVNULL, timeout=5, check=False)
    except (OSError, ValueError, subprocess.SubprocessError):
        return None
    match = re.search(r"(\d+)\.(\d+)\.(\d+)", result.stdout or result.stderr or "")
    return tuple(int(part) for part in match.groups()) if match else None


def validate_turn_close_hook(mode: str, errors: list[str], warnings: list[str]) -> None:
    """Hook `Stop` del cierre de turno (5.3.0 L4, ADR-074).

    Cableado declarativo (doctor_wiring.json → turn_close_hook), más dos avisos:
    la configuración activa de Claude Code todavía no lo engancha, y la versión
    instalada del arnés es anterior a la verificada (D19: se avisa de la
    degradación; el hook deja pasar si el evento no trae lo que espera)."""
    validate_wiring_feature("turn_close_hook", None, errors)
    settings = ROOT / ".claude" / "settings.json"
    if settings.is_file():
        try:
            active = settings.read_text(encoding="utf-8")
        except OSError:
            active = ""
        if "turn_close_hook.py" not in active:
            warn(warnings, ".claude/settings.json no engancha turn_close_hook.py en Stop: el cierre de turno no se "
                           "verifica en Claude Code; ejecutar `python3 .agent/scripts/ensure_claude_settings.py`")
    try:
        import turn_close_hook
    except Exception:  # noqa: BLE001 - el cableado ya informó de que falta o no carga
        return
    template = ROOT / ".agent" / "templates" / "codex_hooks.json.base"
    if template.is_file():
        try:
            groups = json.loads(template.read_text(encoding="utf-8"))["hooks"]["Stop"]
            stop = next(h for g in groups for h in g["hooks"] if str(h.get("command", "")).endswith(":stop"))
            problem = _stop_timeout_problem(stop)
        except (OSError, ValueError, KeyError, TypeError, StopIteration, AttributeError):
            problem = "no se pudo leer el hook stop de la plantilla"
        if problem:
            fail(errors, f".agent/templates/codex_hooks.json.base: {problem}")
    if turn_close_hook.hook_mode(ROOT) == "off":
        warn(warnings, "_project/15_environment_profile.md declara `turn_close_hook: off`: el hook Stop solo registra; "
                       "no bloquea un cierre de turno defectuoso, no corta bucles ni avisa de sesión larga (FW-350)")
    for binary, host, label in (("claude", "claude", "Claude Code"), ("codex", "codex", "Codex")):
        installed = _harness_version(binary)
        verified_text = turn_close_hook.VERIFIED_HARNESS[host]
        verified = tuple(int(part) for part in verified_text.split("."))
        if installed is not None and installed < verified:
            warn(warnings, f"{label} {'.'.join(map(str, installed))} es anterior a la versión en la que se verificó el "
                           f"hook Stop del cierre de turno ({verified_text}): puede no bloquear; si el evento no trae "
                           "el último mensaje, deja pasar y lo registra (D19)")


def validate_turn_close_cache(warnings: list[str]) -> None:
    """Aviso (solo lee el HOME) cuando la caché del hook acumula carpetas que nada limpia (5.3.0 L5b)."""
    try:
        import turn_close_hook

        folders = turn_close_hook.cache_folder_count()
        threshold = turn_close_hook.CACHE_WARN_FOLDERS
    except Exception:  # noqa: BLE001 - sin el módulo o sin HOME no hay nada que avisar
        return
    if folders > threshold:
        warn(warnings, f"~/.cache/framework-v4 tiene {folders} carpetas de proyectos (el hook deja una por ruta, "
                       "incluidos worktrees y temporales) y nada las limpia; revisa lo que borraría con "
                       "`python3 .agent/scripts/turn_close_hook.py --prune-cache` y aplícalo con `--apply`")


def validate_product_ci_shape(mode: str, errors: list[str]) -> None:
    """FW-076: el CI del template (ci_smoke.sh completo) no debe correr en un
    proyecto inicializado — la suite es del producto y el contrato de copia
    limpia es template-only; el resultado real fue 'All jobs have failed' en
    cada push. En initialized, el CI de salud del framework es el doctor."""
    if mode != "initialized":
        return
    workflows_dir = ROOT / ".github" / "workflows"
    if not workflows_dir.exists():
        return
    for wf in workflows_dir.glob("*.yml"):
        text = wf.read_text(encoding="utf-8", errors="ignore")
        # FW-226 (TL FW-206): solo el script de la PLANTILLA delata su CI; un
        # `scripts/ci_smoke.sh` propio del producto es legítimo.
        if re.search(r"(?m)^\s*(?:-\s*)?run:.*\.agent/scripts/ci_smoke\.sh", text):
            fail(
                errors,
                f"{wf.relative_to(ROOT)} ejecuta ci_smoke.sh (CI del template) en un proyecto "
                "inicializado — reemplazar por un job que corra solo framework_doctor.py (FW-076)",
            )


def validate_evidencia_trazabilidad_wiring(errors: list[str]) -> None:
    """Wiring declarativo: ver doctor_wiring.json → evidencia_y_trazabilidad (4.3.1, FW-079/080/081)."""
    validate_wiring_feature("evidencia_y_trazabilidad", None, errors)


def validate_wiring_spec_integrity(errors: list[str]) -> None:
    """Self-check del wiring declarativo (3.18.0): doctor_wiring.json parsea,
    tiene la forma esperada, y cada feature declarada es adjudicada por este
    doctor (nadie deja una entrada muerta en el spec)."""
    try:
        spec = _wiring_spec()
    except Exception as exc:  # noqa: BLE001
        fail(errors, f"doctor_wiring.json ilegible o inválido: {exc!r}")
        return
    features = spec.get("features")
    if not isinstance(features, dict) or not features:
        fail(errors, "doctor_wiring.json sin bloque 'features' (dict no vacío)")
        return
    doctor_src = Path(__file__).resolve().read_text(encoding="utf-8", errors="ignore")
    for feature in features:
        if f'validate_wiring_feature("{feature}"' not in doctor_src:
            fail(
                errors,
                f"feature '{feature}' declarada en doctor_wiring.json pero ningún "
                "validador la adjudica (entrada muerta — cablear o eliminar)",
            )


def validate_gate_exits_wiring(errors: list[str]) -> None:
    """Wiring declarativo: ver doctor_wiring.json → gate_exits (5.3.1 L3.6, §2)."""
    validate_wiring_feature("gate_exits", None, errors)


def validate_minimal_solution_core_wiring(errors: list[str]) -> None:
    """Wiring declarativo: ver doctor_wiring.json → minimal_solution_core."""
    validate_wiring_feature("minimal_solution_core", None, errors)


def validate_deployment_core_wiring(errors: list[str]) -> None:
    """Wiring declarativo: ver doctor_wiring.json → deployment_core (3.17.0, cierre L5)."""
    validate_wiring_feature("deployment_core", None, errors)


def validate_decision_translation_core_wiring(errors: list[str]) -> None:
    """Wiring declarativo: ver doctor_wiring.json → decision_translation_core."""
    validate_wiring_feature("decision_translation_core", None, errors)


# 3.12.0: foco activo. El mapa área→skill es cerrado y reusa skills core que
# ya valida validate_decision_translation_core_wiring (frontend_design,
# cross_platform_web, architecture_core, security_core, quality_testing_core);
# este validator solo exige que la skill referenciada exista en
# `.agent/skills/`, no que sea core (puede haber sido reemplazada por una
# externa con la misma ruta — fuera de scope de Fase 1, queda como anclaje
# de seguridad).
FOCUS_AREA_SKILL_MAP: dict[str, list[str]] = {
    "frontend": ["frontend_design", "cross_platform_web"],
    "ui": ["frontend_design", "cross_platform_web"],
    "ux": ["frontend_design", "cross_platform_web"],
    "backend": ["architecture_core", "security_core"],
    "data": ["architecture_core"],
    "seguridad": ["security_core"],
    "security": ["security_core"],
    "testing": ["quality_testing_core"],
    "qa": ["quality_testing_core"],
    "devops": ["deployment_core", "architecture_core"],
    "deploy": ["deployment_core", "architecture_core"],
    "infra": ["deployment_core", "architecture_core"],
}


def validate_focus_wiring(manifest: dict[str, Any], errors: list[str]) -> None:
    """Wiring declarativo (doctor_wiring.json → foco) + anclaje de seguridad:
    cada skill del mapa área→skill debe existir en `.agent/skills/`."""
    validate_wiring_feature("foco", manifest, errors)
    skills_dir = ROOT / ".agent" / "skills"
    for area, skill_stems in FOCUS_AREA_SKILL_MAP.items():
        for stem in skill_stems:
            if not (skills_dir / f"{stem}.md").exists():
                fail(
                    errors,
                    f"mapa foco: área '{area}' referencia skill '{stem}' inexistente "
                    f"en .agent/skills/ ({stem}.md faltante)",
                )


def validate_ciclo_wiring(manifest: dict[str, Any], errors: list[str]) -> None:
    """Wiring declarativo: ver doctor_wiring.json → ciclo (3.14.0, driver acotado).
    Incluye los needles anti-skip-de-gate como forbidden_regex."""
    validate_wiring_feature("ciclo", manifest, errors)


# 3.13.0: el conjunto canónico de los 5 gates de enforcement debe tener
# entrada en el registro `tests/gate_calibration.GATES`. Mismo patrón que
# las demás "wiring" validators: una capability sin calibrar es un build
# break. La cobertura se importa del registro (no se duplica la lista
# canónica aquí) para que el doctor y el arnés compartan verdad.


def validate_gate_calibration_coverage(errors: list[str]) -> None:
    """Cobertura del registro de calibración: los 5 gates canónicos
    (validate_closure, validate_transition, validate_acceptance,
    secret_scan, pre_commit) deben tener entrada good+bad en
    `tests/gate_calibration.GATES`. Si falta uno, FAIL accionable —
    ningún gate futuro se shipea sin calibrar."""
    sys.path.insert(0, str(ROOT / "tests"))
    try:
        import gate_calibration  # type: ignore  # noqa: E402
    except Exception as exc:  # noqa: BLE001
        fail(
            errors,
            f"no se pudo importar tests/gate_calibration.py (calibration registry): {exc!r}",
        )
        return
    if not hasattr(gate_calibration, "GATES") or not isinstance(gate_calibration.GATES, list):
        fail(errors, "tests/gate_calibration.GATES ausente o no es lista — registry corrupto")
        return
    if not hasattr(gate_calibration, "CANONICAL_GATE_NAMES"):
        fail(errors, "tests/gate_calibration.CANONICAL_GATE_NAMES ausente — el doctor no puede adjudicar cobertura")
        return
    canonical = set(gate_calibration.CANONICAL_GATE_NAMES)
    registered = {g.get("name") for g in gate_calibration.GATES if isinstance(g, dict)}
    missing = canonical - registered
    for name in sorted(missing):
        fail(
            errors,
            f"gate sin calibrar en tests/gate_calibration.GATES: '{name}' "
            f"(canónico: {sorted(canonical)})",
        )
    for entry in gate_calibration.GATES:
        if not isinstance(entry, dict):
            fail(errors, f"entrada inválida en GATES (no es dict): {entry!r}")
            continue
        for required_key in ("name", "fw", "good", "bad"):
            if required_key not in entry:
                fail(errors, f"gate '{entry.get('name', '?')}' sin clave obligatoria '{required_key}'")
        if not callable(entry.get("good")) and not (
            isinstance(entry.get("good"), list) and all(callable(x) for x in entry["good"])
        ):
            fail(errors, f"gate '{entry.get('name', '?')}'.good no es callable (o lista de callables)")
        # Simétrico con `good` (FW-152): un gate puede declarar varias fixtures
        # known-bad. El doctor aceptaba lista en `good` pero no en `bad`, así que
        # calibrar un patrón nuevo obligaba a elegir cuál de los dos casos
        # peligrosos se probaba — y el que quedara fuera dejaba de estar cubierto.
        if not callable(entry.get("bad")) and not (
            isinstance(entry.get("bad"), list) and all(callable(x) for x in entry["bad"])
        ):
            fail(errors, f"gate '{entry.get('name', '?')}'.bad no es callable (o lista de callables)")
    # Nota: gates extra en el registro (registered - canonical) no son FAIL
    # — el registro puede crecer. Cuando un gate nuevo se quiera sumar al
    # canónico, se actualiza CANONICAL_GATE_NAMES y este validator lo cubre
    # automáticamente.


def validate_builder_language_core_wiring(errors: list[str]) -> None:
    """Wiring declarativo: ver doctor_wiring.json → builder_language_core."""
    validate_wiring_feature("builder_language_core", None, errors)


def validate_preflight_review_wiring(manifest: dict[str, Any], errors: list[str]) -> None:
    """Wiring declarativo: ver doctor_wiring.json → preflight_review."""
    validate_wiring_feature("preflight_review", manifest, errors)


def validate_reviewer_security_wiring(errors: list[str]) -> None:
    """Wiring declarativo: ver doctor_wiring.json → reviewer_security."""
    validate_wiring_feature("reviewer_security", None, errors)


def validate_area_subagents_wiring(errors: list[str]) -> None:
    """Wiring declarativo: ver doctor_wiring.json → area_subagents (3.16.0)."""
    validate_wiring_feature("area_subagents", None, errors)


def _is_bare_hex(value: str) -> bool:
    """True si el valor es hexadecimal puro de 16 o más caracteres: una huella
    del motor en los artefactos de estado, nunca una credencial con formato."""
    return len(value) >= 16 and re.fullmatch(r"[0-9a-fA-F]+", value) is not None


def _key_parts(key: str) -> list[str]:
    """Partes de la clave por camelCase y separadores; una cifra final se quita
    (`DB_PASSWORD2` → password), salvo que la parte entera sea una palabra (`sha256`)."""
    key = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", key)
    parts = [part for part in re.split(r"[^0-9a-zñ]+", key.lower()) if part]
    known = _SECRET_KEY_WORDS | _DIGEST_KEY_WORDS
    return [part if part in known else (re.sub(r"\d+$", "", part) or part) for part in parts]


def _key_names_secret(key: str) -> bool:
    """¿Nombra la clave un secreto? Una ruta o un nombre de fichero no lo hace,
    ni una clave cuya ÚLTIMA parte nombra una huella o un dato público
    (`token_sha256`, `auth_id`; `URL_SIGNING_SECRET` sí es un secreto)."""
    if "/" in key or "\\" in key:
        return False
    ext = re.search(r"\.([A-Za-z0-9]{1,5})$", key)
    if ext and ext.group(1).lower() in _FILE_EXTENSIONS:
        return False
    parts = _key_parts(key)
    if not parts or parts[-1] in _DIGEST_KEY_WORDS:
        return False
    if any(part in _SECRET_KEY_WORDS or part.endswith(_SECRET_KEY_SUFFIXES) for part in parts):
        return True
    return any(pair in _SECRET_KEY_PAIRS for pair in zip(parts, parts[1:]))


def _high_entropy(value: str) -> bool:
    if not _SECRET_VALUE_RE.fullmatch(value):
        return False
    if not (any(ch.isdigit() for ch in value) and any(ch.isalpha() for ch in value)):
        return False
    counts = {ch: value.count(ch) for ch in set(value)}
    n = len(value)
    entropy = -sum(c / n * math.log2(c / n) for c in counts.values())
    return entropy >= 3.0 and not SECRET_ASSIGNMENT_PLACEHOLDER_RE.search(value) and not _PLACEHOLDER_VALUE_RE.search(value)


def _keyed_value_spans(line: str, *, require_entropy: bool, allow_bare_hex: bool = False):
    """Valores de la línea asignados a una clave que nombra un secreto (y los de
    `Bearer`). Devuelve `(inicio, fin, ventana)`; la ventana es el contexto de
    ±24 caracteres donde se busca un marcador de valor ficticio."""
    candidates = [
        (m.start("val"), m.end("val"), m.start(), m.end())
        for m in _KEYED_VALUE_RE.finditer(line)
        if _key_names_secret(m.group("key"))
    ]
    candidates += [(m.start("val"), m.end("val"), m.start(), m.end()) for m in _BEARER_RE.finditer(line)]
    for v_start, v_end, m_start, m_end in candidates:
        value = line[v_start:v_end]
        if require_entropy and not _high_entropy(value):
            continue
        if allow_bare_hex and _is_bare_hex(value):
            continue
        yield v_start, v_end, line[max(0, m_start - 24):m_end + 24]


# PA FW-045: el nombre de variable de una asignación tipo `secret = "..."`,
# usado para confirmar que el mismo identificador reaparece dentro de un
# assertNotIn(...) en el resto del archivo (fixture negativo explícito).
_ASSIGNMENT_NAME_RE = re.compile(r"([A-Za-z_][A-Za-z0-9_]*)\s*[:=]\s*['\"]?")


def _is_negative_assertion_fixture(text: str, line: str) -> bool:
    """PA FW-045: una asignación con forma de credencial (`secret = "..."`)
    cuyo identificador reaparece como argumento de `assertNotIn(...)` en el
    mismo archivo es un fixture explícitamente falso que demuestra que un
    valor NO se filtra — no una credencial real. Exige que el MISMO nombre de
    variable de la línea aparezca en el assertNotIn."""
    name_match = _ASSIGNMENT_NAME_RE.match(line.lstrip())
    if not name_match:
        return False
    name = name_match.group(1)
    return re.search(rf"assertNotIn\(\s*{re.escape(name)}\b", text) is not None


def _secret_match_tolerated(value: str, context: str) -> bool:
    """¿Es este match de SECRET_RE un valor ficticio declarado?

    Una credencial con formato (AKIA, ghp_, sk-ant, PEM, JWT, URL con
    contraseña) nunca se tolera. La asignación genérica (regla 1) solo con un
    marcador explícito de valor ficticio en `context` (ventana o clave)."""
    if _FORMATTED_SECRET_RE.search(value):
        return False
    return bool(SECRET_ASSIGNMENT_PLACEHOLDER_RE.search(context))


def _mask_secret_in_line(line: str, match_col_start: int, match_col_end: int) -> str:
    """Enmascara el valor coincidente y cualquier otro candidato de la línea
    (reglas de formato y asignación, valores con clave) para poder imprimir el
    contexto sin filtrar el secreto."""
    def mask_token(tok: str) -> str:
        if "=" in tok or ":" in tok:
            m = re.match(r"^([^=:]+[:=]\s*['\"]?)(.+?)(['\"]?)$", tok)
            if m:
                return f"{m.group(1)}***[REDACTADO]***{m.group(3)}"
        return f"{tok[:4]}…[REDACTADO]"

    spans = [(m.start(), m.end(), True) for m in SECRET_RE.finditer(line)]
    spans += [(s, e, False) for s, e, _ in _keyed_value_spans(line, require_entropy=False)]
    spans.append((match_col_start, match_col_end, False))
    merged: list[list] = []  # tramos solapados se fusionan (si no, el final saldría en claro)
    for start, end, by_rule in sorted(spans):
        if merged and start < merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
            merged[-1][2] = False
        else:
            merged.append([start, end, by_rule])
    out_parts: list[str] = []
    last = 0
    for start, end, by_rule in merged:
        out_parts.append(line[last:start])
        out_parts.append(mask_token(line[start:end]) if by_rule else "***[REDACTADO]***")
        last = end
    out_parts.append(line[last:])
    masked = "".join(out_parts).strip()
    return masked if len(masked) <= 200 else masked[:200] + "…"


def _scan_json_secret(node: Any, key: str = "", allow_bare_hex: bool = False) -> str | None:
    """Recorre un JSON y devuelve la clave del primer valor que parezca secreto.

    Un valor se marca si casa con SECRET_RE (formato o asignación dentro de la
    cadena) o si su clave nombra un secreto y el valor tiene alta entropía. El
    marcador de valor ficticio se busca solo en `clave + valor` del propio
    campo, de modo que un campo vecino no neutraliza el secreto. Con
    `allow_bare_hex`, un valor hexadecimal puro no se marca por la clave
    (artefactos de estado del motor). Devuelve la clave o None."""
    if isinstance(node, dict):
        for k, v in node.items():
            hit = _scan_json_secret(v, str(k), allow_bare_hex)
            if hit is not None:
                return hit
    elif isinstance(node, list):
        for item in node:
            hit = _scan_json_secret(item, key, allow_bare_hex)
            if hit is not None:
                return hit
    elif isinstance(node, str):
        context = f"{key} {node}"
        label = key or "(sin clave)"
        for match in SECRET_RE.finditer(node):
            if not _secret_match_tolerated(match.group(), context):
                return label
        if (
            key
            and _key_names_secret(key)
            and _high_entropy(node)
            and not (allow_bare_hex and _is_bare_hex(node))
            and not SECRET_ASSIGNMENT_PLACEHOLDER_RE.search(context)
        ):
            return label
        # El texto del valor también se mira con la regla por clave y `Bearer`
        # (`"Authorization": "Bearer …"`, `"--api-key=…"`, `"export TOKEN=…"`).
        for _start, _end, window in _keyed_value_spans(node, require_entropy=True, allow_bare_hex=allow_bare_hex):
            if not SECRET_ASSIGNMENT_PLACEHOLDER_RE.search(window):
                return label
    return None


def _first_secret_in_text(text: str, *, allow_bare_hex: bool, in_tests_dir: bool) -> tuple[int, str] | None:
    """Primer posible secreto de un texto: `(línea, línea enmascarada)` o None.
    Una línea que acaba en `clave =` o `clave:` con una clave que nombra un
    secreto se mira unida a la siguiente si esta empieza por comillas: la
    asignación partida en dos líneas (Prettier, YAML)."""
    lines = text.splitlines()
    for idx, line in enumerate(lines):
        probes = [(idx + 1, line)]
        tail = _SPLIT_ASSIGNMENT_RE.search(line)
        if idx + 1 < len(lines) and tail and _key_names_secret(tail.group(1)) and lines[idx + 1].lstrip()[:1] in ("'", '"'):
            probes.append((idx + 2, line.rstrip() + " " + lines[idx + 1].lstrip()))
        for line_no, probe in probes:
            masked = _secret_in_line(text, probe, allow_bare_hex=allow_bare_hex, in_tests_dir=in_tests_dir)
            if masked is not None:
                return line_no, masked
    return None


_SPLIT_ASSIGNMENT_RE = re.compile(r"([^\W\d][\w.\-]{0,63})['\"]?\]?\s*[:=]\s*$")


def _secret_in_line(text: str, line: str, *, allow_bare_hex: bool, in_tests_dir: bool) -> str | None:
    for match in SECRET_RE.finditer(line):
        window = line[max(0, match.start() - 24):match.end() + 24]
        if _secret_match_tolerated(match.group(), window):
            continue
        if in_tests_dir and _is_negative_assertion_fixture(text, line):
            continue
        return _mask_secret_in_line(line, match.start(), match.end())
    for start, end, window in _keyed_value_spans(line, require_entropy=True, allow_bare_hex=allow_bare_hex):
        if SECRET_ASSIGNMENT_PLACEHOLDER_RE.search(window):
            continue
        if in_tests_dir and _is_negative_assertion_fixture(text, line):
            continue
        return _mask_secret_in_line(line, start, end)
    return None


def _git_untracked_unignored_set() -> set[str]:
    """Rutas untracked no ignoradas por `.gitignore` (`git ls-files --others --exclude-standard -z`)."""
    try:
        out = subprocess.run(
            ["git", "ls-files", "--others", "--exclude-standard", "-z"],
            cwd=ROOT, capture_output=True, timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return set()
    if out.returncode != 0:
        return set()
    return {p for p in out.stdout.decode("utf-8", "ignore").split("\0") if p}


def _scannable_paths() -> list:
    """Files to scan for secrets. Prefer `git ls-files` plus untracked non-ignored
    files (`git ls-files --others --exclude-standard`) so a new file is scanned
    both before and during commit, while respecting `.gitignore` (FW-042, FW-339).
    Fall back to rglob when there is no git."""
    tracked = _git_tracked_set()
    if tracked is not None:
        combined = sorted(tracked | _git_untracked_unignored_set())
        return [ROOT / n for n in combined]
    return list(ROOT.rglob("*"))


def _read_index_blob(path) -> bytes | None:
    """Contenido del blob del ÍNDICE para `path` (git show :ruta), o None si
    no está en el índice o git falla. Modo staged del escáner (C3)."""
    try:
        out = subprocess.run(
            ["git", "show", f":0:{rel(path)}"],  # `:0:` explícito: una ruta `0:x` no es una etapa
            cwd=ROOT, capture_output=True, timeout=10, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout if out.returncode == 0 else None


def _gitleaks_findings(warnings: list[str]) -> list[str] | None:
    """D5: hallazgos de `gitleaks git --staged` (sin el valor), [] si no hay o
    None si gitleaks no está o no se pudo ejecutar (entonces decide el escáner
    propio, con un aviso)."""
    exe = shutil.which("gitleaks")
    if not exe:
        return None
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        report = Path(tmp) / "gitleaks.json"
        cmd = [exe, "git", "--staged", "--redact", "--no-banner", "--exit-code", str(_GITLEAKS_LEAK_RC),
               "--report-format", "json", "--report-path", str(report), str(ROOT)]
        try:
            out = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, errors="replace", timeout=60)
        except (OSError, subprocess.SubprocessError) as exc:
            warn(warnings, f"gitleaks instalado pero no se pudo ejecutar ({type(exc).__name__}); decide el escáner propio")
            return None
        if out.returncode == 0:
            return []
        if out.returncode != _GITLEAKS_LEAK_RC:
            detail = (out.stderr or out.stdout).strip().splitlines()[-1:] or [f"salida {out.returncode}"]
            warn(warnings, f"gitleaks instalado pero no se pudo ejecutar (salida {out.returncode}: {detail[0][:120]}); "
                           "decide el escáner propio. gitleaks 8.19 o superior trae el subcomando `git`")
            return None
        try:
            data = json.loads(report.read_text(encoding="utf-8"))
            items = [f"{d['File']}:{d['StartLine']} (regla {d['RuleID']})" for d in data]
        except (OSError, ValueError, TypeError, KeyError):
            items = []
    if not items:
        return ["Posible secreto según gitleaks en lo preparado (no se pudo leer su informe). "
                "Revísalo con: gitleaks git --staged --redact"]
    return [f"Posible secreto según gitleaks en {item} — valor redactado. {_SECRET_RULE_TEXT}"
            for item in dict.fromkeys(items)]


def _staged_paths() -> list | None:
    """Rutas que el commit cambia (sin borrados), leídas del índice activo
    (respeta el `GIT_INDEX_FILE` de `git commit -a` o `git commit <ruta>`), o
    None si git falla."""
    try:
        out = subprocess.run(
            ["git", "diff", "--cached", "--name-only", "--no-renames", "--diff-filter=d", "-z"],
            cwd=ROOT, capture_output=True, timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if out.returncode != 0:
        return None
    return [ROOT / p for p in out.stdout.decode("utf-8", "surrogateescape").split("\0") if p]


def scan_potential_secrets(errors: list[str], staged: bool = False, warnings: list[str] | None = None) -> None:
    warnings = [] if warnings is None else warnings
    ignored_dirs = {".git", "node_modules", ".venv", "venv", "__pycache__", "dist", "build", ".next", ".nuxt", ".pytest_cache", ".mypy_cache", ".ruff_cache"}
    # Files that legitimately contain the patterns by design: this script (the regexes),
    # the secret-scan test (credential fixtures), the gate calibration registry
    # (calibration fixtures that exercise the scanner with PEM prose), and the field
    # reports (record framework friction and quote credential patterns). Both the
    # downstream `_feedback/field_report.md` and the template-internal
    # `.agent/docs/field_report.md` are exempt (FW-051).
    self_ref_files = {
        ".agent/scripts/framework_doctor.py",
        "tests/test_doctor_secret_scan.py",
        "tests/gate_calibration.py",
        "_feedback/field_report.md",
        ".agent/docs/field_report.md",
    }
    # State artifacts where the engine writes SHA-256 fingerprints (APPROVE_RECEIPT
    # basis, review diff_hash): un valor hexadecimal puro no se marca por su clave.
    # Las reglas de formato y de asignación siguen activas: estos ficheros también
    # acumulan prosa escrita por el agente, y el doctor es el gate de secretos de
    # /publicar-github.
    state_artifact_files = {
        # FW-226 (TL FW-204): la proyección de sesión repite huellas del estado.
        "_project/00_session_brief.json",
        "_project/03_project_state.json",
        "_project/state_log.jsonl",
        "_project/05_test_history.md",
        # ADR-139/FW-201: provenance de shrink_state_log_snapshots.py.
        "_project/state_log_rebase.json",
    }
    # FW-185 y FW-226: stores de huellas y checkpoints con la misma forma hex.
    state_artifact_dirs = (
        "_project/approval_snapshots/",
        "_project/codex_execution/",
        "_project/migrations/",
    )
    # Local-only files the doctor itself requires to be gitignored
    # (validate_local_settings_safety los mira aparte).
    local_only_files = {".claude/settings.local.json"}
    # 5.3.1 L2.2 (PA-FW-005, TL-FW-315): en el commit se escanea solo lo que el
    # commit cambia, leído del índice; un fichero sin preparar no entra en el
    # commit y no se escanea. Si git falla, se escanea el conjunto de fuera del
    # commit (falla hacia escanear más).
    index_only = False
    paths = None
    if staged:
        paths = _staged_paths()
        index_only = paths is not None
        found = _gitleaks_findings(warnings)
        for message in found or []:
            fail(errors, message)
    if paths is None:
        paths = _scannable_paths()
    for path in paths:
        if any(part in ignored_dirs for part in path.relative_to(ROOT).parts):  # relativas: no la ruta del proyecto
            continue
        if not index_only and not path.is_file():
            continue
        if path.name in {".DS_Store"} or path.suffix in {".pyc", ".png", ".jpg", ".jpeg", ".gif", ".ico", ".pdf"}:
            continue
        rel_path = rel(path)
        if rel_path in self_ref_files or rel_path in local_only_files:
            continue
        allow_bare_hex = rel_path in state_artifact_files or rel_path.startswith(state_artifact_dirs)
        # .env.example is explicitly designed to show secret variable NAMES without real values
        if path.name in {".env.example", ".env.sample", ".env.template"}:
            continue
        # FW-040 guards: skip oversized blobs and binaries before reading them
        # whole — they are not credential-bearing source and reading them hangs
        # the scan (and trains --no-verify).
        # C3: el commit lleva los blobs del ÍNDICE; en el modo preparado se lee
        # `git show :ruta` y, si la ruta salió del índice, nunca el árbol.
        raw_opt = _read_index_blob(path) if staged else None
        if raw_opt is not None:
            raw = raw_opt
        elif index_only:
            continue
        else:
            try:
                if path.stat().st_size > SECRET_SCAN_MAX_BYTES:
                    continue
                raw = path.read_bytes()
            except OSError:
                continue
        if len(raw) > SECRET_SCAN_MAX_BYTES or _is_probably_binary(raw[:4096]):
            continue
        text = raw.decode("utf-8", errors="ignore")

        # JSON: escanear VALOR por VALOR, con la clave como único contexto del
        # marcador ficticio (un campo vecino no neutraliza un secreto).
        if path.suffix == ".json":
            try:
                hit = _scan_json_secret(json.loads(text), allow_bare_hex=allow_bare_hex)
            except (json.JSONDecodeError, ValueError):
                hit = None  # JSON inválido (p. ej. template) → cae al escaneo por línea
            else:
                if hit is not None:
                    fail(errors, f"Posible secreto en {rel_path}:1 — clave `{hit}`, valor redactado. {_SECRET_RULE_TEXT}")
                continue

        found_line = _first_secret_in_text(
            text, allow_bare_hex=allow_bare_hex, in_tests_dir="tests" in Path(rel_path).parts
        )
        if found_line is not None:
            line_no, masked = found_line
            fail(errors, f"Posible secreto en {rel_path}:{line_no} — línea: `{masked}`. {_SECRET_RULE_TEXT}")


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    json_output = "--json" in argv
    unknown = [arg for arg in argv if arg != "--json"]
    if unknown:
        print(f"Uso: {Path(__file__).name} [--json]", file=sys.stderr)
        return 2
    errors: list[str] = []
    warnings: list[str] = []

    manifest_path = ROOT / ".agent" / "workflows" / "manifest.json"
    state_path = ROOT / "_project" / "03_project_state.json"
    manifest = load_json(manifest_path, errors)
    state = load_json(state_path, errors)

    validate_python_version(errors, warnings)
    expected_claude = validate_manifest(manifest, errors, warnings)
    validate_progressive_context_manifest(manifest, errors)
    validate_claude_wrappers(expected_claude, errors, warnings)
    validate_claude_wrapper_contracts(manifest, errors)
    validate_claude_agents(errors, warnings)
    validate_claude_rules(manifest, errors, warnings)
    validate_opencode_wrappers(expected_claude, errors, warnings)
    validate_opencode_skills(errors, warnings)
    validate_codex_wrappers(manifest, errors, warnings)
    validate_adapter_surface_collision(errors, warnings)
    validate_claude_codex_parity(manifest, errors)
    validate_no_menu_bloat(errors, warnings)
    validate_manifest_trigger_descriptions(manifest, errors)
    mode = validate_state(state, errors, warnings)
    validate_framework_version_consistency(mode, state, errors, warnings)
    validate_placeholders(mode, errors, warnings)
    validate_secondary_placeholders(mode, warnings)
    validate_dependencies_requirements_alignment(mode, state if isinstance(state, dict) else {}, warnings)
    validate_dependency_lockfile_presence(mode, state if isinstance(state, dict) else {}, warnings)
    validate_product_tests_read_state(mode, warnings)
    validate_template_cleanliness(mode, errors)
    validate_technical_plan_artifacts(mode, errors)
    validate_core_tier_consistency(mode, state if isinstance(state, dict) else {}, warnings)
    validate_core_markdown_consistency(mode, state if isinstance(state, dict) else {}, warnings)
    validate_phase_consistency(mode, state if isinstance(state, dict) else {}, warnings)
    validate_domain_sweep_receipts(state if isinstance(state, dict) else {}, mode, errors)
    validate_expert_audit_receipts(state if isinstance(state, dict) else {}, mode, errors)
    validate_state_narrative_consistency(mode, state if isinstance(state, dict) else {}, warnings)
    validate_contracts_sync(mode, warnings)
    validate_openapi_input_inheritance(mode, warnings)
    validate_api_contract_runtime_dependencies(mode, warnings)
    validate_invariant_term_drift(mode, warnings)
    validate_verificar_ejecutable_wiring(manifest, errors)
    validate_flow_guidance(errors)
    validate_workflow_step_numbering(errors)
    validate_flow_output_contract(mode, errors)
    validate_enriquecer_ticket_adr_gate(errors)
    validate_publicar_github_workflow_gates(errors)
    validate_initialization_workflow_version_references(errors)
    validate_phase0_module_specs_policy(errors)
    validate_clarification_schema_policy(errors)
    validate_clarification_entries(mode, warnings)
    validate_arrancar_post_interruption_wiring(errors)
    validate_reconciliar_workflow_wiring(manifest, errors)
    validate_compact_output_policy(errors)
    validate_readme_version_parity(mode, errors)
    validate_guardrails_version_parity(mode, errors)
    validate_evolution_policy(errors)
    validate_field_report_mechanism(mode, errors)
    validate_field_report_staleness(mode, manifest, warnings)
    validate_native_compaction_policy(errors)
    validate_gitignore_template(errors)
    validate_no_headless_claude_wiring(errors)
    validate_mcp_template(errors)
    if _codex_primary_harness():
        mirror_errors: list[str] = []
        validate_active_mcp_config(mirror_errors)
        for mirror_error in mirror_errors:
            warn(warnings, f"Espejo MCP opcional: {mirror_error}; no afecta Codex-native")
    else:
        validate_active_mcp_config(errors)
    validate_post_edit_hook(errors, warnings)
    validate_codex_integration(mode, state if isinstance(state, dict) else {}, errors, warnings)
    validate_lessons_template_cleanliness(mode, errors)
    validate_lessons_digest_template_cleanliness(mode, errors)
    validate_state_extensions_template_cleanliness(mode, errors)
    validate_state_log_template_cleanliness(mode, errors)
    validate_state_log_chain(mode, errors, warnings)
    validate_state_log_size(mode, warnings)
    validate_project_weight(mode, warnings)
    validate_feedback_template_cleanliness(mode, errors)
    validate_imported_skills(errors)
    validate_checkpoint_template_wiring(errors)
    validate_proactive_experts_wiring(errors)
    validate_antigravity_menu_duplicado_wiring(errors)
    validate_stack_escalado_wiring(errors)
    validate_gate_failure_semantics(errors)
    validate_cosecha_sesion_wiring(errors)
    validate_modo_mantenimiento_wiring(errors)
    validate_production_command_guard_wiring(errors)
    validate_claude_guard_coverage(errors, warnings)
    validate_hard_limits_hosts(mode, warnings)
    validate_codex_integration_wiring(errors, mode=mode)
    validate_ciclo_paralelo_wiring(errors)
    validate_subagent_wait_lifecycle_wiring(errors)
    validate_friccion_autonoma_wiring(errors)
    validate_esfuerzo_por_tier_wiring(errors)
    validate_cierre_de_turno_wiring(errors)
    validate_politica_de_sesion_wiring(errors)
    validate_turn_close_hook(mode, errors, warnings)
    validate_turn_close_cache(warnings)
    validate_product_ci_shape(mode, errors)
    validate_evidencia_trazabilidad_wiring(errors)
    validate_wiring_spec_integrity(errors)
    validate_root_docs_freshness(manifest, errors)
    validate_root_docs_contract(mode, errors, warnings)
    validate_host_conflicts(mode, warnings)
    validate_fw_registry(mode, warnings, errors=errors)
    validate_minimal_solution_core_wiring(errors)
    validate_deployment_core_wiring(errors)
    validate_decision_translation_core_wiring(errors)
    validate_gate_exits_wiring(errors)
    validate_focus_wiring(manifest, errors)
    validate_ciclo_wiring(manifest, errors)
    validate_gate_calibration_coverage(errors)
    validate_builder_language_core_wiring(errors)
    validate_preflight_review_wiring(manifest, errors)
    validate_reviewer_security_wiring(errors)
    validate_area_subagents_wiring(errors)
    validate_precommit_hook_installed(warnings)
    validate_prepush_hook_installed(warnings)
    validate_invariant_acceptance_parity(mode, state if isinstance(state, dict) else {}, warnings)
    validate_module_acceptance_status_alignment(mode, state if isinstance(state, dict) else {}, warnings)
    validate_bugs_fixed_but_open(state, warnings)
    validate_local_settings_safety(errors)
    validate_guardrails_startup_source(errors)
    validate_guardrails_anchors(errors)
    validate_acceptance_gate_wiring(errors)
    validate_closure_gate_wiring(errors)
    validate_transition_gate_wiring(errors)
    validate_adapters(errors, warnings)
    validate_code_graph_adapter_wiring(errors)
    validate_cambio_seguro_code_graph_wiring(errors)
    validate_ci_workflow(errors)
    validate_git_skip_worktree_bits(errors)
    validate_workflow_rendered_token_budget(errors)
    warn_workflow_size_threshold(warnings)

    core_skills = [
        ".agent/skills/agile_product.md",
        ".agent/skills/architecture_core.md",
        ".agent/skills/security_core.md",
        ".agent/skills/quality_testing_core.md",
        ".agent/skills/delivery_core.md",
        ".agent/skills/minimal_solution_core.md",
        ".agent/skills/builder_language_core.md",
    ]
    for rel in core_skills:
        if not (ROOT / rel).exists():
            fail(errors, f"Skill core faltante: {rel}")

    onboarding_files = [
        "_project/core.md",
        "_project/00_session_brief.json",
        "_project/01_product_requirements.md",
        "_project/20_builder_profile.md",
        "_project/21_knowledge_map.md",
        ".agent/schemas/project_state.schema.json",
        "llms.txt",
    ]
    for rel in onboarding_files:
        if not (ROOT / rel).exists():
            fail(errors, f"Artefacto de onboarding faltante: {rel}")

    llms = ROOT / "llms.txt"
    if llms.exists():
        llms_text = llms.read_text(encoding="utf-8")
        for needle in ["CLAUDE.md", "_project/03_project_state.json", ".agent/workflows/manifest.json"]:
            if needle not in llms_text:
                fail(errors, f"llms.txt no contiene referencia obligatoria: {needle}")

    readme = ROOT / "README.md"
    if not readme.exists():
        fail(errors, "README.md faltante")
    elif "/diseñar" not in readme.read_text(encoding="utf-8"):
        fail(errors, "README.md no documenta el flujo de descubrimiento (/diseñar)")

    guardrails = ROOT / "GUARDRAILS_MASTER.md"
    if not guardrails.exists():
        fail(errors, "GUARDRAILS_MASTER.md faltante — capa constitucional del Protocolo de 3 Fases")
    # El literal "Protocolo de 3 Fases" lo cubre validate_guardrails_anchors
    # (GUARDRAILS_ANCHORS); validarlo aquí también emitiría dos errores por la
    # misma causa.

    invariants = ROOT / "_project" / "03_invariants.md"
    if mode == "initialized" and not invariants.exists():
        warn(warnings, "_project/03_invariants.md faltante — crear con /nuevo-proyecto o /migrar-proyecto para proteger reglas de negocio y flujos de datos")

    gitignore = ROOT / ".gitignore"
    if not gitignore.exists():
        fail(errors, ".gitignore faltante")
    else:
        gitignore_text = gitignore.read_text(encoding="utf-8")
        for pattern in [".env", ".DS_Store", ".claude/settings.local.json"]:
            if pattern not in gitignore_text:
                fail(errors, f".gitignore no cubre {pattern}")

    gitattributes = ROOT / ".gitattributes"
    if not gitattributes.exists() or "* text=auto" not in gitattributes.read_text(encoding="utf-8"):
        fail(errors, ".gitattributes faltante o sin '* text=auto'")

    validate_hygiene(warnings)
    # Modo staged en pre-commit (C3): el hook exporta FRAMEWORK_PRECOMMIT=1
    # y el escáner lee los blobs del índice — lo que el commit va a llevar.
    scan_potential_secrets(errors, staged=os.environ.get("FRAMEWORK_PRECOMMIT") == "1", warnings=warnings)

    producto_errors, sync_errors = partition_errors(errors)
    summary = summarize_result(
        tool="framework_doctor",
        exit_code=(1 if producto_errors else 2) if errors else 0,
        parsed={
            "status": "fail" if errors else "pass",
            "failures": producto_errors + sync_errors,
            "warnings": warnings,
        },
        evidence_ref="framework_doctor --json",
    )

    if errors:
        if json_output:
            print(json.dumps({
                "ok": False,
                "status": "fail",
                "mode": mode,
                "errors": producto_errors,
                "sync_errors": sync_errors,
                "warnings": warnings,
                "summary": summary,
            }, ensure_ascii=False, indent=2))
            return 1 if producto_errors else 2
        print("Framework doctor: FAIL")
        for error in producto_errors:
            print(f"- ERROR: {error}")
        if sync_errors:
            print(f"Sincronización de menú/wrappers ({SYNC_REGEN_HINT}):")
            for error in sync_errors:
                print(f"- ERROR-SYNC: {error}")
        for warning in warnings:
            print(f"- WARN: {warning}")
        # Exit 2 = SOLO drift de sincronización (regenerable, ajeno al código
        # del producto); exit 1 = hay errores de producto/plantilla. Un gate de
        # cierre de módulo puede así distinguir sin juicio manual (TL FW-004).
        return 1 if producto_errors else 2

    if json_output:
        print(json.dumps({
            "ok": True,
            "status": "pass",
            "mode": mode,
            "errors": [],
            "sync_errors": [],
            "warnings": warnings,
            "summary": summary,
        }, ensure_ascii=False, indent=2))
        return 0
    print("Framework doctor: OK")
    if warnings:
        for warning in warnings:
            print(f"- WARN: {warning}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
