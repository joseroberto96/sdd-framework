"""5.3.1 L4.3 (FW-357 y PA-FW-013): las identidades de test se guardan una vez.

El recibo de aprobación cita su mapa de identidades por referencia al almacén
`_project/approval_snapshots/` en lugar de llevarlo entero. Los recibos
anteriores, con el mapa inline, se leen igual; una migración no destructiva
adelgaza el estado. Con una base explícita, un test que solo aparece en el mapa
y que otro módulo cerrado cambió y selló queda cubierto, como sin base.
"""
import contextlib
import io
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / ".agent" / "scripts"
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(ROOT / "tests"))

import migrate_test_identities as mti  # noqa: E402
import state_core  # noqa: E402
import workflow_receipts as wr  # noqa: E402
import test_l6b_contrato as l6b  # noqa: E402
import test_test_gate_identities as tgi  # noqa: E402

FIXTURE_PRE_L4 = ROOT / "tests" / "fixtures" / "l4" / "recibo_pre_l4"


class ReferenciaTests(tgi.IdentityGateBase):
    def receipt(self) -> dict:
        return self.manager.state_data["workflow_receipts"]["approvals"]["M1"]

    def test_el_recibo_nuevo_cita_el_mapa_y_la_referencia_carga(self):
        self.approve()
        rec = self.receipt()
        self.assertNotIn("test_identities", rec)
        self.assertNotIn("test_identities_origin", rec)
        blob = self.root / "_project" / "approval_snapshots" / f"{rec['test_identities_ref']}.json"
        self.assertTrue(blob.is_file())
        mapa = wr._approval_identities(self.root, rec)
        self.assertEqual(mapa, wr._test_baseline(self.root)["test_identities"])
        self.assertIn("tests/test_app.py", mapa)
        self.assertTrue(rec["prod_digest"])

    def test_la_re_aprobacion_cita_el_mapa_original(self):
        self.approve()
        original = wr._approval_identities(self.root, self.receipt())
        for n in (1, 2):
            self.write("tests/test_app.py", f"def test_a(): assert {n}\n")
            self.commit_all()
            self._approve()
        rec = self.receipt()
        self.assertNotIn("test_identities_origin", rec)
        self.assertEqual(wr._approval_identities(self.root, rec), original)

    def _mapa_estropeado_cae_al_commit_de_apertura(self, estropear):
        self.approve()
        blob = self.root / "_project" / "approval_snapshots" / f"{self.receipt()['test_identities_ref']}.json"
        estropear(blob)
        self.assertIsNone(wr._approval_identities(self.root, self.receipt()))
        self.modificar_test_y_produccion_commiteado()
        with self.assertRaises(ValueError) as cm:
            self.seal()
        msg = str(cm.exception)
        self.assertIn("tests/test_app.py", msg)
        self.assertIn("commit de apertura", msg)
        self.assertIn("mapa de identidades no disponible", msg)

    def test_un_mapa_ausente_cae_al_commit_de_apertura_y_lo_dice(self):
        self._mapa_estropeado_cae_al_commit_de_apertura(lambda blob: blob.unlink())

    def test_un_mapa_manipulado_cae_al_commit_de_apertura_y_lo_dice(self):
        self._mapa_estropeado_cae_al_commit_de_apertura(
            lambda blob: blob.write_text('{"tests/test_app.py": "0"}', encoding="utf-8"))

    def test_una_referencia_que_no_es_un_digest_no_carga(self):
        for ref in ("../../03_project_state", "", 7, "a" * 63):
            with self.subTest(ref=ref):
                self.assertIsNone(wr._approval_identities(self.root, {"test_identities_ref": ref}))


