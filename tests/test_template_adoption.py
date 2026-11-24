#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Regresión: una adopción nueva (proyecciones enviadas por el template +
bootstrap/render) NO debe disparar el guard de proyecciones.

Cubre el bug donde el template enviaba `core.md` / `06_bug_fixes.md` /
`07_pending_clarifications.md` / `03_arch_decisions.md` con headers manuales
SIN el banner de autogeneración: `/nuevo-proyecto` → `bootstrap` (que llama a
`render`) fallaba con ProjectionGuardError sobre esos archivos preexistentes.
"""
import unittest
import shutil
import tempfile
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / ".agent" / "scripts"))
import state_core
import projections

PROJ = ROOT / "_project"
PROJECTION_MD = [
    "core.md",
    "06_bug_fixes.md",
    "07_pending_clarifications.md",
    "03_arch_decisions.md",
]


class TestTemplateAdoption(unittest.TestCase):
    def test_shipped_projections_carry_banner(self):
        """Cada proyección Markdown enviada por el template empieza con el banner."""
        for name in PROJECTION_MD:
            first_line = (PROJ / name).read_text(encoding="utf-8").splitlines()[0]
            self.assertIn(
                projections.BANNER,
                first_line,
                f"{name} debe enviarse con el banner de autogeneración "
                f"o bootstrap/render fallará en una adopción nueva",
            )

    def test_adoption_bootstrap_render_does_not_trip_guard(self):
        """Copiar las proyecciones enviadas + render (como hace bootstrap) no lanza."""
        tmp = tempfile.TemporaryDirectory()
        try:
            root = Path(tmp.name)
            (root / "_project").mkdir(parents=True)
            for name in PROJECTION_MD + [
                "00_session_brief.json",
                "03_project_state.json",
                "08_module_acceptance.md",
            ]:
                src = PROJ / name
                if src.exists():
                    shutil.copy(src, root / "_project" / name)
            # Un bootstrap real siempre trae `.agent/` consigo. Sin el manifest,
            # `validate_session_pointer` falla cerrado en cuanto el estado copiado
            # trae un `next_command` pendiente que validar (p. ej. el de un
            # proyecto real ya en marcha, a diferencia del estado sin sesión
            # activa de la propia plantilla) — no es el guard de proyecciones que
            # este test cubre.
            (root / ".agent" / "workflows").mkdir(parents=True)
            shutil.copy(
                ROOT / ".agent" / "workflows" / "manifest.json",
                root / ".agent" / "workflows" / "manifest.json",
            )

            orig = {
                k: getattr(state_core, k)
                for k in [
                    "WORKSPACE_ROOT", "STATE_FILE_PATH", "CORE_FILE_PATH",
                    "BUGS_FILE_PATH", "CLARIFICATIONS_FILE_PATH",
                    "DECISIONS_FILE_PATH", "SESSION_BRIEF_PATH",
                ]
            }
            try:
                state_core.WORKSPACE_ROOT = root
                state_core.STATE_FILE_PATH = root / "_project" / "03_project_state.json"
                state_core.CORE_FILE_PATH = root / "_project" / "core.md"
                state_core.BUGS_FILE_PATH = root / "_project" / "06_bug_fixes.md"
                state_core.CLARIFICATIONS_FILE_PATH = root / "_project" / "07_pending_clarifications.md"
                state_core.DECISIONS_FILE_PATH = root / "_project" / "03_arch_decisions.md"
                state_core.SESSION_BRIEF_PATH = root / "_project" / "00_session_brief.json"

                mgr = state_core.StateManager()
                mgr.validate_state(mgr.state_data)
                projections.render(mgr.state_data)  # debe NO lanzar ProjectionGuardError
            finally:
                for k, v in orig.items():
                    setattr(state_core, k, v)
        finally:
            tmp.cleanup()


if __name__ == "__main__":
    unittest.main()
