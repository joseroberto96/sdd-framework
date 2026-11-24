#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Cosecha Example Service (2026-08-26) — FW-144 a FW-149.

Un proyecto entero implementado con un modelo rápido cerró cinco fases con el
estado canónico verde (`replay --audit: OK`, doctor OK, evidence gate OK) sobre
un producto que no cumple su PRD. La clase de fallo no es del modelo: la cadena
de gates verificaba *narrativa de haber ejecutado*, no ejecución. Cada test aquí
fija una de las seis costuras que lo permitieron.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / ".agent" / "scripts"))

import validate_acceptance as va  # noqa: E402
import validate_closure as vc  # noqa: E402
import validate_no_stubs as vns  # noqa: E402
import workflow_receipts as wr  # noqa: E402
import replay_state_log as rsl  # noqa: E402


class ExecutionReceiptTests(unittest.TestCase):
    """FW-144 — el EJECUTABLE GATE deja rastro o no cuenta."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)

    def _receipt(self, **kwargs):
        state: dict = {}
        base = dict(
            kind="ok_automatizada",
            command="curl -s -X POST localhost:8000/api/import -d '{...}'",
            exit_code=0,
            evidence="HTTP 200 y body conforme al response schema del contrato",
            now_iso="2026-08-26T00:00:00+00:00",
        )
        base.update(kwargs)
        wr.record_execution(state, self.root, "m1", **base)
        return state

    def test_ausencia_de_receipt_bloquea_el_cierre(self):
        problemas = wr.execution_problems({}, "m1")
        self.assertTrue(problemas)
        self.assertIn("FW-144", problemas[0])

    def test_receipt_valido_no_bloquea(self):
        self.assertEqual(wr.execution_problems(self._receipt(), "m1"), [])

    def test_smoke_que_falla_no_cierra(self):
        problemas = wr.execution_problems(self._receipt(exit_code=1), "m1")
        self.assertTrue(any("exit_code 1" in p for p in problemas))

    def test_verificacion_sin_comando_es_narrativa(self):
        with self.assertRaises(ValueError) as ctx:
            self._receipt(command="")
        self.assertIn("command", str(ctx.exception))

    def test_evidencia_perezosa_rechazada(self):
        with self.assertRaises(ValueError):
            self._receipt(evidence="ok")

    def test_kind_invalido_rechazado(self):
        with self.assertRaises(ValueError):
            self._receipt(kind="ok_supongo")

    def test_exit_code_en_string_no_burla_el_bloqueo(self):
        """El agujero que abrió la primera versión: `execution_problems` mira
        `isinstance(code, int)`, así que sellar "1" como texto habría cerrado un
        módulo con el smoke en rojo. Se normaliza al registrar."""
        state = self._receipt(exit_code="1")
        code = state["workflow_receipts"]["executions"]["m1"]["exit_code"]
        self.assertEqual(code, 1)
        self.assertTrue(any("exit_code 1" in p for p in wr.execution_problems(state, "m1")))

    def test_exit_code_no_numerico_rechazado(self):
        for valor in ("ok", "0.0", True, [0]):
            with self.assertRaises(ValueError, msg=f"aceptó {valor!r}"):
                self._receipt(exit_code=valor)

    def test_verificacion_automatizada_exige_exit_code(self):
        with self.assertRaises(ValueError):
            self._receipt(exit_code=None)

    def test_fallback_manual_no_inventa_exit_code(self):
        """Un smoke confirmado por el builder no tiene código de salida; exigirlo
        enseñaría a rellenar un 0 de mentira para pasar el gate."""
        state: dict = {}
        wr.record_execution(
            state, self.root, "m1", kind="ok_manual",
            command="abrir /candidato, importar CV de ejemplo, aprobar 2 evidencias",
            exit_code=None,
            evidence="el builder confirma que las 2 evidencias quedan aprobadas y visibles",
            now_iso="2026-08-26T00:00:00+00:00",
        )
        self.assertEqual(wr.execution_problems(state, "m1"), [])

    def test_module_specs_corrupto_no_revienta_el_gate(self):
        state = self._receipt(
            kind="no_aplica", command="",
            evidence="módulo solo de documentación, sin superficie ejecutable",
        )
        state["module_specs"] = None
        self.assertEqual(wr.execution_problems(state, "m1"), [])

    def test_receipt_stale_si_el_diff_cambio_despues(self):
        state = self._receipt()
        problemas = wr.execution_problems(state, "m1", current_diff_hash="otro-hash")
        self.assertTrue(any("stale" in p for p in problemas))

    def test_no_aplica_solo_para_doc_config_puro(self):
        state = self._receipt(
            kind="no_aplica", command="",
            evidence="módulo solo de documentación, sin superficie ejecutable",
        )
        self.assertEqual(wr.execution_problems(state, "m1"), [])
        state["module_specs"] = {"m1": {"data_model": "done", "api_contract": "n/a"}}
        problemas = wr.execution_problems(state, "m1")
        self.assertTrue(any("no_aplica" in p for p in problemas))

    def test_la_bitacora_respalda_el_receipt(self):
        """Un receipt escrito a mano en el estado no es prueba de nada."""
        self.assertIn("executions", rsl._BACKING_ACTIONS)
        self.assertIn("record_execution_receipt", rsl._BACKING_ACTIONS["executions"])
        state = self._receipt()
        log = self.root / "state_log.jsonl"
        log.write_text("", encoding="utf-8")
        problemas = rsl.unbacked_module_receipts(state, "m1", log)
        self.assertTrue(any("executions" in p for p in problemas))


class StubGateTests(unittest.TestCase):
    """FW-145 — la ruta citada existe; el gate mira si el código es decorado."""

    def _scan(self, contenido: str) -> list[str]:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        src = Path(tmp.name) / "src"
        src.mkdir()
        f = src / "service.py"
        f.write_text(contenido, encoding="utf-8")
        return vns.scan_file(f, Path(tmp.name))

    def test_dato_cableado_detectado(self):
        hallazgos = self._scan("def c():\n    return {'email': 'candidato@example.com'}\n")
        self.assertTrue(any("dato fabricado" in h for h in hallazgos))

    def test_fail_open_silencioso_detectado(self):
        hallazgos = self._scan(
            "def llm(p):\n    try:\n        return client.generate(p)\n"
            "    except Exception:\n        return {}\n"
        )
        self.assertTrue(any("fail-open" in h for h in hallazgos))

    def test_manejador_que_propaga_no_es_fail_open(self):
        self.assertEqual(
            self._scan(
                "def llm(p):\n    try:\n        return client.generate(p)\n"
                "    except Exception as exc:\n        raise LLMDown(str(exc)) from exc\n"
            ),
            [],
        )

    def test_manejador_que_registra_y_relanza_no_es_fail_open(self):
        self.assertEqual(
            self._scan(
                "def llm(p):\n    try:\n        return client.generate(p)\n"
                "    except Exception:\n        logger.exception('llm caido')\n        raise\n"
            ),
            [],
        )

    def test_opt_out_explicito_respetado(self):
        self.assertEqual(
            self._scan(
                "def demo():  # stub-ok: fixture de la demo pública, fuera del flujo real\n"
                "    return {'email': 'demo@example.com'}\n"
            ),
            [],
        )

    def test_tests_y_fixtures_quedan_fuera_del_escaneo(self):
        """Un dato de mentira en un test es el trabajo, no el defecto."""
        self.assertFalse(vns.is_source_file(Path("tests/test_api.py")))
        self.assertFalse(vns.is_source_file(Path("src/fixtures/data.py")))
        self.assertFalse(vns.is_source_file(Path("docs/guia.md")))
        self.assertTrue(vns.is_source_file(Path("src/services/tailoring.py")))

    def test_documentar_el_antipatron_no_dispara_el_gate(self):
        """FW-155: el detector leía docstrings como código.

        Un módulo que explica el anti-patrón para justificar por qué existe
        —justo lo que hace `src/serialization.py` en Example Service, escrito para
        ELIMINAR esos `except Exception: pass`— se auto-denunciaba. Documentar
        el problema no puede costar un bloqueo.
        """
        self.assertEqual(
            self._scan(
                'def leer(raw):\n'
                '    """Sustituye el patrón:\n'
                '\n'
                '        try:\n'
                '            datos = json.loads(raw)\n'
                '        except Exception:\n'
                '            pass\n'
                '\n'
                '    que perdía el error en silencio.\n'
                '    """\n'
                '    return json.loads(raw)\n'
            ),
            [],
        )

    def test_docstring_de_una_linea_no_oculta_el_codigo_siguiente(self):
        """El salto de docstrings no puede convertirse en un agujero: lo que
        viene DESPUÉS del docstring se sigue juzgando."""
        hallazgos = self._scan(
            'def leer(raw):\n'
            '    """Lee el campo."""\n'
            '    try:\n'
            '        return json.loads(raw)\n'
            '    except Exception:\n'
            '        return {}\n'
        )
        self.assertTrue(any("fail-open" in h for h in hallazgos))

    def test_sin_falsos_positivos_sobre_el_propio_framework(self):
        """Barrido real: el gate no puede gritar lobo sobre los scripts del repo."""
        objetivos = [p for p in (ROOT / ".agent" / "scripts").glob("*.py")]
        self.assertTrue(objetivos, "sin scripts que escanear")
        self.assertEqual(
            [p for p in objetivos if vns.is_source_file(p.relative_to(ROOT))], []
        )


