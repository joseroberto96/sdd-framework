#!/usr/bin/env python3
"""Mediciones de línea base de la 5.3.0 (solo lectura).

Subcomandos que amplían `session_cost.py` (se invocan con el mismo CLI:
``session_cost.py <subcomando> [opciones]``; también ``session_metrics.py
<subcomando>``). Leen las transcripciones de Claude Code (``~/.claude/projects``),
los rollouts de Codex (``~/.codex/{sessions,archived_sessions}``) y los
``_project/state_log.jsonl`` / ``11_file_dependencies.md`` / ``03_project_state.json``
de los proyectos. No escriben nada en ningún sitio: todo va a stdout.

  norm-load            norma cargada por turno, por fichero y por módulo (Claude)
  module-cost          tiempo, turnos y tokens por módulo cerrado (Claude + Codex)
  loops                tests en rojo repetidos y ediciones revertidas (Claude)
  receipts             recibos de aprobación/revisión sellados más de una vez
  read-weight          peso de lecturas, shell, norma y conversación en el contexto (Claude)
  independent-modules  módulos de una fase que no comparten ficheros
  codex-effort-cache   efecto del cambio de esfuerzo en la caché de Codex

Convenciones comunes: ``--json``, ``--project RUTA`` (repetible), ``--since
YYYY-MM-DD``, ``--claude-dir``, ``--codex-home``. Estimación de tokens:
``caracteres / TOKEN_CHARS`` (constante, 4: aproximación habitual para texto
mixto en español/código; es una estimación, no un recuento del tokenizador).
"""

from __future__ import annotations

import argparse
import bisect
import collections
import json
import os
import re
import statistics
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator

import session_cost as sc

TOKEN_CHARS = 4  # tokens estimados = caracteres / 4
DEFAULT_RECEIPTS_SINCE = "2026-10-01"

# --------------------------------------------------------------- Utilidades


def est_tokens(chars: int) -> int:
    return round(chars / TOKEN_CHARS)


def _parse_since(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value).replace(tzinfo=timezone.utc)
    except ValueError:
        raise SystemExit(f"--since inválido: {value!r} (usa YYYY-MM-DD)")


def _add_common(parser: argparse.ArgumentParser, *, since_default: str | None = None) -> None:
    parser.add_argument("--json", action="store_true", help="salida JSON")
    parser.add_argument("--project", action="append", metavar="RUTA",
                        help="ruta real del proyecto (repetible; se compara con el cwd registrado)")
    parser.add_argument("--since", default=since_default, help="solo desde YYYY-MM-DD")
    parser.add_argument("--claude-dir", default="~/.claude/projects")
    parser.add_argument("--codex-home", default=os.environ.get("CODEX_HOME", "~/.codex"))


def _projects(args: argparse.Namespace, *, default_cwd: bool) -> list[str]:
    projects = [p for p in (args.project or []) if p]
    if not projects and default_cwd:
        projects = [os.getcwd()]
    return projects


def _render(rows: list[list[Any]], headers: list[str]) -> str:
    table = [[str(c) for c in headers]] + [[str(c) for c in row] for row in rows]
    widths = [max(len(r[i]) for r in table) for i in range(len(headers))]
    lines = []
    for n, row in enumerate(table):
        cells = [row[0].ljust(widths[0])] + [row[i].rjust(widths[i]) for i in range(1, len(row))]
        lines.append("  ".join(cells).rstrip())
        if n == 0:
            lines.append("  ".join("-" * w for w in widths))
    return "\n".join(lines)


def _emit(args: argparse.Namespace, data: Any, text: str) -> int:
    if args.json:
        print(json.dumps(data, ensure_ascii=False, indent=2))
    else:
        print(text)
    return 0


def _median(values: list[float]) -> float:
    return round(statistics.median(values), 3) if values else 0.0


def claude_files(root: Path, projects: list[str], since: datetime | None) -> list[tuple[Path, str]]:
    """Transcripciones raíz de Claude cuyo cwd real coincide con algún proyecto."""
    found: list[tuple[Path, str]] = []
    for path in sorted(root.glob("*/*.jsonl")):
        if since:
            try:
                if datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc) < since:
                    continue
            except OSError:
                continue
        cwd = ""
        for obj in sc._iter_json(path):
            if obj.get("cwd"):
                cwd = str(obj["cwd"])
                break
        if projects and not any(sc._cwd_matches(cwd, p) for p in projects):
            continue
        found.append((path, cwd))
    return found


def codex_files(home: Path, projects: list[str]) -> list[tuple[Path, dict[str, Any]]]:
    """Rollouts de Codex (sessions + archived_sessions) con su `session_meta`."""
    found: list[tuple[Path, dict[str, Any]]] = []
    paths = sorted({p for sub in ("sessions", "archived_sessions") for p in (home / sub).rglob("rollout-*.jsonl")})
    for path in paths:
        first = next(sc._iter_json(path), None)
        meta = (first or {}).get("payload") if first and first.get("type") == "session_meta" else None
        if not isinstance(meta, dict):
            continue
        if projects and not any(sc._cwd_matches(str(meta.get("cwd") or ""), p) for p in projects):
            continue
        found.append((path, meta))
    return found


