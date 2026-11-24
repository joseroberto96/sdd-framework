import json
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SHIM_PATH = ROOT / ".agent" / "scripts" / "code_graph.py"

sys.path.insert(0, str(ROOT / "tests"))
from _dynamic_import import load_module_from_path  # noqa: E402


class TestCodeGraphShim(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.code_graph = load_module_from_path("code_graph", SHIM_PATH)

    def test_degraded_when_binary_absent(self):
        with mock.patch.object(self.code_graph.shutil, "which", return_value=None):
            result = self.code_graph.consultar_blast_radius(".", "HEAD")
        self.assertEqual(result["status"], "degraded")
        self.assertTrue(any("no encontrado en PATH" in w for w in result["warnings"]))

    def test_contract_shape_always_complete(self):
        with mock.patch.object(self.code_graph.shutil, "which", return_value=None):
            result = self.code_graph.consultar_blast_radius(".", "HEAD")
        expected_keys = {
            "status", "afectados", "callers", "riesgo",
            "clasificacion_sugerida", "evidence", "tool_version",
            "warnings", "source",
        }
        self.assertEqual(set(result.keys()), expected_keys)

    def test_degraded_when_version_mismatch(self):
        with mock.patch.object(self.code_graph.shutil, "which", return_value="/usr/bin/codebase-memory-mcp"), \
             mock.patch.object(self.code_graph, "_version_instalada", return_value="v9.9.9"):
            result = self.code_graph.consultar_blast_radius(".", "HEAD")
        self.assertEqual(result["status"], "degraded")
        self.assertTrue(any("no coincide exactamente con el pin" in w for w in result["warnings"]))

    def _con_binario_ok(self, fake_stdout, returncode=0):
        """FW-157: `detect_changes` resuelve primero el NOMBRE del proyecto.

        La CLI indexa por nombre, no por ruta absoluta. El shim aplicaba ya esa
        resolución en `trace_path` pero no aquí, así que estos fixtures
        codificaban una sola invocación — y con ella el bug pasaba
        desapercibido. Mismo helper que `TestConsultarAfectados`.
        """
        listado = subprocess.CompletedProcess(
            args=[], returncode=0, stderr="",
            stdout=json.dumps({"projects": [{"name": "P", "root_path": ".", "nodes": 1, "edges": 1}]}),
        )
        fake_proc = subprocess.CompletedProcess(
            args=[], returncode=returncode, stdout=fake_stdout, stderr=""
        )
        return (
            mock.patch.object(self.code_graph.shutil, "which", return_value="/usr/bin/codebase-memory-mcp"),
            mock.patch.object(self.code_graph, "_version_instalada", return_value=self.code_graph.TOOL_VERSION_PIN),
            mock.patch.object(self.code_graph.subprocess, "run", side_effect=[listado, fake_proc]),
        )

    def test_degraded_cuando_el_proyecto_no_esta_indexado(self):
        """FW-157: sin nombre resuelto no se inventa una consulta con la ruta."""
        vacio = subprocess.CompletedProcess(
            args=[], returncode=0, stderr="", stdout=json.dumps({"projects": []}),
        )
        with mock.patch.object(self.code_graph.shutil, "which", return_value="/usr/bin/codebase-memory-mcp"), \
             mock.patch.object(self.code_graph, "_version_instalada", return_value=self.code_graph.TOOL_VERSION_PIN), \
             mock.patch.object(self.code_graph.subprocess, "run", return_value=vacio):
            result = self.code_graph.consultar_blast_radius(".", "HEAD")
        self.assertEqual(result["status"], "degraded")
        self.assertTrue(any("no está indexado" in w for w in result["warnings"]), result["warnings"])

    def test_detect_changes_envia_el_nombre_indexado_no_la_ruta(self):
        """FW-157: la aserción que faltaba.

        Los otros fixtures comprueban la SECUENCIA de llamadas, no el valor que
        se manda. Sustituyendo el nombre resuelto por la ruta —el bug original—
        seguían pasando todos. Este mira el payload real."""
        salida = json.dumps({
            "changed_files": ["src/foo.py"], "changed_count": 1,
            "impacted_symbols": [{"name": "f", "label": "Function", "file": "src/foo.py"}],
            "depth": 2,
        })
        listado = subprocess.CompletedProcess(
            args=[], returncode=0, stderr="",
            stdout=json.dumps({"projects": [{"name": "Users-x-Proyecto", "root_path": ".", "nodes": 1, "edges": 1}]}),
        )
        detect = subprocess.CompletedProcess(args=[], returncode=0, stdout=salida, stderr="")
        with mock.patch.object(self.code_graph.shutil, "which", return_value="/usr/bin/codebase-memory-mcp"), \
             mock.patch.object(self.code_graph, "_version_instalada", return_value=self.code_graph.TOOL_VERSION_PIN), \
             mock.patch.object(self.code_graph.subprocess, "run", side_effect=[listado, detect]) as run:
            self.code_graph.consultar_blast_radius(".", "HEAD")
        payload = json.loads(run.call_args_list[1].args[0][3])
        self.assertEqual(payload["project"], "Users-x-Proyecto")

    def test_ok_with_real_schema_when_invocation_succeeds(self):
        fake_output = json.dumps({
            "changed_files": ["src/foo.py"],
            "changed_count": 1,
            "impacted_symbols": [{"name": "process_order", "label": "Function", "file": "src/foo.py"}],
            "depth": 2,
        })
        which, version, run = self._con_binario_ok(fake_output)
        with which, version, run:
            result = self.code_graph.consultar_blast_radius(".", "HEAD")
        self.assertEqual(result["status"], "ok")
        self.assertIn("Function process_order (src/foo.py)", result["afectados"])
        self.assertTrue(any("src/foo.py" in e for e in result["evidence"]))
        self.assertIsNone(result["clasificacion_sugerida"])
        self.assertEqual(result["riesgo"], "unknown")
        self.assertEqual(result["callers"], [])
        self.assertTrue(any("callers no se calcula" in w for w in result["warnings"]))

    def test_degraded_when_invocation_returns_invalid_json(self):
        which, version, run = self._con_binario_ok("no es json")
        with which, version, run:
            result = self.code_graph.consultar_blast_radius(".", "HEAD")
        self.assertEqual(result["status"], "degraded")
        self.assertTrue(any("no devolvió JSON válido" in w for w in result["warnings"]))

    def test_degraded_when_invocation_returncode_nonzero(self):
        fake_proc = subprocess.CompletedProcess(args=[], returncode=1, stdout="", stderr="no store")
        with mock.patch.object(self.code_graph.shutil, "which", return_value="/usr/bin/codebase-memory-mcp"), \
             mock.patch.object(self.code_graph, "_version_instalada", return_value=self.code_graph.TOOL_VERSION_PIN), \
             mock.patch.object(self.code_graph.subprocess, "run", return_value=fake_proc):
            result = self.code_graph.consultar_blast_radius(".", "HEAD")
        self.assertEqual(result["status"], "degraded")

    def test_degraded_when_no_diff_detected(self):
        # detect_changes invocado ANTES de editar (caso normal en /cambio-seguro
        # paso 0.6): árbol de trabajo limpio, sin diff que mapear todavía. Esto
        # NO debe ser "ok" con listas vacías -- eso se leería falsamente como
        # "confirmado sin impacto".
        fake_output = json.dumps({
            "changed_files": [],
            "changed_count": 0,
            "impacted_symbols": [],
            "depth": 2,
        })
        which, version, run = self._con_binario_ok(fake_output)
        with which, version, run:
            result = self.code_graph.consultar_blast_radius(".", "HEAD")
        self.assertEqual(result["status"], "degraded")
        self.assertTrue(any("No se detectó diff" in w for w in result["warnings"]))

    def test_pin_rejects_superstring_version(self):
        # Regresión: comparar con "in" aceptaría "0.8.10" como si coincidiera
        # con el pin "0.8.1" (substring). Debe exigir versión exacta.
        with mock.patch.object(self.code_graph.shutil, "which", return_value="/usr/bin/codebase-memory-mcp"), \
             mock.patch.object(self.code_graph, "_version_instalada", return_value="codebase-memory-mcp 0.8.10"):
            result = self.code_graph.consultar_blast_radius(".", "HEAD")
        self.assertEqual(result["status"], "degraded")
        self.assertTrue(any("no coincide exactamente" in w for w in result["warnings"]))

    def test_pin_accepts_v_prefixed_version(self):
        # Regresión (code-review): \b(\d+\.\d+\.\d+)\b no matchea "v0.8.1"
        # porque \b no es frontera entre 'v' y '0' (ambos son \w). Convención
        # común en CLIs ("vX.Y.Z") que no debía degradar con el pin correcto.
        with mock.patch.object(self.code_graph.shutil, "which", return_value="/usr/bin/codebase-memory-mcp"), \
             mock.patch.object(self.code_graph, "_version_instalada", return_value="v0.8.1"):
            result = self.code_graph.consultar_blast_radius(".", "HEAD")
        # Sin binario real, degrada en el siguiente paso (invocación), pero NO
        # por versión-no-coincide -- eso es lo que este test verifica.
        self.assertFalse(any("no coincide exactamente" in w for w in result["warnings"]), result["warnings"])

    def test_shim_never_writes_to_disk(self):
        text = SHIM_PATH.read_text(encoding="utf-8") if SHIM_PATH.exists() else ""
        self.assertNotIn("write_text(", text)
        self.assertNotIn("os.remove", text)
        self.assertNotIn("shutil.rmtree", text)

    def test_cli_outputs_valid_json_with_status(self):
        proc = subprocess.run(
            [sys.executable, str(SHIM_PATH), "--root", str(ROOT)],
            capture_output=True, text=True, check=True,
        )
        payload = json.loads(proc.stdout)
        self.assertIn(payload["status"], ("ok", "degraded"))


class TestConsultarAfectados(unittest.TestCase):
    """Fase 2 (FW-B): blast radius PRE-edición vía trace_path inbound.

    Esquema verificado en el fuente de codebase-memory-mcp 0.8.1
    (src/mcp/mcp.c: handle_trace_call_path, dispatch "trace_path"):
      entrada {"project","function_name","direction","depth"}
      salida  {"function","direction","callers":[{"name","qualified_name","hop"}]}
    """

    @classmethod
    def setUpClass(cls):
        cls.code_graph = load_module_from_path("code_graph_fase2", SHIM_PATH)

    def _con_binario_ok(self, fake_stdout, returncode=0):
        # Dos invocaciones: list_projects (resolución de nombre) + trace_path.
        listado = subprocess.CompletedProcess(
            args=[], returncode=0, stderr="",
            stdout=json.dumps({"projects": [{"name": "P", "root_path": ".", "nodes": 1, "edges": 1}]}),
        )
        fake_proc = subprocess.CompletedProcess(
            args=[], returncode=returncode, stdout=fake_stdout, stderr=""
        )
        return (
            mock.patch.object(self.code_graph.shutil, "which", return_value="/usr/bin/codebase-memory-mcp"),
            mock.patch.object(self.code_graph, "_version_instalada", return_value=self.code_graph.TOOL_VERSION_PIN),
            mock.patch.object(self.code_graph.subprocess, "run", side_effect=[listado, fake_proc]),
        )

    def test_degraded_sin_binario(self):
        with mock.patch.object(self.code_graph.shutil, "which", return_value=None):
            result = self.code_graph.consultar_afectados(".", "process_order")
        self.assertEqual(result["status"], "degraded")

    def test_ok_con_callers_reales(self):
        salida = json.dumps({
            "function": "process_order", "direction": "inbound",
            "callers": [
                {"name": "checkout", "qualified_name": "api.checkout", "hop": 1},
                {"name": "retry_job", "qualified_name": "jobs.retry_job", "hop": 2},
            ],
        })
        p1, p2, p3 = self._con_binario_ok(salida)
        with p1, p2, p3:
            result = self.code_graph.consultar_afectados(".", "process_order", depth=2)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(len(result["callers"]), 2)
        self.assertTrue(any("checkout" in c for c in result["callers"]))
        # La clasificación sigue siendo del agente (evidencia, no autoridad)
        self.assertIsNone(result["clasificacion_sugerida"])
        self.assertEqual(result["riesgo"], "unknown")

    def test_funcion_no_encontrada_degrada_con_hint(self):
        salida = json.dumps({
            "error": "function not found", "function_name": "no_existe",
            "hint": "Use search_graph(...)",
        })
        p1, p2, p3 = self._con_binario_ok(salida)
        with p1, p2, p3:
            result = self.code_graph.consultar_afectados(".", "no_existe")
        self.assertEqual(result["status"], "degraded")
        self.assertTrue(any("no encontr" in w.lower() or "not found" in w.lower() for w in result["warnings"]))

    def test_cero_callers_es_ok_con_advertencia_de_frescura(self):
        # A diferencia del diff vacío en detect_changes, aquí 0 callers con la
        # función encontrada SÍ es evidencia — pero condicionada al índice.
        salida = json.dumps({"function": "helper", "direction": "inbound", "callers": []})
        p1, p2, p3 = self._con_binario_ok(salida)
        with p1, p2, p3:
            result = self.code_graph.consultar_afectados(".", "helper")
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["callers"], [])
        self.assertTrue(any("índice" in w or "indice" in w for w in result["warnings"]))

    def test_json_invalido_degrada(self):
        p1, p2, p3 = self._con_binario_ok("esto no es json")
        with p1, p2, p3:
            result = self.code_graph.consultar_afectados(".", "x")
        self.assertEqual(result["status"], "degraded")

    def test_contrato_misma_forma_que_blast_radius(self):
        with mock.patch.object(self.code_graph.shutil, "which", return_value=None):
            radius = self.code_graph.consultar_blast_radius(".", "HEAD")
            afectados = self.code_graph.consultar_afectados(".", "x")
        self.assertEqual(set(radius.keys()), set(afectados.keys()))

    def test_cli_acepta_symbol(self):
        proc = subprocess.run(
            [sys.executable, str(SHIM_PATH), "--root", str(ROOT), "--symbol", "main"],
            capture_output=True, text=True, check=True,
        )
        payload = json.loads(proc.stdout)
        self.assertIn(payload["status"], ("ok", "degraded"))


