#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Asegura la integración Codex sin pisar MCPs personalizados.

La rama Claude Code es un no-op deliberado: esta herramienta nunca añade
hooks Codex ni altera la configuración de Claude.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python 3.11+ is the supported runtime
    tomllib = None

from sync_codex_hooks import CONFIG_REL, sync_hooks
import codex_profile
from ensure_claude_settings import load_hard_limits


ROOT = Path(__file__).resolve().parents[2]
STATE_MANAGER = {"command": "python3", "args": [".agent/scripts/state_manager.py"]}
CODEX_CONFIG_REL = Path(".codex/config.toml")
CODEX_CONFIG_TEMPLATE_REL = Path(".agent/templates/codex_config.toml.base")
CODEX_RULES_REL = Path(".codex/rules/framework-reads.rules")
# 5.3.1 L1.4: núcleo irreversible, en un fichero aparte del de lecturas.
CODEX_DESTRUCTIVE_RULES_REL = Path(".codex/rules/framework-destructive.rules")
RULES_MARKER = "# GESTIONADO por el framework (ensure_codex_integration.py): no editar a mano."
# FW-305 (5.2.3 L7): con approval_mode = "approve" el revisor automático evaluaba cada
# lectura del framework (114 llamadas extra al modelo en una sesión). Solo lecturas:
# `mutate`, `git push` y los comandos destructivos no entran aquí ni se rebajan.
READ_SUBCOMMANDS = ("query", "lookup", "describe")
CODEX_RULES_TEXT = f"""{RULES_MARKER}
# Lecturas del framework que no necesitan revisor. Las mutaciones (`state_manager.py mutate`),
# `git push` y lo destructivo siguen su flujo normal. Codex solo carga reglas de proyecto
# cuando la capa `.codex/` es de confianza.
prefix_rule(
    pattern = ["python3", ".agent/scripts/state_manager.py", {list(READ_SUBCOMMANDS)!r}],
    decision = "allow",
    justification = "Lectura del estado del framework (query, lookup, describe).",
    match = ["python3 .agent/scripts/state_manager.py query --view startup"],
    not_match = ["python3 .agent/scripts/state_manager.py mutate --action update_last_step"],
)
prefix_rule(
    pattern = ["python3", "-B", ".agent/scripts/state_manager.py", {list(READ_SUBCOMMANDS)!r}],
    decision = "allow",
    justification = "Lectura del estado del framework (query, lookup, describe).",
    match = ["python3 -B .agent/scripts/state_manager.py lookup --kind receipts"],
    not_match = ["python3 -B .agent/scripts/state_manager.py mutate --action update_last_step"],
)
prefix_rule(
    pattern = ["python3", ".agent/scripts/framework_doctor.py"],
    decision = "allow",
    justification = "El doctor solo lee y valida.",
    match = ["python3 .agent/scripts/framework_doctor.py"],
    not_match = ["python3 .agent/scripts/propagate_framework.py"],
)
""".replace("'", '"')
STATE_MANAGER_TOML = (
    '[mcp_servers.state_manager]\ncommand = "python3"\ndefault_tools_approval_mode = "writes"\n'
    'args = [".agent/scripts/state_manager.py"]\n'
)


