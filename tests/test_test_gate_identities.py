"""N15 (L9b): el gate de FW-250 no depende de que el trabajo esté sin commitear.

Flujo real por `mutate` sobre un repo git temporal: aprobación → cambio de test y
de producción → TODO commiteado → sellado. La base es el mapa de identidades de
contenido guardado al aprobar, no el diff de git."""
import copy
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / ".agent" / "scripts"))
sys.path.insert(0, str(ROOT / "tests"))

import workflow_receipts as wr
import test_workflow_receipts as twr

EVID = "pytest -q: 12 passed, salida real de la verificación"


def git(root, *args):
    subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True)


class IdentityGateBase(unittest.TestCase):
    def setUp(self):
        twr.PilotClosureSequenceTests.setUp(self)
        self.addCleanup(twr.PilotClosureSequenceTests.tearDown, self)
        (self.root / "tests").mkdir(exist_ok=True)
        self.write("tests/test_app.py", "def test_a(): pass\n")
        self.write("tests/test_other.py", "def test_o(): pass\n")
        git(self.root, "init", "-q", "-b", "main")
        git(self.root, "config", "user.email", "t@t")
        git(self.root, "config", "user.name", "t")
        git(self.root, "add", "-A")
        git(self.root, "commit", "-q", "-m", "base")

    def write(self, rel, text):
        (self.root / rel).write_text(text, encoding="utf-8")

    def commit_all(self):
        git(self.root, "add", "-A")
        git(self.root, "commit", "-q", "-m", "trabajo")

    def approve(self, legacy=False):
        """`legacy=True` reproduce una aprobación sellada antes de L9b (sin mapa)."""
        with mock.patch.object(wr, "_test_baseline", return_value={}) if legacy \
                else mock.patch.object(wr, "_test_baseline", wraps=wr._test_baseline):
            self._approve()

    def _approve(self):
        self.manager.mutate("record_approval_receipt", {
            "module": "M1", "approved_by": "builder", "evidence": "aprobación de M1",
            "risk_summary": "bajo", "adr_refs": ["ADR-001"],
        })

    def seal(self, **extra):
        payload = {
            "module": "M1", "kind": "ok_automatizada", "command": "pytest",
            "exit_code": 0, "evidence": EVID,
        }
        payload.update(extra)
        return self.manager.mutate("record_execution_receipt", payload)

    def decl(self, path):
        return {"path": path, "reason": "cambia el contrato", "authorized_by": "builder"}

    def modificar_test_y_produccion_commiteado(self):
        self.write("tests/test_app.py", "def test_a(): assert True\n")
        self.write("src/app.py", "def run():\n    return 1\n")
        self.commit_all()


class N15Tests(IdentityGateBase):
    def test_todo_commiteado_sin_declarar_rechaza(self):
        self.approve()
        self.modificar_test_y_produccion_commiteado()
        with self.assertRaises(ValueError) as cm:
            self.seal()
        self.assertIn("tests/test_app.py", str(cm.exception))
        self.assertIn("FW-250", str(cm.exception))

    def test_todo_commiteado_con_declaracion_pasa(self):
        self.approve()
        self.modificar_test_y_produccion_commiteado()
        self.seal(test_changes=[self.decl("tests/test_app.py")])
        gate = self.manager.state_data["workflow_receipts"]["executions"]["M1"]["test_gate"]
        self.assertEqual(gate["status"], "ok")
        self.assertEqual(gate["modified_tests"], ["tests/test_app.py"])

    def test_test_borrado_y_commiteado_rechaza(self):
        self.approve()
        (self.root / "tests" / "test_other.py").unlink()
        self.write("src/app.py", "def run():\n    return 2\n")
        self.commit_all()
        with self.assertRaises(ValueError) as cm:
            self.seal()
        self.assertIn("tests/test_other.py", str(cm.exception))

    def test_test_nuevo_y_commiteado_pasa(self):
        self.approve()
        self.write("tests/test_nuevo.py", "def test_n(): pass\n")
        self.write("src/app.py", "def run():\n    return 3\n")
        self.commit_all()
        self.seal()

    def test_test_modificado_sin_tocar_produccion_pasa(self):
        self.approve()
        self.write("tests/test_app.py", "def test_a(): assert True\n")
        self.commit_all()
        self.seal()

    def test_el_review_rechaza_si_se_borra_la_declaracion(self):
        self.approve()
        self.modificar_test_y_produccion_commiteado()
        self.seal(test_changes=[self.decl("tests/test_app.py")])
        state = copy.deepcopy(self.manager.state_data)
        state["workflow_receipts"]["executions"]["M1"]["test_gate"]["test_changes"] = []
        with self.assertRaises(ValueError):
            wr.start_review(
                state, self.root, "M1", reviewer_id="a", implementer_id="b",
                mode="HUMAN_INDEPENDENT_REVIEW", now_iso="t",
            )

    def test_el_mapa_sobrevive_al_refresh_y_al_replay(self):
        self.approve()
        rec = self.manager.state_data["workflow_receipts"]["approvals"]["M1"]
        mapa = wr._approval_identities(self.root, rec)  # L4.3: por referencia al almacén
        self.assertIn("tests/test_app.py", mapa)
        self.assertNotIn("src/app.py", mapa)
        self.assertTrue(rec["prod_digest"])
        copia = copy.deepcopy(rec)
        self.assertEqual(wr._approval_identities(self.root, copia), mapa)


