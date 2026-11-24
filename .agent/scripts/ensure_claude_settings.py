#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Asegura `.claude/settings.json` en un proyecto adoptante sin pisar sus hooks.

FW-204 (clase FW-162/FW-171): `.claude/settings.json` lleva el hook `PreToolUse`
del Production Command Guard (FW-082), y `framework_doctor` lo exige también en
modo `initialized` (`doctor_wiring.json` → `production_command_guard`, sin
`template_only`). Pero ni `/nuevo-proyecto` ni `/migrar-proyecto` lo sembraban —
solo `.mcp.json` tenía paso propio. Una adopción que copiara `.agent/` +
`.claude/commands/` pero no ese fichero fallaba el doctor en el primer commit,
sin un `.base` que copiar.

Contrato:
- Sin `.claude/settings.json` → copiar `.agent/templates/settings.json.base`.
- Con `.claude/settings.json` → **merge JSON-aware**:
  - Cada hook del framework se identifica por una firma estable en su `command`
    (`_FW_HOOK_SIGNATURES`). Si el proyecto no lo tiene → se añade. Si lo tiene
    con un `command` distinto → se actualiza (el proyecto recibe la versión
    nueva). Si lo tiene idéntico → no-op.
  - Los hooks **sin** firma del framework son del proyecto → se preservan
    intactos, en su evento, sin reordenar.
  - `permissions` (cambio de contrato de la 5.3.1 L1.4): solo se **añaden** a
    `permissions.deny` las reglas del núcleo irreversible que se generan desde
    `.agent/rules/limites_duros.json`, y se retiran las que el framework retiró
    (`_FW_RETIRED_DENY`). Nunca se tocan `allow`, `ask`, `defaultMode`,
    `additionalDirectories` ni las reglas `deny` del proyecto; ante un tipo
    inesperado se aborta sin escribir.
  - Nunca se toca `env` ni ninguna otra clave fuera de `hooks` y `permissions.deny`.
- Idempotente. `--check` no escribe y sale con código ≠ 0 si haría cambios.

Uso:
  python3 .agent/scripts/ensure_claude_settings.py                 # sobre ROOT
  python3 .agent/scripts/ensure_claude_settings.py --root <proj>   # otro proyecto
  python3 .agent/scripts/ensure_claude_settings.py --check         # dry-run (CI)