def _load(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(f".mcp.json ilegible: {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise SystemExit(".mcp.json debe ser un objeto JSON")
    return data


def _load_toml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    if tomllib is None:
        raise SystemExit("Se necesita Python 3.11+ para validar .codex/config.toml")
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise SystemExit(f".codex/config.toml ilegible: {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise SystemExit(".codex/config.toml debe declarar un documento TOML")
    return data


def _ensure_native_codex_config(root: Path, *, check: bool) -> tuple[bool, dict[str, Any]]:
    """Asegura el MCP nativo de Codex en el config.toml del proyecto."""
    template_path = root / CODEX_CONFIG_TEMPLATE_REL
    try:
        template_text = template_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise SystemExit(f"Plantilla Codex ilegible: {template_path}: {exc}") from exc
    template_data = _load_toml(template_path)
    expected = (template_data.get("mcp_servers") or {}).get("state_manager")
    if (
        not isinstance(expected, dict)
        or expected.get("command") != STATE_MANAGER["command"]
        or expected.get("args") != STATE_MANAGER["args"]
    ):
        raise SystemExit("Plantilla Codex inválida: state_manager debe usar python3 .agent/scripts/state_manager.py")

    path = root / CODEX_CONFIG_REL
    existing_text = path.read_text(encoding="utf-8") if path.exists() else ""
    existing = _load_toml(path)
    servers = existing.get("mcp_servers")
    if servers is not None and not isinstance(servers, dict):
        raise SystemExit(".codex/config.toml.mcp_servers debe ser una tabla")
    state_manager = servers.get("state_manager") if isinstance(servers, dict) else None
    if state_manager is not None and (
        not isinstance(state_manager, dict)
        or state_manager.get("command") != STATE_MANAGER["command"]
        or state_manager.get("args") != STATE_MANAGER["args"]
    ):
        raise SystemExit(
            ".codex/config.toml ya declara mcp_servers.state_manager con otra configuración; se preserva y se requiere revisión explícita"
        )

    if state_manager is None:
        serialized = existing_text.rstrip() + ("\n\n" if existing_text.strip() else "") + STATE_MANAGER_TOML
        if not existing_text:
            serialized = template_text if template_text.endswith("\n") else template_text + "\n"
    else:
        serialized = existing_text
    changed = existing_text != serialized
    return changed, {"path": str(CODEX_CONFIG_REL), "configured": True, "serialized": serialized}


def destructive_rules_text(root: Path = ROOT) -> str:
    """`prefix_rule` con `forbidden` para el núcleo irreversible de la lista única.
    Se mantiene `forbidden` (no `prompt`): con `approval_mode = "approve"` decide un
    revisor automático, no el builder. Los `match` y `not_match` se comprueban al
    cargar: un ejemplo erróneo deja el fichero entero sin cargar."""
    data = load_hard_limits(root)
    exit_text = str(data.get("salida", "")).replace(" APPROVED-DESTRUCTIVE", "")
    lines = [
        RULES_MARKER,
        "# Generado desde .agent/rules/limites_duros.json (5.3.1 L1.4). Segunda capa: el guard sigue siendo",
        "# la principal. Solo carga si la capa .codex/ es de confianza; se lee al arrancar Codex.",
    ]
    for rule in data["reglas"]:
        for spec in rule.get("codex", []):
            justification = f"{rule['motivo']}. {exit_text}"
            lines += [
                "prefix_rule(",
                f"    pattern = {json.dumps(spec['pattern'], ensure_ascii=False)},",
                '    decision = "forbidden",',
                f"    justification = {json.dumps(justification, ensure_ascii=False)},",
                f"    match = {json.dumps(spec.get('match', []), ensure_ascii=False)},",
                f"    not_match = {json.dumps(spec.get('not_match', []), ensure_ascii=False)},",
                ")",
            ]
    return "\n".join(lines) + "\n"


def _rule_files(root: Path) -> tuple[tuple[Path, str], ...]:
    return ((CODEX_RULES_REL, CODEX_RULES_TEXT), (CODEX_DESTRUCTIVE_RULES_REL, destructive_rules_text(root)))


def _ensure_codex_rules(root: Path) -> tuple[bool, str]:
    """Reglas del framework en `.codex/rules/` (lecturas y núcleo irreversible).

    Son archivos propios del framework: si uno existe sin la marca de gestión, es de
    otra persona y se preserva con un error explícito en vez de sobrescribirlo."""
    changed = False
    for rel, text in _rule_files(root):
        path = root / rel
        if path.is_symlink():
            raise SystemExit(f"{rel} es un enlace simbólico; se preserva y se requiere revisión explícita")
        current = path.read_text(encoding="utf-8") if path.exists() else ""
        if current and not current.startswith(RULES_MARKER):
            raise SystemExit(f"{rel} existe y no lo gestiona el framework; se preserva y se requiere revisión explícita")
        changed = changed or current != text
    return changed, str(CODEX_RULES_REL)


def _write_codex_rules(root: Path) -> None:
    for rel, text in _rule_files(root):
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")


def ensure_codex_integration(
    root: Path = ROOT,
    primary_harness: str | None = None,
    *,
    check: bool = False,
    from_profile: bool = False,
    native_only: bool = False,
) -> dict[str, Any]:
    root = Path(root).resolve()
    if from_profile:
        profile = codex_profile.detect_primary_harness(root)
        if profile["status"] == "conflict":
            raise SystemExit(
                "Perfil de entorno contradictorio: declarar un único harness principal "
                f"en {profile['profile_path']} ({profile['declarations']})"
            )
        if profile["status"] == "missing":
            raise SystemExit(
                f"No se pudo resolver el harness principal desde {profile['profile_path']}; "
                "crear/completar el perfil antes de usar --from-profile"
            )
        primary_harness = profile["value"] or ""
    if not primary_harness or primary_harness.strip().casefold() != "codex":
        return {"status": "skipped", "reason": "el harness principal no es Codex", "changed": False}

    if native_only:
        native_changed, native_result = _ensure_native_codex_config(root, check=check)
        hooks_result = sync_hooks(root, check=True)
        rules_changed, rules_path = _ensure_codex_rules(root)
        if not check:
            if native_changed:
                native_path = root / CODEX_CONFIG_REL
                native_path.parent.mkdir(parents=True, exist_ok=True)
                native_path.write_text(str(native_result["serialized"]), encoding="utf-8")
            if hooks_result["changed"]:
                sync_hooks(root, check=False)
            if rules_changed:
                _write_codex_rules(root)
        changed = native_changed or hooks_result["changed"] or rules_changed
        return {
            "status": "drift" if check and changed else "configured",
            "changed": changed,
            "mcp_changed": False,
            "mirror_skipped": True,
            "native_config_changed": native_changed,
            "hooks_changed": hooks_result["changed"],
            "rules_changed": rules_changed,
            "rules_path": rules_path,
            "hooks_path": str(CONFIG_REL),
            "native_config_path": native_result["path"],
        }

    mcp_path = root / ".mcp.json"
    data = _load(mcp_path)
    servers = data.get("mcpServers")
    if servers is None:
        servers = {}
        data["mcpServers"] = servers
    if not isinstance(servers, dict):
        raise SystemExit(".mcp.json.mcpServers debe ser un objeto")
    existing = servers.get("state_manager")
    # Comparar por command+args, igual que `_ensure_native_codex_config` hace para
    # el TOML. La igualdad estricta de dict rechazaba `.mcp.json` correctos por
    # llevar claves de documentación que el propio framework distribuyó (`$purpose`),
    # y abortaba el upgrade de todos los proyectos Codex adoptantes (FW-171).
    if existing is not None and (
        not isinstance(existing, dict)
        or existing.get("command") != STATE_MANAGER["command"]
        or existing.get("args") != STATE_MANAGER["args"]
    ):
        raise SystemExit(
            ".mcp.json ya declara state_manager con otra configuración; se preserva y se requiere revisión explícita"
        )
    if existing is None:
        servers["state_manager"] = dict(STATE_MANAGER)
    serialized = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
    current = mcp_path.read_text(encoding="utf-8") if mcp_path.exists() else ""
    mcp_changed = current != serialized
    native_changed, native_result = _ensure_native_codex_config(root, check=check)
    # Preflight all three managed surfaces before writing any of them. A
    # conflicting native state_manager or malformed hooks config must not leave
    # a partially updated .mcp.json behind.
    hooks_result = sync_hooks(root, check=True)
    rules_changed, rules_path = _ensure_codex_rules(root)
    if not check:
        if rules_changed:
            _write_codex_rules(root)
        if mcp_changed:
            mcp_path.write_text(serialized, encoding="utf-8")
        if native_changed:
            native_path = root / CODEX_CONFIG_REL
            native_path.parent.mkdir(parents=True, exist_ok=True)
            native_path.write_text(str(native_result["serialized"]), encoding="utf-8")
        if hooks_result["changed"]:
            sync_hooks(root, check=False)
    return {
        "status": "drift" if check and (mcp_changed or native_changed or hooks_result["changed"] or rules_changed) else "configured",
        "changed": mcp_changed or native_changed or hooks_result["changed"] or rules_changed,
        "rules_changed": rules_changed,
        "rules_path": rules_path,
        "mcp_changed": mcp_changed,
        "native_config_changed": native_changed,
        "hooks_changed": hooks_result["changed"],
        "hooks_path": str(CONFIG_REL),
        "native_config_path": native_result["path"],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Asegura state_manager y hooks Codex")
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument(
        "--primary-harness",
        help="activar explícitamente para un proyecto Codex; sin selector se hace no-op",
    )
    parser.add_argument(
        "--from-profile",
        action="store_true",
        help="resolver el harness desde _project/15_environment_profile.md",
    )
    parser.add_argument(
        "--native-only",
        action="store_true",
        help="usar solo .codex/config.toml y .codex/hooks.json; no leer ni escribir .mcp.json",
    )
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--json", action="store_true", dest="as_json")
    args = parser.parse_args(argv)
    result = ensure_codex_integration(
        args.root,
        args.primary_harness,
        check=args.check,
        from_profile=args.from_profile,
        native_only=args.native_only,
    )
    if args.as_json:
        print(json.dumps(result, ensure_ascii=False))
    elif result["status"] == "skipped":
        print("Integración Codex: omitida; el harness principal no es Codex")
    elif args.check and result["changed"]:
        print("Integración Codex: DRIFT — ejecutar ensure_codex_integration.py")
    else:
        print("Integración Codex: state_manager y hooks listos")
    return 1 if args.check and result["changed"] else 0


if __name__ == "__main__":
    sys.exit(main())
