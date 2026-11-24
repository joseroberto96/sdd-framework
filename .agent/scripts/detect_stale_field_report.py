#!/usr/bin/env python3
"""Detecta filas de field_report.md con Estado abierto que un commit
posterior ya menciona por su FW-NNN, sin que la fila se haya vuelto a tocar.

Origen: FW-220 (.agent/docs/field_report.md:1139) — auditoría 2026-09-12
encontró 11 de 15 filas "pendiente" ya resueltas semanas/meses antes, porque
field_report.md es la única superficie de memoria operativa del framework
que NO pasa por mutate_state (nada obliga a re-revisar una fila cuando el
fix se mergea después).

Qué hace y qué NO hace: lista cada fila con Estado en
`pendiente-de-cosecha|diferida|candidata` y busca en `git log` (posterior a
la fecha de la fila si la lleva) cualquier commit cuyo subject mencione ese
mismo FW-NNN. NO decide si la fila está resuelta — es una heurística de
correlación (grep de FW-NNN en subject) con falsos negativos conocidos (hay
commits reales que resuelven un FW-NNN sin citarlo). Por eso el llamador
(framework_doctor.validate_field_report_staleness) SIEMPRE vuelca esto a
`warnings`, nunca a `errors`.

Alcance v1 (deliberadamente estrecho, no reabrir sin evidencia de
recurrencia — misma política de evolución que validate_no_stubs.py):

- Solo filas de tabla con patrón `| **FW-NNN** | ...` (la inmensa mayoría de
  las entradas reales). Filas sin negrita en el ID (ej. FW-208..FW-212, sin
  columna Estado) y secciones puramente narrativas fuera de tabla (ej.
  FW-194, FW-217/218/219) quedan FUERA de alcance v1: no hay Estado tabular
  que leer, y un segundo parser de prosa libre sería scope creep para un
  script tier_1 doc-only.
- No hay columna fija de "Estado": el documento usa decenas de cabeceras de
  tabla distintas. Se trata la ÚLTIMA celda no vacía de la fila como Estado.
  Si esa celda es en realidad un párrafo de decisión técnica que no EMPIEZA
  por uno de los 3 literales abiertos, la fila simplemente no se detecta
  como candidata — límite conocido, documentado, no crashea.

Uso:
    python3 .agent/scripts/detect_stale_field_report.py [--file <ruta>]
Códigos de salida: 0 = sin candidatos, 1 = hay candidatos, 2 = error de uso.
El exit code es solo para uso standalone/CI — el wiring en
framework_doctor.py llama a `stale_field_report_candidates()` directamente y
vuelca el resultado a `warnings` sin mirar ningún exit code.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path
from typing import NamedTuple

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_FIELD_REPORT = ROOT / ".agent" / "docs" / "field_report.md"

ROW_RE = re.compile(r"^\|\s*\*\*FW-(\d+)\*\*\s*\|")
OPEN_STATE_RE = re.compile(r"^\**\s*(pendiente-de-cosecha|diferida|candidata|abiert[oa])\b", re.IGNORECASE)
SECTION_RE = re.compile(r"^#{2,3}\s+FW-(\d+)\s+[—-]")
SECTION_STATE_RE = re.compile(r"^\*\*Estado:?\*\*:?\s*(.+)$")
PATH_RE = re.compile(r"`?((?:[\w.-]+/)*[\w.-]+\.(?:py|md|json|sh|toml|yml|yaml))`?")
# Un archivo tocado por más commits que esto no discrimina (state_manager.py, field_report.md…).
HOT_FILE_COMMITS = 25
DATE_RE = re.compile(r"\b(\d{4}-\d{2}-\d{2})\b")
LOG_SEP = "\x01"


class OpenRow(NamedTuple):
    fw_id: str
    line_no: int
    estado_cell: str
    since: str | None
    cited_files: tuple[str, ...] = ()


def split_table_row(line: str) -> list[str]:
    """Celdas de una fila Markdown, respetando `` `código con | dentro` ``.

    Un `str.split('|')` ingenuo rompe filas cuya evidencia cita un pipeline
    de shell entre backticks (hay varias en field_report.md, p.ej.
    "`grep -rh 'Siguiente comando: /' .agent/workflows/`")."""
    cells: list[str] = []
    buf: list[str] = []
    in_code = False
    for ch in line:
        if ch == "`":
            in_code = not in_code
            buf.append(ch)
        elif ch == "|" and not in_code:
            cells.append("".join(buf))
            buf = []
        else:
            buf.append(ch)
    cells.append("".join(buf))
    stripped = [c.strip() for c in cells]
    # Una fila `| a | b |` produce ['', 'a', 'b', ''] — los bordes vacíos no
    # son celdas.
    if stripped and stripped[0] == "":
        stripped = stripped[1:]
    if stripped and stripped[-1] == "":
        stripped = stripped[:-1]
    return stripped


def parse_open_rows(text: str) -> list[OpenRow]:
    """Filas `| **FW-NNN** | ... |` cuyo Estado (última celda no vacía)
    empieza por un literal de "sigue abierto". Tolerante: una fila cuya
    última celda no matchea simplemente no se reporta, nunca lanza."""
    rows: list[OpenRow] = []
    for i, line in enumerate(text.splitlines(), start=1):
        match = ROW_RE.match(line)
        if not match:
            continue
        cells = split_table_row(line)
        if not cells:
            continue
        estado_cell = ""
        for cell in reversed(cells):
            if cell.strip():
                estado_cell = cell.strip()
                break
        estado_clean = estado_cell.strip("*").strip()
        if not OPEN_STATE_RE.match(estado_clean):
            continue
        date_match = DATE_RE.search(estado_cell)
        rows.append(
            OpenRow(
                fw_id=match.group(1),
                line_no=i,
                estado_cell=estado_cell[:120],
                since=date_match.group(1) if date_match else None,
                cited_files=cited_paths(line),
            )
        )
    rows.extend(parse_open_sections(text))
    return rows


def cited_paths(text: str) -> tuple[str, ...]:
    return tuple(dict.fromkeys(m.group(1) for m in PATH_RE.finditer(text)))


def parse_open_sections(text: str) -> list[OpenRow]:
    """Secciones `## FW-NNN — …` con `**Estado:** abierta|diferida|…` (formato narrativo)."""
    lines = text.splitlines()
    rows: list[OpenRow] = []
    for index, line in enumerate(lines):
        head = SECTION_RE.match(line)
        if not head:
            continue
        end = next((j for j in range(index + 1, len(lines)) if SECTION_RE.match(lines[j]) or lines[j].startswith("## ")), len(lines))
        body = lines[index:end]
        state = next((m.group(1) for m in map(SECTION_STATE_RE.match, body) if m), None)
        if state is None or not OPEN_STATE_RE.match(state.strip("*").strip()):
            continue
        date = DATE_RE.search(line) or DATE_RE.search("\n".join(body))
        rows.append(OpenRow(
            fw_id=head.group(1), line_no=index + 1, estado_cell=state[:120],
            since=date.group(1) if date else None, cited_files=cited_paths("\n".join(body)),
        ))
    return rows


def git_commit_log(root: Path) -> list[tuple[str, str, str]]:
    """(sha_corto, fecha ISO, subject) de todo el historial, una sola
    llamada. Devuelve [] ante cualquier fallo (sin git, timeout, repo sin
    commits) — fail-open silencioso deliberado, este detector es
    diagnóstico, no debe bloquear nada."""
    try:
        out = subprocess.run(
            ["git", "-C", str(root), "log", "--date=short", f"--format=%h{LOG_SEP}%ad{LOG_SEP}%s"],
            capture_output=True, timeout=30, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return []
    if out.returncode != 0:
        return []
    commits: list[tuple[str, str, str]] = []
    for raw in out.stdout.decode("utf-8", "ignore").splitlines():
        parts = raw.split(LOG_SEP, 2)
        if len(parts) == 3:
            commits.append((parts[0], parts[1], parts[2]))
    return commits


PROJECT_PREFIX_RE = re.compile(
    r"(?i)(?:\b(?:BOTÓN|BOTON|TL|JS|PA|GW|TALLO|BF|HB|FLAC)\b|\bB\d+\b)[,\s/:_-]+$"
)
PROJECT_PAREN_RE = re.compile(
    r"(?i)(?:BOTÓN|BOTON|TL|JS|PA|GW|TALLO|BF|HB|FLAC|B\d+)\b"
)


def _is_project_mention(subject: str, start_idx: int) -> bool:
    """B6: comprueba si la mención FW-<id> en `start_idx` corresponde a un
    proyecto y no a la plantilla.

    Detecta prefijos directos (BOTÓN FW-, Boton FW-, TL FW-, JS FW-, PA FW-,
    GW FW-, B2, FW-, etc.) y menciones dentro de bloques parentizados de proyecto
    como `(BOTÓN FW-085)` o `(B2, FW-084/...)`.
    """
    prefix_text = subject[:start_idx]
    if PROJECT_PREFIX_RE.search(prefix_text):
        return True
    last_open = prefix_text.rfind("(")
    if last_open != -1 and ")" not in prefix_text[last_open:]:
        inside = prefix_text[last_open + 1:].lstrip()
        if PROJECT_PAREN_RE.match(inside):
            return True
    return False


def git_files_by_commit(root: Path) -> dict[str, list[str]]:
    """sha_corto → archivos que toca (una sola llamada); {} ante cualquier fallo."""
    try:
        out = subprocess.run(
            ["git", "-C", str(root), "log", "--name-only", f"--format={LOG_SEP}%h"],
            capture_output=True, timeout=60, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return {}
    if out.returncode != 0:
        return {}
    files: dict[str, list[str]] = {}
    current: list[str] | None = None
    for raw in out.stdout.decode("utf-8", "ignore").splitlines():
        if raw.startswith(LOG_SEP):
            current = files.setdefault(raw[1:], [])
        elif raw.strip() and current is not None:
            current.append(raw.strip())
    return files


def find_file_candidates(
    rows: list[OpenRow], commits: list[tuple[str, str, str]], files: dict[str, list[str]],
) -> list[str]:
    """FW-304: filas abiertas cuyos archivos citados cambian en commits posteriores a su fecha,
    aunque el subject no cite el FW. Ignora archivos «calientes» y filas sin fecha."""
    if not files:
        return []
    touches: dict[str, int] = {}
    for paths in files.values():
        for path in set(paths):
            touches[path] = touches.get(path, 0) + 1
    findings: list[str] = []
    for row in rows:
        if row.since is None:
            continue
        specific = {p for p in row.cited_files if 0 < touches.get(p, 0) <= HOT_FILE_COMMITS}
        if not specific:
            continue
        hits = [
            (sha, date, subject) for sha, date, subject in commits
            if date >= row.since and specific & set(files.get(sha, []))
        ]
        if not hits:
            continue
        sha, date, subject = hits[0]
        names = ", ".join(sorted(specific & set(files.get(sha, [])))[:2])
        findings.append(
            f"FW-{row.fw_id} (línea {row.line_no}, Estado actual: \"{row.estado_cell}\") "
            f"— commit(s) posteriores tocan archivos que cita ({names}): {sha} ({date}) {subject[:70]}"
            + (f" (+{len(hits) - 1} más)" if len(hits) > 1 else "")
        )
    return findings


def find_stale_candidates(rows: list[OpenRow], commits: list[tuple[str, str, str]]) -> list[str]:
    """Para cada fila abierta, commits cuyo subject cite FW-<id> (límite de
    palabra, para que FW-100 no matchee FW-1005) en o después de `since`."""
    findings: list[str] = []
    for row in rows:
        pattern = re.compile(rf"(?i)\bFW-{row.fw_id}\b")
        hits: list[tuple[str, str, str]] = []
        for sha, date, subject in commits:
            if row.since is not None and date < row.since:
                continue
            for m in pattern.finditer(subject):
                if not _is_project_mention(subject, m.start()):
                    hits.append((sha, date, subject))
                    break
        if not hits:
            continue
        shown = hits[:3]
        cites = "; ".join(f"{sha} ({date}) {subject[:70]}" for sha, date, subject in shown)
        extra = f" (+{len(hits) - 3} más)" if len(hits) > 3 else ""
        findings.append(
            f"FW-{row.fw_id} (línea {row.line_no}, Estado actual: \"{row.estado_cell}\") "
            f"— commit(s) posteriores la mencionan: {cites}{extra}"
        )
    return findings


def stale_field_report_candidates(
    field_report: Path = DEFAULT_FIELD_REPORT, root: Path = ROOT
) -> list[str]:
    """Entry point reutilizado por framework_doctor.py. [] si el fichero no
    existe, no hay filas abiertas, o no hay historial git — nunca lanza."""
    if not field_report.exists():
        return []
    try:
        text = field_report.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return []
    rows = parse_open_rows(text)
    if not rows:
        return []
    commits = git_commit_log(root)
    if not commits:
        return []
    by_subject = find_stale_candidates(rows, commits)
    mentioned = {finding.split(" ", 1)[0] for finding in by_subject}
    by_files = [
        finding for finding in find_file_candidates(rows, commits, git_files_by_commit(root))
        if finding.split(" ", 1)[0] not in mentioned
    ]
    return by_subject + by_files


def main(argv: list[str]) -> int:
    field_report = DEFAULT_FIELD_REPORT
    args = argv[1:]
    if args:
        if args[0] != "--file" or len(args) != 2:
            print("uso: detect_stale_field_report.py [--file <ruta>]", file=sys.stderr)
            return 2
        field_report = Path(args[1])
    findings = stale_field_report_candidates(field_report, ROOT)
    if findings:
        print("FIELD REPORT STALENESS: candidatos a revisar")
        for f in findings:
            print(f"- {f}")
        return 1
    print("FIELD REPORT STALENESS: sin candidatos")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
