#!/usr/bin/env python3
"""L3.5 de la 5.3.1: formatos del gate de implementación (PA-FW-008, PA-FW-011).

La golden crea un módulo con el texto literal de las plantillas de
`/scope-siguiente` (paso 3) y `/enriquecer-ticket` (paso 2), en una copia
aislada de la plantilla, y exige PASS en la vista `implementation` por la CLI
real. Si una plantilla deja de producir lo que el gate valida, la golden falla.

Además: el gate acepta `(AC-n, …)` al final de una línea como evidencia
etiquetada (con la misma extracción y la regla D), lee como una sola las
secciones de preservación de un mismo módulo y devuelve una sección ambigua
como un motivo más (`ambiguous_source`) en vez de cortar con un error.
"""
import json
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / ".agent" / "scripts"))

MODULE = "m7_export"
NAME = "Exportar informe"
TS = "2026-10-21T00:00:00Z"


def fenced_after(text: str, anchor: str) -> str:
    """Primer bloque ```markdown que sigue a `anchor` en el texto del workflow."""
    start = text.index(anchor)
    match = re.search(r"```markdown\n(.*?)\n\s*```", text[start:], re.DOTALL)
    if not match:
        raise AssertionError(f"no hay bloque markdown tras {anchor!r}")
    return "\n".join(line[3:] if line.startswith("   ") else line for line in match.group(1).splitlines()) + "\n"


def fill(block: str) -> str:
    """Rellena la plantilla como lo haría el agente con un módulo concreto."""
    block = block.replace("[module_id]", MODULE).replace("module_id", MODULE)
    block = block.replace("[Name]", NAME).replace("[Nombre]", NAME).replace("Módulo N ", "Módulo 7 ")
    block = block.replace("[required | n/a — razón]", "n/a — el módulo no tiene esa superficie")
    block = block.replace("[criterion 1]", "El sistema debe exportar el informe en CSV")
    block = block.replace("[criterio 1]", "El sistema debe exportar el informe en CSV")
    return re.sub(r"\[(?![ xX]\])[^\]\n]+\]", "exportación semanal del informe a CSV", block)


def templates() -> dict[str, str]:
    scope_next = (ROOT / ".agent/workflows/scope-siguiente.md").read_text("utf-8")
    step3 = scope_next[scope_next.index("3. ESCRIBIR LOS ARTEFACTOS"):scope_next.index("4. FIJAR LOS PUNTEROS DE SESIÓN")]
    enrich = (ROOT / ".agent/workflows/enriquecer-ticket.md").read_text("utf-8")
    step2 = enrich[enrich.index("2. GENERAR BORRADOR ENRIQUECIDO"):enrich.index("3. ECO DE VERIFICACIÓN")]
    return {
        "acceptance": fill(fenced_after(step3, "@08_module_acceptance.md")),
        "scope_header": fill(fenced_after(step3, "@04_active_scope.md")),
        "dependencies": fill(fenced_after(step3, "@11_file_dependencies.md")),
        "enriched": fill(fenced_after(step2, "04_active_scope.md")),
    }


