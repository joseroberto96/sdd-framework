#!/usr/bin/env python3
"""Aviso de prompt caducado (5.3.0 L5, FW-318). Solo lectura; nunca bloquea.

Lo llama el paso 0 de `ciclo.md` en el primer `/ciclo` de la sesión. Si la
instrucción recibida no coincide con el siguiente paso guardado
(`session.next_command` + `session.next_action`) y desde el último
`set_session_pointer` hubo mutaciones de estado o commits, imprime una línea
que propone `/reconciliar`. En cualquier otro caso, y ante cualquier fallo,
calla. Sale siempre con 0.

Límites: los eventos de `state_log.jsonl` no llevan identificador de sesión,
así que «de otra sesión» se aproxima con «posterior al último puntero» (por eso
el workflow solo lo llama al empezar). El commit de sesión que hace el propio
`/gycd` tras guardar el puntero no cuenta.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from difflib import SequenceMatcher
from pathlib import Path

SESSION_COMMIT_PREFIX = "chore(sesion)"
_GIT_TIMEOUT_S = 5
_LOG_TAIL_STEP_BYTES = 256 * 1024


def _normalize(text: str) -> str:
    lines = [ln for ln in (text or "").splitlines() if not ln.strip().startswith("Modelo:")]
    flat = re.sub(r"\s+", " ", " ".join(lines)).strip().casefold()
    return re.sub(r"^siguiente:\s*", "", flat)


def _same_prompt(instruction: str, command: str, action: str) -> bool:
    got, want = _normalize(instruction), _normalize(action)
    if not want:
        return True  # sin instrucción guardada no hay con qué comparar
    full = _normalize(f"{command} {action}")
    if want in got or got == full:
        return True
    # PA-FW-011: una paráfrasis del `next_action` guardado es la misma instrucción.
    return max(SequenceMatcher(None, got, want).ratio(), SequenceMatcher(None, got, full).ratio()) >= 0.8


def _events_after_pointer(log_path: Path, tail_step: int = _LOG_TAIL_STEP_BYTES) -> tuple[str, int] | None:
    """(ts del último `set_session_pointer`, eventos posteriores) o None si no hay puntero.

    Lee desde el final en ventanas crecientes para no cargar decenas de MB de
    historia cuando el último `set_session_pointer` está cerca de la cola.
    """
    size = log_path.stat().st_size
    if size <= 0:
        return None
    window = max(4096, tail_step)
    while True:
        with log_path.open("rb") as handle:
            if size > window:
                handle.seek(size - window)
            chunk = handle.read()
        lines = chunk.decode("utf-8").splitlines()
        if size > window and lines:
            lines.pop(0)
        pointer_ts: str | None = None
        later = 0
        for raw in lines:
            if not raw.strip():
                continue
            event = json.loads(raw)
            if event.get("action") == "set_session_pointer":
                pointer_ts, later = str(event.get("ts") or ""), 0
            else:
                later += 1
        if pointer_ts is not None:
            return (pointer_ts, later)
        if window >= size:
            return None
        window *= 4


def count_commits(subjects: list[str]) -> int:
    return sum(1 for s in subjects if s.strip() and not s.startswith(SESSION_COMMIT_PREFIX))


def _commits_since(root: Path, ts: str) -> int:
    if not ts:
        return 0
    try:
        out = subprocess.run(
            ["git", "-C", str(root), "log", f"--since={ts}", "--format=%s"],
            capture_output=True, text=True, timeout=_GIT_TIMEOUT_S, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return 0
    return count_commits(out.stdout.splitlines()) if out.returncode == 0 else 0


def stale_prompt_warning(instruction: str, root: Path) -> str | None:
    """Línea de aviso, o None si el prompt coincide o no hay señal de que caducó."""
    project = Path(root) / "_project"
    state = json.loads((project / "03_project_state.json").read_text(encoding="utf-8"))
    if state.get("framework_mode") != "initialized":
        return None
    session = state.get("session") if isinstance(state.get("session"), dict) else {}
    command, action = str(session.get("next_command") or ""), str(session.get("next_action") or "")
    if not _normalize(instruction) or _same_prompt(instruction, command, action):
        return None
    log_path = project / "state_log.jsonl"
    found = _events_after_pointer(log_path) if log_path.exists() else None
    if found is None:
        return None
    pointer_ts, mutations = found
    commits = _commits_since(Path(root), pointer_ts)
    if not mutations and not commits:
        return None
    return (
        "Aviso: la instrucción recibida no coincide con el siguiente paso guardado y desde entonces "
        f"hubo {mutations} mutaciones de estado y {commits} commits; el prompt puede estar caducado. "
        "Si no es intencionado, usa /reconciliar."
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--instruction", help="instrucción recibida; si falta, se lee de stdin")
    parser.add_argument("--project-root", default=str(Path(__file__).resolve().parents[2]))
    try:
        args = parser.parse_args(argv)
        instruction = args.instruction if args.instruction is not None else sys.stdin.read()
        warning = stale_prompt_warning(instruction, Path(args.project_root))
        if warning:
            print(warning)
    except (Exception, SystemExit):  # noqa: BLE001 — aviso de cortesía: jamás para el turno
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
