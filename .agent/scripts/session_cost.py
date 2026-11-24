#!/usr/bin/env python3
"""Medidor de coste de sesión (solo lectura) para Codex y Claude Code.

Lee las transcripciones `.jsonl` que dejan los arneses y resume, por sesión:
duración, turnos, subagentes, minutos de espera, compactaciones, contexto
inicial y máximo, lecturas de ``GUARDRAILS_MASTER.md``/``CLAUDE.md``/
``AGENTS.md``, llamadas a ``state_manager`` y evaluaciones del revisor de
permisos de Codex. No escribe nada ni toca el repo del producto.

Extras de la 5.3.0 (L1): contexto medio por turno (``ctx_medio``), compactaciones
deduplicadas por ``uuid``, uso de tokens deduplicado por ``message.id`` y
``--by-model`` (tokens por modelo, sesión raíz y subagentes por separado). Los
subcomandos de medición (``norm-load``, ``module-cost``, ``loops``, ``receipts``,
``read-weight``, ``independent-modules``, ``codex-effort-cache``) viven en
``session_metrics.py`` y se invocan con este mismo CLI:
``session_cost.py <subcomando> [opciones]``.

Fuentes:
- Codex: ``~/.codex/{sessions,archived_sessions}/**/rollout-*.jsonl``. La sesión
  raíz y sus hijas (subagentes y revisor) comparten ``session_id``; al archivar
  una sesión, Codex mueve su raíz a ``archived_sessions``.
- Claude Code: ``~/.claude/projects/<proyecto>/*.jsonl`` (+ ``subagents/``).

El proyecto se filtra por el ``cwd`` real registrado en la transcripción, no
por el nombre de carpeta con guiones (que es ambiguo: ``a-b`` puede ser
``a/b`` o ``a-b``).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

WATCHED_FILES = ("GUARDRAILS_MASTER.md", "CLAUDE.md", "AGENTS.md")
_READ_VERBS = re.compile(r"\b(cat|sed|head|tail|less|more|nl|bat|rg|grep|awk|read)\b")
_STATE_ACTIONS = (
    ("mutate", re.compile(r"mutate_state|state_manager\.py\s+mutate")),
    ("describe", re.compile(r"describe_action|state_manager\.py\s+describe")),
    ("work_context", re.compile(r"get_work_context")),
    ("query", re.compile(r"state_manager\.py\s+(query|lookup)")),
)
_CLAUDE_WAIT_TOOLS = {"TaskOutput", "Monitor"}
IDLE_GAP_MIN = 15.0


@dataclass
class SessionCost:
    agent: str  # "codex" | "claude"
    session_id: str
    cwd: str = ""
    start: str = ""
    end: str = ""
    duration_min: float = 0.0
    active_min: float = 0.0  # suma de huecos ≤ IDLE_GAP_MIN: descarta pausas del builder
    turns: int = 0
    subagents: int = 0
    wait_min: float = 0.0
    wait_by_tool_min: dict[str, float] = field(default_factory=dict)
    compactions: int = 0
    context_initial: int = 0
    context_max: int = 0
    doc_reads: dict[str, int] = field(default_factory=dict)
    state_calls: dict[str, int] = field(default_factory=dict)
    reviewer_evals: int = 0
    files: int = 1
    # Aditivos (5.3.0 L1). ctx_medio: media de entrada+caché por mensaje del
    # asistente (deduplicado) en la sesión raíz; coincide con el pico por turno
    # en context_max, por eso no hay un campo de pico aparte.
    ctx_medio: int = 0
    # Solo con --by-model: {"root": {modelo: {...}}, "subagents": {...}, "totals": {...}}.
    by_model: dict[str, Any] = field(default_factory=dict)


def _ts(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _iter_json(path: Path) -> Iterator[dict[str, Any]]:
    try:
        with path.open(encoding="utf-8", errors="replace") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except ValueError:
                    continue
                if isinstance(obj, dict):
                    yield obj
    except OSError:
        return


def _realpath(value: str) -> str:
    try:
        return os.path.realpath(os.path.expanduser(value))
    except (OSError, ValueError):
        return value


def _cwd_matches(cwd: str, project: str | None) -> bool:
    if not project:
        return True
    if not cwd:
        return False
    want, got = _realpath(project), _realpath(cwd)
    return got == want or got.startswith(want + os.sep)


def _call_text(args: Any) -> str:
    if isinstance(args, str):
        return args
    try:
        return json.dumps(args, ensure_ascii=False)
    except (TypeError, ValueError):
        return str(args)


_USAGE_KEYS = ("input", "cache_read", "cache_creation", "output", "turns", "input_total")
_TOKEN_CHARS = 4  # estimación de tokens = caracteres / 4 (FW-347)


def _new_usage() -> dict[str, int]:
    return {key: 0 for key in _USAGE_KEYS}


def _add_usage(target: dict[str, int], other: dict[str, int]) -> None:
    for key in _USAGE_KEYS:
        target[key] += other.get(key, 0)


def _merge_by_model(into: dict[str, dict[str, int]], other: dict[str, dict[str, int]]) -> None:
    for model, usage in other.items():
        _add_usage(into.setdefault(model, _new_usage()), usage)


def _totals(by_model: dict[str, dict[str, int]]) -> dict[str, int]:
    total = _new_usage()
    for usage in by_model.values():
        _add_usage(total, usage)
    return total


def _content_chars(blocks: Any) -> int:
    """Caracteres generados por el asistente en una línea (`text`, `thinking`, `tool_use`)."""
    if not isinstance(blocks, list):
        return 0
    total = 0
    for block in blocks:
        if not isinstance(block, dict):
            continue
        btype = block.get("type")
        if btype == "text":
            total += len(str(block.get("text") or ""))
        elif btype == "thinking":
            total += len(str(block.get("thinking") or ""))
        elif btype == "tool_use":
            total += len(str(block.get("name") or "")) + len(_call_text(block.get("input") or {}))
    return total


def _claude_usage(obj: dict[str, Any]) -> tuple[str, dict[str, int]] | None:
    """(modelo, uso) de una línea `assistant` de Claude. `input_total` = entrada
    sin caché + lectura de caché + creación de caché. Mensajes sin uso (los
    `<synthetic>` de error) devuelven None."""
    message = obj.get("message") or {}
    usage = message.get("usage") or {}
    entry = {
        "input": int(usage.get("input_tokens") or 0),
        "cache_read": int(usage.get("cache_read_input_tokens") or 0),
        "cache_creation": int(usage.get("cache_creation_input_tokens") or 0),
        "output": int(usage.get("output_tokens") or 0),
        "turns": 1,
        "_chars": _content_chars(message.get("content")),
    }
    entry["input_total"] = entry["input"] + entry["cache_read"] + entry["cache_creation"]
    model = str(message.get("model") or "")
    if not entry["input_total"] and not entry["output"]:
        return None
    if not model or model.startswith("<"):
        return None
    return model, entry


def _record_message(
    msgs: dict[str, tuple[str, dict[str, int]]],
    obj: dict[str, Any],
    seen_line_uuid: set[str] | None = None,
) -> None:
    """Deduplica por `message.id` (un bloque de contenido por línea, y repetido
    al reanudar o compactar): ignora líneas con `uuid` ya visto, acumula los
    caracteres de contenido de las líneas distintas del mismo `message.id` y
    estima `output` cuando el contador registrado es provisional (FW-347)."""
    line_uuid = str(obj.get("uuid") or "")
    if seen_line_uuid is not None and line_uuid:
        if line_uuid in seen_line_uuid:
            return
        seen_line_uuid.add(line_uuid)
    message = obj.get("message") or {}
    key = str(message.get("id") or line_uuid or f"#{len(msgs)}")
    parsed = _claude_usage(obj)
    if parsed is None:
        return
    model, entry = parsed
    prev = msgs.get(key)
    if prev is not None and line_uuid:
        prev_entry = prev[1]
        for k in ("input", "cache_read", "cache_creation", "output", "input_total"):
            entry[k] = max(prev_entry.get(k, 0), entry[k])
        entry["_chars"] = prev_entry.get("_chars", 0) + entry["_chars"]
    entry["output"] = max(entry["output"], round(entry["_chars"] / _TOKEN_CHARS))
    msgs[key] = (model, entry)


def _aggregate_messages(msgs: dict[str, tuple[str, dict[str, int]]]) -> dict[str, dict[str, int]]:
    by_model: dict[str, dict[str, int]] = {}
    for model, entry in msgs.values():
        _add_usage(by_model.setdefault(model, _new_usage()), entry)
    return by_model


def _claude_file_usage(path: Path) -> dict[str, dict[str, int]]:
    """Uso por modelo de una transcripción de Claude (raíz o subagente)."""
    msgs: dict[str, tuple[str, dict[str, int]]] = {}
    seen_uuid: set[str] = set()
    for obj in _iter_json(path):
        if obj.get("type") == "assistant":
            _record_message(msgs, obj, seen_uuid)
    return _aggregate_messages(msgs)


class _Acc:
    """Acumulador común: recibe llamadas a herramientas ya normalizadas."""

    def __init__(self, cost: SessionCost) -> None:
        self.cost = cost
        self.first: datetime | None = None
        self.last: datetime | None = None
        self.pending: dict[str, tuple[str, datetime]] = {}
        self.active_s = 0.0
        self._prev: datetime | None = None
        # Codex: uso por modelo (sin duplicados) y contexto por petición.
        self.models: dict[str, dict[str, int]] = {}
        self.ctx_samples: list[int] = []
        self.cur_model = ""
        self._last_total: int | None = None

    def touch(self, moment: datetime | None) -> None:
        if moment is None:
            return
        if self._prev is not None:
            gap = (moment - self._prev).total_seconds()
            if 0 < gap <= IDLE_GAP_MIN * 60:
                self.active_s += gap
        self._prev = moment
        if self.first is None or moment < self.first:
            self.first = moment
        if self.last is None or moment > self.last:
            self.last = moment

    def context(self, tokens: int) -> None:
        if tokens <= 0:
            return
        if not self.cost.context_initial:
            self.cost.context_initial = tokens
        self.cost.context_max = max(self.cost.context_max, tokens)

    def tool_call(self, text: str, *, track_files: bool = True) -> None:
        if track_files:
            for name in WATCHED_FILES:
                if name in text and _READ_VERBS.search(text):
                    self.cost.doc_reads[name] = self.cost.doc_reads.get(name, 0) + 1
        for label, pattern in _STATE_ACTIONS:
            if pattern.search(text):
                self.cost.state_calls[label] = self.cost.state_calls.get(label, 0) + 1
                break

    def wait_start(self, call_id: str, tool: str, moment: datetime | None) -> None:
        if call_id and moment:
            self.pending[call_id] = (tool, moment)

    def wait_end(self, call_id: str, moment: datetime | None) -> None:
        started = self.pending.pop(call_id, None)
        if started and moment and moment >= started[1]:
            tool, begin = started
            minutes = (moment - begin).total_seconds() / 60
            by_tool = self.cost.wait_by_tool_min
            by_tool[tool] = round(by_tool.get(tool, 0.0) + minutes, 3)

    def finish(self) -> SessionCost:
        cost = self.cost
        cost.wait_min = round(sum(cost.wait_by_tool_min.values()), 2)
        if self.first:
            cost.start = self.first.isoformat()
        if self.last:
            cost.end = self.last.isoformat()
        if self.first and self.last:
            cost.duration_min = round((self.last - self.first).total_seconds() / 60, 2)
        cost.active_min = min(round(self.active_s / 60, 2), cost.duration_min)
        return cost


# --------------------------------------------------------------------- Codex

_CODEX_WAIT_NAMES = {"wait_agent", "sleep", "wait"}


def _codex_file(path: Path, acc: _Acc) -> dict[str, Any]:
    """Procesa un rollout de Codex y devuelve su session_meta."""
    meta: dict[str, Any] = {}
    for obj in _iter_json(path):
        moment = _ts(obj.get("timestamp"))
        acc.touch(moment)
        kind = obj.get("type")
        payload = obj.get("payload")
        if not isinstance(payload, dict):
            continue
        if kind == "session_meta":
            meta = payload
        elif kind == "compacted":
            acc.cost.compactions += 1
        elif kind == "turn_context":
            acc.cur_model = str(payload.get("model") or acc.cur_model)
        elif kind == "event_msg":
            ptype = payload.get("type")
            if ptype == "task_started":
                acc.cost.turns += 1
            elif ptype == "token_count":
                info = payload.get("info") or {}
                last = info.get("last_token_usage") or {}
                acc.context(int(last.get("input_tokens") or 0))
                _codex_usage(acc, info)
        elif kind == "response_item":
            ptype = payload.get("type")
            if ptype in ("function_call", "custom_tool_call"):
                name = str(payload.get("name") or "")
                text = _call_text(payload.get("arguments") or payload.get("input"))
                if name == "spawn_agent":
                    acc.cost.subagents += 1
                if name in _CODEX_WAIT_NAMES:
                    acc.wait_start(str(payload.get("call_id") or ""), name, moment)
                else:
                    acc.tool_call(text)
            elif ptype in ("function_call_output", "custom_tool_call_output"):
                acc.wait_end(str(payload.get("call_id") or ""), moment)
    return meta


def _codex_usage(acc: _Acc, info: dict[str, Any]) -> None:
    """Suma el uso de una petición (`last_token_usage`) una sola vez: Codex repite
    el mismo `token_count` y se reconoce por el total acumulado, que no cambia.
    En Codex `input_tokens` ya incluye la caché leída (`cached_input_tokens`):
    `input_total` = `input_tokens`; `input` = lo no cacheado; `cache_creation`
    (`cache_write_input_tokens`) es informativo y no se suma otra vez."""
    total = (info.get("total_token_usage") or {}).get("total_tokens")
    if total is not None and total == acc._last_total:
        return
    acc._last_total = total
    last = info.get("last_token_usage") or {}
    if not last:
        return
    in_total = int(last.get("input_tokens") or 0)
    cached = int(last.get("cached_input_tokens") or 0)
    entry = {
        "input": max(0, in_total - cached),
        "cache_read": cached,
        "cache_creation": int(last.get("cache_write_input_tokens") or 0),
        "output": int(last.get("output_tokens") or 0),
        "turns": 1,
        "input_total": in_total,
    }
    _add_usage(acc.models.setdefault(acc.cur_model or "desconocido", _new_usage()), entry)
    if in_total > 0:
        acc.ctx_samples.append(in_total)


def collect_codex(home: Path, project: str | None, since: datetime | None, with_models: bool = False) -> list[SessionCost]:
    """`home` es ~/.codex: las sesiones archivadas conservan la raíz de la sesión."""
    groups: dict[str, list[tuple[Path, dict[str, Any], SessionCost, _Acc]]] = {}
    paths = sorted(
        {p for sub in ("sessions", "archived_sessions") for p in (home / sub).rglob("rollout-*.jsonl")}
    )
    for path in paths:
        first = next(_iter_json(path), None)
        meta = (first or {}).get("payload") if first and first.get("type") == "session_meta" else None
        if not isinstance(meta, dict):
            continue
        if not _cwd_matches(str(meta.get("cwd") or ""), project):
            continue
        sid = str(meta.get("session_id") or meta.get("id") or path.stem)
        cost = SessionCost("codex", str(meta.get("id") or sid), cwd=str(meta.get("cwd") or ""))
        acc = _Acc(cost)
        _codex_file(path, acc)
        groups.setdefault(sid, []).append((path, meta, cost, acc))

    sessions: list[SessionCost] = []
    for sid, members in groups.items():
        roots = [m for m in members if m[1].get("thread_source") not in ("subagent", "guardian_review")]
        main = roots[0] if roots else members[0]
        total = main[3].finish()
        total.session_id = sid
        total.files = len(members)
        children_models: dict[str, dict[str, int]] = {}
        for path, meta, cost, acc in members:
            if path == main[0]:
                continue
            child = acc.finish()
            if meta.get("thread_source") == "guardian_review":
                total.reviewer_evals += child.turns  # una evaluación por turno del revisor
            _merge_child(total, child)
            _merge_by_model(children_models, acc.models)
        if main[3].ctx_samples:
            total.ctx_medio = round(sum(main[3].ctx_samples) / len(main[3].ctx_samples))
        if with_models:
            total.by_model = {
                "root": main[3].models,
                "subagents": children_models,
                "totals": {"root": _totals(main[3].models), "subagents": _totals(children_models)},
            }
        total = _finalize_group(total, [m[3] for m in members])
        if since and (_ts(total.end) or since) < since:
            continue
        sessions.append(total)
    return sorted(sessions, key=lambda s: s.start)


def _merge_child(total: SessionCost, child: SessionCost) -> None:
    """Suma lecturas y llamadas de una sesión hija; contexto, turnos, esperas y
    compactaciones son los de la raíz (las de los hijos no frenan al usuario)."""
    for name, count in child.doc_reads.items():
        total.doc_reads[name] = total.doc_reads.get(name, 0) + count
    for name, count in child.state_calls.items():
        total.state_calls[name] = total.state_calls.get(name, 0) + count


def _finalize_group(total: SessionCost, accs: list[_Acc]) -> SessionCost:
    starts = [a.first for a in accs if a.first]
    ends = [a.last for a in accs if a.last]
    if starts and ends:
        total.start = min(starts).isoformat()
        total.end = max(ends).isoformat()
        total.duration_min = round((max(ends) - min(starts)).total_seconds() / 60, 2)
    return total


# -------------------------------------------------------------------- Claude


def _claude_session(
    path: Path, project: str | None, msgs: dict[str, tuple[str, dict[str, int]]] | None = None
) -> SessionCost | None:
    """`msgs` (opcional) recibe los mensajes del asistente deduplicados por id."""
    cost = SessionCost("claude", path.stem)
    acc = _Acc(cost)
    seen_cwd = False
    seen_uuid: set[str] = set()
    seen_msg_uuid: set[str] = set()
    seen_tool_use: set[str] = set()
    msgs = {} if msgs is None else msgs
    for obj in _iter_json(path):
        if not seen_cwd and obj.get("cwd"):
            cost.cwd = str(obj["cwd"])
            seen_cwd = True
            if not _cwd_matches(cost.cwd, project):
                return None
        ident = str(obj.get("uuid") or "")
        if ident:
            if ident in seen_uuid:
                continue
            seen_uuid.add(ident)
        moment = _ts(obj.get("timestamp"))
        acc.touch(moment)
        kind = obj.get("type")
        if kind == "system" and obj.get("subtype") == "compact_boundary":
            cost.compactions += 1
        elif kind == "user":
            message = obj.get("message") or {}
            content = message.get("content")
            blocks = content if isinstance(content, list) else []
            for block in blocks:
                if isinstance(block, dict) and block.get("type") == "tool_result":
                    acc.wait_end(str(block.get("tool_use_id") or ""), moment)
            is_prompt = isinstance(content, str) or any(
                isinstance(b, dict) and b.get("type") == "text" for b in blocks
            )
            if is_prompt and not obj.get("isMeta") and not obj.get("isCompactSummary") and not obj.get("isSidechain"):
                cost.turns += 1
        elif kind == "assistant":
            message = obj.get("message") or {}
            usage = message.get("usage") or {}
            _record_message(msgs, obj, seen_msg_uuid)
            acc.context(
                int(usage.get("input_tokens") or 0)
                + int(usage.get("cache_read_input_tokens") or 0)
                + int(usage.get("cache_creation_input_tokens") or 0)
            )
            for block in message.get("content") or []:
                if not isinstance(block, dict) or block.get("type") != "tool_use":
                    continue
                tid = str(block.get("id") or "")
                if tid:
                    if tid in seen_tool_use:
                        continue
                    seen_tool_use.add(tid)
                name = str(block.get("name") or "")
                data = block.get("input") or {}
                if name in ("Agent", "Task"):
                    cost.subagents += 1
                if name in _CLAUDE_WAIT_TOOLS:
                    acc.wait_start(tid, name, moment)
                    continue
                text = _call_text(data)
                if name == "Read":
                    text = "cat " + str(data.get("file_path") or "")
                if name.startswith("mcp__") or name == "Bash" or name == "Read":
                    acc.tool_call(name + " " + text)
    if not seen_cwd and project:
        return None
    if msgs:
        contexts = [entry["input_total"] for _, entry in msgs.values() if entry["input_total"]]
        if contexts:
            cost.ctx_medio = round(sum(contexts) / len(contexts))
    return acc.finish()


def collect_claude(
    root: Path, project: str | None, since: datetime | None, with_models: bool = False
) -> list[SessionCost]:
    sessions: list[SessionCost] = []
    for path in sorted(root.glob("*/*.jsonl")):
        if since:
            try:
                if datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc) < since:
                    continue
            except OSError:
                continue
        msgs: dict[str, tuple[str, dict[str, int]]] = {}
        cost = _claude_session(path, project, msgs)
        if cost is None:
            continue
        subdir = path.with_suffix("") / "subagents"
        sub_models: dict[str, dict[str, int]] = {}
        if subdir.is_dir():
            sub_files = sorted(subdir.glob("agent-*.jsonl"))
            cost.files += len(sub_files)
            if with_models:
                # Dedup por id dentro de cada transcripción de subagente.
                for sub in sub_files:
                    _merge_by_model(sub_models, _claude_file_usage(sub))
        if with_models:
            root_models = _aggregate_messages(msgs)
            cost.by_model = {
                "root": root_models,
                "subagents": sub_models,
                "totals": {"root": _totals(root_models), "subagents": _totals(sub_models)},
            }
        if since and (_ts(cost.end) or since) < since:
            continue
        sessions.append(cost)
    return sorted(sessions, key=lambda s: s.start)


# ------------------------------------------------------------------- Salida


def _fmt_tokens(value: int) -> str:
    return f"{value / 1000:.1f}k" if value >= 1000 else str(value)


def format_table(sessions: list[SessionCost]) -> str:
    if not sessions:
        return "Sin sesiones que coincidan."
    header = (
        "agente  sesión    inicio            dur(min) act(min) turnos subag espera(min) compact "
        "ctx_ini ctx_max lect_doc state_mgr revisor ctx_medio"
    )
    rows = [header]
    for s in sessions:
        reads = sum(s.doc_reads.values())
        state = sum(s.state_calls.values())
        rows.append(
            f"{s.agent:<7} {s.session_id[:8]:<9} {s.start[:16].replace('T', ' '):<17} "
            f"{s.duration_min:>8.1f} {s.active_min:>8.1f} {s.turns:>6} {s.subagents:>5} {s.wait_min:>11.1f} "
            f"{s.compactions:>7} {_fmt_tokens(s.context_initial):>7} {_fmt_tokens(s.context_max):>7} "
            f"{reads:>8} {state:>9} {s.reviewer_evals:>7} {_fmt_tokens(s.ctx_medio):>9}"
        )
    return "\n".join(rows)


def _fmt_m(value: int) -> str:
    return f"{value / 1_000_000:.2f}M"


def format_by_model(sessions: list[SessionCost]) -> str:
    """Tabla `--by-model`: por sesión, una fila por (ámbito, modelo). La entrada
    incluye la caché (`input + cache_read + cache_creation`; en Codex es
    `input_tokens`, que ya la incluye). Minutos de pared y activos al pie de la sesión."""
    if not sessions:
        return "Sin sesiones que coincidan."
    lines: list[str] = []
    for s in sessions:
        lines.append(
            f"{s.agent} {s.session_id[:8]}  {s.start[:16].replace('T', ' ')}  pared {s.duration_min:.1f} min · "
            f"activos {s.active_min:.1f} min · turnos {s.turns} · compactaciones {s.compactions} · "
            f"ctx_medio {_fmt_tokens(s.ctx_medio)}"
        )
        lines.append("  ámbito      modelo                        entrada_total  cache_lect  cache_crea     salida  mensajes")
        for scope in ("root", "subagents"):
            rows = (s.by_model or {}).get(scope) or {}
            label = "raíz" if scope == "root" else "subagentes"
            for model in sorted(rows, key=lambda m: -rows[m]["input_total"]):
                u = rows[model]
                lines.append(
                    f"  {label:<11} {model[:28]:<28} {_fmt_m(u['input_total']):>14} {_fmt_m(u['cache_read']):>11} "
                    f"{_fmt_m(u['cache_creation']):>11} {_fmt_m(u['output']):>10} {u['turns']:>9}"
                )
            totals = ((s.by_model or {}).get("totals") or {}).get(scope) or _new_usage()
            lines.append(
                f"  {label:<11} {'TOTAL':<28} {_fmt_m(totals['input_total']):>14} {_fmt_m(totals['cache_read']):>11} "
                f"{_fmt_m(totals['cache_creation']):>11} {_fmt_m(totals['output']):>10} {totals['turns']:>9}"
            )
        lines.append("")
    return "\n".join(lines).rstrip()


def summary_line(cost: SessionCost) -> str:
    reads = sum(cost.doc_reads.values())
    state = sum(cost.state_calls.values())
    return (
        f"Coste de sesión ({cost.agent}): {cost.active_min:.0f} min activos ({cost.duration_min:.0f} totales), {cost.turns} turnos, "
        f"{cost.subagents} subagentes, {cost.wait_min:.0f} min de espera, "
        f"{cost.compactions} compactaciones, contexto máx {_fmt_tokens(cost.context_max)}, "
        f"{reads} lecturas de docs raíz, {state} llamadas a state_manager."
    )


def find_current(sessions: list[SessionCost]) -> SessionCost | None:
    return max(sessions, key=lambda s: s.end, default=None) if sessions else None


SUBCOMMANDS = (
    "norm-load", "module-cost", "loops", "receipts", "read-weight", "independent-modules", "codex-effort-cache",
)


def main(argv: list[str] | None = None) -> int:
    args_list = list(sys.argv[1:] if argv is None else argv)
    if args_list and args_list[0] in SUBCOMMANDS:
        # Importación perezosa: session_metrics importa de este módulo.
        import session_metrics

        return session_metrics.main(args_list)
    argv = args_list
    parser = argparse.ArgumentParser(description="Coste de sesiones de Codex y Claude Code (solo lectura).")
    parser.add_argument("--agent", choices=("codex", "claude", "all"), default="all")
    parser.add_argument("--project", help="ruta real del proyecto (se compara con el cwd de la transcripción)")
    parser.add_argument("--since", help="solo sesiones que terminan desde YYYY-MM-DD")
    parser.add_argument("--session", help="prefijo del id de sesión")
    parser.add_argument("--json", action="store_true", help="salida JSON")
    parser.add_argument("--by-model", action="store_true",
                        help="tokens por modelo (raíz y subagentes por separado), con minutos de pared y activos")
    parser.add_argument("--current-line", action="store_true",
                        help="una línea con la sesión más reciente del proyecto; vacío si no hay (para /gycd)")
    parser.add_argument("--max-age-min", type=float, default=30.0,
                        help="con --current-line, ignora sesiones inactivas desde hace más de N min (0 = sin límite)")
    parser.add_argument("--codex-home", default=os.environ.get("CODEX_HOME", "~/.codex"))
    parser.add_argument("--claude-dir", default="~/.claude/projects")
    args = parser.parse_args(argv)

    since = None
    if args.since:
        try:
            since = datetime.fromisoformat(args.since).replace(tzinfo=timezone.utc)
        except ValueError:
            print(f"--since inválido: {args.since!r} (usa YYYY-MM-DD)", file=sys.stderr)
            return 2
    project = args.project
    if args.current_line and not project:
        project = os.getcwd()

    sessions: list[SessionCost] = []
    codex_root = Path(args.codex_home).expanduser()
    claude_root = Path(args.claude_dir).expanduser()
    if args.agent in ("codex", "all") and codex_root.is_dir():
        sessions += collect_codex(codex_root, project, since, args.by_model)
    if args.agent in ("claude", "all") and claude_root.is_dir():
        sessions += collect_claude(claude_root, project, since, args.by_model)
    if args.session:
        sessions = [s for s in sessions if s.session_id.startswith(args.session)]
    sessions.sort(key=lambda s: s.start)

    if args.current_line:
        current = find_current(sessions)
        ended = _ts(current.end) if current else None
        stale = bool(
            args.max_age_min
            and ended
            and (datetime.now(timezone.utc) - ended).total_seconds() > args.max_age_min * 60
        )
        if current and not stale:
            print(summary_line(current))
        return 0
    if args.json:
        print(json.dumps([asdict(s) for s in sessions], ensure_ascii=False, indent=2))
    elif args.by_model:
        print(format_by_model(sessions))
    else:
        print(format_table(sessions))
    return 0


if __name__ == "__main__":
    sys.exit(main())
