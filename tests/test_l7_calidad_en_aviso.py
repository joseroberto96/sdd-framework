"""5.3.1 L7 — calidad visible, en aviso (S6: nunca bloquea ni cambia el veredicto del cierre).

L7.1: contrato perezoso `_project/quality.yaml` (solo se lee si existe; el framework no
instala nada: una herramienta que falta se dice y se sigue).
L7.2: trinquete cosechado de TL: diagnósticos sin línea ni columna, contados con
multiplicidad frente a `quality_baseline_<nombre>.txt`; lo nuevo sale marcado.
L7.3: `validate_closure.py` (paso 0 de `/cierre-modulo`) adjunta la calidad como aviso:
mismo rc y mismas líneas que antes, más las de calidad.
L7.4: la plantilla se mide con `--calidad docs/quality.yaml`, fuera de
`_project/` (semilla de los proyectos nuevos) y de `.agent/` (se propaga).
"""
import contextlib
import io
import json
import shlex
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / ".agent" / "scripts"))

import validate_closure as vc  # noqa: E402
import validate_no_stubs  # noqa: E402

A = "pkg/a.py:10:5: C901 `carga` is too complex (12 > 10)"
A_MOVED = "pkg/a.py:14:5: C901 `carga` is too complex (12 > 10)"
B = "pkg/b.py:3:1: E501 Line too long (120 > 100)"
# Imprime un fichero y sale con un código; con argumentos de más sale con 99 (no deben llegarle).
TOOL = ("import sys\nsys.stdout.write(open(sys.argv[1], encoding='utf-8').read())\n"
        "sys.exit(int(sys.argv[2]) if len(sys.argv) == 3 else 99)\n")


