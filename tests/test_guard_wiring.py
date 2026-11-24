#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Cableado del Production Command Guard en las 3 superficies (FW-082, ADR-051).

El motor (`guard_destructive_command.py`) no sirve de nada si ninguna superficie
lo invoca. Este test verifica que cada adaptador existe y apunta al motor:
- Claude Code: hook `PreToolUse` (matcher Bash) en `.claude/settings.json`.
- OpenCode: `permission.bash` en `opencode.json` con patrones destructivos.
- Codex: sección dura en `AGENTS.md` (best-effort — sin deny-hook nativo).

Anti-regresión: los hooks/bloques preexistentes de cada archivo deben seguir
presentes (cambio ADITIVO).
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GUARD_SCRIPT = "guard_destructive_command.py"


class ClaudeCodeAdapterTests(unittest.TestCase):
    def setUp(self):
        self.settings = json.loads((ROOT / ".claude" / "settings.json").read_text(encoding="utf-8"))

    def test_pretooluse_hook_invokes_guard(self):
        hooks = self.settings.get("hooks", {})
        pre = hooks.get("PreToolUse")
        self.assertTrue(pre, "falta el hook PreToolUse en .claude/settings.json")
        blob = json.dumps(pre)
        self.assertIn(GUARD_SCRIPT, blob, "el hook PreToolUse no invoca el motor del guardia")
        self.assertIn("Bash", blob, "el hook PreToolUse debe filtrar por Bash")

    def test_existing_hooks_preserved(self):
        """ADITIVO: los hooks previos siguen ahí."""
        hooks = self.settings.get("hooks", {})
        for kind in ("Stop", "PostToolUseFailure", "PostToolUse"):
            self.assertIn(kind, hooks, f"regresión: se perdió el hook {kind}")

    def _stop_command(self) -> str:
        return self.settings["hooks"]["Stop"][0]["hooks"][0]["command"]

    def test_stop_hook_reads_session_id(self):
        """FW-205: `Stop` dispara una vez por turno; el recordatorio /gycd debe
        deduplicarse por sesión, no emitirse tras cada respuesta.

        5.3.0 L4: la orden ya no es un script de shell; llama a `turn_close_hook.py`,
        que conserva el recordatorio y lo deduplica por `session_id`."""
        cmd = self._stop_command()
        self.assertIn("turn_close_hook.py --host claude", cmd, "el hook Stop no llama al módulo del cierre de turno")
        module = (ROOT / ".agent" / "scripts" / "turn_close_hook.py").read_text(encoding="utf-8")
        self.assertIn('event.get("session_id")', module, "el hook Stop no lee session_id para deduplicar")
        self.assertIn("/gycd", module, "el hook Stop dejó de recordar /gycd")

    def _run_stop(self, session_id: str):
        # El recordatorio se deduplica en la telemetría del hook (bajo HOME): un HOME
        # temporal por test evita tocar el del usuario y aísla las sesiones.
        if not hasattr(self, "_stop_home"):
            self._stop_home = tempfile.mkdtemp(prefix="fw205_home_")
            self.addCleanup(shutil.rmtree, self._stop_home, ignore_errors=True)
        env = {**os.environ, "HOME": self._stop_home, "CLAUDE_PROJECT_DIR": str(ROOT)}
        return self._stop_home, lambda: subprocess.run(
            ["bash", "-c", self._stop_command()],
            input=json.dumps({"session_id": session_id, "hook_event_name": "Stop"}),
            capture_output=True, text=True, env=env,
        )

    def test_stop_hook_deduplicates_per_session(self):
        sid = f"fw205-{os.getpid()}"
        _, run = self._run_stop(sid)
        r1 = run()
        self.assertIn("gycd", r1.stdout, f"1ª vez debe emitir: {r1.stdout!r} / {r1.stderr!r}")
        r2 = run()
        self.assertEqual(r2.stdout.strip(), "", f"2ª vez en la misma sesión no debe emitir: {r2.stdout!r}")

    def test_stop_hook_emits_again_for_new_session(self):
        _, run_a = self._run_stop(f"fw205a-{os.getpid()}")
        run_a()
        _, run_b = self._run_stop(f"fw205b-{os.getpid()}")
        self.assertIn("gycd", run_b().stdout, "una sesión distinta debe volver a emitir")


class OpenCodeAdapterTests(unittest.TestCase):
    def setUp(self):
        self.cfg = json.loads((ROOT / "opencode.json").read_text(encoding="utf-8"))

    def test_bash_permission_present(self):
        perm = self.cfg.get("permission", {})
        bash = perm.get("bash")
        self.assertTrue(bash, "falta permission.bash en opencode.json")
        keys = " ".join(bash.keys()).lower()
        # al menos un patrón destructivo canónico está gobernado
        self.assertTrue(
            any(tok in keys for tok in ("db push", "drop", "truncate", "rm -rf")),
            f"permission.bash no gobierna patrones destructivos: {list(bash)}",
        )
        # y ninguno de esos está en 'allow'
        for pat, level in bash.items():
            if any(tok in pat.lower() for tok in ("db push", "drop", "truncate", "rm -rf")):
                self.assertIn(level, ("ask", "deny"), f"patrón destructivo en '{level}': {pat}")

    def test_existing_config_preserved(self):
        """ADITIVO: instructions, permission.skill y mcp siguen intactos."""
        self.assertIn("CLAUDE.md", self.cfg.get("instructions", []))
        self.assertIn("skill", self.cfg.get("permission", {}))
        self.assertIn("state_manager", self.cfg.get("mcp", {}))


class CodexAdapterTests(unittest.TestCase):
    def test_agents_md_documents_guard(self):
        text = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
        self.assertIn("APPROVED-DESTRUCTIVE", text,
                      "AGENTS.md debe documentar la escotilla del guardia para Codex")
        self.assertIn(GUARD_SCRIPT, text,
                      "AGENTS.md debe apuntar al motor del guardia")


class DoctorWiringTests(unittest.TestCase):
    def test_feature_declared_in_wiring(self):
        spec = json.loads((ROOT / ".agent" / "scripts" / "doctor_wiring.json").read_text(encoding="utf-8"))
        self.assertIn("production_command_guard", spec.get("features", {}),
                      "falta la feature production_command_guard en doctor_wiring.json")


if __name__ == "__main__":
    unittest.main()
