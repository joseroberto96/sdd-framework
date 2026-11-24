#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Pruebas para Lote L6 (H4 + H5): política de sesión y propagación segura.

H4: Núcleo portable de política de sesión (cierre con /gycd y arranque limpio tras ausencia).
H5: propagate_framework.py detecta sesiones de Claude Code abiertas (< 60 min)
    y se niega a actualizar el proyecto a menos que se pase --allow-open-session.
"""
from __future__ import annotations

import contextlib
import io
import json
import os
import re
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / ".agent" / "scripts"))

import propagate_framework as propagate


class TestL6H4SessionPolicy(unittest.TestCase):
    """Verifica que la política de sesión H4 esté documentada en las superficies clave."""

    def test_runtime_core_has_h4_rule(self):
        content = (ROOT / ".agent" / "rules" / "runtime_core.md").read_text(encoding="utf-8")
        self.assertIn("Si prevés ausentarte más de ~1 h, cierra con `/gycd`", content)
        # Normalizar espacios para comprobar la frase multilínea
        normalized = " ".join(content.split())
        self.assertIn("Reanudar en frío una sesión larga cuesta 4–11× más que arrancar", normalized)

    def test_gycd_has_h4_continuation_advice(self):
        content = (ROOT / ".agent" / "workflows" / "gycd.md").read_text(encoding="utf-8")
        self.assertIn("Si prevés ausentarte más de ~1 h, cierra con `/gycd`; al volver, sesión nueva pegando el `Siguiente:`", content)

    def test_arrancar_has_h4_session_policy_note(self):
        content = (ROOT / ".agent" / "workflows" / "arrancar.md").read_text(encoding="utf-8")
        self.assertIn("Política de sesión (H4):", content)
        self.assertIn("si prevés ausentarte más de ~1 h, cierra con `/gycd`; al volver, sesión nueva pegando el `Siguiente:`", content)


class TestL6H5PropagateFramework(unittest.TestCase):
    """Verifica la detección de sesiones de Claude Code abiertas en propagate_framework.py."""

    def test_claude_project_slug_generation(self):
        """Verifica que el slug coincida con la convención de ~/.claude/projects/."""
        # / y espacios deben sustituirse por '-'
        path_spaces = Path("/path/to/my project with spaces")
        self.assertEqual(
            propagate.claude_project_slug(path_spaces),
            "-path-to-my-project-with-spaces",
        )
        # proyecto sintético (-Users-developer-Projects--framework)
        path_fw = Path("/Users/developer/Projects/_framework")
        self.assertEqual(
            propagate.claude_project_slug(path_fw),
            "-Users-developer-Projects--framework",
        )

    def test_check_open_claude_session_no_base_dir(self):
        """Si el directorio ~/.claude/projects/ no existe, procede sin bloquear."""
        with tempfile.TemporaryDirectory() as tmp:
            no_dir = Path(tmp) / "non_existent_claude_projects"
            has_open, reason = propagate.check_open_claude_session(
                Path("/any/project"), claude_projects_dir=no_dir
            )
            self.assertFalse(has_open)
            self.assertEqual(reason, "")

    def test_check_open_claude_session_no_slug_dir(self):
        """Si no existe el directorio para el slug del proyecto, procede sin bloquear."""
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp) / "claude_projects"
            base.mkdir()
            has_open, reason = propagate.check_open_claude_session(
                Path("/any/project"), claude_projects_dir=base
            )
            self.assertFalse(has_open)
            self.assertEqual(reason, "")

    def test_check_open_claude_session_old_jsonl_proceeds(self):
        """Si los *.jsonl tienen más de 60 min, procede sin bloquear."""
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp) / "claude_projects"
            project_path = Path(tmp) / "project_x"
            project_path.mkdir()
            slug = propagate.claude_project_slug(project_path)
            slug_dir = base / slug
            slug_dir.mkdir(parents=True)

            transcript = slug_dir / "old_session.jsonl"
            transcript.write_text("{}\n", encoding="utf-8")
            # 2 horas atrás
            mtime = time.time() - 7200
            os.utime(transcript, (mtime, mtime))

            has_open, reason = propagate.check_open_claude_session(
                project_path, claude_projects_dir=base
            )
            self.assertFalse(has_open)
            self.assertEqual(reason, "")

    def test_check_open_claude_session_recent_jsonl_refuses(self):
        """Si existe algún *.jsonl con menos de 60 min, detecta la sesión activa."""
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp) / "claude_projects"
            project_path = Path(tmp) / "project_x"
            project_path.mkdir()
            slug = propagate.claude_project_slug(project_path)
            slug_dir = base / slug
            slug_dir.mkdir(parents=True)

            transcript = slug_dir / "active_session.jsonl"
            transcript.write_text("{}\n", encoding="utf-8")
            # 10 minutos atrás
            mtime = time.time() - 600
            os.utime(transcript, (mtime, mtime))

            has_open, reason = propagate.check_open_claude_session(
                project_path, claude_projects_dir=base
            )
            self.assertTrue(has_open)
            self.assertIn("sesión de Claude Code activa", reason)
            self.assertIn("active_session.jsonl", reason)

    def test_apply_upgrade_refuses_when_session_is_open(self):
        """apply_upgrade rechaza el upgrade si hay sesión activa y no se pasa --allow-open-session."""
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp) / "claude_projects"
            project_path = Path(tmp) / "project_target"
            project_path.mkdir()
            slug = propagate.claude_project_slug(project_path)
            slug_dir = base / slug
            slug_dir.mkdir(parents=True)

            transcript = slug_dir / "active.jsonl"
            transcript.write_text("{}\n", encoding="utf-8")
            mtime = time.time() - 120
            os.utime(transcript, (mtime, mtime))

            report = {
                "project": "project_target",
                "path": str(project_path),
                "declared": "4.0.0",
                "target": "5.1.0",
            }

            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                ok = propagate.apply_upgrade(
                    report, allow_open_session=False, claude_projects_dir=base
                )

            self.assertFalse(ok)
            stdout = out.getvalue()
            self.assertIn("actualización rechazada", stdout)
            self.assertIn("sesión de Claude Code activa detectada", stdout)
            self.assertIn("--allow-open-session", stdout)

    def test_apply_upgrade_proceeds_with_allow_open_session(self):
        """apply_upgrade ignora la sesión activa si se pasa allow_open_session=True."""
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp) / "claude_projects"
            project_path = Path(tmp) / "project_target"
            project_path.mkdir()
            (project_path / ".agent").mkdir()
            (project_path / "_project").mkdir()
            (project_path / "_project" / "03_project_state.json").write_text(
                json.dumps({"framework_version": "4.0.0"}), encoding="utf-8"
            )
            slug = propagate.claude_project_slug(project_path)
            slug_dir = base / slug
            slug_dir.mkdir(parents=True)

            transcript = slug_dir / "active.jsonl"
            transcript.write_text("{}\n", encoding="utf-8")
            mtime = time.time() - 120
            os.utime(transcript, (mtime, mtime))

            report = {
                "project": "project_target",
                "path": str(project_path),
                "declared": "4.0.0",
                "target": "5.1.0",
            }

            with mock.patch.object(propagate, "_apply_after_backup", return_value=True), \
                 mock.patch("shutil.copytree"), \
                 mock.patch("subprocess.run", return_value=mock.Mock(returncode=0, stdout=json.dumps({"status": "NOT_APPLICABLE"}))):
                ok = propagate.apply_upgrade(
                    report, allow_open_session=True, claude_projects_dir=base
                )
            self.assertTrue(ok)

    def test_actualizar_framework_workflow_has_h5_rule(self):
        """Verifica que actualizar-framework.md tenga la regla H5 sobre no propagar con sesión abierta."""
        content = (ROOT / ".agent" / "workflows" / "actualizar-framework.md").read_text(encoding="utf-8")
        self.assertIn("no propagar sobre un proyecto con una sesión abierta", content.lower())
        self.assertIn("cambiar las herramientas mcp invalida la caché completa de esa sesión", content.lower())


if __name__ == "__main__":
    unittest.main()
