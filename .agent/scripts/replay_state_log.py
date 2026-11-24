#!/usr/bin/env python3
"""Replay state_log.jsonl to reconstruct 03_project_state.json.

Disaster-recovery tool: usar SOLO cuando 03_project_state.json está perdido
o corrupto y el bitácora (`state_log.jsonl`) está intacta. No es parte del
camino normal — el camino normal es siempre `mutate_state` vía MCP o CLI.

Qué hace:
  1. Lee cada línea de `state_log.jsonl` en orden.
  2. Re-aplica la mutación lógica al state en memoria (vía `_apply_event`).
  3. NO enforce A4 (add_work_tier módulo declarado) ni A5 (set_phase warn):
     los eventos históricos se aceptan como se loguearon, sin re-validar
     con reglas que pudieron no existir cuando se loguearon. FW-186 extiende
     el principio a cuatro precondiciones que rompían el replay estricto de
     bitácoras sembradas por migración incompleta (FW-173): transición de
     módulo ilegal por regla nueva, `record_approval_receipt` /
     `start_independent_review` sin `add_work_tier` logueado,
     `register_decision` con id por delante del contador, y `supersede_decision`
     cuyo objetivo no se reconstruyó. Corrupción real (línea ilegible, acción
     desconocida, status inválido) sigue abortando.
  4. NO re-loggea: el log queda intacto (last write wins ya está en disco).
  5. Al final, escribe el state reconstruido a `03_project_state.json` y
     regenera las proyecciones (core.md, brief, etc.).

Uso:
  python3 .agent/scripts/replay_state_log.py [--write] [--force] [--log <path>] [--out <path>]

Por defecto (FW-094):
  - Sin --write el replay es SIEMPRE dry-run: imprime qué aplicaría, no escribe.
  - Con --write, antes de sobrescribir un estado existente: (a) guarda backup
    `.bak-<timestamp>` y (b) aborta si el estado reconstruido pierde entidades
    (bugs/decisiones/módulos) respecto al actual, salvo --force.
  --log _project/state_log.jsonl
  --out _project/03_project_state.json
"""
from __future__ import annotations

import argparse
import copy
import fnmatch
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / ".agent" / "scripts"))
import state_core


class ReplayError(RuntimeError):
    """Fallo de replay en modo estricto: no se escribe nada."""  # noqa: E402


def parse_log_line(line: str) -> tuple[str, dict, str] | None:
    """Parsea una línea del log JSONL. Devuelve (action, payload, ts) o None si está corrupta."""
    line = line.strip()
    if not line:
        return None
    try:
        entry = json.loads(line)
    except json.JSONDecodeError:
        return None
    if not isinstance(entry, dict):
        return None
    action = entry.get("action")
    payload = entry.get("payload")
    ts = entry.get("ts", "")
    if not isinstance(action, str) or not isinstance(payload, dict):
        return None
    return action, payload, ts


_ENTITY_FIELDS = ("_bugs", "_clarifications", "_decisions")


def _entity_counts(state: dict) -> dict:
    """Conteo de entidades sustantivas para el guard anti-pérdida (FW-094)."""
    counts = {f: len(state.get(f) or []) for f in _ENTITY_FIELDS}
    mods = state.get("modules") or {}
    counts["modules"] = sum(
        len(mods.get(b) or []) for b in ("completed", "in_progress", "pending", "dropped")
    )
    counts["work_tiers"] = len(state.get("work_tiers") or {})
    return counts


