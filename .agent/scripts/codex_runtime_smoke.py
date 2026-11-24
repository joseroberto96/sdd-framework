#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Gates runtime de Codex, separados por superficie CLI y Desktop.

El smoke tiene dos responsabilidades deliberadamente distintas:

* comprobar que la plantilla puede cablear Codex sin tocar las superficies de
  Claude/OpenCode;
* certificar, solo con el host real, los hooks y MCP que se hayan observado.

La ausencia de una capacidad es ``unavailable``. El modo normal conserva el
fallback manual; ``--strict`` solo bloquea cuando una superficie requerida no
puede certificarse.
"""
from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any, Iterable, Iterator

import codex_profile
import codex_app_server_reader
import codex_preservation
import turn_close_hook


ROOT = Path(__file__).resolve().parents[2]
POLICY_REL = Path(".agent/adapters/codex_compatibility.json")
SURFACES = ("cli", "desktop")
MCP_PROFILE_LABEL = "MCP local `state_manager` (Codex `.codex/config.toml` native; `.mcp.json` espejo opcional)"
MCP_PROFILE_LABEL_LEGACY = "MCP local `state_manager` (Codex `.codex/config.toml` / Claude `.mcp.json`)"
REASONING_EFFORTS = ("none", "minimal", "low", "medium", "high", "xhigh", "max", "ultra")
HOOK_IDS = ("guard", "state", "format", "stop")
HOOK_EVENTS = {
    "guard": "PreToolUse",
    "state": "PostToolUse",
    "format": "PostToolUse",
    "stop": "Stop",
}
MCP_CONTEXT_TOOL = "get_work_context"
MCP_CONTEXT_ARGUMENTS = {"view": "startup"}
GENERATED_MARKER = "<!-- GENERATED: framework wrapper;"
SMOKE_MARKER_ENV = "CODEX_FRAMEWORK_SMOKE_MARKER"
SMOKE_RUN_ID_ENV = "CODEX_FRAMEWORK_SMOKE_RUN_ID"
SMOKE_FIXTURE_FILE_ENV = "CODEX_FRAMEWORK_SMOKE_FIXTURE_FILE"
TRUST_TOKENS = (
    "untrusted project",
    "untrusted directory",
    "project is not trusted",
    "directory is not trusted",
    "not trusted",
    "requires trust",
    "trust required",
    "review and trust",
    "trust this project",
    "hooks are disabled",
    "local configuration is disabled",
)
APP_SERVER_TIMEOUT = 120


def _check(status: str, detail: str, **extra: Any) -> dict[str, Any]:
    result: dict[str, Any] = {"status": status, "detail": detail}
    result.update(extra)
    return result


def _aggregate_statuses(statuses: Iterable[str]) -> str:
    values = list(statuses)
    if not values:
        return "unavailable"
    if "fail" in values:
        return "fail"
    if "unavailable" in values:
        return "unavailable"
    return "pass"


def aggregate_surface_status(surfaces: dict[str, dict[str, Any]], required_surfaces: Iterable[str]) -> str:
    """Agrega solo las superficies requeridas, sin certificar una por otra."""
    required = list(required_surfaces)
    return _aggregate_statuses(
        str(surfaces.get(surface, {}).get("status", "unavailable"))
        for surface in required
    )


def _structural_support_status(checks: dict[str, dict[str, Any]]) -> str:
    names = (
        "agents",
        "workflow_discovery",
        "skill_discovery",
        "hooks_structure",
        "hook_event_compatibility",
        "state_manager",
        "codex_config",
        "codex_policy",
        "claude_preservation",
    )
    return _aggregate_statuses(
        checks[name]["status"] for name in names if name in checks
    )


def _runtime_smoke_status(
    checks: dict[str, dict[str, Any]],
    surfaces: dict[str, dict[str, Any]] | None = None,
    required_surfaces: Iterable[str] | None = None,
) -> str:
    if surfaces is not None:
        return aggregate_surface_status(surfaces, required_surfaces or ("cli",))
    # Compatibility con consumidores antiguos del resultado del smoke.
    return _aggregate_statuses(
        checks.get(name, {"status": "unavailable"})["status"]
        for name in ("hooks_runtime", "codex_host_dispatch")
    )


def evaluate_result(
    checks: dict[str, dict[str, Any]],
    surfaces: dict[str, dict[str, Any]],
    required: Iterable[str],
    strict: bool,
) -> dict[str, Any]:
    """Apply one result policy to JSON, profile registration, and CLI exit.

    ``selected`` is optional for compatibility with older callers.  When it
    is absent a surface is treated as selected; the runtime path marks
    unrequested surfaces explicitly so an optional Desktop check is not
    mistaken for a missing required capability.
    """
    required_surfaces = list(required)
    selected_surfaces = [
        name
        for name, surface in surfaces.items()
        if bool(surface.get("selected", True))
    ]
    required_statuses = [
        str(surfaces.get(name, {}).get("status", "unavailable"))
        for name in required_surfaces
    ]
    required_status = _aggregate_statuses(required_statuses)

    preservation_statuses: list[str] = []
    for name in selected_surfaces:
        surface = surfaces.get(name, {})
        preservation = surface.get("preservation")
        if not isinstance(preservation, dict):
            continue
        preservation_value = str(preservation.get("status", "unavailable"))
        # An optional surface that never started has no preservation evidence to
        # certify.  Its unavailable placeholder must remain visible, but it
        # cannot turn a passing required surface into runtime ``unavailable``.
        # Once the surface completed (or otherwise returned pass), an
        # unavailable inventory is a real inability to verify and participates
        # in the aggregate and strict gate.  Failures always participate so a
        # damaged checkout blocks regardless of which surface caused it.
        if preservation_value != "unavailable" or str(surface.get("status")) == "pass":
            preservation_statuses.append(preservation_value)
    global_preservation = checks.get("preservation")
    if isinstance(global_preservation, dict):
        preservation_statuses.append(str(global_preservation.get("status", "unavailable")))
    preservation_status = _aggregate_statuses(preservation_statuses) if preservation_statuses else "pass"
    runtime_smoke = _aggregate_statuses([required_status, preservation_status])

    blocking: list[str] = []
    # These checks are derived from individual surfaces and are represented by
    # the surface-level policy below.  The MCP mirror is explicitly optional.
    derived_checks = {
        "hooks_runtime",
        "codex_host_dispatch",
        "desktop_app_server",
        "mcp_mirror",
    }
    for name, item in checks.items():
        if name in derived_checks or not isinstance(item, dict):
            continue
        if item.get("status") == "fail" or (strict and item.get("status") == "unavailable"):
            blocking.append(name)

    for name in required_surfaces:
        status = str(surfaces.get(name, {}).get("status", "unavailable"))
        if status == "fail" or (strict and status == "unavailable"):
            blocking.append(f"surface:{name}")

    for name in selected_surfaces:
        preservation = surfaces.get(name, {}).get("preservation")
        if not isinstance(preservation, dict):
            continue
        status = str(preservation.get("status", "unavailable"))
        surface_status = str(surfaces.get(name, {}).get("status", "unavailable"))
        if status == "fail" or (strict and status == "unavailable" and surface_status == "pass"):
            blocking.append(f"preservation:{name}")
    if isinstance(global_preservation, dict):
        status = str(global_preservation.get("status", "unavailable"))
        if status == "fail" or (strict and status == "unavailable"):
            blocking.append("preservation:global")

    blocking = list(dict.fromkeys(blocking))
    return {
        "runtime_smoke": runtime_smoke,
        "blocking": blocking,
        "ok": not blocking,
        "required_surfaces": required_surfaces,
        "surfaces": surfaces,
        "strict": strict,
    }


def _load_policy(root: Path) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    """Carga la política canónica que acompaña al checkout bajo prueba."""
    path = root / POLICY_REL
    if not path.is_file():
        return None, _check("unavailable", f"no existe la política Codex {POLICY_REL}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return None, _check("fail", f"política Codex ilegible: {exc}")
    if not isinstance(data, dict):
        return None, _check("fail", "la política Codex debe ser un objeto JSON")
    if data.get("schema_version") != 1 or data.get("surfaces") != ["cli", "desktop"]:
        return None, _check("fail", "política Codex incompatible: schema_version/surfaces")
    defaults = data.get("default_required_surfaces")
    if defaults != ["cli"]:
        return None, _check("fail", "política Codex debe declarar default_required_surfaces=[cli]")
    minimums = data.get("minimum_tested_versions")
    capabilities = data.get("required_capabilities")
    if not isinstance(minimums, dict) or not all(surface in minimums for surface in SURFACES):
        return None, _check("fail", "política Codex sin mínimos probados por superficie")
    if not isinstance(capabilities, dict) or not all(
        isinstance(capabilities.get(surface), list) and capabilities[surface]
        for surface in SURFACES
    ):
        return None, _check("fail", "política Codex sin capacidades requeridas por superficie")
    delivery = data.get("context_delivery")
    required_views = ["startup", "implementation", "closure", "recovery"]
    if not isinstance(delivery, dict) or delivery.get("tool") != "get_work_context":
        return None, _check("fail", "política Codex sin entrega progresiva get_work_context")
    if delivery.get("views") != required_views:
        return None, _check("fail", "context_delivery.views incompleto o desordenado")
    for field, minimum in (("startup_limit_bytes", 1), ("detail_limit_bytes", 1)):
        value = delivery.get(field)
        if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
            return None, _check("fail", f"context_delivery.{field} inválido")
    return data, _check("pass", f"política Codex válida ({path.relative_to(root) if path.is_relative_to(root) else path})")


def _normalize_surfaces(
    surface: str | None,
    required_surfaces: str | Iterable[str] | None,
    host: bool | None,
) -> tuple[list[str], list[str]]:
    selected_name = surface or "cli"
    if host:
        if surface and selected_name != "cli":
            raise ValueError("--host es alias de --surface cli y no puede combinarse con otra superficie")
        selected_name = "cli"
    if selected_name not in (*SURFACES, "all"):
        raise ValueError(f"superficie Codex desconocida: {selected_name}")
    selected = list(SURFACES) if selected_name == "all" else [selected_name]
    if required_surfaces is None:
        required = ["cli"] if selected_name == "all" else list(selected)
    elif isinstance(required_surfaces, str):
        required = [item.strip() for item in required_surfaces.split(",") if item.strip()]
    else:
        required = [str(item).strip() for item in required_surfaces if str(item).strip()]
    if not required or any(item not in SURFACES for item in required) or len(set(required)) != len(required):
        raise ValueError("--required-surfaces debe ser una lista no vacía de cli,desktop sin duplicados")
    if any(item not in selected for item in required):
        raise ValueError("las superficies requeridas deben estar incluidas en --surface")
    return selected, required


def _replace_profile_value(text: str, label: str, value: str) -> tuple[str, bool]:
    pattern = re.compile(
        rf"(?mi)^(\s*(?:[-*]\s*)?(?:\*\*)?{re.escape(label)}(?:\*\*)?"
        rf"(?:\s*\([^:\n]*\))?(?:\*\*)?\s*:\s*)([^\n]+?)\s*$"
    )
    updated, count = pattern.subn(lambda match: f"{match.group(1)}{value}", text, count=1)
    return updated, bool(count)


def _profile_value(text: str, label: str) -> str | None:
    pattern = re.compile(
        rf"(?mi)^\s*(?:[-*]\s*)?(?:\*\*)?{re.escape(label)}(?:\*\*)?"
        rf"(?:\s*\([^:\n]*\))?(?:\*\*)?\s*:\s*([^\n]+?)\s*$"
    )
    match = pattern.search(text)
    return match.group(1).strip() if match else None


def _set_profile_value(text: str, label: str, value: str) -> str:
    updated, replaced = _replace_profile_value(text, label, value)
    if replaced:
        return updated
    suffix = "" if not updated or updated.endswith("\n") else "\n"
    return f"{updated}{suffix}- {label}: {value}\n"


def _set_profile_value_alias(text: str, labels: Iterable[str], value: str) -> str:
    labels = tuple(labels)
    for label in labels:
        updated, replaced = _replace_profile_value(text, label, value)
        if replaced:
            return updated
    return _set_profile_value(text, labels[0], value)


def _record_environment_profile(root: Path, result: dict[str, Any]) -> dict[str, Any]:
    """Registra evidencia observada sin escribir el estado canónico."""
    profile_path = root / "_project" / "15_environment_profile.md"
    if not profile_path.is_file():
        return _check("fail", "no existe _project/15_environment_profile.md para registrar evidencia")
    state_path = root / "_project" / "03_project_state.json"
    if state_path.is_file():
        try:
            state = json.loads(state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            return _check("fail", f"no se pudo verificar el modo del estado canónico: {exc}")
        if isinstance(state, dict) and state.get("framework_mode") == "template":
            return _check("unavailable", "--record-profile no escribe perfiles de un template; inicializar el proyecto primero")

    text = profile_path.read_text(encoding="utf-8", errors="ignore")
    checks = result.get("checks", {})
    surfaces = result.get("surfaces", {})
    required = list(result.get("required_surfaces", ["cli"]))
    structural = str(result.get("structural_support", "unavailable"))
    selected_name = str(result.get("surface", "cli"))
    selected_surfaces = list(SURFACES) if selected_name == "all" else [selected_name]
    cli = surfaces.get("cli", {}) if isinstance(surfaces.get("cli"), dict) else {}
    desktop = surfaces.get("desktop", {}) if isinstance(surfaces.get("desktop"), dict) else {}
    previous_cli_status = _profile_value(text, "Smoke runtime Codex CLI")
    previous_desktop_status = _profile_value(text, "Smoke runtime Codex Desktop/app-server")
    merged_statuses = {
        "cli": str(cli.get("status")) if "cli" in selected_surfaces else (previous_cli_status or "unavailable"),
        "desktop": str(desktop.get("status")) if "desktop" in selected_surfaces else (previous_desktop_status or "unavailable"),
    }
    # ``run_smoke`` has already applied the single H5 policy, including global
    # preservation and strict handling.  Register that exact decision instead
    # of recomputing from surface statuses and accidentally dropping a global
    # preservation failure.
    runtime = str(result.get("runtime_smoke") or aggregate_surface_status(
        {surface: {"status": status} for surface, status in merged_statuses.items()},
        required,
    ))
    cli_hooks = cli.get("hooks", {}) if "cli" in selected_surfaces else {}
    if "cli" not in selected_surfaces:
        previous_hooks = _profile_value(text, "Hooks Codex")
        hooks_value = previous_hooks if previous_hooks in {"gestionados", "manuales", "no disponibles"} else "no disponibles"
    else:
        hooks_value = "gestionados" if all(cli_hooks.get(hook) == "pass" for hook in HOOK_IDS) else "manuales"
        if all(cli_hooks.get(hook) == "unavailable" for hook in HOOK_IDS):
            hooks_value = "no disponibles"
    mcp_statuses = [
        str(surfaces.get(surface, {}).get("mcp", "unavailable"))
        for surface in required
        if surface in selected_surfaces
    ]
    if not mcp_statuses:
        previous_mcp = next(
            (
                value
                for label in (MCP_PROFILE_LABEL, MCP_PROFILE_LABEL_LEGACY)
                if (value := _profile_value(text, label)) is not None
            ),
            None,
        )
        mcp_value = previous_mcp if previous_mcp in {"conectado", "fallback", "no disponible"} else "no disponible"
    else:
        mcp_value = "conectado" if all(status == "pass" for status in mcp_statuses) else "fallback"
        if all(status == "unavailable" for status in mcp_statuses):
            mcp_value = "no disponible"

    previous_cli_version = _profile_value(text, "Versión de Codex CLI")
    previous_desktop_version = _profile_value(text, "Versión de Codex Desktop/app-server")
    cli_version = str(cli.get("version") or (previous_cli_version or "no disponible"))
    desktop_version = str(desktop.get("version") or (previous_desktop_version or "no disponible"))
    model = str(result.get("model") or (_profile_value(text, "Modelo Codex smoke") or "no declarado"))
    effort = str(result.get("reasoning_effort") or (_profile_value(text, "Esfuerzo Codex smoke") or "no declarado"))
    fields = {
        # Campos históricos: se mantienen para proyectos que todavía los leen.
        "Soporte estructural Codex": structural,
        "Smoke runtime Codex": runtime,
        "Versión de Codex": cli_version,
        "Hooks Codex": hooks_value,
        MCP_PROFILE_LABEL: mcp_value,
        "Diferencias Codex conocidas": (
            "Desktop/app-server no certifica detalles visuales; revisar /hooks y usar fallback manual si una capacidad queda unavailable."
        ),
        # Campos por superficie.
        "Superficies Codex requeridas": ",".join(required),
        "Smoke runtime Codex CLI": merged_statuses["cli"],
        "Smoke runtime Codex Desktop/app-server": merged_statuses["desktop"],
        "Versión de Codex CLI": cli_version,
        "Versión de Codex Desktop/app-server": desktop_version,
        "Modelo Codex smoke": model,
        "Esfuerzo Codex smoke": effort,
    }
    for label, value in fields.items():
        if label == MCP_PROFILE_LABEL:
            text = _set_profile_value_alias(text, (MCP_PROFILE_LABEL, MCP_PROFILE_LABEL_LEGACY), value)
        else:
            text = _set_profile_value(text, label, value)
    try:
        profile_path.write_text(text, encoding="utf-8")
    except OSError as exc:
        return _check("fail", f"no se pudo escribir el perfil: {exc}")
    return _check("pass", f"evidencia registrada en {profile_path.relative_to(root)}")


def _protected_hashes(root: Path) -> dict[str, str]:
    paths = [root / ".claude" / "settings.json", root / ".claude" / "settings.local.json"]
    paths.extend(sorted((root / ".claude" / "commands").rglob("*")))
    paths.extend(sorted((root / ".opencode").rglob("*")))
    return {
        str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in paths
        if path.is_file()
    }


def _git_status(root: Path) -> str | None:
    try:
        result = subprocess.run(
            ["git", "status", "--short", "--untracked-files=all"],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return result.stdout if result.returncode == 0 else None


def _git_evidence(before: str | None, after: str | None) -> dict[str, Any]:
    if before is None or after is None:
        return _check("unavailable", "no se pudo comprobar el diff del checkout real")
    if before != after:
        return _check("fail", "el checkout real cambió durante el smoke")
    return _check("pass", "diff del checkout real limpio tras el smoke")


def _preservation_result(
    root: Path,
    before: dict[str, Any] | None,
    after: dict[str, Any] | None,
    transient_paths: Iterable[Path] = (),
) -> dict[str, Any]:
    """Resume la comparación sin exponer hashes ni contenido de archivos."""
    before = before or {"status": "unavailable", "detail": "no se capturó el baseline"}
    after = after or {"status": "unavailable", "detail": "no se capturó la captura final"}
    comparison = codex_preservation.compare_snapshots(before, after)
    summary_source = after if after.get("status") == "pass" else before
    summary = codex_preservation.snapshot_summary(summary_source)
    summary.update(
        {
            "status": comparison["status"],
            "detail": comparison["detail"],
            "changed_paths": list(comparison.get("changed_paths", [])),
            "before_entries": int((comparison.get("counters") or {}).get("before_entries", 0)),
            "after_entries": int((comparison.get("counters") or {}).get("after_entries", 0)),
        }
    )
    transient_relative: list[str] = []
    for path in transient_paths:
        try:
            transient_relative.append(Path(path).resolve().relative_to(root).as_posix())
        except (OSError, ValueError):
            transient_relative.append(str(path))
    summary.setdefault("scope", {})["transient_paths"] = sorted(set(transient_relative))
    return summary


def _append_preservation_leftovers(
    result: dict[str, Any], root: Path, transient_paths: Iterable[Path]
) -> dict[str, Any]:
    leftovers: list[str] = []
    for path in transient_paths:
        candidate = Path(path)
        if not candidate.exists() and not candidate.is_symlink():
            continue
        try:
            # Keep the exact fixture path relative without resolving a
            # symlink target outside the checkout (the target can contain a
            # user-specific or sensitive absolute path).
            relative = candidate.absolute().relative_to(root).as_posix()
        except (OSError, ValueError):
            relative = str(candidate)
        leftovers.append(relative)
    if not leftovers:
        return result
    changed = sorted(set(result.get("changed_paths", [])) | set(leftovers))
    result["status"] = "fail"
    result["detail"] = (
        f"quedaron fixtures temporales en el checkout: {', '.join(sorted(set(leftovers)))}"
    )
    result["changed_paths"] = changed
    return result


def _phase_summary(*turns: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Expose execution state without transcripts or host diagnostics."""
    phases: dict[str, dict[str, Any]] = {}
    for turn in turns:
        name = str(turn.get("phase") or "unknown")
        phases[name] = {
            "state": str(turn.get("phase_state", "not_started")),
            "status": str(turn.get("status", "unavailable")),
        }
    return phases