def _text_of(content: Any) -> str:
    """Texto de un `tool_result.content` (cadena o lista de bloques). Las imágenes no suman."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(str(b.get("text") or "") for b in content if isinstance(b, dict) and b.get("type") == "text")
    return ""


# ------------------------------------------------- Eventos de una sesión Claude


def claude_events(path: Path) -> Iterator[dict[str, Any]]:
    """Eventos normalizados de una transcripción raíz, sin líneas repetidas.

    Las líneas se deduplican por `uuid` (al reanudar o compactar Claude copia las
    anteriores con el mismo uuid). Tipos (`t`): builder, meta, compact_summary,
    assistant_text, tool_use, tool_result, attachment, compact."""
    return events_from_objects(sc._iter_json(path))


def events_from_objects(objects: Iterable[dict[str, Any]]) -> Iterator[dict[str, Any]]:
    """Los mismos eventos a partir de líneas ya leídas (el hook `Stop` solo lee el final)."""
    seen: set[str] = set()
    for obj in objects:
        uid = obj.get("uuid")
        if uid:
            if uid in seen:
                continue
            seen.add(str(uid))
        ts = sc._ts(obj.get("timestamp"))
        kind = obj.get("type")
        if kind == "assistant":
            for block in (obj.get("message") or {}).get("content") or []:
                if not isinstance(block, dict):
                    continue
                if block.get("type") == "text":
                    yield {"t": "assistant_text", "ts": ts, "text": str(block.get("text") or "")}
                elif block.get("type") == "tool_use":
                    yield {"t": "tool_use", "ts": ts, "id": str(block.get("id") or ""),
                           "name": str(block.get("name") or ""), "input": block.get("input") or {}}
        elif kind == "user":
            if obj.get("isSidechain"):
                continue
            content = (obj.get("message") or {}).get("content")
            blocks = content if isinstance(content, list) else [{"type": "text", "text": content}] if isinstance(content, str) else []
            for block in blocks:
                if not isinstance(block, dict):
                    continue
                if block.get("type") == "tool_result":
                    yield {"t": "tool_result", "ts": ts, "id": str(block.get("tool_use_id") or ""),
                           "text": _text_of(block.get("content")), "error": bool(block.get("is_error"))}
                elif block.get("type") == "text":
                    text = str(block.get("text") or "")
                    if obj.get("isCompactSummary"):
                        yield {"t": "compact_summary", "ts": ts, "text": text}
                    elif obj.get("isMeta"):
                        yield {"t": "meta", "ts": ts, "text": text}
                    else:
                        yield {"t": "builder", "ts": ts, "text": text}
        elif kind == "attachment":
            yield {"t": "attachment", "ts": ts, "att": obj.get("attachment") or {}}
        elif kind == "system" and obj.get("subtype") == "compact_boundary":
            yield {"t": "compact", "ts": ts}


# ------------------------------------------------------------------- Norma

# Norma = ficheros del framework que la sesión abre o recibe.
_NORM_PATH_RE = re.compile(
    r"(?:^|/)("
    r"\.agent/(?:rules|workflows|skills)/.+"
    r"|\.agent/docs/extended_protocol\.md"
    r"|GUARDRAILS_MASTER\.md|AGENTS\.md|CLAUDE\.md"
    r"|\.claude/(?:commands|agents)/.+"
    r")$"
)
_PATH_TOKEN_RE = re.compile(r"[\w./~${}-]+\.(?:md|json|py|txt|toml|ya?ml|sh)\b")
_HOME_CLAUDE = os.path.join(os.path.expanduser("~"), ".claude") + os.sep
_WRAPPER_HEAD_RE = re.compile(r"^\s*(?:<!--[^>]*-->\s*)?#\s*/([\w\-ñáéíóúü]+)", re.MULTILINE)


def norm_key(path: str) -> str | None:
    """Clave canónica de un fichero de norma (ruta relativa al proyecto) o None."""
    if not path:
        return None
    expanded = os.path.expanduser(path)
    if expanded.startswith(_HOME_CLAUDE):  # memoria/ajustes globales del usuario: no es norma del proyecto
        return None
    match = _NORM_PATH_RE.search(path.replace("\\", "/"))
    return match.group(1) if match else None


def _disk_text(cwd: str, key: str) -> str | None:
    if not cwd:
        return None
    try:
        return (Path(cwd) / key).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


def norm_reads(name: str, data: dict[str, Any], text: str, error: bool, cwd: str) -> list[dict[str, Any]]:
    """Lecturas de norma que implica una herramienta y su resultado.

    Read: el fichero. Bash con verbo de lectura (`_READ_VERBS`): cada fichero de
    norma que nombra el comando. El tamaño es el del RESULTADO de la herramienta
    (si el comando nombra varios ficheros, se reparte en proporción al tamaño del
    fichero en disco; sin disco, a partes iguales). Si no hay resultado (vacío o
    error) se toma el tamaño actual del fichero en el proyecto (`origen=disco`)."""
    keys: list[str] = []
    if name == "Read":
        key = norm_key(str(data.get("file_path") or ""))
        keys = [key] if key else []
    elif name == "Bash":
        command = str(data.get("command") or "")
        if sc._READ_VERBS.search(command):
            for token in _PATH_TOKEN_RE.findall(command):
                key = norm_key(token.strip("'\""))
                if key and key not in keys:
                    keys.append(key)
    if not keys:
        return []
    disk = {k: _disk_text(cwd, k) for k in keys}
    has_result = bool(text) and not error
    items: list[dict[str, Any]] = []
    if has_result:
        weights = {k: float(len(disk[k] or "")) for k in keys}
        total_w = sum(weights.values())
        for k in keys:
            share = (weights[k] / total_w) if total_w else 1.0 / len(keys)
            items.append({"file": k, "chars": round(len(text) * share), "words": round(len(text.split()) * share),
                          "origen": "resultado"})
    else:
        for k in keys:
            body = disk[k]
            if body is None:
                continue
            items.append({"file": k, "chars": len(body), "words": len(body.split()), "origen": "disco"})
    return items


def _wrapper_key(text: str) -> str | None:
    match = _WRAPPER_HEAD_RE.search(text[:600])
    if match and ("GENERATED: framework wrapper" in text[:200] or "Fuente canónica" in text[:600]):
        return f".claude/commands/{match.group(1)}.md"
    return None


_READ_TOOLS = {"Read", "Grep", "Glob", "LS", "NotebookRead"}
_SHELL_TOOLS = {"Bash", "BashOutput", "TaskOutput", "Monitor", "KillShell"}
WEIGHT_CLASSES = ("lectura", "shell", "otros", "norma", "builder", "asistente")


def scan_claude(path: Path, cwd: str) -> dict[str, Any]:
    """Una pasada por la sesión raíz: norma cargada y peso por clase de contenido."""
    uses: dict[str, tuple[str, dict[str, Any]]] = {}
    items: list[dict[str, Any]] = []
    chars = {k: 0 for k in WEIGHT_CLASSES}
    turns = 0
    first = last = None
    autoloaded: set[str] = set()
    for ev in claude_events(path):
        ts = ev.get("ts")
        if ts:
            first = ts if first is None or ts < first else first
            last = ts if last is None or ts > last else last
        kind = ev["t"]
        if kind == "builder":
            turns += 1
            chars["builder"] += len(ev["text"])
        elif kind in ("assistant_text", "compact_summary"):
            chars["asistente"] += len(ev["text"])
        elif kind == "meta":
            key = _wrapper_key(ev["text"])
            if key:
                items.append({"file": key, "chars": len(ev["text"]), "words": len(ev["text"].split()),
                              "origen": "wrapper", "turn": turns, "ts": ts})
                chars["norma"] += len(ev["text"])
        elif kind == "attachment":
            att = ev["att"]
            if att.get("type") == "instructions":
                for entry in att.get("files") or []:
                    key = norm_key(str(entry.get("path") or ""))
                    body = str(entry.get("content") or "")
                    if key and key not in autoloaded:  # una vez por sesión: las recargas no se suman
                        autoloaded.add(key)
                        items.append({"file": key, "chars": len(body), "words": len(body.split()),
                                      "origen": "autocarga", "turn": turns, "ts": ts})
                        chars["norma"] += len(body)
        elif kind == "tool_use":
            uses[ev["id"]] = (ev["name"], ev["input"] if isinstance(ev["input"], dict) else {})
        elif kind == "tool_result":
            name, data = uses.get(ev["id"], ("?", {}))
            found = norm_reads(name, data, ev["text"], ev["error"], cwd)
            counted = 0
            for item in found:
                item.update({"turn": turns, "ts": ts})
                items.append(item)
                chars["norma"] += item["chars"]
                if item["origen"] == "resultado":
                    counted += item["chars"]
            rest = max(0, len(ev["text"]) - counted)
            if name in _READ_TOOLS:
                chars["lectura"] += rest
            elif name in _SHELL_TOOLS:
                chars["shell"] += rest
            else:
                chars["otros"] += rest
    return {"turns": turns, "items": items, "chars": chars, "start": first, "end": last}


# ----------------------------------------------------------------- state_log


def read_state_log(project: str) -> list[dict[str, Any]]:
    rows = []
    for obj in sc._iter_json(Path(project).expanduser() / "_project" / "state_log.jsonl"):
        obj["_ts"] = sc._ts(obj.get("ts"))
        if obj["_ts"]:
            rows.append(obj)
    rows.sort(key=lambda o: o["_ts"])
    return rows


def find_project_root(cwd: str) -> str | None:
    """Ancestro de `cwd` con `_project/state_log.jsonl` (None si no hay)."""
    if not cwd:
        return None
    path = Path(sc._realpath(cwd))
    for candidate in [path, *path.parents]:
        if (candidate / "_project" / "state_log.jsonl").is_file():
            return str(candidate)
    return None


def module_intervals(log: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Intervalos de módulos CERRADOS: desde el primer `ciclo_started` tras el
    cierre anterior del mismo módulo hasta su `complete_module`. Un cierre sin
    `ciclo_started` previo sale con `start=None`."""
    open_start: dict[str, datetime] = {}
    last_close: dict[str, datetime] = {}
    out: list[dict[str, Any]] = []
    for ev in log:
        action = ev.get("action")
        payload = ev.get("payload") or {}
        if action == "ciclo_started":
            module = str(payload.get("module") or "")
            if module and module not in open_start:
                open_start[module] = ev["_ts"]
        elif action == "complete_module":
            module = str(payload.get("id") or "")
            if module:
                out.append({"module": module, "start": open_start.pop(module, None), "end": ev["_ts"],
                            "prev_close": last_close.get(module)})
                last_close[module] = ev["_ts"]
    return out


def attribute_module(intervals: list[dict[str, Any]], moment: datetime | None) -> str | None:
    """Módulo cuyo intervalo contiene `moment` (con solape, el de inicio más reciente)."""
    if moment is None:
        return None
    best = None
    for it in intervals:
        if it["start"] and it["start"] <= moment <= it["end"]:
            if best is None or it["start"] > best["start"]:
                best = it
    return best["module"] if best else None


# ------------------------------------------------------------------ norm-load