class QualityCase(unittest.TestCase):
    """Proyecto temporal con `_project/` y una herramienta simulada que imprime un fichero."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        (self.root / "_project").mkdir()
        (self.root / "tool.py").write_text(TOOL, encoding="utf-8")

    def tool(self, name, lines, code=0):
        """Orden de una herramienta del stack que imprime `lines`, una por línea, y sale con `code`."""
        (self.root / f"{name}.out").write_text("".join(line + "\n" for line in lines), encoding="utf-8")
        parts = (sys.executable, str(self.root / "tool.py"), str(self.root / f"{name}.out"), str(code))
        return " ".join(shlex.quote(part) for part in parts)

    def config(self, text):
        (self.root / "_project" / "quality.yaml").write_text(text, encoding="utf-8")

    def quality(self):
        return vc.quality_warnings(self.root)


class ContratoPerezoso(QualityCase):
    """L7.1."""

    def test_sin_quality_yaml_no_configurada_y_no_ejecuta_nada(self):
        with mock.patch("subprocess.run") as run:
            self.assertEqual(self.quality(), ["calidad: no configurada"])
        run.assert_not_called()

    def test_diagnosticos_y_resumen(self):
        self.config(f"commands:\n  ruff: {self.tool('ruff', [A, B, 'Found 2 errors.'], 1)}\n")
        out = self.quality()
        self.assertTrue(out[0].startswith("calidad: ruff (rc 1): 2 diagnóstico(s)"), out)
        self.assertEqual(out[1:], [f"calidad: ruff: {A}", f"calidad: ruff: {B}"])

    def test_herramienta_ausente_se_dice_y_se_sigue(self):
        self.config("commands:\n  ruff: herramienta-que-no-existe-l7 check .\n")
        out = self.quality()
        self.assertEqual(len(out), 1, out)
        self.assertTrue(out[0].startswith("calidad: ruff: no se pudo ejecutar ("), out)
        self.assertTrue(out[0].endswith("; se sigue"), out)

    def test_una_que_falta_no_corta_las_demas(self):
        self.config(f"commands:\n  falta: herramienta-que-no-existe-l7\n  ruff: {self.tool('ruff', [B], 1)}\n")
        out = self.quality()
        self.assertTrue(out[0].startswith("calidad: falta: no se pudo ejecutar"), out)
        self.assertTrue(out[1].startswith("calidad: ruff (rc 1): 1 diagnóstico(s)"), out)
        self.assertEqual(out[2:], [f"calidad: ruff: {B}"])

    def test_comillas_sin_cerrar(self):
        self.config("commands:\n  ruff: ruff check 'sin cerrar\n")
        out = self.quality()
        self.assertEqual(len(out), 1, out)
        self.assertIn("no se pudo ejecutar (No closing quotation); se sigue", out[0])

    def test_sin_comandos(self):
        for text in ("# vacío\ncommands:\n", "commands:\n  ruff: # solo un comentario\n"):
            self.config(text)
            with mock.patch("subprocess.run") as run:
                self.assertEqual(self.quality(), ["calidad: quality.yaml sin comandos («nombre: comando» bajo commands:)"])
            run.assert_not_called()

    def test_ilegible(self):
        (self.root / "_project" / "quality.yaml").write_bytes(b"commands:\n  ruff: \xff\xfe\n")
        out = self.quality()
        self.assertEqual(len(out), 1, out)
        self.assertTrue(out[0].startswith("calidad: quality.yaml ilegible ("), out)
        self.assertTrue(out[0].endswith("; se sigue"), out)

    def test_tiempo_agotado(self):
        self.config("commands:\n  lenta: herramienta-lenta\n")
        with mock.patch("subprocess.run", side_effect=subprocess.TimeoutExpired("herramienta-lenta", 600)):
            out = self.quality()
        self.assertEqual(len(out), 1, out)
        self.assertTrue(out[0].startswith("calidad: lenta: no se pudo ejecutar ("), out)

    def test_sin_shell_en_la_raiz_y_sin_entrada(self):
        self.config("commands:\n  ruff: ruff check --select C901 'src/con espacio' | cat\n")
        done = subprocess.CompletedProcess([], 0, "", "")
        with mock.patch("subprocess.run", return_value=done) as run:
            self.quality()
        args, kwargs = run.call_args
        self.assertEqual(args[0], ["ruff", "check", "--select", "C901", "src/con espacio", "|", "cat"])
        self.assertFalse(kwargs.get("shell", False))
        self.assertEqual(kwargs["cwd"], self.root)
        self.assertEqual(kwargs["stdin"], subprocess.DEVNULL)
        self.assertEqual(kwargs["timeout"], 600)

    def test_comentario_al_final_no_llega_a_la_herramienta(self):
        self.config(f"commands:\n  ruff: {self.tool('ruff', [B], 1)}   # comentario de YAML\n")
        out = self.quality()
        self.assertTrue(out[0].startswith("calidad: ruff (rc 1): 1 diagnóstico(s)"), out)
        self.assertEqual(out[1:], [f"calidad: ruff: {B}"])

    def test_salida_sin_ruta_linea_adjunta_sus_ultimas_lineas(self):
        report = [f"linea {n}" for n in range(1, 8)] + ["", "Found 1 clones."]
        self.config(f"commands:\n  jscpd: {self.tool('jscpd', report)}\n")
        out = self.quality()
        self.assertTrue(out[0].startswith("calidad: jscpd (rc 0): 0 diagnóstico(s)"), out)
        self.assertEqual(out[1:], [f"calidad: jscpd: {line}" for line in
                                   ("linea 4", "linea 5", "linea 6", "linea 7", "Found 1 clones.")])

    def test_como_mucho_20_diagnosticos_y_el_total_en_el_resumen(self):
        many = [f"pkg/m.py:{n}:1: E501 Line too long" for n in range(1, 26)]
        self.config(f"commands:\n  ruff: {self.tool('ruff', many, 1)}\n")
        out = self.quality()
        self.assertTrue(out[0].startswith("calidad: ruff (rc 1): 25 diagnóstico(s)"), out)
        self.assertEqual(len(out), 21)

    def test_el_modelo_de_la_plantilla_es_una_orden_de_ruff(self):
        model = ROOT / ".agent" / "templates" / "quality.yaml.base"
        done = subprocess.CompletedProcess([], 0, "", "")
        with mock.patch("subprocess.run", return_value=done) as run:
            vc.quality_warnings(self.root, model)
        self.assertEqual(run.call_count, 1)
        self.assertEqual(run.call_args[0][0],
                         ["ruff", "check", "--output-format", "concise", "--extend-exclude", ".agent", "."])

    def test_los_ejemplos_del_modelo_se_descomentan_limpios(self):
        model = (ROOT / ".agent" / "templates" / "quality.yaml.base").read_text(encoding="utf-8")
        self.config(model.replace("# eslint:", "eslint:").replace("# jscpd:", "jscpd:"))
        done = subprocess.CompletedProcess([], 0, "", "")
        with mock.patch("subprocess.run", return_value=done) as run:
            self.quality()
        self.assertEqual([call[0][0][:3] for call in run.call_args_list],
                         [["ruff", "check", "--output-format"], ["npx", "--no", "eslint"], ["npx", "--no", "jscpd"]])
        self.assertEqual(run.call_args_list[1][0][0], ["npx", "--no", "eslint", "--format", "unix", "."])

    def test_stdout_sin_salto_final_no_se_pega_a_stderr(self):
        code = "import sys; sys.stdout.write('a.py:1:1: E1 x'); sys.stderr.write('b.py:2:2: E2 y')"
        self.config(f"commands:\n  t: {shlex.quote(sys.executable)} -c {shlex.quote(code)}\n")
        out = self.quality()
        self.assertTrue(out[0].startswith("calidad: t (rc 0): 2 diagnóstico(s)"), out)


class Trinquete(QualityCase):
    """L7.2."""

    def ruff(self, today, baseline=None):
        self.config(f"commands:\n  ruff: {self.tool('ruff', today, 1)}\n")
        if baseline is not None:
            (self.root / "_project" / "quality_baseline_ruff.txt").write_text(baseline, encoding="utf-8")
        return self.quality()

    def test_lo_nuevo_sale_marcado_y_lo_que_ya_estaba_no_aunque_cambie_de_linea(self):
        out = self.ruff([A_MOVED, B], f"{A}\nFound 1 error.\n")
        self.assertEqual(out, ["calidad: ruff (rc 1): 2 diagnóstico(s), 1 nuevo(s) frente a quality_baseline_ruff.txt",
                               f"calidad: ruff: NUEVO {B}"])

    def test_multiplicidad(self):
        out = self.ruff([A, A_MOVED], f"{A}\n")
        self.assertEqual(out, ["calidad: ruff (rc 1): 2 diagnóstico(s), 1 nuevo(s) frente a quality_baseline_ruff.txt",
                               f"calidad: ruff: NUEVO {A_MOVED}"])

    def test_linea_base_completa_cero_nuevos(self):
        out = self.ruff([A, B], f"{B}\n{A}\nFound 2 errors.\n")
        self.assertEqual(out, ["calidad: ruff (rc 1): 2 diagnóstico(s), 0 nuevo(s) frente a quality_baseline_ruff.txt"])

    def test_linea_base_con_bom(self):
        (self.root / "_project" / "quality_baseline_ruff.txt").write_text(f"{A}\n", encoding="utf-8-sig")
        out = self.ruff([A], None)
        self.assertEqual(out, ["calidad: ruff (rc 1): 1 diagnóstico(s), 0 nuevo(s) frente a quality_baseline_ruff.txt"])

    def test_sin_linea_base_no_se_marca_nada(self):
        out = self.ruff([A, B])
        self.assertEqual(out, ["calidad: ruff (rc 1): 2 diagnóstico(s), sin línea base",
                               f"calidad: ruff: {A}", f"calidad: ruff: {B}"])


class CierreConCalidad(QualityCase):
    """L7.3: el cierre adjunta la calidad y su veredicto no cambia."""

    def close(self, gate=(), completed=False, quality=True):
        """`main` con los gates forzados a OK (o a FAIL con `gate`); `quality=False` = antes de L7."""
        status = "completed" if completed else "ready_for_validation"
        state = self.root / "_project" / "03_project_state.json"
        state.write_text(json.dumps({"module_statuses": {"M1": status}}), encoding="utf-8")
        (self.root / "_project" / "08_module_acceptance.md").write_text("# ACs\n", encoding="utf-8")
        buf = io.StringIO()
        with contextlib.ExitStack() as st:
            for name, value in (("ROOT", self.root), ("STATE", state),
                                ("ACCEPTANCE", self.root / "_project" / "08_module_acceptance.md")):
                st.enter_context(mock.patch.object(vc, name, value))
            for name in ("ac_coverage_problems", "client_seal_problems"):
                st.enter_context(mock.patch.object(vc, name, return_value=[]))
            st.enter_context(mock.patch.object(vc, "closure_gate", return_value=list(gate)))
            st.enter_context(mock.patch.object(vc._wr, "execution_problems", return_value=[]))
            st.enter_context(mock.patch.object(vc._wr, "workspace_diff_hash", return_value=None))
            st.enter_context(mock.patch.object(vc._wr, "receipt_warnings", return_value=["aviso de recibos"]))
            st.enter_context(mock.patch.object(validate_no_stubs, "stub_problems", return_value=[]))
            if not quality:
                st.enter_context(mock.patch.object(vc, "quality_warnings", return_value=[]))
            st.enter_context(contextlib.redirect_stdout(buf))
            rc = vc.main(["validate_closure.py", "M1"])
        return rc, buf.getvalue().splitlines()

    def assert_same_plus(self, gate, extra):
        rc_before, before = self.close(gate, quality=False)
        rc, out = self.close(gate)
        self.assertEqual(rc, rc_before)
        at = before.index("- AVISO: aviso de recibos") + 1
        self.assertEqual(out, before[:at] + [f"- AVISO: {line}" for line in extra] + before[at:])
        return rc

    def test_sin_quality_yaml_la_salida_de_hoy_mas_no_configurada(self):
        self.assertEqual(self.assert_same_plus((), ["calidad: no configurada"]), 0)
        self.assertEqual(self.assert_same_plus(("motivo de prueba",), ["calidad: no configurada"]), 1)

    def test_con_quality_yaml_el_informe_aparece_y_no_bloquea(self):
        self.config(f"commands:\n  ruff: {self.tool('ruff', [A, B, 'Found 2 errors.'], 1)}\n")
        report = ["calidad: ruff (rc 1): 2 diagnóstico(s), sin línea base", f"calidad: ruff: {A}", f"calidad: ruff: {B}"]
        self.assertEqual(self.assert_same_plus((), report), 0)
        self.assertEqual(self.assert_same_plus(("motivo de prueba",), report), 1)

    def test_una_herramienta_que_falta_no_rompe_el_cierre(self):
        self.config("commands:\n  ruff: herramienta-que-no-existe-l7 check .\n")
        rc, out = self.close()
        self.assertEqual(rc, 0)
        self.assertTrue(any(line.startswith("- AVISO: calidad: ruff: no se pudo ejecutar") for line in out), out)

    def test_un_modulo_cerrado_no_se_mide(self):
        self.config(f"commands:\n  ruff: {self.tool('ruff', [A], 1)}\n")
        with mock.patch("subprocess.run") as run:
            rc, out = self.close(completed=True)
        run.assert_not_called()
        self.assertEqual(rc, 0)
        self.assertFalse([line for line in out if "calidad:" in line], out)

    def test_el_workflow_y_su_wrapper_lo_dicen(self):
        for rel in (".agent/workflows/cierre-modulo.md", ".claude/commands/cierre-modulo.md"):
            text = (ROOT / rel).read_text(encoding="utf-8")
            self.assertIn("calidad: no configurada", text, rel)
            self.assertIn("_project/quality.yaml", text, rel)


class PlantillaSeMide(QualityCase):
    """L7.4."""

    def cli(self, *args):
        script = ROOT / ".agent" / "scripts" / "validate_closure.py"
        return subprocess.run([sys.executable, "-B", str(script), *args], cwd=self.root,
                              capture_output=True, text=True, timeout=120)

    def test_calidad_mide_sin_cerrar_un_modulo(self):
        config = self.root / "otra.yaml"
        config.write_text(f"commands:\n  ruff: {self.tool('ruff', [B], 1)}\n", encoding="utf-8")
        proc = self.cli("--calidad", "otra.yaml")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertEqual(proc.stdout.splitlines(),
                         ["calidad: ruff (rc 1): 1 diagnóstico(s), sin línea base", f"calidad: ruff: {B}"])

    def test_sin_argumentos_el_uso_nuevo(self):
        proc = self.cli()
        self.assertEqual(proc.returncode, 2)
        self.assertEqual(proc.stdout.strip(), "uso: validate_closure.py <module_id> | --calidad <quality.yaml>")

    def test_la_plantilla_no_siembra_quality_yaml(self):
        self.assertFalse((ROOT / "_project" / "quality.yaml").exists())
        self.assertFalse(list((ROOT / "_project").glob("quality_baseline_*")))

    def test_la_plantilla_mide_agent_scripts_con_ruff_aislado(self):
        done = subprocess.CompletedProcess([], 1, "", "")
        with mock.patch("subprocess.run", return_value=done) as run:
            vc.quality_warnings(ROOT, ROOT / "docs" / "quality.yaml")
        self.assertEqual(run.call_count, 1)
        argv = run.call_args[0][0]
        self.assertEqual(argv[:2], ["ruff", "check"])
        self.assertIn("--isolated", argv)
        self.assertEqual(argv[-1], ".agent/scripts")
        self.assertEqual(run.call_args[1]["cwd"], ROOT)


if __name__ == "__main__":
    unittest.main()