def _check_entity_loss(current_path: Path, reconstructed: dict) -> list[str]:
    """FW-094 (Example Tool): si el out ya existe y es legible, un replay que pierde
    entidades (bugs, decisiones, módulos…) es casi siempre un log incompleto —
    p. ej. un estado construido por migración cuyas entidades nunca pasaron por
    el motor. Devuelve la lista de pérdidas detectadas ([] si no hay)."""
    if not current_path.exists():
        return []
    try:
        current = json.loads(current_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []  # estado corrupto/ilegible: ese ES el caso de disaster-recovery
    if not isinstance(current, dict):
        return []
    cur, new = _entity_counts(current), _entity_counts(reconstructed)
    return [
        f"{field}: {cur[field]} → {new[field]}"
        for field in cur
        if new[field] < cur[field]
    ]


#: Qué acción del log da origen a cada entidad del estado. Solo acciones que
#: CREAN: si una entidad existe sin su acción de origen, nadie la registró.
_BACKING_ACTIONS: dict[str, tuple[str, ...]] = {
    "_decisions": ("register_decision", "import_markdown_entities"),
    "_bugs": ("register_bug", "import_markdown_entities"),
    "_clarifications": ("register_clarification", "import_markdown_entities"),
    "reviews": ("record_review_receipt", "start_independent_review"),
    "approvals": ("record_approval_receipt", "refresh_approval_receipt"),
    # FW-144: un execution receipt escrito a mano en 03_project_state.json no es
    # prueba de que el smoke corriera; solo la bitácora lo sabe.
    "executions": ("record_execution_receipt",),
}


def _logged_ids(log_path: Path) -> dict[str, set[str]]:
    """Huellas que el log declara haber creado, por categoría.

    Se recogen identificadores **y títulos**: el motor autoasigna el id cuando
    el payload no lo trae (así nacieron ADR-009/010 en Example Documents),
    de modo que cruzar solo por id daría por fabricadas entidades legítimas.
    """
    creado: dict[str, set[str]] = {k: set() for k in _BACKING_ACTIONS}
    if not log_path.exists():
        return creado
    for line in log_path.read_text(encoding="utf-8", errors="ignore").splitlines():
        parsed = parse_log_line(line)
        if parsed is None:
            continue
        action, payload, _ = parsed
        if action == "import_markdown_entities":
            for b in payload.get("bugs", []) or []:
                if isinstance(b, dict):
                    for campo in ("id", "description"):
                        valor = b.get(campo)
                        if isinstance(valor, str) and valor.strip():
                            creado["_bugs"].add(valor.strip())
            for c in payload.get("clarifications", []) or []:
                if isinstance(c, dict):
                    for campo in ("id", "question", "title"):
                        valor = c.get(campo)
                        if isinstance(valor, str) and valor.strip():
                            creado["_clarifications"].add(valor.strip())
            for d in payload.get("decisions", []) or []:
                if isinstance(d, dict):
                    for campo in ("id", "title"):
                        valor = d.get(campo)
                        if isinstance(valor, str) and valor.strip():
                            creado["_decisions"].add(valor.strip())
            continue
        for categoria, acciones in _BACKING_ACTIONS.items():
            if action not in acciones:
                continue
            for campo in ("id", "key", "module", "module_id", "task_id", "title", "question"):
                valor = payload.get(campo)
                if isinstance(valor, str) and valor.strip():
                    creado[categoria].add(valor.strip())
                    # los receipts de tarea se registran con y sin prefijo
                    if categoria in ("reviews", "approvals"):
                        creado[categoria].add(f"task:{valor.strip()}")
    return creado


def audit_state_backing(state: dict, log_path: Path) -> dict:
    """Entidades del estado que ninguna acción de la bitácora respalda.

    El estado es la fuente de verdad operativa, pero `state_log.jsonl` es la
    única que registra *qué ocurrió*. Una entidad presente en el primero y
    ausente del segundo llegó por una vía que no es el motor: edición a mano.
    Puede ser legítimo (proyecto migrado, cuyo estado se construyó fuera) o no
    serlo (evidencia fabricada) — esta función no lo juzga, lo expone.

    A diferencia de `replay()`, no reconstruye nada: cruza identificadores, así
    que sigue funcionando cuando el log está incompleto y el replay no puede
    terminar (4 de los 7 proyectos reales, medido el 2026-07-24).
    """
    creado = _logged_ids(log_path)
    informe: dict = {}

    for campo in ("_decisions", "_bugs", "_clarifications"):
        sin_respaldo = []
        for entrada in (state.get(campo) or []):
            if not isinstance(entrada, dict):
                continue
            ident = entrada.get("id")
            if not isinstance(ident, str):
                continue
            # respaldada si el log menciona su id o su título/pregunta
            huellas = {ident} | {
                str(entrada.get(k)).strip()
                for k in ("title", "question")
                if isinstance(entrada.get(k), str) and entrada.get(k).strip()
            }
            if not (huellas & creado[campo]):
                sin_respaldo.append(ident)
        informe[campo] = sorted(sin_respaldo)

    receipts = state.get("workflow_receipts") or {}
    for campo in ("reviews", "approvals", "executions"):
        presentes = set((receipts.get(campo) or {}).keys())
        informe[campo] = sorted(presentes - creado[campo])

    informe["sin_respaldo"] = sum(len(v) for k, v in informe.items() if k != "sin_respaldo")

    # Dirección inversa: el log registra una creación y la entidad ya no está.
    # Cubre el otro modo de manipulación que midió el sabotaje — borrar una
    # decisión del estado, o truncar la propia bitácora — donde no aparece
    # nada de más, sino que falta algo que sí ocurrió.
    desaparecidas: list[str] = []
    for campo in ("_decisions", "_bugs", "_clarifications"):
        ids_estado = {
            e.get("id") for e in (state.get(campo) or []) if isinstance(e, dict)
        }
        for huella in creado[campo]:
            # solo identificadores con forma de id (los títulos no se comparan)
            if re.fullmatch(r"(ADR|BUG|CLAR)-\d+", huella) and huella not in ids_estado:
                desaparecidas.append(f"{campo}:{huella}")
    informe["desaparecidas"] = sorted(desaparecidas)
    return informe


def unbacked_module_receipts(state: dict, module: str, log_path: Path) -> list[str]:
    """Receipts de UN módulo que la bitácora no respalda.

    Versión acotada de `audit_state_backing` para el gate de cierre. Auditar el
    proyecto entero produce ruido inservible en proyectos migrados (93
    entidades legítimamente anteriores al log en Example Tool, medido el 2026-07-24);
    auditar los receipts del módulo que se está cerrando no produce ninguno, y
    es justo donde una evidencia fabricada tendría efecto: sellar un cierre
    tier_2 con una revisión independiente que nunca ocurrió.
    """
    if not log_path.exists():
        return []  # sin bitácora no hay nada que contrastar (no inventamos culpa)
    creado = _logged_ids(log_path)
    receipts = state.get("workflow_receipts") or {}
    problemas: list[str] = []
    for campo in ("reviews", "approvals", "executions"):
        for clave, valor in (receipts.get(campo) or {}).items():
            # FW-165: "reviews" es historial append-only (lista de rondas);
            # "approvals"/"executions" siguen siendo un único dict por clave.
            # Se normaliza a lista aquí para no bifurcar el chequeo: una forma
            # inesperada (ni dict ni lista-de-dicts) simplemente no matchea
            # ningún módulo abajo — no se salta el chequeo en silencio.
            entradas = valor if isinstance(valor, list) else [valor]
            matches_module = any(
                isinstance(entrada, dict)
                and (entrada.get("module") == module or module in str(clave))
                for entrada in entradas
            )
            if not matches_module:
                continue
            if clave not in creado[campo]:
                problemas.append(
                    f"workflow_receipts.{campo}['{clave}'] no tiene evento en "
                    "state_log.jsonl: el receipt está en el estado pero la "
                    "bitácora no registra que se creara. Un receipt escrito a "
                    "mano no es evidencia de que la revisión ocurriera."
                )
    return problemas


def _backup_existing(out_path: Path) -> Path | None:
    """Respaldo del estado actual antes de sobrescribirlo (FW-094)."""
    if not out_path.exists():
        return None
    import datetime as _dt
    import shutil as _shutil
    stamp = _dt.datetime.now().strftime("%Y%m%d%H%M%S")
    bak = out_path.with_name(out_path.name + f".bak-{stamp}")
    _shutil.copy2(out_path, bak)
    return bak


# Claves que el replay siembra con un valor vacío cuando el estado base no las
# trae (espejo de los `setdefault` de `_mutate_locked` y de los handlers).
_BASE_DEFAULTS = {
    "_bugs": list, "_clarifications": list, "_decisions": list,
    "contracts": lambda: {"public_interfaces": {}},
    "modules": lambda: {"completed": [], "in_progress": [], "pending": []},
    "module_statuses": dict, "work_tiers": dict, "module_specs": dict,
    "closure_rejections": list, "active_mcps": list, "_refs": dict,
}


def _normalize_base_state(base_state: dict) -> dict:
    """Copia profunda de `base_state` con las colecciones que el motor espera."""
    state = copy.deepcopy(base_state)
    for key, factory in _BASE_DEFAULTS.items():
        state.setdefault(key, factory())
    return state


def _seed_state() -> dict:
    """Estado semilla del replay sin base (espejo del que construye `replay()`)."""
    return _normalize_base_state({
        "project": "[REPLAY]",
        "framework_mode": "initialized",
        "framework_version": state_core.FRAMEWORK_VERSION,
        "current_phase": "phase_01",
        "current_step": "",
        "last_completed_step": "",
        "stack": {"backend": "", "frontend": "", "database": "", "auth": ""},
        "dependencies": [],
        "architecture_constraints": [],
    })


def apply_events(base_state: dict, lines: list[str], workspace_root: Path | None = None) -> tuple[dict, list[str]]:
    """Aplica líneas del log sobre una copia de `base_state`, sin tocar disco.

    Tolerante: una línea ilegible o un evento que el motor no reproduce se
    devuelve como aviso y no detiene la cadena (el veredicto lo dan las
    diferencias de estado resultantes). Devuelve (estado, avisos).
    """
    warnings: list[str] = []
    manager = state_core.StateManager.__new__(state_core.StateManager)
    manager._is_replay = True
    manager._replay_workspace_root = workspace_root or ROOT
    manager._replay_warnings = warnings
    manager.state_data = _normalize_base_state(base_state)
    manager._state_sig = None
    for num, line in enumerate(lines, 1):
        if not line.strip():
            continue
        parsed = parse_log_line(line)
        if parsed is None:
            warnings.append(f"evento nuevo {num}: línea ilegible, no reproducible")
            continue
        action, payload, ts = parsed
        try:
            manager._apply_event(action, payload, ts or "1970-01-01T00:00:00Z")
        except Exception as e:  # noqa: BLE001
            warnings.append(f"evento nuevo {num} ({action}) no reproducible: {e}")
    state = manager.state_data
    # Lo que el replay sembró vacío y el estado base no traía no es una diferencia.
    for key in _BASE_DEFAULTS:
        if key not in base_state and state.get(key) in ([], {}):
            del state[key]
    return state, warnings


# --- Gate de estado respaldado por la bitácora (N2, plan 5.2 L7) -------------
#
# Clase DURA: si el estado diverge de lo que la bitácora respalda en estas
# raíces (o la bitácora de HEAD no es prefijo de la staged), el commit se
# bloquea. Medido en L6b: 15 de 486 commits históricos (3 %), todos de migración.
STATE_DELTA_HARD = frozenset({
    "modules", "module_statuses", "current_phase", "_bugs", "_clarifications",
    "_decisions", "work_tiers", "module_specs",
})

# Allowlist: campos que el framework escribe legítimamente FUERA de eventos.
# Un comentario por entrada: quién los escribe (medido en L6/L6b).
STATE_DELTA_IGNORE = (
    "workflow_receipts",             # sidecars de preflight/verificación independiente
    "workflow_receipts.*",          # (derived_snapshot, reviews): workflow_receipts.py, sin evento
    "session",                      # punteros de sesión: set_session_pointer y workflows
    "session.*",
    "closure_rejection_archive*",   # archivo de rechazos: upgrade y resolve_closure_rejections
    "execution_profile*",           # perfil de ejecución: upgrades y arranque
    "framework_mode",               # /nuevo-proyecto y /migrar-proyecto
    "last_completed_step",          # update_last_step y cierres de workflow
    "framework_version",            # actualizar-framework / propagate_framework.py
    "guardrails_version",           # idem
)

_MISSING = object()
_SEG_SPLIT = re.compile(r"[.\[]")
_ACK_ACTION = "acknowledge_state_delta"


def _root(path: str) -> str:
    return _SEG_SPLIT.split(path, 1)[0]


def _is_timestamp_path(path: str) -> bool:
    last = _SEG_SPLIT.split(path)[-1].rstrip("]")
    return last in ("generated_at", "timestamp") or last.endswith("_timestamp")


def _is_ignored(path: str, patterns) -> bool:
    return _is_timestamp_path(path) or any(fnmatch.fnmatchcase(path, p) for p in patterns)


def _by_id(items: list):
    if not all(isinstance(e, dict) and isinstance(e.get("id"), str) for e in items):
        return None
    ids = [e["id"] for e in items]
    return {e["id"]: e for e in items} if len(set(ids)) == len(ids) else None


def _collect_diffs(exp, got, path: str, out: list) -> None:
    if isinstance(exp, dict) and isinstance(got, dict):
        for k in sorted(set(exp) | set(got), key=str):
            _collect_diffs(exp.get(k, _MISSING), got.get(k, _MISSING),
                           f"{path}.{k}" if path else str(k), out)
        return
    if isinstance(exp, list) and isinstance(got, list):
        ei, gi = _by_id(exp), _by_id(got)
        if ei is not None and gi is not None:
            for k in sorted(set(ei) | set(gi)):
                _collect_diffs(ei.get(k, _MISSING), gi.get(k, _MISSING), f"{path}[{k}]", out)
            return
    if exp != got:
        out.append((path, exp, got))


def _canon(x) -> str:
    return json.dumps(x, sort_keys=True, ensure_ascii=False, default=str)


def _same_multiset(exp, got) -> bool:
    return (isinstance(exp, list) and isinstance(got, list) and exp != got
            and sorted(map(_canon, exp)) == sorted(map(_canon, got)))


def _empty_or_missing(x) -> bool:
    return x is _MISSING or x is None or x == [] or x == {}


def _fmt(x) -> str:
    text = "<ausente>" if x is _MISSING else _canon(x)
    return text if len(text) <= 120 else text[:117] + "..."


def _parse_acks(new_lines: list[str]) -> tuple[list[dict], list[str]]:
    """Eventos `acknowledge_state_delta` de las líneas NUEVAS. (válidos, problemas)."""
    acks: list[dict] = []
    problems: list[str] = []
    for line in new_lines:
        parsed = parse_log_line(line)
        if parsed is None or parsed[0] != _ACK_ACTION:
            continue
        payload = parsed[1]
        reason, approver, paths = payload.get("reason"), payload.get("approver"), payload.get("paths")
        if (isinstance(reason, str) and reason.strip() and isinstance(approver, str) and approver.strip()
                and isinstance(paths, list) and paths and all(isinstance(x, str) and x for x in paths)):
            acks.append({"reason": reason.strip(), "approver": approver.strip(), "paths": list(paths)})
        else:
            problems.append(f"ERROR: {_ACK_ACTION} inválido en la bitácora (falta reason, approver o paths)")
    return acks, problems


def verify_state_delta(head_state: dict, head_log_lines: list[str], staged_state: dict,
                       staged_log_lines: list[str], ignore_paths=None,
                       workspace_root: Path | None = None) -> list[str]:
    """N2: ¿el estado staged es lo que la bitácora staged respalda?

    Devuelve problemas legibles, cada uno prefijado `ERROR:` (bloquea el commit)
    o `WARN:`. Reglas:
      - el log de HEAD debe ser prefijo exacto del staged;
      - si el estado cambia (fuera de la allowlist) y el log no aporta eventos
        nuevos: ERROR;
      - con eventos nuevos, se reproducen sobre el estado de HEAD y se compara:
        divergencia en la clase dura → ERROR; en el resto → WARN;
      - `acknowledge_state_delta` (rutas literales) degrada un ERROR de estado a
        WARN y su uso se imprime siempre; no excusa un log truncado.
    Normalizaciones (solo fuera de la clase dura): mismos elementos en otro orden
    (salvo `modules.in_progress`, cuyo primer elemento es el módulo activo: WARN)
    y ausente frente a `[]`/`{}`/`null`.
    """
    patterns = tuple(STATE_DELTA_IGNORE) + tuple(ignore_paths or ())
    head = [l.strip() for l in head_log_lines if l.strip()]
    staged = [l.strip() for l in staged_log_lines if l.strip()]
    if staged[:len(head)] != head:
        return [
            "ERROR: bitácora truncada o reescrita: el log de HEAD no es prefijo del log staged "
            f"(HEAD tiene {len(head)} eventos, staged {len(staged)}; se borró, truncó o reescribió historia)"
        ]
    new = staged[len(head):]
    problems: list[str] = []

    acks, ack_problems = _parse_acks(new)
    problems += ack_problems
    for a in acks:
        problems.append(
            f"WARN: {_ACK_ACTION} en uso — aprobador={a['approver']!r} motivo={a['reason']!r} "
            f"rutas={a['paths']}"
        )
    covered = {p for a in acks for p in a["paths"]}

    if new:
        expected, warns = apply_events(head_state, new, workspace_root)
        problems += [f"WARN: {w}" for w in warns]
    else:
        expected = copy.deepcopy(head_state)
    got = copy.deepcopy(staged_state)
    for key in _BASE_DEFAULTS:
        if key not in head_state and got.get(key) in ([], {}):
            del got[key]

    raw: list = []
    _collect_diffs(expected, got, "", raw)
    order_warns: list[str] = []
    diffs: list = []
    for path, exp, act in raw:
        if _is_ignored(path, patterns):
            continue
        hard = _root(path) in STATE_DELTA_HARD
        if not hard and _empty_or_missing(exp) and _empty_or_missing(act):
            continue
        if _same_multiset(exp, act):
            if path == "modules.in_progress":
                order_warns.append(
                    f"WARN: modules.in_progress cambió de orden (el primero es el módulo activo): "
                    f"antes {exp[0]!r}, ahora {act[0]!r}; orden completo {_fmt(exp)} → {_fmt(act)}"
                )
            continue
        diffs.append((path, exp, act, hard))
    problems += order_warns

    if diffs and not new:
        problems.append(
            "ERROR: el estado cambió sin eventos nuevos en la bitácora (¿editado a mano, o "
            "state_log.jsonl no está en el stage?). Muta solo con el motor "
            "(mutate_state / state_manager.py mutate) y revisa con /reconciliar."
        )
    for path, exp, act, hard in diffs:
        detalle = f"{path}: esperado {_fmt(exp)}, encontrado {_fmt(act)}"
        if not new:
            problems.append(f"ERROR: {detalle}")
        elif hard and path in covered:
            problems.append(f"WARN: {detalle} (admitido por {_ACK_ACTION})")
        elif hard:
            problems.append(
                f"ERROR: {detalle} — la bitácora no respalda este cambio en la clase dura. "
                f"Muta con el motor y revisa con /reconciliar; si es una migración legítima, "
                f"registra {_ACK_ACTION} con esta ruta literal."
            )
        else:
            problems.append(f"WARN: {detalle}")
    return problems


def _seed_lines(log_path: Path) -> list[str]:
    """Líneas `seed_snapshot` del log, en orden."""
    out = []
    for line in Path(log_path).read_text(encoding="utf-8").splitlines():
        parsed = parse_log_line(line) if line.strip() else None
        if parsed is not None and parsed[0] == "seed_snapshot":
            out.append(line)
    return out


def _seed_covered_events(log_path: Path, lines: list[str]) -> int:
    """Eventos del log que el estado de una semilla por referencia ya incluye.

    El estado en el commit S de la semilla contiene los efectos de los eventos que
    el log tenía en S; reaplicarlos duplicaría lo no idempotente (BUG-002 fantasma).
    Devuelve cuántos eventos iniciales se saltan: los del log en el mayor `source_commit`
    referenciado, solo si son prefijo exacto del log actual. 0 si no aplica.
    """
    log_path = Path(log_path).resolve()
    repo = log_path.parent.parent if log_path.parent.name == "_project" else log_path.parent
    rel = log_path.relative_to(repo).as_posix()
    current = [l.strip() for l in lines if l.strip()]
    covered = 0
    for line in _seed_lines(log_path):
        parsed = parse_log_line(line)
        payload = parsed[1] if parsed else {}
        commit = payload.get("source_commit")
        if payload.get("entities") is not None or not isinstance(commit, str) or not commit.strip():
            continue
        show = subprocess.run(["git", "-C", str(repo), "show", f"{commit.strip()}:{rel}"],
                              capture_output=True, text=True)
        if show.returncode != 0:
            continue
        at_seed = [l.strip() for l in show.stdout.splitlines() if l.strip()]
        if current[:len(at_seed)] == at_seed:
            covered = max(covered, len(at_seed))
    return covered


def anchor_state(log_path: Path, warnings: list | None = None) -> dict | None:
    """FW-252: estado del commit PADRE del que introdujo el primer evento del log.

    Si el primer evento está en el commit raíz (sin padre) devuelve la semilla del
    replay. None si no se puede anclar (sin git, sin historia, sin estado en el padre).

    L14 (D9c): `seed_snapshot` declara la base previa al log, así que se aplica
    ANTES que el resto (sea cual sea su posición en la bitácora), y por referencia
    se carga desde git verificando el hash. Con semilla, un padre sin estado (caso
    Example Desktop) ya no impide anclar: la base es la semilla del replay. Los fallos
    de la semilla (commit inalcanzable, hash distinto) se añaden a `warnings`.
    """
    log_path = Path(log_path).resolve()
    repo = log_path.parent.parent if log_path.parent.name == "_project" else log_path.parent
    base = _anchor_base(log_path, repo)
    seeds = _seed_lines(log_path)
    if not seeds:
        return base
    if base is None:
        base = _seed_state()
    seeded, seed_warnings = apply_events(base, seeds, workspace_root=repo)
    if warnings is not None:
        warnings.extend(w.replace("evento nuevo", "semilla") for w in seed_warnings)
    return seeded


def _anchor_base(log_path: Path, repo: Path) -> dict | None:
    def git(*args):
        return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True)

    rel = log_path.relative_to(repo).as_posix()
    res = git("log", "--reverse", "--format=%H", "--", rel)
    if res.returncode != 0:
        return None
    for sha in res.stdout.split():
        show = git("show", f"{sha}:{rel}")
        if show.returncode == 0 and show.stdout.strip():
            parent = git("rev-parse", "--verify", "-q", f"{sha}^")
            if parent.returncode != 0:
                # El primer evento vive en el commit raíz: no hay estado previo
                # que anclar, la base es la semilla del replay.
                return _seed_state()
            state_rel = (log_path.parent / "03_project_state.json").relative_to(repo).as_posix()
            old = git("show", f"{parent.stdout.strip()}:{state_rel}")
            if old.returncode != 0:
                return None
            try:
                return json.loads(old.stdout)
            except json.JSONDecodeError:
                return None
    return None