def cmd_norm_load(args: argparse.Namespace) -> int:
    since = _parse_since(args.since)
    projects = _projects(args, default_cwd=False)
    files = claude_files(Path(args.claude_dir).expanduser(), projects, since)
    sessions: list[dict[str, Any]] = []
    by_file: dict[str, dict[str, int]] = {}
    by_module: dict[str, dict[str, Any]] = {}
    state_cache: dict[str, list[dict[str, Any]]] = {}
    attributed_chars = total_chars = 0
    for path, cwd in files:
        scan = scan_claude(path, cwd)
        if since and scan["end"] and scan["end"] < since:
            continue
        items = scan["items"]
        if not items:
            continue
        per_file: dict[str, dict[str, int]] = {}
        per_turn: dict[int, int] = collections.defaultdict(int)
        root = find_project_root(cwd)
        intervals = []
        if root:
            if root not in state_cache:
                state_cache[root] = module_intervals(read_state_log(root))
            intervals = state_cache[root]
        for item in items:
            slot = per_file.setdefault(item["file"], {"chars": 0, "words": 0, "veces": 0})
            slot["chars"] += item["chars"]
            slot["words"] += item["words"]
            slot["veces"] += 1
            per_turn[item["turn"]] += item["chars"]
            agg = by_file.setdefault(item["file"], {"chars": 0, "words": 0, "veces": 0, "sesiones": 0})
            agg["chars"] += item["chars"]
            agg["words"] += item["words"]
            agg["veces"] += 1
            total_chars += item["chars"]
            module = attribute_module(intervals, item.get("ts"))
            if module:
                attributed_chars += item["chars"]
                slot_m = by_module.setdefault(module, {"chars": 0, "words": 0, "sesiones": set()})
                slot_m["chars"] += item["chars"]
                slot_m["words"] += item["words"]
                slot_m["sesiones"].add(path.stem)
        for key in per_file:
            by_file[key]["sesiones"] += 1
        chars_total = sum(v["chars"] for v in per_file.values())
        words_total = sum(v["words"] for v in per_file.values())
        turns = max(scan["turns"], 1)
        series = [est_tokens(per_turn.get(i, 0)) for i in range(scan["turns"] + 1)]
        sessions.append({
            "session_id": path.stem, "cwd": cwd, "start": scan["start"].isoformat() if scan["start"] else "",
            "turns": scan["turns"], "norm_words": words_total, "norm_tokens": est_tokens(chars_total),
            "tokens_per_turn": round(est_tokens(chars_total) / turns, 1),
            "per_turn_tokens": series,
            "top_files": [
                {"file": k, "words": v["words"], "tokens": est_tokens(v["chars"]), "veces": v["veces"]}
                for k, v in sorted(per_file.items(), key=lambda kv: -kv[1]["chars"])[:10]
            ],
        })
    sessions.sort(key=lambda s: s["start"])
    top_global = [
        {"file": k, "words": v["words"], "tokens": est_tokens(v["chars"]), "veces": v["veces"], "sesiones": v["sesiones"]}
        for k, v in sorted(by_file.items(), key=lambda kv: -kv[1]["chars"])[:10]
    ]
    modules = [
        {"module": m, "words": v["words"], "tokens": est_tokens(v["chars"]), "sesiones": len(v["sesiones"])}
        for m, v in sorted(by_module.items(), key=lambda kv: -kv[1]["chars"])
    ]
    coverage = round(100 * attributed_chars / total_chars, 1) if total_chars else 0.0
    data = {
        "token_chars": TOKEN_CHARS, "sesiones": sessions, "top_ficheros": top_global, "por_modulo": modules,
        "atribucion_modulo_pct": coverage,
        "nota": "Tokens estimados = caracteres/%d. La atribución a módulo cruza la hora de cada carga con los "
                "intervalos ciclo_started..complete_module del state_log del proyecto: no es fiable con ciclos en "
                "paralelo (se atribuye al de inicio más reciente) ni fuera de un ciclo." % TOKEN_CHARS,
    }
    limit = args.limit
    rows = [[s["session_id"][:8], s["start"][:10], s["turns"], s["norm_words"], s["norm_tokens"], s["tokens_per_turn"],
             s["top_files"][0]["file"] if s["top_files"] else ""] for s in sessions[-limit:]]
    text = [f"Norma cargada por sesión (tokens ≈ caracteres/{TOKEN_CHARS}); últimas {min(limit, len(sessions))} de {len(sessions)}",
            _render(rows, ["sesión", "inicio", "turnos", "palabras", "tokens", "tok/turno", "mayor fichero"])]
    if sessions:
        tok = [s["norm_tokens"] for s in sessions]
        text.append(f"Total {sum(tok)} tokens; mediana por sesión {_median(tok):.0f}; "
                    f"media por turno {sum(tok) / max(1, sum(max(s['turns'], 1) for s in sessions)):.1f}")
    text += ["", "Top 10 ficheros de norma (todas las sesiones)",
             _render([[t["file"], t["words"], t["tokens"], t["veces"], t["sesiones"]] for t in top_global],
                     ["fichero", "palabras", "tokens", "cargas", "sesiones"])]
    if modules:
        text += ["", f"Por módulo (atribuido el {coverage}% de la norma; no fiable con ciclos en paralelo)",
                 _render([[m["module"], m["words"], m["tokens"], m["sesiones"]] for m in modules[:limit]],
                         ["módulo", "palabras", "tokens", "sesiones"])]
    else:
        text += ["", "Por módulo: sin atribución (sin state_log con ciclos en los proyectos de estas sesiones)."]
    return _emit(args, data, "\n".join(text))


# ----------------------------------------------------------------- read-weight


def cmd_read_weight(args: argparse.Namespace) -> int:
    since = _parse_since(args.since)
    files = claude_files(Path(args.claude_dir).expanduser(), _projects(args, default_cwd=False), since)
    rows_json: list[dict[str, Any]] = []
    grand = {k: 0 for k in WEIGHT_CLASSES}
    for path, cwd in files:
        scan = scan_claude(path, cwd)
        if since and scan["end"] and scan["end"] < since:
            continue
        total = sum(scan["chars"].values())
        if not total:
            continue
        for k in WEIGHT_CLASSES:
            grand[k] += scan["chars"][k]
        rows_json.append({
            "session_id": path.stem, "cwd": cwd, "start": scan["start"].isoformat() if scan["start"] else "",
            "chars_total": total, "tokens_total": est_tokens(total),
            "clases": {k: {"chars": scan["chars"][k], "tokens": est_tokens(scan["chars"][k]),
                           "pct": round(100 * scan["chars"][k] / total, 1)} for k in WEIGHT_CLASSES},
        })
    rows_json.sort(key=lambda r: r["start"])
    gt = sum(grand.values())
    totals = {k: {"chars": grand[k], "tokens": est_tokens(grand[k]), "pct": round(100 * grand[k] / gt, 1) if gt else 0.0}
              for k in WEIGHT_CLASSES}
    data = {
        "token_chars": TOKEN_CHARS, "sesiones": rows_json, "total": totals,
        "nota": "Contenido de la sesión raíz por clase (lo que escriben herramientas y personas, sin contar el "
                "prompt de sistema ni el razonamiento). Un resultado de lectura/shell que es norma se cuenta como "
                "norma. El texto de un adjunto o de un subagente no entra.",
    }
    limit = args.limit
    rows = [[r["session_id"][:8], r["start"][:10], r["tokens_total"]] + [f"{r['clases'][k]['pct']}%" for k in WEIGHT_CLASSES]
            for r in rows_json[-limit:]]
    text = [f"Peso de contenido por clase (tokens ≈ caracteres/{TOKEN_CHARS}); últimas {min(limit, len(rows_json))} de {len(rows_json)}",
            _render(rows, ["sesión", "inicio", "tokens"] + [f"%{k}" for k in WEIGHT_CLASSES]), "",
            "Total: " + ", ".join(f"{k} {totals[k]['pct']}% ({totals[k]['tokens']} tok)" for k in WEIGHT_CLASSES)]
    return _emit(args, data, "\n".join(text))


# ----------------------------------------------------------------------- loops

# Ejecutores que se reconocen en cualquier parte del segmento: llevan su subcomando propio.
_TEST_CMD_RE = re.compile(
    r"(?:python3?\s+-m\s+(?:pytest|unittest)\b"
    r"|\bnpm\s+(?:run\s+)?test\b|\byarn\s+test\b|\bpnpm\s+(?:run\s+)?test\b|\bnode\s+--test\b|\bcargo\s+test\b"
    r"|\bgo\s+test\b)"
)
# Ejecutores sueltos: solo cuentan como palabra en posición de comando (FW-352). Un programa que los
# nombra como argumento (`python3 herramienta.py pytest`) no lanza ningún test.
_BARE_RUNNERS = {"pytest", "py.test", "vitest", "jest", "tox"}
_ENV_ASSIGN_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*=")
_PYTHON_RE = re.compile(r"(?:python|pypy)[0-9.]*")
_VARIABLE_RE = re.compile(r"\$(?:[A-Za-z_][A-Za-z0-9_]*|\{[A-Za-z_][A-Za-z0-9_]*\})")  # `$PY`, `${PY}`
_M_FLAG_RE = re.compile(r"-[A-Za-z]*m")
_SHELLS = {"bash", "sh", "zsh", "dash"}
_SHELL_C_RE = re.compile(r"-[A-Za-z]*c")
_SHELL_KEYWORDS = {"do", "then", "else", "elif", "if", "while", "until", "!", "{"}
# Envoltorios que se saltan para llegar al comando; el valor es la lista de sus subcomandos opcionales.
_WRAPPERS: dict[str, frozenset[str]] = {
    "env": frozenset(), "time": frozenset(), "sudo": frozenset(), "nohup": frozenset(), "exec": frozenset(),
    "command": frozenset(), "nice": frozenset(), "xargs": frozenset(), "timeout": frozenset(),
    "npx": frozenset(), "uvx": frozenset(), "bunx": frozenset(), "coverage": frozenset({"run"}),
    "uv": frozenset({"run", "tool"}), "poetry": frozenset({"run"}), "pipenv": frozenset({"run"}),
    "pdm": frozenset({"run"}), "rye": frozenset({"run"}), "hatch": frozenset({"run"}), "conda": frozenset({"run"}),
    "pnpm": frozenset({"exec", "dlx"}), "yarn": frozenset({"run", "exec", "dlx"}), "npm": frozenset({"exec"}),
    "bun": frozenset({"x"}),
}
# Envoltorios que ejecutan lo que sigue en otro entorno. El valor son los verbos de ejecución que deben
# aparecer (vacío: cualquiera), para que `docker build -t pytest .` o `kubectl get pods` no cuenten.
_ENV_WRAPPERS: dict[str, frozenset[str]] = {
    "docker": frozenset({"exec", "run"}), "podman": frozenset({"exec", "run"}), "nerdctl": frozenset({"exec", "run"}),
    "kubectl": frozenset({"exec"}), "oc": frozenset({"exec"}), "nix": frozenset({"develop", "shell", "run"}),
    "ssh": frozenset(), "mosh": frozenset(),
}
# Opciones con valor de cada envoltorio de entorno (las demás se toman como interruptores). Si falta alguna, el
# valor se toma por el destino y el comando real por una palabra cualquiera: no se marca (queda corto a propósito).
_ENV_VALUE_OPTS: dict[str, frozenset[str]] = {
    "docker": frozenset({"-e", "--env", "-u", "--user", "-w", "--workdir", "-v", "--volume", "--name", "--network",
                         "-p", "--publish", "--entrypoint", "-l", "--label", "--env-file", "--add-host", "--platform",
                         "-h", "--hostname", "--mount", "--cpus", "--memory", "-m", "--context", "-H", "--host",
                         "--detach-keys", "--pid", "--ipc", "--restart", "--tmpfs", "--device", "--group-add"}),
    "kubectl": frozenset({"-n", "--namespace", "-c", "--container", "--context", "--kubeconfig", "-f", "--filename",
                          "-l", "--selector", "--request-timeout", "-s", "--server", "--user", "--cluster"}),
    "nix": frozenset({"-c", "--command", "--run", "--profile", "--override-input", "-f", "--file"}),
    "ssh": frozenset({"-p", "-i", "-l", "-o", "-F", "-J", "-L", "-R", "-D", "-b", "-c", "-E", "-e", "-m", "-O", "-S",
                      "-W", "-w", "-B", "-I", "-Q"}),
    "mosh": frozenset({"-p", "--ssh", "--port", "--server", "--predict"}),
}
_ENV_VALUE_OPTS["podman"] = _ENV_VALUE_OPTS["nerdctl"] = _ENV_VALUE_OPTS["docker"]
_ENV_VALUE_OPTS["oc"] = _ENV_VALUE_OPTS["kubectl"]
_WRAPPER_VALUE_OPTS = {"-u", "-n", "-g", "-C", "-W", "-X", "--with", "--python", "--project", "--directory"}
_WRAPPER_POSITIONALS = {"timeout": 1}  # `timeout 60 pytest`: la duración no es el comando
_COMMAND_WORDS = 16  # basta mirar el principio de cada etapa: el coste no depende del tamaño de los argumentos
_MAX_SHELL_DEPTH = 3  # `bash -c '...'` dentro de `bash -c '...'`
# Troceado respetando comillas: un separador dentro de comillas no corta. Todo es posesivo y cada
# alternativa empieza por un carácter distinto, así que el coste es lineal; una comilla sin cerrar
# se toma como un carácter más.
_SHELL_TOKEN_RE = re.compile(
    r"""(?P<sep>&&|\|\||[;\n])
    |(?P<pipe>[|&()])
    |(?P<space>[^\S\n]++)
    |(?P<word>(?:[^\s;|&()'"\\$]++|\$\{[^}\s]{0,128}\}|'[^']*+'|"(?:[^"\\]|\\.)*+"|\\.|[$'"\\])++)""",
    re.VERBOSE | re.DOTALL,
)
# `pytest.main(` / `unittest.main(` dentro de un programa de python pasado con `-c` o por la entrada estándar
# (heredoc o `-`): lo lanza el intérprete. Buscarlo con grep o escribirlo en un fichero no cuenta.
_PY_MAIN_RE = re.compile(r"\b(?:pytest|unittest)\.main\s*\(")
_UNQUOTE_RE = re.compile(r"""\\(.)|['"]""", re.DOTALL)
_NOT_RUN = {"grep", "rg", "echo", "cat", "sed", "ls", "head", "tail", "git", "printf", "awk", "which", "man"}
_FAIL_RE = re.compile(
    r"(?m)^(?:FAILED|ERROR)\b|\bFAILED\s*\(|\b(?!0+\s)\d+\s+(?:failed|errors?)\b|^FAIL\b|AssertionError|Tests?:\s+(?!0+\s)\d+\s+failed|^Exit code [1-9]"
)
_FAIL_ID_RE = re.compile(r"(?m)^(?:FAILED|ERROR) (\S+)|^(?:FAIL|ERROR): (\S+)")


