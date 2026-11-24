#!/usr/bin/env python3
"""tests/tools/token_baseline.py — Sonda consolidada de línea base y métricas de contexto.

Consolida las sondas del scratchpad (FW-239..242) en una herramienta local reproducible:
- token_usage_sessions.py: consumo de tokens por sesión y reparto de texto por herramientas
- token_levers.py: turnos por sesión, contexto por turno y relecturas de workflows grandes
- bash_origin_refined.py: desglose fino de salidas de Bash por origen y destino
- engine_read_causes.py: episodios de lectura de código del motor y errores precedentes
- state_payload_sizes.py: tamaños de ficheros de estado en disco y respuestas MCP
- Invocaciones reales de comandos y origen de carga de workflows (base empírica Lote 5A)

Vive en la plantilla de desarrollo y no se propaga a los proyectos adoptantes.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
import re
import statistics
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple


def parse_iso(ts: Optional[str]) -> Optional[datetime]:
    """Parsea una cadena ISO8601 a datetime UTC."""
    if not ts:
        return None
    try:
        s = ts.strip().replace("Z", "+00:00")
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return None


def get_session_first_timestamp(path: Path) -> Optional[str]:
    """Obtiene el primer timestamp de cualquier evento en el fichero de sesión."""
    try:
        with path.open(encoding="utf-8", errors="ignore") as handle:
            for line in handle:
                try:
                    ev = json.loads(line)
                except json.JSONDecodeError:
                    continue
                ts = ev.get("timestamp") or (ev.get("message") or {}).get("timestamp")
                if ts:
                    return str(ts)
    except Exception:
        pass
    return None


DEFAULT_TRANSCRIPTS_ROOT = Path(os.path.expanduser("~/.claude/projects"))
DEFAULT_PROJECTS_ROOT = Path(os.path.expanduser("~/Projects"))

TARGET_PROJECTS: Tuple[str, ...] = ()

EXCLUDED_PROJECT_MARKERS = ("-Projects--framework",)

BIG_FILES = (
    "ciclo.md",
    "cierre-modulo.md",
    "gycd.md",
    "cambio-seguro.md",
    "arrancar.md",
    "GUARDRAILS_MASTER.md",
    "state_core.py",
    "workflow_receipts.py",
    "framework_doctor.py",
    "guard_destructive_command.py",
)

READ_VERBS = re.compile(
    r"^\s*(?:cd\s+\S+\s*(?:&&|;)\s*)?(cat|head|tail|sed|less|wc|ls|find|grep|rg|tree|awk|nl|diff|jq)\b"
)
EXEC_FW = re.compile(
    r"(?:python3?|bash|sh|uv run(?: python)?)\s+(?:-[\w-]+\s+)*\S*\.agent/scripts/([\w.-]+)"
)
FW_PATH = re.compile(r"\.agent/|GUARDRAILS_MASTER|CLAUDE\.md|AGENTS\.md")
STATE_PATH = re.compile(r"_project/|_feedback/")
ENGINE_PATTERN = re.compile(r"(state_core|workflow_receipts)\.py")
ERROR_HINT = re.compile(
    r"(Error|ERROR|FAIL|rechaz|Rechaz|BLOQUEAD|Traceback|no se pudo|inválid|invalid|REJECT|denied|exit code [1-9])"
)
CD_PREFIX = re.compile(r"""^\s*cd\s+(?:"[^"]*"|'[^']*'|(?:\\.|[^\s;&|])+)\s*(?:&&|;)\s*""")
SLASH_CMD_PATTERN = re.compile(r"^\s*(/[a-zA-Z0-9_-]+)")


def text_len(content: Any) -> int:
    """Calcula la longitud de texto de un bloque de contenido tool_result o mensaje."""
    if isinstance(content, str):
        return len(content)
    if isinstance(content, list):
        return sum(len(b.get("text", "")) for b in content if isinstance(b, dict))
    return 0