"""
from __future__ import annotations

import argparse
import copy
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
BASE_REL = Path(".agent/templates/settings.json.base")
TARGET_REL = Path(".claude/settings.json")
HARD_LIMITS_REL = Path(".agent/rules/limites_duros.json")
# Reglas `deny` que el framework generó en versiones anteriores y ya no genera:
# se retiran del proyecto al actualizar (como `_FW_RETIRED_SIGNATURES`).
_FW_RETIRED_DENY: tuple[str, ...] = ()

# Substrings que identifican un hook gestionado por el framework. Cada hook del
# `.base` lleva exactamente una. STOP GATE #3 de la spec del lote 2026-09-02:
# detección por substring del `command`, no por marcador `_fw_managed` (que
# metería una clave no estándar en el objeto hook).
_FW_HOOK_SIGNATURES = (
    "guard_destructive_command.py",  # PreToolUse — Production Command Guard
    "cc_gycd",                       # Stop — recordatorio /gycd y cierre de turno (FW-205; 5.3.0 L4)
    "cc_err_count",                  # PostToolUseFailure — nudge a /debug (FW-235: .txt antiguo o _<sesión>)
    "post_edit_format.sh",           # PostToolUse — formateo stack-agnóstico
    "claude_continuity_hook.py",     # SessionStart — hook de continuidad (FW-4.5)
)

# Firmas de hooks que existieron en versiones anteriores pero fueron retirados
# (p. ej. recordatorio 03_project_state retirado en 4.5 Lote 3). El helper los
# reconoce para eliminarlos de proyectos adoptantes durante actualizaciones.
_FW_RETIRED_SIGNATURES = (
    "03_project_state",              # PostToolUse — recordatorio /cierre-modulo retirado en 4.5 Lote 3
)


def _signature_of(command: str) -> str | None:
    if not isinstance(command, str):
        return None
    # Reconocer firmas de hooks retirados para eliminarlos durante actualizaciones
    probes_state = (
        "grep -q '03_project_state\\.json'" in command
        or 'grep -q "03_project_state\\.json"' in command
        or "03_project_state.json'" in command and "grep" in command
        # FW-235: la forma actual decide por la ruta editada, no por el contenido.
        or 'case "$path" in 03_project_state.json|*/03_project_state.json)' in command
    )
    if probes_state and "project_state.json actualizado" in command and "/cierre-modulo" in command:
        return "03_project_state"

    # 5.3.0 L4: el Stop del framework es un solo hook. `turn_close_hook.py` verifica
    # el cierre de turno y emite el recordatorio de /gycd: ocupa el mismo hueco que
    # la orden antigua (`cc_gycd`) para actualizarla en sitio, sin duplicarla.
    if "turn_close_hook.py" in command:
        return "cc_gycd"
    for sig in _FW_HOOK_SIGNATURES:
        if sig in command:
            return sig
    # Antes de FW-205 el recordatorio Stop no tenía la firma `cc_gycd`.
    # Reconocer su texto estable permite actualizarlo en sitio sin duplicarlo.
    if "ejecuta /gycd para cerrar ordenadamente" in command:
        return "cc_gycd"
    return None


def load_hard_limits(root: Path = ROOT) -> dict[str, Any]:
    """La lista única del núcleo irreversible (5.3.1 L1.4). Se lee la del proyecto
    y, si no la tiene, la de la plantilla. Ninguna regla de Claude empieza por `*`."""
    path = root / HARD_LIMITS_REL
    if not path.exists():
        path = ROOT / HARD_LIMITS_REL
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise SystemExit(f"limites_duros.json ilegible: {path}: {exc}")
    rules = data.get("reglas") if isinstance(data, dict) else None
    if not isinstance(rules, list) or not all(isinstance(r, dict) and isinstance(r.get("id"), str) for r in rules):
        raise SystemExit(f"limites_duros.json inválido: {path} debe declarar 'reglas' con 'id'")
    for rule in rules:
        claude = rule.get("claude", [])
        if not isinstance(claude, list) or any(
            not isinstance(c, str) or not c.startswith("Bash(") or not c.endswith(")") or c[5:6] == "*" for c in claude
        ):
            raise SystemExit(f"limites_duros.json: reglas de Claude inválidas en {rule['id']}")
    return data


def claude_deny_rules(root: Path = ROOT) -> list[str]:
    return [c for rule in load_hard_limits(root)["reglas"] for c in rule.get("claude", [])]


def _merge_deny(result: dict[str, Any], managed: list[str], changes: list[str]) -> None:
    """Añade el núcleo a `permissions.deny` sin tocar nada más de `permissions`."""
    permissions = result.setdefault("permissions", {})
    if not isinstance(permissions, dict):
        raise ValueError(".claude/settings.json.permissions debe ser un objeto")
    deny = permissions.setdefault("deny", [])
    if not isinstance(deny, list) or any(not isinstance(rule, str) for rule in deny):
        raise ValueError(".claude/settings.json.permissions.deny debe ser una lista de cadenas")
    for rule in [r for r in deny if r in _FW_RETIRED_DENY and r not in managed]:
        deny.remove(rule)
        changes.append(f"permissions.deny: regla del framework retirada ({rule})")
    for rule in managed:
        if rule not in deny:
            deny.append(rule)
            changes.append(f"permissions.deny: regla del framework añadida ({rule})")


def _load(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise SystemExit(f"ensure_claude_settings: {path} no es JSON válido: {exc}")
    if not isinstance(data, dict):
        raise SystemExit(f"ensure_claude_settings: {path} debe contener un objeto JSON")
    return data


def _scope_key(group: dict[str, Any]) -> str:
    """Serialize every group restriction except its hook list.

    Claude currently documents ``matcher`` as the common restriction, but a
    future/legacy group can carry additional restriction fields.  Treating only
    the matcher as scope would merge two hooks that run under different
    conditions.  JSON input is already parsed, so a canonical serialization is
    stable and does not add private keys to the settings file.
    """
    restrictions = {key: value for key, value in group.items() if key != "hooks"}
    return json.dumps(restrictions, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _remove_identity(items: list[Any], target: Any) -> bool:
    """Remove the exact duplicate object, not an equal earlier dictionary."""
    for index, item in enumerate(items):
        if item is target:
            del items[index]
            return True
    return False


def merge_settings(
    base: dict[str, Any],
    current: dict[str, Any] | None,
    *,
    enable_continuity_hook: bool = False,
    disable_continuity_hook: bool = False,
    managed_deny: list[str] | None = None,
) -> tuple[dict[str, Any], list[str]]:
    """Devuelve (settings resultante, lista de cambios legibles)."""
    if not isinstance(base, dict):
        raise ValueError("settings.json.base debe contener un objeto JSON")
    if current is None:
        result = copy.deepcopy(base)
        changes = ["creado desde settings.json.base"]
        if enable_continuity_hook:
            continuity_cmd = 'python3 "$CLAUDE_PROJECT_DIR"/.agent/scripts/claude_continuity_hook.py'
            continuity_msg = "Cargando contexto de continuidad (FW-4.5)..."
            result.setdefault("hooks", {}).setdefault("SessionStart", []).append({
                "hooks": [{
                    "type": "command",
                    "command": continuity_cmd,
                    "statusMessage": continuity_msg,
                }]
            })
            changes.append("SessionStart: hook del framework añadido (claude_continuity_hook.py)")
        if managed_deny is not None:
            _merge_deny(result, managed_deny, [])
        return result, changes
    if not isinstance(current, dict):
        raise ValueError(".claude/settings.json debe contener un objeto JSON")

    result = copy.deepcopy(current)
    changes: list[str] = []
    current_hooks = result.setdefault("hooks", {})
    if not isinstance(current_hooks, dict):
        raise ValueError(".claude/settings.json.hooks debe ser un objeto")
    base_hooks = base.get("hooks", {})
    if not isinstance(base_hooks, dict):
        raise ValueError("settings.json.base.hooks debe ser un objeto")

    def validate_event_groups(events: dict[str, Any], label: str) -> None:
        for event, groups in events.items():
            if not isinstance(groups, list) or any(
                not isinstance(group, dict)
                or not isinstance(group.get("hooks"), list)
                or any(not isinstance(hook, dict) for hook in group["hooks"])
                for group in groups
            ):
                raise ValueError(f"{label}.hooks.{event} debe ser una lista de grupos válidos")

    # Validate every existing event before merging.  Skipping a malformed
    # project group would preserve an invalid settings file while adding a new
    # framework group, making the operation appear successful but leaving the
    # target unusable.  Fail before any write instead.
    validate_event_groups(result["hooks"], ".claude/settings.json")

    # FW-4.5 (Lote 3): Retirar hooks obsoletos del framework (ej. 03_project_state)
    # y opcionalmente el hook de continuidad si se solicita desactivación explícita.
    retired_to_remove = set(_FW_RETIRED_SIGNATURES)
    if disable_continuity_hook:
        retired_to_remove.add("claude_continuity_hook.py")

    for ev, groups in list(result["hooks"].items()):
        if not isinstance(groups, list):
            continue
        empty_groups: list[dict] = []
        for group in groups:
            if not isinstance(group, dict) or not isinstance(group.get("hooks"), list):
                continue
            hooks_list = group["hooks"]
            for hook in list(hooks_list):
                if not isinstance(hook, dict):
                    continue
                sig = _signature_of(hook.get("command", ""))
                if sig and sig in retired_to_remove:
                    hooks_list.remove(hook)
                    changes.append(f"{ev}: hook del framework retirado ({sig})")
            if not hooks_list:
                empty_groups.append(group)
        for eg in empty_groups:
            _remove_identity(groups, eg)

    # FW-4.5 (Lote 3): Habilitar el hook de continuidad de Claude Code si se solicita
    if enable_continuity_hook:
        continuity_cmd = 'python3 "$CLAUDE_PROJECT_DIR"/.agent/scripts/claude_continuity_hook.py'
        continuity_msg = "Cargando contexto de continuidad (FW-4.5)..."
        session_groups = result["hooks"].setdefault("SessionStart", [])
        if not isinstance(session_groups, list):
            raise ValueError(".claude/settings.json.hooks.SessionStart debe ser una lista")

        found = False
        for group in session_groups:
            if not isinstance(group, dict) or not isinstance(group.get("hooks"), list):
                continue
            for hook in group["hooks"]:
                if _signature_of(hook.get("command", "")) == "claude_continuity_hook.py":
                    found = True
                    if hook.get("command") != continuity_cmd or hook.get("statusMessage") != continuity_msg:
                        hook["command"] = continuity_cmd
                        hook["statusMessage"] = continuity_msg
                        changes.append("SessionStart: hook del framework actualizado (claude_continuity_hook.py)")
                    break
            if found:
                break
        if not found:
            session_groups.append({
                "hooks": [{
                    "type": "command",
                    "command": continuity_cmd,
                    "statusMessage": continuity_msg,
                }]
            })
            changes.append("SessionStart: hook del framework añadido (claude_continuity_hook.py)")

    for event, base_groups in base_hooks.items():
        validate_event_groups({event: base_groups}, "settings.json.base")
        cur_groups = result["hooks"].setdefault(event, [])
        if not isinstance(cur_groups, list):
            raise ValueError(f".claude/settings.json.hooks.{event} debe ser una lista")
        # Índice de los hooks del framework ya presentes en este evento del proyecto.
        present_scope: dict[tuple[str, str], tuple[list, dict]] = {}
        present_exact: set[tuple[str, str, str]] = set()
        duplicates: list[tuple[list, dict, dict]] = []
        for group in cur_groups:
            if not isinstance(group, dict) or not isinstance(group.get("hooks"), list):
                continue
            for hook in group.get("hooks", []):
                if not isinstance(hook, dict):
                    continue
                sig = _signature_of(hook.get("command", ""))
                if sig:
                    hooks = group.get("hooks", [])
                    matcher_key = _scope_key(group)
                    hook_key = json.dumps(hook, ensure_ascii=False, sort_keys=True)
                    scope = (sig, matcher_key)
                    identity = (sig, matcher_key, hook_key)
                    if identity in present_exact:
                        duplicates.append((hooks, hook, group))
                    else:
                        present_exact.add(identity)
                        present_scope.setdefault(scope, (hooks, hook))

        empty_groups: list[dict] = []
        for hooks, hook, group in duplicates:
            _remove_identity(hooks, hook)
            changes.append(f"{event}: hook del framework duplicado eliminado ({_signature_of(hook.get('command', ''))})")
            if not hooks:
                empty_groups.append(group)
        for group in empty_groups:
            _remove_identity(cur_groups, group)

        for base_group in base_groups:
            for base_hook in base_group.get("hooks", []):
                sig = _signature_of(base_hook.get("command", ""))
                if sig is None:
                    continue  # un .base sin firma no debería ocurrir; se ignora
                matcher_key = _scope_key(base_group)
                scope = (sig, matcher_key)
                if scope in present_scope:
                    _, cur_hook = present_scope[scope]
                    if cur_hook.get("command") != base_hook.get("command") or \
                       cur_hook.get("statusMessage") != base_hook.get("statusMessage") or \
                       ("timeout" in base_hook and cur_hook.get("timeout") != base_hook["timeout"]) or \
                       ("onFailure" in base_hook and cur_hook.get("onFailure") != base_hook["onFailure"]):
                        cur_hook["command"] = base_hook["command"]
                        for key in ("statusMessage", "timeout", "onFailure"):
                            if key in base_hook:
                                cur_hook[key] = base_hook[key]
                        changes.append(f"{event}: hook del framework actualizado ({sig})")
                else:
                    new_group = {
                        key: copy.deepcopy(value)
                        for key, value in base_group.items()
                        if key != "hooks"
                    }
                    new_group["hooks"] = [copy.deepcopy(base_hook)]
                    cur_groups.append(new_group)
                    changes.append(f"{event}: hook del framework añadido ({sig})")

    if managed_deny is not None:
        _merge_deny(result, managed_deny, changes)
    return result, changes


def ensure(
    root: Path,
    *,
    check: bool,
    enable_continuity_hook: bool = False,
    disable_continuity_hook: bool = False,
) -> tuple[int, list[str]]:
    base = _load(root / BASE_REL) if (root / BASE_REL).exists() else _load(ROOT / BASE_REL)
    if base is None:
        return 2, [f"settings.json.base no encontrado ({BASE_REL})"]
    target = root / TARGET_REL
    current = _load(target)
    try:
        merged, changes = merge_settings(
            base,
            current,
            enable_continuity_hook=enable_continuity_hook,
            disable_continuity_hook=disable_continuity_hook,
            managed_deny=claude_deny_rules(root),
        )
    except ValueError as exc:
        return 2, [f"estructura JSON inválida: {exc}"]

    if not changes:
        return 0, ["sin cambios (idempotente)"]
    if check:
        return 1, changes + ["(--check: no se escribió)"]

    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(merged, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return 0, changes


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Asegura .claude/settings.json sin pisar hooks del proyecto")
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--check", action="store_true", help="dry-run: no escribe, exit ≠ 0 si haría cambios")
    parser.add_argument(
        "--enable-continuity-hook", action="store_true", help="habilita el hook SessionStart de continuidad (FW-4.5)"
    )
    parser.add_argument(
        "--disable-continuity-hook", action="store_true", help="deshabilita/retira el hook SessionStart de continuidad"
    )
    args = parser.parse_args(argv)

    code, changes = ensure(
        args.root.resolve(),
        check=args.check,
        enable_continuity_hook=args.enable_continuity_hook,
        disable_continuity_hook=args.disable_continuity_hook,
    )
    for line in changes:
        print(f"ensure_claude_settings: {line}")
    return code


if __name__ == "__main__":
    sys.exit(main())