class ReciboAntiguoTests(unittest.TestCase):
    """El recibo de antes de L4 (mapas inline) se lee igual."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name) / "proyecto"
        shutil.copytree(FIXTURE_PRE_L4, self.root)
        self.state_path = self.root / "_project" / "03_project_state.json"
        self.state = json.loads(self.state_path.read_text("utf-8"))

    def receipts(self) -> dict:
        return self.state["workflow_receipts"]["approvals"]

    def test_el_mapa_inline_se_lee_igual(self):
        for module, receipt in self.receipts().items():
            with self.subTest(module):
                self.assertEqual(wr._approval_identities(self.root, receipt), receipt["test_identities"])
                self.assertEqual(wr._changes_vs_baseline(self.root, receipt), ({}, False))
        (self.root / "tests" / "test_m1.py").write_text("def test_m1():\n    assert 1\n", encoding="utf-8")
        self.assertEqual(wr._changes_vs_baseline(self.root, self.receipts()["M1"])[0], {"tests/test_m1.py": "M"})

    def test_la_migracion_en_seco_no_escribe(self):
        before = self.state_path.read_bytes()
        rc = mti.main(["--state", str(self.state_path)])
        self.assertEqual(rc, 0)
        self.assertEqual(self.state_path.read_bytes(), before)
        self.assertEqual(list(self.state_path.parent.glob("*.bak")), [])

    def test_la_migracion_cita_por_referencia_y_no_cambia_veredictos(self):
        (self.root / "tests" / "test_m1.py").write_text("def test_m1():\n    assert 1\n", encoding="utf-8")
        antes = {m: (wr._approval_identities(self.root, r), wr._changes_vs_baseline(self.root, r))
                 for m, r in self.receipts().items()}
        self.assertEqual(mti.migrate_state(self.state, self.root), 2)
        for module, receipt in self.receipts().items():
            with self.subTest(module):
                self.assertNotIn("test_identities", receipt)
                self.assertIn("test_identities_ref", receipt)
                despues = (wr._approval_identities(self.root, receipt), wr._changes_vs_baseline(self.root, receipt))
                self.assertEqual(despues, antes[module])
                self.assertEqual(list(wr.approval_problems(self.root, self.state, module)), [])
        self.assertEqual(mti.migrate_state(self.state, self.root), 0)  # idempotente

    def test_la_migracion_escrita_deja_copia_valida_y_adelgaza(self):
        before_bytes = self.state_path.read_bytes()
        self.assertEqual(mti.main(["--state", str(self.state_path), "--write"]), 0)
        [backup] = self.state_path.parent.glob("03_project_state.json.fw357-*.bak")
        self.assertEqual(backup.read_bytes(), before_bytes)
        migrated = json.loads(self.state_path.read_text("utf-8"))
        self.assertEqual(mti._inline_maps(migrated), [])
        self.assertLess(self.state_path.stat().st_size, len(before_bytes))
        self.assertEqual(mti.main(["--state", str(self.state_path), "--write"]), 0)  # nada que migrar

    def test_la_migracion_conserva_el_origen_vacio_como_hoy(self):
        receipt = self.receipts()["M1"]
        receipt["test_identities_origin"] = {}
        esperado = wr._approval_identities(self.root, receipt)
        mti.migrate_state(self.state, self.root)
        self.assertEqual(wr._approval_identities(self.root, receipt), esperado)
        del receipt["test_identities_ref"]
        self.assertIsNone(wr._approval_identities(self.root, receipt))

    def test_por_la_linea_de_ordenes(self):
        out = subprocess.run(
            [sys.executable, "-B", str(SCRIPTS / "migrate_test_identities.py"), "--state", str(self.state_path)],
            capture_output=True, text=True, check=False,
        )
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertIn("inline", out.stdout)


class RevisionSecurityTests(unittest.TestCase):
    """Hallazgos 5 y 6 de la revisión `security` de L4, sobre la fixture anterior a L4."""

    setUp = ReciboAntiguoTests.setUp
    receipts = ReciboAntiguoTests.receipts

    def migrate(self, *extra):
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            rc = mti.main(["--state", str(self.state_path), *extra])
        return rc, out.getvalue()

    def test_un_fichero_plantado_con_el_nombre_del_digest_se_reescribe(self):
        digest = wr._stable_hash(self.receipts()["M1"]["test_identities"])
        (self.root / "_project" / "approval_snapshots" / f"{digest}.json").write_text("{}", encoding="utf-8")
        self.assertEqual(self.migrate("--write")[0], 0)
        migrated = json.loads(self.state_path.read_text("utf-8"))["workflow_receipts"]["approvals"]["M1"]
        self.assertEqual(wr._approval_identities(self.root, migrated), self.receipts()["M1"]["test_identities"])

    def test_si_un_mapa_no_se_lee_igual_no_escribe(self):
        before = self.state_path.read_bytes()
        with mock.patch.object(wr, "store_derived_snapshot", return_value="0" * 64):
            rc, out = self.migrate("--write")
        self.assertEqual(rc, 2)
        self.assertIn("no se lee igual", out)
        self.assertEqual(self.state_path.read_bytes(), before)
        self.assertEqual(list(self.state_path.parent.glob("*.bak")), [])

    def test_toma_el_candado_del_estado_que_migra_y_lo_devuelve(self):
        usados = []
        real = state_core.state_write_lock

        @contextlib.contextmanager
        def candado(*args, **kwargs):
            usados.append(state_core.STATE_FILE_PATH)
            with real(*args, **kwargs):
                yield

        original = state_core.STATE_FILE_PATH
        with mock.patch.object(state_core, "state_write_lock", candado):
            self.assertEqual(self.migrate("--write")[0], 0)
        self.assertEqual(usados, [self.state_path])
        self.assertEqual(state_core.STATE_FILE_PATH, original)

    def test_una_segunda_migracion_no_pisa_la_primera_copia(self):
        original = self.state_path.read_bytes()
        self.migrate("--write")
        state = json.loads(self.state_path.read_text("utf-8"))
        state["workflow_receipts"]["approvals"]["M1"]["test_identities"] = {"tests/x.py": "1" * 64}
        self.state_path.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        self.migrate("--write")
        copias = [b.read_bytes() for b in self.state_path.parent.glob("*.bak")]
        self.assertIn(original, copias)

    def test_sin_git_avisa_de_que_la_copia_cuenta_en_la_huella(self):
        rc, out = self.migrate("--write")
        self.assertEqual(rc, 0)
        self.assertIn("AVISO: git no ignora la copia", out)

    def test_el_lector_no_devuelve_algo_que_no_es_un_mapa(self):
        self.assertIsNone(wr._approval_identities(self.root, {"test_identities_origin": ["x"], "test_identities": {"a": "b"}}))


class MapaIlegibleConBaseTests(tgi.IdentityGateBase):
    """Hallazgo 2: con el mapa ilegible, base_ref no puede esconder un cambio propio."""

    def preparar(self):
        self.approve()
        rec = self.manager.state_data["workflow_receipts"]["approvals"]["M1"]
        self.modificar_test_y_produccion_commiteado()
        (self.root / "_project" / "approval_snapshots" / f"{rec['test_identities_ref']}.json").unlink()

    def test_sin_declarar_se_rechaza_contra_el_commit_de_apertura(self):
        self.preparar()
        with self.assertRaises(ValueError) as cm:
            self.seal(base_ref="HEAD")
        self.assertIn("tests/test_app.py", str(cm.exception))
        self.assertIn("commit de apertura", str(cm.exception))

    def test_declarado_pasa_y_el_cierre_avisa(self):
        self.preparar()
        self.seal(base_ref="HEAD", test_changes=[self.decl("tests/test_app.py")])
        gate = self.manager.state_data["workflow_receipts"]["executions"]["M1"]["test_gate"]
        self.assertTrue(gate["identity_map_missing"])
        self.assertIn("WARN FW-357", " ".join(wr.test_gate_warnings(self.manager.state_data, "M1")))


class BaseExplicitaTests(l6b.PruebasBase):
    """PA-FW-013: con base_ref explícito, el mapa histórico ya no exige los tests
    que otro módulo cerrado cambió y selló antes de esa base."""

    def m2_covers_and_commits(self) -> str:
        self.approve()
        self.m2_covers_test_a()
        l6b._git(self.root, "add", "-A")
        l6b._git(self.root, "commit", "-qm", "M2")
        return subprocess.run(["git", "-C", str(self.root), "rev-parse", "HEAD"],
                              capture_output=True, text=True, check=True).stdout.strip()

    def test_cadena_de_un_modulo_cerrado_cubre_con_base_explicita(self):
        base = self.m2_covers_and_commits()
        self.touch_prod("M1")
        gate = self.execute("M1", l6b.ts(20), base_ref=base)
        self.assertEqual(gate["modified_tests"], [])
        self.assertEqual(gate.get("covered_tests"), [{"path": "tests/test_a.py", "by": ["M2"]}])

    def test_un_cambio_propio_commiteado_antes_de_la_base_sigue_pidiendose(self):
        self.approve()
        self.test_a.write_text("def test_a(): pass  # debilitado por M1\n")
        self.touch_prod("M1")
        l6b._git(self.root, "add", "-A")
        l6b._git(self.root, "commit", "-qm", "M1 esconde su cambio")
        self.assert_rejected("tests/test_a.py", base_ref="HEAD")

    def test_lo_que_git_ve_contra_la_base_no_se_descuenta(self):
        self.approve()
        self.m2_covers_test_a()
        self.touch_prod("M1")
        self.assert_rejected("tests/test_a.py", base_ref="HEAD")

    def test_un_modulo_abierto_no_cubre(self):
        base = self.m2_covers_and_commits()
        self.state["modules"]["completed"].remove("M2")
        self.touch_prod("M1")
        self.assert_rejected("tests/test_a.py", base_ref=base)


if __name__ == "__main__":
    unittest.main()