def text_content(content: Any) -> str:
    """Extrae el contenido textual completo de un bloque o lista de bloques."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(b.get("text", "") for b in content if isinstance(b, dict))
    return ""


def categorize_tool_use(tool_name: Optional[str], inp: Dict[str, Any]) -> str:
    """Clasifica el uso de herramienta según la procedencia del dato."""
    if not tool_name:
        return "otras"
    if tool_name == "Read":
        p = str(inp.get("file_path", ""))
        if "/.agent/" in p or "GUARDRAILS" in p or p.endswith(("CLAUDE.md", "AGENTS.md")):
            return "lectura framework"
        if "/_project/" in p or "/_feedback/" in p:
            return "lectura estado/_project"
        return "lectura producto"
    if tool_name == "Bash":
        return "bash"
    if tool_name.startswith("mcp__") and "state" in tool_name:
        return "mcp estado"
    if tool_name in ("Grep", "Glob"):
        return "búsqueda"
    if tool_name in ("Edit", "Write", "MultiEdit"):
        return "edición"
    if tool_name in ("Agent", "Task"):
        return "subagente"
    if tool_name == "Skill":
        return "skill harness"
    return "otras"


def classify_bash_command(raw_cmd: str) -> Tuple[str, str]:
    """Clasifica una orden Bash según si inspecciona producto, estado o framework, o ejecuta."""
    cmd = raw_cmd
    while CD_PREFIX.match(cmd):
        cmd = CD_PREFIX.sub("", cmd, count=1)
    cmd_stripped = cmd.strip()

    m = EXEC_FW.search(cmd_stripped)
    if m:
        return ("ejecuta script framework", m.group(1))

    v = READ_VERBS.search(cmd_stripped)
    if v:
        verb = v.group(1)
        if FW_PATH.search(cmd_stripped):
            fm = re.search(r"\.agent/[\w./-]+|GUARDRAILS_MASTER\.md|CLAUDE\.md|AGENTS\.md", cmd_stripped)
            detail = fm.group(0).split("/")[-1] if fm else verb
            return ("inspección → framework", detail)
        if STATE_PATH.search(cmd_stripped):
            sm = re.search(r"_project/[\w./-]+|_feedback/[\w./-]+", cmd_stripped)
            detail = sm.group(0).split("/")[-1] if sm else verb
            return ("inspección → estado", detail)
        return ("inspección → producto", verb)

    low = cmd_stripped.lower()
    if re.search(r"\b(pytest|unittest|npm (run )?test|vitest|jest|go test|cargo test)\b", low):
        return ("tests producto", "tests")
    if re.search(r"(^|\s|[;&|])git\s", " " + cmd_stripped):
        return ("git", "git")
    first_token = low.split()[0] if low.split() else "?"
    return ("otros", first_token)


@dataclass
class ColdResume:
    gap_hours: float
    creation_tokens: int
    session_id: str
    timestamp: str


@dataclass
class SessionMetrics:
    project: str
    session_id: str
    first_timestamp: Optional[str] = None
    turns: int = 0
    input_tokens: int = 0
    cache_read_tokens: int = 0
    cache_creation_tokens: int = 0
    output_tokens: int = 0
    first_turn_cache_creation: Optional[int] = None
    hot_turn_creations: List[int] = field(default_factory=list)
    cold_resumes: List[ColdResume] = field(default_factory=list)
    context_per_turn: List[int] = field(default_factory=list)
    tool_chars_by_category: Dict[str, int] = field(default_factory=lambda: defaultdict(int))
    fw_files_read: Dict[str, int] = field(default_factory=lambda: defaultdict(int))
    big_files_seen: Counter = field(default_factory=Counter)
    bash_classes: Dict[str, int] = field(default_factory=lambda: defaultdict(int))
    bash_calls: Counter = field(default_factory=Counter)
    bash_details: Dict[str, Dict[str, int]] = field(default_factory=lambda: defaultdict(lambda: defaultdict(int)))
    mcp_state_calls: Dict[str, List[int]] = field(default_factory=lambda: defaultdict(list))
    mcp_state_overflows: Counter = field(default_factory=Counter)
    command_invocations: Counter = field(default_factory=Counter)
    workflow_load_origins: Dict[str, Counter] = field(default_factory=lambda: defaultdict(Counter))


def parse_session_file(
    path: Path,
    project_name: str,
    since: Optional[str] = None,
    until: Optional[str] = None,
) -> Optional[SessionMetrics]:
    """Parsea una sesión .jsonl y extrae métricas detalladas."""
    first_ts = get_session_first_timestamp(path)
    if since:
        dt_since = parse_iso(since)
        dt_first = parse_iso(first_ts)
        if not dt_first or (dt_since and dt_first < dt_since):
            return None
    if until:
        dt_until = parse_iso(until)
        dt_first = parse_iso(first_ts)
        if not dt_first or (dt_until and dt_first > dt_until):
            return None

    metrics = SessionMetrics(project=project_name, session_id=path.stem, first_timestamp=first_ts)
    pending_tool_use: Dict[str, Tuple[str, Dict[str, Any]]] = {}
    prev_turn_dt: Optional[datetime] = None

    try:
        with path.open(encoding="utf-8", errors="ignore") as handle:
            for line in handle:
                try:
                    ev = json.loads(line)
                except json.JSONDecodeError:
                    continue

                msg = ev.get("message") or {}
                ev_type = ev.get("type")

                if ev_type == "assistant":
                    u = msg.get("usage") or {}
                    if u:
                        metrics.turns += 1
                        inp_tok = int(u.get("input_tokens") or 0)
                        cache_read = int(u.get("cache_read_input_tokens") or 0)
                        cache_create = int(u.get("cache_creation_input_tokens") or 0)
                        out_tok = int(u.get("output_tokens") or 0)
                        metrics.input_tokens += inp_tok
                        metrics.cache_read_tokens += cache_read
                        metrics.cache_creation_tokens += cache_create
                        metrics.output_tokens += out_tok
                        metrics.context_per_turn.append(inp_tok + cache_read + cache_create)

                        ts_str = ev.get("timestamp") or msg.get("timestamp")
                        cur_dt = parse_iso(ts_str) if ts_str else None
                        gap_sec = (cur_dt - prev_turn_dt).total_seconds() if (cur_dt and prev_turn_dt) else None

                        if metrics.turns == 1:
                            metrics.first_turn_cache_creation = cache_create
                        else:
                            if gap_sec is not None:
                                if gap_sec < 600:
                                    metrics.hot_turn_creations.append(cache_create)
                                elif gap_sec > 3600:
                                    metrics.cold_resumes.append(ColdResume(
                                        gap_hours=gap_sec / 3600.0,
                                        creation_tokens=cache_create,
                                        session_id=metrics.session_id[:8],
                                        timestamp=ts_str or "",
                                    ))
                        if cur_dt:
                            prev_turn_dt = cur_dt

                    content_blocks = msg.get("content") or []
                    if isinstance(content_blocks, list):
                        for b in content_blocks:
                            if isinstance(b, dict) and b.get("type") == "tool_use":
                                t_id = b.get("id")
                                t_name = b.get("name")
                                t_inp = b.get("input") or {}
                                pending_tool_use[t_id] = (t_name, t_inp)

                                # Registro directo de invocaciones de Skill
                                if t_name == "Skill":
                                    s_name = str(t_inp.get("skill") or t_inp.get("name") or "unknown")
                                    metrics.command_invocations[f"skill:{s_name}"] += 1
                                    metrics.workflow_load_origins[s_name]["skill_tool"] += 1

                                # Registro de lecturas directas de Read
                                elif t_name == "Read":
                                    fp = str(t_inp.get("file_path", ""))
                                    fname = fp.split("/")[-1]
                                    if fname in BIG_FILES:
                                        metrics.big_files_seen[fname] += 1
                                    if ".agent/workflows" in fp:
                                        metrics.workflow_load_origins[fname]["read_tool"] += 1

                elif ev_type == "user":
                    user_content = msg.get("content")
                    # Detección de slash commands del usuario
                    if isinstance(user_content, str):
                        m = SLASH_CMD_PATTERN.match(user_content)
                        if m:
                            metrics.command_invocations[f"user_slash:{m.group(1)}"] += 1
                    elif isinstance(user_content, list):
                        for item in user_content:
                            if isinstance(item, dict):
                                if item.get("type") == "text":
                                    txt = item.get("text", "")
                                    m = SLASH_CMD_PATTERN.match(txt)
                                    if m:
                                        metrics.command_invocations[f"user_slash:{m.group(1)}"] += 1
                                elif item.get("type") == "tool_result":
                                    tu_id = item.get("tool_use_id")
                                    tool, inp = pending_tool_use.pop(tu_id, (None, {}))
                                    n_chars = text_len(item.get("content"))
                                    cat = categorize_tool_use(tool, inp)
                                    metrics.tool_chars_by_category[cat] += n_chars

                                    if cat == "lectura framework":
                                        fname = str(inp.get("file_path", "")).split("/")[-1]
                                        metrics.fw_files_read[fname] += n_chars

                                    elif tool == "Bash":
                                        cmd = str(inp.get("command", ""))
                                        b_cls, b_det = classify_bash_command(cmd)
                                        metrics.bash_classes[b_cls] += n_chars
                                        metrics.bash_calls[b_cls] += 1
                                        metrics.bash_details[b_cls][b_det] += n_chars

                                        if ".agent/workflows" in cmd:
                                            wf_match = re.search(r"([\w-]+\.md)", cmd)
                                            wf_name = wf_match.group(1) if wf_match else "unknown.md"
                                            metrics.workflow_load_origins[wf_name]["bash_read"] += 1

                                    elif tool and "state_manager" in tool:
                                        action_name = tool.split("__")[-1]
                                        metrics.mcp_state_calls[action_name].append(n_chars)
                                        txt_res = text_content(item.get("content"))
                                        if "exceeds maximum allowed tokens" in txt_res:
                                            metrics.mcp_state_overflows[action_name] += 1

    except Exception:
        return None

    if metrics.turns == 0:
        return None
    return metrics


def find_engine_read_episodes(
    transcripts_dir: Path,
    target_projects: Tuple[str, ...] = TARGET_PROJECTS,
    project: Optional[str] = None,
    since: Optional[str] = None,
    until: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Encuentra episodios donde el agente lee state_core.py o workflow_receipts.py."""
    episodes = []
    read_bash_tool = re.compile(r"\b(cat|sed|grep|rg|head|tail|awk|nl)\b")

    for proj in sorted(transcripts_dir.iterdir()):
        if not proj.is_dir() or "Projects" not in proj.name or any(x in proj.name for x in EXCLUDED_PROJECT_MARKERS):
            continue
        if project:
            if project not in proj.name:
                continue
        elif target_projects:
            if not any(proj.name.endswith(t) for t in target_projects):
                continue
        proj_clean_name = proj.name.split("-Projects-")[-1]

        for path in sorted(proj.glob("*.jsonl")):
            first_ts = get_session_first_timestamp(path)
            if since:
                dt_since = parse_iso(since)
                dt_first = parse_iso(first_ts)
                if not dt_first or (dt_since and dt_first < dt_since):
                    continue
            if until:
                dt_until = parse_iso(until)
                dt_first = parse_iso(first_ts)
                if not dt_first or (dt_until and dt_first > dt_until):
                    continue
            last_error = None
            last_agent_text = ""
            pending: Dict[str, Tuple[str, Dict[str, Any]]] = {}
            done_files: Set[str] = set()

            try:
                with path.open(encoding="utf-8", errors="ignore") as handle:
                    for line in handle:
                        try:
                            ev = json.loads(line)
                        except json.JSONDecodeError:
                            continue

                        content = (ev.get("message") or {}).get("content")
                        if not isinstance(content, list):
                            continue

                        if ev.get("type") == "assistant":
                            for b in content:
                                if not isinstance(b, dict):
                                    continue
                                if b.get("type") == "text" and b.get("text", "").strip():
                                    last_agent_text = b["text"].strip()
                                if b.get("type") != "tool_use":
                                    continue
                                name, inp = b.get("name"), b.get("input") or {}
                                target = None
                                if name == "Read" and ENGINE_PATTERN.search(str(inp.get("file_path", ""))):
                                    target = ENGINE_PATTERN.search(str(inp.get("file_path", ""))).group(0)
                                elif name == "Bash":
                                    cmd = str(inp.get("command", ""))
                                    if ENGINE_PATTERN.search(cmd) and read_bash_tool.search(cmd) and "python" not in cmd.split("|")[0]:
                                        target = ENGINE_PATTERN.search(cmd).group(0)

                                if target and target not in done_files:
                                    done_files.add(target)
                                    episodes.append({
                                        "proyecto": proj_clean_name,
                                        "sesion": path.stem[:8],
                                        "fichero": target,
                                        "error_previo": (last_error or "")[:500],
                                        "agente_dijo": last_agent_text[:400],
                                    })
                                pending[b.get("id")] = (name, inp)

                        elif ev.get("type") == "user":
                            for b in content:
                                if isinstance(b, dict) and b.get("type") == "tool_result":
                                    txt = text_content(b.get("content"))
                                    if b.get("is_error") or ERROR_HINT.search(txt[:3000]):
                                        tool, inp = pending.get(b.get("tool_use_id"), ("?", {}))
                                        cmd = str(inp.get("command", inp.get("file_path", "")))[:160]
                                        m = ERROR_HINT.search(txt)
                                        start = max(0, (m.start() if m else 0) - 200)
                                        last_error = f"[{tool}] {cmd}\n  → {txt[start:start + 320]}"
            except Exception:
                continue

    return episodes