class ClientSealTests(unittest.TestCase):
    """FW-147 — estado y aceptación cierran juntos o no cierran."""

    ACS = "# ACs\n\n## m1\n\nEstado: {estado}\n\n- [x] AC1 — evidencia: `src/x.py`\n\n## m2\n\n- [x] AC2\n"

    def test_sin_sello_bloquea(self):
        problemas = vc.client_seal_problems(self.ACS.format(estado="🟢 Completado"), "m1")
        self.assertTrue(any("FW-147" in p for p in problemas))

    def test_con_sello_pasa(self):
        self.assertEqual(
            vc.client_seal_problems(self.ACS.format(estado=vc.CLIENT_SEAL), "m1"), []
        )

    def test_el_sello_de_otro_modulo_no_vale(self):
        """El caso real: la leyenda de la cabecera contiene el mismo literal."""
        texto = (
            "# ACs\n\nEstado: ⬜ Pendiente | 🔄 En progreso | ✅ Verificado con cliente\n\n"
            "## m1\n\n- [x] AC1\n"
        )
        self.assertTrue(vc.client_seal_problems(texto, "m1"))

    def test_sin_seccion_bloquea(self):
        """Sigue siendo fail-closed aunque duplique la razón de cobertura."""
        self.assertTrue(vc.client_seal_problems("# ACs\n", "m1"))


