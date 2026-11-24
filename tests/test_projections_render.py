#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Render de proyecciones — resolución de tier del próximo módulo (FW-039).

Cuando no hay módulo activo (`modules.in_progress == []`) pero el próximo
módulo ya está clasificado en `work_tiers` (p. ej. M0 = tier_2), el render
mostraba el tier del módulo activo ("ninguno" → "no clasificado") y nunca
resolvía el tier canónico del próximo. Un agente que siguiera core.md podía
implementar un cambio tier_2 saltándose su rigor (ADR, rollback, 2ª revisión).
El render debe proyectar el tier canónico del próximo módulo desde work_tiers.
"""
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / ".agent" / "scripts"))
import state_core
import projections


class TestRenderNextModuleTier(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        (root / "_project").mkdir(parents=True)
        self._orig = {k: getattr(state_core, k) for k in
                      ["WORKSPACE_ROOT", "CORE_FILE_PATH", "BUGS_FILE_PATH",
                       "CLARIFICATIONS_FILE_PATH", "DECISIONS_FILE_PATH", "SESSION_BRIEF_PATH"]}
        state_core.WORKSPACE_ROOT = root
        state_core.CORE_FILE_PATH = root / "_project" / "core.md"
        state_core.BUGS_FILE_PATH = root / "_project" / "06_bug_fixes.md"
        state_core.CLARIFICATIONS_FILE_PATH = root / "_project" / "07_pending_clarifications.md"
        state_core.DECISIONS_FILE_PATH = root / "_project" / "03_arch_decisions.md"
        state_core.SESSION_BRIEF_PATH = root / "_project" / "00_session_brief.json"
        manifest_dir = root / ".agent" / "workflows"
        manifest_dir.mkdir(parents=True, exist_ok=True)
        (manifest_dir / "manifest.json").write_text(
            json.dumps({
                "commands": [
                    {"name": "ciclo", "builder_facing": True},
                    {"name": "arrancar", "builder_facing": True},
                ]
            }),
            encoding="utf-8",
        )

    def tearDown(self):
        for k, v in self._orig.items():
            setattr(state_core, k, v)
        self.tmp.cleanup()

    def _state_no_active_next_tier2(self):
        return {
            "project": "P", "current_phase": "phase_01",
            "modules": {"in_progress": [], "completed": [], "pending": ["M0_x"]},
            "module_statuses": {"M0_x": "not_started"},
            "work_tiers": {"M0_x": {"tier": "tier_2", "rationale": "destructivo",
                                    "classified_at": "t", "classified_by": "b"}},
            "session": {"next_module": "M0_x", "next_action": "implementar M0_x"},
            "_bugs": [], "_clarifications": [], "_decisions": [],
        }

    def test_core_md_exposes_next_module_tier(self):
        projections.render(self._state_no_active_next_tier2())
        core = state_core.CORE_FILE_PATH.read_text(encoding="utf-8")
        self.assertIn("tier_2", core,
                      "core.md debe exponer el tier canónico del próximo módulo cuando no hay activo")

    def test_brief_exposes_next_module_and_tier(self):
        projections.render(self._state_no_active_next_tier2())
        brief = json.loads(state_core.SESSION_BRIEF_PATH.read_text(encoding="utf-8"))
        self.assertEqual(brief.get("next_module"), "M0_x")
        self.assertEqual(brief.get("next_work_tier"), "tier_2")

    def test_core_keeps_command_and_prompt_in_one_next_line(self):
        state = self._state_no_active_next_tier2()
        state["session"].update({
            "next_command": "/ciclo",
            "next_action": "Implementar M0_x con sus pruebas y gates.",
        })

        projections.render(state)

        core = state_core.CORE_FILE_PATH.read_text(encoding="utf-8")
        self.assertIn(
            "Siguiente: /ciclo Implementar M0_x con sus pruebas y gates.",
            core,
        )
        self.assertEqual(core.count("Siguiente:"), 1)
        self.assertNotIn("## Siguiente comando", core)
        self.assertNotIn("Prompt sugerido:", core)

    def test_core_rejects_legacy_prompt_embedded_in_next_command(self):
        state = self._state_no_active_next_tier2()
        state["session"].update({
            "next_command": "/enriquecer-ticket — detallar HU antes de implementar",
            "next_action": "Implementar M0_x con sus pruebas y gates.",
        })

        with self.assertRaisesRegex(ValueError, "next_command"):
            projections.render(state)

    def test_core_formats_blocking_sentinel_with_prompt(self):
        state = self._state_no_active_next_tier2()
        state["session"].update({
            "next_command": "ninguno",
            "next_action": "Resolver el bloqueo registrado.",
        })

        projections.render(state)

        core = state_core.CORE_FILE_PATH.read_text(encoding="utf-8")
        self.assertIn("Siguiente: ninguno — Resolver el bloqueo registrado.", core)
        self.assertEqual(core.count("Siguiente:"), 1)


class TestActiveVsNextModuleSeparation(unittest.TestCase):
    """FW-095 (Research App FW-043 reincidente): el render no debe aplicar
    la descripción del próximo módulo al módulo activo/bloqueado actual."""

    def _base_state(self):
        return {
            "project": "demo",
            "current_phase": "phase_02",
            "last_completed_step": "cierre de L2",
            "modules": {"completed": ["L2"], "in_progress": ["S4"], "pending": ["L3"]},
            "module_statuses": {"L2": "completed", "S4": "blocked", "L3": "not_started"},
            "work_tiers": {}, "module_specs": {},
            "_bugs": [], "_clarifications": [], "_decisions": [],
            "session": {
                "active_module_description": "Expediente local que conecta captura y decisión.",
                "described_module": "L3",
                "next_module": "L3",
                "next_module_description": "Expediente local que conecta captura y decisión.",
                "next_action": "Abrir L3 vía /scope-siguiente.",
            },
        }

    def test_stale_description_not_applied_to_active_module(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "_project").mkdir()
            saved = {k: getattr(state_core, k) for k in
                     ["WORKSPACE_ROOT", "CORE_FILE_PATH", "BUGS_FILE_PATH",
                      "CLARIFICATIONS_FILE_PATH", "DECISIONS_FILE_PATH", "SESSION_BRIEF_PATH"]}
            try:
                state_core.WORKSPACE_ROOT = root
                state_core.CORE_FILE_PATH = root / "_project" / "core.md"
                state_core.BUGS_FILE_PATH = root / "_project" / "06_bug_fixes.md"
                state_core.CLARIFICATIONS_FILE_PATH = root / "_project" / "07_pending_clarifications.md"
                state_core.DECISIONS_FILE_PATH = root / "_project" / "03_arch_decisions.md"
                state_core.SESSION_BRIEF_PATH = root / "_project" / "00_session_brief.json"
                projections.render(self._base_state())
                core = state_core.CORE_FILE_PATH.read_text(encoding="utf-8")
            finally:
                for k, v in saved.items():
                    setattr(state_core, k, v)
        # El bloque de módulo activo (S4) no debe llevar la descripción de L3
        activo = core.split("## Módulo activo")[1].split("---")[0]
        self.assertIn("S4", activo)
        self.assertNotIn("Expediente local", activo,
                         "descripción de L3 aplicada al módulo activo S4")
        self.assertIn("corresponde a `L3`", activo)
        # Y el próximo módulo sí la lleva, en su propia sección
        proximo = core.split("## Próximo módulo")[1] if "## Próximo módulo" in core else core.split(activo)[1]
        self.assertIn("Expediente local", proximo)


class TestSessionPointerStamping(unittest.TestCase):
    """FW-095: set_session_pointer sella a qué módulo pertenece la descripción
    y avisa cuando el activo está blocked."""

    def _manager(self, state):
        m = state_core.StateManager.__new__(state_core.StateManager)
        m.state_data = state
        return m

    def test_pointer_stamps_described_module_and_warns_on_blocked(self):
        state = {
            "modules": {"completed": [], "in_progress": ["S4"], "pending": []},
            "module_statuses": {"S4": "blocked"},
            "session": {},
        }
        m = self._manager(state)
        msg = m._apply_event("set_session_pointer",
                             {"active_module_description": "desc de otro módulo"},
                             "2026-07-11T00:00:00Z")
        self.assertEqual(state["session"]["described_module"], "S4")
        self.assertIn("blocked", msg)
        self.assertIn("next_module_description", msg)

    def test_pointer_accepts_next_module_description(self):
        state = {"modules": {"completed": [], "in_progress": [], "pending": []},
                 "module_statuses": {}, "session": {}}
        m = self._manager(state)
        m._apply_event("set_session_pointer",
                       {"next_module": "L3", "next_module_description": "expediente"},
                       "2026-07-11T00:00:00Z")
        self.assertEqual(state["session"]["next_module_description"], "expediente")


class TestNoTrailingWhitespaceInProjections(unittest.TestCase):
    """TL FW-018 / BOTÓN FW-082 (reabierto): campos opcionales vacíos
    (fix/options/impact sin completar) dejaban un espacio colgando tras
    '**Label:**', rompiendo `git diff --check` en cada commit que tocara
    06_bug_fixes.md o 07_pending_clarifications.md."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        (root / "_project").mkdir(parents=True)
        self._orig = {k: getattr(state_core, k) for k in
                      ["WORKSPACE_ROOT", "CORE_FILE_PATH", "BUGS_FILE_PATH",
                       "CLARIFICATIONS_FILE_PATH", "DECISIONS_FILE_PATH", "SESSION_BRIEF_PATH"]}
        state_core.WORKSPACE_ROOT = root
        state_core.CORE_FILE_PATH = root / "_project" / "core.md"
        state_core.BUGS_FILE_PATH = root / "_project" / "06_bug_fixes.md"
        state_core.CLARIFICATIONS_FILE_PATH = root / "_project" / "07_pending_clarifications.md"
        state_core.DECISIONS_FILE_PATH = root / "_project" / "03_arch_decisions.md"
        state_core.SESSION_BRIEF_PATH = root / "_project" / "00_session_brief.json"

    def tearDown(self):
        for k, v in self._orig.items():
            setattr(state_core, k, v)
        self.tmp.cleanup()

    def _state_with_empty_optional_fields(self):
        return {
            "project": "P", "current_phase": "phase_01",
            "modules": {"in_progress": ["M1"], "completed": [], "pending": []},
            "module_statuses": {"M1": "in_progress"},
            "work_tiers": {}, "session": {},
            "_bugs": [{
                "id": "BUG-001", "module_id": "M1", "severity": "Menor",
                "description": "algo menor", "affects_ac": False,
                "status": "Abierto", "fix": "", "affected_files": [],
            }],
            "_clarifications": [{
                "id": "CLAR-001", "title": "duda", "blocked_scope": "M1",
                "blocks_start": False, "blocks_implementation": False,
                "blocks_closure": False, "blocking_condition": "",
                "question": "¿qué hacer?", "options": "", "impact": "",
                "status": "Abierta", "answer": "",
            }],
            "_decisions": [],
        }

    def test_no_trailing_whitespace_in_any_rendered_projection(self):
        projections.render(self._state_with_empty_optional_fields())
        for path in (
            state_core.CORE_FILE_PATH,
            state_core.BUGS_FILE_PATH,
            state_core.CLARIFICATIONS_FILE_PATH,
            state_core.DECISIONS_FILE_PATH,
        ):
            text = path.read_text(encoding="utf-8")
            offending = [
                (i, line) for i, line in enumerate(text.split("\n"), start=1)
                if line != line.rstrip()
            ]
            self.assertEqual(
                offending, [],
                f"{path.name} tiene líneas con espacios finales: {offending}",
            )