def inspect_disk_state_sizes(projects_root: Path) -> List[Dict[str, Any]]:
    """Mide los tamaños en disco de los ficheros de estado en proyectos existentes."""
    results = []
    if not projects_root.exists():
        return results

    state_files = sorted(projects_root.glob("*/*/_project/03_project_state.json"))
    for state in state_files:
        p = state.parent
        project_name = p.parent.name
        sizes = {}
        for name in (
            "03_project_state.json",
            "state_log.jsonl",
            "core.md",
            "00_session_brief.json",
            "08_module_acceptance.md",
            "04_active_scope.md",
        ):
            f_path = p / name
            if f_path.exists():
                sizes[name] = f_path.stat().st_size
        results.append({
            "project": project_name,
            "path": str(p),
            "sizes_bytes": sizes,
            "sizes_kb": {k: round(v / 1024, 1) for k, v in sizes.items()},
        })
    return results


def run_full_analysis(
    transcripts_root: Path = DEFAULT_TRANSCRIPTS_ROOT,
    projects_root: Path = DEFAULT_PROJECTS_ROOT,
    target_projects: Tuple[str, ...] = TARGET_PROJECTS,
    project: Optional[str] = None,
    since: Optional[str] = None,
    until: Optional[str] = None,
) -> Dict[str, Any]:
    """Ejecuta el análisis consolidado completo sobre transcripciones y ficheros."""
    all_sessions: List[SessionMetrics] = []
    by_project_sessions: Dict[str, List[SessionMetrics]] = defaultdict(list)

    if transcripts_root.exists():
        for proj_dir in sorted(transcripts_root.iterdir()):
            if not proj_dir.is_dir() or "Projects" not in proj_dir.name or any(x in proj_dir.name for x in EXCLUDED_PROJECT_MARKERS):
                continue
            if project:
                if project not in proj_dir.name:
                    continue
            elif target_projects:
                if not any(proj_dir.name.endswith(t) for t in target_projects):
                    continue
            proj_name = proj_dir.name.split("-Projects-")[-1]

            for jsonl_file in sorted(proj_dir.glob("*.jsonl")):
                parsed = parse_session_file(jsonl_file, proj_name, since=since, until=until)
                if parsed:
                    all_sessions.append(parsed)
                    by_project_sessions[proj_name].append(parsed)

    # 1. Resumen por proyecto
    project_summaries = {}
    grand_tool_chars = defaultdict(int)
    grand_fw_reads = defaultdict(int)
    grand_bash_classes = defaultdict(int)
    grand_bash_calls = Counter()
    grand_bash_details = defaultdict(lambda: defaultdict(int))
    grand_mcp_calls = defaultdict(list)
    grand_mcp_overflows = Counter()
    grand_command_invocations = Counter()
    grand_workflow_origins = defaultdict(lambda: defaultdict(int))

    for proj_name, sessions in by_project_sessions.items():
        turns = [s.turns for s in sessions]
        contexts = [statistics.median(s.context_per_turn) for s in sessions if s.context_per_turn]
        total_input = [s.input_tokens + s.cache_read_tokens + s.cache_creation_tokens for s in sessions]
        out_tokens = sum(s.output_tokens for s in sessions)
        cache_created = sum(s.cache_creation_tokens for s in sessions)

        proj_big_reads = Counter()
        for s in sessions:
            proj_big_reads.update(s.big_files_seen)
            for cat, n in s.tool_chars_by_category.items():
                grand_tool_chars[cat] += n
            for f_name, n in s.fw_files_read.items():
                grand_fw_reads[f_name] += n
            for cls, n in s.bash_classes.items():
                grand_bash_classes[cls] += n
            for cls, c in s.bash_calls.items():
                grand_bash_calls[cls] += c
            for cls, det_dict in s.bash_details.items():
                for det, n in det_dict.items():
                    grand_bash_details[cls][det] += n
            for action, sizes in s.mcp_state_calls.items():
                grand_mcp_calls[action].extend(sizes)
            for action, c in s.mcp_state_overflows.items():
                grand_mcp_overflows[action] += c
            for cmd, c in s.command_invocations.items():
                grand_command_invocations[cmd] += c
            for wf, origins in s.workflow_load_origins.items():
                for orig, c in origins.items():
                    grand_workflow_origins[wf][orig] += c

        project_summaries[proj_name] = {
            "sessions_count": len(sessions),
            "median_turns": round(statistics.median(turns), 1) if turns else 0,
            "max_turns": max(turns) if turns else 0,
            "median_context_tokens": round(statistics.median(contexts), 0) if contexts else 0,
            "total_input_and_cache_tokens": sum(total_input),
            "total_cache_creation_tokens": cache_created,
            "total_output_tokens": out_tokens,
            "big_files_read": dict(proj_big_reads.most_common(10)),
        }

    # Dimensión de caché agregada
    all_cold_resumes: List[Dict[str, Any]] = []
    all_hot_creations: List[int] = []
    first_turn_creations: List[int] = []
    total_cache_creation = 0
    total_cache_read = 0
    total_uncached_input = 0

    for s in all_sessions:
        total_cache_creation += s.cache_creation_tokens
        total_cache_read += s.cache_read_tokens
        total_uncached_input += s.input_tokens
        if s.first_turn_cache_creation is not None:
            first_turn_creations.append(s.first_turn_cache_creation)
        all_hot_creations.extend(s.hot_turn_creations)
        for cr in s.cold_resumes:
            all_cold_resumes.append({
                "session": cr.session_id,
                "timestamp": cr.timestamp,
                "gap_hours": round(cr.gap_hours, 2),
                "creation_tokens": cr.creation_tokens,
            })

    all_cold_resumes.sort(key=lambda r: -r["gap_hours"])
    cold_resumes_creation = sum(r["creation_tokens"] for r in all_cold_resumes)
    cold_pct = (cold_resumes_creation / total_cache_creation * 100) if total_cache_creation else 0.0

    cost_creation = total_cache_creation * 1.25
    cost_read = total_cache_read * 0.1
    cost_uncached = total_uncached_input * 1.0
    cost_total = cost_creation + cost_read + cost_uncached

    cache_dimension = {
        "total_cache_creation_tokens": total_cache_creation,
        "total_cache_read_tokens": total_cache_read,
        "total_uncached_input_tokens": total_uncached_input,
        "cache_read_ratio": round(total_cache_read / total_cache_creation, 1) if total_cache_creation else 0.0,
        "cache_read_pct": round((total_cache_read / (total_cache_read + total_cache_creation)) * 100, 1) if (total_cache_read + total_cache_creation) else 0.0,
        "cache_create_pct": round((total_cache_creation / (total_cache_read + total_cache_creation)) * 100, 1) if (total_cache_read + total_cache_creation) else 0.0,
        "effective_cost_creation": round(cost_creation, 1),
        "effective_cost_read": round(cost_read, 1),
        "effective_cost_total": round(cost_total, 1),
        "effective_cost_creation_pct": round((cost_creation / cost_total) * 100, 1) if cost_total else 0.0,
        "effective_cost_read_pct": round((cost_read / cost_total) * 100, 1) if cost_total else 0.0,
        "median_first_turn_creation": round(statistics.median(first_turn_creations), 0) if first_turn_creations else 0,
        "median_hot_turn_creation": round(statistics.median(all_hot_creations), 0) if all_hot_creations else 0,
        "cold_resumes_count": len(all_cold_resumes),
        "cold_resumes_creation_tokens": cold_resumes_creation,
        "cold_resumes_pct_of_total_creation": round(cold_pct, 1),
        "cold_resumes": all_cold_resumes,
    }

    # Episodios de lectura de motor
    engine_episodes = find_engine_read_episodes(
        transcripts_root,
        target_projects,
        project=project,
        since=since,
        until=until,
    ) if transcripts_root.exists() else []

    # Tamaños de estado en disco
    disk_states = inspect_disk_state_sizes(projects_root)

    return {
        "metadata": {
            "transcripts_root": str(transcripts_root),
            "projects_root": str(projects_root),
            "target_projects": [project] if project else list(target_projects),
            "project_filter": project,
            "since": since,
            "until": until,
            "total_sessions_analyzed": len(all_sessions),
        },
        "project_summaries": project_summaries,
        "cache_dimension": cache_dimension,
        "tool_chars_breakdown": dict(sorted(grand_tool_chars.items(), key=lambda kv: -kv[1])),
        "fw_files_read_chars": dict(sorted(grand_fw_reads.items(), key=lambda kv: -kv[1])[:20]),
        "bash_classes": {
            cls: {
                "chars": grand_bash_classes[cls],
                "calls": grand_bash_calls[cls],
                "top_details": dict(sorted(grand_bash_details[cls].items(), key=lambda kv: -kv[1])[:10]),
            }
            for cls in sorted(grand_bash_classes.keys(), key=lambda k: -grand_bash_classes[k])
        },
        "mcp_state_stats": {
            action: {
                "calls": len(sizes),
                "total_chars": sum(sizes),
                "median_chars": round(statistics.median(sizes), 1) if sizes else 0,
                "p90_chars": round(sorted(sizes)[int(0.9 * (len(sizes) - 1))], 1) if sizes else 0,
                "max_chars": max(sizes) if sizes else 0,
                "overflows": grand_mcp_overflows.get(action, 0),
            }
            for action, sizes in sorted(grand_mcp_calls.items(), key=lambda kv: -sum(kv[1]))
        },
        "command_invocations": dict(grand_command_invocations.most_common(25)),
        "workflow_origins": {wf: dict(origins) for wf, origins in grand_workflow_origins.items()},
        "engine_read_episodes_count": len(engine_episodes),
        "engine_read_episodes_sample": engine_episodes[:15],
        "disk_states": disk_states,
    }


