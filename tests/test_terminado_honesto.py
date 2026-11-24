"""5.2.2 L6 — terminado honesto (FW-275, PA FW-133/136/137).

El agente firma lo que verificó él (código + integración); el uso real lo
confirma el builder con una acción propia (`confirm_builder_use`), y el
recuento visible distingue «completado» de «usado por ti».
"""
from __future__ import annotations

import copy
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / ".agent" / "scripts"))
sys.path.insert(0, str(ROOT / "tests"))

import projections  # noqa: E402
import replay_state_log  # noqa: E402
import state_queries  # noqa: E402
import validate_closure as vc  # noqa: E402
from test_maintenance_lifecycle import BASE_STATE, entorno  # noqa: E402

EVIDENCIA = "Builder: «usé el comparador toda la semana con mi cartera real»"


def _estado(**extra) -> dict:
    state = copy.deepcopy(BASE_STATE)
    state["modules"] = {"completed": ["m1", "m2"], "in_progress": [], "pending": ["m3"]}
    state["module_statuses"] = {"m1": "completed", "m2": "completed", "m3": "not_started"}
    state["work_tiers"] = {
        mod: {"tier": "tier_1", "rationale": "r", "classified_at": "t", "classified_by": "a"}
        for mod in ("m1", "m2", "m3")
    }
    state.update(extra)
    return state


class SelloTests(unittest.TestCase):
    ACS = "# ACs\n\n## m1\n\nEstado: {estado}\n\n- [x] AC1\n"

    def test_sello_nuevo_pasa(self):
        self.assertEqual(vc.client_seal_problems(self.ACS.format(estado=vc.AGENT_SEAL), "m1"), [])

    def test_sello_legado_sigue_valiendo(self):
        texto = self.ACS.format(estado="✅ Verificado con cliente")
        self.assertEqual(vc.client_seal_problems(texto, "m1"), [])

    def test_sin_sello_bloquea_con_el_texto_nuevo(self):
        problemas = vc.client_seal_problems(self.ACS.format(estado="🔄 En progreso"), "m1")
        self.assertTrue(any(vc.AGENT_SEAL in p for p in problemas))

    def test_la_leyenda_nueva_no_satisface_el_gate(self):
        leyenda = (ROOT / "_project" / "08_module_acceptance.md").read_text(encoding="utf-8")
        self.assertIn("👤 Usado por el builder", leyenda)
        texto = leyenda + "\n## m1\n\n- [x] AC1\n"
        self.assertTrue(vc.client_seal_problems(texto, "m1"))

    def test_ningun_workflow_escribe_el_sello_viejo(self):
        cierre = (ROOT / ".agent" / "workflows" / "cierre-modulo.md").read_text(encoding="utf-8")
        self.assertIn(f"Estado: {vc.AGENT_SEAL}", cierre)
        self.assertNotIn("Estado: ✅ Verificado con cliente", cierre)


class ConfirmBuilderUseTests(unittest.TestCase):
    def _mutar(self, mgr, **payload):
        base = {"module": "m1", "evidence": EVIDENCIA, "builder_confirmed": True}
        base.update(payload)
        return mgr.mutate("confirm_builder_use", base)

    def test_registra_y_es_idempotente_aditivo(self):
        with entorno(_estado()) as (tmp, mgr):
            self._mutar(mgr)
            self._mutar(mgr, evidence="Builder: «hoy lo volví a usar para rebalancear, sin fallos»")
            use = mgr.state_data["work_tiers"]["m1"]["builder_use"]
            self.assertEqual(use["confirmed_by"], "builder")
            self.assertEqual(len(use["evidence"]), 2)
            self.assertEqual(use["confirmed_at"], use["evidence"][0]["confirmed_at"])

    def test_sin_builder_confirmed_rechaza(self):
        with entorno(_estado()) as (tmp, mgr):
            with self.assertRaisesRegex(ValueError, "builder_confirmed"):
                self._mutar(mgr, builder_confirmed=False)

    def test_evidencia_corta_rechaza(self):
        with entorno(_estado()) as (tmp, mgr):
            with self.assertRaisesRegex(ValueError, "evidence"):
                self._mutar(mgr, evidence="lo usó")

    def test_modulo_pendiente_rechaza(self):
        with entorno(_estado()) as (tmp, mgr):
            with self.assertRaisesRegex(ValueError, "not_started"):
                self._mutar(mgr, module="m3")

    def test_modulo_sin_work_tier_rechaza(self):
        state = _estado()
        del state["work_tiers"]["m2"]
        with entorno(state) as (tmp, mgr):
            with self.assertRaisesRegex(ValueError, "work_tiers"):
                self._mutar(mgr, module="m2")

    def test_replay_la_reproduce(self):
        with entorno(_estado()) as (tmp, mgr):
            self._mutar(mgr)
            log = tmp / "_project" / "state_log.jsonl"
            lines = [l for l in log.read_text(encoding="utf-8").splitlines()
                     if "confirm_builder_use" in l]
            self.assertEqual(len(lines), 1)
            reconstruido, avisos = replay_state_log.apply_events(_estado(), lines, tmp)
            self.assertEqual(avisos, [])
            self.assertEqual(
                reconstruido["work_tiers"]["m1"]["builder_use"]["evidence"][0]["text"], EVIDENCIA
            )


class RecuentoTests(unittest.TestCase):
    def _con_uso(self) -> dict:
        state = _estado()
        state["work_tiers"]["m1"]["builder_use"] = {
            "confirmed_at": "t", "confirmed_by": "builder", "evidence": [{"text": EVIDENCIA, "confirmed_at": "t"}],
        }
        return state

    def test_core_md_muestra_el_recuento(self):
        core = projections.render_core_markdown(self._con_uso(), now_iso="t")
        self.assertIn("**Módulos**: 2 completados · 1 usados por ti · 1 pendientes", core)

    def test_brief_trae_el_recuento_y_el_doctor_no_ve_desfase(self):
        state = self._con_uso()
        brief = projections.expected_session_brief(state, generated_at="t")
        self.assertEqual(brief["module_counts"], {"completed": 2, "builder_used": 1, "pending": 1})
        self.assertEqual(projections.projection_semantic_diff(state, brief), {})

    def test_brief_anterior_sin_recuento_no_es_desfase(self):
        state = self._con_uso()
        brief = projections.expected_session_brief(state, generated_at="t")
        del brief["module_counts"]
        self.assertEqual(projections.projection_semantic_diff(state, brief), {})

    def test_brief_con_recuento_viejo_si_es_desfase(self):
        state = self._con_uso()
        brief = projections.expected_session_brief(state, generated_at="t")
        brief["module_counts"]["builder_used"] = 0
        self.assertIn("module_counts", projections.projection_semantic_diff(state, brief))

    def test_startup_sin_trabajo_pendiente(self):
        state = self._con_uso()
        state["modules"]["pending"] = []
        summary = state_queries._summary(state, None, "none", "startup", [])
        self.assertEqual(
            (summary["completed_count"], summary["builder_used_count"], summary["pending_count"]), (2, 1, 0)
        )
        self.assertIs(summary["no_pending_work"], True)

    def test_startup_con_trabajo_pendiente(self):
        summary = state_queries._summary(self._con_uso(), None, "none", "startup", [])
        self.assertIs(summary["no_pending_work"], False)


if __name__ == "__main__":
    unittest.main()
