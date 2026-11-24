"""5.3.3 — una dependencia nueva caduca el recibo aunque otro módulo la cubra, como en main.

El reclamo de dependencias ajenas (TL-FW-332: no caducar el recibo de M1 por la
dependencia que cubre el recibo de M2) queda fuera de la 5.3.3 junto con las partes
2 y 3 de L7.3. Regresión: M1 caduca en `dependencies` y pide la firma del builder;
M2, que la cubre en su propio recibo, sigue sin caducar (FW-337).
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / ".agent" / "scripts"))
sys.path.insert(0, str(ROOT / "tests"))

import test_workflow_receipts as twr
import workflow_receipts as wr

DEP = "fred-csv-contraste (fuente de M2, ADR-002)"


class DependenciaDeOtroModuloCaducaTests(unittest.TestCase):
    def setUp(self) -> None:
        twr.WorkflowReceiptTests.setUp(self)
        self.addCleanup(twr.WorkflowReceiptTests.tearDown, self)
        project = self.root / "_project"
        (project / "04_active_scope.md").write_text("## M1\nScope de M1\n\n## M2\nScope de M2\n", encoding="utf-8")
        (project / "08_module_acceptance.md").write_text("## M1\n- [ ] AC de M1\n\n## M2\n- [ ] AC de M2\n", encoding="utf-8")
        self.state["modules"] = {"active": ["M1", "M2"]}
        self.state["work_tiers"]["M2"] = {"tier": "tier_2", "rationale": "fuente de datos nueva"}
        self.state["module_specs"]["M2"] = {"data_model": "n/a", "api_contract": "n/a"}
        self.state["_decisions"].append({"id": "ADR-002", "module_id": "M2", "decision": "contraste con FRED"})

    def approve(self, module: str, **kwargs) -> dict:
        return wr.record_approval(
            self.state, self.root, module, approved_by="builder", evidence=f"Sí, adelante con {module}",
            risk_summary="cambio Tier 2 con rollback", adr_refs=["ADR-001" if module == "M1" else "ADR-002"],
            now_iso="2026-10-24T12:00:00+00:00", **kwargs,
        )

    def test_la_dependencia_que_cubre_otro_modulo_caduca_mi_recibo(self) -> None:
        self.approve("M1")
        self.approve("M2", covers_dependencies=[DEP])
        self.state["dependencies"].append(DEP)
        problems = " ".join(wr.approval_problems(self.root, self.state, "M1"))
        self.assertIn("stale", problems)
        self.assertIn("dependencies", problems)
        self.assertIn("requiere nueva aprobación del builder", problems)
        self.assertEqual(list(wr.approval_problems(self.root, self.state, "M2")), [])


if __name__ == "__main__":
    unittest.main()
