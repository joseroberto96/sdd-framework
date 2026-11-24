"""FW-250 (L9): declaración obligatoria de tests modificados.

Ejecutan `record_execution` y `start_review` reales sobre repos git temporales:
comprobar solo texto no probaría el cableado (P1)."""
import copy
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / ".agent" / "scripts"))
sys.path.insert(0, str(ROOT / "tests"))

import workflow_receipts as wr
import test_workflow_receipts as twr

EVID = "pytest -q: 12 passed, salida real de la verificación"


def git(root, *args):
    subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True)


class GateBase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        git(self.root, "init", "-q", "-b", "main")
        git(self.root, "config", "user.email", "t@t")
        git(self.root, "config", "user.name", "t")
        self.write("src/app.py", "x = 1\n")
        self.write("tests/test_app.py", "def test_a(): pass\n")
        self.write("tests/test_other.py", "def test_o(): pass\n")
        git(self.root, "add", "-A")
        git(self.root, "commit", "-q", "-m", "base")
        self.base = subprocess.run(
            ["git", "-C", str(self.root), "rev-parse", "HEAD"], capture_output=True, text=True
        ).stdout.strip()
        self.state = {}

    def write(self, rel, text):
        p = self.root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)

    def seal(self, **kw):
        return wr.record_execution(
            self.state, self.root, "m1", kind="ok_automatizada", command="pytest -q",
            exit_code=0, evidence=EVID, now_iso="2026-09-19T00:00:00Z",
            base_ref=kw.pop("base_ref", self.base), **kw,
        )

    def decl(self, path, reason="cambia el contrato", by="builder"):
        return {"path": path, "reason": reason, "authorized_by": by}


class ClassifierTests(unittest.TestCase):
    def test_rutas_de_test(self):
        for p in ("tests/a.py", "test/a.js", "pkg/tests/x/y.py", "src/test_a.py",
                  "src/a_test.go", "web/a.spec.ts", "tests/conftest.py"):
            self.assertTrue(wr._is_test_path(p), p)
        for p in ("src/app.py", "docs/tests.md", "src/contest.py", "latest.py"):
            self.assertFalse(wr._is_test_path(p), p)

    def test_produccion_reutiliza_la_allowlist_de_docs(self):
        self.assertTrue(wr._is_production_code("src/app.py"))
        self.assertTrue(wr._is_production_code(".agent/workflows/ciclo.md"))
        self.assertFalse(wr._is_production_code("docs/guia.md"))
        self.assertFalse(wr._is_production_code("README.md"))
        self.assertFalse(wr._is_production_code("tests/test_app.py"))


class GateTests(GateBase):
    def test_solo_anade_tests_pasa(self):
        self.write("src/app.py", "x = 2\n")
        self.write("tests/test_nuevo.py", "def test_n(): pass\n")
        r = self.seal()
        self.assertEqual(r["test_gate"]["status"], "ok")
        self.assertFalse(r["test_gate"]["requires_declaration"])

    def test_modifica_test_y_produccion_sin_declarar_rechaza(self):
        self.write("src/app.py", "x = 2\n")
        self.write("tests/test_app.py", "def test_a(): assert True\n")
        with self.assertRaises(ValueError) as cm:
            self.seal()
        msg = str(cm.exception)
        self.assertIn("tests/test_app.py", msg)
        self.assertIn("test_changes", msg)
        self.assertIn("authorized_by", msg)
        self.assertNotIn("tests/test_other.py", msg)

    def test_con_declaracion_completa_pasa(self):
        self.write("src/app.py", "x = 2\n")
        self.write("tests/test_app.py", "def test_a(): assert True\n")
        r = self.seal(test_changes=[self.decl("tests/test_app.py")])
        self.assertEqual(r["test_gate"]["test_changes"][0]["path"], "tests/test_app.py")
        self.assertTrue(r["test_gate"]["requires_declaration"])

    def test_declaracion_parcial_nombra_el_que_falta(self):
        self.write("src/app.py", "x = 2\n")
        self.write("tests/test_app.py", "def test_a(): assert True\n")
        self.write("tests/test_other.py", "def test_o(): assert True\n")
        with self.assertRaises(ValueError) as cm:
            self.seal(test_changes=[self.decl("tests/test_app.py")])
        self.assertIn("tests/test_other.py", str(cm.exception))
        self.assertNotIn("Sin declarar: tests/test_app.py", str(cm.exception))

    def test_test_modificado_sin_tocar_produccion_pasa(self):
        self.write("tests/test_app.py", "def test_a(): assert True\n")
        r = self.seal()
        self.assertEqual(r["test_gate"]["modified_tests"], ["tests/test_app.py"])
        self.assertFalse(r["test_gate"]["requires_declaration"])

    def test_test_borrado_con_produccion_rechaza(self):
        self.write("src/app.py", "x = 2\n")
        (self.root / "tests/test_other.py").unlink()
        with self.assertRaises(ValueError) as cm:
            self.seal()
        self.assertIn("tests/test_other.py", str(cm.exception))

    def test_declaracion_con_campos_vacios_rechaza(self):
        self.write("src/app.py", "x = 2\n")
        self.write("tests/test_app.py", "def test_a(): assert True\n")
        for bad in (self.decl("tests/test_app.py", reason=" "),
                    self.decl("tests/test_app.py", by="")):
            with self.assertRaises(ValueError):
                self.seal(test_changes=[bad])

    def test_cambios_ya_commiteados_se_ven_contra_la_base(self):
        self.write("src/app.py", "x = 2\n")
        self.write("tests/test_app.py", "def test_a(): assert True\n")
        git(self.root, "add", "-A")
        git(self.root, "commit", "-q", "-m", "cambio")
        with self.assertRaises(ValueError):
            self.seal()


