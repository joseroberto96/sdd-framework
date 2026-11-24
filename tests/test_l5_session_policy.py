#!/usr/bin/env python3
"""5.3.0 L5: política de sesión coherente, aviso de prompt caducado y FW-349.

- FW-318: la misma frase de política en el núcleo, `/gycd`, `/arrancar` y los
  dos adaptadores (cableado `politica_de_sesion`); ningún cierre con trabajo
  definido deja `/arrancar` como siguiente; `/arrancar` no se devuelve a sí mismo.
- Prompt caducado: `stale_prompt.py` avisa en una línea y nunca bloquea.
- FW-349: el bug bloqueante se corrige con `/ciclo` en un módulo de
  Estabilización; el STOP GATE del workflow lo permite igual que el motor.
"""
from __future__ import annotations

import importlib.util
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / ".agent" / "scripts"))

import turn_close as tc  # noqa: E402
from state_core import StateManager  # noqa: E402

POLICY = (
    "prevés ausentarte más de ~1 h, cierra con `/gycd`; al volver, sesión nueva "
    "pegando el `Siguiente:`; si no tienes prompt o quieres ver el estado, `/arrancar`"
)
POLICY_SURFACES = (
    ".agent/rules/runtime_core.md",
    ".agent/workflows/gycd.md",
    ".agent/workflows/arrancar.md",
    ".agent/adapters/claude-code.md",
    ".agent/adapters/codex.md",
)
# Cierres donde todavía no hay trabajo definido (punto 2 de L5).
ARRANCAR_ALLOWED = {"nuevo-proyecto.md", "migrar-proyecto.md", "actualizar-framework.md"}
STALE_SCRIPT = ROOT / ".agent" / "scripts" / "stale_prompt.py"


def _read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


