#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Genera ``.codex/hooks.json`` preservando hooks Codex personalizados.

La configuración activa es local e ignorada por git. Esta herramienta no toca
ningún archivo de Claude Code, OpenCode ni los wrappers compartidos.
"""
from __future__ import annotations

import argparse
import json
import shlex
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
TEMPLATE_REL = Path(".agent/templates/codex_hooks.json.base")
CONFIG_REL = Path(".codex/hooks.json")
MANAGED_PREFIX = "[FW-CODEX:"
HOOK_IDS = ("guard", "state", "format", "stop")


def _bootstrap_command(hook_id: str) -> str:
    """Construye una orden autocontenida que busca el runner desde cwd/event.cwd.

    FW-363 (5.3.1 L1.3): para el hook `guard`, cualquier fallo antes de llegar al
    runner (no se encuentra, el evento no se puede leer, el cwd ya no existe, el
    runner revienta) deniega. Los hooks `state`, `format` y `stop` no protegen
    nada y se siguen omitiendo."""
    code = (
        "import io,json,pathlib,runpy,sys\n"
        "hook=sys.argv[-1]\n"
        "def stop(msg):\n"
        "    if hook==\"guard\":\n"
        "        print(json.dumps({\"hookSpecificOutput\":{\"hookEventName\":\"PreToolUse\",\"permissionDecision\":\"deny\",\"permissionDecisionReason\":\"Production Command Guard (FW-363): \"+msg+\". Sin veredicto del guard no se ejecuta; si el builder lo autoriza, lo ejecuta él.\"}}))\n"
        "    else:\n"
        "        print(\"Codex hook: \"+msg+\"; se omite de forma segura\",file=sys.stderr)\n"
        "    sys.exit(0)\n"
        "try:\n"
        "    raw=sys.stdin.read()\n"
        "    try:\n"
        "        event=json.loads(raw) if raw.strip() else {}\n"
        "    except ValueError:\n"
        "        event={}\n"
        "    cwd=event.get(\"cwd\") if isinstance(event,dict) else None\n"
        "    seeds=[pathlib.Path(cwd)] if isinstance(cwd,str) and cwd else []\n"
        "    try:\n"
        "        seeds.append(pathlib.Path.cwd())\n"
        "    except OSError:\n"
        "        pass\n"
        "    script=next((candidate/\".agent/scripts/codex_hook_runner.py\" for seed in seeds for candidate in [seed.expanduser().resolve(),*seed.expanduser().resolve().parents] if (candidate/\".agent/scripts/codex_hook_runner.py\").is_file()),None)\n"
        "except Exception as exc:\n"
        "    stop(\"el arranque del hook falló (\"+type(exc).__name__+\")\")\n"
        "if script is None:\n"
        "    stop(\"no se pudo resolver codex_hook_runner.py; reinicia Codex en la raíz del proyecto o ejecuta python3 .agent/scripts/sync_codex_hooks.py\")\n"
        "sys.argv=[str(script),\"--hook-id\",hook]\n"
        "sys.stdin=io.StringIO(raw)\n"
        "try:\n"
        "    runpy.run_path(str(script),run_name=\"__main__\")\n"
        "except SystemExit:\n"
        "    raise\n"
        "except Exception as exc:\n"
        "    stop(\"el runner falló (\"+type(exc).__name__+\")\")"
    )
    return f"python3 -c {shlex.quote(code)} -- --hook-id {shlex.quote(hook_id)}"


def _managed_groups(template: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    """Materializa los hooks desde la plantilla y sustituye solo sus marcadores."""
    hooks = json.loads(json.dumps(template["hooks"]))
    managed_ids: set[str] = set()
    for event, groups in hooks.items():
        if not isinstance(groups, list):
            raise SystemExit(f"Plantilla Codex inválida: hooks.{event} debe ser una lista")
        for group in groups:
            if not isinstance(group, dict) or not isinstance(group.get("hooks"), list):
                raise SystemExit(f"Plantilla Codex inválida: hooks.{event} contiene un grupo sin hooks")
            for hook in group["hooks"]:
                if not isinstance(hook, dict):
                    raise SystemExit(f"Plantilla Codex inválida: hooks.{event} contiene un hook no objeto")
                command = hook.get("command")
                if not isinstance(command, str) or not command.startswith("__CODEX_HOOK_COMMAND__:"):
                    raise SystemExit(f"Plantilla Codex inválida: falta marcador __CODEX_HOOK_COMMAND__ en hooks.{event}")
                hook_id = command.split(":", 1)[1]
                if hook_id not in HOOK_IDS or hook_id in managed_ids:
                    raise SystemExit(f"Plantilla Codex inválida: hook gestionado desconocido o duplicado: {hook_id}")
                managed_ids.add(hook_id)
                hook["command"] = _bootstrap_command(hook_id)
    if managed_ids != set(HOOK_IDS):
        missing = ", ".join(sorted(set(HOOK_IDS) - managed_ids))
        raise SystemExit(f"Plantilla Codex incompleta: faltan hooks gestionados: {missing}")
    return hooks


def _managed_command_set() -> set[str]:
    return {_bootstrap_command(hook_id) for hook_id in HOOK_IDS}


def _is_managed_hook(hook: Any, managed_commands: set[str]) -> bool:
    if not isinstance(hook, dict):
        return False
    command = str(hook.get("command", ""))
    status = str(hook.get("statusMessage", ""))
    if MANAGED_PREFIX in status or command in managed_commands:
        return True
    # Migración conservadora de la configuración local Codex anterior.
    # Solo se migran las formas exactas que emitía la configuración anterior;
    # un hook personalizado que también invoque el guardia o el formateador no
    # debe desaparecer por compartir una ruta.
    normalized = " ".join(command.split())
    legacy_exact = {
        "python3 .agent/scripts/guard_destructive_command.py",
        "input=$(cat); echo \"$input\" | grep -q '03_project_state\\.json' && echo '{\"systemMessage\": \"project_state.json actualizado. Si acabas de cerrar un módulo, ejecuta /cierre-modulo.\"}' || true",
        "input=$(cat); path=$(printf '%s' \"$input\" | python3 -c \"import json,sys; d=json.load(sys.stdin); print(d.get('tool_input',{}).get('file_path',''))\" 2>/dev/null); [ -n \"$path\" ] && CLAUDE_TOOL_FILE_PATH=\"$path\" .agent/scripts/post_edit_format.sh || true",
        "echo '{\"systemMessage\": \"Sesión terminada. Si trabajaste en el proyecto, ejecuta /gycd para cerrar ordenadamente y guardar el estado.\"}'",
    }
    return normalized in legacy_exact


def _load_template(root: Path) -> dict[str, Any]:
    path = root / TEMPLATE_REL
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(f"Plantilla Codex ilegible: {path}: {exc}") from exc
    if not isinstance(data, dict) or not isinstance(data.get("hooks"), dict):
        raise SystemExit(f"Plantilla Codex inválida: {path} debe declarar hooks como objeto")
    return data


def _load_existing(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(f"Configuración Codex ilegible: {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise SystemExit(f"Configuración Codex inválida: {path} debe ser un objeto JSON")
    if "hooks" in data and not isinstance(data["hooks"], dict):
        raise SystemExit(f"Configuración Codex inválida: {path}.hooks debe ser un objeto")
    return data


def _preserve_custom_hooks(hooks: dict[str, Any], managed_commands: set[str]) -> dict[str, list[Any]]:
    preserved: dict[str, list[Any]] = {}
    for event, groups in hooks.items():
        if not isinstance(groups, list):
            raise SystemExit(f"Configuración Codex inválida: hooks.{event} debe ser una lista")
        kept_groups: list[Any] = []
        for group in groups:
            if not isinstance(group, dict):
                kept_groups.append(group)
                continue
            group_copy = dict(group)
            group_hooks = group_copy.get("hooks")
            if not isinstance(group_hooks, list):
                kept_groups.append(group_copy)
                continue
            custom = [hook for hook in group_hooks if not _is_managed_hook(hook, managed_commands)]
            if custom:
                group_copy["hooks"] = custom
                kept_groups.append(group_copy)
            elif not group_hooks:
                kept_groups.append(group_copy)
        if kept_groups:
            preserved[event] = kept_groups
    return preserved


def sync_hooks(root: Path = ROOT, *, check: bool = False) -> dict[str, Any]:
    root = Path(root).resolve()
    template = _load_template(root)
    managed_groups = _managed_groups(template)
    managed_commands = _managed_command_set()
    path = root / CONFIG_REL
    existing = _load_existing(path)
    preserved = _preserve_custom_hooks(existing.get("hooks", {}), managed_commands)
    merged: dict[str, Any] = dict(existing)
    merged_hooks = dict(preserved)
    for event, groups in managed_groups.items():
        merged_hooks.setdefault(event, [])
        merged_hooks[event].extend(groups)
    merged["hooks"] = merged_hooks
    # Keep the template as a schema/contract source and reject accidental drift
    # in its event set, while allowing user custom events in the active config.
    expected_events = set(template["hooks"]) & set(managed_groups)
    if expected_events != set(managed_groups):
        raise SystemExit("Plantilla Codex incompleta: faltan eventos PreToolUse/PostToolUse/Stop")
    serialized = json.dumps(merged, ensure_ascii=False, indent=2) + "\n"
    current = path.read_text(encoding="utf-8") if path.exists() else ""
    changed = current != serialized
    if changed and not check:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(serialized, encoding="utf-8")
    return {
        "changed": changed,
        "path": str(CONFIG_REL),
        "managed_hooks": 4,
        "preserved_custom_events": sorted(set(preserved) - set(managed_groups)),
        "check": check,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Sincroniza los hooks locales de Codex")
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--check", action="store_true", help="solo detectar drift, no escribir")
    parser.add_argument("--json", action="store_true", dest="as_json")
    args = parser.parse_args(argv)
    try:
        result = sync_hooks(args.root, check=args.check)
    except SystemExit:
        raise
    if args.as_json:
        print(json.dumps(result, ensure_ascii=False))
    elif result["changed"] and args.check:
        print("Codex hooks: DRIFT — ejecutar python3 .agent/scripts/sync_codex_hooks.py")
    else:
        print(f"Codex hooks sincronizados: {result['managed_hooks']} hooks gestionados en {result['path']}")
    return 1 if args.check and result["changed"] else 0


if __name__ == "__main__":
    sys.exit(main())