class LegacyApprovalTests(IdentityGateBase):
    def test_aprobacion_antigua_sin_mapa_usa_git_como_hoy(self):
        self.approve(legacy=True)
        self.write("tests/test_app.py", "def test_a(): assert True\n")
        self.write("src/app.py", "def run():\n    return 1\n")   # sin commitear
        with self.assertRaises(ValueError):
            self.seal(base_ref="HEAD")
        self.seal(base_ref="HEAD", test_changes=[self.decl("tests/test_app.py")])

    def test_sin_mapa_y_sin_base_sigue_siendo_sin_base_visible(self):
        self.approve(legacy=True)
        msg = self.seal(base_ref="no-existe")
        self.assertIn("WARN FW-250", str(msg))


class ApprovalPosteriorTests(IdentityGateBase):
    def test_sin_aprobacion_previa_cae_a_git(self):
        self.write("tests/test_app.py", "def test_a(): assert True\n")
        self.write("src/app.py", "def run():\n    return 1\n")
        with self.assertRaises(ValueError):
            self.seal(base_ref="HEAD")


class N16Tests(IdentityGateBase):
    """N16: volver a aprobar (record_approval_receipt) no borra la base de tests."""
    def test_reaprobacion_conserva_mapa_y_rechaza_cambios_declarados_previos(self):
        # Primera aprobación → se guarda test_identities
        self.approve()
        rec1 = self.manager.state_data["workflow_receipts"]["approvals"]["M1"]
        base1 = wr._approval_identities(self.root, rec1)  # L4.3: por referencia

        # Modificar y commitear el test y la producción
        self.write("tests/test_app.py", "def test_a(): assert True\n")
        self.write("src/app.py", "def run():\n    return 1\n")
        self.commit_all()

        # Re-aprobar el módulo (con el nuevo estado)
        self._approve()
        rec2 = self.manager.state_data["workflow_receipts"]["approvals"]["M1"]

        # El nuevo mapa no debería ser idéntico (contiene el test modificado)
        # pero se debe guardar test_identities_origin con el anterior
        self.assertIn("test_identities_origin_ref", rec2,
                      "se debe citar el mapa anterior como test_identities_origin_ref")
        self.assertEqual(wr._approval_identities(self.root, rec2), base1,
                         "el mapa de origen debe coincidir con el anterior")

        # Sellar SIN declarar el cambio. Debe rechazar porque el gate usa
        # test_identities_origin y ve que el test fue modificado
        with self.assertRaises(ValueError) as cm:
            self.seal()
        self.assertIn("tests/test_app.py", str(cm.exception),
                      "debe detectar el test modificado usando el mapa original")
        self.assertIn("FW-250", str(cm.exception))