def anchored_report(log_path: Path, state_path: Path) -> dict:
    """Reproduce el log entero sobre el estado ancla y lo compara con el actual."""
    seed_warnings: list[str] = []
    anchor = anchor_state(log_path, seed_warnings)
    if anchor is None:
        return {"anchored": False, "reproduces": False, "divergences": [],
                "warnings": ["sin ancla: no hay historia git del log o el commit padre no tiene estado"]}
    lines = Path(log_path).read_text(encoding="utf-8").splitlines()
    current = json.loads(Path(state_path).read_text(encoding="utf-8"))
    non_empty = [l for l in lines if l.strip()]
    lines = non_empty[_seed_covered_events(log_path, non_empty):]
    problems = verify_state_delta(anchor, [], current, lines,
                                  workspace_root=Path(log_path).resolve().parent.parent)
    divergences = [p for p in problems if not p.startswith(f"WARN: {_ACK_ACTION} en uso")]
    return {"anchored": True, "reproduces": not divergences and not seed_warnings,
            "divergences": divergences, "warnings": seed_warnings}


def replay(log_path: Path, out_path: Path, dry_run: bool = False, strict: bool = True,
           force: bool = False, base_state: dict | None = None) -> dict:
    """Replay el log y devuelve un dict con estadísticas + el state reconstruido.

    Estructura del resultado:
      {"applied": int, "skipped": int, "errors": [(ts, action, error_str)],
       "state": dict | None}
    """
    if not log_path.exists():
        raise FileNotFoundError(f"Log no encontrado: {log_path}")

    # Construir un manager sin tocar disco: la replay es disaster-recovery y
    # no debe depender del state actual (que puede estar corrupto o faltante).
    manager = state_core.StateManager.__new__(state_core.StateManager)
    manager._is_replay = True
    # La replay puede reconstruir un log EXTERNO (disaster-recovery de otro
    # workspace). Sin fijar la raíz, el motor resolvería rutas contra el
    # workspace actual y validaría los eventos ajenos contra artefactos que no
    # les corresponden. `_workspace_root()` lee este atributo (FW-174).
    resolved_log = log_path.resolve()
    manager._replay_workspace_root = (
        resolved_log.parent.parent
        if resolved_log.parent.name == "_project"
        else resolved_log.parent
    )
    if base_state is not None:
        manager.state_data = _normalize_base_state(base_state)
    else:
        manager.state_data = {
            "project": "[REPLAY]",
            "framework_mode": "initialized",
            "framework_version": state_core.FRAMEWORK_VERSION,
            "current_phase": "phase_01",
            "current_step": "",
            "last_completed_step": "",
            "stack": {"backend": "", "frontend": "", "database": "", "auth": ""},
            "dependencies": [],
            "architecture_constraints": [],
            "contracts": {"public_interfaces": {}},
            "modules": {"completed": [], "in_progress": [], "pending": []},
            "module_statuses": {},
            "work_tiers": {},
            "module_specs": {},
            "closure_rejections": [],
            "active_mcps": [],
            "_refs": {},
            # TL FW-009: los handlers de register_decision/register_bug/
            # register_clarification acceden a estas colecciones con indexación
            # directa; sin ellas, un log cuyo PRIMER evento sea register_decision
            # abortaba el replay estricto ('_decisions' KeyError) y la bitácora
            # no servía como mecanismo de reconstrucción. Espejo exacto de los
            # setdefault de `_mutate_locked` (el resto de claves — session,
            # workflow_receipts — se crean on-demand vía setdefault en los
            # handlers, igual que en el camino vivo).
            "_bugs": [],
            "_clarifications": [],
            "_decisions": [],
        }
    manager._state_sig = None

    stats = {"applied": 0, "skipped": 0, "errors": []}

    with log_path.open(encoding="utf-8") as f:
        for num, line in enumerate(f, 1):
            if not line.strip():
                continue
            parsed = parse_log_line(line)
            if parsed is None:
                # Triaje 2026-07-09: replay ESTRICTO por defecto — una línea
                # ilegible durante disaster-recovery es señal de log dañado y
                # continuar produciría un estado silenciosamente incompleto.
                if strict:
                    raise ReplayError(
                        f"línea {num} ilegible en {log_path} — replay estricto aborta. "
                        "Para recuperación parcial explícita usar --salvage con --out temporal."
                    )
                stats["skipped"] += 1
                continue
            action, payload, ts = parsed
            try:
                manager._apply_event(action, payload, ts or "1970-01-01T00:00:00Z")
                stats["applied"] += 1
            except Exception as e:  # noqa: BLE001
                if strict:
                    raise ReplayError(
                        f"evento {num} ({action}) falló durante replay estricto: {e}. "
                        "Para recuperación parcial explícita usar --salvage con --out temporal."
                    ) from e
                # Modo salvage: un evento que falla no detiene la cadena.
                stats["errors"].append((ts, action, str(e)))
                stats["skipped"] += 1

    if not dry_run:
        manager.state_data.setdefault("framework_mode", "initialized")
        # Validar el estado reconstruido ANTES de escribir: un replay que
        # produce un estado inválido no debe publicarlo (fail closed en
        # estricto; en salvage se reporta y se escribe igualmente al --out
        # explícito, que nunca es el canónico).
        try:
            manager.validate_state(manager.state_data)
        except ValueError as e:
            if strict:
                raise ReplayError(f"estado reconstruido inválido: {e}") from e
            stats["errors"].append(("(post)", "validate_state", str(e)))
        # FW-094: guard anti-pérdida — la validación de schema no detecta un
        # estado semánticamente vacío (log incompleto sobre estado migrado).
        losses = _check_entity_loss(out_path, manager.state_data)
        if losses and not force:
            raise ReplayError(
                "el estado reconstruido PIERDE entidades respecto al actual "
                f"({'; '.join(losses)}). El log probablemente no contiene la "
                "historia completa (estado construido por migración o log "
                "truncado). Nada escrito. Si la pérdida es intencional, "
                "repite con --force."
            )
        bak = _backup_existing(out_path)
        if bak is not None:
            stats["backup"] = str(bak)
        _save_state_to(manager.state_data, out_path)
        try:
            _render_projections_to(manager.state_data, out_path.parent.parent)
        except Exception as e:  # noqa: BLE001
            if strict:
                raise ReplayError(f"render de proyecciones falló tras replay: {e}") from e
            stats["errors"].append(("(post)", "render", str(e)))

    stats["state"] = None if dry_run else manager.state_data
    return stats


