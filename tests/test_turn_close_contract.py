#!/usr/bin/env python3
"""5.3.0 L2: el contrato escrito del cierre de turno coincide con su validador.

Cada ejemplo de cierre de `runtime_core.md` y de `decision_translation_core.md`
se pasa por `validate_turn_close` en modo live: los buenos no dan defectos y los
known-bad dan exactamente los códigos que el texto declara (D1, D3).
"""
from __future__ import annotations

import importlib.util
import json
import re
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / ".agent" / "scripts"))

import turn_close as tc  # noqa: E402

CORE = ROOT / ".agent" / "rules" / "runtime_core.md"
SKILL = ROOT / ".agent" / "skills" / "decision_translation_core.md"
MANIFEST = json.loads((ROOT / ".agent" / "workflows" / "manifest.json").read_text(encoding="utf-8"))
MODEL = "Modelo: standard · Esfuerzo: 🟡 medio (tier_1)"
MAX_CORE_LINES = 15

_SIG_QUOTE_RE = re.compile(r"«(Siguiente: [^»]*)»")
_MODEL_QUOTE_RE = re.compile(r"«(Modelo: [^»]*)»")
_CODE_RE = re.compile(r"`([A-Z_]{6,})`")


def _section(text: str, heading: str, level: str = "## ") -> str:
    start = text.index(heading)
    nxt = re.search(rf"^{re.escape(level)}", text[start + len(heading):], re.MULTILINE)
    return text[start: start + len(heading) + nxt.start()] if nxt else text[start:]


def _defects(line: str, model: str | None = None) -> list[tc.Defect]:
    body = "Resumen del turno.\n\n" + line + (f"\n{model}" if model else "")
    return tc.validate_turn_close(body, manifest=MANIFEST, mode="live")


def _assert_good(case: unittest.TestCase, line: str) -> None:
    """Un ejemplo bueno no da defectos; la forma con comando lleva debajo la línea de modelo."""
    form = tc.extract_close_parts(line, manifest=MANIFEST).form
    model = MODEL if form in (tc.FORM_COMMAND, tc.FORM_CICLO_FASE) else None
    case.assertEqual([d.code for d in _defects(line, model)], [], line)