def _load_config(root: Path) -> dict[str, Any] | None:
    path = root / ".codex" / "hooks.json"
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _find_hook(config: dict[str, Any], hook_id: str, event_name: str | None = None) -> str | None:
    hooks = config.get("hooks")
    if not isinstance(hooks, dict):
        return None
    groups_by_event = ((event_name, hooks.get(event_name)),) if event_name else hooks.items()
    for configured_event, groups in groups_by_event:
        if event_name and configured_event != event_name:
            continue
        if not isinstance(groups, list):
            continue
        for group in groups:
            if not isinstance(group, dict):
                continue
            for hook in group.get("hooks", []):
                if (
                    isinstance(hook, dict)
                    and hook.get("type") == "command"
                    and f"[FW-CODEX:{hook_id}]" in str(hook.get("statusMessage", ""))
                    and f"--hook-id {hook_id}" in str(hook.get("command", ""))
                ):
                    return str(hook["command"])
    return None


def _skill_frontmatter_valid(path: Path) -> bool:
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return False
    if not text.startswith("---\n"):
        return False
    end = text.find("\n---", 4)
    if end < 0:
        return False
    fields: dict[str, str] = {}
    for line in text[4:end].splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        fields[key.strip()] = value.strip().strip('"')
    expected_name = path.parent.name
    return bool(fields.get("name") == expected_name and fields.get("description") and GENERATED_MARKER in text)