class IsolatedCopy:
    def __init__(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        shutil.copytree(ROOT / ".agent", self.root / ".agent",
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        shutil.copytree(ROOT / "_project", self.root / "_project")
        shutil.copy2(ROOT / "AGENTS.md", self.root / "AGENTS.md")
        state_path = self.root / "_project/03_project_state.json"
        state = json.loads(state_path.read_text("utf-8"))
        state["modules"].setdefault("in_progress", []).append(MODULE)
        state["module_statuses"][MODULE] = "in_progress"
        state["module_specs"][MODULE] = {
            "data_model": "n/a", "api_contract": "n/a",
            "justification": "módulo de exportación sin entidades nuevas ni HTTP",
            "classified_at": TS, "classified_by": "scope-siguiente",
        }
        state["work_tiers"][MODULE] = {"tier": "tier_1", "rationale": "golden", "classified_at": TS,
                                       "classified_by": "scope-siguiente"}
        state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", "utf-8")

    def append(self, relative: str, text: str) -> None:
        with (self.root / "_project" / relative).open("a", encoding="utf-8") as stream:
            stream.write("\n" + text)

    def write(self, relative: str, text: str) -> None:
        (self.root / "_project" / relative).write_text(text, "utf-8")

    def query(self) -> dict:
        proc = subprocess.run(
            [sys.executable, "-B", ".agent/scripts/state_manager.py", "query", "--view", "implementation",
             "--module-id", MODULE], cwd=self.root, capture_output=True, text=True, timeout=120, check=False)
        try:
            return json.loads(proc.stdout)
        except ValueError as exc:
            raise AssertionError(f"rc={proc.returncode} {proc.stdout[-800:]} {proc.stderr[-800:]}") from exc

    def cleanup(self) -> None:
        self._tmp.cleanup()


def reasons(result: dict) -> list[tuple[str, str]]:
    return sorted({(ref.get("section"), ref.get("reason")) for ref in result.get("refs", [])})


class GoldenTemplatesTests(unittest.TestCase):
    def setUp(self):
        self.copy = IsolatedCopy()
        self.addCleanup(self.copy.cleanup)
        self.t = templates()

    def golden(self):
        self.copy.append("08_module_acceptance.md", self.t["acceptance"])
        self.copy.write("04_active_scope.md", self.t["scope_header"] + "\n" + self.t["enriched"])
        self.copy.append("11_file_dependencies.md", self.t["dependencies"])

    def test_golden_las_plantillas_pasan_el_gate(self):
        self.golden()
        result = self.copy.query()
        self.assertEqual(result.get("gate_status"), "PASS", reasons(result))
        self.assertTrue(result.get("required_context_complete"))

    def test_ui_states_con_lista_de_ac_entre_parentesis(self):
        """PA-FW-011: «(AC-1, AC-2)» al final de la línea es evidencia etiquetada."""
        self.golden()
        acceptance = self.t["acceptance"].replace("- [ ] ", "- [x] ").replace(
            "— evidencia: pendiente", "— evidencia: .agent/scripts/state_manager.py").replace(
            "UI_STATES: n/a — el módulo no tiene esa superficie", "UI_STATES: required")
        acceptance += "- [x] AC-2 Si el informe está vacío, el sistema avisa — evidencia: .agent/scripts/state_queries.py\n"
        ui = "### UI_STATES\n" + "".join(
            f"- {estado} — comportamiento visible del estado (AC-1, AC-2)\n"
            for estado in ("Ideal", "Vacío", "Cargando", "Error", "Parcial"))
        acceptance = acceptance.replace("- [x] El sistema debe exportar", "- [x] AC-1 El sistema debe exportar")
        self.copy.write("08_module_acceptance.md", self.copy.root.joinpath(
            "_project/08_module_acceptance.md").read_text("utf-8").replace(self.t["acceptance"], acceptance + ui))
        result = self.copy.query()
        self.assertEqual(result.get("gate_status"), "PASS", reasons(result))

    def test_lista_de_ac_sin_marcar_sigue_sin_valer(self):
        """Regla D: citar no es declarar; AC-9 no está marcada."""
        self.golden()
        acceptance = self.t["acceptance"].replace(
            "UI_STATES: n/a — el módulo no tiene esa superficie", "UI_STATES: required")
        ui = "### UI_STATES\n" + "".join(
            f"- {estado} — comportamiento visible del estado (AC-9)\n"
            for estado in ("Ideal", "Vacío", "Cargando", "Error", "Parcial"))
        self.copy.write("08_module_acceptance.md", self.copy.root.joinpath(
            "_project/08_module_acceptance.md").read_text("utf-8").replace(self.t["acceptance"], acceptance + ui))
        result = self.copy.query()
        self.assertEqual(result.get("gate_status"), "NEEDS_INPUT")
        self.assertIn(("ui_evidence", "invalid_evidence"), reasons(result))

    def test_dos_secciones_de_preservacion_del_mismo_modulo(self):
        """PA-FW-008: una ampliación del inventario al final del fichero se lee con la primera."""
        self.golden()
        self.copy.append("11_file_dependencies.md",
                         f"### {MODULE} — Ampliación del inventario\n"
                         "preserva:\n- Nombre del fichero exportado — evidencia: .agent/scripts/state_queries.py\n")
        result = self.copy.query()
        self.assertEqual(result.get("gate_status"), "PASS", reasons(result))

    def test_scope_ambiguo_es_un_motivo_mas_y_no_un_error(self):
        self.copy.append("08_module_acceptance.md", self.t["acceptance"].replace("UI_STATES:", "UI_ESTADOS:"))
        self.copy.write("04_active_scope.md",
                        f"## {MODULE} — primera\nuno\n\n## {MODULE} — segunda\ndos\n")
        result = self.copy.query()
        self.assertNotIn("error", result)
        self.assertEqual(result.get("gate_status"), "NEEDS_INPUT")
        found = reasons(result)
        self.assertIn(("scope", "ambiguous_source"), found)
        self.assertIn(("ui_states", "missing_applicability"), found)
        self.assertIn(("preserve_evidence", "missing_evidence"), found)
        self.assertNotIn(("scope", "missing_source"), found)


class AmbiguousSourceInEveryViewTests(unittest.TestCase):
    """Revisión `security` de L3, hallazgo 2: una ambigüedad nunca da PASS."""

    def test_scope_ambiguo_en_startup_y_recovery(self):
        copy = IsolatedCopy()
        self.addCleanup(copy.cleanup)
        copy.write("04_active_scope.md", f"## {MODULE} — primera\nuno\n\n## {MODULE} — segunda\ndos\n")
        for view in ("startup", "recovery"):
            with self.subTest(view=view):
                proc = subprocess.run(
                    [sys.executable, "-B", ".agent/scripts/state_manager.py", "query", "--view", view,
                     "--module-id", MODULE], cwd=copy.root, capture_output=True, text=True, timeout=120)
                result = json.loads(proc.stdout)
                self.assertEqual(result.get("gate_status"), "NEEDS_INPUT")
                self.assertIn(("scope", "ambiguous_source"), reasons(result))


class EvidenceHintTests(unittest.TestCase):
    def test_el_formato_aceptado_nombra_la_lista_entre_parentesis(self):
        import state_queries
        self.assertIn("(AC-n, …)", state_queries._EVIDENCE_FORMAT_HINT)


if __name__ == "__main__":
    unittest.main()