def load_doctor():
    spec = importlib.util.spec_from_file_location(
        "framework_doctor_l5", ROOT / ".agent" / "scripts" / "framework_doctor.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def load_stale_prompt():
    spec = importlib.util.spec_from_file_location("stale_prompt_l5", STALE_SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class SessionPolicyWiringTests(unittest.TestCase):
    def test_same_phrase_on_every_surface(self) -> None:
        for rel in POLICY_SURFACES:
            self.assertIn(POLICY, _read(rel), rel)

    def test_doctor_wiring_passes_on_real_repo(self) -> None:
        errors: list[str] = []
        load_doctor().validate_politica_de_sesion_wiring(errors)
        self.assertEqual(errors, [])

    def test_doctor_wiring_fails_when_one_surface_diverges(self) -> None:
        doctor = load_doctor()
        original_root = doctor.ROOT
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            for rel in POLICY_SURFACES:
                dst = tmp_path / rel
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy(ROOT / rel, dst)
            target = tmp_path / ".agent" / "workflows" / "gycd.md"
            text = target.read_text(encoding="utf-8")
            changed = text.replace("sesión nueva pegando el", "abre una sesión nueva con /arrancar y pega el")
            self.assertNotEqual(changed, text, "el fixture no cambió la frase")
            target.write_text(changed, encoding="utf-8")
            try:
                doctor.ROOT = tmp_path
                errors: list[str] = []
                doctor.validate_politica_de_sesion_wiring(errors)
            finally:
                doctor.ROOT = original_root
        self.assertEqual(len(errors), 1, errors)
        self.assertIn("gycd.md", errors[0])
        self.assertIn("FW-318", errors[0])


class ClosingCommandTests(unittest.TestCase):
    def setUp(self) -> None:
        self.manifest = json.loads(_read(".agent/workflows/manifest.json"))

    def test_closings_with_defined_work_do_not_point_to_arrancar(self) -> None:
        found: list[str] = []
        for path in sorted((ROOT / ".agent" / "workflows").glob("*.md")):
            if path.name in ARRANCAR_ALLOWED or path.name == "_README.md":
                continue
            for finding in tc.scan_templates(path.read_text(encoding="utf-8"), manifest=self.manifest):
                if finding.text.split()[1:2] == ["/arrancar"]:
                    found.append(f"{path.name}:{finding.line}")
        self.assertEqual(found, [])

    def test_module_closing_no_longer_sends_to_arrancar_by_default(self) -> None:
        text = _read(".agent/workflows/cierre-modulo.md")
        self.assertIn("pegando el `Siguiente:` (sin prompt, `/arrancar`)", text)
        self.assertNotIn("para el siguiente módulo (`/arrancar`)", text)

    def test_gycd_never_persists_arrancar_with_defined_work(self) -> None:
        self.assertIn("Nunca `/arrancar` cuando hay trabajo definido", _read(".agent/workflows/gycd.md"))

    def test_arrancar_never_returns_itself(self) -> None:
        text = _read(".agent/workflows/arrancar.md")
        self.assertNotIn("Siguiente: /arrancar", text)
        self.assertIn("nunca se devuelve a sí mismo", text)
        self.assertIn("**tal cual** el siguiente paso vigente", text)

    def test_arrancar_keeps_what_it_did(self) -> None:
        text = _read(".agent/workflows/arrancar.md")
        for fragment in ("Producto en producción (FW-225)", "Modo template", "(FW-275)",
                         "Siguiente: /ciclo <instrucción concreta, suficiente y necesaria>"):
            self.assertIn(fragment, text)

    def test_nine_builder_commands_remain(self) -> None:
        names = sorted(c["name"] for c in self.manifest["commands"] if c.get("builder_facing"))
        self.assertEqual(names, sorted([
            "arrancar", "reconciliar", "gycd", "diseñar", "nuevo-bug", "nuevo-proyecto",
            "migrar-proyecto", "actualizar-framework", "ciclo"]))


class StalePromptTests(unittest.TestCase):
    POINTER_TS = "2026-10-10T10:00:00Z"
    ACTION = "Implementar el login en src/auth.py con sus tests"

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        (self.root / "_project").mkdir()
        self.stale = load_stale_prompt()

    def _project(self, *, later: int = 0, mode: str = "initialized", pointer: bool = True) -> None:
        state = {"framework_mode": mode,
                 "session": {"next_command": "/ciclo", "next_action": self.ACTION}}
        (self.root / "_project" / "03_project_state.json").write_text(
            json.dumps(state, ensure_ascii=False), encoding="utf-8")
        events = [{"ts": "2026-10-10T09:00:00Z", "action": "add_module"}]
        if pointer:
            events.append({"ts": self.POINTER_TS, "action": "set_session_pointer"})
        events += [{"ts": f"2026-10-11T0{i}:00:00Z", "action": "update_module_status"} for i in range(later)]
        (self.root / "_project" / "state_log.jsonl").write_text(
            "".join(json.dumps(e) + "\n" for e in events), encoding="utf-8")

    def test_different_prompt_with_later_mutation_warns_in_one_line(self) -> None:
        self._project(later=2)
        warning = self.stale.stale_prompt_warning("Añadir el panel de informes", self.root)
        self.assertIsNotNone(warning)
        self.assertNotIn("\n", warning)
        self.assertIn("/reconciliar", warning)
        self.assertIn("2 mutaciones", warning)

    def test_same_prompt_is_silent(self) -> None:
        self._project(later=2)
        for prompt in (self.ACTION, f"/ciclo {self.ACTION}", f"Siguiente: /ciclo {self.ACTION}",
                       f"Siguiente: /ciclo  {self.ACTION}\nModelo: standard · Esfuerzo: 🟡 medio (tier_1)",
                       f"{self.ACTION}. Empieza por los tests."):
            self.assertIsNone(self.stale.stale_prompt_warning(prompt, self.root), prompt)

    def test_paraphrase_is_silent_and_a_different_task_still_warns(self) -> None:
        """PA-FW-011 (5.3.1 L3.5): se compara por similitud, no por igualdad."""
        self._project(later=2)
        self.assertIsNone(self.stale.stale_prompt_warning(
            "/ciclo Implementa el login de src/auth.py y sus tests", self.root))
        self.assertIsNotNone(self.stale.stale_prompt_warning(
            "/ciclo Cerrar el módulo de exportación con su revisión", self.root))

    def test_different_prompt_without_later_activity_is_silent(self) -> None:
        self._project(later=0)
        self.assertIsNone(self.stale.stale_prompt_warning("Añadir el panel de informes", self.root))

    def test_template_mode_and_missing_pointer_are_silent(self) -> None:
        self._project(later=2, mode="template")
        self.assertIsNone(self.stale.stale_prompt_warning("Otra cosa", self.root))
        self._project(later=2, pointer=False)
        self.assertIsNone(self.stale.stale_prompt_warning("Otra cosa", self.root))

    def test_session_commit_of_the_closing_does_not_count(self) -> None:
        self.assertEqual(self.stale.count_commits(["chore(sesion): cierre", "feat: panel", ""]), 1)

    def test_events_after_pointer_reads_tail_without_parsing_old_history(self) -> None:
        self._project(later=2)
        log = self.root / "_project" / "state_log.jsonl"
        tail = log.read_text(encoding="utf-8")
        # Prefijo antiguo fuera de la ventana inicial (con una línea truncada antigua):
        # la lectura por cola resuelve el último puntero sin releer toda la historia.
        old_prefix = "{linea_historica_truncada\n" + (
            json.dumps({"ts": "2026-01-01T00:00:00Z", "action": "update_last_step", "pad": "x" * 200}) + "\n"
        ) * 40
        log.write_text(old_prefix + tail, encoding="utf-8")
        self.assertEqual(
            self.stale._events_after_pointer(log, tail_step=4096),
            ("2026-10-10T10:00:00Z", 2),
        )

    def test_real_commits_after_the_pointer_count_with_engine_timestamps(self) -> None:
        """Con git de verdad y el formato de fecha del motor (microsegundos y +00:00)."""
        git = lambda *args: subprocess.run(  # noqa: E731
            ["git", "-C", str(self.root), "-c", "user.name=t", "-c", "user.email=t@t", *args],
            capture_output=True, text=True, timeout=30, check=True)
        git("init", "-q")
        self._project(later=0)
        log = self.root / "_project" / "state_log.jsonl"
        log.write_text(json.dumps({"ts": "2020-01-01T00:00:00.123456+00:00", "action": "set_session_pointer"}) + "\n",
                       encoding="utf-8")
        self.assertIsNone(self.stale.stale_prompt_warning("Otra cosa", self.root))
        git("commit", "-q", "--allow-empty", "-m", "chore(sesion): cierre")
        self.assertIsNone(self.stale.stale_prompt_warning("Otra cosa", self.root))
        git("commit", "-q", "--allow-empty", "-m", "feat: panel")
        warning = self.stale.stale_prompt_warning("Otra cosa", self.root)
        self.assertIn("0 mutaciones de estado y 1 commits", warning or "")

    def test_cli_never_blocks(self) -> None:
        self._project(later=1)
        run = lambda *args: subprocess.run(  # noqa: E731
            [sys.executable, str(STALE_SCRIPT), "--project-root", str(self.root), *args],
            capture_output=True, text=True, timeout=30)
        warned = run("--instruction", "Añadir el panel de informes")
        self.assertEqual(warned.returncode, 0)
        self.assertEqual(len(warned.stdout.strip().splitlines()), 1)
        silent = run("--instruction", self.ACTION)
        self.assertEqual((silent.returncode, silent.stdout), (0, ""))
        # Como lo llama el workflow: por la entrada estándar, con comillas y guion inicial.
        stdin = subprocess.run(
            [sys.executable, str(STALE_SCRIPT), "--project-root", str(self.root)],
            input="- Arregla el 'panel' de \"informes\"; $(echo x)\n", capture_output=True, text=True, timeout=30)
        self.assertEqual(stdin.returncode, 0)
        self.assertEqual(len(stdin.stdout.strip().splitlines()), 1)
        log = self.root / "_project" / "state_log.jsonl"
        good_log = log.read_text(encoding="utf-8")
        log.write_text(good_log + "{roto\n", encoding="utf-8")
        self.assertEqual(run("--instruction", "Otra cosa").stdout, "")
        log.write_text(good_log, encoding="utf-8")
        (self.root / "_project" / "03_project_state.json").write_text("{roto", encoding="utf-8")
        broken = run("--instruction", "Añadir el panel de informes")
        self.assertEqual((broken.returncode, broken.stdout), (0, ""))

    def test_ciclo_step_zero_calls_it_without_blocking(self) -> None:
        text = _read(".agent/workflows/ciclo.md")
        step0 = text[text.index("### 0. STOP GATE"):text.index("### 1. Identificar")]
        self.assertIn("stale_prompt.py", step0)
        self.assertIn("entrada estándar", step0)
        self.assertNotIn("--instruction", step0)
        self.assertIn("aviso que no bloquea", step0)
        self.assertIn("/reconciliar", step0)


class BlockingBugRouteTests(unittest.TestCase):
    """FW-349: `/nuevo-bug` registra; `/ciclo` corrige en un módulo de Estabilización."""

    def test_no_closing_sends_a_blocking_bug_back_to_the_bug_command(self) -> None:
        for rel in (".agent/workflows/ciclo.md", ".agent/workflows/nuevo-bug.md"):
            self.assertNotIn("Siguiente: /nuevo-bug", _read(rel), rel)
        self.assertIn(
            "Siguiente: /ciclo Abrir el módulo de Estabilización estabilizacion-BUG-XXX y corregir "
            "el bug bloqueante BUG-XXX.",
            _read(".agent/workflows/nuevo-bug.md"))
        self.assertEqual(_read(".agent/workflows/ciclo.md").count(
            "Siguiente: /ciclo <instrucción: abrir un módulo de Estabilización"), 2)

    def test_stop_gate_text_has_the_stabilization_exception_and_keeps_the_rest(self) -> None:
        text = _read(".agent/workflows/ciclo.md")
        step0 = text[text.index("### 0. STOP GATE"):text.index("### 1. Identificar")]
        self.assertIn("Excepción — módulo de Estabilización (FW-349)", step0)
        self.assertIn("`stabilization: true`", step0)
        self.assertIn("Para cualquier otro módulo o instrucción el STOP GATE se aplica entero", step0)
        # Sin esto el cierre recomendado volvería a parar en el mismo paso 0 (bucle).
        self.assertIn("Si aún no existe, crearlo con `add_module` (`stabilization: true`)", step0)
        self.assertLess(step0.index("Excepción — módulo de Estabilización"), step0.index("**NO avanzar**"))
        self.assertIn("**NO avanzar**", step0)
        self.assertIn("NO saltarse el STOP GATE ante bugs críticos o reyecciones.", text)

    def test_engine_lets_only_the_stabilization_module_start(self) -> None:
        """El camino que el cierre recomienda existe en el motor: no es un bucle."""
        mgr = StateManager.__new__(StateManager)
        mgr._is_replay = False
        mgr._migration_mode = False
        with tempfile.TemporaryDirectory() as tmp:
            mgr._workspace_root = lambda: Path(tmp)
            mgr.state_data = {
                "project": "T", "framework_mode": "initialized", "framework_version": "5.2.3",
                "current_phase": "phase_01", "current_step": "s", "last_completed_step": "Init",
                "stack": {"backend": "python", "frontend": "none", "database": "none", "auth": "none"},
                "modules": {"completed": [], "in_progress": [], "pending": ["mod_normal"]},
                "module_statuses": {"mod_normal": "not_started"},
                "work_tiers": {}, "module_specs": {}, "contracts": {"public_interfaces": {}},
                "dependencies": [], "architecture_constraints": [], "active_mcps": [],
                "closure_rejections": [], "_clarifications": [], "_refs": {},
                "_bugs": [{"id": "BUG-001", "module_id": "mod_normal", "severity": "Crítico",
                           "status": "Abierto", "description": "d", "created_at": "2026-10-12T00:00:00Z"}],
            }
            with self.assertRaises(ValueError) as ctx:
                mgr._apply_event("update_module_status", {"id": "mod_normal", "status": "in_progress"},
                                 "2026-10-12T01:00:00Z")
            self.assertIn("criterio de parada activo", str(ctx.exception))
            mgr._apply_event("add_module", {"id": "mod_estabilizacion", "stabilization": True},
                             "2026-10-12T01:01:00Z")
            result = mgr._apply_event("update_module_status",
                                      {"id": "mod_estabilizacion", "status": "in_progress"},
                                      "2026-10-12T01:02:00Z")
            self.assertIn("actualizado a 'in_progress'", result)


if __name__ == "__main__":
    unittest.main()