def _unquote(word: str) -> str:
    """Valor de una palabra del shell: sin comillas ni barras de escape."""
    if "'" not in word and '"' not in word and "\\" not in word:
        return word
    return _UNQUOTE_RE.sub(lambda m: m.group(1) or "", word)


def _inline_python(words: list[str]) -> tuple[str, str] | None:
    """Programa inline de una etapa que es un intérprete de python: («code», texto) con `-c`, («heredoc»,
    delimitador) si lo lee de un heredoc, o None si no hay programa inline (hay un script, o no es python)."""
    index = 0
    while index < len(words) and (_ENV_ASSIGN_RE.match(words[index]) or words[index] in _SHELL_KEYWORDS):
        index += 1
    if index >= len(words) or not (_PYTHON_RE.fullmatch(words[index].rsplit("/", 1)[-1])
                                   or _VARIABLE_RE.fullmatch(words[index])):
        return None
    index += 1
    while index < len(words):
        word = words[index]
        if word.startswith("<<") and not word.startswith("<<<"):
            return "heredoc", word[2:].lstrip("-").strip()
        if word == "-":
            # `python - arg <<'EOF'`: lo que sigue son argumentos del programa; solo importa el heredoc.
            return next((("heredoc", w[2:].lstrip("-").strip()) for w in words[index + 1:]
                         if w.startswith("<<") and not w.startswith("<<<")), None)
        elif word.startswith("-") and not word.startswith("--"):
            if re.fullmatch(r"-[A-Za-z]*c", word) and index + 1 < len(words):
                return "code", words[index + 1]
            index += 2 if word in _WRAPPER_VALUE_OPTS else 1
        elif word.startswith("--"):
            index += 1
        else:
            return None  # un script: la entrada estándar no es el programa
    return None


def _note_inline_python(words: list[str], delimiters: list[str], position: int,
                        heredocs: list[tuple[int, str, bool]]) -> bool:
    """True si la etapa es `python -c "...pytest.main(..."`; anota los heredocs de la etapa para resolverlos luego."""
    inline = _inline_python(words) if words else None
    if inline and inline[0] == "code" and _PY_MAIN_RE.search(inline[1]):
        return True
    python_delimiter = inline[1] if inline and inline[0] == "heredoc" else None
    for delimiter in delimiters:
        if delimiter:
            heredocs.append((position, delimiter, delimiter == python_delimiter))
    return False


def _heredocs_call_main(command: str, heredocs: list[tuple[int, str, bool]]) -> bool:
    """True si el cuerpo de algún heredoc de un intérprete de python contiene `pytest.main(` o `unittest.main(`.

    `heredocs` son (fin de la etapa, delimitador, si lo lee un intérprete de python) en orden de aparición. Los
    cuerpos de los heredocs de una misma línea van uno tras otro, así que se recorren en una sola pasada: cada
    cuerpo se examina una vez, aunque haya miles de heredocs."""
    cursor = 0
    for after, delimiter, is_python in heredocs:
        body_start = cursor if after < cursor else command.find("\n", after) + 1
        if body_start <= 0:
            break  # no queda ningún salto de línea: ni este heredoc ni los siguientes tienen cuerpo
        closing = _heredoc_closing(command, delimiter, body_start)
        body_end = closing[0] if closing else len(command)
        if is_python and _PY_MAIN_RE.search(command, body_start, body_end):
            return True
        cursor = closing[1] + 1 if closing else len(command)
    return False


def _heredoc_closing(command: str, delimiter: str, start: int) -> tuple[int, int] | None:
    """(inicio, fin) de la línea que cierra el heredoc: el delimitador solo, con blancos alrededor.

    `start` es un comienzo de línea. Cada línea se mira una vez; sin expresión regular por delimitador."""
    if not delimiter:
        return None
    position = start
    while True:
        found = command.find(delimiter, position)
        if found < 0:
            return None
        line_start = max(start, command.rfind("\n", position, found) + 1, position)
        line_end = command.find("\n", found)
        if line_end < 0:
            line_end = len(command)
        if not command[line_start:found].strip(" \t") and not command[found + len(delimiter):line_end].strip(" \t"):
            return line_start, line_end
        position = line_end + 1
        if position > len(command):
            return None


def _remote_command_start(wrapper: str, words: list[str], index: int) -> int | None:
    """Dónde empieza el comando que un envoltorio de entorno ejecuta en otro sitio, o None si no se sabe.

    Tras el verbo (`exec`, `run`...) y las opciones, la primera palabra suelta es el destino (host, contenedor,
    imagen, pod) y lo siguiente es el comando; `--` y, con nix, `-c` lo marcan. Con una opción con valor que no
    se conoce, el valor se toma por destino y el comando queda mal situado: no se marca antes que marcar de más."""
    verbs = _ENV_WRAPPERS[wrapper]
    value_opts = _ENV_VALUE_OPTS.get(wrapper, frozenset())
    if verbs:
        verb_at = next((i for i in range(index, min(index + 4, len(words))) if words[i] in verbs), None)
        if verb_at is None:
            return None
        index = verb_at + 1
    destination = False
    while index < len(words):
        word = words[index]
        if wrapper == "nix":
            if word in ("-c", "--command"):
                return index + 1
            index += 1
        elif word == "--":
            if destination:
                return index + 1
            index += 1  # `ssh -- host cmd`: el destino viene después
        elif word.startswith("-") and word != "-":
            index += 2 if word in value_opts else 1
        elif not destination:
            destination = True
            index += 1
        else:
            return index
    return None