def _load_doctor():
    spec = importlib.util.spec_from_file_location(
        "framework_doctor", ROOT / ".agent" / "scripts" / "framework_doctor.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class CoreSectionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.section = _section(CORE.read_text(encoding="utf-8"), "## Cierre de turno")

    def test_section_is_short(self) -> None:
        lines = self.section.strip().splitlines()
        self.assertLessEqual(len(lines), MAX_CORE_LINES, f"la sección tiene {len(lines)} líneas")

    def test_scope_form_and_five_cases(self) -> None:
        flat = " ".join(self.section.split())
        for fragment in (
            "Todo turno que devuelve el control", "lo último del mensaje", "nunca en bloque de código",
            "comillas invertidas", "- Normal, dos líneas:", "- Decisión pendiente, una línea:",
            "- Espera o proceso largo:", "- Bloqueo, una línea:", "- Pregunta suelta:",
            "`/ciclo` no aprueba ni decide", "sin texto tras el tier", "no empieza por «esperar»",
        ):
            self.assertIn(fragment, flat)

    def test_command_by_kind_of_work_names_builder_commands_only(self) -> None:
        builder, _ = tc.manifest_commands(MANIFEST)
        line = next(l for l in self.section.splitlines() if l.startswith("Comando por tipo de trabajo:"))
        named = set(re.findall(r"`/([^`\s]+)`", line))
        self.assertEqual(named, {"ciclo", "diseñar", "reconciliar", "nuevo-bug", "gycd", "arrancar"})
        self.assertLessEqual(named, set(builder))

    def test_every_example_passes_the_validator(self) -> None:
        examples = _SIG_QUOTE_RE.findall(self.section)
        self.assertEqual(len(examples), 3, examples)  # normal, decisión y bloqueo
        for line in examples:
            with self.subTest(line=line):
                _assert_good(self, line)
        forms = {tc.extract_close_parts(line, manifest=MANIFEST).form for line in examples}
        self.assertEqual(forms, {tc.FORM_COMMAND, tc.FORM_DECISION, tc.FORM_NINGUNO})

    def test_model_example_is_well_formed(self) -> None:
        models = _MODEL_QUOTE_RE.findall(self.section)
        self.assertEqual(len(models), 1)
        self.assertEqual(_defects("Siguiente: /ciclo fase", models[0]), [])


class SkillSectionTests(unittest.TestCase):
    def setUp(self) -> None:
        text = SKILL.read_text(encoding="utf-8")
        self.section = _section(text, "## Bloque de cierre de turno")
        good, bad = self.section.split("**Known-bad**", 1)
        self.good = good
        self.bad_table, self.after_bad = bad.split("\n\n", 2)[1:]

    def test_points_to_the_core_and_to_the_validator(self) -> None:
        flat = " ".join(self.section.split())
        for fragment in (".agent/rules/runtime_core.md", "validate_turn_close", "Alcance:",
                         "todo turno que devuelve el control al builder", "Decisión pendiente del builder",
                         "Espera o proceso largo", "Pregunta suelta", "Bloqueo"):
            self.assertIn(fragment, flat)
        self.assertNotIn("respuesta de workflow", flat)

    def test_no_fenced_example_of_the_close(self) -> None:
        self.assertNotIn("```", self.section)
        self.assertNotRegex(self.section, r"(?m)^Siguiente:")

    def test_good_examples_pass(self) -> None:
        examples = _SIG_QUOTE_RE.findall(self.good) + _SIG_QUOTE_RE.findall(self.after_bad)
        self.assertGreaterEqual(len(examples), 5)
        for line in examples:
            with self.subTest(line=line):
                _assert_good(self, line)

    def test_known_bad_fail_with_the_declared_codes(self) -> None:
        rows = [r for r in self.bad_table.splitlines() if r.startswith("| «")]
        self.assertGreaterEqual(len(rows), 7)
        for row in rows:
            cells = [c.strip() for c in row.strip("|").split("|")]
            line = cells[0].strip("«»")
            declared = set(_CODE_RE.findall(cells[1]))
            got = {d.code for d in _defects(line)} - {tc.SIN_MODELO}
            with self.subTest(line=line):
                self.assertEqual(got, declared)

    def test_plan_known_bad_are_listed(self) -> None:
        for start in ("/ciclo Aprobar ", "/ciclo Decidir el rumbo ", "/ciclo Pendiente del builder: "):
            self.assertIn(f"«Siguiente: {start}", self.bad_table)

    def test_prose_codes_exist_in_the_validator(self) -> None:
        for code in _CODE_RE.findall(self.section):
            self.assertIn(code, tc.ALL_CODES)

    def test_prose_scenarios_give_the_codes_the_text_names(self) -> None:
        tick = chr(96)
        good = "Siguiente: /ciclo Implementar el motor en src/motor.py"
        scenarios = {
            tc.SIN_SIGUIENTE: "Resumen del turno, sin cierre.",
            tc.EN_BLOQUE_DE_CODIGO: f"Resumen.\n\n{tick * 3}\n{good}\n{MODEL}\n{tick * 3}",
            tc.COMILLAS_EN_COMANDO: f"Resumen.\n\nSiguiente: {tick}/ciclo{tick} Implementar el motor en src/motor.py\n{MODEL}",
            tc.NO_ES_LO_ULTIMO: f"Resumen.\n\n{good}\n{MODEL}\n\nCoste de la sesión: 1,2 M de tokens.",
            tc.FORMATO_HEREDADO: "Resumen.\n\nSiguiente " + "comando: /ciclo — seguir con el motor",
        }
        for code, text in scenarios.items():
            with self.subTest(code=code):
                self.assertIn(f"`{code}`", self.section)
                self.assertIn(code, [d.code for d in tc.validate_turn_close(text, manifest=MANIFEST)])

    def test_codes_called_warnings_are_warnings(self) -> None:
        sentence = next(l for l in self.after_bad.splitlines() if "son avisos" in l)
        named = set(_CODE_RE.findall(sentence))
        self.assertEqual(named, {tc.DECISION_EN_CICLO, tc.INSTRUCCION_VAGA, tc.PROHIBICIONES})  # 5.3.2, FW-402
        declared = {c for row in self.bad_table.splitlines() for c in _CODE_RE.findall(row)}
        self.assertEqual(declared & set(tc.WARNING_CODES), named)

    def test_model_examples(self) -> None:
        models = _MODEL_QUOTE_RE.findall(self.section)
        self.assertEqual(len(models), 3)
        for model in models:
            codes = [d.code for d in _defects("Siguiente: /ciclo fase", model)]
            with self.subTest(model=model):
                self.assertEqual(codes, [tc.MODELO_MAL_FORMADO] if "tarea acotada" in model else [])


class WiringTests(unittest.TestCase):
    def setUp(self) -> None:
        self.doctor = _load_doctor()
        self.addCleanup(setattr, self.doctor, "ROOT", self.doctor.ROOT)

    def _errors(self, root: Path) -> list[str]:
        self.doctor.ROOT = root
        errors: list[str] = []
        self.doctor.validate_cierre_de_turno_wiring(errors)
        return errors

    def _copy(self, tmp: str) -> Path:
        root = Path(tmp)
        (root / ".agent" / "rules").mkdir(parents=True)
        shutil.copy(CORE, root / ".agent" / "rules" / "runtime_core.md")
        shutil.copy(ROOT / "AGENTS.md", root / "AGENTS.md")
        return root

    def test_real_repo_passes(self) -> None:
        self.assertEqual(self._errors(ROOT), [])
        self.assertNotIn("respuesta de workflow", " ".join((ROOT / "AGENTS.md").read_text(encoding="utf-8").split()))

    def test_fails_without_the_section(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = self._copy(tmp)
            core = root / ".agent" / "rules" / "runtime_core.md"
            text = core.read_text(encoding="utf-8")
            core.write_text(text.replace(_section(text, "## Cierre de turno"), ""), encoding="utf-8")
            errors = self._errors(root)
        self.assertTrue(any("perdió la sección «Cierre de turno»" in e for e in errors), errors)

    def test_fails_when_a_case_is_missing(self) -> None:
        for case in ("- Normal, dos líneas:", "- Decisión pendiente, una línea:", "- Espera o proceso largo:",
                     "- Bloqueo, una línea:", "- Pregunta suelta:", "Todo turno que devuelve el control",
                     "nunca en bloque", "`/arrancar`"):
            with self.subTest(case=case), tempfile.TemporaryDirectory() as tmp:
                root = self._copy(tmp)
                core = root / ".agent" / "rules" / "runtime_core.md"
                text = core.read_text(encoding="utf-8")
                core.write_text(text.replace(case, "- Otro:"), encoding="utf-8")
                self.assertEqual(len(self._errors(root)), 1)

    def test_fails_when_agents_scopes_the_close_to_workflows(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = self._copy(tmp)
            agents = root / "AGENTS.md"
            text = agents.read_text(encoding="utf-8")
            marker = "Todo turno que devuelve el control al builder cierra como"
            self.assertIn(marker, text)
            agents.write_text(text.replace(marker, "Toda respuesta de\nworkflow cierra como"), encoding="utf-8")
            errors = self._errors(root)
        self.assertTrue(any("respuesta de workflow" in e for e in errors), errors)

    def test_fails_when_agents_stops_pointing_to_the_core(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = self._copy(tmp)
            agents = root / "AGENTS.md"
            text = agents.read_text(encoding="utf-8")
            agents.write_text(text.replace("«Cierre de turno»", "de cierre"), encoding="utf-8")
            errors = self._errors(root)
        self.assertTrue(any("no remite a la sección" in e for e in errors), errors)

    def test_adopter_with_an_old_managed_block_is_not_blocked(self) -> None:
        """En `initialized` el bloque antiguo no da ERROR: lo refresca la propagación (FW-202)."""
        with tempfile.TemporaryDirectory() as tmp:
            root = self._copy(tmp)
            agents = root / "AGENTS.md"
            text = agents.read_text(encoding="utf-8")
            marker = "Todo turno que devuelve el control al builder cierra como"
            agents.write_text(text.replace(marker, "Toda respuesta de workflow termina como"), encoding="utf-8")
            self.doctor.ROOT = root
            errors: list[str] = []
            self.doctor.validate_wiring_feature("cierre_de_turno", None, errors, mode="initialized")
        self.assertEqual(errors, [])

    def test_project_zone_of_agents_is_not_policed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = self._copy(tmp)
            agents = root / "AGENTS.md"
            agents.write_text("Nota del proyecto: toda respuesta de workflow se revisa.\n\n"
                              + agents.read_text(encoding="utf-8"), encoding="utf-8")
            self.assertEqual(self._errors(root), [])


if __name__ == "__main__":
    unittest.main()
