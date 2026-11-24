#!/usr/bin/env python3
"""Detector de stub-presentado-como-implementación y de fail-open silencioso.

Origen (FW-145, cosecha Example Service 2026-08-26): el evidence gate de
`/cierre-modulo` verifica que la ruta citada como evidencia EXISTA (FW-127
anti-fabricación), no que el código haga algo. Un modelo rápido que cablea el
dato para que el flujo pase produce un fichero real, citable y verde:
`Extracted requirement 1` como extracción de requisitos, un evaluador que da
PASS salvo un literal, un `fit_score = 50.0` fijo, un contacto
`candidato@example.com` en el documento generado. Su hermano es el fail-open
silencioso — `except Exception: return {}` — que convierte una dependencia
caída en un 200 OK y borra la señal de que nada funciona.

Alcance deliberadamente estrecho, para mantener el falso positivo bajo
(política de evolución: una regla mecánica nueva exige recurrencia + FP bajo):

- Solo ficheros FUENTE. Tests, fixtures, docs, ejemplos y `_project/` quedan
  fuera: ahí un dato de mentira es el trabajo, no el defecto.
- **`raise NotImplementedError` NO se marca** (FW-161). Falla ruidosamente: es
  lo contrario de un stub y exactamente lo que RN-000 pide. Marcarlo daba 12
  falsos positivos solo en Example Lab —una interfaz legada documentada con
  ADR— y golpearía a cualquier proyecto con clases abstractas.
- Solo los ficheros del módulo: los citados como evidencia de sus ACs más los
  modificados en el working tree. No audita el repo entero.
- Salida explícita por línea: `stub-ok: <razón de >= 10 caracteres>` en la
  misma línea o en la anterior. El caso legítimo se declara, no se esconde.

Uso:
    python3 .agent/scripts/validate_no_stubs.py "<module_id>"
    python3 .agent/scripts/validate_no_stubs.py --paths src/a.py src/b.py
Códigos de salida: 0 = limpio, 1 = hallazgos, 2 = error de uso.
"""

from __future__ import annotations

import ast
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import validate_acceptance as _va  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
ACCEPTANCE = ROOT / "_project" / "08_module_acceptance.md"

SOURCE_SUFFIXES = {
    ".py", ".js", ".jsx", ".ts", ".tsx", ".go", ".rb", ".rs", ".java", ".kt",
    ".php", ".cs", ".swift", ".sh", ".sql",
}
# Un dato falso es legítimo en un test, un fixture, un ejemplo o un documento.
EXCLUDED_PARTS = {
    "tests", "test", "__tests__", "spec", "specs", "fixtures", "fixture",
    "mocks", "__mocks__", "examples", "example", "samples", "seeds",
    "migrations", "node_modules", ".venv", "venv", "__pycache__", "dist",
    "build", ".git", "_project", "_feedback", "_scratch", "docs", ".agent",
    # FW-161: un worktree es el MISMO código contado dos o tres veces. En
    # Example Desktop inflaba los hallazgos de 19 a 54, y un conteo inflado es la
    # forma más rápida de que un gate se ignore.
    ".claude", "worktrees",
    # `.agents/` (wrappers de Codex) hospeda las skills instaladas de terceros:
    # código que el proyecto NO posee y no puede arreglar. `.agent` ya estaba
    # excluido; el plural se coló y aportaba 11 de los 15 hallazgos de
    # Example Service, todos en skills de diseño ajenas.
    ".agents",
}
EXCLUDED_NAME_RE = re.compile(r"(?i)(^|[._-])(test|spec|mock|fixture|conftest)([._-]|$)")

OPT_OUT_RE = re.compile(r"stub-ok:\s*(?P<razon>.{10,})")