def _host_failure_detail(result: subprocess.CompletedProcess[str]) -> str:
    for line in reversed(result.stdout.splitlines()):
        stripped = line.strip()
        if not stripped:
            continue
        try:
            payload = json.loads(stripped)
        except json.JSONDecodeError:
            payload = None
        if isinstance(payload, dict):
            if payload.get("type") == "error" and payload.get("message"):
                return str(payload["message"])
            error = payload.get("error")
            if isinstance(error, dict) and error.get("message"):
                return str(error["message"])
        if "error" in stripped.casefold() or "failed" in stripped.casefold():
            return stripped
    for line in reversed(result.stderr.splitlines()):
        stripped = line.strip()
        if stripped and " warn " not in f" {stripped.casefold()} ":
            return stripped
    return f"Codex host terminó con rc={result.returncode}"


def _trust_status(stdout: str, stderr: str) -> dict[str, Any] | None:
    lowered = f"{stdout}\n{stderr}".casefold()
    for token in TRUST_TOKENS:
        if token in lowered:
            return _check("unavailable", "Codex no cargó la configuración local: el proyecto requiere confianza/revisión explícita mediante /hooks")
    return None


def _parse_json_lines(text: str) -> tuple[list[dict[str, Any]], str | None]:
    events: list[dict[str, Any]] = []
    invalid: list[str] = []
    for line in text.splitlines():
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            invalid.append(line[:120])
            continue
        if isinstance(payload, dict):
            events.append(payload)
    return events, (f"salida JSONL inválida: {invalid[0]}" if invalid else None)


def _walk_dicts(value: Any) -> Iterable[dict[str, Any]]:
    if isinstance(value, dict):
        yield value
        for nested in value.values():
            yield from _walk_dicts(nested)
    elif isinstance(value, list):
        for nested in value:
            yield from _walk_dicts(nested)


def _mcp_evidence(events: Iterable[dict[str, Any]]) -> dict[str, Any]:
    started: set[str] = set()
    completed: set[str] = set()
    seen_target = False
    for payload in events:
        for item in _walk_dicts(payload):
            item_type = str(item.get("type", "")).replace("_", "").casefold()
            if item_type != "mcptoolcall":
                continue
            if item.get("server") != "state_manager" or item.get("tool") != MCP_CONTEXT_TOOL:
                continue
            seen_target = True
            item_id = str(item.get("id", ""))
            arguments = item.get("arguments")
            status = str(item.get("status", ""))
            if item_id and status in {"inProgress", "started", "pending"} and arguments == MCP_CONTEXT_ARGUMENTS:
                started.add(item_id)
            result = item.get("result")
            error = item.get("error")
            if (
                item_id
                and status == "completed"
                and arguments == MCP_CONTEXT_ARGUMENTS
                and isinstance(result, dict)
                and isinstance(result.get("content"), list)
                and ("error" not in item or error is None)
            ):
                completed.add(item_id)
    if started & completed:
        return _check("pass", "state_manager.get_work_context(startup) invocable y completado por el host")
    if seen_target:
        return _check("fail", "el host mencionó state_manager.get_work_context pero no produjo inicio+finalización correlacionados y satisfactorios")
    return _check("fail", "MCP visible o textual, pero no se observó una llamada real a state_manager.get_work_context(startup)")


_NO_MARKER_DIR = Path("/nonexistent-codex-smoke")  # sin HOME no hay carpeta: el runner rechazará la escritura
_DIR_OPEN_FLAGS = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)


def _open_marker_dir(path: Path) -> int | None:
    """Descriptor de la carpeta del marcador, sin seguir un enlace y solo si es un directorio propio."""
    try:
        fd = os.open(path.parent, _DIR_OPEN_FLAGS)
    except OSError:
        return None
    if turn_close_hook.is_own_directory_fd(fd):
        return fd
    os.close(fd)
    return None


@contextlib.contextmanager
def _smoke_marker(root: Path, run_id: str) -> Iterator[Path]:
    """Ruta del marcador del host: `smoke/<run_id>.jsonl` en la telemetría del proyecto.

    Es el único sitio donde el runner de Codex acepta escribirlo. Al terminar se borra SU fichero
    (nunca la carpeta ni otros ficheros), relativo a un descriptor de la carpeta real y propia: si
    `smoke/` es un enlace o no es nuestra, no se borra nada. Si la carpeta no se puede preparar (o no
    hay HOME), el runner rechazará la escritura y el veredicto será que el host no produjo marcador."""
    name = re.sub(r"[^A-Za-z0-9_-]", "", run_id)[:64] or "run"
    path = _NO_MARKER_DIR / f"{name}.jsonl"
    project_fd = smoke_fd = None
    try:
        path = turn_close_hook.telemetry_dir(root) / "smoke" / f"{name}.jsonl"
        project_fd = turn_close_hook.open_telemetry_dir(root)
        if project_fd is not None:
            smoke_fd = turn_close_hook.open_own_dir(project_fd, "smoke")
    except Exception:  # noqa: BLE001 - p. ej. HOME irresoluble: el marcador no se podrá escribir
        pass
    finally:
        for fd in (smoke_fd, project_fd):
            if fd is not None:
                os.close(fd)
    try:
        yield path
    finally:
        if path.parent != _NO_MARKER_DIR:
            dir_fd = _open_marker_dir(path)
            if dir_fd is not None:
                try:
                    os.unlink(path.name, dir_fd=dir_fd)
                except OSError:
                    pass
                finally:
                    os.close(dir_fd)


def _marker_records(path: Path, run_id: str) -> tuple[list[dict[str, Any]], str | None]:
    # Se lee relativo a la carpeta del marcador abierta sin seguir enlaces (un `smoke/` enlazado no cuenta).
    dir_fd = _open_marker_dir(path)
    if dir_fd is None:
        return [], "el host no produjo marcador de hooks"
    records: list[dict[str, Any]] = []
    try:
        try:
            fd = os.open(path.name, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
                         | getattr(os, "O_CLOEXEC", 0), dir_fd=dir_fd)
        except FileNotFoundError:
            return [], "el host no produjo marcador de hooks"
        except OSError as exc:
            return [], f"no se pudo leer el marcador de hooks: {exc}"
        with os.fdopen(fd, "rb") as handle:
            if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
                return [], "el host no produjo marcador de hooks"
            try:
                lines = handle.read().decode("utf-8").splitlines()
            except (OSError, UnicodeDecodeError) as exc:
                return [], f"no se pudo leer el marcador de hooks: {exc}"
    finally:
        os.close(dir_fd)
    for line in lines:
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            return [], "marcador de hooks corrupto; no se ignoran líneas inválidas"
        if not isinstance(record, dict):
            return [], "marcador de hooks contiene una línea que no es objeto"
        if (
            record.get("schema_version") != 1
            or record.get("run_id") != run_id
            or record.get("hook_id") not in HOOK_IDS
            or record.get("phase") not in {"started", "completed", "error"}
            or record.get("status") not in {"pass", "fail", "unavailable"}
        ):
            return [], "marcador de hooks con schema, run_id o enum inválido"
        records.append(record)
    return records, None