class TestAmendmentAndSupersedeAreVisibleInProjections(unittest.TestCase):
    """Ronda de revisión final (hallazgo 5): `amend_bug` (FW-122) y
    `supersede_decision` (FW-125) dejan rastro en el estado, pero las
    proyecciones de solo lectura (06_bug_fixes.md, 03_arch_decisions.md) no lo
    mostraban. La Regla 2 del framework prohíbe leer el JSON crudo para
    reconstruir contexto — si el rastro no está en la proyección, es como si
    no existiera para un agente que siga las reglas."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        (root / "_project").mkdir(parents=True)
        self._orig = {k: getattr(state_core, k) for k in
                      ["WORKSPACE_ROOT", "CORE_FILE_PATH", "BUGS_FILE_PATH",
                       "CLARIFICATIONS_FILE_PATH", "DECISIONS_FILE_PATH", "SESSION_BRIEF_PATH"]}
        state_core.WORKSPACE_ROOT = root
        state_core.CORE_FILE_PATH = root / "_project" / "core.md"
        state_core.BUGS_FILE_PATH = root / "_project" / "06_bug_fixes.md"
        state_core.CLARIFICATIONS_FILE_PATH = root / "_project" / "07_pending_clarifications.md"
        state_core.DECISIONS_FILE_PATH = root / "_project" / "03_arch_decisions.md"
        state_core.SESSION_BRIEF_PATH = root / "_project" / "00_session_brief.json"

    def tearDown(self):
        for k, v in self._orig.items():
            setattr(state_core, k, v)
        self.tmp.cleanup()

    def _state(self):
        return {
            "project": "P", "current_phase": "phase_01",
            "modules": {"in_progress": ["M1"], "completed": [], "pending": []},
            "module_statuses": {"M1": "in_progress"},
            "work_tiers": {}, "session": {},
            "_bugs": [{
                "id": "BUG-001", "module_id": "M1", "severity": "Menor",
                "description": "la cobertura cayó a 12 tests", "affects_ac": False,
                "status": "Abierto", "fix": "", "affected_files": [],
                "amendments": [{
                    "at": "2026-08-20T10:00:00+00:00",
                    "reason": "la cifra 23 vino de un subagente y no se reprodujo",
                    "previous": {"description": "la cobertura cayó a 23 tests"},
                }],
            }],
            "_clarifications": [],
            "_decisions": [
                {"id": "ADR-001", "title": "mecanismo v1", "date": "2026-08-01",
                 "status": "Superseded", "module_id": "M1", "context": "c",
                 "decision": "d", "alternatives": "", "consequences": "",
                 "superseded_by": "ADR-002",
                 "superseded_at": "2026-08-20T10:00:00+00:00",
                 "supersede_reason": "el mecanismo v1 se sustituyo entero"},
                {"id": "ADR-002", "title": "mecanismo v2", "date": "2026-08-20",
                 "status": "Aceptada", "module_id": "M1", "context": "c",
                 "decision": "d", "alternatives": "", "consequences": ""},
            ],
        }

    def test_bug_amendments_are_rendered_in_bugs_projection(self):
        projections.render(self._state())
        text = state_core.BUGS_FILE_PATH.read_text(encoding="utf-8")
        self.assertIn("2026-08-20T10:00:00+00:00", text)
        self.assertIn("la cifra 23 vino de un subagente y no se reprodujo", text)
        self.assertIn("description", text)

    def test_superseded_adr_shows_replacement_pointer_in_decisions_projection(self):
        projections.render(self._state())
        text = state_core.DECISIONS_FILE_PATH.read_text(encoding="utf-8")
        self.assertIn("Superseded por:** ADR-002", text)
        self.assertIn("el mecanismo v1 se sustituyo entero", text)


if __name__ == "__main__":
    unittest.main()
