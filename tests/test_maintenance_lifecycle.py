#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Modo mantenimiento (`lifecycle: production`, FW-225).

Un producto en producción paga hoy la ceremonia de construcción por cambios
pequeños (Example Tool `5a4c5af`: 40 líneas de JS → 19 mutaciones y ~275 líneas de
artefactos) y a la vez un cambio riesgoso hecho por fuera pasa sin gate (Example Tool
`19ba2de`: migración + routers, 2 mutaciones). Estos tests fijan el contrato
del motor: ruta ligera para tier_0/1, gates mecánicos que muerden, y escalada
obligatoria a módulo cuando el diff toca superficies frágiles.
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / ".agent" / "scripts"))
import maintenance  # noqa: E402
import state_core  # noqa: E402
from state_core import StateManager  # noqa: E402

BASE_STATE = {
    "project": "Mantenimiento",
    "framework_mode": "initialized",
    "framework_version": state_core.FRAMEWORK_VERSION,
    "current_phase": "phase_07",
    "current_step": "operación",
    "last_completed_step": "",
    "stack": {"backend": "Python", "frontend": "-", "database": "SQLite", "auth": "-"},
    "dependencies": [],
    "architecture_constraints": [],
    "modules": {"completed": ["m1"], "in_progress": [], "pending": []},
    "module_statuses": {"m1": "completed"},
    "work_tiers": {}, "module_specs": {}, "closure_rejections": [],
    "active_mcps": [], "contracts": {"public_interfaces": {}},
    "_refs": {}, "_bugs": [], "_clarifications": [], "_decisions": [],
}

RAZON = "El producto está desplegado en campo y solo recibe ajustes puntuales"
EVIDENCIA_OK = "tests/test_x.py → rc=0, cubre el banner de reimpresión"