def _stage_runs_test(words: list[str], depth: int) -> bool:
    """True si el comando de una etapa lanza un test con un ejecutor suelto.

    `words` son los valores de sus primeras palabras. Se saltan asignaciones de entorno, palabras
    clave del shell y envoltorios; un intérprete de python (por nombre base) o una variable
    (`$PY`) cuentan con `-m pytest` o `-m unittest`, con cualquier opción antes de `-m`. Tras un envoltorio
    que ejecuta dentro de otro entorno (`docker exec`, `ssh`...) se busca el ejecutor en el resto de la etapa."""
    index = 0
    skip_positionals = 0
    while index < len(words):
        word = re.split(r"[<>]", words[index], maxsplit=1)[0]
        index += 1
        if not word or _ENV_ASSIGN_RE.match(word) or word in _SHELL_KEYWORDS:
            continue
        if skip_positionals:
            skip_positionals -= 1
            continue
        base = word.rsplit("/", 1)[-1]
        if base in _BARE_RUNNERS:
            return True
        if base in _SHELLS and depth < _MAX_SHELL_DEPTH:
            # `bash -c 'cd app && pytest'`: el guion es una orden entera, se evalúa aparte.
            while index < len(words) and words[index].startswith("-"):
                index += 1
                if _SHELL_C_RE.fullmatch(words[index - 1]) and index < len(words):
                    return _is_test_command(words[index], depth + 1)
            return False
        if base in _ENV_WRAPPERS:
            # Una sola pasada: se localiza dónde empieza el comando remoto y se evalúa ese sufijo, sin probar
            # otras posiciones (las sondas no reabren la búsqueda).
            start = _remote_command_start(base, words, index)
            return start is not None and _stage_runs_test(words[start:], depth)
        variable = bool(_VARIABLE_RE.fullmatch(word))
        interpreter = variable or bool(_PYTHON_RE.fullmatch(base))
        if interpreter or base in _WRAPPERS:
            subcommands = _WRAPPERS.get(base, frozenset())
            skip_positionals = _WRAPPER_POSITIONALS.get(base, 0)
            module_flag = False
            # Opciones del envoltorio (y el valor de las que lo llevan) y su subcomando, si lo tiene.
            while index < len(words):
                option = words[index]
                if option in subcommands:
                    index += 1
                elif interpreter and _M_FLAG_RE.fullmatch(option):
                    module_flag = True
                    if index + 1 < len(words) and words[index + 1].rsplit("/", 1)[-1] == "unittest":
                        return True
                    index += 1  # el módulo es el comando que sigue
                    break
                elif option.startswith("-") and option != "-":
                    index += 2 if option in _WRAPPER_VALUE_OPTS else 1
                else:
                    break
            if variable and not module_flag:
                return False  # una variable sin `-m` puede ser cualquier cosa
            continue
        return False
    return False


def _is_test_command(command: str, depth: int = 0) -> bool:
    """True si la orden lanza una suite de tests (la regla está en FW-352; el límite conocido son los
    `pytest.main([...])` dentro de un script de python)."""
    segment_start = 0
    first: str | None = None  # primera palabra del segmento: si es de `_NOT_RUN`, el segmento no lanza nada
    hit = False
    words: list[str] = []
    has_main = _PY_MAIN_RE.search(command) is not None
    heredocs: list[tuple[int, str, bool]] = []  # (dónde acaba la etapa, delimitador, lo lee python) en orden
    delimiters: list[str] = []  # heredocs de la etapa en curso (también los de `cat`: ocupan su sitio en el orden)
    for match in _SHELL_TOKEN_RE.finditer(command):
        kind = match.lastgroup
        if kind == "space":
            continue
        if kind == "word":
            raw = match.group()
            if has_main and raw.startswith("<<") and not raw.startswith("<<<"):
                delimiters.append(_unquote(raw)[2:].lstrip("-").strip())
            if first is None:
                first = _unquote(raw)
                words.append(first)
            elif first not in _NOT_RUN and len(words) < _COMMAND_WORDS:
                words.append(_unquote(raw))
            continue
        if words and not hit and first not in _NOT_RUN:
            hit = _stage_runs_test(words, depth)
        if has_main and (words or delimiters) and _note_inline_python(words, delimiters, match.start(), heredocs):
            return True
        words = []
        delimiters = []
        if kind == "sep":
            if first is not None and first not in _NOT_RUN and (
                hit or _TEST_CMD_RE.search(command[segment_start:match.start()])
            ):
                return True
            segment_start = match.end()
            first = None
            hit = False
    if words and not hit and first not in _NOT_RUN:
        hit = _stage_runs_test(words, depth)
    if has_main and (words or delimiters) and _note_inline_python(words, delimiters, len(command), heredocs):
        return True
    if heredocs and _heredocs_call_main(command, heredocs):
        return True
    return first is not None and first not in _NOT_RUN and (hit or bool(_TEST_CMD_RE.search(command[segment_start:])))


def _norm_command(command: str) -> str:
    text = " ".join(command.split())
    text = re.sub(r"^cd\s+(?:\"[^\"]*\"|'[^']*'|\S+)\s*&&\s*", "", text)
    text = re.sub(r"\s*2>&1", "", text)
    text = re.sub(r"\s*\|\s*(?:tail|head|grep|tee|wc)\b.*$", "", text)
    return text.strip().rstrip("&").strip()


def _signature(text: str) -> str:
    ids = sorted({a or b for a, b in _FAIL_ID_RE.findall(text)})
    if ids:
        return "ids:" + ",".join(ids)
    tail = [ln.strip() for ln in text.splitlines() if ln.strip()][-2:]
    return "cola:" + re.sub(r"\d+(?:\.\d+)?", "#", " | ".join(tail))


def _chains(results: list[tuple[bool, str]]) -> tuple[list[int], list[int]]:
    """Cadenas de rojos consecutivos de un mismo comando: todas, y las de MISMA firma de fallo."""
    any_c: list[int] = []
    same_c: list[int] = []
    run = same = 0
    prev: str | None = None
    for failed, sig in results + [(False, "")]:
        if failed:
            run += 1
            if sig == prev:
                same += 1
            else:
                if same:
                    same_c.append(same)
                same = 1
            prev = sig
        else:
            if run:
                any_c.append(run)
            if same:
                same_c.append(same)
            run = same = 0
            prev = None
    return any_c, same_c


def trailing_same(results: list[tuple[bool, str]]) -> tuple[int, str]:
    """Rojos seguidos con la MISMA firma al final de la serie (0 si lo último fue verde)."""
    count = 0
    signature = ""
    for failed, sig in reversed(results):
        if not failed or (count and sig != signature):
            break
        signature = sig
        count += 1
    return count, signature


def analyze_loops(path: Path) -> dict[str, Any]:
    return analyze_loop_events(claude_events(path))


def analyze_loop_events(events: Iterable[dict[str, Any]]) -> dict[str, Any]:
    uses: dict[str, tuple[str, dict[str, Any]]] = {}
    runs: dict[str, list[tuple[bool, str]]] = collections.defaultdict(list)
    edits: dict[str, list[tuple[str, str]]] = collections.defaultdict(list)
    writes: dict[str, list[str]] = collections.defaultdict(list)
    reverts: dict[str, int] = collections.defaultdict(int)
    last_command = ""
    for ev in events:
        if ev["t"] == "tool_use":
            uses[ev["id"]] = (ev["name"], ev["input"] if isinstance(ev["input"], dict) else {})
        elif ev["t"] == "tool_result":
            name, data = uses.get(ev["id"], ("?", {}))
            if name == "Bash":
                command = str(data.get("command") or "")
                if _is_test_command(command):
                    failed = ev["error"] or bool(_FAIL_RE.search(ev["text"]))
                    last_command = _norm_command(command)
                    runs[last_command].append((failed, _signature(ev["text"]) if failed else ""))
            elif name in ("Edit", "MultiEdit", "Write") and not ev["error"]:
                target = str(data.get("file_path") or "")
                if not target:
                    continue
                if name == "Write":
                    body = str(data.get("content") or "")
                    previous = writes[target]
                    if previous and body != previous[-1] and body in previous[:-1]:
                        reverts[target] += 1
                    previous.append(body)
                    continue
                pairs = [(str(e.get("old_string") or ""), str(e.get("new_string") or ""))
                         for e in (data.get("edits") or [])] if name == "MultiEdit" \
                    else [(str(data.get("old_string") or ""), str(data.get("new_string") or ""))]
                for old, new in pairs:
                    if old == new:
                        continue
                    history = edits[target]
                    if (new, old) in history:
                        history.remove((new, old))  # cada edición se revierte una sola vez
                        reverts[target] += 1
                    else:
                        history.append((old, new))
    chains_any: list[int] = []
    chains_same: list[int] = []
    worst: list[dict[str, Any]] = []
    # La racha en curso es la del último test ejecutado: un bucle que ya quedó atrás no cuenta (FW-351).
    count, signature = trailing_same(runs.get(last_command, []))
    trailing = {"count": count, "command": last_command if count else "", "signature": signature}
    for command, results in runs.items():
        any_c, same_c = _chains(results)
        chains_any += any_c
        chains_same += same_c
        if max(any_c, default=0) >= 2:
            worst.append({"command": command[:140], "max_rojos_seguidos": max(any_c),
                          "max_misma_firma": max(same_c, default=0)})
    return {
        "chains_any": chains_any, "chains_same": chains_same, "worst": worst,
        "reverts": {k: v for k, v in reverts.items() if v}, "trailing": trailing,
    }


def _hist(values: list[int]) -> dict[str, int]:
    counts = collections.Counter(values)
    return {str(k): counts[k] for k in sorted(counts)}