class SinBaseTests(GateBase):
    def test_sin_git_omite_y_avisa_con_motivo(self):
        with tempfile.TemporaryDirectory() as d:
            r = wr.record_execution(
                {}, Path(d), "m1", kind="ok_automatizada", command="x", exit_code=0,
                evidence=EVID, now_iso="t",
            )
        self.assertEqual(r["test_gate"]["status"], "sin_base")
        self.assertTrue(r["test_gate"]["reason"])
        st = {"workflow_receipts": {"executions": {"m1": r}}}
        warns = wr.test_gate_warnings(st, "m1")
        self.assertEqual(len(warns), 1)
        self.assertIn("no pudo verificar", warns[0])
        self.assertIn(r["test_gate"]["reason"], warns[0])

    def test_base_igual_a_head_avisa_de_diff_solo_local(self):
        r = wr.record_execution(
            self.state, self.root, "m1", kind="ok_automatizada", command="x", exit_code=0,
            evidence=EVID, now_iso="t",
        )
        self.assertEqual(r["test_gate"]["status"], "ok")
        self.assertEqual(r["test_gate"]["base"], self.base)


class ReviewTests(GateBase):
    def review(self):
        return wr.start_review(
            self.state, self.root, "m1", reviewer_id="a", implementer_id="b",
            mode="HUMAN_INDEPENDENT_REVIEW", now_iso="t",
        )

    def test_review_rechaza_receipt_que_exigia_declaracion_y_no_la_tiene(self):
        self.write("src/app.py", "x = 2\n")
        self.write("tests/test_app.py", "def test_a(): assert True\n")
        self.seal(test_changes=[self.decl("tests/test_app.py")])
        self.state["workflow_receipts"]["executions"]["m1"]["test_gate"]["test_changes"] = []
        with self.assertRaises(ValueError) as cm:
            self.review()
        self.assertIn("tests/test_app.py", str(cm.exception))

    def test_review_pasa_con_declaracion(self):
        self.write("src/app.py", "x = 2\n")
        self.write("tests/test_app.py", "def test_a(): assert True\n")
        self.seal(test_changes=[self.decl("tests/test_app.py")])
        self.assertEqual(self.review()["status"], "pending")

    def test_review_avisa_si_el_gate_fue_sin_base(self):
        self.seal()
        rec = self.state["workflow_receipts"]["executions"]["m1"]
        rec["test_gate"] = {"status": "sin_base", "reason": "sin repositorio git",
                            "requires_declaration": False}
        self.review()
        self.assertTrue(wr.test_gate_warnings(self.state, "m1"))


class LegacyTests(GateBase):
    def test_receipt_antiguo_sin_bloque_sigue_valido(self):
        self.seal()
        self.state["workflow_receipts"]["executions"]["m1"].pop("test_gate")
        self.assertEqual(wr.test_gate_warnings(self.state, "m1"), [])
        self.assertEqual(wr.test_gate_review_problems(self.state, "m1"), [])
        self.assertEqual(self.review_ok(), "pending")

    def review_ok(self):
        return wr.start_review(
            self.state, self.root, "m1", reviewer_id="a", implementer_id="b",
            mode="HUMAN_INDEPENDENT_REVIEW", now_iso="t",
        )["status"]


class MutateWarnTests(unittest.TestCase):
    """Camino real: mutate imprime el WARN de sin_base al sellar y al abrir review."""

    def setUp(self):
        twr.PilotClosureSequenceTests.setUp(self)

    def tearDown(self):
        twr.PilotClosureSequenceTests.tearDown(self)

    def test_sellado_y_review_avisan_de_sin_base(self):
        m = self.manager
        # Aprobación «antigua» (sin mapa de identidades, L9b): sin mapa ni base git
        # el gate sigue siendo sin_base y visible.
        with mock.patch.object(wr, "_test_baseline", return_value={}):
            m.mutate("record_approval_receipt", {
                "module": "M1", "approved_by": "builder", "evidence": "aprobación de M1",
                "risk_summary": "bajo", "adr_refs": ["ADR-001"],
            })
        msg = m.mutate("record_execution_receipt", {
            "module": "M1", "kind": "ok_automatizada", "command": "pytest",
            "exit_code": 0, "evidence": "tests pasaron con 1 passed en 0.05s",
            "base_ref": "no-existe",
        })
        self.assertIn("WARN FW-250", str(msg))
        self.assertIn("no pudo verificar", str(msg))
        msg = m.mutate("start_independent_review", {
            "module": "M1", "reviewer_id": "reviewer", "implementer_id": "builder",
            "mode": "INDEPENDENT_SUBAGENT_NO_ISOLATION",
        })
        self.assertIn("WARN FW-250", str(msg))


if __name__ == "__main__":
    unittest.main()
