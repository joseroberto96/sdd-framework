#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Pruebas de la integración Codex aislada de Claude Code.

Cada prueba nombra una regresión concreta que debe capturar:
- hooks gestionados que eliminan una entrada personalizada;
- hooks que solo funcionan desde la raíz;
- guardia destructivo que deja de bloquear en Codex;
- state_manager que pisa MCPs del usuario;
- doctor que exige Codex en proyectos cuyo harness es Claude.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import tomllib
import unittest
from unittest.mock import patch
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / ".agent" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import ensure_codex_integration  # noqa: E402
import codex_profile  # noqa: E402
import codex_runtime_smoke  # noqa: E402
import framework_doctor  # noqa: E402
import propagate_framework  # noqa: E402
import sync_claude_commands  # noqa: E402
import sync_codex_hooks  # noqa: E402
import turn_close_hook  # noqa: E402


class CodexProfileTests(unittest.TestCase):
    def test_detects_legacy_and_generated_profile_labels(self):
        self.assertEqual(
            codex_profile.detect_primary_harness_text("- Herramienta: Codex\n")["status"],
            "codex",
        )
        self.assertEqual(
            codex_profile.detect_primary_harness_text("**Entorno:** Codex\n")["status"],
            "codex",
        )
        self.assertEqual(
            codex_profile.detect_primary_harness_text("Entorno principal: Codex\n")["status"],
            "codex",
        )
        self.assertEqual(
            codex_profile.detect_primary_harness_text("- Herramienta: Codex desktop.\n")["status"],
            "codex",
        )

    def test_ignores_placeholders_and_reports_conflicts(self):
        missing = codex_profile.detect_primary_harness_text("**Entorno:** [entorno declarado]\n")
        self.assertEqual(missing["status"], "missing")
        conflict = codex_profile.detect_primary_harness_text(
            "Herramienta: Claude Code\nEntorno: Codex\n"
        )
        self.assertEqual(conflict["status"], "conflict")

    def test_from_profile_does_not_silently_skip_a_missing_profile(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(SystemExit):
                ensure_codex_integration.ensure_codex_integration(
                    Path(tmp), from_profile=True
                )

    def test_without_harness_selector_is_a_safe_noop(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = ensure_codex_integration.ensure_codex_integration(Path(tmp))
            self.assertEqual(result["status"], "skipped")
            self.assertFalse((Path(tmp) / ".codex").exists())

    def test_codex_artifacts_are_visible_when_profile_is_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".codex").mkdir()
            (root / ".codex" / "hooks.json").write_text("{}\n", encoding="utf-8")
            result = codex_profile.detect_primary_harness(root)
            self.assertEqual(result["status"], "missing")
            self.assertTrue(result["artifacts_present"])


class CodexPropagationTests(unittest.TestCase):
    def test_unverified_smoke_values_remain_doctor_safe_placeholders(self):
        """La propagación no debe sembrar un esfuerzo que el smoke no verificó."""
        smoke_payload = {
            "structural_support": "unavailable",
            "runtime_smoke": "unavailable",
            "model": None,
            "reasoning_effort": None,
            "required_surfaces": ["cli"],
            "surfaces": {
                "cli": {"status": "unavailable", "version": None},
                "desktop": {"status": "unavailable", "version": None},
            },
        }
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(
                propagate_framework.subprocess,
                "run",
                side_effect=[
                    subprocess.CompletedProcess(
                        ["codex_runtime_smoke.py"],
                        1,
                        stdout=json.dumps(smoke_payload),
                        stderr="",
                    ),
                    subprocess.CompletedProcess(
                        ["codex", "--version"],
                        1,
                        stdout="",
                        stderr="",
                    ),
                ],
            ):
                values = propagate_framework._probe_codex_values(Path(tmp))

        self.assertEqual(values["Modelo Codex smoke"], "[no ejecutado]")
        self.assertEqual(values["Esfuerzo Codex smoke"], "[no ejecutado]")


class CodexOnlySyncTests(unittest.TestCase):
    def test_codex_only_sync_does_not_touch_shared_surfaces(self):
        original = {
            name: getattr(sync_claude_commands, name)
            for name in ("ROOT", "MANIFEST", "CODEX_SKILLS", "CLAUDE_COMMANDS", "OPENCODE_COMMANDS", "OPENCODE_AGENTS", "OPENCODE_SKILLS")
        }
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            try:
                sync_claude_commands.ROOT = root
                sync_claude_commands.MANIFEST = root / ".agent" / "workflows" / "manifest.json"
                sync_claude_commands.CODEX_SKILLS = root / ".agents" / "skills"
                sync_claude_commands.CLAUDE_COMMANDS = root / ".claude" / "commands"
                sync_claude_commands.OPENCODE_COMMANDS = root / ".opencode" / "commands"
                sync_claude_commands.OPENCODE_AGENTS = root / ".opencode" / "agents"
                sync_claude_commands.OPENCODE_SKILLS = root / ".opencode" / "skills"
                source = root / ".agent" / "workflows" / "arrancar.md"
                source.parent.mkdir(parents=True)
                source.write_text("# arrancar\n", encoding="utf-8")
                shared = {
                    root / ".claude" / "settings.json": "{\"keep\": true}\n",
                    root / ".claude" / "commands" / "custom.md": "custom\n",
                    root / ".opencode" / "commands" / "custom.md": "custom\n",
                    root / ".opencode" / "agents" / "custom.md": "custom\n",
                    root / "AGENTS.md": "agents\n",
                    root / "CLAUDE.md": "claude\n",
                }
                for path, content in shared.items():
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_text(content, encoding="utf-8")
                before = {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in shared}
                count = sync_claude_commands.sync_codex_only(
                    {"commands": [{"name": "arrancar", "source": ".agent/workflows/arrancar.md", "description": "Usar cuando: iniciar", "claude_command": True}]}
                )
                self.assertEqual(count, 1)
                self.assertTrue((root / ".agents" / "skills" / "source-command-arrancar" / "SKILL.md").exists())
                after = {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in shared}
                self.assertEqual(before, after)
            finally:
                for name, value in original.items():
                    setattr(sync_claude_commands, name, value)

    def test_check_is_non_mutating_and_detects_drift(self):
        original = {
            name: getattr(sync_claude_commands, name)
            for name in ("ROOT", "MANIFEST", "CODEX_SKILLS", "CLAUDE_COMMANDS", "OPENCODE_COMMANDS", "OPENCODE_AGENTS", "OPENCODE_SKILLS")
        }
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            try:
                sync_claude_commands.ROOT = root
                sync_claude_commands.MANIFEST = root / ".agent" / "workflows" / "manifest.json"
                sync_claude_commands.CODEX_SKILLS = root / ".agents" / "skills"
                sync_claude_commands.CLAUDE_COMMANDS = root / ".claude" / "commands"
                sync_claude_commands.OPENCODE_COMMANDS = root / ".opencode" / "commands"
                sync_claude_commands.OPENCODE_AGENTS = root / ".opencode" / "agents"
                sync_claude_commands.OPENCODE_SKILLS = root / ".opencode" / "skills"
                source = root / ".agent" / "workflows" / "arrancar.md"
                source.parent.mkdir(parents=True)
                source.write_text("# arrancar\n", encoding="utf-8")
                manifest = {
                    "commands": [
                        {
                            "name": "arrancar",
                            "source": ".agent/workflows/arrancar.md",
                            "description": "Usar cuando: iniciar",
                            "claude_command": True,
                        }
                    ]
                }
                sync_claude_commands.MANIFEST.write_text(
                    json.dumps(manifest), encoding="utf-8"
                )
                sync_claude_commands.sync_codex_only(manifest)
                wrapper = root / ".agents" / "skills" / "source-command-arrancar" / "SKILL.md"
                before = wrapper.read_bytes()
                sync_claude_commands.main(["--codex-only", "--check"])
                self.assertEqual(wrapper.read_bytes(), before)
                wrapper.write_text(wrapper.read_text(encoding="utf-8") + "drift\n", encoding="utf-8")
                with self.assertRaises(SystemExit) as raised:
                    sync_claude_commands.main(["--codex-only", "--check"])
                self.assertEqual(raised.exception.code, 1)
            finally:
                for name, value in original.items():
                    setattr(sync_claude_commands, name, value)


def _copy_codex_runtime(root: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    scripts = root / ".agent" / "scripts"
    templates = root / ".agent" / "templates"
    adapters = root / ".agent" / "adapters"
    scripts.mkdir(parents=True)
    templates.mkdir(parents=True)
    adapters.mkdir(parents=True)
    for name in ("codex_hook_runner.py", "guard_destructive_command.py", "post_edit_format.sh"):
        shutil.copy2(SCRIPTS / name, scripts / name)
    shutil.copy2(ROOT / ".agent" / "templates" / "codex_hooks.json.base", templates / "codex_hooks.json.base")
    shutil.copy2(ROOT / ".agent" / "templates" / "codex_config.toml.base", templates / "codex_config.toml.base")
    shutil.copy2(ROOT / ".agent" / "adapters" / "codex_compatibility.json", adapters / "codex_compatibility.json")
    (root / "nested").mkdir()


def _write_fake_cli(root: Path, *, include_mcp: bool = True, edit_fixture: bool = True) -> Path:
    """Host CLI mínimo: eventos estructurados, hooks append-only y fixture aislado."""
    fake = root / "fake-codex"
    mcp_events = (
        "  echo '{\"type\":\"mcpToolCall\",\"id\":\"mcp1\",\"server\":\"state_manager\",\"tool\":\"get_work_context\",\"arguments\":{\"view\":\"startup\"},\"status\":\"inProgress\"}'\n"
        "  echo '{\"type\":\"mcpToolCall\",\"id\":\"mcp1\",\"server\":\"state_manager\",\"tool\":\"get_work_context\",\"arguments\":{\"view\":\"startup\"},\"status\":\"completed\",\"result\":{\"content\":[{\"type\":\"text\",\"text\":\"{}\"}]},\"error\":null}'\n"
        if include_mcp
        else "  echo '{\"type\":\"message\",\"text\":\"state_manager.get_session_brief\"}'\n"
    )
    fixture_edit = (
        "  if [ -n \"${CODEX_FRAMEWORK_SMOKE_FIXTURE_FILE:-}\" ]; then printf '%s\\n' CODEX_FIXTURE_EDIT_OK >> \"$CODEX_FRAMEWORK_SMOKE_FIXTURE_FILE\"; fi\n"
        if edit_fixture
        else ""
    )
    fake.write_text(
        "#!/bin/sh\n"
        "if [ \"$1\" = \"--version\" ]; then echo codex-cli 0.142.3; exit 0; fi\n"
        "if [ \"$1\" = \"mcp\" ] && [ \"$2\" = \"list\" ]; then echo state_manager; exit 0; fi\n"
        "if [ \"$1\" = \"exec\" ]; then\n"
        "  run_id=\"${CODEX_FRAMEWORK_SMOKE_RUN_ID:-unknown}\"\n"
        "  for hook in guard state format stop; do\n"
        "    printf '{\"schema_version\":1,\"run_id\":\"%s\",\"hook_id\":\"%s\",\"phase\":\"started\",\"status\":\"pass\"}\\n' \"$run_id\" \"$hook\" >> \"$CODEX_FRAMEWORK_SMOKE_MARKER\"\n"
        "    printf '{\"schema_version\":1,\"run_id\":\"%s\",\"hook_id\":\"%s\",\"phase\":\"completed\",\"status\":\"pass\"}\\n' \"$run_id\" \"$hook\" >> \"$CODEX_FRAMEWORK_SMOKE_MARKER\"\n"
        "  done\n"
        + fixture_edit
        + "  if [ -n \"${CODEX_FRAMEWORK_SMOKE_MUTATE_PATH:-}\" ]; then printf '%s\\n' CODEX_UNEXPECTED_EDIT >> \"$CODEX_FRAMEWORK_SMOKE_MUTATE_PATH\"; fi\n"
        + mcp_events
        + "  echo '{\"type\":\"message\",\"text\":\"CODEX_FRAMEWORK_SMOKE_OK\"}'\n"
        "  exit 0\n"
        "fi\n"
        "exit 1\n",
        encoding="utf-8",
    )
    fake.chmod(0o755)
    return fake


def _prepare_codex_smoke_root(root: Path) -> None:
    _copy_codex_runtime(root)
    (root / "AGENTS.md").write_text("Framework v4\n", encoding="utf-8")
    (root / ".agent" / "workflows").mkdir(parents=True, exist_ok=True)
    (root / ".agent" / "workflows" / "manifest.json").write_text('{"commands": []}\n', encoding="utf-8")
    skill = root / ".agents" / "skills" / "source-command-test"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text(
        '---\nname: source-command-test\ndescription: prueba\n---\n\n'
        '<!-- GENERATED: framework wrapper;\n',
        encoding="utf-8",
    )
    (root / "_project").mkdir()
    (root / "_project" / "15_environment_profile.md").write_text("- Herramienta: Codex\n", encoding="utf-8")
    ensure_codex_integration.ensure_codex_integration(root, "Codex", native_only=True)


def _hook_entry(config: dict, event: str, hook_id: str) -> dict:
    for group in config["hooks"][event]:
        for hook in group.get("hooks", []):
            if f"--hook-id {hook_id}" in hook.get("command", ""):
                return hook
    raise AssertionError(f"No existe hook {event}/{hook_id}")


class CodexHookSyncTests(unittest.TestCase):
    def test_sync_preserves_custom_hooks_and_migrates_legacy_entries(self):
        """Un sync Codex no borra hooks custom y reemplaza la configuración heredada."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _copy_codex_runtime(root)
            codex_dir = root / ".codex"
            codex_dir.mkdir()
            (codex_dir / "hooks.json").write_text(
                json.dumps(
                    {
                        "hooks": {
                            "PreToolUse": [
                                {
                                    "matcher": "Bash",
                                    "hooks": [
                                        {
                                            "type": "command",
                                            "command": "python3 .agent/scripts/guard_destructive_command.py",
                                        },
                                        {"type": "command", "command": "echo custom-pre"},
                                    ],
                                }
                            ],
                            "PostToolUse": [
                                {
                                    "matcher": "Write|Edit",
                                    "hooks": [
                                        {
                                            "type": "command",
                                            "command": "input=$(cat); echo \"$input\" | grep -q '03_project_state\\.json' && echo '{\"systemMessage\": \"project_state.json actualizado. Si acabas de cerrar un módulo, ejecuta /cierre-modulo.\"}' || true",
                                        },
                                        {"type": "command", "command": "echo custom-post"},
                                        {"type": "command", "command": "python3 .agent/scripts/codex_hook_runner.py --hook-id custom"},
                                        {"type": "command", "command": "python3 .agent/scripts/guard_destructive_command.py --audit"},
                                    ],
                                }
                            ],
                            "SessionStart": [
                                {"hooks": [{"type": "command", "command": "echo custom-session"}]}
                            ],
                        }
                    }
                ),
                encoding="utf-8",
            )

            result = sync_codex_hooks.sync_hooks(root)
            self.assertTrue(result["changed"])
            config = json.loads((codex_dir / "hooks.json").read_text(encoding="utf-8"))
            blob = json.dumps(config, ensure_ascii=False)
            self.assertIn("echo custom-pre", blob)
            self.assertIn("echo custom-post", blob)
            self.assertIn("--hook-id custom", blob)
            self.assertIn("--audit", blob)
            self.assertIn("echo custom-session", blob)
            self.assertIn("[FW-CODEX:guard]", blob)
            self.assertIn("[FW-CODEX:state]", blob)
            self.assertIn("[FW-CODEX:format]", blob)
            self.assertIn("[FW-CODEX:stop]", blob)
            self.assertNotIn("guard_destructive_command.py\"", blob)
            self.assertFalse(sync_codex_hooks.sync_hooks(root, check=True)["changed"])

    def test_check_mode_does_not_create_or_modify_configuration(self):
        """El modo check solo informa drift; nunca crea .codex/hooks.json."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _copy_codex_runtime(root)
            result = sync_codex_hooks.sync_hooks(root, check=True)
            self.assertTrue(result["changed"])
            self.assertFalse((root / ".codex" / "hooks.json").exists())


class CodexHookRunnerTests(unittest.TestCase):
    def _run(self, root: Path, hook_id: str, event: dict) -> subprocess.CompletedProcess:
        return subprocess.run(
            [
                sys.executable,
                str(SCRIPTS / "codex_hook_runner.py"),
                "--hook-id",
                hook_id,
            ],
            cwd=root / "nested",
            input=json.dumps(event),
            capture_output=True,
            text=True,
        )

    def test_runner_reuses_guard_and_works_from_subdirectory(self):
        """El hook Codex conserva el deny del guardia aun lanzado desde nested/."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _copy_codex_runtime(root)
            safe = self._run(root, "guard", {"cwd": str(root), "tool_name": "Bash", "tool_input": {"command": "git status"}})
            self.assertEqual(safe.returncode, 0, safe.stderr)
            self.assertEqual(safe.stdout.strip(), "")
            blocked = self._run(
                root,
                "guard",
                {"cwd": str(root), "tool_name": "Bash", "tool_input": {"command": "rm -rf /Users/x"}},
            )
            self.assertEqual(blocked.returncode, 0, blocked.stderr)
            self.assertEqual(json.loads(blocked.stdout)["hookSpecificOutput"]["permissionDecision"], "deny")

    def test_runner_deniega_si_el_guard_no_termina(self):
        """FW-237: el runner llamaba al guard sin `timeout`; un análisis que no
        termina colgaba el hook de Codex. Ahora deniega con motivo."""
        import io

        import codex_hook_runner

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            scripts = root / ".agent" / "scripts"
            scripts.mkdir(parents=True)
            (scripts / "guard_destructive_command.py").write_text("import time\ntime.sleep(10)\n", encoding="utf-8")
            stdout = io.StringIO()
            with patch.object(codex_hook_runner, "GUARD_TIMEOUT_S", 0.5, create=True), \
                    patch.object(sys, "stdout", stdout):
                inicio = __import__("time").monotonic()
                codex_hook_runner._run_guard(root, json.dumps({"tool_name": "Bash", "tool_input": {"command": "echo hola"}}))
                transcurrido = __import__("time").monotonic() - inicio
            self.assertLess(transcurrido, 5.0)
            salida = json.loads(stdout.getvalue())
            self.assertEqual(salida["hookSpecificOutput"]["permissionDecision"], "deny")

    def test_runner_deniega_si_el_guard_muere_sin_veredicto(self):
        """Revisión 2026-09-15 (O3): un guard que revienta al importar sale con
        código 1 y sin salida; eso no es un "no destructivo"."""
        import io

        import codex_hook_runner

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            scripts = root / ".agent" / "scripts"
            scripts.mkdir(parents=True)
            (scripts / "guard_destructive_command.py").write_text("raise SystemExit(1)\n", encoding="utf-8")
            stdout = io.StringIO()
            with patch.object(sys, "stdout", stdout):
                codex_hook_runner._run_guard(root, json.dumps({"tool_name": "Bash", "tool_input": {"command": "echo hola"}}))
            self.assertEqual(json.loads(stdout.getvalue())["hookSpecificOutput"]["permissionDecision"], "deny")

    def test_runner_emits_state_and_stop_messages_in_spanish(self):
        """Los eventos PostToolUse/Stop producen recordatorios observables sin escribir."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _copy_codex_runtime(root)
            state = self._run(
                root,
                "state",
                {
                    "cwd": str(root),
                    "tool_name": "Write",
                    "tool_input": {"file_path": str(root / "_project" / "03_project_state.json")},
                },
            )
            self.assertEqual(state.returncode, 0, state.stderr)
            self.assertIn("project_state.json actualizado", json.loads(state.stdout)["systemMessage"])
            stop = self._run(root, "stop", {"cwd": str(root)})
            self.assertEqual(stop.returncode, 0, stop.stderr)
            self.assertIn("Sesión terminada", json.loads(stop.stdout)["systemMessage"])

    def test_runner_handles_apply_patch_file_headers(self):
        """Codex reporta apply_patch mediante tool_input.command, no file_path."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _copy_codex_runtime(root)
            state = self._run(
                root,
                "state",
                {
                    "cwd": str(root),
                    "tool_name": "apply_patch",
                    "tool_input": {"command": "*** Begin Patch\n*** Update File: _project/03_project_state.json\n"},
                },
            )
            self.assertIn("project_state.json actualizado", json.loads(state.stdout)["systemMessage"])
            target = root / "nested" / "apply-patch.md"
            target.write_text("apply patch\n", encoding="utf-8")
            formatted = self._run(
                root,
                "format",
                {
                    "cwd": str(root),
                    "tool_name": "apply_patch",
                    "tool_input": {"command": "*** Begin Patch\n*** Update File: nested/apply-patch.md\n"},
                },
            )
            self.assertEqual(formatted.returncode, 0, formatted.stderr)

    def test_generated_command_bootstraps_from_nested_directory(self):
        """La orden generada encuentra el runner usando cwd/event.cwd, no una ruta relativa frágil."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _copy_codex_runtime(root)
            sync_codex_hooks.sync_hooks(root)
            config = json.loads((root / ".codex" / "hooks.json").read_text(encoding="utf-8"))
            hook = _hook_entry(config, "Stop", "stop")
            result = subprocess.run(
                hook["command"],
                cwd=root / "nested",
                input=json.dumps({"cwd": str(root / "nested")}),
                capture_output=True,
                text=True,
                shell=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("Sesión terminada", json.loads(result.stdout)["systemMessage"])

    def test_generated_command_is_safe_when_runner_is_missing(self):
        """El bootstrap no bloquea ni lanza traceback si el proyecto perdió el runner."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _copy_codex_runtime(root)
            sync_codex_hooks.sync_hooks(root)
            command = _hook_entry(json.loads((root / ".codex" / "hooks.json").read_text(encoding="utf-8")), "Stop", "stop")["command"]
            (root / ".agent" / "scripts" / "codex_hook_runner.py").unlink()
            result = subprocess.run(
                command,
                cwd=root / "nested",
                input=json.dumps({"cwd": str(root / "nested")}),
                capture_output=True,
                text=True,
                shell=True,
            )
            self.assertEqual(result.returncode, 0)
            self.assertIn("no se pudo resolver codex_hook_runner.py", result.stderr)

    def test_smoke_marker_is_append_only_and_structured(self):
        """Dos hooks consecutivos conservan evidencia separable de una ejecución."""
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as home, \
                patch.dict(os.environ, {"HOME": home}):
            root = Path(tmp)
            _copy_codex_runtime(root)
            marker = turn_close_hook.telemetry_dir(root) / "smoke" / "hooks.jsonl"
            env = os.environ.copy()
            env["CODEX_FRAMEWORK_SMOKE_MARKER"] = str(marker)
            env["CODEX_FRAMEWORK_SMOKE_RUN_ID"] = "run-test-1"
            for hook_id, event in (
                ("guard", {"cwd": str(root), "tool_name": "Bash", "tool_input": {"command": "git status"}}),
                ("stop", {"cwd": str(root)}),
            ):
                result = subprocess.run(
                    [sys.executable, str(SCRIPTS / "codex_hook_runner.py"), "--hook-id", hook_id],
                    cwd=root / "nested",
                    input=json.dumps(event),
                    capture_output=True,
                    text=True,
                    env=env,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
            records = [json.loads(line) for line in marker.read_text(encoding="utf-8").splitlines()]
            self.assertGreaterEqual(len(records), 4)
            self.assertEqual({record["run_id"] for record in records}, {"run-test-1"})
            self.assertEqual({record["hook_id"] for record in records}, {"guard", "stop"})
            self.assertTrue(all(record["schema_version"] == 1 for record in records))

    def test_missing_hook_events_never_become_pass(self):
        statuses = codex_runtime_smoke._hook_statuses([], None)
        self.assertEqual(
            {hook_id: detail["status"] for hook_id, detail in statuses.items()},
            {hook_id: "unavailable" for hook_id in codex_runtime_smoke.HOOK_IDS},
        )

    def test_incomplete_hook_phases_never_become_pass(self):
        records = [
            {
                "schema_version": 1,
                "run_id": "run-test-1",
                "hook_id": "guard",
                "phase": "completed",
                "status": "pass",
            }
        ]
        statuses = codex_runtime_smoke._hook_statuses(records, None)
        self.assertEqual(statuses["guard"]["status"], "unavailable")


class CodexStateIntegrationTests(unittest.TestCase):
    def test_claude_harness_is_a_true_noop(self):
        """Claude no recibe archivos Codex ni un MCP obligatorio por este adaptador."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            result = ensure_codex_integration.ensure_codex_integration(root, "Claude Code")
            self.assertEqual(result["status"], "skipped")
            self.assertFalse((root / ".mcp.json").exists())
            self.assertFalse((root / ".codex").exists())

    def test_codex_adds_state_manager_without_touching_remote_mcps(self):
        """La integración obligatoria añade solo state_manager y preserva MCPs custom."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _copy_codex_runtime(root)
            original = {
                "mcpServers": {"remote_custom": {"command": "custom", "args": ["--pinned"]}},
                "custom": {"keep": True},
            }
            (root / ".mcp.json").write_text(json.dumps(original), encoding="utf-8")
            (root / ".codex").mkdir()
            (root / ".codex" / "config.toml").write_text(
                '[mcp_servers.remote_custom]\ncommand = "custom"\nargs = ["--pinned"]\n',
                encoding="utf-8",
            )
            result = ensure_codex_integration.ensure_codex_integration(root, "Codex")
            self.assertEqual(result["status"], "configured")
            data = json.loads((root / ".mcp.json").read_text(encoding="utf-8"))
            self.assertEqual(data["mcpServers"]["remote_custom"], original["mcpServers"]["remote_custom"])
            self.assertEqual(data["custom"], original["custom"])
            self.assertEqual(data["mcpServers"]["state_manager"], {"command": "python3", "args": [".agent/scripts/state_manager.py"]})
            native = tomllib.loads((root / ".codex" / "config.toml").read_text(encoding="utf-8"))
            self.assertEqual(native["mcp_servers"]["remote_custom"], {"command": "custom", "args": ["--pinned"]})
            self.assertEqual(native["mcp_servers"]["state_manager"], {"command": "python3", "args": [".agent/scripts/state_manager.py"], "default_tools_approval_mode": "writes"})
            self.assertEqual(ensure_codex_integration.ensure_codex_integration(root, "Claude Code")["status"], "skipped")

    def test_native_only_does_not_read_or_write_mcp_mirror(self):
        """Codex-native puede configurarse sin crear ni parsear .mcp.json."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _copy_codex_runtime(root)
            mirror = root / ".mcp.json"
            mirror.write_text("not json and must remain untouched\n", encoding="utf-8")
            result = ensure_codex_integration.ensure_codex_integration(
                root, "Codex", native_only=True
            )
            self.assertEqual(result["status"], "configured")
            self.assertTrue(result["mirror_skipped"])
            self.assertEqual(mirror.read_text(encoding="utf-8"), "not json and must remain untouched\n")
            self.assertTrue((root / ".codex" / "config.toml").is_file())


class CodexDoctorContractTests(unittest.TestCase):
    def test_doctor_reads_required_surfaces_without_turning_unavailable_into_pass(self):
        original_root = framework_doctor.ROOT
        with tempfile.TemporaryDirectory() as tmp:
            try:
                root = Path(tmp)
                _copy_codex_runtime(root)
                ensure_codex_integration.ensure_codex_integration(root, "Codex", native_only=True)
                (root / "_project").mkdir()
                (root / "_project" / "15_environment_profile.md").write_text(
                    "- Herramienta: Codex\n"
                    "- Soporte estructural Codex: pass\n"
                    "- Smoke runtime Codex: unavailable\n"
                    "- Versión de Codex: no disponible\n"
                    "- Hooks Codex: gestionados\n"
                    "- MCP local `state_manager` (Codex `.codex/config.toml` native; `.mcp.json` espejo opcional): conectado\n"
                    "- Diferencias Codex conocidas: ninguna\n"
                    "- Superficies Codex requeridas: cli,desktop\n"
                    "- Smoke runtime Codex CLI: pass\n"
                    "- Smoke runtime Codex Desktop/app-server: unavailable\n"
                    "- Versión de Codex CLI: codex-cli 0.142.3\n"
                    "- Versión de Codex Desktop/app-server: no disponible\n"
                    "- Modelo Codex smoke: gpt-5.5\n"
                    "- Esfuerzo Codex smoke: xhigh\n",
                    encoding="utf-8",
                )
                framework_doctor.ROOT = root
                errors: list[str] = []
                warnings: list[str] = []
                framework_doctor.validate_codex_integration("initialized", {}, errors, warnings)
                self.assertEqual(errors, [], errors)
                self.assertTrue(any("unavailable" in warning for warning in warnings), warnings)
            finally:
                framework_doctor.ROOT = original_root

    def test_doctor_rejects_generated_wrapper_with_invalid_frontmatter(self):
        original_root = framework_doctor.ROOT
        with tempfile.TemporaryDirectory() as tmp:
            try:
                root = Path(tmp)
                manifest = {
                    "commands": [
                        {
                            "name": "arrancar",
                            "source": ".agent/workflows/arrancar.md",
                            "claude_command": True,
                        }
                    ]
                }
                skill = root / ".agents" / "skills" / "source-command-arrancar"
                skill.mkdir(parents=True)
                (skill / "SKILL.md").write_text(
                    "---\nname: wrong-name\ndescription: \"\"\n---\n\n"
                    "<!-- GENERATED: framework wrapper; edit manifest/source, then run sync_claude_commands.py -->\n",
                    encoding="utf-8",
                )
                framework_doctor.ROOT = root
                errors: list[str] = []
                warnings: list[str] = []
                framework_doctor.validate_codex_wrappers(manifest, errors, warnings)
                self.assertTrue(any("name inconsistente" in error for error in errors), errors)
                self.assertTrue(any("description utilizable" in error for error in errors), errors)
            finally:
                framework_doctor.ROOT = original_root

    def test_claude_project_does_not_require_codex_files(self):
        """Declarar Claude no activa ningún gate obligatorio de Codex."""
        original_root = framework_doctor.ROOT
        with tempfile.TemporaryDirectory() as tmp:
            try:
                root = Path(tmp)
                (root / "_project").mkdir()
                (root / "_project" / "15_environment_profile.md").write_text(
                    "- Herramienta: Claude Code\n", encoding="utf-8"
                )
                framework_doctor.ROOT = root
                errors: list[str] = []
                warnings: list[str] = []
                framework_doctor.validate_codex_integration("initialized", {}, errors, warnings)
                self.assertEqual(errors, [])
            finally:
                framework_doctor.ROOT = original_root

    def test_codex_project_requires_hooks_and_state_manager(self):
        """Un proyecto Codex incompleto falla de forma explícita y accionable."""
        original_root = framework_doctor.ROOT
        with tempfile.TemporaryDirectory() as tmp:
            try:
                root = Path(tmp)
                (root / "_project").mkdir()
                (root / "_project" / "15_environment_profile.md").write_text(
                    "- Herramienta: Codex\n"
                    "- Soporte estructural Codex: pass\n"
                    "- Smoke runtime Codex: unavailable\n"
                    "- Versión de Codex: no disponible\n"
                    "- Hooks Codex: gestionados\n"
                    "- MCP local `state_manager` (Codex `.codex/config.toml` / Claude `.mcp.json`): conectado\n"
                    "- Diferencias Codex conocidas: ninguna\n",
                    encoding="utf-8",
                )
                framework_doctor.ROOT = root
                errors: list[str] = []
                warnings: list[str] = []
                framework_doctor.validate_codex_integration("initialized", {}, errors, warnings)
                self.assertTrue(any(".codex/hooks.json" in error for error in errors), errors)
                self.assertTrue(any("state_manager" in error for error in errors), errors)
            finally:
                framework_doctor.ROOT = original_root

    def test_codex_project_accepts_structural_support_with_unavailable_smoke(self):
        """El smoke ausente es informativo; la integración estructural sí debe estar completa."""
        original_root = framework_doctor.ROOT
        with tempfile.TemporaryDirectory() as tmp:
            try:
                root = Path(tmp)
                _copy_codex_runtime(root)
                ensure_codex_integration.ensure_codex_integration(root, "Codex")
                (root / "_project").mkdir()
                (root / "_project" / "15_environment_profile.md").write_text(
                    "- Herramienta: Codex\n"
                    "- Soporte estructural Codex: pass\n"
                    "- Smoke runtime Codex: unavailable\n"
                    "- Versión de Codex: no disponible\n"
                    "- Hooks Codex: gestionados\n"
                    "- MCP local `state_manager` (Codex `.codex/config.toml` / Claude `.mcp.json`): conectado\n"
                    "- Diferencias Codex conocidas: ninguna\n",
                    encoding="utf-8",
                )
                framework_doctor.ROOT = root
                errors: list[str] = []
                warnings: list[str] = []
                framework_doctor.validate_codex_integration("initialized", {}, errors, warnings)
                self.assertEqual(errors, [], errors)
            finally:
                framework_doctor.ROOT = original_root

    def test_doctor_reads_environment_profile_format_generated_by_workflow(self):
        """El doctor reconoce el perfil real con etiquetas Markdown y aliases de campo."""
        original_root = framework_doctor.ROOT
        with tempfile.TemporaryDirectory() as tmp:
            try:
                root = Path(tmp)
                _copy_codex_runtime(root)
                ensure_codex_integration.ensure_codex_integration(root, "Codex")
                (root / "_project").mkdir()
                (root / "_project" / "15_environment_profile.md").write_text(
                    "**Entorno:** Codex\n"
                    "- **Soporte estructural Codex (`structural_support`):** pass\n"
                    "- **Smoke runtime Codex (`runtime_smoke`):** unavailable\n"
                    "- **Versión de Codex:** no disponible\n"
                    "- **Hooks Codex:** gestionados\n"
                    "- **MCP local `state_manager` (Codex `.codex/config.toml` / Claude `.mcp.json`):** conectado\n"
                    "- **Diferencias Codex conocidas:** ninguna\n",
                    encoding="utf-8",
                )
                framework_doctor.ROOT = root
                errors: list[str] = []
                warnings: list[str] = []
                framework_doctor.validate_codex_integration("initialized", {}, errors, warnings)
                self.assertEqual(errors, [], errors)
            finally:
                framework_doctor.ROOT = original_root

    def test_runtime_pass_without_structural_pass_is_rejected(self):
        """No se puede certificar runtime si la validación estructural falló."""
        original_root = framework_doctor.ROOT
        with tempfile.TemporaryDirectory() as tmp:
            try:
                root = Path(tmp)
                _copy_codex_runtime(root)
                ensure_codex_integration.ensure_codex_integration(root, "Codex")
                (root / "_project").mkdir()
                (root / "_project" / "15_environment_profile.md").write_text(
                    "- Herramienta: Codex\n"
                    "- Soporte estructural Codex: fail\n"
                    "- Smoke runtime Codex: pass\n",
                    encoding="utf-8",
                )
                framework_doctor.ROOT = root
                errors: list[str] = []
                warnings: list[str] = []
                framework_doctor.validate_codex_integration("initialized", {}, errors, warnings)
                self.assertTrue(any("runtime" in error.lower() and "estructural" in error.lower() for error in errors), errors)
            finally:
                framework_doctor.ROOT = original_root

    def test_doctor_suppresses_codex_warning_in_template_mode(self):
        """BUG-07: En template mode con artefactos Codex presentes pero perfil neutral, no advertir."""
        original_root = framework_doctor.ROOT
        with tempfile.TemporaryDirectory() as tmp:
            try:
                root = Path(tmp)
                (root / ".codex").mkdir()
                (root / ".codex" / "hooks.json").write_text("{}", encoding="utf-8")
                framework_doctor.ROOT = root
                errors: list[str] = []
                warnings: list[str] = []
                # En template mode: cero warnings
                framework_doctor.validate_codex_integration("template", {}, errors, warnings)
                self.assertEqual(warnings, [])
                self.assertEqual(errors, [])
                # En initialized mode: debe emitir el warning
                framework_doctor.validate_codex_integration("initialized", {}, errors, warnings)
                self.assertTrue(any("hay configuración Codex" in w for w in warnings))
            finally:
                framework_doctor.ROOT = original_root


class CodexDocumentationContractTests(unittest.TestCase):
    def test_codex_wrapper_body_is_spanish_without_changing_claude_text(self):
        """La mejora de ruteo Codex no debe alterar el cuerpo Claude."""
        import sync_claude_commands

        codex = sync_claude_commands.codex_wrapper_text("arrancar", "Usar cuando: iniciar", ".agent/workflows/arrancar.md")
        claude = sync_claude_commands.claude_wrapper_text("arrancar", "Usar cuando: iniciar", ".agent/workflows/arrancar.md")
        self.assertIn("Usar esta skill", codex)
        self.assertNotIn("Use this skill", codex)
        self.assertIn("Ejecuta el artefacto canónico", claude)


class ClaudeSurfacePreservationTests(unittest.TestCase):
    def test_codex_sync_does_not_touch_claude_or_opencode_surfaces(self):
        """Sincronizar Codex no modifica archivos protegidos de Claude/OpenCode."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _copy_codex_runtime(root)
            protected_files = {
                root / ".claude" / "settings.json": '{"hooks": {}}\n',
                root / ".claude" / "settings.local.json": '{"permissions": []}\n',
                root / ".claude" / "commands" / "custom.md": "custom Claude command\n",
                root / ".opencode" / "agents" / "custom.md": "custom OpenCode agent\n",
            }
            for path, content in protected_files.items():
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(content, encoding="utf-8")
            protected = list(protected_files)
            before = {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in protected}
            guard_event = json.dumps(
                {
                    "tool_name": "Bash",
                    "tool_input": {"command": "rm -rf /Users/x"},
                }
            )
            guard_path = root / ".agent" / "scripts" / "guard_destructive_command.py"
            guard_before = subprocess.run(
                [sys.executable, str(guard_path)],
                cwd=root,
                input=guard_event,
                capture_output=True,
                text=True,
            )
            sync_codex_hooks.sync_hooks(root)
            ensure_codex_integration.ensure_codex_integration(root, "Codex")
            after = {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in protected}
            self.assertEqual(before, after)
            guard_after = subprocess.run(
                [sys.executable, str(guard_path)],
                cwd=root,
                input=guard_event,
                capture_output=True,
                text=True,
            )
            self.assertEqual(
                (guard_before.returncode, guard_before.stdout, guard_before.stderr),
                (guard_after.returncode, guard_after.stdout, guard_after.stderr),
            )


class CodexRuntimeSmokeTests(unittest.TestCase):
    def setUp(self):
        # El smoke crea su marcador bajo HOME (carpeta de telemetría del proyecto).
        home = tempfile.TemporaryDirectory()
        self.addCleanup(home.cleanup)
        patcher = patch.dict(os.environ, {"HOME": home.name})
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_profile_record_preserves_the_other_surface_and_does_not_touch_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            profile = root / "_project" / "15_environment_profile.md"
            profile.parent.mkdir()
            profile.write_text("- Herramienta: Codex\n", encoding="utf-8")
            state = root / "_project" / "03_project_state.json"
            state.write_text('{"framework_mode": "initialized"}\n', encoding="utf-8")
            state_log = root / "_project" / "state_log.jsonl"
            state_log.write_text("sentinel\n", encoding="utf-8")
            cli_hooks = {hook_id: "pass" for hook_id in codex_runtime_smoke.HOOK_IDS}
            first = {
                "surface": "cli",
                "required_surfaces": ["cli"],
                "structural_support": "pass",
                "runtime_smoke": "pass",
                "model": "gpt-5.5",
                "reasoning_effort": "xhigh",
                "surfaces": {
                    "cli": {"status": "pass", "version": "codex-cli 0.142.3", "hooks": cli_hooks, "mcp": "pass"},
                    "desktop": {"status": "unavailable", "version": None, "hooks": {}, "mcp": "unavailable"},
                },
            }
            self.assertEqual(codex_runtime_smoke._record_environment_profile(root, first)["status"], "pass")
            before_state = (state.read_bytes(), state_log.read_bytes())
            second = {
                "surface": "desktop",
                "required_surfaces": ["desktop"],
                "structural_support": "pass",
                "runtime_smoke": "pass",
                "model": "gpt-5.5",
                "reasoning_effort": "xhigh",
                "surfaces": {
                    "cli": {"status": "unavailable", "version": None, "hooks": {hook_id: "unavailable" for hook_id in codex_runtime_smoke.HOOK_IDS}, "mcp": "unavailable"},
                    "desktop": {"status": "pass", "version": "Codex Desktop/0.142.3", "hooks": {}, "mcp": "pass"},
                },
            }
            self.assertEqual(codex_runtime_smoke._record_environment_profile(root, second)["status"], "pass")
            text = profile.read_text(encoding="utf-8")
            self.assertIn("Smoke runtime Codex CLI: pass", text)
            self.assertIn("Smoke runtime Codex Desktop/app-server: pass", text)
            self.assertEqual((state.read_bytes(), state_log.read_bytes()), before_state)

    def test_informational_smoke_is_non_blocking_for_non_codex_harness(self):
        """Antigravity no recibe una falsa falla por no tener config nativa Codex."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "_project").mkdir()
            profile = root / "_project" / "15_environment_profile.md"
            profile.write_text("- Herramienta: Antigravity\n", encoding="utf-8")
            (root / "AGENTS.md").write_text("Framework v4\n", encoding="utf-8")
            (root / ".agent" / "workflows").mkdir(parents=True)
            (root / ".agent" / "workflows" / "manifest.json").write_text("{}\n", encoding="utf-8")
            (root / ".agents" / "skills").mkdir(parents=True)
            before = profile.read_bytes()
            result = codex_runtime_smoke.run_smoke(root, codex_bin="/definitely/missing/codex")
            self.assertTrue(result["ok"], result)
            self.assertEqual(result["checks"]["harness"]["status"], "unavailable")
            self.assertEqual(result["runtime_smoke"], "unavailable")
            self.assertEqual(result["blocking"], [])
            self.assertEqual(profile.read_bytes(), before)

    def test_missing_codex_cli_is_informational_by_default(self):
        """Sin CLI disponible el smoke informa unavailable y no bloquea el modo normal."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _copy_codex_runtime(root)
            (root / "AGENTS.md").write_text("Framework v4\n", encoding="utf-8")
            (root / ".agent" / "workflows").mkdir(parents=True, exist_ok=True)
            (root / ".agent" / "workflows" / "manifest.json").write_text("{\"commands\": []}\n", encoding="utf-8")
            (root / ".agents" / "skills" / "source-command-test").mkdir(parents=True)
            (root / ".agents" / "skills" / "source-command-test" / "SKILL.md").write_text(
                '---\nname: source-command-test\ndescription: prueba\n---\n\n<!-- GENERATED: framework wrapper;\n',
                encoding="utf-8",
            )
            (root / "_project").mkdir(exist_ok=True)
            (root / "_project" / "15_environment_profile.md").write_text(
                "- Herramienta: Codex\n"
                "- Soporte estructural Codex: unavailable\n"
                "- Smoke runtime Codex: unavailable\n"
                "- Versión de Codex: [versión o no disponible]\n"
                "- Hooks Codex: [gestionados | manuales | no disponibles]\n"
                "- MCP local `state_manager` (Codex `.codex/config.toml` / Claude `.mcp.json`): [conectado | fallback | no disponible]\n"
                "- Diferencias Codex conocidas: [lista o \"ninguna\"]\n",
                encoding="utf-8",
            )
            ensure_codex_integration.ensure_codex_integration(root, "Codex")
            result = codex_runtime_smoke.run_smoke(root, codex_bin="/definitely/missing/codex")
            self.assertTrue(result["ok"], result)
            self.assertEqual(result["checks"]["codex_cli"]["status"], "unavailable")
            self.assertEqual(result["runtime_smoke"], "unavailable")

    def test_strict_mode_rejects_missing_codex_cli(self):
        """La certificación estricta no puede declarar runtime verificado sin Codex."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _copy_codex_runtime(root)
            (root / "AGENTS.md").write_text("Framework v4\n", encoding="utf-8")
            (root / ".agent" / "workflows").mkdir(parents=True, exist_ok=True)
            (root / ".agent" / "workflows" / "manifest.json").write_text("{\"commands\": []}\n", encoding="utf-8")
            (root / ".agents" / "skills" / "source-command-test").mkdir(parents=True)
            (root / ".agents" / "skills" / "source-command-test" / "SKILL.md").write_text(
                '---\nname: source-command-test\ndescription: prueba\n---\n\n<!-- GENERATED: framework wrapper;\n',
                encoding="utf-8",
            )
            ensure_codex_integration.ensure_codex_integration(root, "Codex")
            result = codex_runtime_smoke.run_smoke(root, codex_bin="/definitely/missing/codex", strict=True)
            self.assertFalse(result["ok"])

    def test_smoke_reads_native_codex_mcp_configuration(self):
        """Con CLI disponible, el smoke comprueba la lista MCP nativa de Codex."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _copy_codex_runtime(root)
            (root / "AGENTS.md").write_text("Framework v4\n", encoding="utf-8")
            (root / ".agent" / "workflows").mkdir(parents=True, exist_ok=True)
            (root / ".agent" / "workflows" / "manifest.json").write_text('{"commands": []}\n', encoding="utf-8")
            (root / ".agents" / "skills" / "source-command-test").mkdir(parents=True)
            (root / ".agents" / "skills" / "source-command-test" / "SKILL.md").write_text(
                '---\nname: source-command-test\ndescription: prueba\n---\n\n<!-- GENERATED: framework wrapper;\n',
                encoding="utf-8",
            )
            ensure_codex_integration.ensure_codex_integration(root, "Codex")
            fake_codex = root / "fake-codex"
            fake_codex.write_text(
                "#!/bin/sh\n"
                "if [ \"$1\" = \"--version\" ]; then echo codex-cli test; exit 0; fi\n"
                "if [ \"$1\" = \"mcp\" ] && [ \"$2\" = \"list\" ]; then echo state_manager; exit 0; fi\n"
                "exit 1\n",
                encoding="utf-8",
            )
            fake_codex.chmod(0o755)
            result = codex_runtime_smoke.run_smoke(root, codex_bin=str(fake_codex))
            self.assertTrue(result["ok"], result)
            self.assertEqual(result["checks"]["codex_config"]["status"], "pass")
            self.assertEqual(result["checks"]["codex_mcp_read"]["status"], "pass")

    def test_malformed_hooks_configuration_is_observable_not_a_smoke_crash(self):
        """Una configuración corrupta devuelve fail accionable en vez de levantar una excepción."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _copy_codex_runtime(root)
            (root / "AGENTS.md").write_text("Framework v4\n", encoding="utf-8")
            (root / ".agent" / "workflows").mkdir(parents=True, exist_ok=True)
            (root / ".agent" / "workflows" / "manifest.json").write_text('{"commands": []}\n', encoding="utf-8")
            (root / ".agents" / "skills" / "source-command-test").mkdir(parents=True)
            (root / ".agents" / "skills" / "source-command-test" / "SKILL.md").write_text(
                '---\nname: source-command-test\ndescription: prueba\n---\n\n<!-- GENERATED: framework wrapper;\n',
                encoding="utf-8",
            )
            (root / ".codex").mkdir()
            (root / ".codex" / "hooks.json").write_text('{"hooks": []}\n', encoding="utf-8")
            result = codex_runtime_smoke.run_smoke(root, codex_bin="/definitely/missing/codex")
            self.assertFalse(result["ok"])
            self.assertEqual(result["checks"]["hooks_structure"]["status"], "fail")

    def test_smoke_rejects_invalid_skill_frontmatter(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _copy_codex_runtime(root)
            (root / "AGENTS.md").write_text("Framework v4\n", encoding="utf-8")
            (root / ".agent" / "workflows").mkdir(parents=True, exist_ok=True)
            (root / ".agent" / "workflows" / "manifest.json").write_text('{"commands": []}\n', encoding="utf-8")
            skill = root / ".agents" / "skills" / "source-command-test"
            skill.mkdir(parents=True)
            (skill / "SKILL.md").write_text("skill\n", encoding="utf-8")
            ensure_codex_integration.ensure_codex_integration(root, "Codex")
            result = codex_runtime_smoke.run_smoke(root, codex_bin="/definitely/missing/codex")
            self.assertFalse(result["ok"])
            self.assertEqual(result["checks"]["skill_discovery"]["status"], "fail")

    def test_strict_smoke_requires_real_host_hook_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _copy_codex_runtime(root)
            (root / "AGENTS.md").write_text("Framework v4\n", encoding="utf-8")
            (root / ".agent" / "workflows").mkdir(parents=True, exist_ok=True)
            (root / ".agent" / "workflows" / "manifest.json").write_text('{"commands": []}\n', encoding="utf-8")
            skill = root / ".agents" / "skills" / "source-command-test"
            skill.mkdir(parents=True)
            (skill / "SKILL.md").write_text(
                '---\nname: source-command-test\ndescription: prueba\n---\n\n<!-- GENERATED: framework wrapper;\n',
                encoding="utf-8",
            )
            (root / "_project").mkdir(exist_ok=True)
            (root / "_project" / "15_environment_profile.md").write_text(
                "- Herramienta: Codex\n"
                "- Soporte estructural Codex: unavailable\n"
                "- Smoke runtime Codex: unavailable\n"
                "- Versión de Codex: no disponible\n"
                "- Hooks Codex: no disponibles\n"
                "- MCP local `state_manager` (Codex `.codex/config.toml` / Claude `.mcp.json`): no disponible\n"
                "- Diferencias Codex conocidas: ninguna\n",
                encoding="utf-8",
            )
            ensure_codex_integration.ensure_codex_integration(root, "Codex")
            fake = _write_fake_cli(root)
            result = codex_runtime_smoke.run_smoke(
                root,
                codex_bin=str(fake),
                strict=True,
                record_profile=True,
                model="gpt-5.5",
                reasoning_effort="xhigh",
            )
            self.assertTrue(result["ok"], result)
            self.assertEqual(result["checks"]["codex_host_dispatch"]["status"], "pass")
            self.assertEqual(result["structural_support"], "pass")
            self.assertEqual(result["runtime_smoke"], "pass")
            argv = json.dumps(result["surfaces"]["cli"]["argv"])
            self.assertIn("read-only", argv)
            self.assertNotIn("--dangerously-bypass-hook-trust", argv)
            self.assertNotIn("--ask-for-approval", argv)
            profile = (root / "_project" / "15_environment_profile.md").read_text(encoding="utf-8")
            self.assertIn("Smoke runtime Codex: pass", profile)

    def test_smoke_detects_content_change_in_preexisting_dirty_file(self):
        """La preservación por contenido detecta un cambio aunque Git ya estuviera dirty."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _copy_codex_runtime(root)
            (root / "AGENTS.md").write_text("Framework v4\n", encoding="utf-8")
            (root / ".agent" / "workflows").mkdir(parents=True, exist_ok=True)
            (root / ".agent" / "workflows" / "manifest.json").write_text('{"commands": []}\n', encoding="utf-8")
            skill = root / ".agents" / "skills" / "source-command-test"
            skill.mkdir(parents=True)
            (skill / "SKILL.md").write_text(
                '---\nname: source-command-test\ndescription: prueba\n---\n\n'
                '<!-- GENERATED: framework wrapper;\n',
                encoding="utf-8",
            )
            (root / "_project").mkdir()
            (root / "_project" / "15_environment_profile.md").write_text("- Herramienta: Codex\n", encoding="utf-8")
            ensure_codex_integration.ensure_codex_integration(root, "Codex", native_only=True)
            dirty = root / "dirty.txt"
            dirty.write_text("before\n", encoding="utf-8")
            fake = _write_fake_cli(root)
            with patch.dict(os.environ, {"CODEX_FRAMEWORK_SMOKE_MUTATE_PATH": str(dirty)}):
                result = codex_runtime_smoke.run_smoke(
                    root,
                    codex_bin=str(fake),
                    surface="cli",
                    required_surfaces=["cli"],
                    strict=True,
                    model="gpt-5.5",
                    reasoning_effort="xhigh",
                )
            self.assertFalse(result["ok"], result)
            self.assertEqual(result["surfaces"]["cli"]["status"], "fail")
            self.assertEqual(result["surfaces"]["cli"]["preservation"]["status"], "fail")
            self.assertIn("dirty.txt", result["surfaces"]["cli"]["preservation"]["changed_paths"])

    def test_record_profile_uses_preservation_verdict(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _prepare_codex_smoke_root(root)
            dirty = root / "dirty.txt"
            dirty.write_text("before\n", encoding="utf-8")
            fake = _write_fake_cli(root)
            with patch.dict(os.environ, {"CODEX_FRAMEWORK_SMOKE_MUTATE_PATH": str(dirty)}):
                result = codex_runtime_smoke.run_smoke(
                    root,
                    codex_bin=str(fake),
                    surface="cli",
                    required_surfaces=["cli"],
                    strict=False,
                    record_profile=True,
                    model="gpt-5.5",
                    reasoning_effort="xhigh",
                )

            self.assertFalse(result["ok"], result)
            profile = (root / "_project" / "15_environment_profile.md").read_text(encoding="utf-8")
            self.assertIn("Smoke runtime Codex: fail", profile)

    def test_completed_fixture_phase_without_edit_is_an_observed_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _prepare_codex_smoke_root(root)
            fake = _write_fake_cli(root, edit_fixture=False)

            result = codex_runtime_smoke.run_smoke(
                root,
                codex_bin=str(fake),
                surface="cli",
                required_surfaces=["cli"],
                strict=True,
                model="gpt-5.5",
                reasoning_effort="xhigh",
            )

            self.assertFalse(result["ok"], result)
            self.assertEqual(result["surfaces"]["cli"]["status"], "fail")
            self.assertEqual(result["surfaces"]["cli"]["phases"]["fixture_edit"]["state"], "completed")
            self.assertIn("edición fixture esperada", result["surfaces"]["cli"]["detail"])

    def test_optional_desktop_failure_remains_visible_without_blocking_required_cli(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _prepare_codex_smoke_root(root)
            fake = _write_fake_cli(root)

            result = codex_runtime_smoke.run_smoke(
                root,
                codex_bin=str(fake),
                surface="all",
                required_surfaces=["cli"],
                strict=True,
                model="gpt-5.5",
                reasoning_effort="xhigh",
            )

            self.assertTrue(result["ok"], result)
            self.assertEqual(result["runtime_smoke"], "pass", result)
            self.assertEqual(result["surfaces"]["cli"]["status"], "pass")
            self.assertEqual(result["surfaces"]["desktop"]["status"], "fail")
            self.assertEqual(result["blocking"], [], result)

    def test_strict_smoke_forwards_explicit_model_without_trust_bypass(self):
        """La certificación puede desacoplar versión de CLI y modelo global."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _copy_codex_runtime(root)
            (root / "AGENTS.md").write_text("Framework v4\n", encoding="utf-8")
            (root / ".agent" / "workflows").mkdir(parents=True, exist_ok=True)
            (root / ".agent" / "workflows" / "manifest.json").write_text('{"commands": []}\n', encoding="utf-8")
            skill = root / ".agents" / "skills" / "source-command-test"
            skill.mkdir(parents=True)
            (skill / "SKILL.md").write_text(
                '---\nname: source-command-test\ndescription: prueba\n---\n\n<!-- GENERATED: framework wrapper;\n',
                encoding="utf-8",
            )
            (root / "_project").mkdir(exist_ok=True)
            (root / "_project" / "15_environment_profile.md").write_text(
                "- Herramienta: Codex\n"
                "- Soporte estructural Codex: unavailable\n"
                "- Smoke runtime Codex: unavailable\n"
                "- Versión de Codex: no disponible\n"
                "- Hooks Codex: no disponibles\n"
                "- MCP local `state_manager` (Codex `.codex/config.toml` / Claude `.mcp.json`): no disponible\n"
                "- Diferencias Codex conocidas: ninguna\n",
                encoding="utf-8",
            )
            ensure_codex_integration.ensure_codex_integration(root, "Codex")
            fake = _write_fake_cli(root)
            result = codex_runtime_smoke.run_smoke(
                root,
                codex_bin=str(fake),
                strict=True,
                record_profile=True,
                model="gpt-5.5",
                reasoning_effort="xhigh",
            )
            self.assertTrue(result["ok"], result)
            argv = json.dumps(result["surfaces"]["cli"]["argv"])
            self.assertIn("--model", argv)
            self.assertIn("gpt-5.5", argv)
            self.assertNotIn("--dangerously-bypass-hook-trust", argv)

    def test_surface_status_aggregation_is_independent(self):
        surfaces = {
            "cli": {"status": "pass"},
            "desktop": {"status": "unavailable"},
        }
        self.assertEqual(
            codex_runtime_smoke.aggregate_surface_status(surfaces, ["cli"]),
            "pass",
        )
        self.assertEqual(
            codex_runtime_smoke.aggregate_surface_status(surfaces, ["cli", "desktop"]),
            "unavailable",
        )
        surfaces["desktop"] = {"status": "fail"}
        self.assertEqual(
            codex_runtime_smoke.aggregate_surface_status(surfaces, ["cli", "desktop"]),
            "fail",
        )

    def test_host_flag_is_compatible_alias_for_cli_surface(self):
        selected, required = codex_runtime_smoke._normalize_surfaces(None, None, True)
        self.assertEqual(selected, ["cli"])
        self.assertEqual(required, ["cli"])

    def test_cli_runtime_fails_when_mcp_is_only_visible_not_invocable(self):
        """La lista estática MCP no puede certificar una llamada real."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _copy_codex_runtime(root)
            (root / "AGENTS.md").write_text("Framework v4\n", encoding="utf-8")
            (root / ".agent" / "workflows").mkdir(parents=True, exist_ok=True)
            (root / ".agent" / "workflows" / "manifest.json").write_text(
                '{"commands": []}\n', encoding="utf-8"
            )
            skill = root / ".agents" / "skills" / "source-command-test"
            skill.mkdir(parents=True)
            (skill / "SKILL.md").write_text(
                '---\nname: source-command-test\ndescription: prueba\n---\n\n'
                '<!-- GENERATED: framework wrapper;\n',
                encoding="utf-8",
            )
            (root / "_project").mkdir()
            (root / "_project" / "15_environment_profile.md").write_text(
                "- Herramienta: Codex\n", encoding="utf-8"
            )
            ensure_codex_integration.ensure_codex_integration(root, "Codex", native_only=True)
            fake = _write_fake_cli(root, include_mcp=False)
            result = codex_runtime_smoke.run_smoke(
                root,
                codex_bin=str(fake),
                surface="cli",
                required_surfaces=["cli"],
                strict=True,
                model="gpt-5.5",
                reasoning_effort="xhigh",
            )
            self.assertFalse(result["ok"], result)
            self.assertEqual(result["surfaces"]["cli"]["mcp"], "fail")
            self.assertEqual(result["runtime_smoke"], "fail")

    def test_desktop_app_server_fixture_negotiates_and_observes_real_events(self):
        """El gate Desktop usa el wire app-server, no una terminal CLI simulada."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _copy_codex_runtime(root)
            (root / "AGENTS.md").write_text("Framework v4\n", encoding="utf-8")
            (root / ".agent" / "workflows").mkdir(parents=True, exist_ok=True)
            (root / ".agent" / "workflows" / "manifest.json").write_text(
                '{"commands": []}\n', encoding="utf-8"
            )
            skill = root / ".agents" / "skills" / "source-command-test"
            skill.mkdir(parents=True)
            (skill / "SKILL.md").write_text(
                '---\nname: source-command-test\ndescription: prueba\n---\n\n'
                '<!-- GENERATED: framework wrapper;\n',
                encoding="utf-8",
            )
            (root / "_project").mkdir()
            (root / "_project" / "15_environment_profile.md").write_text(
                "- Herramienta: Codex\n", encoding="utf-8"
            )
            ensure_codex_integration.ensure_codex_integration(root, "Codex", native_only=True)
            fake = root / "fake-codex-app-server"
            fake.write_text(
                "#!/usr/bin/env python3\n"
                "import json, os, sys\n"
                "args = sys.argv[1:]\n"
                "if args == ['--version']:\n"
                "    print('codex-cli 0.142.3')\n"
                "    raise SystemExit(0)\n"
                "if args[:2] == ['mcp', 'list']:\n"
                "    print('state_manager')\n"
                "    raise SystemExit(0)\n"
                "def send(value):\n"
                "    print(json.dumps(value), flush=True)\n"
                "def marker(hook):\n"
                "    path = os.environ.get('CODEX_FRAMEWORK_SMOKE_MARKER')\n"
                "    if path:\n"
                "        with open(path, 'a', encoding='utf-8') as out:\n"
                "            run_id = os.environ.get('CODEX_FRAMEWORK_SMOKE_RUN_ID')\n"
                "            for phase in ('started', 'completed'):\n"
                "                out.write(json.dumps({'schema_version': 1, 'run_id': run_id, 'hook_id': hook, 'phase': phase, 'status': 'pass'}) + '\\n')\n"
                "if args and args[0] == 'app-server':\n"
                "    for line in sys.stdin:\n"
                "        request = json.loads(line)\n"
                "        method = request.get('method')\n"
                "        request_id = request.get('id')\n"
                "        if method == 'initialize':\n"
                "            send({'id': request_id, 'result': {'userAgent': 'Codex Desktop/0.142.3'}})\n"
                "        elif method == 'initialized':\n"
                "            continue\n"
                "        elif method == 'thread/start':\n"
                "            send({'id': request_id, 'result': {'thread': {'id': 'thr_smoke'}}})\n"
                "        elif method == 'turn/start':\n"
                "            turn_id = 'turn_smoke'\n"
                "            send({'id': request_id, 'result': {'turn': {'id': turn_id, 'items': [], 'status': 'inProgress'}}})\n"
                "            item = {'id': 'item_smoke', 'type': 'mcpToolCall', 'server': 'state_manager', 'tool': 'get_work_context', 'arguments': {'view': 'startup'}, 'status': 'inProgress', 'result': None, 'error': None}\n"
                "            send({'method': 'item/started', 'params': {'threadId': 'thr_smoke', 'turnId': turn_id, 'startedAtMs': 0, 'item': item}})\n"
                "            item['status'] = 'completed'\n"
                "            item['result'] = {'content': [{'type': 'text', 'text': '{}'}]}\n"
                "            send({'method': 'item/completed', 'params': {'threadId': 'thr_smoke', 'turnId': turn_id, 'completedAtMs': 1, 'item': item}})\n"
                "            for hook in ('guard', 'state', 'format', 'stop'):\n"
                "                send({'method': 'hook/started', 'params': {'eventName': hook}})\n"
                "                send({'method': 'hook/completed', 'params': {'eventName': hook, 'status': 'completed'}})\n"
                "                marker(hook)\n"
                "            fixture = os.environ.get('CODEX_FRAMEWORK_SMOKE_FIXTURE_FILE')\n"
                "            if fixture:\n"
                "                with open(fixture, 'a', encoding='utf-8') as out:\n"
                "                    out.write('CODEX_FIXTURE_EDIT_OK\\n')\n"
                "            send({'method': 'turn/completed', 'params': {'threadId': 'thr_smoke', 'turn': {'id': turn_id, 'items': [], 'status': 'completed', 'startedAt': 0, 'completedAt': 1, 'error': None}}})\n"
                "            raise SystemExit(0)\n"
                "        elif request_id is not None:\n"
                "            send({'id': request_id, 'error': {'code': -32601, 'message': 'unsupported'}})\n"
                "    raise SystemExit(0)\n"
                "raise SystemExit(1)\n",
                encoding="utf-8",
            )
            fake.chmod(0o755)
            result = codex_runtime_smoke.run_smoke(
                root,
                codex_bin=str(fake),
                surface="desktop",
                required_surfaces=["desktop"],
                strict=True,
                model="gpt-5.5",
                reasoning_effort="xhigh",
            )
            self.assertTrue(result["ok"], result)
            self.assertEqual(result["surfaces"]["desktop"]["status"], "pass")
            self.assertEqual(result["surfaces"]["desktop"]["transport"], "app-server")
            self.assertIsNone(result["surfaces"]["desktop"]["protocol_version"])

    def test_desktop_protocol_error_is_fail_and_blocks_strict_gate(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _copy_codex_runtime(root)
            (root / "AGENTS.md").write_text("Framework v4\n", encoding="utf-8")
            (root / ".agent" / "workflows").mkdir(parents=True, exist_ok=True)
            (root / ".agent" / "workflows" / "manifest.json").write_text(
                '{"commands": []}\n', encoding="utf-8"
            )
            skill = root / ".agents" / "skills" / "source-command-test"
            skill.mkdir(parents=True)
            (skill / "SKILL.md").write_text(
                '---\nname: source-command-test\ndescription: prueba\n---\n\n'
                '<!-- GENERATED: framework wrapper;\n',
                encoding="utf-8",
            )
            (root / "_project").mkdir()
            (root / "_project" / "15_environment_profile.md").write_text(
                "- Herramienta: Codex\n", encoding="utf-8"
            )
            ensure_codex_integration.ensure_codex_integration(root, "Codex", native_only=True)
            fake = root / "fake-codex-protocol-error"
            fake.write_text(
                "#!/bin/sh\n"
                "if [ \"$1\" = \"--version\" ]; then echo codex-cli 0.142.3; exit 0; fi\n"
                "if [ \"$1\" = \"mcp\" ] && [ \"$2\" = \"list\" ]; then echo state_manager; exit 0; fi\n"
                "if [ \"$1\" = \"app-server\" ]; then read request; echo '{\"id\":1,\"error\":{\"code\":-32000,\"message\":\"protocol changed\"}}'; exit 0; fi\n"
                "exit 1\n",
                encoding="utf-8",
            )
            fake.chmod(0o755)
            result = codex_runtime_smoke.run_smoke(
                root,
                codex_bin=str(fake),
                surface="desktop",
                required_surfaces=["desktop"],
                strict=True,
                model="gpt-5.5",
                reasoning_effort="xhigh",
            )
            self.assertFalse(result["ok"], result)
            self.assertEqual(result["surfaces"]["desktop"]["status"], "fail")
            self.assertIn("surface:desktop", result["blocking"])


class CodexResultPolicyTests(unittest.TestCase):
    def _checks(self, **overrides):
        checks = {
            "agents": {"status": "pass"},
            "workflow_discovery": {"status": "pass"},
            "skill_discovery": {"status": "pass"},
            "hooks_structure": {"status": "pass"},
            "hook_event_compatibility": {"status": "pass"},
            "state_manager": {"status": "pass"},
            "codex_config": {"status": "pass"},
            "codex_policy": {"status": "pass"},
            "claude_preservation": {"status": "pass"},
        }
        checks.update(overrides)
        return checks

    @staticmethod
    def _surface(status, *, preservation="pass", selected=True):
        return {
            "status": status,
            "selected": selected,
            "preservation": {"status": preservation, "detail": preservation},
        }

    def test_optional_failure_does_not_block_required_cli(self):
        surfaces = {
            "cli": self._surface("pass"),
            "desktop": self._surface("fail"),
        }

        normal = codex_runtime_smoke.evaluate_result(self._checks(), surfaces, ["cli"], False)
        strict = codex_runtime_smoke.evaluate_result(self._checks(), surfaces, ["cli"], True)

        self.assertEqual(normal["runtime_smoke"], "pass", normal)
        self.assertTrue(normal["ok"], normal)
        self.assertTrue(strict["ok"], strict)
        self.assertEqual(normal["surfaces"]["desktop"]["status"], "fail")

    def test_optional_unavailable_surface_does_not_block_or_degrade_required_cli(self):
        surfaces = {
            "cli": self._surface("pass"),
            "desktop": self._surface("unavailable", preservation="unavailable"),
        }

        normal = codex_runtime_smoke.evaluate_result(self._checks(), surfaces, ["cli"], False)
        strict = codex_runtime_smoke.evaluate_result(self._checks(), surfaces, ["cli"], True)

        self.assertEqual(normal["runtime_smoke"], "pass", normal)
        self.assertEqual(strict["runtime_smoke"], "pass", strict)
        self.assertTrue(normal["ok"], normal)
        self.assertTrue(strict["ok"], strict)

    def test_required_unavailable_is_informational_only_in_normal_mode(self):
        surfaces = {"cli": self._surface("unavailable"), "desktop": self._surface("pass")}

        normal = codex_runtime_smoke.evaluate_result(self._checks(), surfaces, ["cli"], False)
        strict = codex_runtime_smoke.evaluate_result(self._checks(), surfaces, ["cli"], True)

        self.assertEqual(normal["runtime_smoke"], "unavailable", normal)
        self.assertTrue(normal["ok"], normal)
        self.assertFalse(strict["ok"], strict)
        self.assertIn("surface:cli", strict["blocking"])

    def test_optional_preservation_damage_is_global_and_blocks(self):
        surfaces = {
            "cli": self._surface("pass"),
            "desktop": self._surface("fail", preservation="fail"),
        }

        result = codex_runtime_smoke.evaluate_result(self._checks(), surfaces, ["cli"], False)

        self.assertEqual(result["runtime_smoke"], "fail", result)
        self.assertFalse(result["ok"], result)
        self.assertTrue(any("preservation" in item for item in result["blocking"]), result)

    def test_structural_global_failure_always_blocks(self):
        surfaces = {"cli": self._surface("pass"), "desktop": self._surface("fail")}

        result = codex_runtime_smoke.evaluate_result(
            self._checks(codex_policy={"status": "fail"}), surfaces, ["cli"], False
        )

        self.assertFalse(result["ok"], result)
        self.assertIn("codex_policy", result["blocking"])

    def test_global_unavailable_blocks_only_strict_mode(self):
        surfaces = {"cli": self._surface("pass"), "desktop": self._surface("fail")}
        checks = self._checks(codex_mcp_read={"status": "unavailable"})

        normal = codex_runtime_smoke.evaluate_result(checks, surfaces, ["cli"], False)
        strict = codex_runtime_smoke.evaluate_result(checks, surfaces, ["cli"], True)

        self.assertTrue(normal["ok"], normal)
        self.assertFalse(strict["ok"], strict)
        self.assertIn("codex_mcp_read", strict["blocking"])

    def test_app_server_error_notification_cannot_be_hidden_by_completed_turn(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fake = root / "fake-codex-error-notification"
            fake.write_text(
                "#!/usr/bin/env python3\n"
                "import json, sys\n"
                "def send(value):\n"
                "    print(json.dumps(value), flush=True)\n"
                "for line in sys.stdin:\n"
                "    request = json.loads(line)\n"
                "    method = request.get('method')\n"
                "    request_id = request.get('id')\n"
                "    if method == 'initialize':\n"
                "        send({'id': request_id, 'result': {'userAgent': 'Codex/0.142.3', 'protocolVersion': 'v2'}})\n"
                "    elif method == 'initialized':\n"
                "        continue\n"
                "    elif method == 'thread/start':\n"
                "        send({'id': request_id, 'result': {'thread': {'id': 'thr_smoke'}}})\n"
                "    elif method == 'turn/start':\n"
                "        send({'id': request_id, 'result': {'turn': {'id': 'turn_smoke'}}})\n"
                "        send({'method': 'error', 'params': {'message': 'turn failed'}})\n"
                "        send({'method': 'turn/completed', 'params': {'threadId': 'thr_smoke', 'turn': {'id': 'turn_smoke', 'status': 'completed'}}})\n"
                "        raise SystemExit(0)\n",
                encoding="utf-8",
            )
            fake.chmod(0o755)
            marker = root / "hooks.jsonl"
            result = codex_runtime_smoke._run_app_server_turn(
                str(fake),
                root,
                root,
                "read-only",
                "gpt-5.5",
                "xhigh",
                "smoke",
                marker,
                "run-test-1",
            )
            self.assertEqual(result["status"], "fail")

    def test_app_server_clean_eof_without_handshake_is_unavailable(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fake = root / "fake-codex-eof"
            fake.write_text(
                "#!/bin/sh\n"
                "if [ \"$1\" = \"app-server\" ]; then read request; exit 0; fi\n"
                "exit 1\n",
                encoding="utf-8",
            )
            fake.chmod(0o755)
            result = codex_runtime_smoke._run_app_server_turn(
                str(fake),
                root,
                root,
                "read-only",
                "gpt-5.5",
                "xhigh",
                "smoke",
                root / "hooks.jsonl",
                "run-test-eof",
            )
            self.assertEqual(result["status"], "unavailable", result)


class CodexStopTimeoutTests(unittest.TestCase):
    """5.3.0 L5b (FW-352): sin `timeout` declarado el límite del `Stop` de Codex es de 600 segundos."""

    def test_el_sincronizador_genera_el_stop_con_su_timeout(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _copy_codex_runtime(root)
            sync_codex_hooks.sync_hooks(root)
            config = json.loads((root / ".codex" / "hooks.json").read_text(encoding="utf-8"))
            stop = _hook_entry(config, "Stop", "stop")
            self.assertEqual(stop.get("timeout"), 15)
            self.assertGreater(stop["timeout"], turn_close_hook.HOOK_TIMEOUT_S)
            others = [h for groups in config["hooks"].values() for g in groups for h in g["hooks"] if h is not stop]
            self.assertEqual(len(others), 3)
            self.assertFalse([h for h in others if "timeout" in h], "solo el Stop declara timeout")

    def _doctor_errors(self, edit) -> list[str]:
        original_root = framework_doctor.ROOT
        with tempfile.TemporaryDirectory() as tmp:
            try:
                root = Path(tmp)
                _copy_codex_runtime(root)
                ensure_codex_integration.ensure_codex_integration(root, "Codex")
                (root / "_project").mkdir()
                (root / "_project" / "15_environment_profile.md").write_text(
                    "- Herramienta: Codex\n- Soporte estructural Codex: pass\n- Smoke runtime Codex: unavailable\n"
                    "- Versión de Codex: no disponible\n- Hooks Codex: gestionados\n"
                    "- MCP local `state_manager` (Codex `.codex/config.toml` / Claude `.mcp.json`): conectado\n"
                    "- Diferencias Codex conocidas: ninguna\n",
                    encoding="utf-8",
                )
                path = root / ".codex" / "hooks.json"
                config = json.loads(path.read_text(encoding="utf-8"))
                edit(_hook_entry(config, "Stop", "stop"))
                path.write_text(json.dumps(config), encoding="utf-8")
                framework_doctor.ROOT = root
                errors: list[str] = []
                framework_doctor.validate_codex_integration("initialized", {}, errors, [])
                return [error for error in errors if "timeout" in error]
            finally:
                framework_doctor.ROOT = original_root

    def test_el_doctor_acepta_el_timeout_generado(self):
        self.assertEqual(self._doctor_errors(lambda stop: None), [])

    def test_el_doctor_rechaza_un_stop_sin_timeout_o_fuera_de_rango(self):
        errors = self._doctor_errors(lambda stop: stop.pop("timeout", None))
        self.assertEqual(len(errors), 1, errors)
        self.assertIn("600", errors[0])
        self.assertIn("python3 .agent/scripts/sync_codex_hooks.py", errors[0])
        for value in (turn_close_hook.HOOK_TIMEOUT_S, 0, 61, 600, True, "15", None, [15]):
            with self.subTest(timeout=value):
                self.assertEqual(len(self._doctor_errors(lambda stop, v=value: stop.update(timeout=v))), 1)
        for value in (turn_close_hook.HOOK_TIMEOUT_S + 1, 15, 30.5, 60):
            with self.subTest(timeout=value):
                self.assertEqual(self._doctor_errors(lambda stop, v=value: stop.update(timeout=v)), [])

    def test_el_doctor_comprueba_que_la_plantilla_lo_traiga(self):
        original_root = framework_doctor.ROOT
        for edit, expected in ((lambda stop: stop.pop("timeout", None), 1), (lambda stop: None, 0)):
            with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as home, \
                    patch.dict(os.environ, {"HOME": home}), \
                    patch.object(framework_doctor, "_harness_version", return_value=None):
                try:
                    root = Path(tmp)
                    _copy_codex_runtime(root)
                    path = root / ".agent" / "templates" / "codex_hooks.json.base"
                    template = json.loads(path.read_text(encoding="utf-8"))
                    edit(template["hooks"]["Stop"][0]["hooks"][0])
                    path.write_text(json.dumps(template), encoding="utf-8")
                    framework_doctor.ROOT = root
                    errors: list[str] = []
                    framework_doctor.validate_turn_close_hook("template", errors, [])
                    mine = [error for error in errors if "codex_hooks.json.base" in error]
                    self.assertEqual(len(mine), expected, errors)
                finally:
                    framework_doctor.ROOT = original_root


class CodexRunnerHardeningTests(unittest.TestCase):
    """5.3.0 L5b (FW-352): salida ASCII del runner y marcador de smoke confinado a la telemetría."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="runner_l5b_"))
        self.addCleanup(shutil.rmtree, self.tmp)
        self.home = self.tmp / "home"
        self.home.mkdir()
        self.root = self.tmp / "proyecto"
        self.root.mkdir()
        patcher = patch.dict(os.environ, {"HOME": str(self.home)})
        patcher.start()
        self.addCleanup(patcher.stop)
        _copy_codex_runtime(self.root)
        self.smoke = turn_close_hook.telemetry_dir(self.root) / "smoke"

    def _run(self, hook_id: str, event: dict, marker: str | None = None) -> subprocess.CompletedProcess:
        env = os.environ.copy()
        env.pop("CODEX_FRAMEWORK_SMOKE_MARKER", None)
        if marker is not None:
            env["CODEX_FRAMEWORK_SMOKE_MARKER"] = marker
            env["CODEX_FRAMEWORK_SMOKE_RUN_ID"] = "run-l5b"
        return subprocess.run(
            [sys.executable, str(SCRIPTS / "codex_hook_runner.py"), "--hook-id", hook_id],
            cwd=self.root / "nested", input=json.dumps(event).encode("utf-8"), capture_output=True, env=env,
        )

    def _stop(self, marker: str | None = None) -> subprocess.CompletedProcess:
        return self._run("stop", {"cwd": str(self.root)}, marker)

    def test_la_salida_del_runner_sale_en_ascii_y_conserva_el_texto(self):
        state_event = {"cwd": str(self.root), "tool_name": "Write",
                       "tool_input": {"file_path": str(self.root / "_project" / "03_project_state.json")}}
        for hook_id, event, word in (("stop", {"cwd": str(self.root)}, "Sesión"), ("state", state_event, "módulo")):
            with self.subTest(hook=hook_id):
                result = self._run(hook_id, event)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertTrue(result.stdout.isascii(), result.stdout)
                self.assertIn(word, json.loads(result.stdout.decode("ascii"))["systemMessage"])

    def test_un_marcador_dentro_de_la_carpeta_de_smoke_se_escribe(self):
        marker = self.smoke / "run-l5b.jsonl"
        result = self._stop(str(marker))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, b"")
        records = [json.loads(line) for line in marker.read_text(encoding="utf-8").splitlines()]
        self.assertEqual([(r["hook_id"], r["phase"]) for r in records], [("stop", "started"), ("stop", "completed")])
        self.assertEqual(marker.stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.smoke.stat().st_mode & 0o777, 0o700)
        self.assertEqual(self.smoke.parent.stat().st_mode & 0o777, 0o700)

    def test_un_marcador_fuera_de_la_carpeta_de_smoke_no_se_escribe_y_avisa(self):
        baseline = self._stop()
        outside = self.tmp / "otro"
        outside.mkdir()
        (self.smoke / "sub").mkdir(parents=True, mode=0o700)
        cases = {
            "raiz del proyecto": self.root / "hooks.jsonl",
            "con ..": Path(f"{self.smoke}/../fuera.jsonl"),
            "subcarpeta": self.smoke / "sub" / "hooks.jsonl",
            "otro sitio": outside / "hooks.jsonl",
            "ruta relativa": Path("hooks.jsonl"),
            "la propia carpeta": self.smoke,
        }
        for label, marker in cases.items():
            with self.subTest(label):
                result = self._stop(str(marker))
                self.assertEqual((result.returncode, result.stdout), (0, baseline.stdout), result.stderr)
                self.assertIn(b"smoke", result.stderr)
        self.assertFalse((self.root / "hooks.jsonl").exists())
        self.assertEqual(list(outside.iterdir()), [])
        self.assertEqual(list((self.root / "nested").iterdir()), [])
        self.assertFalse((self.smoke.parent / "fuera.jsonl").exists())
        self.assertEqual(list((self.smoke / "sub").iterdir()), [])

    def test_el_marcador_no_sigue_enlaces_ni_acepta_enlaces_duros(self):
        outside = self.tmp / "ajeno"
        outside.mkdir()
        victim = outside / "intacto.txt"
        victim.write_text("intacto", encoding="utf-8")
        self.smoke.parent.mkdir(parents=True, mode=0o700)
        # La carpeta de smoke es un enlace.
        self.smoke.symlink_to(outside, target_is_directory=True)
        self.assertEqual(self._stop(str(self.smoke / "hooks.jsonl")).returncode, 0)
        self.assertEqual(sorted(p.name for p in outside.iterdir()), ["intacto.txt"])
        self.smoke.unlink()
        self.smoke.mkdir(mode=0o700)
        # El fichero del marcador es un enlace simbólico o duro.
        (self.smoke / "simbolico.jsonl").symlink_to(victim)
        os.link(victim, self.smoke / "duro.jsonl")
        for name in ("simbolico.jsonl", "duro.jsonl"):
            result = self._stop(str(self.smoke / name))
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn(b"smoke", result.stderr)
        self.assertEqual(victim.read_text(encoding="utf-8"), "intacto")

    def test_el_smoke_crea_su_marcador_en_la_telemetria_y_borra_solo_su_fichero(self):
        root = self.tmp / "smoke_root"
        root.mkdir()
        _prepare_codex_smoke_root(root)
        fake = _write_fake_cli(root)
        smoke = turn_close_hook.telemetry_dir(root) / "smoke"
        smoke.mkdir(parents=True, mode=0o700)
        keep = smoke / "otra-ejecucion.jsonl"
        keep.write_text("{}\n", encoding="utf-8")
        with patch.object(codex_runtime_smoke, "_run_cli_turn", wraps=codex_runtime_smoke._run_cli_turn) as turn:
            result = codex_runtime_smoke.run_smoke(
                root, codex_bin=str(fake), surface="cli", required_surfaces=["cli"], strict=True,
                model="gpt-5.5", reasoning_effort="xhigh",
            )
        self.assertTrue(result["ok"], result)
        markers = {call.args[7] for call in turn.call_args_list}
        self.assertEqual(len(markers), 1)
        (marker,) = markers
        self.assertEqual(Path(marker).parent, smoke)
        self.assertEqual(sorted(p.name for p in smoke.iterdir()), ["otra-ejecucion.jsonl"])
        self.assertEqual(keep.read_text(encoding="utf-8"), "{}\n")


    # ---- Ronda 2 (revisión security)

    def test_un_fifo_como_marcador_de_smoke_no_cuelga_el_runner(self):
        self.smoke.mkdir(parents=True, mode=0o700)
        os.mkfifo(self.smoke / "fifo.jsonl")
        started = __import__("time").monotonic()
        try:
            result = subprocess.run(
                [sys.executable, str(SCRIPTS / "codex_hook_runner.py"), "--hook-id", "stop"], cwd=self.root / "nested",
                input=json.dumps({"cwd": str(self.root)}).encode("utf-8"), capture_output=True, timeout=10,
                env={**os.environ, "CODEX_FRAMEWORK_SMOKE_MARKER": str(self.smoke / "fifo.jsonl"),
                     "CODEX_FRAMEWORK_SMOKE_RUN_ID": "run-l5b"},
            )
        except subprocess.TimeoutExpired:
            self.fail("el runner se colgó con un FIFO como marcador")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertLess(__import__("time").monotonic() - started, 3)
        self.assertIn("Sesión", json.loads(result.stdout.decode("ascii"))["systemMessage"])

    def test_el_smoke_con_home_irresoluble_da_veredicto_en_vez_de_lanzar(self):
        with patch.object(Path, "home", side_effect=RuntimeError("sin home")):
            with codex_runtime_smoke._smoke_marker(self.root, "abc") as marker:
                records, error = codex_runtime_smoke._marker_records(marker, "abc")
        self.assertEqual(records, [])
        self.assertIn("no produjo marcador", error)

    def test_el_smoke_no_borra_a_traves_de_una_carpeta_enlazada(self):
        smoke = turn_close_hook.telemetry_dir(self.root) / "smoke"
        smoke.parent.mkdir(parents=True, mode=0o700)
        outside = self.tmp / "otro"
        outside.mkdir()
        (outside / "abc.jsonl").write_text("ajeno", encoding="utf-8")
        smoke.symlink_to(outside, target_is_directory=True)
        with codex_runtime_smoke._smoke_marker(self.root, "abc"):
            pass
        self.assertEqual((outside / "abc.jsonl").read_text(encoding="utf-8"), "ajeno")

    def test_el_smoke_no_lee_marcadores_a_traves_de_una_carpeta_enlazada(self):
        record = json.dumps({"schema_version": 1, "run_id": "abc", "hook_id": "stop", "phase": "started", "status": "pass"})
        outside = self.tmp / "otro"
        outside.mkdir()
        (outside / "abc.jsonl").write_text(record + "\n", encoding="utf-8")
        link = self.tmp / "enlace"
        link.symlink_to(outside, target_is_directory=True)
        records, error = codex_runtime_smoke._marker_records(link / "abc.jsonl", "abc")
        self.assertEqual(records, [])
        self.assertIsNotNone(error)
        records, error = codex_runtime_smoke._marker_records(outside / "abc.jsonl", "abc")
        self.assertEqual((len(records), error), (1, None))


if __name__ == "__main__":
    unittest.main()
