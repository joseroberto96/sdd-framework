import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".agent" / "workflows"


class CicloNestedExecutionContractTests(unittest.TestCase):
    def read(self, relative_path: str) -> str:
        return (ROOT / relative_path).read_text(encoding="utf-8")

    def test_ciclo_owns_turn_until_real_stop(self):
        ciclo = self.read(".agent/workflows/ciclo.md")
        for needle in (
            "INVOCADO_POR_CICLO: true",
            "INTERNAL_RESULT: CONTINUE",
            "INTERNAL_RESULT: BLOCK",
            "INTERNAL_RESULT: DECISION_REQUIRED",
            "propiedad del turno",
        ):
            self.assertIn(needle, ciclo)
        self.assertIn("no devuelve el turno al builder", ciclo)

    def test_internal_workflows_honor_nested_receipt(self):
        for name in (
            "scope-siguiente.md",
            "enriquecer-ticket.md",
            "preflight-review.md",
            "cambio-seguro.md",
            "tdd.md",
            "verificar-ejecutable.md",
            "cierre-modulo.md",
        ):
            text = (WORKFLOWS / name).read_text(encoding="utf-8")
            with self.subTest(workflow=name):
                self.assertIn("INVOCADO_POR_CICLO", text)
                self.assertIn("INTERNAL_RESULT: CONTINUE", text)
                self.assertIn("INTERNAL_RESULT: BLOCK", text)
                self.assertIn("INTERNAL_RESULT: DECISION_REQUIRED", text)

    def test_standalone_outputs_are_disabled_when_nested(self):
        expected = {
            "scope-siguiente.md": "no ejecutar las plantillas standalone",
            "enriquecer-ticket.md": "no usar las plantillas standalone",
            "preflight-review.md": "no ejecutar la plantilla standalone",
            "cambio-seguro.md": "En standalone, reportar",
            "tdd.md": "En standalone",
            "verificar-ejecutable.md": "Solo en standalone",
            "cierre-modulo.md": "no ejecutar las plantillas standalone",
        }
        for name, needle in expected.items():
            text = (WORKFLOWS / name).read_text(encoding="utf-8")
            with self.subTest(workflow=name):
                self.assertIn(needle, text)

    def test_tier2_checkpoint_is_informative_inside_authorized_ciclo(self):
        ciclo = self.read(".agent/workflows/ciclo.md")
        decisions = self.read(".agent/skills/decision_translation_core.md")
        for text in (ciclo, decisions):
            self.assertIn("checkpoint informativo", text.lower())
            self.assertIn("continúa en el mismo turno", text)
        self.assertIn("revisión independiente", ciclo)
        self.assertIn("rollback", ciclo)
        self.assertIn("APPROVE_RECEIPT", ciclo)
        self.assertIn("workflow_receipts.py approval", ciclo)
        self.assertIn("nunca se reconstruye desde memoria conversacional", ciclo)

    def test_declared_workspace_dependencies_can_bootstrap_without_pause(self):
        ciclo = self.read(".agent/workflows/ciclo.md")
        self.assertIn("dependencias ya declaradas", ciclo)
        self.assertIn(".venv", ciclo)
        self.assertIn("dependencia nueva no declarada", ciclo)
        self.assertIn("manifest/lock con la misma versión exacta", ciclo)
        self.assertIn("03_project_state.json.dependencies", ciclo)
        self.assertIn("No modificar el", ciclo)
        self.assertIn("instalar globalmente", ciclo)

    def test_nested_enrichment_can_write_only_derived_additive_specs(self):
        workflow = self.read(".agent/workflows/enriquecer-ticket.md")
        self.assertIn("aditivas y derivadas", workflow)
        self.assertIn("scope, ACs, PRD o ADRs ya aprobados", workflow)
        self.assertIn("DESTRUCTIVO", workflow)
        self.assertIn("DECISION_REQUIRED", workflow)

    def test_hard_gates_remain_explicit(self):
        ciclo = self.read(".agent/workflows/ciclo.md")
        for needle in (
            "STOP GATE",
            "cambio destructivo",
            "ampliación de scope",
            "dependencia nueva no declarada",
            "migración irreversible",
            "fork A/B",
            "NO bypasear",
        ):
            self.assertIn(needle, ciclo)

    def test_diff_size_alone_does_not_return_turn_to_builder(self):
        safe_change = self.read(".agent/workflows/cambio-seguro.md")
        tdd = self.read(".agent/workflows/tdd.md")
        self.assertIn("**nunca por\n   número de líneas**", safe_change)
        self.assertIn("NO pide\n   aprobación", safe_change)
        self.assertIn("No pedir aprobación solo por líneas/archivos", tdd)

    def test_codex_uses_honest_independent_reviewer_mode(self):
        adapter = self.read(".agent/adapters/codex.md")
        closure = self.read(".agent/workflows/cierre-modulo.md")
        for text in (adapter, closure):
            self.assertIn("INDEPENDENT_SUBAGENT_NO_ISOLATION", text)
        self.assertIn("diff pre/post", adapter)

    def test_tier2_review_seals_final_diff_before_completion_not_before_entry(self):
        closure = self.read(".agent/workflows/cierre-modulo.md")
        self.assertIn("planned second-pass review", closure)
        self.assertIn("auditoría independiente sustantiva corre en el paso 3", closure)
        self.assertIn("sello final independiente", closure)
        self.assertIn("Sin `RECEIPT GATE: OK`", closure)
        self.assertIn("record_review_receipt", closure)
        self.assertIn("complete_module", closure)


if __name__ == "__main__":
    unittest.main()