def _hook_statuses(records: list[dict[str, Any]], marker_error: str | None) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for hook_id in HOOK_IDS:
        entries = [record for record in records if record.get("hook_id") == hook_id]
        if any(record.get("phase") == "error" or record.get("status") == "fail" for record in entries):
            result[hook_id] = _check("fail", f"el hook {hook_id} registró error")
        elif (
            any(record.get("phase") == "started" and record.get("status") == "pass" for record in entries)
            and any(record.get("phase") == "completed" and record.get("status") == "pass" for record in entries)
        ):
            result[hook_id] = _check("pass", f"hook {hook_id} observado")
        elif marker_error:
            result[hook_id] = _check("fail", marker_error)
        else:
            result[hook_id] = _check("unavailable", f"evidencia incompleta del hook {hook_id}; faltan fases started/completed")
    return result


def _run_configured_hook(
    command: str,
    root: Path,
    event: dict[str, Any],
    cwd: Path,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        cwd=cwd,
        input=json.dumps(event, ensure_ascii=False),
        capture_output=True,
        text=True,
        shell=True,
        timeout=15,
        check=False,
        env=os.environ.copy(),
    )


def _codex_binary(explicit: str | None) -> str | None:
    if explicit:
        candidates = [explicit, shutil.which(explicit)]
    else:
        candidates = [shutil.which("codex"), "/Applications/ChatGPT.app/Contents/Resources/codex"]
    for candidate in candidates:
        if candidate and Path(candidate).is_file() and os.access(candidate, os.X_OK):
            return candidate
    return None