def cmd_loops(args: argparse.Namespace) -> int:
    since = _parse_since(args.since)
    files = claude_files(Path(args.claude_dir).expanduser(), _projects(args, default_cwd=False), since)
    threshold = args.threshold
    all_any: list[int] = []
    all_same: list[int] = []
    revert_counts: list[int] = []
    flagged: list[dict[str, Any]] = []
    n_sessions = 0
    sess_red = sess_rev = 0
    for path, cwd in files:
        info = analyze_loops(path)
        n_sessions += 1
        all_any += info["chains_any"]
        all_same += info["chains_same"]
        revert_counts += list(info["reverts"].values())
        max_any = max(info["chains_any"], default=0)
        max_same = max(info["chains_same"], default=0)
        max_rev = max(info["reverts"].values(), default=0)
        sess_red += max_any >= threshold
        sess_rev += max_rev >= threshold
        if max_any >= threshold or max_rev >= threshold:
            flagged.append({
                "session_id": path.stem, "cwd": cwd, "max_rojos_seguidos": max_any, "max_misma_firma": max_same,
                "max_reverts_fichero": max_rev,
                "comandos": sorted(info["worst"], key=lambda w: -w["max_rojos_seguidos"])[:3],
                "ficheros": [{"file": k, "reverts": v} for k, v in sorted(info["reverts"].items(), key=lambda kv: -kv[1])[:3]],
            })
    flagged.sort(key=lambda f: -(max(f["max_rojos_seguidos"], f["max_reverts_fichero"])))
    data = {
        "umbral": threshold, "sesiones": n_sessions,
        "sesiones_con_tests_rojos_seguidos": sess_red, "sesiones_con_ediciones_revertidas": sess_rev,
        "distribucion_rojos_seguidos": _hist(all_any), "distribucion_rojos_misma_firma": _hist(all_same),
        "distribucion_reverts_por_fichero": _hist(revert_counts), "marcadas": flagged,
        "nota": "Cadena de rojos = ejecuciones consecutivas del MISMO comando de test que fallan (un verde la corta; "
                "las ediciones intermedias no). 'Misma firma' exige además los mismos tests fallidos (o la misma cola). "
                "Revert = Edit B->A tras A->B, o Write que vuelve a un contenido anterior. Solo Claude Code.",
    }
    text = [f"Bucles en {n_sessions} sesiones (umbral {threshold}): {sess_red} con rojos seguidos, {sess_rev} con ediciones revertidas",
            "Distribución de cadenas de rojos (longitud: nº de cadenas): " + json.dumps(data["distribucion_rojos_seguidos"]),
            "  con la misma firma de fallo: " + json.dumps(data["distribucion_rojos_misma_firma"]),
            "Distribución de reverts por fichero (nº: ficheros): " + json.dumps(data["distribucion_reverts_por_fichero"])]
    if flagged:
        text += ["", _render([[f["session_id"][:8], f["max_rojos_seguidos"], f["max_misma_firma"], f["max_reverts_fichero"],
                               (f["comandos"][0]["command"][:60] if f["comandos"] else
                                (f["ficheros"][0]["file"][-60:] if f["ficheros"] else ""))] for f in flagged[:args.limit]],
                              ["sesión", "rojos", "misma_firma", "reverts", "detalle"])]
    return _emit(args, data, "\n".join(text))


# --------------------------------------------------------------- module-cost


class Activity:
    """Actividad de un proyecto (Claude + Codex) ordenada por hora, para cruzar con intervalos."""

    def __init__(self) -> None:
        self.stamps: list[datetime] = []
        self.prompts: list[datetime] = []
        self.usage: list[tuple[datetime, str, int, int]] = []  # (hora, raiz|sub, entrada_total, salida)

    def sort(self) -> None:
        self.stamps.sort()
        self.prompts.sort()
        self.usage.sort(key=lambda u: u[0])
        self._ut = [u[0] for u in self.usage]

    def window(self, start: datetime, end: datetime) -> dict[str, Any]:
        lo, hi = bisect.bisect_left(self.stamps, start), bisect.bisect_right(self.stamps, end)
        active = 0.0
        for a, b in zip(self.stamps[lo:hi], self.stamps[lo + 1:hi]):
            gap = (b - a).total_seconds()
            if 0 < gap <= sc.IDLE_GAP_MIN * 60:
                active += gap
        pl, ph = bisect.bisect_left(self.prompts, start), bisect.bisect_right(self.prompts, end)
        ul, uh = bisect.bisect_left(self._ut, start), bisect.bisect_right(self._ut, end)
        tot = {"raiz": [0, 0], "sub": [0, 0]}
        for _, scope, inp, out in self.usage[ul:uh]:
            tot[scope][0] += inp
            tot[scope][1] += out
        return {"active_min": round(active / 60, 1), "turns": ph - pl,
                "input_root": tot["raiz"][0], "output_root": tot["raiz"][1],
                "input_sub": tot["sub"][0], "output_sub": tot["sub"][1]}


def _claude_usage_events(path: Path, scope: str) -> list[tuple[datetime, str, int, int]]:
    """Mensajes deduplicados por id: hora de la PRIMERA aparición, uso con salida estimada si es provisional (FW-347)."""
    first_ts: dict[str, datetime] = {}
    msgs: dict[str, tuple[str, dict[str, int]]] = {}
    seen_uuid: set[str] = set()
    for obj in sc._iter_json(path):
        if obj.get("type") != "assistant":
            continue
        ts = sc._ts(obj.get("timestamp"))
        if ts is None:
            continue
        key = str((obj.get("message") or {}).get("id") or obj.get("uuid") or f"#{len(msgs)}")
        sc._record_message(msgs, obj, seen_uuid)
        if key in msgs:
            first_ts.setdefault(key, ts)
    return [(first_ts[k], scope, v[1]["input_total"], v[1]["output"]) for k, v in msgs.items() if k in first_ts]


def load_activity(project: str, claude_dir: Path, codex_home: Path, since: datetime | None) -> Activity:
    act = Activity()
    for path, _cwd in claude_files(claude_dir, [project], since):
        act.stamps += [t for t in (sc._ts(o.get("timestamp")) for o in sc._iter_json(path)) if t]
        seen_prompt: set[str] = set()
        for obj in sc._iter_json(path):
            uid = obj.get("uuid")
            if obj.get("type") != "user" or obj.get("isMeta") or obj.get("isCompactSummary") or obj.get("isSidechain"):
                continue
            if uid and str(uid) in seen_prompt:
                continue
            content = (obj.get("message") or {}).get("content")
            blocks = content if isinstance(content, list) else []
            if isinstance(content, str) or any(isinstance(b, dict) and b.get("type") == "text" for b in blocks):
                ts = sc._ts(obj.get("timestamp"))
                if ts:
                    act.prompts.append(ts)
                    if uid:
                        seen_prompt.add(str(uid))
        act.usage += _claude_usage_events(path, "raiz")
        subdir = path.with_suffix("") / "subagents"
        if subdir.is_dir():
            for sub in sorted(subdir.glob("agent-*.jsonl")):
                act.stamps += [t for t in (sc._ts(o.get("timestamp")) for o in sc._iter_json(sub)) if t]
                act.usage += _claude_usage_events(sub, "sub")
    if codex_home.is_dir():
        for path, meta in codex_files(codex_home, [project]):
            scope = "sub" if meta.get("thread_source") in ("subagent", "guardian_review") else "raiz"
            prev_total = None
            for obj in sc._iter_json(path):
                ts = sc._ts(obj.get("timestamp"))
                if ts is None:
                    continue
                act.stamps.append(ts)
                payload = obj.get("payload") if isinstance(obj.get("payload"), dict) else {}
                if obj.get("type") != "event_msg":
                    continue
                if payload.get("type") == "task_started" and scope == "raiz":
                    act.prompts.append(ts)
                elif payload.get("type") == "token_count":
                    info = payload.get("info") or {}
                    total = (info.get("total_token_usage") or {}).get("total_tokens")
                    if total is not None and total == prev_total:
                        continue
                    prev_total = total
                    last = info.get("last_token_usage") or {}
                    if last:
                        act.usage.append((ts, scope, int(last.get("input_tokens") or 0), int(last.get("output_tokens") or 0)))
    act.sort()
    return act


def cmd_module_cost(args: argparse.Namespace) -> int:
    since = _parse_since(args.since)
    claude_dir = Path(args.claude_dir).expanduser()
    codex_home = Path(args.codex_home).expanduser()
    result: list[dict[str, Any]] = []
    text: list[str] = []
    for project in _projects(args, default_cwd=True):
        log = read_state_log(project)
        intervals = [i for i in module_intervals(log) if not since or i["end"] >= since]
        if not intervals:
            text.append(f"{project}: sin módulos cerrados{' desde ' + args.since if args.since else ''} en el state_log.")
            result.append({"project": project, "modulos": []})
            continue
        lo = min((i["start"] or i["end"]) for i in intervals)
        act = load_activity(project, claude_dir, codex_home, lo)
        modules = []
        for it in intervals:
            row: dict[str, Any] = {"module": it["module"], "start": it["start"].isoformat() if it["start"] else None,
                                   "end": it["end"].isoformat()}
            if it["start"]:
                row["wall_min"] = round((it["end"] - it["start"]).total_seconds() / 60, 1)
                row.update(act.window(it["start"], it["end"]))
            else:
                row["nota"] = "cierre sin ciclo_started previo: sin intervalo"
            modules.append(row)
        result.append({"project": project, "modulos": modules})
        rows = [[m["module"][:34], (m["start"] or "")[:16].replace("T", " "), m.get("wall_min", "-"), m.get("active_min", "-"),
                 m.get("turns", "-"), sc._fmt_m(m.get("input_root", 0)), sc._fmt_m(m.get("output_root", 0)),
                 sc._fmt_m(m.get("input_sub", 0)), sc._fmt_m(m.get("output_sub", 0))] for m in modules[-args.limit:]]
        text += [f"{project}: {len(modules)} módulos cerrados (últimos {min(args.limit, len(modules))})",
                 _render(rows, ["módulo", "inicio", "pared_min", "activos_min", "turnos", "in_raíz", "out_raíz", "in_sub", "out_sub"])]
        walls = [m["wall_min"] for m in modules if "wall_min" in m]
        if walls:
            text.append(f"mediana pared {_median(walls):.0f} min; mediana activos "
                        f"{_median([m['active_min'] for m in modules if 'active_min' in m]):.0f} min")
        text.append("")
    note = ("Intervalo = primer ciclo_started del módulo tras su cierre anterior .. complete_module. Tokens: entrada = "
            "input+caché (Claude: deduplicado por message.id; Codex: input_tokens). Sesiones por cwd dentro del proyecto "
            "y por hora; módulos en paralelo se solapan y cada uno recibe toda la actividad de su ventana.")
    return _emit(args, {"nota": note, "proyectos": result}, "\n".join(text).rstrip() + "\n\n" + note)


