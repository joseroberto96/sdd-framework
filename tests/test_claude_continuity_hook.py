#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Pruebas del Hook de Continuidad de Claude Code (Framework 4.5 — Lote 3).

Cubre el contrato formal de 5 campos (completitud, cursor, referencias, vigencia,
vía de recuperación), selección de vistas por evento, límites de tiempo y tamaño
(12 KiB sin truncar JSON), degradación a sobre compacto, modo plantilla y
garantía estricta de solo lectura (cero escrituras).
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / ".agent" / "scripts"))

import claude_continuity_hook as hook
import ensure_claude_settings as ecs
import state_core


def _snapshot_dir(directory: Path) -> dict[str, bytes]:
    """Toma una instantánea de todos los archivos y contenidos de un directorio."""
    snapshot: dict[str, bytes] = {}
    if not directory.exists():
        return snapshot
    for root_dir, _, files in os.walk(directory):
        for f in files:
            path = Path(root_dir) / f
            try:
                snapshot[str(path.relative_to(directory))] = path.read_bytes()
            except OSError:
                pass
    return snapshot


class ContinuityHookTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.project = self.tmp / "mi-proyecto"
        self.project.mkdir(parents=True)

        # Crear estructura mínima de proyecto initialized
        proj_dir = self.project / "_project"
        proj_dir.mkdir(parents=True)
        agent_dir = self.project / ".agent" / "rules"
        agent_dir.mkdir(parents=True)
        workflows_dir = self.project / ".agent" / "workflows"
        workflows_dir.mkdir(parents=True)

        self.initial_state = {
            "schema_version": 1,
            "project": "Test Project",
            "framework_mode": "initialized",
            "framework_version": "4.40.1",
            "current_phase": "Fase 1",
            "active_module": "mod-alpha",
            "session": {"active_module": "mod-alpha"},
            "modules": {
                "fase_1": ["mod-alpha", "mod-beta"],
                "fase_2": [],
            },
            "module_statuses": {
                "mod-alpha": "in_progress",
                "mod-beta": "pending",
            },
            "work_tiers": {
                "mod-alpha": {"tier": "tier_1", "rationale": "Test alpha"},
            },
            "dependencies": [],
            "active_mcps": ["state_manager"],
            "changes": [],
            "last_completed_step": "Pasos previos de diseño completados",
        }
        (proj_dir / "03_project_state.json").write_text(
            json.dumps(self.initial_state, indent=2), encoding="utf-8"
        )
        (proj_dir / "04_active_scope.md").write_text(
            "# Scope\n\n<!-- CONTEXT_SOURCE: module_scope -->\nScope del módulo mod-alpha\n<!-- END_CONTEXT_SOURCE: module_scope -->\n",
            encoding="utf-8",
        )
        (proj_dir / "core.md").write_text("# Core projection\n", encoding="utf-8")
        (proj_dir / "08_module_acceptance.md").write_text(
            "# Acceptance\n\n<!-- CONTEXT_SOURCE: module_acceptance -->\nAC1: Criterio verificado\n<!-- END_CONTEXT_SOURCE: module_acceptance -->\n",
            encoding="utf-8",
        )
        (workflows_dir / "reconciliar.md").write_text(
            "# Reconciliar\n\n<!-- CONTEXT_SOURCE: recovery_audit -->\nAuditoría de recuperación\n<!-- END_CONTEXT_SOURCE: recovery_audit -->\n",
            encoding="utf-8",
        )
        (proj_dir / "13_session_journal.md").write_text(
            "# Session Journal\n\n<!-- CONTEXT_SOURCE: recovery_journal -->\nJournal activo\n<!-- END_CONTEXT_SOURCE: recovery_journal -->\n",
            encoding="utf-8",
        )

        self.orig_root = state_core.WORKSPACE_ROOT

    def tearDown(self):
        hook._set_workspace_root(self.orig_root)

    def test_template_mode_emits_minimal_orientation_without_queries(self):
        template_proj = self.tmp / "plantilla"
        (template_proj / "_project").mkdir(parents=True)
        template_state = {"framework_mode": "template", "project": "[NOMBRE]"}
        (template_proj / "_project" / "03_project_state.json").write_text(
            json.dumps(template_state), encoding="utf-8"
        )

        envelope = hook.build_continuity_context(
            root=template_proj, event={"session_id": "ses-tmpl", "source": "startup"}
        )

        self.assertEqual(envelope["status"], "TEMPLATE_MODE")
        self.assertTrue(envelope["completitud"]["complete"])
        self.assertTrue(envelope["completitud"]["required_context_complete"])
        self.assertEqual(envelope["completitud"]["gate_status"], "PASS")
        self.assertIn("/nuevo-proyecto", envelope["via_de_recuperacion"])
        self.assertIn("template", envelope["data"]["summary"]["framework_mode"])

    def test_startup_and_clear_route_to_startup_view(self):
        for src in ("startup", "clear"):
            with self.subTest(source=src):
                envelope = hook.build_continuity_context(
                    root=self.project, event={"session_id": "ses-01", "source": src}
                )
                self.assertIn("vigencia", envelope)
                self.assertEqual(envelope["vigencia"]["view"], "startup")
                self.assertEqual(envelope["vigencia"]["source"], src)
                self.assertEqual(envelope["vigencia"]["session_id"], "ses-01")
                self.assertIn("completitud", envelope)
                self.assertIn(envelope["completitud"]["gate_status"], ("PASS", "NEEDS_INPUT", "BLOCK"))
                self.assertIn("cursor", envelope)
                self.assertIn("referencias", envelope)
                self.assertIn("via_de_recuperacion", envelope)
                self.assertTrue(len(envelope["via_de_recuperacion"]) > 0)

    def test_recovery_sources_route_to_recovery_view(self):
        for src in ("resume", "compact", "fork"):
            with self.subTest(source=src):
                envelope = hook.build_continuity_context(
                    root=self.project, event={"session_id": "ses-02", "source": src}
                )
                self.assertEqual(envelope["vigencia"]["view"], "recovery")
                self.assertEqual(envelope["vigencia"]["source"], src)
                self.assertIn("completitud", envelope)
                self.assertIn("via_de_recuperacion", envelope)

    def test_unknown_source_emits_advisory_and_never_pass(self):
        envelope = hook.build_continuity_context(
            root=self.project, event={"session_id": "ses-03", "source": "unknown_origin"}
        )
        self.assertEqual(envelope["status"], "UNKNOWN_SOURCE")
        self.assertFalse(envelope["completitud"]["complete"])
        self.assertEqual(envelope["completitud"]["gate_status"], "NEEDS_INPUT")
        self.assertNotEqual(envelope["completitud"]["gate_status"], "PASS")
        self.assertIn("/reconciliar", envelope["via_de_recuperacion"])

    def test_timeout_handling_emits_incomplete_and_never_pass(self):
        # Query con timeout minúsculo (0.00001 s) para forzar TimeoutError
        envelope = hook.build_continuity_context(
            root=self.project,
            event={"session_id": "ses-timeout", "source": "startup"},
            query_timeout=0.000001,
        )
        self.assertEqual(envelope["status"], "TIMEOUT")
        self.assertFalse(envelope["completitud"]["complete"])
        self.assertEqual(envelope["completitud"]["gate_status"], "NEEDS_INPUT")
        self.assertNotEqual(envelope["completitud"]["gate_status"], "PASS")
        self.assertIn("/reconciliar", envelope["via_de_recuperacion"])
        self.assertIn("excedió el límite", envelope["via_de_recuperacion"])

    def test_corrupt_state_does_not_crash_and_guarantees_zero_writes(self):
        # Corromper deliberadamente el JSON de estado
        state_file = self.project / "_project" / "03_project_state.json"
        state_file.write_text("{ corrupt json :::: ", encoding="utf-8")

        before_snapshot = _snapshot_dir(self.project)

        envelope = hook.build_continuity_context(
            root=self.project, event={"session_id": "ses-err", "source": "startup"}
        )

        after_snapshot = _snapshot_dir(self.project)

        # 1. No debe lanzar excepción, sino reportar ERROR y contexto incompleto
        self.assertEqual(envelope["status"], "ERROR")
        self.assertFalse(envelope["completitud"]["complete"])
        self.assertEqual(envelope["completitud"]["gate_status"], "NEEDS_INPUT")
        self.assertNotEqual(envelope["completitud"]["gate_status"], "PASS")
        self.assertIn("/reconciliar", envelope["via_de_recuperacion"])

        # 2. Cero escrituras en disco (instantánea idéntica)
        self.assertEqual(before_snapshot, after_snapshot, "El hook modificó archivos ante estado corrupto")

    def test_output_size_overflow_degrades_to_compact_valid_json(self):
        # Generar un sobre ficticio que exceda 12 KiB
        large_items = [{"id": f"item_{i}", "kind": "data", "content": "x" * 500} for i in range(40)]
        envelope = {
            "hook": "claude_continuity_hook",
            "version": "1.0",
            "status": "OK",
            "vigencia": {
                "session_id": "ses-big",
                "source": "startup",
                "view": "startup",
                "source_fingerprint": "abc123hash",
                "timestamp": "2026-09-15T12:00:00Z",
                "module_id": "mod-alpha",
            },
            "completitud": {
                "complete": False,
                "required_context_complete": False,
                "gate_status": "NEEDS_INPUT",
                "blocking_count": 0,
            },
            "cursor": "token_cursor_123",
            "referencias": [{"section": "scope", "reason": "more_pages"}],
            "via_de_recuperacion": "Continuar paginación con cursor",
            "data": {"summary": {"total_items": 40}, "items": large_items},
        }

        # Serializar con límite de 12 KiB
        output_str = hook.render_hook_output(envelope, max_bytes=12 * 1024)
        output_bytes = len(output_str.encode("utf-8"))

        self.assertLessEqual(output_bytes, 12 * 1024, "La salida excedió el límite de 12 KiB")

        # Verificar que es JSON válido
        parsed_wrapper = json.loads(output_str)
        self.assertIn("systemMessage", parsed_wrapper)
        self.assertIn("hookSpecificOutput", parsed_wrapper)
        self.assertIn("additionalContext", parsed_wrapper["hookSpecificOutput"])

        inner_envelope = json.loads(parsed_wrapper["hookSpecificOutput"]["additionalContext"])
        self.assertEqual(inner_envelope["status"], "COMPACT_OVERFLOW")
        self.assertEqual(inner_envelope["cursor"], "token_cursor_123")
        self.assertEqual(inner_envelope["data"]["items"], [])
        self.assertIn("Salida excedió el límite", inner_envelope["via_de_recuperacion"])
        self.assertIn("COMPACT_OVERFLOW", parsed_wrapper["systemMessage"])

    def test_cli_no_se_bloquea_con_stdin_abierto_sin_datos(self):
        """N14: un host que hereda una tubería abierta sin cerrarla no cuelga el hook."""
        script = ROOT / ".agent" / "scripts" / "claude_continuity_hook.py"
        env = {**os.environ, "CLAUDE_PROJECT_DIR": str(self.project)}

        proc = subprocess.Popen(
            [sys.executable, str(script), "--source", "startup", "--raw"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=env,
        )
        try:
            proc.wait(timeout=5)
            stdout = proc.stdout.read()
            stderr = proc.stderr.read()
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
            self.fail("el hook no terminó en 5 s con stdin abierto y sin datos")
        finally:
            for stream in (proc.stdin, proc.stdout, proc.stderr):
                if stream:
                    stream.close()
        self.assertEqual(proc.returncode, 0, stderr)
        self.assertEqual(json.loads(stdout)["hook"], "claude_continuity_hook")

    def test_cli_subprocess_execution(self):
        script = ROOT / ".agent" / "scripts" / "claude_continuity_hook.py"
        env = {**os.environ, "CLAUDE_PROJECT_DIR": str(self.project)}

        proc = subprocess.run(
            [sys.executable, str(script), "--source", "startup", "--raw"],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            env=env,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        data = json.loads(proc.stdout)
        self.assertEqual(data["hook"], "claude_continuity_hook")
        self.assertEqual(data["vigencia"]["view"], "startup")

    def test_cli_subprocess_stdin_event(self):
        script = ROOT / ".agent" / "scripts" / "claude_continuity_hook.py"
        env = {**os.environ, "CLAUDE_PROJECT_DIR": str(self.project)}
        event_input = json.dumps({"session_id": "ses-stdin-1", "source": "resume"})

        proc = subprocess.run(
            [sys.executable, str(script)],
            input=event_input,
            capture_output=True,
            text=True,
            env=env,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        wrapper = json.loads(proc.stdout)
        self.assertIn("systemMessage", wrapper)
        self.assertIn("hookSpecificOutput", wrapper)
        self.assertIn("additionalContext", wrapper["hookSpecificOutput"])
        inner = json.loads(wrapper["hookSpecificOutput"]["additionalContext"])
        self.assertEqual(inner["vigencia"]["view"], "recovery")
        self.assertEqual(inner["vigencia"]["session_id"], "ses-stdin-1")
        self.assertIn("recovery", wrapper["systemMessage"])


class EnsureClaudeSettingsContinuityTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.target = self.tmp / ".claude" / "settings.json"

    def test_enable_continuity_hook_adds_session_start(self):
        code, changes = ecs.ensure(self.tmp, check=False, enable_continuity_hook=True)
        self.assertEqual(code, 0)
        self.assertTrue(any("SessionStart: hook del framework añadido (claude_continuity_hook.py)" in c for c in changes))

        settings = json.loads(self.target.read_text(encoding="utf-8"))
        session_hooks = [h for g in settings["hooks"].get("SessionStart", []) for h in g["hooks"]]
        self.assertEqual(len(session_hooks), 1)
        self.assertIn("claude_continuity_hook.py", session_hooks[0]["command"])

    def test_disable_continuity_hook_removes_session_start_cleanly(self):
        # Primero habilitar
        ecs.ensure(self.tmp, check=False, enable_continuity_hook=True)
        # Luego deshabilitar
        code, changes = ecs.ensure(self.tmp, check=False, disable_continuity_hook=True)
        self.assertEqual(code, 0)
        self.assertTrue(any("SessionStart: hook del framework retirado (claude_continuity_hook.py)" in c for c in changes))

        settings = json.loads(self.target.read_text(encoding="utf-8"))
        session_hooks = [h for g in settings["hooks"].get("SessionStart", []) for h in g["hooks"]]
        self.assertEqual(len(session_hooks), 0)
        # Confirmar que los demás hooks (guard, format, stop, err_count) se preservan intactos
        self.assertIn("PreToolUse", settings["hooks"])
        self.assertIn("PostToolUse", settings["hooks"])
        self.assertIn("Stop", settings["hooks"])
        self.assertIn("PostToolUseFailure", settings["hooks"])


if __name__ == "__main__":
    unittest.main()