def _version_probe(binary: str, root: Path) -> dict[str, Any]:
    try:
        result = subprocess.run(
            [binary, "--version"], cwd=root, capture_output=True, text=True, timeout=15, check=False
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return _check("unavailable", f"no se pudo ejecutar codex --version: {exc}")
    detail = (result.stdout or result.stderr).strip().splitlines()[0] if (result.stdout or result.stderr).strip() else "sin versión reportada"
    return _check("pass" if result.returncode == 0 else "fail", detail, version=detail if result.returncode == 0 else None)


def _cli_command(binary: str, cwd: Path, sandbox: str, model: str, effort: str, prompt: str) -> list[str]:
    return [
        binary,
        "exec",
        "--ephemeral",
        "--json",
        "--cd",
        str(cwd),
        "--sandbox",
        sandbox,
        "--model",
        model,
        "-c",
        f"model_reasoning_effort={json.dumps(effort)}",
        prompt,
    ]


def _run_cli_turn(
    binary: str,
    root: Path,
    cwd: Path,
    sandbox: str,
    model: str,
    effort: str,
    prompt: str,
    marker_path: Path,
    run_id: str,
    fixture_file: Path | None = None,
) -> dict[str, Any]:
    phase_name = "fixture_edit" if fixture_file is not None else "read_only"

    def result(status: str, detail: str, *, phase_state: str, events: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        return {
            "status": status,
            "detail": detail,
            "events": events or [],
            "argv": command,
            "phase": phase_name,
            "phase_state": phase_state,
        }

    environment = os.environ.copy()
    environment[SMOKE_MARKER_ENV] = str(marker_path)
    environment[SMOKE_RUN_ID_ENV] = run_id
    if fixture_file is not None:
        environment[SMOKE_FIXTURE_FILE_ENV] = str(fixture_file)
    command = _cli_command(binary, cwd, sandbox, model, effort, prompt)
    try:
        completed = subprocess.run(
            command,
            cwd=root,
            env=environment,
            capture_output=True,
            text=True,
            timeout=APP_SERVER_TIMEOUT,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        return result("unavailable", f"timeout de Codex exec tras {APP_SERVER_TIMEOUT}s: {exc}", phase_state="running")
    except OSError as exc:
        return result("unavailable", f"no se pudo ejecutar Codex exec: {exc}", phase_state="not_started")
    trust = _trust_status(completed.stdout, completed.stderr)
    if trust:
        return result(trust["status"], trust["detail"], phase_state="not_started")
    events, invalid = _parse_json_lines(completed.stdout)
    if completed.returncode != 0:
        return result("fail", _host_failure_detail(completed), phase_state="completed", events=events)
    if invalid:
        return result("fail", invalid, phase_state="completed", events=events)
    if "codex_framework_smoke_ok" not in completed.stdout.casefold():
        return result("fail", "Codex no devolvió el marcador final de la prueba host", phase_state="completed", events=events)
    return result("pass", "turno Codex exec completado", phase_state="completed", events=events)


def _run_cli_surface(
    binary: str,
    root: Path,
    model: str | None,
    effort: str | None,
    version: str | None,
) -> dict[str, Any]:
    unavailable_hooks = {hook_id: "unavailable" for hook_id in HOOK_IDS}
    base: dict[str, Any] = {
        "status": "unavailable",
        "version": version,
        "hooks": unavailable_hooks,
        "mcp": "unavailable",
        "argv": [],
        "detail": "modelo y esfuerzo deben ser explícitos para certificar CLI",
        "preservation": _check("unavailable", "no se ejecutó el host CLI"),
    }
    if not model or not effort or effort not in REASONING_EFFORTS:
        return base
    codex_dir = root / ".codex"
    if not codex_dir.is_dir():
        base["detail"] = "falta .codex para ejecutar el host Codex"
        return base
    before_snapshot = codex_preservation.capture_snapshot(root)
    if before_snapshot.get("status") != "pass":
        preservation = _preservation_result(root, before_snapshot, None)
        base.update(
            {
                "status": preservation["status"],
                "preservation": preservation,
                "detail": f"no se puede iniciar el host: {preservation['detail']}",
            }
        )
        return base
    run_id = uuid.uuid4().hex
    before_git = _git_status(root)
    fixture_paths: list[Path] = []
    fixture_changed = False
    first: dict[str, Any] = {
        "status": "unavailable",
        "detail": "no se ejecutó la fase de lectura",
        "events": [],
        "phase": "read_only",
        "phase_state": "not_started",
    }
    second: dict[str, Any] = {
        "status": "unavailable",
        "detail": "la primera fase no habilitó la edición fixture",
        "events": [],
        "phase": "fixture_edit",
        "phase_state": "not_started",
    }
    records: list[dict[str, Any]] = []
    marker_error: str | None = None
    with _smoke_marker(root, run_id) as marker_path:
        try:
            first = _run_cli_turn(
                binary,
                root,
                root,
                "read-only",
                model,
                effort,
                "Prueba de integración del framework. Ejecuta únicamente `git status --short` y llama exactamente a la herramienta MCP local state_manager.get_work_context con argumentos {\"view\":\"startup\"}. No edites archivos, no llames mutate_state y responde exactamente CODEX_FRAMEWORK_SMOKE_OK.",
                marker_path,
                run_id,
            )
        except Exception as exc:
            first = {
                "status": "fail",
                "detail": f"falló la fase de lectura del host CLI: {exc}",
                "events": [],
                "phase": "read_only",
                "phase_state": "completed",
                "argv": [],
            }
        fixture_file: Path | None = None
        if first["status"] == "pass":
            try:
                with tempfile.TemporaryDirectory(prefix=f"smoke-fixture-{run_id}-", dir=str(codex_dir)) as fixture_dir:
                    fixture_paths.append(Path(fixture_dir))
                    fixture_file = Path(fixture_dir) / "edit.md"
                    fixture_file.write_text("Codex smoke fixture\n", encoding="utf-8")
                    try:
                        second = _run_cli_turn(
                            binary,
                            root,
                            Path(fixture_dir),
                            "workspace-write",
                            model,
                            effort,
                            f"Prueba aislada de hooks. Edita únicamente {fixture_file} añadiendo una línea 'CODEX_FIXTURE_EDIT_OK'. No edites ningún otro archivo y responde exactamente CODEX_FRAMEWORK_SMOKE_OK.",
                            marker_path,
                            run_id,
                            fixture_file,
                        )
                    except Exception as exc:
                        second = {
                            "status": "fail",
                            "detail": f"falló la fase fixture del host CLI: {exc}",
                            "events": [],
                            "phase": "fixture_edit",
                            "phase_state": "completed",
                            "argv": [],
                        }
                    fixture_changed = fixture_file.read_text(encoding="utf-8") != "Codex smoke fixture\n"
            except Exception as exc:
                second = {"status": "fail", "detail": f"falló el fixture temporal: {exc}", "events": [], "phase": "fixture_edit", "phase_state": "completed"}
                fixture_changed = False
        else:
            fixture_changed = False
        records, marker_error = _marker_records(marker_path, run_id)
    hooks = _hook_statuses(records, marker_error)
    hook_values = {hook_id: detail["status"] for hook_id, detail in hooks.items()}
    if first.get("status") == "pass":
        mcp = _mcp_evidence(first.get("events", []) + second.get("events", []))
    else:
        mcp = _check("unavailable", "MCP no se evaluó porque la fase de lectura no terminó satisfactoriamente")
    after_snapshot = codex_preservation.capture_snapshot(root, tuple(fixture_paths))
    preservation = _preservation_result(root, before_snapshot, after_snapshot, fixture_paths)
    preservation = _append_preservation_leftovers(preservation, root, fixture_paths)
    after_git = _git_status(root)
    git_check = _git_evidence(before_git, after_git)
    statuses = [
        first["status"],
        second["status"],
        mcp["status"],
        git_check["status"],
        preservation["status"],
        *hook_values.values(),
    ]
    if second.get("phase_state") == "completed" and second.get("status") == "pass" and not fixture_changed:
        statuses.append("fail")
    status = _aggregate_statuses(statuses)
    base.update(
        {
            "status": status,
            "hooks": hook_values,
            "mcp": mcp["status"],
            "git_diff": git_check["status"],
            "preservation": preservation,
            "phases": _phase_summary(first, second),
            "fixture_changed": fixture_changed,
            "argv": [
                turn["argv"]
                for turn in (first, second)
                if isinstance(turn.get("argv"), list)
            ],
            "detail": "; ".join(
                part
                for part in (
                    first.get("detail"),
                    second.get("detail"),
                    mcp.get("detail"),
                    None if git_check["status"] == "pass" else git_check.get("detail"),
                    (
                        "el host no produjo la edición fixture esperada"
                        if second.get("phase_state") == "completed"
                        and second.get("status") == "pass"
                        and not fixture_changed
                        else None
                    ),
                )
                if part
            ),
        }
    )
    return base


def _toml_override(key: str, value: str) -> str:
    return f"{key}={json.dumps(value)}"


def _app_server_command(binary: str, model: str, effort: str, transport: str) -> list[str]:
    return [
        binary,
        "app-server",
        "-c",
        _toml_override("model", model),
        "-c",
        _toml_override("model_reasoning_effort", effort),
        *(["--stdio"] if transport == "stdio" else ["--listen", "stdio://"]),
    ]


def _app_server_schema_probe(binary: str, root: Path, model: str, effort: str) -> dict[str, Any]:
    """Lee el esquema efímero si el binario ofrece el comando experimental."""
    with tempfile.TemporaryDirectory(prefix="codex-app-server-schema-") as output_dir:
        try:
            completed = subprocess.run(
                [
                    binary,
                    "app-server",
                    "generate-json-schema",
                    "-c",
                    _toml_override("model", model),
                    "-c",
                    _toml_override("model_reasoning_effort", effort),
                    "--out",
                    output_dir,
                ],
                cwd=root,
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            return _check("unavailable", f"app-server no ofrece generación de esquema: {exc}")
        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout).strip().splitlines()
            return _check(
                "unavailable",
                "app-server no ofrece generate-json-schema" + (f": {detail[-1]}" if detail else ""),
            )
        schema_files = sorted(Path(output_dir).rglob("*.json"))
        if not schema_files:
            return _check("unavailable", "generate-json-schema terminó sin producir un bundle")
        try:
            documents = [json.loads(path.read_text(encoding="utf-8")) for path in schema_files]
        except (OSError, json.JSONDecodeError) as exc:
            return _check("unavailable", f"bundle app-server ilegible: {exc}")
        combined = json.dumps(documents, ensure_ascii=False)
        required_needles = ("thread/start", "turn/start", "mcpToolCall", "hook/started")
        missing = [needle for needle in required_needles if needle not in combined]
        if missing:
            return _check("unavailable", f"esquema app-server sin capacidades requeridas: {', '.join(missing)}")

        def enum_values(document: Any) -> set[str]:
            values: set[str] = set()
            for item in _walk_dicts(document):
                enum = item.get("enum")
                if isinstance(enum, list):
                    values.update(str(value) for value in enum if isinstance(value, str))
            return values

        thread_documents = [
            document
            for path, document in zip(schema_files, documents)
            if path.name == "ThreadStartParams.json"
        ]
        turn_documents = [
            document
            for path, document in zip(schema_files, documents)
            if path.name == "TurnStartParams.json"
        ]
        thread_values = set().union(*(enum_values(document) for document in thread_documents))
        turn_values = set().union(*(enum_values(document) for document in turn_documents))
        if {"read-only", "workspace-write"}.issubset(thread_values):
            sandbox_read_only, sandbox_workspace_write = "read-only", "workspace-write"
        elif {"readOnly", "workspaceWrite"}.issubset(thread_values):
            sandbox_read_only, sandbox_workspace_write = "readOnly", "workspaceWrite"
        else:
            return _check("unavailable", "esquema app-server no declara un enum SandboxMode compatible")
        if "readOnly" in turn_values and "workspaceWrite" in turn_values:
            policy_read_only, policy_workspace_write = "readOnly", "workspaceWrite"
        else:
            return _check("unavailable", "esquema app-server no declara SandboxPolicy readOnly/workspaceWrite")
        return _check(
            "pass",
            f"esquema app-server observado ({len(schema_files)} archivos efímeros)",
            schema_sha256=hashlib.sha256(combined.encode("utf-8")).hexdigest(),
            schema_files=len(schema_files),
            sandbox_read_only=sandbox_read_only,
            sandbox_workspace_write=sandbox_workspace_write,
            policy_read_only=policy_read_only,
            policy_workspace_write=policy_workspace_write,
        )


def _send_json(process: subprocess.Popen[bytes], payload: dict[str, Any]) -> None:
    if process.stdin is None:
        raise OSError("app-server stdin no disponible")
    encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8") + b"\n"
    process.stdin.write(encoded)
    process.stdin.flush()


def _read_until_response(
    wire: codex_app_server_reader.AppServerReader,
    process: subprocess.Popen[bytes],
    expected_id: int,
    messages: list[dict[str, Any]],
    server_requests: list[dict[str, Any]],
    deadline: float,
) -> dict[str, Any] | None:
    while time.monotonic() < deadline:
        payload = _read_one_message(wire, process, server_requests, deadline)
        if payload is None:
            return None
        if payload.get("id") == expected_id:
            return payload
        messages.append(payload)
    return None


def _read_one_message(
    wire: codex_app_server_reader.AppServerReader,
    process: subprocess.Popen[bytes],
    server_requests: list[dict[str, Any]],
    deadline: float,
) -> dict[str, Any] | None:
    """Lee una línea JSONL persistente y rechaza requests no soportados."""
    while True:
        payload = wire.read_message(deadline)
        if payload is None:
            return None
        if "id" in payload and "method" in payload and "result" not in payload and "error" not in payload:
            server_requests.append(payload)
            try:
                _send_json(
                    process,
                    {
                        "id": payload.get("id"),
                        "error": {"code": -32601, "message": "unsupported smoke request"},
                    },
                )
            except OSError:
                pass
            continue
        return payload


def _finish_process(
    process: subprocess.Popen[bytes] | None,
    wire: codex_app_server_reader.AppServerReader | None = None,
) -> str:
    if process is None:
        if wire is not None:
            try:
                tail = wire.stderr_tail()
            except Exception:
                tail = ""
            try:
                wire.close()
            except Exception:
                pass
            return tail
        return ""
    try:
        if process.stdin:
            process.stdin.close()
    except (OSError, ValueError):
        pass
    if process.poll() is None:
        try:
            process.terminate()
        except OSError:
            pass
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        try:
            process.kill()
        except OSError:
            pass
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            # The process is still external state; there is no safe way to
            # block indefinitely during cleanup.  Streams are closed below and
            # the caller receives the observed phase result.
            pass
    try:
        if wire is not None:
            try:
                wire.drain_stderr(time.monotonic() + 5)
            except codex_app_server_reader.ReaderError:
                pass
            try:
                stderr_text = wire.stderr_tail()
            except Exception:
                stderr_text = ""
            try:
                wire.close()
            except Exception:
                pass
        else:
            stderr_text = process.stderr.read() if process.stderr is not None else b""
    except (OSError, ValueError):
        stderr_text = b""
    finally:
        for stream in (process.stdout, process.stderr):
            try:
                if stream is not None:
                    stream.close()
            except (OSError, ValueError):
                pass
    if isinstance(stderr_text, bytes):
        return stderr_text.decode("utf-8", "replace")
    return str(stderr_text)


def _run_app_server_turn(
    binary: str,
    root: Path,
    cwd: Path,
    sandbox: str,
    model: str,
    effort: str,
    prompt: str,
    marker_path: Path,
    run_id: str,
    fixture_file: Path | None = None,
    policy_type: str | None = None,
) -> dict[str, Any]:
    phase_name = "fixture_edit" if fixture_file is not None else "read_only"

    def make_result(
        status: str,
        detail: str,
        *,
        phase_state: str,
        version: str | None = None,
        protocol_version: str | None = None,
        transport: str = "app-server",
        argv: list[str] | None = None,
    ) -> dict[str, Any]:
        return {
            "status": status,
            "detail": detail,
            "messages": messages,
            "version": version,
            "protocol_version": protocol_version,
            "transport": transport,
            "argv": argv or last_command,
            "phase": phase_name,
            "phase_state": phase_state,
        }

    environment = os.environ.copy()
    environment[SMOKE_MARKER_ENV] = str(marker_path)
    environment[SMOKE_RUN_ID_ENV] = run_id
    if fixture_file is not None:
        environment[SMOKE_FIXTURE_FILE_ENV] = str(fixture_file)
    messages: list[dict[str, Any]] = []
    server_requests: list[dict[str, Any]] = []
    process: subprocess.Popen[bytes] | None = None
    wire: codex_app_server_reader.AppServerReader | None = None
    launch_error: str | None = None
    used_transport = "stdio"
    last_command: list[str] = []
    phase_state = "not_started"
    for transport in ("stdio", "listen"):
        command = _app_server_command(binary, model, effort, transport)
        last_command = command
        try:
            process = subprocess.Popen(
                command,
                cwd=root,
                env=environment,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                bufsize=0,
            )
            wire = codex_app_server_reader.AppServerReader(process.stdout, process.stderr)
            used_transport = transport
            phase_state = "running"
        except (OSError, ValueError, TypeError) as exc:
            launch_error = str(exc)
            if process is not None:
                _finish_process(process, wire)
                process = None
                wire = None
            break
        try:
            _send_json(
                process,
                {
                    "id": 1,
                    "method": "initialize",
                    "params": {
                        "clientInfo": {"name": "framework-codex-smoke", "title": "Framework smoke", "version": "1"},
                        "capabilities": {},
                    },
                },
            )
            deadline = time.monotonic() + APP_SERVER_TIMEOUT
            initialize = _read_until_response(wire, process, 1, messages, server_requests, deadline)
            if initialize is None:
                raise RuntimeError("app-server no respondió initialize")
            if "error" in initialize or not isinstance(initialize.get("result"), dict):
                raise RuntimeError(f"initialize inválido: {initialize.get('error', initialize)}")
            result = initialize["result"]
            user_agent = str(result.get("userAgent") or result.get("version") or "").strip()
            if not user_agent:
                raise RuntimeError("initialize no declaró userAgent/version")
            protocol_version = str(result.get("protocolVersion") or result.get("protocol_version") or "") or None
            _send_json(process, {"method": "initialized", "params": {}})
            _send_json(
                process,
                {
                    "id": 2,
                    "method": "thread/start",
                    "params": {
                        "cwd": str(cwd),
                        "model": model,
                        "approvalPolicy": "never",
                        "sandbox": sandbox,
                        "ephemeral": True,
                    },
                },
            )
            thread_response = _read_until_response(wire, process, 2, messages, server_requests, deadline)
            thread = thread_response.get("result", {}).get("thread", {}) if isinstance(thread_response, dict) else {}
            thread_id = thread.get("id") if isinstance(thread, dict) else None
            if thread_response is None or "error" in thread_response or not thread_id:
                raise RuntimeError(f"thread/start inválido: {thread_response}")
            _send_json(
                process,
                {
                    "id": 3,
                    "method": "turn/start",
                    "params": {
                        "threadId": thread_id,
                        "input": [{"type": "text", "text": prompt}],
                        "model": model,
                        "effort": effort,
                        "approvalPolicy": "never",
                        "sandboxPolicy": {"type": policy_type or ("readOnly" if sandbox == "read-only" else "workspaceWrite")},
                    },
                },
            )
            turn_response = _read_until_response(wire, process, 3, messages, server_requests, deadline)
            turn = turn_response.get("result", {}).get("turn", {}) if isinstance(turn_response, dict) else {}
            turn_id = turn.get("id") if isinstance(turn, dict) else None
            if turn_response is None or "error" in turn_response or not turn_id:
                raise RuntimeError(f"turn/start inválido: {turn_response}")
            completed = False
            turn_failed = False
            while time.monotonic() < deadline:
                notification = _read_one_message(wire, process, server_requests, deadline)
                if notification is None:
                    break
                messages.append(notification)
                if notification.get("method") == "error":
                    turn_failed = True
                if notification.get("method") == "turn/completed":
                    params = notification.get("params", {})
                    if isinstance(params, dict) and params.get("threadId") == thread_id:
                        completed_turn = params.get("turn", {})
                        if isinstance(completed_turn, dict) and completed_turn.get("id") == turn_id:
                            status = completed_turn.get("status")
                            completed = status == "completed"
                            turn_failed = turn_failed or status in {"failed", "interrupted"}
                            break
            if not completed or turn_failed:
                state = "falló" if turn_failed else "no terminó"
                raise RuntimeError(f"turn/completed {state}")
            stderr = _finish_process(process, wire)
            process = None
            wire = None
            trust = _trust_status("\n".join(json.dumps(message) for message in messages), stderr)
            if trust:
                return make_result(trust["status"], trust["detail"], phase_state="not_started", version=user_agent, protocol_version=protocol_version, argv=command)
            if server_requests:
                return make_result("fail", "app-server solicitó una operación no permitida; se respondió -32601", phase_state="completed", version=user_agent, protocol_version=protocol_version, argv=command)
            return make_result("pass", f"turno app-server completado ({used_transport})", phase_state="completed", version=user_agent, protocol_version=protocol_version, argv=command)
        except Exception as exc:
            launch_error = str(exc)
            process_was_running = process is not None and process.poll() is None
            stderr = _finish_process(process, wire)
            process = None
            wire = None
            # --stdio existe en el baseline; solo reintentar con la forma
            # documentada si el primer proceso rechazó la bandera de transporte.
            if transport == "stdio" and any(token in f"{launch_error}\n{stderr}".casefold() for token in ("unknown option", "unexpected argument", "unrecognized option", "--stdio")):
                phase_state = "not_started"
                continue
            timed_out = isinstance(exc, codex_app_server_reader.ReaderTimeout)
            explicit_error = isinstance(exc, RuntimeError)
            no_conclusive_response = explicit_error and any(
                phrase in launch_error.casefold() for phrase in ("no respondió", "no terminó")
            )
            if timed_out or no_conclusive_response:
                status = "unavailable"
                phase_state = "running" if process_was_running else "not_started"
            elif (
                isinstance(exc, codex_app_server_reader.ReaderError)
                or explicit_error
                or not isinstance(exc, (OSError, subprocess.SubprocessError, ValueError, TypeError))
            ):
                status = "fail"
                phase_state = "completed"
            else:
                status = "unavailable"
                phase_state = "running" if process_was_running else "not_started"
            return make_result(status, f"app-server no disponible: {launch_error}" if status == "unavailable" else f"app-server falló: {launch_error}", phase_state=phase_state, argv=command)
    return make_result("unavailable", f"app-server no disponible: {launch_error or 'sin respuesta'}", phase_state=phase_state, argv=last_command)


def _hook_ids_from_app_messages(messages: Iterable[dict[str, Any]]) -> set[str]:
    found: set[str] = set()
    for message in messages:
        if message.get("method") not in {"hook/started", "hook/completed"}:
            continue
        params = message.get("params", {})
        if not isinstance(params, dict):
            continue
        run = params.get("run") if isinstance(params.get("run"), dict) else params
        raw_id = run.get("hookId") or run.get("hook_id") or run.get("hook")
        event_name = run.get("eventName") or run.get("event_name")
        normalized = str(raw_id or event_name or "").casefold()
        if normalized in {"guard", "state", "format", "stop"}:
            found.add(normalized)
        elif normalized in {"pretooluse", "pretool_use"}:
            found.add("guard")
        elif normalized in {"posttooluse", "posttool_use"}:
            found.update({"state", "format"})
        elif normalized == "stop":
            found.add("stop")
    return found


def _run_desktop_surface(
    binary: str,
    root: Path,
    model: str | None,
    effort: str | None,
    version: str | None,
) -> dict[str, Any]:
    base: dict[str, Any] = {
        "status": "unavailable",
        "transport": "app-server",
        "version": version,
        "protocol_version": None,
        "hooks": {hook_id: "unavailable" for hook_id in HOOK_IDS},
        "mcp": "unavailable",
        "argv": [],
        "detail": "modelo y esfuerzo deben ser explícitos para certificar Desktop/app-server",
        "preservation": _check("unavailable", "no se ejecutó el host Desktop/app-server"),
    }
    if not model or not effort or effort not in REASONING_EFFORTS:
        return base
    codex_dir = root / ".codex"
    if not codex_dir.is_dir():
        base["detail"] = "falta .codex para ejecutar app-server"
        return base
    before_snapshot = codex_preservation.capture_snapshot(root)
    if before_snapshot.get("status") != "pass":
        preservation = _preservation_result(root, before_snapshot, None)
        base.update(
            {
                "status": preservation["status"],
                "preservation": preservation,
                "detail": f"no se puede iniciar el host: {preservation['detail']}",
            }
        )
        return base
    schema = _app_server_schema_probe(binary, root, model, effort)
    base["schema"] = schema["status"]
    if schema["status"] == "pass":
        sandbox_read_only = str(schema["sandbox_read_only"])
        sandbox_workspace_write = str(schema["sandbox_workspace_write"])
        policy_read_only = str(schema["policy_read_only"])
        policy_workspace_write = str(schema["policy_workspace_write"])
    else:
        # Older app-server builds may not expose the experimental schema command;
        # the installed baseline remains the authority for this wire shape.
        sandbox_read_only, sandbox_workspace_write = "read-only", "workspace-write"
        policy_read_only, policy_workspace_write = "readOnly", "workspaceWrite"
    base["schema_detail"] = schema["detail"]
    if "schema_sha256" in schema:
        base["schema_sha256"] = schema["schema_sha256"]
    if "schema_files" in schema:
        base["schema_files"] = schema["schema_files"]
    run_id = uuid.uuid4().hex
    before_git = _git_status(root)
    fixture_paths: list[Path] = []
    fixture_changed = False
    first: dict[str, Any] = {
        "status": "unavailable",
        "detail": "no se ejecutó la fase de lectura",
        "messages": [],
        "phase": "read_only",
        "phase_state": "not_started",
    }
    second: dict[str, Any] = {
        "status": "unavailable",
        "detail": "la primera fase no habilitó la edición fixture",
        "messages": [],
        "phase": "fixture_edit",
        "phase_state": "not_started",
    }
    records: list[dict[str, Any]] = []
    marker_error: str | None = None
    with _smoke_marker(root, run_id) as marker_path:
        try:
            first = _run_app_server_turn(
                binary,
                root,
                root,
                sandbox_read_only,
                model,
                effort,
                "Prueba de integración del framework. Ejecuta únicamente `git status --short` y llama exactamente a state_manager.get_work_context con argumentos {\"view\":\"startup\"}. No edites archivos ni llames mutate_state.",
                marker_path,
                run_id,
                policy_type=policy_read_only,
            )
        except Exception as exc:
            first = {
                "status": "fail",
                "detail": f"falló la fase de lectura del host Desktop: {exc}",
                "messages": [],
                "phase": "read_only",
                "phase_state": "completed",
                "argv": [],
            }
        if first["status"] == "pass":
            try:
                with tempfile.TemporaryDirectory(prefix=f"smoke-fixture-{run_id}-", dir=str(codex_dir)) as fixture_dir:
                    fixture_paths.append(Path(fixture_dir))
                    fixture_file = Path(fixture_dir) / "edit.md"
                    fixture_file.write_text("Codex smoke fixture\n", encoding="utf-8")
                    try:
                        second = _run_app_server_turn(
                            binary,
                            root,
                            Path(fixture_dir),
                            sandbox_workspace_write,
                            model,
                            effort,
                            f"Prueba aislada de hooks. Edita únicamente {fixture_file} añadiendo una línea 'CODEX_FIXTURE_EDIT_OK'. No edites ningún otro archivo.",
                            marker_path,
                            run_id,
                            fixture_file,
                            policy_type=policy_workspace_write,
                        )
                    except Exception as exc:
                        second = {
                            "status": "fail",
                            "detail": f"falló la fase fixture del host Desktop: {exc}",
                            "messages": [],
                            "phase": "fixture_edit",
                            "phase_state": "completed",
                            "argv": [],
                        }
                    fixture_changed = fixture_file.read_text(encoding="utf-8") != "Codex smoke fixture\n"
            except Exception as exc:
                second = {"status": "fail", "detail": f"falló el fixture temporal: {exc}", "messages": [], "phase": "fixture_edit", "phase_state": "completed"}
        records, marker_error = _marker_records(marker_path, run_id)
    hooks = _hook_statuses(records, marker_error)
    observed_host_hooks = _hook_ids_from_app_messages(first.get("messages", []) + second.get("messages", []))
    hook_values: dict[str, str] = {}
    for hook_id, detail in hooks.items():
        if detail["status"] == "pass" and hook_id not in observed_host_hooks:
            hook_values[hook_id] = "fail"
        else:
            hook_values[hook_id] = detail["status"]
    if first.get("status") == "pass":
        mcp = _mcp_evidence(
            [message for message in first.get("messages", []) + second.get("messages", []) if isinstance(message, dict)]
        )
    else:
        mcp = _check("unavailable", "MCP no se evaluó porque la fase de lectura no terminó satisfactoriamente")
    after_snapshot = codex_preservation.capture_snapshot(root, tuple(fixture_paths))
    preservation = _preservation_result(root, before_snapshot, after_snapshot, fixture_paths)
    preservation = _append_preservation_leftovers(preservation, root, fixture_paths)
    after_git = _git_status(root)
    git_check = _git_evidence(before_git, after_git)
    statuses = [
        first["status"],
        second["status"],
        mcp["status"],
        git_check["status"],
        preservation["status"],
        *hook_values.values(),
    ]
    if second.get("phase_state") == "completed" and second.get("status") == "pass" and not fixture_changed:
        statuses.append("fail")
    status = _aggregate_statuses(statuses)
    app_version = first.get("version") or second.get("version") or version
    base.update(
        {
            "status": status,
            "version": app_version,
            "protocol_version": first.get("protocol_version") or second.get("protocol_version"),
            "hooks": hook_values,
            "mcp": mcp["status"],
            "git_diff": git_check["status"],
            "preservation": preservation,
            "phases": _phase_summary(first, second),
            "fixture_changed": fixture_changed,
            "argv": [
                turn["argv"]
                for turn in (first, second)
                if isinstance(turn.get("argv"), list)
            ],
            "detail": "; ".join(
                part
                for part in (
                    first.get("detail"),
                    second.get("detail"),
                    mcp.get("detail"),
                    None if git_check["status"] == "pass" else git_check.get("detail"),
                    (
                        "el host no produjo la edición fixture esperada"
                        if second.get("phase_state") == "completed"
                        and second.get("status") == "pass"
                        and not fixture_changed
                        else None
                    ),
                )
                if part
            ),
        }
    )
    return base


def _structural_checks(root: Path, checks: dict[str, dict[str, Any]]) -> tuple[dict[str, Any] | None, dict[str, Any] | None, dict[str, Any] | None]:
    agents = root / "AGENTS.md"
    manifest = root / ".agent" / "workflows" / "manifest.json"
    skills = root / ".agents" / "skills"
    checks["agents"] = _check("pass" if agents.is_file() and agents.read_text(encoding="utf-8", errors="ignore") else "fail", "AGENTS.md leído")
    checks["workflow_discovery"] = _check("pass" if manifest.is_file() else "fail", "manifest de workflows descubierto")
    source_skills = sorted(skills.glob("source-command-*/SKILL.md")) if skills.is_dir() else []
    skills_valid = bool(skills.is_dir()) and all(_skill_frontmatter_valid(path) for path in source_skills)
    checks["skill_discovery"] = _check(
        "pass" if skills_valid else "fail",
        "skills Codex descubiertas y frontmatter válido" if skills_valid else "superficie .agents/skills ausente o con SKILL.md inválido",
    )
    config = _load_config(root)
    if config is None:
        checks["hooks_structure"] = _check("unavailable", "no existe .codex/hooks.json; ejecutar el sincronizador")
    else:
        hook_map = config.get("hooks")
        if not isinstance(hook_map, dict):
            checks["hooks_structure"] = _check("fail", ".codex/hooks.json.hooks debe ser un objeto")
            checks["hook_event_compatibility"] = _check("fail", "no se pudo leer el mapa de eventos Codex")
        else:
            missing = sorted(
                hook_id
                for hook_id, event_name in HOOK_EVENTS.items()
                if _find_hook(config, hook_id, event_name) is None
            )
            checks["hooks_structure"] = _check(
                "pass" if not missing else "fail",
                "hooks gestionados presentes" if not missing else f"hooks ausentes: {', '.join(missing)}",
            )
            supported_events = set(HOOK_EVENTS.values())
            unexpected = sorted(
                event_name
                for event_name, groups in hook_map.items()
                if isinstance(groups, list)
                and any("[FW-CODEX:" in json.dumps(group, ensure_ascii=False) for group in groups)
                and event_name not in supported_events
            )
            checks["hook_event_compatibility"] = _check(
                "pass" if not unexpected else "fail",
                "eventos Codex compatibles con el runner" if not unexpected else f"eventos no soportados por el runner: {', '.join(unexpected)}",
            )
    native_path = root / ".codex" / "config.toml"
    native: dict[str, Any] | None = None
    native_config_available = native_path.is_file()
    native_state_valid = False
    if not native_config_available:
        checks["codex_config"] = _check("unavailable", "no existe .codex/config.toml; ejecutar ensure_codex_integration.py --native-only")
    else:
        try:
            import tomllib

            native = tomllib.loads(native_path.read_text(encoding="utf-8"))
            state_manager = (native.get("mcp_servers") or {}).get("state_manager")
            native_state_valid = isinstance(state_manager, dict) and state_manager.get("command") == "python3" and state_manager.get("args") == [".agent/scripts/state_manager.py"]
            checks["codex_config"] = _check("pass" if native_state_valid else "fail", "MCP nativo state_manager declarado" if native_state_valid else "MCP nativo state_manager ausente o alterado")
        except ImportError:
            # FW-238: sin `tomllib` (Python < 3.11) no se puede leer; el TOML no tiene la culpa.
            checks["codex_config"] = _check(
                "fail", "no se pudo leer .codex/config.toml: falta tomllib, que requiere Python 3.11 o superior"
            )
        except (OSError, ValueError) as exc:
            checks["codex_config"] = _check("fail", f".codex/config.toml ilegible: {exc}")
    checks["state_manager"] = _check(
        checks["codex_config"]["status"],
        "state_manager nativo listo" if native_state_valid else checks["codex_config"]["detail"],
    )
    mirror = root / ".mcp.json"
    if not mirror.exists():
        checks["mcp_mirror"] = _check("unavailable", ".mcp.json ausente; el espejo es opcional para Codex-native")
    else:
        try:
            mirror_data = json.loads(mirror.read_text(encoding="utf-8"))
            server = (mirror_data.get("mcpServers") or {}).get("state_manager") if isinstance(mirror_data, dict) else None
            mirror_valid = isinstance(server, dict) and server.get("command") == "python3" and server.get("args") == [".agent/scripts/state_manager.py"]
            checks["mcp_mirror"] = _check("pass" if mirror_valid else "fail", "espejo .mcp.json válido" if mirror_valid else "espejo .mcp.json ausente o alterado; no afecta Codex-native")
        except (OSError, json.JSONDecodeError) as exc:
            checks["mcp_mirror"] = _check("fail", f"espejo .mcp.json ilegible; no afecta Codex-native: {exc}")
    _, policy_check = _load_policy(root)
    checks["codex_policy"] = policy_check
    return config, (native if native_config_available and native_state_valid else None), policy_check


def _non_codex_result(
    root: Path,
    checks: dict[str, dict[str, Any]],
    strict: bool,
    selected: list[str],
    required: list[str],
    record_profile: bool,
) -> dict[str, Any]:
    checks["harness"] = _check("unavailable", "el harness principal declarado no es Codex")
    checks["hooks_structure"] = _check("unavailable", "hooks Codex no aplican al harness principal declarado")
    checks["state_manager"] = _check("unavailable", "MCP nativo Codex no aplica al harness principal declarado")
    checks["codex_config"] = _check("unavailable", "configuración nativa Codex no aplica al harness principal declarado")
    checks["hooks_runtime"] = _check("unavailable", "hooks Codex no se ejecutan fuera del harness principal Codex")
    checks["codex_host_dispatch"] = _check("unavailable", "dispatch Codex no solicitado para este harness")
    surfaces = {
        "cli": {"status": "unavailable", "version": None, "hooks": {hook_id: "unavailable" for hook_id in HOOK_IDS}, "mcp": "unavailable", "detail": "harness principal no es Codex"},
        "desktop": {"status": "unavailable", "transport": "app-server", "version": None, "hooks": {}, "mcp": "unavailable", "detail": "harness principal no es Codex"},
    }
    if record_profile:
        checks["profile_record"] = _check("unavailable", "no se modifica el perfil de un proyecto cuyo harness no es Codex")
    checks["claude_preservation"] = _check("pass", "superficies Claude/OpenCode sin cambios")
    blocking = [name for name, item in checks.items() if item["status"] == "fail"]
    blocking.extend(
        f"surface:{surface}"
        for surface in required
        if strict and surfaces[surface]["status"] == "unavailable"
    )
    return {
        "ok": not blocking,
        "strict": strict,
        "root": str(root),
        "surface": "all" if selected == list(SURFACES) else selected[0],
        "required_surfaces": required,
        "structural_support": _structural_support_status(checks),
        "runtime_smoke": aggregate_surface_status(surfaces, required),
        "checks": checks,
        "surfaces": surfaces,
        "blocking": blocking,
    }


def run_smoke(
    root: Path = ROOT,
    *,
    strict: bool = False,
    codex_bin: str | None = None,
    host: bool | None = None,
    record_profile: bool = False,
    model: str | None = None,
    reasoning_effort: str | None = None,
    surface: str | None = None,
    required_surfaces: str | Iterable[str] | None = None,
) -> dict[str, Any]:
    root = Path(root).resolve()
    selected, required = _normalize_surfaces(surface, required_surfaces, host)
    checks: dict[str, dict[str, Any]] = {}
    before = _protected_hashes(root)
    primary_harness = codex_profile.detect_primary_harness(root)
    agents = root / "AGENTS.md"
    manifest = root / ".agent" / "workflows" / "manifest.json"
    skills = root / ".agents" / "skills"
    checks["agents"] = _check("pass" if agents.is_file() and agents.read_text(encoding="utf-8", errors="ignore") else "fail", "AGENTS.md leído")
    checks["workflow_discovery"] = _check("pass" if manifest.is_file() else "fail", "manifest de workflows descubierto")
    source_skills = sorted(skills.glob("source-command-*/SKILL.md")) if skills.is_dir() else []
    skills_valid = bool(skills.is_dir()) and all(_skill_frontmatter_valid(path) for path in source_skills)
    checks["skill_discovery"] = _check("pass" if skills_valid else "fail", "skills Codex descubiertas y frontmatter válido" if skills_valid else "superficie .agents/skills ausente o con SKILL.md inválido")
    if primary_harness["status"] == "other":
        return _non_codex_result(root, checks, strict, selected, required, record_profile)

    config, native, _ = _structural_checks(root, checks)
    binary = _codex_binary(codex_bin)
    global_before_snapshot = (
        codex_preservation.capture_snapshot(root) if binary is not None else None
    )
    version_check: dict[str, Any]
    if binary is None:
        version_check = _check("unavailable", "CLI Codex no disponible en este entorno")
    else:
        version_check = _version_probe(binary, root)
    checks["codex_cli"] = version_check
    if binary is not None and native is not None:
        try:
            mcp_list = subprocess.run([binary, "mcp", "list"], cwd=root, capture_output=True, text=True, timeout=30, check=False)
            visible = "state_manager" in (mcp_list.stdout + mcp_list.stderr)
            checks["codex_mcp_read"] = _check("pass" if mcp_list.returncode == 0 and visible else "fail", "Codex lista state_manager" if mcp_list.returncode == 0 and visible else (mcp_list.stderr.strip() or "Codex no listó state_manager"))
        except (OSError, subprocess.SubprocessError) as exc:
            checks["codex_mcp_read"] = _check("fail", f"no se pudo leer la lista MCP Codex: {exc}")
    else:
        checks["codex_mcp_read"] = _check("unavailable", "no hay CLI/configuración nativa para consultar la lista MCP")

    preflight_surfaces: dict[str, dict[str, Any]] = {}
    if global_before_snapshot is not None:
        global_after_preflight = codex_preservation.capture_snapshot(root)
        checks["preservation"] = _preservation_result(
            root, global_before_snapshot, global_after_preflight
        )
        if checks["preservation"]["status"] != "pass":
            # A preflight mutation or incomplete inventory is already a global
            # result; do not launch either host surface afterwards.
            for surface_name in selected:
                if surface_name == "cli":
                    surfaces_placeholder = {
                        "status": checks["preservation"]["status"],
                        "selected": True,
                        "version": version_check.get("version"),
                        "hooks": {hook_id: "unavailable" for hook_id in HOOK_IDS},
                        "mcp": "unavailable",
                        "detail": "host detenido por la preservación global",
                        "preservation": checks["preservation"],
                    }
                else:
                    surfaces_placeholder = {
                        "status": "unavailable",
                        "selected": True,
                        "transport": "app-server",
                        "version": None,
                        "protocol_version": None,
                        "hooks": {},
                        "mcp": "unavailable",
                        "detail": "host detenido por la preservación global",
                        "preservation": checks["preservation"],
                    }
                preflight_surfaces[surface_name] = surfaces_placeholder

    execute_cli = "cli" in selected
    execute_desktop = "desktop" in selected
    surfaces: dict[str, dict[str, Any]] = {
        "cli": {"status": "unavailable", "selected": execute_cli, "version": version_check.get("version") if execute_cli else None, "hooks": {hook_id: "unavailable" for hook_id in HOOK_IDS}, "mcp": "unavailable", "detail": "prueba CLI no solicitada", "preservation": _check("unavailable", "prueba CLI no solicitada")},
        "desktop": {"status": "unavailable", "selected": execute_desktop, "transport": "app-server", "version": None, "protocol_version": None, "hooks": {}, "mcp": "unavailable", "detail": "prueba Desktop no solicitada", "preservation": _check("unavailable", "prueba Desktop no solicitada")},
    }
    surfaces.update(preflight_surfaces)
    if execute_cli and not preflight_surfaces:
        if config is None or native is None:
            surfaces["cli"] = {"status": "unavailable", "selected": True, "version": version_check.get("version"), "hooks": {hook_id: "unavailable" for hook_id in HOOK_IDS}, "mcp": "unavailable", "detail": "faltan hooks/configuración nativa para CLI", "preservation": _check("unavailable", "no se ejecutó el host CLI")}
        elif binary is None:
            surfaces["cli"] = {"status": "unavailable", "selected": True, "version": None, "hooks": {hook_id: "unavailable" for hook_id in HOOK_IDS}, "mcp": "unavailable", "detail": "CLI Codex no disponible para el dispatch real", "preservation": _check("unavailable", "no se ejecutó el host CLI")}
        else:
            surfaces["cli"] = _run_cli_surface(binary, root, model, reasoning_effort, version_check.get("version"))
            surfaces["cli"]["selected"] = True
    if execute_desktop and not preflight_surfaces:
        cli_preservation_status = str(surfaces["cli"].get("preservation", {}).get("status", "pass")) if execute_cli else "pass"
        if execute_cli and cli_preservation_status in {"fail", "unavailable"}:
            surfaces["desktop"] = {"status": "unavailable", "selected": True, "transport": "app-server", "version": None, "protocol_version": None, "hooks": {}, "mcp": "unavailable", "detail": "superficie Desktop detenida por la preservación CLI", "preservation": _check("unavailable", "no se ejecutó por daño o imposibilidad de preservación CLI")}
        elif config is None or native is None:
            surfaces["desktop"] = {"status": "unavailable", "selected": True, "transport": "app-server", "version": None, "protocol_version": None, "hooks": {}, "mcp": "unavailable", "detail": "faltan hooks/configuración nativa para app-server", "preservation": _check("unavailable", "no se ejecutó el host Desktop/app-server")}
        elif binary is None:
            surfaces["desktop"] = {"status": "unavailable", "selected": True, "transport": "app-server", "version": None, "protocol_version": None, "hooks": {}, "mcp": "unavailable", "detail": "CLI Codex no disponible para app-server", "preservation": _check("unavailable", "no se ejecutó el host Desktop/app-server")}
        else:
            surfaces["desktop"] = _run_desktop_surface(binary, root, model, reasoning_effort, None)
            surfaces["desktop"]["selected"] = True

    cli_hook_status = _aggregate_statuses(surfaces["cli"].get("hooks", {}).values())
    checks["hooks_runtime"] = _check(cli_hook_status, surfaces["cli"].get("detail", "hooks CLI"))
    checks["codex_host_dispatch"] = _check(surfaces["cli"]["status"], surfaces["cli"].get("detail", "dispatch CLI"))
    checks["desktop_app_server"] = _check(surfaces["desktop"]["status"], surfaces["desktop"].get("detail", "dispatch Desktop"))
    result: dict[str, Any] = {
        "ok": False,
        "strict": strict,
        "root": str(root),
        "surface": "all" if selected == list(SURFACES) else selected[0],
        "required_surfaces": required,
        "model": model,
        "reasoning_effort": reasoning_effort,
        "structural_support": _structural_support_status(checks),
        "runtime_smoke": aggregate_surface_status(surfaces, required),
        "checks": checks,
        "surfaces": surfaces,
        "blocking": [],
    }
    after = _protected_hashes(root)
    checks["claude_preservation"] = _check("pass" if before == after else "fail", "superficies Claude/OpenCode sin cambios" if before == after else "cambió una superficie protegida")
    decision = evaluate_result(checks, surfaces, required, strict)
    result["runtime_smoke"] = decision["runtime_smoke"]
    result["blocking"] = decision["blocking"]
    result["ok"] = decision["ok"]
    if record_profile:
        checks["profile_record"] = _record_environment_profile(root, result)
        decision = evaluate_result(checks, surfaces, required, strict)
        result["runtime_smoke"] = decision["runtime_smoke"]
        result["blocking"] = decision["blocking"]
        result["ok"] = decision["ok"]
    result["structural_support"] = _structural_support_status(checks)
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Smoke runtime de Codex CLI/Desktop")
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--codex-bin")
    parser.add_argument("--surface", choices=("cli", "desktop", "all"), default=None)
    parser.add_argument("--required-surfaces", help="lista separada por comas: cli,desktop")
    parser.add_argument("--model", help="modelo explícito de la certificación")
    parser.add_argument("--reasoning-effort", choices=REASONING_EFFORTS, help="esfuerzo explícito de la certificación")
    parser.add_argument("--strict", action="store_true")
    parser.add_argument("--host", action="store_true", help="alias compatible de --surface cli")
    parser.add_argument("--record-profile", action="store_true", help="registrar evidencia en _project/15_environment_profile.md")
    parser.add_argument("--json", action="store_true", dest="as_json")
    args = parser.parse_args(argv)
    try:
        result = run_smoke(
            args.root,
            strict=args.strict,
            codex_bin=args.codex_bin,
            host=args.host or None,
            record_profile=args.record_profile,
            model=args.model,
            reasoning_effort=args.reasoning_effort,
            surface=args.surface,
            required_surfaces=args.required_surfaces,
        )
    except ValueError as exc:
        parser.error(str(exc))
        return 2
    if args.as_json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"Codex runtime smoke: {'OK' if result['ok'] else 'FAIL'}")
        for name, check in result["checks"].items():
            print(f"- {name}: {check['status']} — {check['detail']}")
        for surface_name, surface_result in result["surfaces"].items():
            print(f"- surface:{surface_name}: {surface_result['status']} — {surface_result.get('detail', '')}")
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