# -------------------------------------------------------------------- receipts

_APPROVAL_ACTIONS = ("record_approval_receipt", "refresh_approval_receipt")


def analyze_receipts(log: list[dict[str, Any]], since: datetime) -> dict[str, Any]:
    intervals = [i for i in module_intervals(log) if i["end"] >= since]
    refresh_total = sum(1 for e in log if e.get("action") == "refresh_approval_receipt" and e["_ts"] >= since)
    modules = []
    for it in intervals:
        counts = collections.Counter()
        for ev in log:
            payload = ev.get("payload") or {}
            if str(payload.get("module") or "") != it["module"]:
                continue
            if ev["_ts"] > it["end"] or (it["prev_close"] and ev["_ts"] <= it["prev_close"]):
                continue
            counts[ev.get("action")] += 1
        approval = counts["record_approval_receipt"] + counts["refresh_approval_receipt"]
        review = counts["record_review_receipt"]
        modules.append({
            "module": it["module"], "closed": it["end"].isoformat(),
            "approval_record": counts["record_approval_receipt"], "approval_refresh": counts["refresh_approval_receipt"],
            "approval_seals": approval, "review_seals": review, "review_invalidated": counts["invalidate_stale_review"],
            "approval_resealed": approval > 1, "review_resealed": review > 1,
        })
    return {
        "since": since.date().isoformat(), "modulos_cerrados": len(modules),
        "refresh_approval_receipt_acciones": refresh_total,
        "modulos_con_aprobacion_sellada_mas_de_una_vez": sum(m["approval_resealed"] for m in modules),
        "modulos_con_revision_sellada_mas_de_una_vez": sum(m["review_resealed"] for m in modules),
        "modulos": modules,
    }


def cmd_receipts(args: argparse.Namespace) -> int:
    since = _parse_since(args.since or DEFAULT_RECEIPTS_SINCE)
    assert since is not None
    result = []
    text = []
    for project in _projects(args, default_cwd=True):
        data = analyze_receipts(read_state_log(project), since)
        data["project"] = project
        result.append(data)
        text.append(f"{project} (desde {data['since']}): {data['modulos_cerrados']} módulos cerrados; "
                    f"{data['refresh_approval_receipt_acciones']} acciones refresh_approval_receipt; "
                    f"aprobación sellada >1 vez en {data['modulos_con_aprobacion_sellada_mas_de_una_vez']}, "
                    f"revisión sellada >1 vez en {data['modulos_con_revision_sellada_mas_de_una_vez']}")
        worst = sorted(data["modulos"], key=lambda m: -(m["approval_seals"] + m["review_seals"]))[:args.limit]
        if worst:
            text.append(_render([[m["module"][:36], m["approval_record"], m["approval_refresh"], m["review_seals"],
                                  m["review_invalidated"]] for m in worst],
                                ["módulo (más sellados)", "aprob.record", "aprob.refresh", "revisión", "rev.invalidada"]))
        text.append("")
    note = ("Sellado de aprobación = record_approval_receipt + refresh_approval_receipt; de revisión = "
            "record_review_receipt (payload.module). Se cuentan los eventos del módulo entre su cierre anterior y su cierre.")
    return _emit(args, {"nota": note, "proyectos": result}, "\n".join(text).rstrip() + "\n\n" + note)


# ----------------------------------------------------------- independent-modules

_GLOB_CHARS = set("*?[{")


def parse_file_map(text: str) -> list[dict[str, Any]]:
    """Parsea `_project/11_file_dependencies.md`.

    Cada encabezado `##`/`###` (con o sin el prefijo `Módulo:`) abre una entrada
    candidata; solo se conservan las que traen alguna lista `lee:` / `escribe:` /
    `escribe_previsto:` (el resto son secciones del documento). La fase sale de
    `**Fase:** x` o `**Fase propuesta:** x` (se ignora la plantilla `phase_XX`).
    Cada viñeta aporta su ruta: el primer `código` o, sin él, el texto hasta el comentario (`# …`, dos espacios, `—` o `(`). Solo se entienden las listas con viñetas bajo `lee:`/`escribe:`; el formato libre (`**Lee:** …` en prosa, como el de Example Tool) no se interpreta."""
    modules: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    section = ""
    for line in text.splitlines():
        head = re.match(r"^#{2,3}\s+(?:M[óo]dulo:\s*)?(\S+)", line)
        if head:
            ident = head.group(1).strip("`")
            current = None if ident.startswith("[") else {"id": ident, "phase": "", "lee": [], "escribe": [], "_keys": 0}
            if current:
                modules.append(current)
            section = ""
            continue
        if current is None:
            continue
        phase = re.match(r"^\*\*Fase(?: propuesta)?:\*\*\s*(\S+)", line)
        if phase:
            if phase.group(1) != "phase_XX":
                current["phase"] = phase.group(1)
            continue
        key = re.match(r"^([a-z_]+):\s*$", line)
        if key:
            section = {"lee": "lee", "escribe": "escribe", "escribe_previsto": "escribe"}.get(key.group(1), "")
            if section:
                current["_keys"] += 1
            continue
        if line.startswith("**"):
            section = ""
            continue
        if section:
            bullet = re.match(r"^\s*[-*]\s+(?:`([^`]+)`|(.+?))\s*(?:\s#.*)?$", line)
            if bullet:
                raw = (bullet.group(1) or bullet.group(2) or "").strip()
                raw = re.split(r"\s{2,}|\s[—(]", raw)[0].strip("`' ")
                if raw:
                    current[section].append(raw)
    return [{k: v for k, v in m.items() if k != "_keys"} for m in modules if m["_keys"]]


def path_prefix(raw: str) -> tuple[str, ...] | None:
    """Prefijo de componentes comparable de una ruta, o None si se descarta.

    Reglas: se quita `./`, el sufijo `:líneas` y la `/` final. Un glob (`* ? [ {`) se
    trunca en el primer componente que lo contiene (`app/api/*.py` -> `app/api`).
    Si queda vacío (`*.md`, `**/x`, `.`) la ruta es demasiado ancha y se descarta
    (no se compara: chocaría con todo). `_project/**` (estado y documentación de
    cada módulo) también se descarta."""
    text = re.sub(r":\d[\d,\- ]*$", "", raw.strip())
    text = text[2:] if text.startswith("./") else text
    parts = []
    for comp in [c for c in text.split("/") if c and c != "."]:
        if _GLOB_CHARS & set(comp):
            break
        parts.append(comp)
    if not parts or parts[0] == "_project":
        return None
    return tuple(parts)


def _overlap(a: tuple[str, ...], b: tuple[str, ...]) -> bool:
    n = min(len(a), len(b))
    return a[:n] == b[:n]


def _natural(value: str) -> list[Any]:
    return [int(t) if t.isdigit() else t for t in re.split(r"(\d+)", value)]


def analyze_independence(project: str) -> dict[str, Any]:
    root = Path(project).expanduser()
    map_path = root / "_project" / "11_file_dependencies.md"
    state_path = root / "_project" / "03_project_state.json"
    modules = parse_file_map(map_path.read_text(encoding="utf-8", errors="replace")) if map_path.is_file() else []
    state: dict[str, Any] = {}
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        pass
    known = set(state.get("module_specs") or {}) | set(state.get("module_statuses") or {})
    for names in (state.get("modules") or {}).values():
        if isinstance(names, list):
            known |= {n for n in names if isinstance(n, str)}
    in_map = {m["id"] for m in modules}
    sin_entrada = sorted(known - in_map)
    sin_rutas = sorted(m["id"] for m in modules if not m["lee"] and not m["escribe"])
    evaluables = [m for m in modules if m["lee"] or m["escribe"]]
    phases: dict[str, list[dict[str, Any]]] = collections.defaultdict(list)
    for m in evaluables:
        m["_w"] = [p for p in map(path_prefix, m["escribe"]) if p]
        m["_r"] = [p for p in map(path_prefix, m["lee"]) if p]
        phases[m["phase"] or "sin fase"].append(m)
    out_phases = []
    for phase in sorted(phases, key=_natural):
        group = phases[phase]
        conflicts: list[dict[str, str]] = []
        in_conflict: set[str] = set()
        for i, a in enumerate(group):
            for b in group[i + 1:]:
                hit = None
                for wa in a["_w"]:
                    for other, kind in [(wb, "escribe/escribe") for wb in b["_w"]] + [(rb, "escribe/lee") for rb in b["_r"]]:
                        if _overlap(wa, other):
                            hit = (kind, "/".join(wa), "/".join(other))
                            break
                    if hit:
                        break
                if not hit:
                    for rb in a["_r"]:
                        for wb in b["_w"]:
                            if _overlap(rb, wb):
                                hit = ("lee/escribe", "/".join(rb), "/".join(wb))
                                break
                        if hit:
                            break
                if hit:
                    conflicts.append({"a": a["id"], "b": b["id"], "tipo": hit[0], "ruta_a": hit[1], "ruta_b": hit[2]})
                    in_conflict |= {a["id"], b["id"]}
        out_phases.append({
            "fase": phase, "modulos": len(group), "independientes": len(group) - len(in_conflict),
            "pares_en_conflicto": len(conflicts), "conflictos": conflicts,
        })
    return {
        "project": project, "mapa": str(map_path), "mapa_existe": map_path.is_file(),
        "modulos_en_mapa": len(modules), "modulos_en_estado": len(known),
        "no_evaluables": {"sin_entrada_en_el_mapa": len(sin_entrada), "entrada_sin_lee_ni_escribe": len(sin_rutas),
                          "lista_sin_entrada": sin_entrada, "lista_sin_rutas": sin_rutas},
        "fases": out_phases,
    }


