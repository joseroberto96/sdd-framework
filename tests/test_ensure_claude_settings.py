#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""FW-204: `ensure_claude_settings.py` siembra/mergea `.claude/settings.json`
sin pisar los hooks propios del proyecto.

Clase FW-162/FW-171: el doctor exige ese fichero también en `initialized`, pero
ningún workflow lo sembraba. El helper es el vehículo de siembra (nuevo/migrar) y
de propagación (`seed_claude_settings` en `apply_upgrade`)."""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / ".agent" / "scripts" / "ensure_claude_settings.py"
BASE = ROOT / ".agent" / "templates" / "settings.json.base"
LIVE = ROOT / ".claude" / "settings.json"

sys.path.insert(0, str(ROOT / ".agent" / "scripts"))
import ensure_claude_settings as ecs  # noqa: E402


def _run(root: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--root", str(root), *args],
        capture_output=True, text=True,
    )


class EnsureClaudeSettingsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.target = self.tmp / ".claude" / "settings.json"

    def _settings(self) -> dict:
        return json.loads(self.target.read_text(encoding="utf-8"))

    def test_creates_from_base_when_absent(self):
        res = _run(self.tmp)
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        self.assertTrue(self.target.exists())
        self.assertEqual(self._settings(), json.loads(BASE.read_text(encoding="utf-8")))

    def test_preserves_project_own_hook(self):
        self.target.parent.mkdir(parents=True)
        self.target.write_text(json.dumps({
            "permissions": {"allow": ["Bash(ls *)"]},
            "hooks": {
                "PreToolUse": [
                    {"matcher": "Bash", "hooks": [
                        {"type": "command", "command": "my-project-linter.sh"}
                    ]}
                ]
            },
        }, indent=2), encoding="utf-8")
        res = _run(self.tmp)
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        s = self._settings()
        blob = json.dumps(s)
        self.assertIn("my-project-linter.sh", blob, "se perdió el hook propio del proyecto")
        self.assertIn("guard_destructive_command.py", blob, "no se añadió el hook del guard")
        # 5.3.1 L1.4 (cambio de contrato): solo se añade el núcleo a `deny`.
        self.assertEqual(s["permissions"]["allow"], ["Bash(ls *)"], "no debe tocar allow")
        self.assertEqual(set(s["permissions"]), {"allow", "deny"})
        self.assertEqual(s["permissions"]["deny"], ecs.claude_deny_rules(ROOT))

    def test_updates_stale_framework_hook_without_duplicating(self):
        self.target.parent.mkdir(parents=True)
        self.target.write_text(json.dumps({
            "hooks": {
                "Stop": [
                    {"hooks": [
                        {"type": "command",
                         "command": "echo '{\"systemMessage\": \"Sesión terminada. Si trabajaste en el proyecto, ejecuta /gycd para cerrar ordenadamente y guardar el estado.\"}'"}
                    ]}
                ]
            },
        }, indent=2), encoding="utf-8")
        res = _run(self.tmp)
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        stop_hooks = [h for g in self._settings()["hooks"]["Stop"] for h in g["hooks"]]
        self.assertEqual(len(stop_hooks), 1, f"el hook legacy no se reemplazó: {stop_hooks}")
        # 5.3.0 L4: la forma vigente del Stop es `turn_close_hook.py`, que deduplica el
        # recordatorio por sesión y verifica el cierre de turno (antes: orden `cc_gycd`).
        current = [h for h in stop_hooks if "turn_close_hook.py --host claude" in h["command"]]
        self.assertEqual(len(current), 1, f"no se actualizó a la forma vigente: {stop_hooks}")
        self.assertEqual(current[0]["timeout"], 15, "el Stop debe declarar su tiempo máximo")

    def test_check_does_not_write_and_signals(self):
        res = _run(self.tmp, "--check")
        self.assertNotEqual(res.returncode, 0, "con el fichero ausente, --check debe señalar cambios")
        self.assertFalse(self.target.exists(), "--check no debe escribir")

    def test_idempotent(self):
        _run(self.tmp)
        first = self.target.read_text(encoding="utf-8")
        res = _run(self.tmp)
        self.assertEqual(res.returncode, 0)
        self.assertIn("sin cambios", res.stdout)
        self.assertEqual(self.target.read_text(encoding="utf-8"), first)

    def test_check_clean_after_ensure(self):
        _run(self.tmp)
        res = _run(self.tmp, "--check")
        self.assertEqual(res.returncode, 0, f"tras sembrar, --check debe estar limpio: {res.stdout}")

    def test_malformed_hooks_structure_fails_without_writing(self):
        self.target.parent.mkdir(parents=True)
        original = '{"hooks": []}\n'
        self.target.write_text(original, encoding="utf-8")

        res = _run(self.tmp)

        self.assertEqual(res.returncode, 2, res.stdout + res.stderr)
        self.assertIn("estructura JSON inválida", res.stdout)
        self.assertEqual(original, self.target.read_text(encoding="utf-8"))

    def test_malformed_hook_group_fails_without_writing(self):
        self.target.parent.mkdir(parents=True)
        original = '{"hooks": {"PreToolUse": [{"matcher": "Bash"}]}}\n'
        self.target.write_text(original, encoding="utf-8")

        res = _run(self.tmp)

        self.assertEqual(res.returncode, 2, res.stdout + res.stderr)
        self.assertIn("grupos válidos", res.stdout)
        self.assertEqual(original, self.target.read_text(encoding="utf-8"))

    def test_malformed_hook_entry_fails_without_writing(self):
        self.target.parent.mkdir(parents=True)
        original = '{"hooks": {"PreToolUse": [{"hooks": ["not-a-hook"]}]}}\n'
        self.target.write_text(original, encoding="utf-8")

        res = _run(self.tmp)

        self.assertEqual(res.returncode, 2, res.stdout + res.stderr)
        self.assertIn("grupos válidos", res.stdout)
        self.assertEqual(original, self.target.read_text(encoding="utf-8"))

    def test_new_framework_group_keeps_all_base_restrictions(self):
        base = json.loads(BASE.read_text(encoding="utf-8"))
        base["hooks"]["PreToolUse"][0]["timeout"] = 17

        merged, _ = ecs.merge_settings(base, {"hooks": {}})

        guard_groups = [
            group
            for group in merged["hooks"]["PreToolUse"]
            if any("guard_destructive_command.py" in hook.get("command", "") for hook in group["hooks"])
        ]
        self.assertEqual(len(guard_groups), 1)
        self.assertEqual(guard_groups[0]["timeout"], 17)

    def test_merge_preserves_same_framework_hook_under_distinct_matchers(self):
        base = json.loads(BASE.read_text(encoding="utf-8"))
        guard = "python3 .agent/scripts/guard_destructive_command.py"
        current = {"hooks": {"PreToolUse": [
            {"matcher": "Edit", "hooks": [{"type": "command", "command": guard}]},
            {"matcher": "Bash", "hooks": [{"type": "command", "command": guard}]},
        ]}}

        merged, changes = ecs.merge_settings(base, current)

        groups = merged["hooks"]["PreToolUse"]
        self.assertEqual({group.get("matcher") for group in groups}, {"Edit", "Bash"})
        self.assertFalse(any("duplicado eliminado" in change for change in changes))

    def test_merge_does_not_claim_project_hook_from_incidental_path_text(self):
        base = json.loads(BASE.read_text(encoding="utf-8"))
        current = {"hooks": {"PostToolUse": [
            {"matcher": "Write", "hooks": [{
                "type": "command",
                "command": "python3 audit.py --watch 03_project_state.json",
            }]},
        ]}}

        merged, _ = ecs.merge_settings(base, current)

        hooks = [hook for group in merged["hooks"]["PostToolUse"] for hook in group["hooks"]]
        self.assertIn("python3 audit.py --watch 03_project_state.json", [hook["command"] for hook in hooks])
        self.assertIn("post_edit_format.sh", " ".join(hook["command"] for hook in hooks))

    def test_merge_does_not_claim_audit_command_that_mentions_the_reminder_text(self):
        base = json.loads(BASE.read_text(encoding="utf-8"))
        current = {"hooks": {"PostToolUse": [{"matcher": "Write", "hooks": [{
            "type": "command",
            "command": "python3 audit.py --watch 03_project_state.json --message 'project_state.json actualizado; ejecuta /cierre-modulo'",
        }]}]}}

        merged, _ = ecs.merge_settings(base, current)

        hooks = [hook for group in merged["hooks"]["PostToolUse"] for hook in group["hooks"]]
        self.assertIn(current["hooks"]["PostToolUse"][0]["hooks"][0], hooks)
        self.assertEqual(sum("post_edit_format.sh" in hook.get("command", "") for hook in hooks), 1)

    def test_merge_removes_only_exact_duplicate_and_keeps_hook_options(self):
        base = json.loads(BASE.read_text(encoding="utf-8"))
        canonical = {
            "type": "command",
            "command": 'python3 "$CLAUDE_PROJECT_DIR"/.agent/scripts/guard_destructive_command.py',
        }
        current = {
            "hooks": {
                "PreToolUse": [
                    {"matcher": "Bash", "hooks": [canonical.copy(), canonical.copy()]},
                    {"matcher": "Bash", "hooks": [{**canonical, "timeout": 30}]},
                ]
            }
        }

        merged, changes = ecs.merge_settings(base, current)

        bash_hooks = [
            hook
            for group in merged["hooks"]["PreToolUse"]
            if group.get("matcher") == "Bash"
            for hook in group.get("hooks", [])
            if "guard_destructive_command.py" in hook.get("command", "")
        ]
        self.assertEqual(len(bash_hooks), 2)
        self.assertEqual(sum("duplicado eliminado" in change for change in changes), 1)
        self.assertEqual(bash_hooks[1].get("timeout"), 30)

    def test_managed_hooks_with_distinct_group_restrictions_keep_both_scopes(self):
        base = json.loads(BASE.read_text(encoding="utf-8"))
        guard = "python3 .agent/scripts/guard_destructive_command.py"
        current = {"hooks": {"PreToolUse": [
            {"matcher": "Bash", "timeout": 30, "hooks": [{"type": "command", "command": guard}]},
        ]}}

        merged, _ = ecs.merge_settings(base, current)

        groups = merged["hooks"]["PreToolUse"]
        guards = [
            (group.get("matcher"), group.get("timeout"))
            for group in groups
            for hook in group.get("hooks", [])
            if "guard_destructive_command.py" in hook.get("command", "")
        ]
        self.assertEqual(len(guards), 2)
        self.assertIn(("Bash", 30), guards)
        self.assertIn(("Bash", None), guards)

    def test_exact_duplicate_removal_keeps_first_object_for_canonical_update(self):
        base = json.loads(BASE.read_text(encoding="utf-8"))
        old = {
            "type": "command",
            "command": "python3 .agent/scripts/guard_destructive_command.py",
            "statusMessage": "old",
        }
        current = {"hooks": {"PreToolUse": [{"matcher": "Bash", "hooks": [old.copy(), old.copy()]}]}}

        merged, changes = ecs.merge_settings(base, current)

        hooks = merged["hooks"]["PreToolUse"][0]["hooks"]
        self.assertEqual(len(hooks), 1)
        self.assertEqual(hooks[0]["statusMessage"], base["hooks"]["PreToolUse"][0]["hooks"][0]["statusMessage"])
        self.assertTrue(any("duplicado eliminado" in change for change in changes))

    def test_retires_03_project_state_reminder_on_update(self):
        self.target.parent.mkdir(parents=True)
        old_state_hook = {
            "type": "command",
            "command": "input=$(cat); echo \"$input\" | grep -q '03_project_state\\.json' && echo '{\"systemMessage\": \"project_state.json actualizado. Si acabas de cerrar un módulo, ejecuta /cierre-modulo.\"}'; true",
        }
        self.target.write_text(json.dumps({
            "hooks": {
                "PostToolUse": [
                    {"matcher": "Write|Edit", "hooks": [old_state_hook]},
                    {"matcher": "Write|Edit|MultiEdit", "hooks": [{"type": "command", "command": "post_edit_format.sh"}]},
                ]
            }
        }, indent=2), encoding="utf-8")

        res = _run(self.tmp)
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        self.assertIn("hook del framework retirado (03_project_state)", res.stdout)

        settings = self._settings()
        post_hooks = [h["command"] for g in settings["hooks"]["PostToolUse"] for h in g["hooks"]]
        self.assertFalse(any("03_project_state" in cmd for cmd in post_hooks))
        self.assertTrue(any("post_edit_format.sh" in cmd for cmd in post_hooks))

    def test_cli_enable_and_disable_continuity_hook(self):
        # Habilitar via CLI
        res = _run(self.tmp, "--enable-continuity-hook")
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        self.assertIn("hook del framework añadido (claude_continuity_hook.py)", res.stdout)
        settings = self._settings()
        session_hooks = [h["command"] for g in settings["hooks"].get("SessionStart", []) for h in g["hooks"]]
        self.assertTrue(any("claude_continuity_hook.py" in cmd for cmd in session_hooks))

        # Deshabilitar via CLI
        res_dis = _run(self.tmp, "--disable-continuity-hook")
        self.assertEqual(res_dis.returncode, 0, res_dis.stdout + res_dis.stderr)
        self.assertIn("hook del framework retirado (claude_continuity_hook.py)", res_dis.stdout)
        settings_dis = self._settings()
        session_hooks_dis = [h["command"] for g in settings_dis["hooks"].get("SessionStart", []) for h in g["hooks"]]
        self.assertEqual(len(session_hooks_dis), 0)


class NoDriftTests(unittest.TestCase):
    def test_base_matches_live_settings(self):
        """El `.base` es el `.claude/settings.json` de la plantilla — sin deriva."""
        self.assertEqual(
            json.loads(BASE.read_text(encoding="utf-8")),
            json.loads(LIVE.read_text(encoding="utf-8")),
            "settings.json.base difiere de .claude/settings.json — regenerar el .base",
        )

    def test_every_framework_hook_has_a_signature(self):
        """Cada hook del `.base` debe llevar una firma reconocible, o el merge lo
        trataría como hook del proyecto y no lo sembraría."""
        base = json.loads(BASE.read_text(encoding="utf-8"))
        for event, groups in base.get("hooks", {}).items():
            for group in groups:
                for hook in group.get("hooks", []):
                    self.assertIsNotNone(
                        ecs._signature_of(hook.get("command", "")),
                        f"hook de {event} sin firma en _FW_HOOK_SIGNATURES: {hook.get('command','')[:60]}",
                    )


class WorkflowWiringTests(unittest.TestCase):
    def test_init_workflows_invoke_the_helper(self):
        for rel in ("nuevo-proyecto.md", "migrar-proyecto.md", "actualizar-framework.md"):
            text = (ROOT / ".agent" / "workflows" / rel).read_text(encoding="utf-8")
            self.assertIn(
                "ensure_claude_settings.py", text,
                f"{rel} no invoca ensure_claude_settings.py (FW-204)",
            )


if __name__ == "__main__":
    unittest.main()
