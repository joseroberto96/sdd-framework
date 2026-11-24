#!/usr/bin/env python3
"""5.3.0 L3: plantillas de cierre en documentos (D1, un solo validador).

Cubre el escaneo `turn_close.scan_templates`, su uso en el doctor
(`validate_flow_guidance`), la proyección `core.md` (puntero de sesión) y el
ejemplo del contrato de acciones `set_session_pointer`.

Dos tests miden los workflows reales del repo y pasan solo cuando los workflows
están corregidos (cero defectos): `test_real_workflows_have_zero_template_defects`
y `test_gycd_does_not_compose_a_double_slash_command`.
"""
from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / ".agent" / "scripts"))

import action_contracts  # noqa: E402
import projections  # noqa: E402
import state_core  # noqa: E402
import turn_close as tc  # noqa: E402

FIXTURE = ROOT / "tests" / "fixtures" / "turn_close_templates_bad.json"
BUILDER = ["arrancar", "reconciliar", "gycd", "diseñar", "nuevo-bug", "nuevo-proyecto",
           "migrar-proyecto", "actualizar-framework", "ciclo"]
MANIFEST = {"commands": [{"name": n, "builder_facing": True} for n in BUILDER]
            + [{"name": "tdd", "builder_facing": False}, {"name": "cierre-modulo", "builder_facing": False}]}
MODEL = "Modelo: standard · Esfuerzo: 🟡 medio (tier_1)"