if __name__ == "__main__":
    unittest.main()


class FW277BaseTests(IdentityGateBase):
    """FW-277 (5.2.2 L1): con mapa, el mapa manda; sin mapa, el commit de apertura."""

    def _otro_modulo_en_la_rama(self):
        # Rama de trabajo larga: otro módulo, ya cerrado, cambió un test y
        # producción ANTES de que M1 se aprobara (PA C1: 19 tests ajenos).
        git(self.root, "checkout", "-q", "-b", "trabajo")
        self.write("tests/test_other.py", "def test_o(): assert 1\n")
        self.write("src/otro.py", "X = 1\n")
        self.commit_all()

    def test_con_mapa_no_lista_tests_de_otros_modulos_previos_a_la_aprobacion(self):
        self._otro_modulo_en_la_rama()
        self.approve()
        self.write("src/app.py", "def run():\n    return 1\n")
        self.commit_all()
        self.seal()  # 5.2.1: rechazaba exigiendo declarar tests/test_other.py
        gate = self.manager.state_data["workflow_receipts"]["executions"]["M1"]["test_gate"]
        self.assertEqual(gate["base_kind"], "approval_map")
        self.assertEqual(gate["modified_tests"], [])

    def test_con_mapa_sigue_rechazando_los_tests_del_modulo(self):
        self._otro_modulo_en_la_rama()
        self.approve()
        self.write("tests/test_app.py", "def test_a(): assert True\n")
        self.write("src/app.py", "def run():\n    return 1\n")
        with self.assertRaises(ValueError) as cm:
            self.seal()
        msg = str(cm.exception)
        self.assertIn("tests/test_app.py", msg)
        self.assertNotIn("tests/test_other.py", msg)
        self.assertIn("mapa de tests de la aprobación", msg)
        self.assertIn("src/app.py", msg)
        self.assertNotIn("None", msg)

    def test_aprobacion_guarda_el_commit_de_apertura(self):
        self.approve()
        rec = self.manager.state_data["workflow_receipts"]["approvals"]["M1"]
        head = subprocess.run(["git", "-C", str(self.root), "rev-parse", "HEAD"],
                              capture_output=True, text=True).stdout.strip()
        self.assertEqual(rec["head_at_approval"], head)

    def test_sin_mapa_usa_el_commit_de_apertura_y_no_el_merge_base(self):
        self._otro_modulo_en_la_rama()
        self.approve(legacy=True)
        self.write("src/app.py", "def run():\n    return 1\n")
        self.seal()
        gate = self.manager.state_data["workflow_receipts"]["executions"]["M1"]["test_gate"]
        self.assertEqual(gate["base_kind"], "approval_head")
        self.assertEqual(gate["modified_tests"], [])

    def test_merge_base_avisa_de_trabajo_ajeno(self):
        self._otro_modulo_en_la_rama()
        real = wr._git_out

        def sin_head(root, *args):  # aprobación anterior a 5.2.2: sin head_at_approval
            return None if args == ("rev-parse", "HEAD") else real(root, *args)

        with mock.patch.object(wr, "_git_out", side_effect=sin_head):
            self.approve(legacy=True)
        self.assertIsNone(
            self.manager.state_data["workflow_receipts"]["approvals"]["M1"].get("head_at_approval")
        )
        self.write("src/app.py", "def run():\n    return 1\n")
        with self.assertRaises(ValueError) as cm:
            self.seal()
        self.assertIn("merge-base", str(cm.exception))
        self.assertIn("OTROS módulos", str(cm.exception))

    def test_tercera_aprobacion_conserva_el_mapa_original_n16(self):
        self.approve()
        base1 = wr._approval_identities(self.root, self.manager.state_data["workflow_receipts"]["approvals"]["M1"])
        for n in (1, 2):
            self.write("tests/test_app.py", f"def test_a(): assert {n}\n")
            self.commit_all()
            self._approve()
        rec = self.manager.state_data["workflow_receipts"]["approvals"]["M1"]
        self.assertEqual(wr._approval_identities(self.root, rec), base1)  # L4.3: por referencia