class IntegrationRealSpecTests(unittest.TestCase):
    """FW-148 — una suite entera sobre mocks puede estar verde y mentir."""

    def _specs(self, **extra):
        entry = {
            "data_model": "done", "api_contract": "done",
            "classified_at": "t", "classified_by": "manual",
        }
        entry.update(extra)
        return {"module_specs": {"m1": entry}}

    def test_ausente_es_retrocompatible(self):
        self.assertEqual(vc.spec_problems(self._specs(), "m1"), [])

    def test_valor_invalido_bloquea(self):
        self.assertTrue(vc.spec_problems(self._specs(integration_real="quizá"), "m1"))

    def test_na_exige_justificacion(self):
        self.assertTrue(vc.spec_problems(self._specs(integration_real="n/a"), "m1"))
        self.assertEqual(
            vc.spec_problems(
                self._specs(integration_real="n/a", justification="lógica pura, sin costuras"),
                "m1",
            ),
            [],
        )

    def test_done_pasa(self):
        self.assertEqual(vc.spec_problems(self._specs(integration_real="done"), "m1"), [])


class LimitedExecutorDeadlockTests(unittest.TestCase):
    """FW-151 — el gate del ejecutor 'limited' era insatisfacible en tier_1.

    `executor_problems` exige `requires_second_review: true` en todo tier cuando
    el ejecutor es 'limited', pero `add_work_tier` solo lo escribía en tier_2:
    la única salida era editar el JSON a mano. Cosechado de Example Service, que lo
    llevaba parcheado en local sin subirlo (FW-143).
    """

    def test_limited_en_tier_1_puede_satisfacer_su_propio_gate(self):
        estado = {
            "execution_profile": {"capability_tier": "limited"},
            "work_tiers": {
                "m1": {
                    "tier": "tier_1", "rationale": "r",
                    "classified_at": "t", "classified_by": "manual",
                    "requires_second_review": True,
                }
            },
        }
        self.assertEqual(vc.executor_problems(estado, "m1"), [])

    def test_sin_la_marca_el_gate_sigue_bloqueando(self):
        estado = {
            "execution_profile": {"capability_tier": "limited"},
            "work_tiers": {
                "m1": {"tier": "tier_1", "rationale": "r",
                       "classified_at": "t", "classified_by": "manual"}
            },
        }
        self.assertTrue(vc.executor_problems(estado, "m1"))


class ModuleSectionTests(unittest.TestCase):
    """La sección del módulo tiene una sola definición para todos sus lectores."""

    def test_seccion_y_acs_coinciden_en_frontera(self):
        texto = "# ACs\n\n## m1\n\n- [x] A\n\n## m2\n\n- [x] B\n"
        seccion = va.module_section_lines(texto, "m1")
        self.assertIsNotNone(seccion)
        self.assertNotIn("## m2", "\n".join(seccion))
        self.assertEqual(len(va.module_ac_lines(texto, "m1")), 1)

    def test_modulo_inexistente_devuelve_none(self):
        self.assertIsNone(va.module_section_lines("# ACs\n", "m1"))


if __name__ == "__main__":
    unittest.main()