def cmd_independent_modules(args: argparse.Namespace) -> int:
    results = [analyze_independence(p) for p in _projects(args, default_cwd=True)]
    text = []
    for r in results:
        ne = r["no_evaluables"]
        text.append(f"{r['project']}: {r['modulos_en_mapa']} módulos en el mapa, {r['modulos_en_estado']} en el estado; "
                    f"no evaluables: {ne['sin_entrada_en_el_mapa']} sin entrada en el mapa + {ne['entrada_sin_lee_ni_escribe']} "
                    "con entrada sin lee ni escribe")
        text.append(_render([[f["fase"], f["modulos"], f["independientes"], f["pares_en_conflicto"]] for f in r["fases"]],
                            ["fase", "módulos", "independientes", "pares_en_conflicto"]))
        shown = 0
        for f in r["fases"]:
            for c in f["conflictos"]:
                if shown >= args.limit:
                    break
                text.append(f"  [{f['fase']}] {c['a']} x {c['b']} ({c['tipo']}: {c['ruta_a']} ~ {c['ruta_b']})")
                shown += 1
        text.append("")
    note = ("COTA SUPERIOR de independencia: el estado no declara dependencias entre módulos (module_specs no tiene "
            "escribe/lee) y solo se compara el solapamiento de ficheros del mapa 11_file_dependencies.md "
            "(escribe∩escribe o escribe∩lee, por prefijo de ruta). Se descartan rutas con glob que quedan sin prefijo, "
            "rutas anchas y _project/**. Dos módulos 'independientes' pueden depender uno de otro sin que conste.")
    return _emit(args, {"nota": note, "proyectos": results}, "\n".join(text).rstrip() + "\n\n" + note)


# ------------------------------------------------------------ codex-effort-cache


def codex_turns(path: Path) -> list[dict[str, Any]]:
    """Turnos de un rollout con su esfuerzo y la caché de su primera/última petición."""
    turns: list[dict[str, Any]] = []
    prev_total = None
    for obj in sc._iter_json(path):
        payload = obj.get("payload") if isinstance(obj.get("payload"), dict) else {}
        if obj.get("type") == "turn_context":
            effort = payload.get("effort")
            if effort is None:
                mode = payload.get("collaboration_mode")
                settings = (mode or {}).get("settings") if isinstance(mode, dict) else None
                effort = (settings or {}).get("reasoning_effort") if isinstance(settings, dict) else None
            turns.append({"turn_id": payload.get("turn_id"), "effort": effort, "model": payload.get("model"),
                          "first": None, "last": None, "requests": 0})
        elif obj.get("type") == "event_msg" and payload.get("type") == "token_count" and turns:
            info = payload.get("info") or {}
            total = (info.get("total_token_usage") or {}).get("total_tokens")
            if total is not None and total == prev_total:
                continue
            prev_total = total
            last = info.get("last_token_usage") or {}
            if not last:
                continue
            req = (int(last.get("input_tokens") or 0), int(last.get("cached_input_tokens") or 0))
            turns[-1]["first"] = turns[-1]["first"] or req
            turns[-1]["last"] = req
            turns[-1]["requests"] += 1
    return [t for t in turns if t["first"] and t["first"][0] > 0]


def _ratio(req: tuple[int, int]) -> float:
    return req[1] / req[0] if req[0] else 0.0


def analyze_effort(files: list[tuple[Path, dict[str, Any]]]) -> dict[str, Any]:
    changes: list[dict[str, Any]] = []
    control: list[float] = []
    sessions_changed: set[str] = set()
    seen_pairs: set[tuple[Any, Any]] = set()
    for path, meta in files:
        if meta.get("thread_source") == "guardian_review":
            continue
        turns = codex_turns(path)
        for prev, nxt in zip(turns, turns[1:]):
            if prev["model"] != nxt["model"] or prev["effort"] is None or nxt["effort"] is None:
                continue
            pair = (prev["turn_id"], nxt["turn_id"])
            if pair in seen_pairs:  # los hilos bifurcados copian los turnos del padre
                continue
            seen_pairs.add(pair)
            delta = _ratio(nxt["first"]) - _ratio(prev["first"])
            if prev["effort"] != nxt["effort"]:
                sessions_changed.add(str(meta.get("id") or meta.get("session_id") or path.stem))
                changes.append({
                    "session": str(meta.get("id") or meta.get("session_id") or path.stem)[:8], "model": nxt["model"],
                    "de": prev["effort"], "a": nxt["effort"],
                    "cached_antes": prev["first"][1], "cached_despues": nxt["first"][1],
                    "input_antes": prev["first"][0], "input_despues": nxt["first"][0],
                    "ratio_antes": round(_ratio(prev["first"]), 3), "ratio_despues": round(_ratio(nxt["first"]), 3),
                    "delta_ratio": round(delta, 3),
                })
            else:
                control.append(delta)
    out: dict[str, Any] = {
        "cambios": len(changes), "sesiones_con_cambio": len(sessions_changed), "pares_control": len(control),
        "control_delta_ratio_mediana": _median(control),
        "control_delta_ratio_abs_mediana": _median([abs(c) for c in control]),
        "detalle": changes,
    }
    if changes:
        d = [c["delta_ratio"] for c in changes]
        out["cambio_delta_ratio_mediana"] = _median(d)
        out["cambio_delta_ratio_media"] = round(sum(d) / len(d), 3)
        out["cambio_ratio_antes_media"] = round(sum(c["ratio_antes"] for c in changes) / len(changes), 3)
        out["cambio_ratio_despues_media"] = round(sum(c["ratio_despues"] for c in changes) / len(changes), 3)
    return out


def cmd_codex_effort_cache(args: argparse.Namespace) -> int:
    home = Path(args.codex_home).expanduser()
    files = codex_files(home, _projects(args, default_cwd=False)) if home.is_dir() else []
    since = _parse_since(args.since)
    if since:
        files = [(p, m) for p, m in files if (sc._ts(m.get("timestamp")) or since) >= since]
    data = analyze_effort(files)
    data["nota"] = ("Ratio = cached_input_tokens/input_tokens de la PRIMERA petición de cada turno (donde se nota "
                    "que el prefijo cacheado cambió). Se comparan turnos consecutivos del mismo modelo; el control son "
                    "los pares consecutivos sin cambio de esfuerzo. No se incluyen los hilos guardian_review.")
    if not data["cambios"]:
        text = [f"sin muestra: ningún cambio de esfuerzo de razonamiento dentro de una misma sesión en {len(files)} rollouts "
                f"({data['pares_control']} pares de control)."]
    else:
        text = [f"{data['cambios']} cambios de esfuerzo en {data['sesiones_con_cambio']} sesiones "
                f"(control: {data['pares_control']} pares sin cambio)",
                f"ratio de caché medio: antes {data['cambio_ratio_antes_media']} -> después {data['cambio_ratio_despues_media']}; "
                f"Δ mediana en cambios {data['cambio_delta_ratio_mediana']} (media {data['cambio_delta_ratio_media']}) "
                f"frente al control: Δ mediana {data['control_delta_ratio_mediana']}, |Δ| mediana {data['control_delta_ratio_abs_mediana']}",
                _render([[c["session"], c["model"], f"{c['de']}->{c['a']}", c["cached_antes"], c["cached_despues"],
                          c["ratio_antes"], c["ratio_despues"], c["delta_ratio"]] for c in data["detalle"][:args.limit]],
                        ["sesión", "modelo", "esfuerzo", "cached_antes", "cached_después", "ratio_antes", "ratio_después", "Δratio"])]
    text.append(data["nota"])
    return _emit(args, data, "\n".join(text))


# ------------------------------------------------------------------------ CLI

COMMANDS = {
    "norm-load": cmd_norm_load,
    "module-cost": cmd_module_cost,
    "loops": cmd_loops,
    "receipts": cmd_receipts,
    "read-weight": cmd_read_weight,
    "independent-modules": cmd_independent_modules,
    "codex-effort-cache": cmd_codex_effort_cache,
}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="session_cost.py", description="Mediciones de línea base de la 5.3.0 (solo lectura).")
    sub = parser.add_subparsers(dest="command", required=True)
    for name, func in COMMANDS.items():
        p = sub.add_parser(name, help=(func.__doc__ or "").strip() or name)
        _add_common(p, since_default=DEFAULT_RECEIPTS_SINCE if name == "receipts" else None)
        p.add_argument("--limit", type=int, default=15, help="filas máximas de la tabla de texto")
        if name == "loops":
            p.add_argument("--threshold", type=int, default=3, help="repeticiones para marcar una sesión (3)")
    args = parser.parse_args(argv)
    return COMMANDS[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