def load_doctor():
    spec = importlib.util.spec_from_file_location(
        "framework_doctor_l3", ROOT / ".agent" / "scripts" / "framework_doctor.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def codes(findings: list[tc.TemplateFinding]) -> list[list[str]]:
    return [f.codes for f in findings]


class ScanTemplatesTests(unittest.TestCase):
    def scan(self, text: str) -> list[tc.TemplateFinding]:
        return tc.scan_templates(text, manifest=MANIFEST)

    def test_valid_command_template_with_model_line_has_no_defects(self) -> None:
        found = self.scan(f"# W\n\nSiguiente: /ciclo <instrucción concreta>\n{MODEL}\n")
        self.assertEqual(len(found), 1)
        self.assertEqual((found[0].line, found[0].text), (3, "Siguiente: /ciclo <instrucción concreta>"))
        self.assertEqual(found[0].codes, [])
        self.assertFalse(found[0].defective)

    def test_command_template_without_model_line_is_sin_modelo_error(self) -> None:
        found = self.scan("Siguiente: /ciclo Cerrar el módulo X\n\nOtro texto\n")
        self.assertEqual(codes(found), [["SIN_MODELO"]])
        self.assertEqual(found[0].errors[0].severity, tc.SEVERITY_ERROR)
        self.assertEqual(found[0].errors[0].line, 1)

    def test_model_line_may_follow_after_blank_lines_and_decorations(self) -> None:
        found = self.scan("Siguiente: /ciclo Cerrar X\n\n> **Modelo:** standard · Esfuerzo: 🟡 medio (tier_1)\n")
        self.assertEqual(codes(found), [[]])

    def test_ciclo_fase_requires_model_line(self) -> None:
        self.assertEqual(codes(self.scan("Siguiente: /ciclo fase\n")), [["SIN_MODELO"]])
        self.assertEqual(codes(self.scan(f"Siguiente: /ciclo fase --paralelo\n{MODEL}\n")), [[]])

    def test_ninguno_and_decision_forms_do_not_require_model_line(self) -> None:
        text = "Siguiente: ninguno — sin pasos\n\nSiguiente: decisión — elige A o B en el chat\n"
        self.assertEqual(codes(self.scan(text)), [[], []])

    def test_template_inside_fence_is_en_bloque_de_codigo(self) -> None:
        text = f"```\nSiguiente: /ciclo Hacer X\n{MODEL}\n```\n"
        found = self.scan(text)
        self.assertEqual((found[0].line, found[0].codes), (2, ["EN_BLOQUE_DE_CODIGO"]))

    def test_fences_alternate_and_tilde_and_quoted_fences_count(self) -> None:
        text = (
            "~~~\nSiguiente: ninguno — a\n~~~\n"
            "Siguiente: ninguno — b\n"
            "> ```md\n> Siguiente: ninguno — c\n> ```\n"
        )
        self.assertEqual([(f.line, f.codes) for f in self.scan(text)],
                         [(2, ["EN_BLOQUE_DE_CODIGO"]), (4, []), (6, ["EN_BLOQUE_DE_CODIGO"])])

    def test_prose_mention_is_not_a_template_but_is_a_mention(self) -> None:
        text = "La línea `Siguiente:` es obligatoria.\n"
        self.assertEqual(self.scan(text), [])
        self.assertEqual(tc.template_mentions(text), [1])
        self.assertEqual(tc.template_mentions("Siguiente: ninguno — x\n"), [])

    def test_contract_defects_are_reported_with_document_line(self) -> None:
        found = self.scan(f"# W\n\n\nSiguiente: `/tdd` Aplicar el cambio.\n{MODEL}\n")
        self.assertEqual(found[0].codes, ["COMILLAS_EN_COMANDO", "COMANDO_INTERNO"])
        self.assertTrue(all(d.line == 4 for d in found[0].defects))

    def test_audit_uses_the_same_candidate_function(self) -> None:
        import turn_close_audit as audit
        self.assertIs(audit._template_candidate, tc.template_candidate)


class DoctorFlowGuidanceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.doctor = load_doctor()
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self._orig_root = self.doctor.ROOT
        self.addCleanup(setattr, self.doctor, "ROOT", self._orig_root)
        self.doctor.ROOT = Path(self._tmp.name)
        self.workflows = Path(self._tmp.name) / ".agent" / "workflows"
        self.workflows.mkdir(parents=True)
        # Manifest real: así los comandos internos se reconocen como tales (COMANDO_INTERNO).
        (self.workflows / "manifest.json").write_text(
            (ROOT / ".agent" / "workflows" / "manifest.json").read_text(encoding="utf-8"), encoding="utf-8")

    def run_with(self, body: str, name: str = "w.md") -> list[str]:
        (self.workflows / name).write_text(body, encoding="utf-8")
        errors: list[str] = []
        self.doctor.validate_flow_guidance(errors)
        return errors

    def test_valid_template_with_model_line_passes(self) -> None:
        self.assertEqual(self.run_with(f"# W\n\nSiguiente: /ciclo Cerrar el módulo X\n{MODEL}\n"), [])

    def test_command_template_without_model_line_fails_with_sin_modelo(self) -> None:
        errors = self.run_with("# W\n\nSiguiente: /ciclo Cerrar el módulo X\n")
        self.assertEqual(len(errors), 1, errors)
        self.assertIn("workflows/w.md:3", errors[0])
        self.assertIn("SIN_MODELO", errors[0])
        self.assertIn("Modelo:", errors[0])  # el fix del primer defecto

    def test_template_inside_fence_fails_with_en_bloque_de_codigo(self) -> None:
        errors = self.run_with(f"# W\n\n```\nSiguiente: /ciclo Hacer X\n{MODEL}\n```\n")
        self.assertEqual(len(errors), 1, errors)
        self.assertIn("EN_BLOQUE_DE_CODIGO", errors[0])
        self.assertIn("w.md:4", errors[0])

    def test_ninguno_and_decision_forms_without_model_line_pass(self) -> None:
        self.assertEqual(self.run_with("# W\n\nSiguiente: ninguno — sin pasos\n"), [])
        self.assertEqual(self.run_with("# W\n\nSiguiente: decisión — elige A o B en el chat\n"), [])

    def test_internal_result_only_passes(self) -> None:
        self.assertEqual(self.run_with("# W\n\nDevuelve INTERNAL_RESULT al driver.\n"), [])

    def test_prose_mention_only_passes(self) -> None:
        self.assertEqual(self.run_with("# W\n\nCierra con la línea `Siguiente:` vigente de la sesión.\n"), [])

    def test_workflow_without_any_orientation_fails(self) -> None:
        errors = self.run_with("# W\n\nSin cierre operacional.\n")
        self.assertEqual(len(errors), 1, errors)
        self.assertIn("orientación canónica", errors[0])
        self.assertIn("w.md", errors[0])

    def test_legacy_format_is_still_rejected(self) -> None:
        marker = self.doctor.FLOW_LEGACY_MARKERS[0]
        errors = self.run_with(f"# W\n\n{marker} /arrancar — razón corta.\n")
        self.assertTrue(any("formato de flujo heredado" in e for e in errors), errors)

    def test_readme_is_skipped(self) -> None:
        self.assertEqual(self.run_with("# Índice\n\nSin cierre.\n", name="_README.md"), [])

    def test_missing_or_broken_manifest_fails_clearly_without_raising(self) -> None:
        manifest = self.workflows / "manifest.json"
        (self.workflows / "w.md").write_text("# W\n\nSiguiente: ninguno — x\n", encoding="utf-8")
        for content in (None, "{no es json", "[1, 2]"):
            with self.subTest(content=content):
                if content is None:
                    manifest.unlink(missing_ok=True)
                else:
                    manifest.write_text(content, encoding="utf-8")
                errors: list[str] = []
                self.doctor.validate_flow_guidance(errors)
                self.assertEqual(len(errors), 1, errors)
                self.assertIn("manifest.json", errors[0])

    def test_every_known_bad_form_makes_the_doctor_fail(self) -> None:
        forms = json.loads(FIXTURE.read_text(encoding="utf-8"))
        self.assertEqual(len(forms), 27)
        for form in forms:
            with self.subTest(fichero=form["fichero"], linea=form["linea"], texto=form["texto"]):
                errors = self.run_with(f"# W\n\n{form['texto']}\n")
                self.assertTrue(errors, form)
                joined = "\n".join(errors)
                for code in form["codigos"]:
                    self.assertIn(code, joined, form)


class RealRepoTests(unittest.TestCase):
    """Miden los workflows reales: rojos hasta que estén corregidos."""

    def test_real_workflows_have_zero_template_defects(self) -> None:
        manifest = json.loads((ROOT / ".agent" / "workflows" / "manifest.json").read_text(encoding="utf-8"))
        bad: list[str] = []
        for path in sorted((ROOT / ".agent" / "workflows").glob("*.md")):
            if path.name == "_README.md":
                continue
            for finding in tc.scan_templates(path.read_text(encoding="utf-8"), manifest=manifest):
                if finding.defective:
                    bad.append(f"{path.relative_to(ROOT)}:{finding.line} {','.join(finding.codes)}")
        self.assertEqual(bad, [], f"{len(bad)} plantillas con defectos")

    def test_gycd_does_not_compose_a_double_slash_command(self) -> None:
        text = (ROOT / ".agent" / "workflows" / "gycd.md").read_text(encoding="utf-8")
        self.assertFalse("/{next_command}" in text, "gycd.md compone «/{next_command}»: next_command ya lleva la barra")

    def test_real_repo_doctor_flow_guidance_is_clean(self) -> None:
        doctor = load_doctor()
        errors: list[str] = []
        doctor.validate_flow_guidance(errors)
        self.assertEqual(errors, [])


class ProjectionPointerTests(unittest.TestCase):
    NO_POINTER = ("Siguiente: ninguno — no hay siguiente paso registrado; "
                  "pide el estado en una sesión nueva o usa /reconciliar")

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        root = Path(self._tmp.name)
        (root / "_project").mkdir(parents=True)
        names = ["WORKSPACE_ROOT", "CORE_FILE_PATH", "BUGS_FILE_PATH", "CLARIFICATIONS_FILE_PATH",
                 "DECISIONS_FILE_PATH", "SESSION_BRIEF_PATH"]
        orig = {k: getattr(state_core, k) for k in names}
        self.addCleanup(lambda: [setattr(state_core, k, v) for k, v in orig.items()])
        self.root = root
        state_core.WORKSPACE_ROOT = root
        state_core.CORE_FILE_PATH = root / "_project" / "core.md"
        state_core.BUGS_FILE_PATH = root / "_project" / "06_bug_fixes.md"
        state_core.CLARIFICATIONS_FILE_PATH = root / "_project" / "07_pending_clarifications.md"
        state_core.DECISIONS_FILE_PATH = root / "_project" / "03_arch_decisions.md"
        state_core.SESSION_BRIEF_PATH = root / "_project" / "00_session_brief.json"
        wf = root / ".agent" / "workflows"
        wf.mkdir(parents=True)
        (wf / "manifest.json").write_text(json.dumps(MANIFEST), encoding="utf-8")

    def state(self, session: dict) -> dict:
        return {
            "project": "P", "current_phase": "phase_01",
            "modules": {"in_progress": [], "completed": [], "pending": []},
            "module_statuses": {}, "work_tiers": {}, "session": session,
            "_bugs": [], "_clarifications": [], "_decisions": [],
        }

    def render(self, session: dict) -> tuple[str, dict]:
        projections.render(self.state(session))
        core = state_core.CORE_FILE_PATH.read_text(encoding="utf-8")
        brief = json.loads(state_core.SESSION_BRIEF_PATH.read_text(encoding="utf-8"))
        return core, brief

    @staticmethod
    def next_section(core: str) -> list[str]:
        section = core.split("## Siguiente", 1)[1].split("\n---", 1)[0]
        return [line for line in section.splitlines() if line.strip()]

    def test_without_pointer_the_line_is_ninguno_and_no_default_work(self) -> None:
        for session in ({}, None):
            with self.subTest(session=session):
                core, brief = self.render(session)
                lines = self.next_section(core)
                self.assertEqual(lines[-1], self.NO_POINTER)
                self.assertFalse(any(line.startswith("> Aviso") for line in lines), lines)
                self.assertNotIn("Continuar con las tareas", core)
                self.assertNotIn("Continuar con las tareas", json.dumps(brief, ensure_ascii=False))
                self.assertEqual(brief["next_action"],
                                 "no hay siguiente paso registrado; pide el estado en una sesión nueva o usa /reconciliar")

    def test_pointer_helper_returns_ninguno_without_pointer(self) -> None:
        command, action, line = projections._session_pointer_values({})
        self.assertEqual((command, line), ("ninguno", self.NO_POINTER))
        self.assertTrue(line.endswith(action))

    def test_valid_pointer_has_no_warning_and_no_double_slash(self) -> None:
        core, _ = self.render({"next_command": "/ciclo", "next_action": "Implementar el módulo M1 en src/m1.py hasta pasar sus tests."})
        lines = self.next_section(core)
        self.assertEqual(lines[-1], "Siguiente: /ciclo Implementar el módulo M1 en src/m1.py hasta pasar sus tests.")
        self.assertNotIn("//", lines[-1])
        self.assertFalse(any(line.startswith("> Aviso") for line in lines), lines)

    def test_pointer_with_bracket_marker_shows_warning_and_does_not_fail(self) -> None:
        core, brief = self.render({"next_command": "/ciclo", "next_action": "Implementar [módulo] hasta cerrar."})
        lines = self.next_section(core)
        self.assertTrue(lines[-1].startswith("Siguiente: /ciclo Implementar [módulo]"), lines)
        warning = lines[-2]
        self.assertTrue(warning.startswith("> Aviso: el siguiente paso registrado no cumple el contrato de cierre ("), warning)
        self.assertIn("MARCADOR", warning)
        self.assertIn("set_session_pointer", warning)
        self.assertEqual(brief["next_action"], "Implementar [módulo] hasta cerrar.")  # el estado no se reescribe

    def test_only_next_action_keeps_composing_with_ciclo(self) -> None:
        core, _ = self.render({"next_action": "implementar M0_x"})
        self.assertEqual(self.next_section(core)[-1], "Siguiente: /ciclo implementar M0_x")

    def test_blocking_sentinel_keeps_its_dash(self) -> None:
        core, _ = self.render({"next_command": "ninguno", "next_action": "esperando la respuesta del builder en el chat"})
        self.assertEqual(self.next_section(core)[-1], "Siguiente: ninguno — esperando la respuesta del builder en el chat")

    def test_unreadable_manifest_skips_validation_silently(self) -> None:
        (self.root / ".agent" / "workflows" / "manifest.json").write_text("{roto", encoding="utf-8")
        core = projections.render_core_markdown(
            self.state({"next_action": "Implementar [módulo] hasta cerrar."}), now_iso="t", workspace_root=self.root)
        self.assertNotIn("> Aviso", core)


class SetSessionPointerExampleTests(unittest.TestCase):
    def test_example_composes_a_valid_closing_line(self) -> None:
        contract = action_contracts.describe_action("set_session_pointer")
        example = contract["example"]
        self.assertEqual(example["next_command"], "/ciclo")
        self.assertNotIn("/cierre-modulo", json.dumps(contract, ensure_ascii=False))
        line = f"Siguiente: {example['next_command']} {example['next_action']}"
        defects = tc.validate_turn_close(line, manifest=MANIFEST, mode="live")
        self.assertEqual([d.code for d in defects if d.severity == tc.SEVERITY_ERROR], [], line)


if __name__ == "__main__":
    unittest.main()
