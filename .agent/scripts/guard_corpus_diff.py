#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Diferencial del guard destructivo sobre un corpus de comandos reales.

Método de la 4.40 (ADR-065) y de L8 (plan 5.2): pasar la versión nueva y la
publicada por los mismos comandos y comparar veredicto y motivo. Cero
regresiones de `deny` a `pass` y de `deny` a `ask`; cada cambio, listado.

  extract  --out FICHERO [--transcripts DIR]
      Extrae los comandos Bash de las transcripciones locales de Claude Code
      (`~/.claude/projects/**/*.jsonl`) a un JSONL `{"cmd", "cwd"}`.
  diff     --old GUARD.py --corpus FICHERO --report INFORME.json
      Compara el guard indicado (p. ej. `git show main:… > guard_main.py`) con el
      actual. Sale con 1 si hay una regresión.

PRIVACIDAD (condición del builder, 2026-09-19): el corpus real contiene rutas,
nombres y posibles credenciales. Esta herramienta se NIEGA a escribir el corpus
o el informe dentro de un repositorio (el de la plantilla o cualquier otro con
`.git`, sin seguir enlaces simbólicos hacia él) y su salida estándar solo lleva
recuentos: nunca comandos ni motivos.
"""
from __future__ import annotations

import argparse
import collections
import glob
import importlib.util
import json
import signal
import sys
import time
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parents[1]
TIME_LIMIT_S = 20.0


def _refuse_inside_repo(path: Path) -> None:
    """Sale con código 2 si `path` (resuelto, sin crear nada) cae bajo un repositorio."""
    resolved = path.expanduser().resolve()  # sigue enlaces simbólicos de lo que ya existe
    chain = [resolved, *resolved.parents]
    inside = REPO_ROOT in chain or any(
        (p / ".git").exists() or ((p / "HEAD").is_file() and (p / "objects").is_dir())  # incluye repos bare
        for p in chain
    )
    if inside:
        print(
            f"guard_corpus_diff: me niego a escribir {path} dentro de un repositorio: el corpus real "
            "contiene rutas, nombres y posibles credenciales. Usa un directorio fuera de cualquier "
            "repositorio (p. ej. el scratchpad de la sesión).",
            file=sys.stderr,
        )
        raise SystemExit(2)


def _write_private(path: Path, text: str) -> None:
    """Corpus e informe llevan comandos reales: solo legibles por su dueño."""
    import os
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(text)
    os.chmod(path, 0o600)


def _bash_commands(transcript: Path):
    try:
        lines = transcript.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return
    for line in lines:
        if '"Bash"' not in line:
            continue
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        message = entry.get("message") if isinstance(entry, dict) else None
        content = message.get("content") if isinstance(message, dict) else None
        if not isinstance(content, list):
            continue
        for block in content:
            if isinstance(block, dict) and block.get("type") == "tool_use" and block.get("name") == "Bash":
                command = (block.get("input") or {}).get("command")
                if isinstance(command, str):
                    yield command, entry.get("cwd") if isinstance(entry.get("cwd"), str) else None


def cmd_extract(args: argparse.Namespace) -> int:
    out = Path(args.out)
    _refuse_inside_repo(out)
    root = Path(args.transcripts).expanduser()
    files = sorted(glob.glob(str(root / "**" / "*.jsonl"), recursive=True))
    seen: set[tuple[str, str | None]] = set()
    total = 0
    rows: list[str] = []
    for name in files:
        for command, cwd in _bash_commands(Path(name)):
            total += 1
            if (command, cwd) not in seen:
                seen.add((command, cwd))
                rows.append(json.dumps({"cmd": command, "cwd": cwd}, ensure_ascii=False))
    out.parent.mkdir(parents=True, exist_ok=True)
    _write_private(out, "\n".join(rows) + ("\n" if rows else ""))
    print(json.dumps({"transcripts": len(files), "commands": total, "unique": len(rows)}))
    return 0


def _load_old(path: Path):
    spec = importlib.util.spec_from_file_location("guard_old_for_diff", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _Timeout(Exception):
    pass


def _with_limit(fn):
    def _expired(_signum, _frame):
        raise _Timeout

    previous = signal.signal(signal.SIGALRM, _expired)
    signal.setitimer(signal.ITIMER_REAL, TIME_LIMIT_S)
    start = time.monotonic()
    try:
        return fn(), time.monotonic() - start
    except _Timeout:
        return ("error", "timeout"), time.monotonic() - start
    except Exception as exc:  # noqa: BLE001 — un fallo del análisis es un dato del diferencial
        return ("error", type(exc).__name__), time.monotonic() - start
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)


def _ask_class(reason: str) -> str:
    for prefix, name in (
        ("ejecuta una orden guardada", "variable"),
        ("decodifica", "decodificar-y-ejecutar"),
        ("ejecuta contenido decodificado", "decodificar-y-ejecutar"),
        ("escribe ", "fichero-escrito-y-ejecutado"),
        ("código Python", "python"),
    ):
        if reason.startswith(prefix):
            return name
    return "otra"


def cmd_diff(args: argparse.Namespace) -> int:
    report_path = Path(args.report)
    _refuse_inside_repo(report_path)
    sys.path.insert(0, str(SCRIPT_DIR))
    import guard_destructive_command as new  # noqa: PLC0415

    old = _load_old(Path(args.old))
    transitions: collections.Counter[str] = collections.Counter()
    regressions: collections.Counter[str] = collections.Counter()
    ask_classes: collections.Counter[str] = collections.Counter()
    reason_changed = errors = slow = 0
    max_s = 0.0
    items: list[dict] = []
    total = 0
    for line in Path(args.corpus).read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        command, cwd = row["cmd"], row.get("cwd")
        total += 1
        (old_res, t_old) = _with_limit(lambda: old.is_destructive(command))
        (new_res, t_new) = _with_limit(lambda: new.judge(command, cwd))
        (nocwd_res, _) = _with_limit(lambda: new.judge(command, None))
        max_s = max(max_s, t_old, t_new)
        slow += t_new > 2.0
        if old_res[0] == "error" or new_res[0] == "error":
            errors += 1
            transitions["error"] += 1
            items.append({"cmd": command, "cwd": cwd, "old": old_res, "new": new_res, "kind": "error"})
            continue
        old_v, old_reason = ("deny", old_res[1]) if old_res[0] else ("pass", "")
        new_v, new_reason = new_res
        nocwd_v = nocwd_res[0]
        key = f"{old_v}->{new_v}"
        transitions[key] += 1
        if key in ("deny->pass", "deny->ask"):
            regressions[key] += 1
        if new_v == "ask":
            ask_classes[_ask_class(new_reason)] += 1
        changed = key not in ("pass->pass",) and not (key == "deny->deny" and old_reason == new_reason)
        if key == "deny->deny" and old_reason != new_reason:
            reason_changed += 1
        if changed:
            items.append({
                "cmd": command, "cwd": cwd, "transition": key, "old_reason": old_reason,
                "new_reason": new_reason, "new_without_cwd": nocwd_v,
            })
    summary = {
        "commands": total,
        "transitions": dict(transitions),
        "regressions": {"deny->pass": regressions["deny->pass"], "deny->ask": regressions["deny->ask"]},
        "ask_by_class": dict(ask_classes),
        "deny_reason_changed": reason_changed,
        "errors": errors,
        "slow_over_2s": slow,
        "max_seconds": round(max_s, 3),
    }
    report_path.parent.mkdir(parents=True, exist_ok=True)
    _write_private(report_path, json.dumps({**summary, "items": items}, ensure_ascii=False, indent=1))
    print(json.dumps(summary))
    return 1 if sum(summary["regressions"].values()) else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    extract = sub.add_parser("extract")
    extract.add_argument("--out", required=True)
    extract.add_argument("--transcripts", default="~/.claude/projects")
    diff = sub.add_parser("diff")
    diff.add_argument("--old", required=True)
    diff.add_argument("--corpus", required=True)
    diff.add_argument("--report", required=True)
    args = parser.parse_args(argv)
    return cmd_extract(args) if args.command == "extract" else cmd_diff(args)


if __name__ == "__main__":
    sys.exit(main())