def _save_state_to(state_data: dict, out_path: Path) -> None:
    """Escribe state_data a out_path con escritura atómica (tmp + os.replace)."""
    import os
    import tempfile as _tempfile
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = _tempfile.mkstemp(prefix=".replay-", suffix=".json.tmp", dir=out_path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(state_data, f, indent=2, sort_keys=True, ensure_ascii=False)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, out_path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _render_projections_to(state_data: dict, project_root: Path) -> None:
    """Regenera las proyecciones en sus paths canónicos dentro de project_root.

    Wrapper sobre projections.render que temporalmente apunta los paths
    canónicos a project_root para que las proyecciones caigan en el lugar
    correcto (no en el ROOT del framework que es donde corre el script).
    """
    # Los paths canónicos viven en state_core (projections los lee de ahí en
    # tiempo de llamada). El bug original parcheaba atributos inexistentes de
    # `projections` → AttributeError silenciado y proyecciones nunca regeneradas
    # (triaje 2026-07-09, auditoría Codex).
    import projections
    saved = (
        state_core.CORE_FILE_PATH,
        state_core.BUGS_FILE_PATH,
        state_core.CLARIFICATIONS_FILE_PATH,
        state_core.DECISIONS_FILE_PATH,
        state_core.SESSION_BRIEF_PATH,
    )
    try:
        (project_root / "_project").mkdir(parents=True, exist_ok=True)
        state_core.CORE_FILE_PATH = project_root / "_project" / "core.md"
        state_core.BUGS_FILE_PATH = project_root / "_project" / "06_bug_fixes.md"
        state_core.CLARIFICATIONS_FILE_PATH = project_root / "_project" / "07_pending_clarifications.md"
        state_core.DECISIONS_FILE_PATH = project_root / "_project" / "03_arch_decisions.md"
        state_core.SESSION_BRIEF_PATH = project_root / "_project" / "00_session_brief.json"
        projections.render(state_data)
    finally:
        (state_core.CORE_FILE_PATH,
         state_core.BUGS_FILE_PATH,
         state_core.CLARIFICATIONS_FILE_PATH,
         state_core.DECISIONS_FILE_PATH,
         state_core.SESSION_BRIEF_PATH) = saved


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "--log",
        type=Path,
        default=ROOT / "_project" / "state_log.jsonl",
        help="Ruta al state log (default: _project/state_log.jsonl)",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=ROOT / "_project" / "03_project_state.json",
        help="Ruta de salida del state reconstruido (default: _project/03_project_state.json)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="No escribe el state; solo imprime estadísticas (comportamiento por defecto desde FW-094)",
    )
    parser.add_argument(
        "--write",
        action="store_true",
        help="Escribe el state reconstruido. Sin este flag el replay es dry-run (FW-094).",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Escribe aunque el estado reconstruido pierda entidades respecto al actual (FW-094).",
    )
    parser.add_argument(
        "--salvage",
        action="store_true",
        help="Recuperación parcial: tolera líneas/eventos rotos. NUNCA sobre el canónico — exige --out explícito distinto.",
    )
    parser.add_argument(
        "--anchor",
        choices=("auto",),
        help="FW-252: reproduce el log sobre el estado del commit padre del primer evento "
        "(en vez del estado semilla) y compara con el estado actual.",
    )
    parser.add_argument(
        "--audit",
        action="store_true",
        help="Solo auditoría: lista las entidades del estado que la bitácora no respalda. No reconstruye ni escribe nada.",
    )
    args = parser.parse_args()

    if args.audit:
        estado_path = args.out
        if not estado_path.exists():
            print(f"ERROR: estado no encontrado: {estado_path}", file=sys.stderr)
            return 1
        try:
            estado = json.loads(estado_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as e:
            print(f"ERROR: estado ilegible: {e}", file=sys.stderr)
            return 1
        # La auditoría contrasta el estado contra la bitácora, así que lo
        # primero es saber si la bitácora misma es de fiar.
        cadena = state_core.verify_log_chain(args.log)
        if cadena:
            print("CADENA DE LA BITÁCORA: ROTA")
            for p in cadena:
                print(f"  {p}")
            print(
                "\nLa bitácora fue modificada después de escribirse. Todo lo que se\n"
                "apoye en ella (auditorías, replay, gates de cierre) queda en duda:\n"
                "recuperarla desde git antes de seguir."
            )
            return 2

        informe = audit_state_backing(estado, args.log)
        desaparecidas = informe.get("desaparecidas") or []
        if not informe["sin_respaldo"] and not desaparecidas:
            print("AUDITORÍA DE RESPALDO: OK — el estado y la bitácora se corresponden.")
            return 0
        if informe["sin_respaldo"]:
            print(f"AUDITORÍA DE RESPALDO: {informe['sin_respaldo']} entidades sin evento que las cree.")
        for campo, ids in informe.items():
            if campo in ("sin_respaldo", "desaparecidas") or not ids:
                continue
            print(f"  {campo}: {', '.join(ids)}")
        if desaparecidas:
            print(
                f"\n{len(desaparecidas)} entidades que la bitácora registró y ya no están "
                "en el estado:"
            )
            print(f"  {', '.join(desaparecidas)}")
            print(
                "  Alguien las borró del estado, o la bitácora se truncó. En ambos casos\n"
                "  el historial y el estado dejaron de contar la misma historia."
            )
        print(
            "\nUna entidad sin respaldo llegó al estado por una vía que no es el motor.\n"
            "Legítimo si el proyecto se migró desde un formato anterior (su historia\n"
            "es previa a la bitácora). Si no es el caso, alguien editó\n"
            "03_project_state.json a mano y esa evidencia no ocurrió."
        )
        return 1

    if args.write and args.dry_run:
        print("ERROR: --write y --dry-run son excluyentes.", file=sys.stderr)
        return 2
    anchor = None
    if args.anchor == "auto":
        report = anchored_report(args.log, args.out) if args.out.exists() else None
        anchor = anchor_state(args.log)
        if anchor is None:
            print("ERROR: --anchor auto no encontró ancla (sin historia git del log, o el commit padre no tiene estado).",
                  file=sys.stderr)
            return 2
        if report is not None:
            print("REPLAY ANCLADO: " + ("REPRODUCE el estado actual." if report["reproduces"]
                                        else "NO reproduce el estado actual."))
            for w in report["warnings"]:
                print(f"  {w}")
            for d in report["divergences"][:15]:
                print(f"  {d}")
            if len(report["divergences"]) > 15:
                print(f"  ... y {len(report['divergences']) - 15} más")
            if not report["reproduces"]:
                print("  Las entidades anteriores al log necesitan la acción auditada seed_snapshot (FW-252).")
        if not args.write:
            return 0 if report is None or report["reproduces"] else 1
    # FW-094 (Example Tool): escribir es opt-in. Un replay invocado sin flags durante
    # un diagnóstico reescribía el estado real desde un log incompleto.
    effective_dry_run = not args.write

    canonical = ROOT / "_project" / "03_project_state.json"
    if args.salvage and args.out.resolve() == canonical.resolve():
        print(
            "ERROR: --salvage no puede escribir sobre el estado canónico. "
            "Usa --out <ruta temporal> y revisa el resultado antes de promoverlo.",
            file=sys.stderr,
        )
        return 2

    try:
        stats = replay(args.log, args.out, dry_run=effective_dry_run,
                       strict=not args.salvage, force=args.force, base_state=anchor)
    except FileNotFoundError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1
    except ReplayError as e:
        print(f"ERROR (replay estricto, nada escrito): {e}", file=sys.stderr)
        return 2

    print(f"Replay: applied={stats['applied']} skipped={stats['skipped']} errors={len(stats['errors'])}")
    if stats["errors"]:
        print("Errores durante el replay (evento no aplicado):")
        for ts, action, err in stats["errors"][:10]:
            print(f"  [{ts}] {action}: {err}")
        if len(stats["errors"]) > 10:
            print(f"  ... y {len(stats['errors']) - 10} más")
    if effective_dry_run:
        print("(dry-run: state no escrito)")
        if not args.dry_run:
            print("Para escribir el estado reconstruido, repite con --write.")
    else:
        if stats.get("backup"):
            print(f"Backup del estado previo: {stats['backup']}")
        print(f"State escrito a {args.out}")
        print("Regenera proyecciones con: python3 .agent/scripts/state_manager.py render")
        # Recordatorio post-replay
        mode = stats["state"].get("framework_mode") if stats["state"] else None
        if mode == "initialized" and not stats["state"].get("execution_profile"):
            print(
                "⚠️  El state reconstruido es initialized pero no tiene execution_profile. "
                "Decláralo antes de cerrar módulos:"
                "  python3 .agent/scripts/state_manager.py mutate --action set_execution_profile "
                '--payload \'{"capability_tier": "standard"}\''
            )
    return 0


if __name__ == "__main__":
    sys.exit(main())