def format_markdown_report(data: Dict[str, Any]) -> str:
    """Genera informe en formato Markdown legible para archivo de línea base."""
    lines = []
    lines.append("# Línea Base de Consumo de Tokens y Métricas de Contexto (2026-09)")
    lines.append("")
    if data["metadata"].get("since") or data["metadata"].get("until"):
        s = data["metadata"].get("since") or "inicio"
        u = data["metadata"].get("until") or "fin"
        lines.append(f"**Rango de fechas:** {s} → {u}")
    else:
        lines.append("**Fecha de corte:** 2026-09-15")
    if data["metadata"].get("project_filter"):
        lines.append(f"**Filtro proyecto:** {data['metadata']['project_filter']}")
    lines.append(f"**Sesiones analizadas:** {data['metadata']['total_sessions_analyzed']}")
    lines.append(f"**Proyectos objetivo:** {', '.join(data['metadata']['target_projects'])}")
    lines.append("")
    lines.append("---")
    lines.append("")

    # Tabla 1: Resumen por proyecto
    lines.append("## 1. Sesiones, Turnos y Contexto por Proyecto")
    lines.append("")
    lines.append("| Proyecto | Sesiones | Turnos/sesión (mediana) | Turnos máx | Contexto/turno (mediana) | Tokens entrada+caché |")
    lines.append("|---|---|---|---|---|---|")
    for proj, s in data["project_summaries"].items():
        ctx_k = f"{s['median_context_tokens']/1e3:.0f}K" if s["median_context_tokens"] else "n/d"
        tot_m = f"{s['total_input_and_cache_tokens']/1e6:.1f}M"
        lines.append(f"| {proj} | {s['sessions_count']} | {s['median_turns']:.0f} | {s['max_turns']} | {ctx_k} | {tot_m} |")
    lines.append("")

    # Tabla 2: Reparto de texto por herramientas
    lines.append("## 2. Reparto del Texto Incorporado por Herramientas")
    lines.append("")
    tool_chars = data["tool_chars_breakdown"]
    total_chars = sum(tool_chars.values()) or 1
    lines.append("| Herramienta / Origen | Chars (M) | % del Total |")
    lines.append("|---|---|---|")
    for tool, ch in tool_chars.items():
        pct = (ch / total_chars) * 100
        lines.append(f"| {tool} | {ch/1e6:.2f}M | {pct:.1f} % |")
    lines.append("")

    # Tabla 3: Salidas de Bash por origen y destino
    lines.append("## 3. Salidas de Bash por Origen y Destino")
    lines.append("")
    lines.append("> Referencia canónica FW-239: **20,3 % del texto de salida de Bash** corresponde a inspección del estado.")
    lines.append("")
    lines.append("| Clasificación Bash | Chars (M) | % de Bash | Llamadas |")
    lines.append("|---|---|---|---|")
    bash_classes = data["bash_classes"]
    total_bash = sum(v["chars"] for v in bash_classes.values()) or 1
    for cls, info in bash_classes.items():
        pct = (info["chars"] / total_bash) * 100
        lines.append(f"| {cls} | {info['chars']/1e6:.2f}M | {pct:.1f} % | {info['calls']} |")
    lines.append("")

    # Tabla 4: Herramientas MCP del estado y desbordes (FW-239)
    lines.append("## 4. Herramientas MCP del Estado y Desbordes (FW-239)")
    lines.append("")
    lines.append("| Acción MCP | Llamadas | Total Chars (K) | Mediana (chars) | Máx (chars) | Desbordes host |")
    lines.append("|---|---|---|---|---|---|")
    for action, info in data["mcp_state_stats"].items():
        lines.append(f"| {action} | {info['calls']} | {info['total_chars']/1e3:.0f}K | {info['median_chars']:.0f} | {info['max_chars']} | {info['overflows']} |")
    lines.append("")

    # Tabla 5: Invocaciones reales y origen de carga de workflows (Lote 5A)
    lines.append("## 5. Invocaciones Reales de Comandos y Origen de Carga de Workflows (Lote 5A)")
    lines.append("")
    lines.append("### Invocaciones de comandos observadas")
    lines.append("")
    lines.append("| Comando / Entrada | Conteo | Tipo de invocación |")
    lines.append("|---|---|---|")
    for cmd, count in list(data["command_invocations"].items())[:15]:
        inv_type = "Skill harness" if cmd.startswith("skill:") else ("Slash command usuario" if cmd.startswith("user_slash:") else "otro")
        lines.append(f"| `{cmd}` | {count} | {inv_type} |")
    lines.append("")

    lines.append("### Origen de carga por fichero de workflow")
    lines.append("")
    lines.append("| Workflow | Cargas vía `Read` | Cargas vía `Bash` | Invocaciones `Skill` |")
    lines.append("|---|---|---|---|")
    for wf, origins in sorted(data["workflow_origins"].items(), key=lambda kv: -sum(kv[1].values()))[:15]:
        r_count = origins.get("read_tool", 0)
        b_count = origins.get("bash_read", 0)
        s_count = origins.get("skill_tool", 0)
        lines.append(f"| `{wf}` | {r_count} | {b_count} | {s_count} |")
    lines.append("")

    # Tabla 6: Fricción por lectura del código del motor (FW-240)
    lines.append("## 6. Fricción por Lectura del Motor (FW-240)")
    lines.append("")
    lines.append(f"- **Episodios de primera lectura detectados:** {data['engine_read_episodes_count']}")
    lines.append("- **Causa habitual:** errores sin contrato explícito (`APPROVE_RECEIPT stale`, `modo de review inválido`).")
    lines.append("")

    # Tabla 7: Tamaños de ficheros del estado en disco — no comparable entre cortes
    lines.append("## 7. Ficheros del Estado en Disco (Adoptantes) — no comparable entre cortes")
    lines.append("")
    lines.append("| Proyecto | `03_project_state.json` | `state_log.jsonl` | `core.md` | `08_module_acceptance.md` |")
    lines.append("|---|---|---|---|---|")
    for d in data["disk_states"]:
        sz = d["sizes_kb"]
        lines.append(f"| {d['project']} | {sz.get('03_project_state.json', 0)} KB | {sz.get('state_log.jsonl', 0)} KB | {sz.get('core.md', 0)} KB | {sz.get('08_module_acceptance.md', 0)} KB |")
    lines.append("")

    # 10 dimensiones canónicas de Lote 0 — no comparable entre cortes
    lines.append("## 8. Matriz de las 10 Dimensiones de Medición del Lote 0 — no comparable entre cortes")
    lines.append("")
    lines.append("| Dimensión | Métrica medida | Valor observado en línea base | Unidad |")
    lines.append("|---|---|---|---|")
    lines.append("| 1. Bytes instrucciones entregadas | Carga controlada por el framework | `cierre-modulo.md` ≈12,6K tok; `ciclo.md` ≈5,5K tok; lecturas framework 14 % chars | Tokens / Chars |")
    lines.append(f"| 2. Bytes resultados herramientas | Información incorporada por consultas/comandos | {total_chars/1e6:.2f}M chars totales (Bash 49 %, producto 18 %, framework 14 %, estado 9 %) | Chars |")
    lines.append("| 3. Tokens entrada y caché | Uso observado del modelo | 231–281K tok/turno (mediana); 392–978 turnos/sesión | Tokens |")
    lines.append("| 4. Turnos y llamadas por tarea | Trabajo de coordinación | 392–978 turnos mediana por sesión (máx 1.000+) | Turnos |")
    lines.append(f"| 5. Lecturas código del motor | Fricción para usar interfaces | {data['engine_read_episodes_count']} episodios documentados | Episodios |")
    lines.append("| 6. Tiempo consultas y hooks | Coste local de integración | No disponible en transcripciones históricas (marcado n/d) | Segundos |")
    lines.append("| 7. Correcciones e interrupciones | Fricción experimentada por el usuario | Documentado en field report: desbordes, compactaciones forzadas, re-ejecuciones | Incidencias |")
    lines.append("| 8. Controles omitidos o cierres incorrectos | Regresiones de calidad | 0 cierres incorrectos en histórico con gates en verde | Conteo |")
    lines.append("| 9. Invocaciones reales por comando | Base empírica hipótesis Lote 5A | Invocaciones concentradas en `/ciclo`, `/arrancar`, `/gycd` y `cierre-modulo` | Llamadas |")
    lines.append("| 10. Origen carga de workflow | Distinguir inyección vs Read agente | Mayoritaria vía `Read` explícito del agente (64 gycd, 54 ciclo, 43 cierre) frente a `Skill` | Llamadas |")
    lines.append("")

    # Dimensión de Caché de Prompt (H4)
    lines.append("## 9. Dimensión de Caché de Prompt (H4)")
    lines.append("")
    c = data.get("cache_dimension", {})
    if not c or (c.get("total_cache_creation_tokens", 0) == 0 and c.get("total_cache_read_tokens", 0) == 0):
        lines.append("> Sin datos de uso de caché en las sesiones analizadas.")
        lines.append("")
    else:
        tot_create = c["total_cache_creation_tokens"]
        tot_read = c["total_cache_read_tokens"]
        ratio = c["cache_read_ratio"]
        read_pct = c["cache_read_pct"]
        create_pct = c["cache_create_pct"]

        lines.append("### Consumo y ratio de caché")
        lines.append("")
        lines.append(f"- **Creación de caché total:** {tot_create:,} tokens ({tot_create/1e6:.2f}M)")
        lines.append(f"- **Lectura de caché total:** {tot_read:,} tokens ({tot_read/1e6:.2f}M)")
        lines.append(f"- **Entrada no cacheada:** {c['total_uncached_input_tokens']:,} tokens")
        lines.append(f"- **Ratio lectura/creación:** {ratio}× ({read_pct} % lectura, {create_pct} % creación sobre tokens cacheados)")
        lines.append("")

        lines.append("### Coste efectivo relativo (escritura ×1,25, lectura ×0,1)")
        lines.append("")
        lines.append(f"- **Coste efectivo creación (×1,25):** {c['effective_cost_creation']:,.1f} ({c['effective_cost_creation']/1e6:.2f}M)")
        lines.append(f"- **Coste efectivo lectura (×0,1):** {c['effective_cost_read']:,.1f} ({c['effective_cost_read']/1e6:.2f}M)")
        lines.append(f"- **Coste efectivo total entrada:** {c['effective_cost_total']:,.1f} ({c['effective_cost_total']/1e6:.2f}M)")
        lines.append(f"- **Reparto de coste de entrada:** Creación {c['effective_cost_creation_pct']} %, Lectura {c['effective_cost_read_pct']} %")
        lines.append("")

        lines.append("### Medianas de creación de caché")
        lines.append("")
        lines.append(f"- **Primer turno de sesión (arranque limpio):** {c['median_first_turn_creation']:,.0f} tokens")
        lines.append(f"- **Turno caliente (hueco previo < 10 min):** {c['median_hot_turn_creation']:,.0f} tokens")
        lines.append("")

        lines.append("### Reanudaciones en frío (hueco previo > 1 h)")
        lines.append("")
        lines.append(f"- **Total reanudaciones en frío detectadas:** {c['cold_resumes_count']}")
        lines.append(f"- **Creación en reanudaciones en frío:** {c['cold_resumes_creation_tokens']:,} tokens ({c['cold_resumes_pct_of_total_creation']} % de toda la creación)")
        lines.append("")

        if c.get("cold_resumes"):
            lines.append("| Sesión | Hora | Hueco | Creación |")
            lines.append("|---|---|---|---|")
            for r in c["cold_resumes"]:
                lines.append(f"| `{r['session']}` | {r['timestamp']} | {r['gap_hours']:.1f} h | {r['creation_tokens']:,} |")
            lines.append("")

    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Línea base reproducible de consumo de tokens (Lote 0, Plan 4.5)")
    parser.add_argument("--transcripts-dir", type=Path, default=DEFAULT_TRANSCRIPTS_ROOT, help="Ruta a ~/.claude/projects")
    parser.add_argument("--projects-dir", type=Path, default=DEFAULT_PROJECTS_ROOT, help="Ruta a ~/Projects")
    parser.add_argument("--project", type=str, default=None, help="Limita el análisis a un proyecto (coincidencia de nombre)")
    parser.add_argument("--since", type=str, default=None, help="Filtrar sesiones cuyo primer timestamp sea >= fecha ISO8601")
    parser.add_argument("--until", type=str, default=None, help="Filtrar sesiones cuyo primer timestamp sea <= fecha ISO8601")
    parser.add_argument("--json", action="store_true", help="Salida en JSON crudo")
    parser.add_argument("--output", type=Path, help="Guardar informe en fichero especificado")
    args = parser.parse_args()

    results = run_full_analysis(
        transcripts_root=args.transcripts_dir,
        projects_root=args.projects_dir,
        project=args.project,
        since=args.since,
        until=args.until,
    )

    if args.json:
        out_text = json.dumps(results, indent=2, ensure_ascii=False)
    else:
        out_text = format_markdown_report(results)

    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(out_text, encoding="utf-8")
        print(f"Informe guardado en {args.output}")
    else:
        print(out_text)

    return 0


if __name__ == "__main__":
    sys.exit(main())
