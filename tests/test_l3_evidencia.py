#!/usr/bin/env python3
"""L3.3 de la 5.3.1: evidencia (TL-FW-323) y recibo de ejecución (FW-299, D4-A).

TL-FW-323: `retirado: <ruta>` se comprueba al revés: la ruta no existe y aparece
borrada (o como origen de un renombrado) en el diff del módulo, contra la base
de su aprobación. Una ruta retirada que sigue existiendo, o que nunca estuvo en
el diff del módulo, es FAIL.

FW-299 (mínimo, D4-A): `tests_run` es opcional en el recibo; si vale 0 es FAIL,
y una herramienta de verificación ausente en la evidencia de `ok_automatizada`
es FAIL aunque el comando saliera con 0.
"""
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / ".agent" / "scripts"))

import action_contracts  # noqa: E402
import state_core  # noqa: E402
import validate_acceptance  # noqa: E402
import workflow_receipts  # noqa: E402

MODULE = "swe_cleanup"
ENV = {"GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_NOSYSTEM": "1",
       "PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": os.environ.get("HOME", "/tmp")}


def git(root, *args):
    return subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True,
                          text=True, env=ENV).stdout.strip()


def acceptance(evidence):
    return (f"## Módulo 9 — Limpieza (`{MODULE}`)\n### Criterios\n"
            f"- [x] AC-SWE-09 Retirar el módulo de graduación — evidencia: {evidence}\n")


class RetiredPathTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        git(self.root, "init", "-q", "-b", "main")
        git(self.root, "config", "user.email", "t@t")
        git(self.root, "config", "user.name", "t")
        (self.root / "src").mkdir()
        for name in ("graduation.py", "keep.py", "old.py", "antes.py"):
            (self.root / "src" / name).write_text(f"{name} = 1\n", encoding="utf-8")
        git(self.root, "add", ".")
        git(self.root, "commit", "-qm", "base")
        git(self.root, "rm", "-q", "src/antes.py")
        git(self.root, "commit", "-qm", "retira antes.py fuera del módulo")
        head = git(self.root, "rev-parse", "HEAD")
        self.state = {"workflow_receipts": {"approvals": {MODULE: {"head_at_approval": head}}}}
        git(self.root, "rm", "-q", "src/graduation.py")
        git(self.root, "mv", "src/old.py", "src/new.py")
        git(self.root, "commit", "-qm", "trabajo del módulo")

    def tearDown(self):
        self._tmp.cleanup()

    def gate(self, evidence, state="default"):
        state = self.state if state == "default" else state
        return validate_acceptance.evidence_gate(acceptance(evidence), MODULE, root=self.root, state=state)

    def test_tl_fw_323_ruta_retirada_y_borrada_en_el_diff_pasa(self):
        self.assertEqual(self.gate("retirado: src/graduation.py"), [])

    def test_renombrado_cuenta_como_retirada(self):
        self.assertEqual(self.gate("retirado: src/old.py"), [])

    def test_ruta_retirada_que_sigue_existiendo_falla(self):
        problems = self.gate("retirado: src/keep.py")
        self.assertEqual(len(problems), 1, problems)
        self.assertIn("sigue existiendo", problems[0])

    def test_ruta_que_nunca_existio_falla(self):
        problems = self.gate("retirado: src/nunca.py")
        self.assertEqual(len(problems), 1, problems)
        self.assertIn("no aparece borrada en el diff del módulo", problems[0])

    def test_ruta_borrada_fuera_del_modulo_falla(self):
        problems = self.gate("retirado: src/antes.py")
        self.assertEqual(len(problems), 1, problems)
        self.assertIn("no aparece borrada en el diff del módulo", problems[0])

    def test_borrado_sin_commitear_cuenta(self):
        (self.root / "src" / "keep.py").unlink()
        self.assertEqual(self.gate("retirado: src/keep.py"), [])

    def test_sin_aprobacion_usa_la_rama_base(self):
        """Sin head_at_approval, la base es el merge-base con main: aquí HEAD."""
        problems = self.gate("retirado: src/graduation.py", state={})
        self.assertTrue(any("no aparece borrada" in p for p in problems), problems)

    def test_otras_rutas_citadas_siguen_comprobandose(self):
        problems = self.gate("retirado: src/graduation.py; tests/test_nada.py::test_x")
        self.assertEqual(len(problems), 1, problems)
        self.assertIn("artefacto citado inexistente: tests/test_nada.py", problems[0])

    def test_url_o_remoto_tras_retirado_no_esquivan_la_comprobacion(self):
        """Revisión `security` de L3, hallazgo 1: una sola extracción para las dos comprobaciones."""
        for middle in ("https://ejemplo.org/x", "git@host:o/r.git"):
            with self.subTest(middle=middle):
                problems = self.gate(f"retirado: {middle} tests/test_inventado.py::test_ok rc=0")
                self.assertTrue(any("tests/test_inventado.py" in p for p in problems), problems)

    def test_base_de_aprobacion_que_no_es_un_commit_falla_sin_ejecutar_nada(self):
        """Revisión `security` de L3, hallazgo 4: la base se verifica antes de pasarla a git."""
        written = self.root / "pisado.txt"
        for head in (f"--output={written}", "HEAD~1", "no-es-un-commit", "abcdef0"):
            with self.subTest(head=head):
                state = {"workflow_receipts": {"approvals": {MODULE: {"head_at_approval": head}}}}
                problems = self.gate("retirado: src/graduation.py", state=state)
                self.assertTrue(any("no se pudo comprobar el borrado" in p for p in problems), problems)
        self.assertFalse(written.exists())

    def test_sin_root_solo_se_comprueba_la_forma(self):
        problems = validate_acceptance.evidence_gate(acceptance("retirado: src/keep.py"), MODULE)
        self.assertEqual(problems, [])

    def test_fuera_de_git_falla_cerrado(self):
        with tempfile.TemporaryDirectory() as d:
            problems = validate_acceptance.evidence_gate(
                acceptance("retirado: src/graduation.py"), MODULE, root=Path(d), state={})
        self.assertEqual(len(problems), 1, problems)
        self.assertIn("no se pudo comprobar el borrado", problems[0])

    def test_mantenimiento_conserva_la_comprobacion_de_existencia(self):
        """`missing_artifacts` sin más contexto sigue marcando la ruta inexistente."""
        body = "- [x] c — evidencia: retirado: src/nunca.py"
        self.assertEqual(validate_acceptance.missing_artifacts(body, self.root), ["src/nunca.py"])

    def test_el_mensaje_de_inexistente_nombra_las_salidas(self):
        problems = self.gate("tests/test_nada.py::test_x rc=0")
        self.assertIn("retirado:", problems[0])
        self.assertIn("(no es ruta del repo)", problems[0])


