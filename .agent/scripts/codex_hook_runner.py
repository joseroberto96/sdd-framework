#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Adaptador runtime de hooks Codex para el framework.

El runner mantiene los contratos existentes de Claude: delega el bloqueo al
Production Command Guard y delega el formateo a ``post_edit_format.sh`` sin
modificar ninguno de esos componentes compartidos. Solo añade la traducción
del formato de evento y la resolución segura de la raíz del proyecto.
"""
from __future__ import annotations

import argparse
import fcntl
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any


SUPPORTED_HOOKS = {"guard", "state", "format", "stop"}
STATE_MESSAGE = "project_state.json actualizado. Si acabas de cerrar un módulo, ejecuta /cierre-modulo."
STOP_MESSAGE = "Sesión terminada. Si trabajaste en el proyecto, ejecuta /gycd para cerrar ordenadamente y guardar el estado."
# FW-237: por encima del límite interno del guard (20 s), para que el veredicto
# del propio guard llegue antes; si aun así no responde, se deniega.
GUARD_TIMEOUT_S = 30.0
SMOKE_MARKER_ENV = "CODEX_FRAMEWORK_SMOKE_MARKER"
SMOKE_RUN_ID_ENV = "CODEX_FRAMEWORK_SMOKE_RUN_ID"


def _candidate_roots(seed: Path):
    seed = seed.expanduser().resolve()
    if seed.is_file():
        seed = seed.parent
    yield seed
    yield from seed.parents


def find_project_root(event: dict[str, Any], start: Path | None = None) -> Path | None:
    """Encuentra la raíz sin depender de que Codex invoque el hook desde ella."""
    seeds: list[Path] = []
    event_cwd = event.get("cwd")
    if isinstance(event_cwd, str) and event_cwd.strip():
        seeds.append(Path(event_cwd))
    if start is not None:
        seeds.append(start)
    try:
        seeds.append(Path.cwd())
    except OSError:  # FW-363: el directorio actual puede haber desaparecido
        pass
    seen: set[Path] = set()
    for seed in seeds:
        try:
            candidates = list(_candidate_roots(seed))
        except OSError:
            continue
        for candidate in candidates:
            if candidate in seen:
                continue
            seen.add(candidate)
            scripts = candidate / ".agent" / "scripts"
            if (scripts / "codex_hook_runner.py").is_file() and (scripts / "guard_destructive_command.py").is_file():
                return candidate
    return None


def _tool_paths(event: dict[str, Any]) -> list[str]:
    tool_input = event.get("tool_input")
    if not isinstance(tool_input, dict):
        return []
    paths: list[str] = []
    for key in ("file_path", "path"):
        value = tool_input.get(key)
        if isinstance(value, str):
            paths.append(value)
    command = tool_input.get("command")
    if isinstance(command, str):
        for line in command.splitlines():
            for marker in ("*** Update File: ", "*** Add File: "):
                if line.startswith(marker):
                    paths.append(line[len(marker):].strip())
    return list(dict.fromkeys(paths))


def _tool_path(event: dict[str, Any]) -> str:
    paths = _tool_paths(event)
    return paths[0] if paths else ""


def _emit_message(message: str) -> None:
    sys.stdout.write(json.dumps({"systemMessage": message}))  # ASCII: no depende de la codificación de stdout


def _smoke_directory(root: Path) -> Path:
    """Única carpeta donde puede escribir el marcador: `smoke/` dentro de la telemetría del proyecto."""
    scripts = str(root / ".agent" / "scripts")
    if scripts not in sys.path:
        sys.path.insert(0, scripts)
    import turn_close_hook

    return turn_close_hook.telemetry_dir(root) / "smoke"


def _write_smoke_marker(hook_id: str, root: Path, *, phase: str, status: str) -> None:
    """Añade evidencia JSONL sin permitir que un hook pise otro evento.

    Solo escribe si la ruta del marcador está directamente en `smoke/` de la telemetría del proyecto:
    cualquier otra se rechaza (sin reubicarla) y se avisa por stderr."""
    marker_text = os.environ.get(SMOKE_MARKER_ENV)
    if not marker_text:
        return
    if hook_id not in SUPPORTED_HOOKS or phase not in {"started", "completed", "error"} or status not in {"pass", "fail", "unavailable"}:
        return
    record = {
        "schema_version": 1,
        "run_id": os.environ.get(SMOKE_RUN_ID_ENV) or "manual",
        "hook_id": hook_id,
        "phase": phase,
        "status": status,
    }
    try:
        import turn_close_hook

        directory = _smoke_directory(root)
        marker_path = Path(marker_text)
        if marker_path.parent != directory or marker_path.name in ("", ".", ".."):
            print(f"Codex hook: el marcador de smoke debe estar directamente en {directory}; no se escribe evidencia",
                  file=sys.stderr)
            return
        project_fd = turn_close_hook.open_telemetry_dir(root)
        smoke_fd = turn_close_hook.open_own_dir(project_fd, "smoke") if project_fd is not None else None
        if project_fd is not None:
            os.close(project_fd)
        if smoke_fd is None:
            print("Codex hook: la carpeta de smoke no es un directorio propio y real; no se escribe evidencia",
                  file=sys.stderr)
            return
        try:
            # O_NONBLOCK: un FIFO puesto en el nombre no puede colgar el hook antes de la comprobación.
            fd = os.open(marker_path.name, os.O_WRONLY | os.O_APPEND | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
                         | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_CLOEXEC", 0), 0o600, dir_fd=smoke_fd)
            try:
                if not turn_close_hook.is_own_regular_file(fd):
                    print("Codex hook: el marcador de smoke no es un fichero regular y propio; no se escribe evidencia",
                          file=sys.stderr)
                    return
                fcntl.flock(fd, fcntl.LOCK_EX)
                try:
                    os.write(fd, (json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8"))
                    os.fsync(fd)
                finally:
                    fcntl.flock(fd, fcntl.LOCK_UN)
            finally:
                os.close(fd)
        finally:
            os.close(smoke_fd)
    except Exception as exc:  # noqa: BLE001 - la evidencia es opcional: un fallo no rompe el hook
        print(f"Codex hook: no se pudo escribir evidencia de smoke: {type(exc).__name__}: {exc}", file=sys.stderr)


def _run_guard(root: Path, raw: str) -> int:
    script = root / ".agent" / "scripts" / "guard_destructive_command.py"
    try:
        result = subprocess.run(
            [sys.executable, str(script)],
            cwd=root,
            input=raw,
            capture_output=True,
            text=True,
            check=False,
            timeout=GUARD_TIMEOUT_S,
        )
    except subprocess.TimeoutExpired:
        _emit_guard_deny(
            f"el guard no respondió en {GUARD_TIMEOUT_S:g} s; parte el comando o mueve el contenido grande a un fichero"
        )
        return 0
    except OSError as exc:
        print(f"Codex hook guard: no se pudo ejecutar el guardia: {exc}", file=sys.stderr)
        # Sin veredicto no es «no destructivo»: Codex seguiría con el comando (fail-open).
        _emit_guard_deny(f"no se pudo ejecutar el guard ({type(exc).__name__})")
        return 0
    if result.stdout:
        sys.stdout.write(_translate_guard_output(result.stdout))
    if result.stderr:
        sys.stderr.write(result.stderr)
    if result.returncode != 0 and not result.stdout.strip():
        # El guard sale siempre con 0 y solo calla ante un evento que no le toca:
        # morir sin veredicto no es "no destructivo" (FW-237, revisión 2026-09-15).
        _emit_guard_deny(f"el guard terminó con código {result.returncode} sin veredicto; revisar su instalación")
    return 0


def _translate_guard_output(stdout: str) -> str:
    """Codex solo admite `allow` y `deny` en PreToolUse. Una decisión `ask` la
    marca como fallo del hook y CONTINÚA con la herramienta (fail-open; docs de
    hooks de Codex), así que el `ask` del guard (FW-236) se traduce a `deny` con el
    mismo motivo (D4: lo portable es lo más estricto). Una salida que no se
    entiende también deniega."""
    if not stdout.strip():
        return stdout
    try:
        spec = json.loads(stdout)["hookSpecificOutput"]
        decision = spec["permissionDecision"]
        reason = str(spec.get("permissionDecisionReason", ""))
    except (ValueError, KeyError, TypeError):
        return _guard_deny_json("el guard emitió una salida ilegible")
    if decision == "deny":
        return stdout
    if decision == "ask":
        return _guard_deny_json(
            f"{reason} (Codex no admite `ask` en PreToolUse y seguiría con la ejecución: se deniega, D4)",
            raw_reason=True,
        )
    return _guard_deny_json(f"decisión del guard no soportada: {decision}")


def _guard_deny_json(reason: str, *, raw_reason: bool = False) -> str:
    return json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason if raw_reason else f"Production Command Guard (FW-082): {reason} (FW-236).",
        }
    })


def _emit_guard_deny(reason: str, fw: str = "FW-237") -> None:
    sys.stdout.write(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": f"Production Command Guard (FW-082): {reason} ({fw}).",
        }
    }))


def _skip_or_deny(hook_id: str, reason: str) -> int:
    """FW-363 (5.3.1 L1.3): sin veredicto del guard no se ejecuta nada; los hooks
    que no protegen nada (`state`, `format`, `stop`) se siguen omitiendo."""
    if hook_id == "guard":
        _emit_guard_deny(f"{reason}. Sin veredicto del guard no se ejecuta; si el builder lo autoriza, lo ejecuta él", "FW-363")
    else:
        print(f"Codex hook: {reason}; se omite de forma segura", file=sys.stderr)
    return 0


def _run_format(root: Path, event: dict[str, Any]) -> int:
    formatter = root / ".agent" / "scripts" / "post_edit_format.sh"
    target_text = _tool_path(event)
    if not formatter.exists() or not target_text:
        if not formatter.exists():
            print(f"Codex hook format: falta {formatter}", file=sys.stderr)
        return 0
    target = Path(target_text)
    if not target.is_absolute():
        target = root / target
    try:
        target = target.resolve()
        if not target.is_relative_to(root.resolve()) or not target.is_file():
            return 0
    except OSError as exc:
        print(f"Codex hook format: ruta de archivo no válida: {exc}", file=sys.stderr)
        return 0
    env = os.environ.copy()
    env["CLAUDE_TOOL_FILE_PATH"] = str(target)
    try:
        subprocess.run(
            ["bash", str(formatter)],
            cwd=root,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError as exc:
        print(f"Codex hook format: no se pudo ejecutar el formateador: {exc}", file=sys.stderr)
    return 0


def _run_stop(root: Path, event: dict[str, Any]) -> None:
    """Stop: el cierre de turno lo verifica `turn_close_hook.py` (5.3.0 L4), el mismo
    módulo que en Claude Code. Si pide continuar, se emite su bloqueo (probado en
    Codex 0.162.1); si no, o si el módulo falla, queda el recordatorio de siempre."""
    output: dict[str, Any] = {}
    try:
        scripts = str(root / ".agent" / "scripts")
        if scripts not in sys.path:
            sys.path.insert(0, scripts)
        import turn_close_hook

        output = turn_close_hook.evaluate(event, host="codex", root=root, gycd=False)
    except Exception as exc:  # noqa: BLE001 - D2: si el hook falla, deja pasar
        print(f"Codex hook stop: no se pudo verificar el cierre de turno: {type(exc).__name__}", file=sys.stderr)
    if isinstance(output, dict) and output.get("decision") == "block" and isinstance(output.get("reason"), str):
        sys.stdout.write(json.dumps({"decision": "block", "reason": output["reason"]}))
        return
    # Un aviso del hook (estado no guardado, cerrojo ocupado) va junto al recordatorio, en un solo mensaje.
    notice = output.get("systemMessage") if isinstance(output, dict) else None
    _emit_message(f"{STOP_MESSAGE} {notice}" if isinstance(notice, str) and notice else STOP_MESSAGE)


def dispatch(hook_id: str, event: dict[str, Any], raw: str, root: Path) -> int:
    _write_smoke_marker(hook_id, root, phase="started", status="pass")
    try:
        if hook_id == "guard":
            return_code = _run_guard(root, raw)
        elif hook_id == "state":
            if any("_project/03_project_state.json" in path.replace("\\", "/") for path in _tool_paths(event)):
                _emit_message(STATE_MESSAGE)
            return_code = 0
        elif hook_id == "format":
            for path in _tool_paths(event):
                _run_format(root, {**event, "tool_input": {"file_path": path}})
            return_code = 0
        elif hook_id == "stop":
            _run_stop(root, event)
            return_code = 0
        else:
            print(f"Codex hook: hook_id no soportado: {hook_id}", file=sys.stderr)
            return_code = 0
    except Exception as exc:  # noqa: BLE001 - el marcador debe registrar el fallo del adaptador
        _write_smoke_marker(hook_id, root, phase="error", status="fail")
        print(f"Codex hook: fallo inesperado: {exc}", file=sys.stderr)
        if hook_id == "guard":
            _emit_guard_deny(f"fallo inesperado del adaptador ({type(exc).__name__})", "FW-363")
        return 0
    _write_smoke_marker(hook_id, root, phase="completed", status="pass" if return_code == 0 else "fail")
    return return_code


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Ejecuta un hook Codex del framework")
    parser.add_argument("--hook-id", choices=sorted(SUPPORTED_HOOKS), required=True)
    args = parser.parse_args(argv)
    hook_id = args.hook_id
    try:
        raw = sys.stdin.read()
    except (OSError, ValueError) as exc:
        return _skip_or_deny(hook_id, f"no se pudo leer el evento ({type(exc).__name__})")
    try:
        event = json.loads(raw) if raw.strip() else {}
    except (TypeError, json.JSONDecodeError) as exc:
        return _skip_or_deny(hook_id, f"evento JSON inválido ({exc})")
    if not isinstance(event, dict):
        return _skip_or_deny(hook_id, "el evento no es un objeto JSON")
    if hook_id == "guard":
        tool_input = event.get("tool_input")
        if not isinstance(tool_input, dict) or not isinstance(tool_input.get("command"), str):
            return _skip_or_deny(hook_id, "el evento no trae tool_input.command como cadena")
        # El guard solo analiza eventos `Bash`; este hook solo se registra para Bash,
        # así que un cambio de nombre del host no puede volverlo un `pass` mudo.
        if event.get("tool_name") != "Bash":
            event = {**event, "tool_name": "Bash"}
            raw = json.dumps(event)
    try:
        root = find_project_root(event)
    except Exception:  # noqa: BLE001 - FW-363: sin raíz no hay veredicto
        root = None
    if root is None:
        return _skip_or_deny(hook_id, "no se pudo resolver la raíz del proyecto; reinicia Codex en la raíz del proyecto")
    return dispatch(hook_id, event, raw, root)


if __name__ == "__main__":
    sys.exit(main())