# --- Dato fabricado -------------------------------------------------------
# Cada patrón describe algo que NO puede quedarse en producción. Se buscan como
# literales de código, no como palabras sueltas en prosa.
UNFINISHED_COMMENT_RE = re.compile(r"(?i)\b(TODO|FIXME|HACK|XXX)\b\s*[:(]")
FABRICATED_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("dominio de ejemplo", re.compile(r"(?i)@example\.(com|org|net)\b")),
    ("host de ejemplo", re.compile(r"(?i)\bexample\.(com|org)\b")),
    ("marcador de trabajo sin terminar", UNFINISHED_COMMENT_RE),
    ("literal placeholder", re.compile(r"(?i)[\"'`][^\"'`]*\b(placeholder|lorem ipsum|changeme|change-me|your[-_ ]api[-_ ]key|tu[-_ ]clave|dummy|foo ?bar)\b[^\"'`]*[\"'`]")),
    ("nombre de relleno", re.compile(r"(?i)[\"'`]\s*(john doe|jane doe|jane roe|nombre real|nombre completo|full name|usuario de ejemplo)\s*[\"'`]")),
]

# --- Fail-open silencioso -------------------------------------------------
# Una captura ancha cuyo cuerpo devuelve vacío o no hace nada: el error de la
# dependencia deja de existir y el flujo continúa como si hubiera funcionado.
PY_BROAD_EXCEPT_RE = re.compile(r"^\s*except\s*(\(?\s*(Exception|BaseException)[^)]*\)?\s*(as\s+\w+)?\s*)?:\s*$")
JS_CATCH_RE = re.compile(r"\bcatch\s*(\([^)]*\))?\s*\{\s*$")
EMPTY_RETURN_RE = re.compile(
    r"^\s*(pass|return|return\s+(None|null|\{\}|\[\]|\"\"|''|``|0|False|false|True|true)\s*;?)\s*$"
)
# FW-152: una llamada de logging no propaga nada — es ruido dentro del manejador,
# no manejo del error. Se ignora al juzgar si el handler es silencioso.
LOG_CALL_RE = re.compile(
    r"^\s*(?:self\.)?(?:logger|logging|log|_log|LOG|LOGGER)\s*\.\s*\w+\s*\("
    r"|^\s*print\s*\("
    r"|^\s*console\s*\.\s*\w+\s*\("
)


# FW-226 (Example Tool FW-223): `placeholder` como NOMBRE de atributo o propiedad
# (`<input placeholder="Buscar">`, `setAttribute('placeholder', …)`,
# `el.placeholder = …`, `placeholder:text-gray` de Tailwind, `::placeholder`) es
# UI legítima, no un dato de relleno. Se retira ese uso antes de aplicar el
# patrón "literal placeholder"; un VALOR `"placeholder"` sigue marcándose.
ATTR_PLACEHOLDER_RE = re.compile(
    r"(?i)\bplaceholder(?=\s*[=:])"
    r"|\bplaceholder(?=-[A-Za-z])"
    r"|(?<=Attribute\()\s*[\"']placeholder[\"']"
    r"|(?<=\.)placeholder\b"
    r"|(?<=::)placeholder\b"
)

# PA-FW-006 (5.3.1 L3.4): el marcador se busca en cualquier caja solo dentro de
# un comentario; en código y cadenas, solo en mayúsculas («comparar todo: falta…»).
UNFINISHED_LABEL = "marcador de trabajo sin terminar"
UNFINISHED_CODE_RE = re.compile(r"\b(TODO|FIXME|HACK|XXX)\b\s*[:(]")
_SLASH_COMMENT = r"(?<!:)//|/\*"
_COMMENT_START = {".py": "#", ".rb": "#", ".sh": "#", ".sql": r"--|/\*", ".php": _SLASH_COMMENT + "|#"}


def _unfinished_marker(line: str, suffix: str) -> bool:
    start_re = _COMMENT_START.get(suffix, _SLASH_COMMENT)
    if "/" in start_re and line.lstrip().startswith(("*", "/*")):
        start = 0  # dentro de un bloque /* … */ o abriéndolo
    else:
        match = re.search(start_re, line)
        start = match.start() if match else len(line)
    return bool(UNFINISHED_COMMENT_RE.search(line[start:]) or UNFINISHED_CODE_RE.search(line[:start]))


HUNK_RE = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")