def execution_state(**receipt):
    base = {"kind": "ok_automatizada", "command": "scripts/quality_gate.sh", "exit_code": 0,
            "evidence": "quality_gate.sh → 41 passed, exit 0", "diff_hash": "x", "status": "recorded"}
    base.update(receipt)
    return {"workflow_receipts": {"executions": {MODULE: base}}, "module_specs": {}}


class ExecutionReceiptTests(unittest.TestCase):
    def test_recibo_normal_pasa(self):
        self.assertEqual(workflow_receipts.execution_problems(execution_state(), MODULE), [])
        self.assertEqual(workflow_receipts.execution_problems(execution_state(tests_run=41), MODULE), [])

    def test_herramienta_ausente_con_exit_0_falla(self):
        for text in ("quality_gate.sh: mypy: command not found — resto OK, exit 0",
                     "python -m pytest → /usr/bin/python: No module named pytest, exit 0",
                     "'ruff' is not recognized as an internal or external command, exit 0"):
            with self.subTest(text):
                problems = workflow_receipts.execution_problems(execution_state(evidence=text), MODULE)
                self.assertTrue(any("herramienta de verificación ausente" in p for p in problems), problems)

    def test_herramienta_ausente_en_ok_manual_no_cuenta(self):
        state = execution_state(kind="ok_manual", exit_code=None,
                                evidence="el builder confirmó en pantalla; mypy: command not found en su equipo")
        self.assertEqual(workflow_receipts.execution_problems(state, MODULE), [])

    def test_cero_tests_falla(self):
        problems = workflow_receipts.execution_problems(execution_state(tests_run=0), MODULE)
        self.assertTrue(any("no ejecutó ningún test" in p for p in problems), problems)

    def test_exit_127_sigue_fallando(self):
        problems = workflow_receipts.execution_problems(execution_state(exit_code=127), MODULE)
        self.assertTrue(problems)

    def test_record_execution_guarda_y_valida_tests_run(self):
        with tempfile.TemporaryDirectory() as d:
            state = {"workflow_receipts": {"approvals": {}, "executions": {}}}
            receipt = workflow_receipts.record_execution(
                state, Path(d), MODULE, kind="ok_automatizada", command="pytest -q",
                evidence="pytest -q → 12 passed in 0.3s", exit_code=0,
                now_iso="2026-10-21T00:00:00Z", tests_run=12)
            self.assertEqual(receipt["tests_run"], 12)
            receipt = workflow_receipts.record_execution(
                state, Path(d), MODULE, kind="ok_automatizada", command="pytest -q",
                evidence="pytest -q → 12 passed in 0.3s", exit_code=0,
                now_iso="2026-10-21T00:00:00Z")
            self.assertNotIn("tests_run", receipt)
            for bad in (-1, "12", 1.5, True):
                with self.subTest(bad=bad), self.assertRaises(ValueError):
                    workflow_receipts.record_execution(
                        state, Path(d), MODULE, kind="ok_automatizada", command="pytest -q",
                        evidence="pytest -q → 12 passed in 0.3s", exit_code=0,
                        now_iso="2026-10-21T00:00:00Z", tests_run=bad)

    def test_la_accion_acepta_tests_run(self):
        self.assertIn("tests_run", state_core._STRICT_PAYLOAD_KEYS["record_execution_receipt"])
        self.assertIn("tests_run", action_contracts.ACTION_DESCRIPTIONS["record_execution_receipt"]["fields"])

    def test_verificar_ejecutable_pide_tests_run(self):
        text = (ROOT / ".agent/workflows/verificar-ejecutable.md").read_text("utf-8")
        self.assertIn('"tests_run"', text)


if __name__ == "__main__":
    unittest.main()