@contextmanager
def entorno(state=None, *, git=False):
    attrs = (
        "WORKSPACE_ROOT", "STATE_FILE_PATH", "CORE_FILE_PATH",
        "BUGS_FILE_PATH", "CLARIFICATIONS_FILE_PATH", "DECISIONS_FILE_PATH",
        "SESSION_BRIEF_PATH",
    )
    original = {name: getattr(state_core, name) for name in attrs}
    try:
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            (tmp / "_project").mkdir()
            (tmp / "tests").mkdir()
            (tmp / "tests" / "test_x.py").write_text("def test_x():\n    pass\n", encoding="utf-8")
            for attr, rel in (
                ("STATE_FILE_PATH", "_project/03_project_state.json"),
                ("CORE_FILE_PATH", "_project/core.md"),
                ("BUGS_FILE_PATH", "_project/06_bug_fixes.md"),
                ("CLARIFICATIONS_FILE_PATH", "_project/07_pending_clarifications.md"),
                ("DECISIONS_FILE_PATH", "_project/03_arch_decisions.md"),
                ("SESSION_BRIEF_PATH", "_project/00_session_brief.json"),
            ):
                setattr(state_core, attr, tmp / rel)
            state_core.WORKSPACE_ROOT = tmp
            (tmp / "_project" / "03_project_state.json").write_text(
                json.dumps(state or BASE_STATE, ensure_ascii=False), encoding="utf-8")
            if git:
                env = {"GIT_CONFIG_GLOBAL": "/dev/null", "PATH": os.environ.get("PATH", "/usr/bin:/bin")}
                for cmd in (["init", "-q"], ["add", "-A"],
                            ["-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "base"]):
                    subprocess.run(["git", "-C", str(tmp), *cmd], check=True, env=env,
                                   capture_output=True)
            yield tmp, StateManager()
    finally:
        for name, value in original.items():
            setattr(state_core, name, value)


def _produccion(mgr):
    mgr.mutate("set_lifecycle", {"stage": "production", "reason": RAZON, "builder_confirmed": True})


def _abrir(mgr, **extra):
    payload = {"summary": "Banner de reimpresión en Vista por grupo",
               "affected_modules": ["m1"], "tier": "tier_1",
               "criteria": ["el banner aparece filtrado por grupo"]}
    payload.update(extra)
    return mgr.mutate("open_change", payload)


def _cerrar(mgr, evidencia=EVIDENCIA_OK, **extra):
    payload = {"id": "CHG-001",
               "evidence": {"el banner aparece filtrado por grupo": evidencia},
               "tests": "python3 -m pytest tests/test_x.py → rc=0",
               "changelog": "Banner de reimpresión visible en Vista por grupo"}
    payload.update(extra)
    return mgr.mutate("close_change", payload)


class LifecycleTests(unittest.TestCase):
    def test_acciones_registradas(self):
        for action in ("set_lifecycle", "open_change", "close_change", "escalate_change"):
            self.assertIn(action, state_core.BUILTIN_ACTIONS)

    def test_sin_campo_el_proyecto_esta_en_desarrollo(self):
        self.assertEqual("development", maintenance.lifecycle_stage(BASE_STATE))

    def test_pasar_a_produccion_exige_confirmacion_del_builder(self):
        with entorno() as (_, mgr):
            with self.assertRaises(ValueError) as ctx:
                mgr.mutate("set_lifecycle", {"stage": "production", "reason": RAZON})
            self.assertIn("builder", str(ctx.exception))

    def test_pasar_a_produccion_exige_razon(self):
        with entorno() as (_, mgr):
            with self.assertRaises(ValueError):
                mgr.mutate("set_lifecycle", {"stage": "production", "reason": "corto",
                                             "builder_confirmed": True})

    def test_no_pasa_a_produccion_con_modulos_en_curso(self):
        state = json.loads(json.dumps(BASE_STATE))
        state["modules"]["in_progress"] = ["m2"]
        state["module_statuses"]["m2"] = "in_progress"
        with entorno(state) as (_, mgr):
            with self.assertRaises(ValueError) as ctx:
                _produccion(mgr)
            self.assertIn("m2", str(ctx.exception))

    def test_no_pasa_a_produccion_con_bug_critico_abierto(self):
        with entorno() as (_, mgr):
            mgr.mutate("register_bug", {"module_id": "m1", "severity": "Crítico",
                                        "description": "se cae al imprimir"})
            with self.assertRaises(ValueError):
                _produccion(mgr)

    def test_produccion_queda_registrada_y_proyectada(self):
        with entorno() as (tmp, mgr):
            _produccion(mgr)
            state = json.loads((tmp / "_project" / "03_project_state.json").read_text())
            self.assertEqual("production", state["lifecycle"]["stage"])
            self.assertEqual("builder", state["lifecycle"]["set_by"])
            self.assertIn("production", (tmp / "_project" / "core.md").read_text())

    def test_stage_invalido_rechazado_por_validate_state(self):
        state = json.loads(json.dumps(BASE_STATE))
        state["lifecycle"] = {"stage": "mantenimiento"}
        with self.assertRaises(ValueError):
            StateManager.validate_state(StateManager.__new__(StateManager), state)


class OpenChangeTests(unittest.TestCase):
    def test_no_se_abre_cambio_en_desarrollo(self):
        with entorno() as (_, mgr):
            with self.assertRaises(ValueError) as ctx:
                _abrir(mgr)
            self.assertIn("production", str(ctx.exception))

    def test_tier_2_no_entra_por_la_ruta_ligera(self):
        with entorno() as (_, mgr):
            _produccion(mgr)
            with self.assertRaises(ValueError) as ctx:
                _abrir(mgr, tier="tier_2")
            self.assertIn("escala", str(ctx.exception))

    def test_criterios_entre_uno_y_tres(self):
        with entorno() as (_, mgr):
            _produccion(mgr)
            for criterios in ([], ["a", "b", "c", "d"]):
                with self.assertRaises(ValueError):
                    _abrir(mgr, criteria=criterios)

    def test_abre_con_id_y_tier_registrado(self):
        with entorno() as (tmp, mgr):
            _produccion(mgr)
            _abrir(mgr)
            state = json.loads((tmp / "_project" / "03_project_state.json").read_text())
            self.assertEqual("CHG-001", state["changes"][0]["id"])
            self.assertEqual("open", state["changes"][0]["status"])
            self.assertEqual("tier_1", state["work_tiers"]["task:CHG-001"]["tier"])

    def test_un_solo_cambio_abierto(self):
        with entorno() as (_, mgr):
            _produccion(mgr)
            _abrir(mgr)
            with self.assertRaises(ValueError) as ctx:
                _abrir(mgr)
            self.assertIn("CHG-001", str(ctx.exception))

    def test_modulo_afectado_debe_existir(self):
        with entorno() as (_, mgr):
            _produccion(mgr)
            with self.assertRaises(ValueError):
                _abrir(mgr, affected_modules=["fantasma"])


class CloseChangeTests(unittest.TestCase):
    def test_cierre_valido(self):
        with entorno() as (tmp, mgr):
            _produccion(mgr)
            _abrir(mgr)
            _cerrar(mgr)
            state = json.loads((tmp / "_project" / "03_project_state.json").read_text())
            self.assertEqual("closed", state["changes"][0]["status"])
            log = (tmp / "_project" / "24_change_log.md").read_text()
            self.assertIn("CHG-001", log)
            self.assertIn("Banner de reimpresión visible", log)

    def test_evidencia_perezosa_bloquea(self):
        with entorno() as (_, mgr):
            _produccion(mgr)
            _abrir(mgr)
            with self.assertRaises(ValueError):
                _cerrar(mgr, evidencia="funciona")

    def test_criterio_sin_evidencia_bloquea(self):
        with entorno() as (_, mgr):
            _produccion(mgr)
            _abrir(mgr)
            with self.assertRaises(ValueError) as ctx:
                _cerrar(mgr, evidence={})
            self.assertIn("banner", str(ctx.exception))

    def test_ruta_fabricada_bloquea(self):
        with entorno() as (_, mgr):
            _produccion(mgr)
            _abrir(mgr)
            with self.assertRaises(ValueError) as ctx:
                _cerrar(mgr, evidencia="tests/test_inventado.py → rc=0 cubre el banner")
            self.assertIn("test_inventado", str(ctx.exception))

    def test_cierre_exige_comando_de_tests_con_rc(self):
        with entorno() as (_, mgr):
            _produccion(mgr)
            _abrir(mgr)
            with self.assertRaises(ValueError):
                _cerrar(mgr, tests="probé y va bien")

    def test_bug_critico_en_modulo_afectado_bloquea(self):
        with entorno() as (_, mgr):
            _produccion(mgr)
            _abrir(mgr)
            mgr.mutate("register_bug", {"module_id": "m1", "severity": "Crítico",
                                        "description": "se cae al imprimir"})
            with self.assertRaises(ValueError) as ctx:
                _cerrar(mgr)
            self.assertIn("BUG-001", str(ctx.exception))

    def test_diff_que_toca_migraciones_obliga_a_escalar(self):
        """El caso 19ba2de: una migración no puede cerrar por la ruta ligera."""
        with entorno(git=True) as (tmp, mgr):
            _produccion(mgr)
            _abrir(mgr)
            (tmp / "src" / "core").mkdir(parents=True)
            (tmp / "src" / "core" / "migrations.py").write_text("VERSION = 2\n", encoding="utf-8")
            with self.assertRaises(ValueError) as ctx:
                _cerrar(mgr)
            self.assertIn("escalate_change", str(ctx.exception))
            self.assertIn("migrations.py", str(ctx.exception))

    def test_stub_en_el_diff_bloquea(self):
        with entorno(git=True) as (tmp, mgr):
            _produccion(mgr)
            _abrir(mgr)
            (tmp / "svc.py").write_text("def c():\n    return {'email': 'x@example.com'}\n",
                                        encoding="utf-8")
            with self.assertRaises(ValueError) as ctx:
                _cerrar(mgr)
            self.assertIn("STUB", str(ctx.exception))

    def test_cambio_inexistente_o_cerrado(self):
        with entorno() as (_, mgr):
            _produccion(mgr)
            with self.assertRaises(ValueError):
                _cerrar(mgr)


class EscalationTests(unittest.TestCase):
    def test_patrones_por_defecto(self):
        hits = maintenance.escalation_hits(
            ["static/app.js", "src/core/migrations.py", "src/auth/login.py", "docs/x.md"],
            maintenance.DEFAULT_ESCALATION_PATTERNS,
        )
        self.assertEqual(["src/core/migrations.py", "src/auth/login.py"], hits)

    def test_patrones_propios_del_proyecto_amplian(self):
        with entorno() as (tmp, mgr):
            mgr.mutate("set_lifecycle", {"stage": "production", "reason": RAZON,
                                         "builder_confirmed": True,
                                         "escalation_patterns": ["*/pagos/*"]})
            state = json.loads((tmp / "_project" / "03_project_state.json").read_text())
            patterns = maintenance.escalation_patterns(state)
            self.assertIn("*/pagos/*", patterns)
            self.assertIn("*migration*", patterns)

    def test_escalar_convierte_el_cambio_en_modulo_tier_2(self):
        with entorno() as (tmp, mgr):
            _produccion(mgr)
            _abrir(mgr)
            mgr.mutate("escalate_change", {"id": "CHG-001", "module_id": "tallos_estandar_bloque",
                                           "reason": "toca migración de esquema de bloques"})
            state = json.loads((tmp / "_project" / "03_project_state.json").read_text())
            self.assertEqual("escalated", state["changes"][0]["status"])
            self.assertIn("tallos_estandar_bloque", state["modules"]["in_progress"])
            self.assertEqual("tier_2", state["work_tiers"]["tallos_estandar_bloque"]["tier"])
            self.assertEqual("production", state["lifecycle"]["stage"])

    def test_modulo_escalado_queda_respaldado_ante_el_gate_de_transiciones(self):
        """El módulo nace en in_progress sin pasar por add_module: el pre-commit
        debe aceptarlo, y la bitácora debe registrar ese estado como respaldado
        por escalate_change (no por una edición a mano)."""
        import validate_transition

        with entorno() as (tmp, mgr):
            _produccion(mgr)
            _abrir(mgr)
            prev = json.loads((tmp / "_project" / "03_project_state.json").read_text())
            mgr.mutate("escalate_change", {"id": "CHG-001", "module_id": "nuevo",
                                           "reason": "toca migración de esquema de bloques"})
            new = json.loads((tmp / "_project" / "03_project_state.json").read_text())
            logged = validate_transition._last_logged_statuses(tmp / "_project" / "state_log.jsonl")

            self.assertEqual("in_progress", logged.get("nuevo"))
            self.assertEqual([], validate_transition.transition_problems(prev, new, logged))

    def test_volver_a_desarrollo_tambien_es_del_builder(self):
        with entorno() as (_, mgr):
            _produccion(mgr)
            with self.assertRaises(ValueError):
                mgr.mutate("set_lifecycle", {"stage": "development", "reason": RAZON})
            mgr.mutate("set_lifecycle", {"stage": "development", "reason": RAZON,
                                         "builder_confirmed": True})


if __name__ == "__main__":
    unittest.main()