def added_lines(path: Path, root: Path) -> set[int] | None:
    """Líneas (1-based) añadidas o modificadas en `path` respecto a HEAD.

    FW-226 (Example Tool FW-223, búfer TL `stub-gate-marca-codigo-preexistente-por-
    tocar-el-fichero`): tocar un fichero no debe hacer al módulo responsable de
    todo lo que ya había en él. `None` significa "escanear entero" — fichero
    no rastreado, sin git o fallo del comando: ante la duda el gate mira más.
    """
    try:
        rel = path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return None
    try:
        tracked = subprocess.run(
            ["git", "-C", str(root), "ls-files", "--error-unmatch", "--", rel],
            capture_output=True, text=True, timeout=30, check=False,
        )
        if tracked.returncode != 0:
            return None
        out = subprocess.run(
            ["git", "-C", str(root), "diff", "-U0", "HEAD", "--", rel],
            capture_output=True, text=True, timeout=30, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if out.returncode != 0:
        return None
    lines: set[int] = set()
    for raw in out.stdout.splitlines():
        match = HUNK_RE.match(raw)
        if match:
            start = int(match.group(1))
            count = int(match.group(2)) if match.group(2) is not None else 1
            lines.update(range(start, start + count))
    return lines


def is_source_file(path: Path) -> bool:
    if path.suffix.lower() not in SOURCE_SUFFIXES:
        return False
    parts = {p.lower() for p in path.parts}
    if parts & EXCLUDED_PARTS:
        return False
    return not EXCLUDED_NAME_RE.search(path.stem)


def _opted_out(lines: list[str], index: int, span: int = 1) -> bool:
    """La excepción se declara en la línea, la anterior, o dentro del bloque.

    `span` amplía la ventana hacia abajo para un manejador: la razón se escribe
    de forma natural sobre el `return` que degrada, no sobre el `except` que lo
    envuelve. Con la ventana de una sola línea, un `stub-ok` perfectamente
    puesto no se veía y el gate seguía bloqueando — enseñar a poner el marcador
    "en el sitio correcto y no obvio" es la vía rápida a que nadie lo use.
    """
    for i in range(index - 1, index + span + 1):
        if 0 <= i < len(lines) and OPT_OUT_RE.search(lines[i]):
            return True
    return False


STRING_LITERAL_RE = re.compile(r"'[^']*'|\"[^\"]*\"")


def _balance(texto: str) -> int:
    """Delimitadores sin cerrar en `texto`, ignorando los que viven en strings."""
    limpio = STRING_LITERAL_RE.sub("", texto)
    return (
        limpio.count("(") - limpio.count(")")
        + limpio.count("[") - limpio.count("]")
        + limpio.count("{") - limpio.count("}")
    )


def _handler_body(lines: list[str], index: int) -> list[str]:
    """Cuerpo del manejador que empieza en `lines[index]`, en líneas LÓGICAS.

    Se corta en la primera línea cuya indentación no supera la del `except` /
    `catch` (fin de bloque) o en el `}` de cierre, y se limita a 3 líneas
    lógicas: un manejador largo ya no es silencioso, haga lo que haga. Sin el
    corte por indentación, la primera línea del siguiente `def` entraba en el
    cuerpo y hacía fallar la detección justo en el caso que la motivó.

    FW-152: una sentencia repartida en varias líneas físicas se une en una sola
    lógica. Sin esto, el `return {` de un literal vacío multilínea (el patrón
    exacto de Example Service `src/llm.py:66-71`) agotaba el tope de 3 líneas antes
    de poder juzgarse, y el fail-open pasaba el gate.
    """
    base = len(lines[index]) - len(lines[index].lstrip())
    body: list[str] = []
    pendiente = ""
    for line in lines[index + 1:]:
        if not pendiente:
            if not line.strip():
                continue
            if line.strip() == "}":
                break
            if len(line) - len(line.lstrip()) <= base:
                break
            pendiente = line
        else:
            pendiente = pendiente + " " + line.strip()
        if _balance(pendiente) > 0:
            continue  # sentencia abierta: seguir uniendo
        body.append(pendiente)
        pendiente = ""
        if len(body) >= 3:
            break
    if pendiente:
        body.append(pendiente)
    return body


def _is_empty_literal(valor: object) -> bool:
    """Un literal es vacío si todas sus hojas lo son: {}, [], None, '', 0, False."""
    if isinstance(valor, dict):
        return all(_is_empty_literal(v) for v in valor.values())
    if isinstance(valor, (list, tuple, set)):
        return all(_is_empty_literal(v) for v in valor)
    return valor in (None, "", 0, False)


def _returns_empty(linea_logica: str) -> bool:
    """`return` de un literal vacío, simple o compuesto.

    Se apoya en `ast.literal_eval`, que solo acepta literales: `return {"a": []}`
    se juzga, y `return {"ok": True, "data": filas}` ni siquiera parsea (hay un
    nombre), así que un fallback con datos reales nunca entra aquí. Esa
    diferencia es la que mantiene bajo el falso positivo.
    """
    if EMPTY_RETURN_RE.match(linea_logica):
        return True
    match = re.match(r"^\s*return\s+(.+?);?\s*$", linea_logica, re.S)
    if not match:
        return False
    try:
        return _is_empty_literal(ast.literal_eval(match.group(1)))
    except (ValueError, SyntaxError, MemoryError, RecursionError):
        return False


def _is_silent_handler(body: list[str]) -> bool:
    """FW-152 — el manejador loguea (o no) y devuelve vacío: el error desaparece.

    Origen: `validate_no_stubs.py --paths src/llm.py` devolvía OK sobre el
    fail-open EXACTO que causó BUG-001…004 en Example Service — `logger.error(...)`
    seguido de `return {"skills": [], "experiences": [], "education": []}`. La
    primera versión exigía que el cuerpo fuera SOLO retornos vacíos, así que una
    sola línea de log bastaba para esquivar el gate. Registrar el error no lo
    propaga: el llamante sigue recibiendo un éxito con datos vacíos.

    Un `raise`, un retorno con datos o cualquier otra sentencia sacan al
    manejador de esta categoría.
    """
    utiles = [
        l for l in body
        if l.strip() and not l.strip().startswith(("#", "//")) and not LOG_CALL_RE.match(l)
    ]
    return bool(utiles) and all(_returns_empty(l) for l in utiles)


def _es_teardown(lines: list[str], index: int) -> bool:
    """El `try` protegía una limpieza, no la obtención de un dato (FW-161).

    `recorder.shutdown()` dentro de un `try/except: pass` es teardown: no hay
    valor que el llamante vaya a recibir falseado, que es lo que el gate
    persigue. Se distingue por la forma del `try`: si su última sentencia es una
    llamada suelta —sin asignación, sin `return`— no se está fabricando ningún
    dato. Medido: 9 casos así entre los 5 proyectos, todos legítimos.

    Deliberadamente estrecho: en cuanto el `try` asigna o retorna algo, vuelve a
    ser candidato a fail-open y el gate lo juzga.
    """
    base = len(lines[index]) - len(lines[index].lstrip())
    cuerpo_try: list[str] = []
    for linea in reversed(lines[:index]):
        if not linea.strip():
            continue
        sangria = len(linea) - len(linea.lstrip())
        if sangria <= base:
            # `try:` de apertura → fin del bloque protegido
            break
        cuerpo_try.append(linea.strip())
    if not cuerpo_try:
        return False
    ultima = cuerpo_try[0]
    if ultima.startswith(("return", "yield", "raise")) or "=" in ultima.split("(")[0]:
        return False
    return bool(re.match(r"^[\w.]+\s*\(.*\)\s*$", ultima))


DOCSTRING_LINE_RE = re.compile(r'^\s*[rubf]*("""|\'\'\')')


def _docstring_lines(lines: list[str]) -> set[int]:
    """Índices de líneas dentro de un bloque \"\"\"…\"\"\" (o \'\'\'…\'\'\').

    FW-155: el detector leía la documentación como si fuera código. Un módulo
    que EXPLICA el anti-patrón para justificar por qué existe —justo lo que hace
    `src/serialization.py` en Example Service, escrito para eliminar esos
    `except Exception: pass`— se auto-denunciaba. Documentar el problema no
    puede costar un bloqueo del gate.
    """
    dentro: set[int] = set()
    delimitador = None
    for i, line in enumerate(lines):
        if delimitador is None:
            m = DOCSTRING_LINE_RE.match(line)
            if not m:
                continue
            delimitador = m.group(1)
            dentro.add(i)
            resto = line[m.end():]
            if delimitador in resto:  # docstring de una sola línea
                delimitador = None
            continue
        dentro.add(i)
        if delimitador in line:
            delimitador = None
    return dentro


def scan_file(path: Path, root: Path, only_lines: set[int] | None = None) -> list[str]:
    try:
        lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
    except OSError:
        return []
    try:
        shown = path.relative_to(root)
    except ValueError:
        shown = path
    findings: list[str] = []
    docstrings = _docstring_lines(lines) if path.suffix == ".py" else set()
    for i, line in enumerate(lines):
        if i in docstrings:
            continue
        if only_lines is not None and (i + 1) not in only_lines:
            continue
        code = line.split("#", 1)[0] if path.suffix == ".py" else line
        for label, pattern in FABRICATED_PATTERNS:
            # El marcador de trabajo sin terminar vive precisamente en un
            # comentario; el resto de patrones se buscan solo en el código para
            # no disparar sobre prosa explicativa.
            if label == UNFINISHED_LABEL:
                hit = _unfinished_marker(line, path.suffix.lower())
            else:
                objetivo = ATTR_PLACEHOLDER_RE.sub("", code) if label == "literal placeholder" else code
                hit = bool(pattern.search(objetivo))
            if hit and not _opted_out(lines, i):
                findings.append(f"{shown}:{i + 1}: dato fabricado ({label}): {line.strip()[:90]}")
                break
        if PY_BROAD_EXCEPT_RE.match(line) or JS_CATCH_RE.search(line):
            body = _handler_body(lines, i)
            if (
                _is_silent_handler(body)
                and not _es_teardown(lines, i)
                and not _opted_out(lines, i, span=len(body) + 3)
            ):
                findings.append(
                    f"{shown}:{i + 1}: fail-open silencioso (captura ancha que devuelve vacío): "
                    f"{line.strip()[:70]}"
                )
    return findings


def git_changed_paths(root: Path) -> list[str]:
    """Rutas relativas con cambios en el working tree (staged, sin stage y sin seguir).

    FW-231: `--untracked-files=all` porque, sin él, git colapsa una carpeta nueva
    en `src/` y ninguno de sus ficheros llegaba al gate; `-z` porque, sin él, git
    escapa en octal los nombres no ASCII (`dise\\303\\261o.py`) y la ruta ya no
    existía en disco. En un rename/copia (`R`/`C`) la entrada siguiente es la
    ruta de origen y se descarta. Compartido con `maintenance.changed_paths`.
    """
    try:
        out = subprocess.run(
            ["git", "-C", str(root), "status", "--porcelain=v1", "-z", "--untracked-files=all"],
            capture_output=True, timeout=30, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return []
    if out.returncode != 0:
        return []
    entries = out.stdout.decode("utf-8", "surrogateescape").split("\0")
    paths: list[str] = []
    skip_origin = False
    for entry in entries:
        if skip_origin:
            skip_origin = False
            continue
        if len(entry) < 4:
            continue
        status, path = entry[:2], entry[3:]
        if "R" in status or "C" in status:
            skip_origin = True
        paths.append(path)
    return paths


def changed_files(root: Path) -> list[Path]:
    """Ficheros con cambios en el working tree (staged, sin stage y sin seguir)."""
    return [root / rel for rel in git_changed_paths(root)]


def module_cited_files(module: str, root: Path, acceptance: Path) -> list[Path]:
    """Ficheros fuente citados como evidencia en los ACs del módulo.

    La evidencia del propio módulo dice qué código tocó: es el conjunto más
    preciso disponible sin inventar un mapa módulo→ficheros que el estado no
    tiene, y es exactamente donde un stub tendría efecto (en Example Service los 6
    hallazgos estaban en ficheros citados por ACs ya cerrados)."""
    if not acceptance.exists():
        return []
    text = acceptance.read_text(encoding="utf-8", errors="ignore")
    section = _va.module_section_lines(text, module)
    if section is None:
        return []
    found: list[Path] = []
    for line in section:
        cleaned = _va.URL_RE.sub(" ", line)
        cleaned = _va.SSH_REMOTE_RE.sub(" ", cleaned)
        for m in _va.PATH_RE.finditer(cleaned):
            candidate = root / m.group(1)
            if candidate.is_file():
                found.append(candidate)
    return found


def collect_targets(module: str | None, root: Path, extra: list[str] | None = None) -> list[Path]:
    candidates: list[Path] = []
    if module:
        # FW-156: la aceptación se deriva de `root`, no de la constante global.
        # `collect_targets` recibía `root` pero leía siempre
        # `ROOT/_project/08_module_acceptance.md` — la del repo donde vive ESTE
        # script. Coincide por casualidad en un proyecto adoptado (ROOT == raíz
        # del proyecto), así que el fallo era invisible; pero en cualquier otro
        # contexto —un sandbox, un worktree, un test— el gate no encontraba
        # ningún fichero citado y devolvía OK sin escanear nada. Un gate que no
        # mira nada y dice OK es peor que no tenerlo.
        candidates.extend(
            module_cited_files(module, root, root / "_project" / "08_module_acceptance.md")
        )
        candidates.extend(changed_files(root))
    for raw in extra or []:
        candidates.append(Path(raw) if Path(raw).is_absolute() else root / raw)
    unique: dict[Path, None] = {}
    root_resolved = root.resolve()
    for path in candidates:
        resolved = path.resolve()
        # FW-231: las exclusiones se juzgan DENTRO del proyecto. Con la ruta
        # absoluta, un ancestro llamado `build`, `docs`, `tests`… o un worktree
        # bajo `.claude/worktrees/` excluía todos los ficheros y el gate daba OK
        # sin escanear. Una ruta fuera de la raíz (`--paths` absoluta) se juzga
        # por su nombre: sus ancestros no son del proyecto.
        try:
            filtro = resolved.relative_to(root_resolved)
        except ValueError:
            filtro = Path(resolved.name)
        if resolved.is_file() and is_source_file(filtro) and resolved not in unique:
            unique[resolved] = None
    return list(unique)


def _module_scan(module: str, root: Path) -> tuple[list[str], int]:
    """Hallazgos del módulo y número de ficheros fuente escaneados."""
    cited = {
        p.resolve()
        for p in module_cited_files(module, root, root / "_project" / "08_module_acceptance.md")
    }
    targets = collect_targets(module, root)
    findings: list[str] = []
    for path in targets:
        only = None if path.resolve() in cited else added_lines(path, root)
        findings.extend(scan_file(path, root, only_lines=only))
    return findings, len(targets)


def module_findings(module: str, root: Path = ROOT) -> list[str]:
    """Ficheros citados como evidencia: enteros (son el código del módulo).
    Ficheros solo tocados en el working tree: únicamente sus líneas cambiadas."""
    return _module_scan(module, root)[0]


def stub_problems(module: str, root: Path = ROOT) -> list[str]:
    """Hallazgos del módulo, en el formato de razones del closure_gate."""
    findings = module_findings(module, root)
    if not findings:
        return []
    detalle = "; ".join(findings[:6])
    extra = f" (+{len(findings) - 6} más)" if len(findings) > 6 else ""
    return [
        "STUB GATE: dato fabricado o fail-open silencioso en código del módulo "
        f"'{module}' — {detalle}{extra}. Implementar de verdad, o declarar la "
        "excepción en la línea con `stub-ok: <razón>` (FW-145)"
    ]


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print('uso: validate_no_stubs.py "<module_id>" | --paths <fichero> [...]')
        return 2
    findings: list[str] = []
    if argv[1] == "--paths":
        targets = collect_targets(None, ROOT, argv[2:])
        for path in targets:
            findings.extend(scan_file(path, ROOT))
        scanned = len(targets)
    else:
        findings, scanned = _module_scan(argv[1], ROOT)
    if findings:
        print("STUB GATE: FAIL")
        for f in findings:
            print(f"- {f}")
        return 1
    if scanned == 0:
        # FW-156: un OK que no miró nada tiene que decirlo.
        print(
            "STUB GATE: OK (0 ficheros fuente revisados — ningún fichero fuente "
            "citado ni modificado: nada que escanear)"
        )
        return 0
    print(f"STUB GATE: OK ({scanned} ficheros fuente revisados)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
