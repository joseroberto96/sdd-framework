# -*- coding: utf-8 -*-
"""Tests de wiring y mutación del modo foco (3.12.0).

Cubre:
- validate_focus_wiring (doctor) — caso positivo (repo limpio), negativo
  (sin foco en manifest, sin workflow, sin subsección, skill del mapa
  inexistente).
- set_focus — muta y lee; null limpia el foco; proyección de core.md
  muestra "Foco activo: <área>" o "sin foco" según el estado; state
  sin active_focus sigue validando (retrocompatibilidad).
"""
import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / ".agent" / "scripts"))
import state_core  # noqa: E402
import state_manager  # noqa: E402


def _load_doctor():
    path = ROOT / ".agent" / "scripts" / "framework_doctor.py"
    spec = importlib.util.spec_from_file_location("framework_doctor", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _load_manifest() -> dict:
    return json.loads((ROOT / ".agent" / "workflows" / "manifest.json").read_text(encoding="utf-8"))


class TestFocusWiring(unittest.TestCase):
    def setUp(self):
        self.doctor = _load_doctor()

    def _validate(self, manifest: dict, errors: list[str]) -> None:
        old_root = self.doctor.ROOT
        try:
            self.doctor.ROOT = ROOT
            self.doctor.validate_focus_wiring(manifest, errors)
        finally:
            self.doctor.ROOT = old_root

    def test_wiring_passes_on_real_repo(self):
        errors: list[str] = []
        self._validate(_load_manifest(), errors)
        self.assertEqual(errors, [], f"el repo real debe estar wired: {errors}")

    def test_wiring_fails_when_foco_missing_from_manifest(self):
        manifest = {"commands": []}
        errors: list[str] = []
        self._validate(manifest, errors)
        self.assertTrue(
            any("foco no está declarado" in e for e in errors),
            f"debe fallar sin /foco en manifest; got {errors}",
        )

    def test_wiring_fails_when_foco_claude_command_false(self):
        manifest = {"commands": [{"name": "foco", "source": ".agent/workflows/foco.md", "claude_command": False}]}
        errors: list[str] = []
        self._validate(manifest, errors)
        self.assertTrue(
            any("claude_command:true" in e for e in errors),
            f"debe exigir claude_command:true; got {errors}",
        )

    def test_wiring_fails_when_foco_source_wrong(self):
        manifest = {"commands": [{"name": "foco", "source": ".agent/workflows/otro.md", "claude_command": True}]}
        errors: list[str] = []
        self._validate(manifest, errors)
        self.assertTrue(
            any("foco.source inesperado" in e for e in errors),
            f"debe exigir source=.agent/workflows/foco.md; got {errors}",
        )

    def test_wiring_fails_when_mapa_skill_missing(self):
        """Si una skill del mapa área→skill deja de existir, validate_focus_wiring
        debe fallar. Simulado renombrando temporalmente cross_platform_web.md."""
        manifest = _load_manifest()
        skill_path = ROOT / ".agent" / "skills" / "cross_platform_web.md"
        backup = skill_path.read_text(encoding="utf-8")
        try:
            skill_path.unlink()
            errors: list[str] = []
            self._validate(manifest, errors)
            self.assertTrue(
                any("cross_platform_web" in e and "inexistente" in e for e in errors),
                f"debe fallar cuando una skill del mapa desaparece; got {errors}",
            )
        finally:
            skill_path.write_text(backup, encoding="utf-8")


class TestSetFocusMutation(unittest.TestCase):
    """Acción set_focus: muta state, persiste, null limpia, retrocompat."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.tmp_root = Path(self.tmp.name)
        (self.tmp_root / "_project").mkdir(parents=True, exist_ok=True)

        self.orig_state = state_core.STATE_FILE_PATH
        self.orig_core = state_core.CORE_FILE_PATH
        self.orig_bugs = state_core.BUGS_FILE_PATH
        self.orig_clars = state_core.CLARIFICATIONS_FILE_PATH
        self.orig_decs = state_core.DECISIONS_FILE_PATH
        self.orig_brief = state_core.SESSION_BRIEF_PATH
        self.orig_root = state_core.WORKSPACE_ROOT

        state_core.STATE_FILE_PATH = self.tmp_root / "_project" / "03_project_state.json"
        state_core.CORE_FILE_PATH = self.tmp_root / "_project" / "core.md"
        state_core.BUGS_FILE_PATH = self.tmp_root / "_project" / "06_bug_fixes.md"
        state_core.CLARIFICATIONS_FILE_PATH = self.tmp_root / "_project" / "07_pending_clarifications.md"
        state_core.DECISIONS_FILE_PATH = self.tmp_root / "_project" / "03_arch_decisions.md"
        state_core.SESSION_BRIEF_PATH = self.tmp_root / "_project" / "00_session_brief.json"
        state_core.WORKSPACE_ROOT = self.tmp_root

        self.base = {
            "project": "TestFoco",
            "framework_mode": "template",
            "framework_version": "3.12.0",
            "current_phase": "phase_01",
            "current_step": "init",
            "last_completed_step": "Inicio",
            "stack": {"backend": "", "frontend": "", "database": "", "auth": ""},
            "dependencies": [],
            "architecture_constraints": [],
            "contracts": {"public_interfaces": {}},
            "modules": {"completed": [], "in_progress": [], "pending": []},
            "module_statuses": {},
            "work_tiers": {},
            "module_specs": {},
            "closure_rejections": [],
            "active_mcps": [],
            "_refs": {},
        }
        with open(state_core.STATE_FILE_PATH, "w", encoding="utf-8") as f:
            json.dump(self.base, f, indent=2, sort_keys=True)

        self.manager = state_manager.StateManager()

    def tearDown(self):
        state_core.STATE_FILE_PATH = self.orig_state
        state_core.CORE_FILE_PATH = self.orig_core
        state_core.BUGS_FILE_PATH = self.orig_bugs
        state_core.CLARIFICATIONS_FILE_PATH = self.orig_clars
        state_core.DECISIONS_FILE_PATH = self.orig_decs
        state_core.SESSION_BRIEF_PATH = self.orig_brief
        state_core.WORKSPACE_ROOT = self.orig_root
        self.tmp.cleanup()

    def test_set_focus_persists_and_renders(self):
        msg = self.manager.mutate("set_focus", {"area": "frontend"})
        self.assertIn("frontend", msg)
        self.assertEqual(self.manager.state_data["active_focus"], "frontend")

        core_text = state_core.CORE_FILE_PATH.read_text(encoding="utf-8")
        self.assertIn("Foco activo**: frontend", core_text)

    def test_set_focus_null_clears_focus(self):
        self.manager.mutate("set_focus", {"area": "backend"})
        self.assertEqual(self.manager.state_data["active_focus"], "backend")

        self.manager.mutate("set_focus", {"area": None})
        self.assertIsNone(self.manager.state_data["active_focus"])

        core_text = state_core.CORE_FILE_PATH.read_text(encoding="utf-8")
        self.assertIn("Foco activo**: sin foco", core_text)

    def test_set_focus_rejects_empty_string(self):
        """Strict contract: payload {'area': 'X'} o {'area': null}. Vacío
        es un error del caller (no se normaliza a null silenciosamente: el
        caller debe ser explícito si quiere limpiar)."""
        with self.assertRaises(ValueError):
            self.manager.mutate("set_focus", {"area": ""})

    def test_set_focus_rejects_non_string_non_null(self):
        with self.assertRaises(ValueError):
            self.manager.mutate("set_focus", {"area": 42})
        with self.assertRaises(ValueError):
            self.manager.mutate("set_focus", {"area": []})

    def test_set_focus_rejects_wrong_key_instead_of_clearing_silently(self):
        # FW-112 (Botón, known-bad): {"focus": ...} (clave equivocada) leía
        # 'area' como ausente y limpiaba el foco en silencio — ahora rechaza.
        self.manager.mutate("set_focus", {"area": "backend"})
        with self.assertRaises(ValueError):
            self.manager.mutate("set_focus", {"focus": "frontend"})
        self.assertEqual(self.manager.state_data["active_focus"], "backend")

    def test_set_focus_with_explicit_area_null_still_clears(self):
        # known-good: la clave correcta, explícita, sigue limpiando.
        self.manager.mutate("set_focus", {"area": "backend"})
        self.manager.mutate("set_focus", {"area": None})
        self.assertIsNone(self.manager.state_data["active_focus"])

    def test_state_without_active_focus_still_validates(self):
        """Retrocompatibilidad: un state sin el campo (proyectos pre-3.12) debe
        seguir validando y la proyección debe renderizar 'sin foco'."""
        with open(state_core.STATE_FILE_PATH, "w", encoding="utf-8") as f:
            json.dump(self.base, f, indent=2, sort_keys=True)
        mgr = state_manager.StateManager()
        mgr.validate_state(mgr.state_data)

        import projections  # import local para no contaminar imports globales
        projections.render(mgr.state_data)
        core_text = state_core.CORE_FILE_PATH.read_text(encoding="utf-8")
        self.assertIn("Foco activo**: sin foco", core_text)


if __name__ == "__main__":
    unittest.main()