class TestSalidaConLogsYResolucionDeProyecto(unittest.TestCase):
    """Dogfood real (2026-07-10, Example Desktop con el binario 0.8.1 instalado):

    Dos bugs del shim que solo el uso real reveló:
    - la CLI emite líneas `level=info ...` en stdout ANTES del JSON, así que
      `json.loads(stdout)` completo revienta;
    - `project` NO es una ruta: es el nombre normalizado que la herramienta
      asigna al indexar. Se resuelve vía `cli list_projects` (root_path→name).
    """

    @classmethod
    def setUpClass(cls):
        cls.cg = load_module_from_path("code_graph_dogfood", SHIM_PATH)

    def test_parsea_json_aunque_stdout_traiga_logs(self):
        salida = (
            "level=info msg=mem.init budget_mb=24576\n"
            'level=info msg=pipeline.route path=full\n'
            '{"function":"f","direction":"inbound","callers":[]}\n'
        )
        self.assertEqual(
            self.cg._ultimo_json(salida),
            {"function": "f", "direction": "inbound", "callers": []},
        )

    def test_ultimo_json_none_si_no_hay_json(self):
        self.assertIsNone(self.cg._ultimo_json("level=info solo logs\nnada\n"))

    def test_ultimo_json_multilinea_con_objetos_anidados(self):
        # BUG-05: salida multilínea formateada (pretty-print) con objetos anidados
        salida = (
            "level=info msg=mem.init budget_mb=24576\n"
            "{\n"
            '  "project": "foo",\n'
            '  "meta": {\n'
            '    "blast_radius": 3,\n'
            '    "details": {\n'
            '      "depth": 2\n'
            "    }\n"
            "  },\n"
            '  "symbols": ["a", "b"]\n'
            "}\n"
        )
        self.assertEqual(
            self.cg._ultimo_json(salida),
            {
                "project": "foo",
                "meta": {"blast_radius": 3, "details": {"depth": 2}},
                "symbols": ["a", "b"],
            },
        )

    def test_ultimo_json_monolinea_con_objetos_anidados(self):
        # BUG-05: objeto anidado en una sola línea no debe retornar el sub-objeto
        salida = 'level=info x\n{"project": "foo", "meta": {"blast_radius": 3}}'
        self.assertEqual(
            self.cg._ultimo_json(salida),
            {"project": "foo", "meta": {"blast_radius": 3}},
        )

    def test_ultimo_json_multiples_objetos_toplevel_retorna_el_ultimo(self):
        # BUG-05: logs estructurados emiten varios JSONs; debe devolver el último top-level
        salida = (
            '{"log": "init", "level": "info"}\n'
            '{"log": "progress", "level": "info"}\n'
            '{"project": "foo", "status": "done"}\n'
        )
        self.assertEqual(
            self.cg._ultimo_json(salida),
            {"project": "foo", "status": "done"},
        )

    def test_resuelve_nombre_de_proyecto_desde_root_path(self):
        listado = json.dumps({"projects": [
            {"name": "Users-x-Proyecto", "root_path": "/Users/x/Proyecto", "nodes": 1, "edges": 1},
        ]})
        proc = subprocess.CompletedProcess(args=[], returncode=0, stdout="level=info x\n" + listado, stderr="")
        with mock.patch.object(self.cg.subprocess, "run", return_value=proc):
            self.assertEqual(
                self.cg._resolver_nombre_proyecto("/usr/bin/cbm", "/Users/x/Proyecto"),
                "Users-x-Proyecto",
            )

    def test_proyecto_no_indexado_degrada_con_hint_de_index(self):
        listado = json.dumps({"projects": []})
        proc = subprocess.CompletedProcess(args=[], returncode=0, stdout=listado, stderr="")
        with mock.patch.object(self.cg.shutil, "which", return_value="/usr/bin/codebase-memory-mcp"), \
             mock.patch.object(self.cg, "_version_instalada", return_value=self.cg.TOOL_VERSION_PIN), \
             mock.patch.object(self.cg.subprocess, "run", return_value=proc):
            res = self.cg.consultar_afectados("/Users/x/Proyecto", "f")
        self.assertEqual(res["status"], "degraded")
        self.assertTrue(any("index_repository" in w for w in res["warnings"]), res["warnings"])

    def test_symbol_no_encontrado_conserva_el_hint_pese_a_rc_1(self):
        # Dogfood real: ante símbolo inexistente la herramienta sale con rc=1
        # PERO devuelve JSON con `error` + `hint`. Descartarlo por el rc pierde
        # la guía y reporta "no devolvió JSON válido", que es falso.
        listado = json.dumps({"projects": [{"name": "P", "root_path": "/r", "nodes": 1, "edges": 1}]})
        error_json = json.dumps({
            "error": "function not found", "function_name": "nope",
            "hint": "Use search_graph(...) to find the exact name.",
        })
        procs = [
            subprocess.CompletedProcess(args=[], returncode=0, stdout=listado, stderr=""),
            # La herramienta emite el JSON de error por STDERR (verificado en vivo)
            subprocess.CompletedProcess(args=[], returncode=1, stdout="level=info x\n", stderr=error_json),
        ]
        with mock.patch.object(self.cg.shutil, "which", return_value="/usr/bin/codebase-memory-mcp"), \
             mock.patch.object(self.cg, "_version_instalada", return_value=self.cg.TOOL_VERSION_PIN), \
             mock.patch.object(self.cg.subprocess, "run", side_effect=procs):
            res = self.cg.consultar_afectados("/r", "nope")
        self.assertEqual(res["status"], "degraded")
        aviso = " ".join(res["warnings"])
        self.assertIn("function not found", aviso)
        self.assertIn("search_graph", aviso, "el hint de la herramienta debe llegar al agente")
        self.assertNotIn("no devolvió JSON válido", aviso)

    def test_callers_reales_con_logs_y_nombre_resuelto(self):
        listado = json.dumps({"projects": [
            {"name": "P", "root_path": "/r", "nodes": 1, "edges": 1}]})
        trace = "level=info msg=mem.init\n" + json.dumps({
            "function": "check_accessibility", "direction": "inbound",
            "callers": [{"name": "run_smoke", "qualified_name": "P.mod.run_smoke", "hop": 1}],
        })
        procs = [
            subprocess.CompletedProcess(args=[], returncode=0, stdout=listado, stderr=""),
            subprocess.CompletedProcess(args=[], returncode=0, stdout=trace, stderr=""),
        ]
        with mock.patch.object(self.cg.shutil, "which", return_value="/usr/bin/codebase-memory-mcp"), \
             mock.patch.object(self.cg, "_version_instalada", return_value=self.cg.TOOL_VERSION_PIN), \
             mock.patch.object(self.cg.subprocess, "run", side_effect=procs):
            res = self.cg.consultar_afectados("/r", "check_accessibility")
        self.assertEqual(res["status"], "ok", res["warnings"])
        self.assertEqual(len(res["callers"]), 1)
        self.assertIn("run_smoke", res["callers"][0])


class TestCambioSeguroWiringFase2(unittest.TestCase):
    def test_cambio_seguro_menciona_symbol(self):
        text = (ROOT / ".agent" / "workflows" / "cambio-seguro.md").read_text(encoding="utf-8")
        self.assertIn("--symbol", text)


if __name__ == "__main__":
    unittest.main()
