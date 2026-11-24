#!/usr/bin/env python3
"""Contrato de L9 (plan 5.3.0): ejecutores y revisores en worktree.

Escrito por el orquestador en rojo antes de tocar código. Cubre FW-341 (entrega
sin commit, `.codex/` en el checkout principal, patch para revisores), FW-342
(prefijo de FW: error hacia delante), el bug de FW-332 (`mode` en `ciclo_*`),
FW-346 (el ejecutor parte del sha del lote), FW-356 (el informe del ejecutor no
es evidencia) y la decisión A del builder sobre el punto 6 (señal de pytest).

Cada clase lleva sus PRESERVAR. Este fichero no se modifica durante la ejecución.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / ".agent" / "scripts"))
sys.path.insert(0, str(ROOT / "tests"))

import action_contracts  # noqa: E402
import ensure_codex_integration  # noqa: E402
import framework_doctor as doctor  # noqa: E402
import fw_registry  # noqa: E402
from test_codex_integration import _copy_codex_runtime  # noqa: E402
import test_l8_contrato as l8  # noqa: E402
from test_l8_contrato import _mutate, _query, _root  # noqa: E402


def _read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


def _two_modules() -> dict:
    return l8.Punto3ObjetivoDeStartup._state(
        l8.Punto3ObjetivoDeStartup(), ["M1", "M2"], {"M1": "in_progress", "M2": "in_progress"}
    )


def _last_log(root: Path) -> dict:
    lines = (root / "_project" / "state_log.jsonl").read_text(encoding="utf-8").splitlines()
    return json.loads(lines[-1])


# ---------------------------------------------------------------------------
# Punto 4 — `ciclo_finished` y `ciclo_started` con `mode` (bug de FW-332)
# ---------------------------------------------------------------------------
class Punto4CicloConMode(unittest.TestCase):
    MODES = ("modulo", "fase", "paralelo")

    def _project(self) -> Path:
        holder, root = _root(_two_modules())
        self.addCleanup(holder.cleanup)
        return root

    def test_ciclo_finished_con_mode_se_acepta(self):
        for mode in self.MODES:
            root = self._project()
            done = _mutate(root, "ciclo_finished", {"module": "M2", "result": "ok", "mode": mode})
            self.assertEqual(done.returncode, 0, mode + ": " + done.stdout + done.stderr)
            self.assertEqual(_last_log(root)["payload"].get("mode"), mode)

    def test_ciclo_started_con_mode_se_acepta(self):
        for mode in self.MODES:
            root = self._project()
            started = _mutate(root, "ciclo_started", {"module": "M2", "mode": mode})
            self.assertEqual(started.returncode, 0, mode + ": " + started.stdout + started.stderr)

    def test_mode_fuera_del_conjunto_se_rechaza(self):
        root = self._project()
        for action, payload in (
            ("ciclo_finished", {"module": "M2", "result": "ok", "mode": "otro"}),
            ("ciclo_started", {"module": "M2", "mode": "otro"}),
            ("ciclo_finished", {"module": "M2", "result": "ok", "mode": ""}),
            ("ciclo_started", {"module": "M2", "mode": 3}),
        ):
            rejected = _mutate(root, action, payload)
            self.assertNotEqual(rejected.returncode, 0, f"{action} {payload} debió rechazarse")

    def test_el_contrato_declara_mode_opcional_y_cerrado(self):
        for action in ("ciclo_finished", "ciclo_started"):
            field = action_contracts.ACTION_DESCRIPTIONS[action]["fields"].get("mode")
            self.assertIsNotNone(field, f"{action} no declara mode")
            self.assertEqual(sorted(field.get("allowed_values", [])), sorted(self.MODES))

    def test_ciclo_md_envia_el_payload_completo(self):
        text = _read(".agent/workflows/ciclo.md")
        self.assertNotIn('con `{"mode": "fase"}`', text, "la telemetría de fase sigue sin module ni result")
        full = [line for line in text.splitlines()
                if "ciclo_finished" in line and '"module"' in line and '"result"' in line and '"mode": "fase"' in line]
        self.assertTrue(full, "ciclo.md debe enviar ciclo_finished con module, result y mode en modo fase")
        started = [line for line in text.splitlines()
                   if "ciclo_started" in line and '"module"' in line and '"mode": "fase"' in line]
        self.assertTrue(started, "ciclo.md debe enviar ciclo_started con module y mode en modo fase")

    def test_replay_no_rejuzga_mode_d10(self):
        import state_core
        manager = state_core.StateManager.__new__(state_core.StateManager)
        manager._is_replay = True
        manager._validate_cycle_mode("ciclo_finished", {"module": "M2", "result": "ok", "mode": "otro"})
        manager._is_replay = False
        with self.assertRaises(ValueError):
            manager._validate_cycle_mode("ciclo_finished", {"module": "M2", "result": "ok", "mode": "otro"})

    # --- PRESERVAR --------------------------------------------------------
    def test_preservar_sin_mode_igual_que_en_5_2_3(self):
        root = self._project()
        done = _mutate(root, "ciclo_finished", {"module": "M2", "result": "ok"})
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertEqual(_last_log(root)["result"], "ciclo_finished: M2 (result=ok)")
        started = _mutate(root, "ciclo_started", {"module": "M1"})
        self.assertEqual(_last_log(root)["result"], "ciclo_started: M1")
        self.assertEqual(started.returncode, 0)

    def test_preservar_result_cerrado_con_y_sin_mode(self):
        root = self._project()
        for payload in ({"module": "M2", "result": "done"}, {"module": "M2", "result": "done", "mode": "fase"},
                        {"module": "M2", "mode": "fase"}, {"result": "ok", "mode": "fase"}):
            self.assertNotEqual(_mutate(root, "ciclo_finished", payload).returncode, 0, payload)
        self.assertNotEqual(_mutate(root, "ciclo_started", {"mode": "fase"}).returncode, 0)

    def test_preservar_pista_de_ciclo_de_l8_con_mode(self):
        root = self._project()
        self.assertEqual(_mutate(root, "ciclo_started", {"module": "M2", "mode": "fase"}).returncode, 0)
        self.assertEqual(_query(root, "startup")["module_id"], "M2", "un ciclo_started con mode sigue dando la pista")
        self.assertEqual(_mutate(root, "ciclo_finished", {"module": "M2", "result": "ok", "mode": "fase"}).returncode, 0)
        self.assertEqual(_query(root, "startup")["module_id"], "M1", "un ciclo_finished con mode sigue cerrando la pista")

    def test_preservar_pista_de_ciclo_de_l8_sin_mode(self):
        root = self._project()
        self.assertEqual(_mutate(root, "ciclo_started", {"module": "M2"}).returncode, 0)
        self.assertEqual(_mutate(root, "ciclo_finished", {"module": "M2", "result": "paused"}).returncode, 0)
        self.assertEqual(_query(root, "startup")["module_id"], "M1")


# ---------------------------------------------------------------------------
# Punto 2 — `.codex/` se valida en el checkout principal (PA:FW-283, FW-308)
# ---------------------------------------------------------------------------
def _git(cwd: Path, *args: str) -> None:
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, env=env)


class Punto2WorktreeSecundario(unittest.TestCase):
    def _codex_project(self, *, with_codex: bool = True, git: bool = True) -> Path:
        holder = tempfile.TemporaryDirectory()
        self.addCleanup(holder.cleanup)
        main = Path(holder.name) / "main"
        main.mkdir()
        _copy_codex_runtime(main)
        if with_codex:
            ensure_codex_integration.ensure_codex_integration(main, "Codex", native_only=True)
        (main / "_project").mkdir()
        (main / "_project" / "15_environment_profile.md").write_text("- Herramienta: Codex\n", encoding="utf-8")
        (main / ".gitignore").write_text(".codex/\n", encoding="utf-8")
        if git:
            _git(main, "init", "-q")
            _git(main, "add", "-A")
            _git(main, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "base", "--no-verify")
        return main

    def _worktree(self, main: Path) -> Path:
        wt = main.parent / "wt"
        _git(main, "worktree", "add", "-q", "--detach", str(wt))
        self.assertFalse((wt / ".codex").exists(), "el worktree no debe traer .codex/ (está en .gitignore)")
        return wt

    def _doctor(self, root: Path) -> list[str]:
        original = doctor.ROOT
        doctor.ROOT = root
        try:
            errors: list[str] = []
            warnings: list[str] = []
            doctor.validate_codex_integration("initialized", {}, errors, warnings)
        finally:
            doctor.ROOT = original
        return [e for e in errors if ".codex/" in e]

    def test_worktree_sin_codex_pasa_si_el_principal_lo_tiene(self):
        main = self._codex_project()
        self.assertEqual(self._doctor(main), [], "el principal debe pasar")
        self.assertEqual(self._doctor(self._worktree(main)), [])

    def test_worktree_usa_la_configuracion_del_principal(self):
        main = self._codex_project()
        wt = self._worktree(main)
        (main / ".codex" / "hooks.json").write_text("{no es json", encoding="utf-8")
        errors = self._doctor(wt)
        self.assertTrue(any("hooks.json" in e and "JSON" in e for e in errors), errors)

    def test_git_dir_heredado_de_otro_repo_no_confunde(self):
        main = self._codex_project()
        wt = self._worktree(main)
        other = self._codex_project(with_codex=False)
        saved = {k: os.environ.get(k) for k in ("GIT_DIR", "GIT_WORK_TREE")}
        os.environ["GIT_DIR"] = str(other / ".git")
        os.environ["GIT_WORK_TREE"] = str(other)
        try:
            self.assertEqual(self._doctor(wt), [])
        finally:
            for key, value in saved.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value

    def test_worktree_falla_si_tampoco_el_principal_lo_tiene(self):
        main = self._codex_project(with_codex=False)
        errors = self._doctor(self._worktree(main))
        self.assertTrue(any(".codex/hooks.json faltante" in e for e in errors), errors)

    # --- PRESERVAR: sin worktrees, como en 5.2.3 ----------------------------
    def test_preservar_checkout_principal_sin_codex_falla_igual(self):
        errors = self._doctor(self._codex_project(with_codex=False))
        self.assertTrue(any(".codex/hooks.json faltante" in e for e in errors), errors)
        self.assertTrue(any(".codex/config.toml faltante" in e for e in errors), errors)

    def test_preservar_sin_git_se_valida_el_arbol_actual(self):
        errors = self._doctor(self._codex_project(with_codex=False, git=False))
        self.assertTrue(any(".codex/hooks.json faltante" in e for e in errors), errors)


# ---------------------------------------------------------------------------
# Punto 5 — prefijo de FW: error hacia delante (FW-342)
# ---------------------------------------------------------------------------
class Punto5PrefijoFW(unittest.TestCase):
    def _adopter(self, report: str, prefix: str | None = "TL") -> Path:
        holder = tempfile.TemporaryDirectory()
        self.addCleanup(holder.cleanup)
        root = Path(holder.name)
        (root / "_project").mkdir()
        (root / "_feedback").mkdir()
        profile = f"# Perfil\n- `fw_prefix`: {prefix}\n" if prefix else "# Perfil\n"
        (root / "_project" / "15_environment_profile.md").write_text(profile, encoding="utf-8")
        (root / "_feedback" / "field_report.md").write_text(report, encoding="utf-8")
        return root

    def _run(self, root: Path, mode: str = "initialized") -> tuple[list[str], list[str]]:
        original = doctor.ROOT
        doctor.ROOT = root
        errors: list[str] = []
        warnings: list[str] = []
        try:
            doctor.validate_fw_registry(mode, warnings, errors=errors)
        finally:
            doctor.ROOT = original
        return errors, warnings

    def test_hay_fecha_de_corte(self):
        self.assertEqual(fw_registry.PREFIX_REQUIRED_FROM, "2026-10-13")
        self.assertEqual(fw_registry.PREFIX_ENFORCED_FROM, "2026-10-01", "el aviso de 5.2.3 conserva su fecha")

    def test_entrada_nueva_sin_prefijo_es_error(self):
        errors, _ = self._run(self._adopter("## FW-400 — algo\n\n**Fecha:** 2026-11-02\n"))
        self.assertTrue(any("FW-400" in e and "TL-" in e for e in errors), errors)

    def test_entrada_del_dia_de_corte_es_error(self):
        errors, _ = self._run(self._adopter("## FW-401 — algo (2026-10-13)\n"))
        self.assertTrue(any("FW-401" in e for e in errors), errors)

    def test_sin_fw_prefix_y_entrada_nueva_es_error_que_pide_declararlo(self):
        errors, _ = self._run(self._adopter("## FW-400 — algo\n\n**Fecha:** 2026-11-02\n", prefix=None))
        self.assertTrue(any("FW-400" in e and "fw_prefix" in e for e in errors), errors)

    def test_entrada_nueva_con_prefijo_no_es_error(self):
        errors, warnings = self._run(self._adopter("## TL-FW-400 — algo\n\n**Fecha:** 2026-11-02\n"))
        self.assertEqual(errors, [])
        self.assertFalse([w for w in warnings if "FW-400" in w], warnings)

    def test_reincidencia_h3_de_una_fw_antigua_no_es_error(self):
        report = ("## FW-100 — vieja\n\n**Fecha:** 2026-08-01\n\n"
                  "### FW-100 — Reincidencia (2026-11-01)\n\nTexto.\n")
        errors, _ = self._run(self._adopter(report))
        self.assertEqual(errors, [])

    def test_seccion_h2_que_reusa_un_numero_antiguo_no_es_error(self):
        report = "## FW-100 — vieja\n\n**Fecha:** 2026-08-01\n\n## FW-100 — Reincidencia (2026-11-01)\n"
        errors, _ = self._run(self._adopter(report))
        self.assertEqual(errors, [])

    def test_reincidencia_escrita_encima_del_original_no_es_error(self):
        report = "## FW-100 — Reincidencia (2026-11-01)\n\n## FW-100 — vieja\n\n**Fecha:** 2026-08-01\n"
        errors, _ = self._run(self._adopter(report))
        self.assertEqual(errors, [])

    def test_sin_fecha_propia_no_hereda_la_de_la_seccion_siguiente(self):
        report = "## FW-401 — sin fecha\n\n## TL-FW-402 — otra\n**Fecha:** 2026-11-02\n"
        errors, warnings = self._run(self._adopter(report))
        self.assertEqual(errors, [])
        self.assertFalse([w for w in warnings if "FW-401" in w], warnings)

    def test_ventana_y_corte_no_se_repiten(self):
        report = ("## FW-300 — en la ventana\n\n**Fecha:** 2026-10-05\n\n"
                  "## FW-400 — tras el corte\n\n**Fecha:** 2026-11-02\n")
        errors, warnings = self._run(self._adopter(report))
        self.assertTrue(any("FW-400" in e for e in errors), errors)
        self.assertFalse([e for e in errors if "FW-300" in e], errors)
        fw_warnings = [w for w in warnings if "FW-3" in w or "FW-4" in w]
        self.assertTrue(any("FW-300" in w for w in fw_warnings), warnings)
        self.assertFalse([w for w in fw_warnings if "FW-400" in w], warnings)

    # --- PRESERVAR --------------------------------------------------------
    def test_preservar_ventana_de_5_2_3_sigue_siendo_aviso(self):
        errors, warnings = self._run(self._adopter("## FW-300 — algo\n\n**Fecha:** 2026-10-05\n"))
        self.assertEqual(errors, [])
        self.assertTrue(any("FW-300" in w for w in warnings), warnings)

    def test_preservar_antigua_no_da_nada(self):
        errors, warnings = self._run(self._adopter("## FW-100 — algo\n\n**Fecha:** 2026-08-02\n"))
        self.assertEqual(errors, [])
        self.assertFalse([w for w in warnings if "FW-100" in w], warnings)

    def test_preservar_sin_fw_prefix_y_sin_entradas_nuevas_solo_avisa(self):
        errors, warnings = self._run(self._adopter("## FW-300 — algo\n\n**Fecha:** 2026-10-05\n", prefix=None))
        self.assertEqual(errors, [])
        self.assertTrue(any("fw_prefix" in w for w in warnings), warnings)

    def test_preservar_plantilla_numera_sin_prefijo(self):
        root = self._adopter("## FW-400 — algo\n\n**Fecha:** 2026-11-02\n")
        (root / ".agent" / "docs").mkdir(parents=True)
        (root / ".agent" / "docs" / "field_report.md").write_text(
            "## FW-400 — algo\n\n**Fecha:** 2026-11-02\n", encoding="utf-8")
        errors, warnings = self._run(root, mode="template")
        self.assertEqual(errors, [])
        self.assertFalse([w for w in warnings if "prefijo" in w], warnings)

    def test_preservar_firma_antigua(self):
        original = doctor.ROOT
        doctor.ROOT = self._adopter("## FW-100 — algo\n\n**Fecha:** 2026-08-02\n")
        warnings: list[str] = []
        try:
            doctor.validate_fw_registry("initialized", warnings)
        finally:
            doctor.ROOT = original
        self.assertFalse([w for w in warnings if "FW-100" in w], warnings)
        doctor.ROOT = self._adopter("## FW-400 — algo\n\n**Fecha:** 2026-11-02\n")
        warnings = []
        try:
            doctor.validate_fw_registry("initialized", warnings)
        finally:
            doctor.ROOT = original
        self.assertTrue(any("FW-400" in w for w in warnings), "sin errors, lo nuevo sigue siendo aviso")

    # --- Cerrar el camino: instrucciones y alta del prefijo -----------------
    def test_las_instrucciones_que_lee_el_agente_dicen_el_prefijo(self):
        rels = (".agent/scripts/pre_commit.sh", ".agent/workflows/cierre-modulo.md", ".agent/workflows/gycd.md",
                ".agent/workflows/ciclo.md", ".agent/prompts/cosecha_sesion.md", "CLAUDE.md", "AGENTS.md")
        for rel in rels:
            text = _read(rel)
            self.assertRegex(text, r"PREFIJO>?-FW-NNN", rel)

    def test_el_pre_commit_no_pide_fw_nnn_a_secas(self):
        text = _read(".agent/scripts/pre_commit.sh")
        asks = re.findall(r"registrala AHORA como ([^ ]+)", text)
        self.assertTrue(asks)
        for ask in asks:
            self.assertRegex(ask, r"PREFIJO>?-FW-NNN", ask)

    def test_los_workflows_de_alta_piden_fw_prefix(self):
        for rel in (".agent/workflows/actualizar-framework.md", ".agent/workflows/migrar-proyecto.md",
                    ".agent/workflows/nuevo-proyecto.md"):
            self.assertIn("fw_prefix", _read(rel), rel)


# ---------------------------------------------------------------------------
# Punto 6 — señal de pytest (decisión A del builder)
# ---------------------------------------------------------------------------
REAL_HOOK = ROOT / ".agent" / "scripts" / "pre_commit.sh"


def _exec(path: Path, body: str) -> None:
    path.write_text(body, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)


class Punto6SenalPytest(unittest.TestCase):
    def _sandbox(self, *, test_files: bool = True, pytest_ok: bool = True, pytest_exit: int = 0) -> tuple[Path, Path]:
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        sb = Path(tmp)
        scripts = sb / ".agent" / "scripts"
        scripts.mkdir(parents=True)
        shutil.copy(REAL_HOOK, scripts / "pre_commit.sh")
        _exec(scripts / "framework_doctor.py", "import sys\nsys.exit(0)\n")
        _exec(scripts / "validate_transition.py", "import sys\nsys.exit(0)\n")
        (sb / "tests").mkdir()
        (sb / "tests" / "__init__.py").write_text("", encoding="utf-8")
        if test_files:
            (sb / "tests" / "test_ok.py").write_text(
                "import unittest\nclass T(unittest.TestCase):\n    def test_ok(self): self.assertTrue(True)\n",
                encoding="utf-8",
            )
        runner_log = sb / "runner_log"
        venvbin = sb / ".venv" / "bin"
        venvbin.mkdir(parents=True)
        _exec(
            venvbin / "python",
            "#!/usr/bin/env bash\n"
            'args="$*"\n'
            'if [[ "$args" == *"import pytest"* ]]; then\n'
            f"    exit {0 if pytest_ok else 1}\n"
            "fi\n"
            'if [[ "$args" == *"-m pytest"* ]]; then\n'
            f'    echo pytest >> "{runner_log}"\n'
            f"    exit {pytest_exit}\n"
            "fi\n"
            'if [[ "$args" == *"-m unittest"* ]]; then\n'
            f'    echo unittest >> "{runner_log}"\n'
            "    exit 0\n"
            "fi\n"
            'exec python3 "$@"\n',
        )
        return sb, runner_log

    def _run(self, sb: Path) -> subprocess.CompletedProcess:
        env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
        env["FRAMEWORK_PYTHON"] = ""
        return subprocess.run(["bash", ".agent/scripts/pre_commit.sh"], cwd=sb, capture_output=True, text=True, env=env)

    def _runner(self, log: Path) -> str:
        return log.read_text(encoding="utf-8") if log.exists() else ""

    def test_pyproject_con_tool_pytest_es_senal(self):
        sb, log = self._sandbox()
        (sb / "pyproject.toml").write_text('[tool.pytest.ini_options]\naddopts = "-q"\n', encoding="utf-8")
        res = self._run(sb)
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        self.assertIn("pytest", self._runner(log))
        self.assertNotIn("unittest", self._runner(log))

    def test_dot_pytest_ini_es_senal(self):
        sb, log = self._sandbox()
        (sb / ".pytest.ini").write_text("[pytest]\n", encoding="utf-8")
        res = self._run(sb)
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        self.assertIn("pytest", self._runner(log))

    def test_senal_sin_pytest_importable_bloquea(self):
        sb, log = self._sandbox(pytest_ok=False)
        (sb / "tests" / "conftest.py").write_text("", encoding="utf-8")
        res = self._run(sb)
        self.assertNotEqual(res.returncode, 0, "con señal de pytest y sin pytest, el fallback a unittest da un falso verde")
        self.assertIn("pytest", res.stdout + res.stderr)
        self.assertNotIn("unittest", self._runner(log))

    def test_coleccion_vacia_con_ficheros_de_test_bloquea(self):
        sb, _ = self._sandbox(pytest_exit=5)
        (sb / "tests" / "conftest.py").write_text("", encoding="utf-8")
        res = self._run(sb)
        self.assertNotEqual(res.returncode, 0, "pytest no recogió nada pese a haber test_*.py")

    def test_toml_invalido_bloquea(self):
        sb, _ = self._sandbox()
        (sb / "pyproject.toml").write_text("[tool.pytest\nroto", encoding="utf-8")
        res = self._run(sb)
        self.assertNotEqual(res.returncode, 0)
        self.assertIn("TOML", res.stdout + res.stderr)

    # --- PRESERVAR --------------------------------------------------------
    def test_preservar_fw_038_coleccion_vacia_sin_ficheros_de_test_pasa(self):
        sb, _ = self._sandbox(test_files=False, pytest_exit=5)
        (sb / "tests" / "conftest.py").write_text("", encoding="utf-8")
        res = self._run(sb)
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)

    def test_preservar_pyproject_sin_pytest_usa_unittest(self):
        sb, log = self._sandbox()
        (sb / "pyproject.toml").write_text('[project]\nname = "x"\n', encoding="utf-8")
        res = self._run(sb)
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        self.assertIn("unittest", self._runner(log))
        self.assertNotIn("pytest", self._runner(log))

    def test_preservar_sin_senal_usa_unittest(self):
        sb, log = self._sandbox()
        res = self._run(sb)
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        self.assertEqual(self._runner(log).split(), ["unittest"])

    def test_el_validador_de_un_proyecto_ajeno_no_entra(self):
        self.assertNotIn("validate_project.py", _read(".agent/scripts/pre_commit.sh"))


# ---------------------------------------------------------------------------
# Puntos 1 y 3 — cableado de la entrega sin commit y del patch (FW-341, 346, 356)
# ---------------------------------------------------------------------------
class Punto1EntregaSinCommit(unittest.TestCase):
    def test_executor_entrega_diff_sin_commit(self):
        text = _read(".agent/subagents/executor.md")
        self.assertRegex(text, r"(?i)sin commit")
        self.assertRegex(text, r"(?i)\bdiff\b")
        self.assertNotIn("BRANCH: <rama", text)
        self.assertRegex(text, r"(?m)^BASE: ")
        self.assertRegex(text, r"(?m)^PATCH: ")
        self.assertRegex(text, r"(?m)^EXECUTOR_RESULT: ok \| fail \| needs_builder$")

    def test_executor_no_toca_estado_ni_framework(self):
        text = _read(".agent/subagents/executor.md")
        self.assertIn("`_project/`", text)
        self.assertIn("`.agent/`", text)

    def test_executor_parte_del_sha_del_lote_fw_346(self):
        text = _read(".agent/subagents/executor.md")
        self.assertIn("git rev-parse HEAD", text)
        self.assertIn("--detach", text)

    def test_ciclo_p3_crea_el_worktree_desde_el_sha(self):
        self.assertIn("git worktree add --detach", _read(".agent/workflows/ciclo.md"))

    def test_ciclo_p4_integra_por_rutas_con_inventario(self):
        text = _read(".agent/workflows/ciclo.md")
        self.assertNotIn("Integrar ramas una a una (merge", text)
        self.assertIn("rutas de producto", text)
        self.assertRegex(text, r"(?i)inventario")
        self.assertRegex(text, r"(?i)un commit por módulo")

    def test_ciclo_p4_el_informe_del_ejecutor_no_es_evidencia_fw_356(self):
        text = _read(".agent/workflows/ciclo.md")
        self.assertRegex(text, r"(?i)el informe del ejecutor no es evidencia")
        self.assertRegex(text, r"(?i)en el diff")

    def test_el_ejecutor_no_enriquece_lo_hace_el_driver(self):
        text = _read(".agent/subagents/executor.md")
        self.assertNotIn("enriquecer (si falta)", text)
        self.assertRegex(text, r"(?i)el driver enriquece")

    def test_el_patch_del_ejecutor_va_fuera_del_arbol_y_admite_binarios(self):
        text = _read(".agent/subagents/executor.md")
        self.assertIn("git rev-parse --git-dir", text)
        self.assertIn("--binary", text)

    def test_ciclo_p4_integra_tambien_los_tests(self):
        self.assertRegex(_read(".agent/workflows/ciclo.md"), r"rutas de producto y (sus )?tests")

    # --- PRESERVAR --------------------------------------------------------
    def test_preservar_cierre_en_el_arbol_principal_y_en_serie(self):
        self.assertIn("`/cierre-modulo` corre siempre en el árbol principal", _read(".agent/subagents/executor.md"))
        text = _read(".agent/workflows/ciclo.md")
        self.assertIn("`/cierre-modulo` por módulo: los gates de cierre no se paralelizan.", text)
        self.assertIn("state_write_lock", text)

    def test_preservar_executor_no_muta_estado(self):
        self.assertIn("**NO muta el estado**", _read(".agent/subagents/executor.md"))


class Punto3PatchParaRevisores(unittest.TestCase):
    def test_cierre_genera_el_patch_fuera_del_arbol(self):
        text = _read(".agent/workflows/cierre-modulo.md")
        self.assertIn("git rev-parse --git-dir", text)
        self.assertIn("framework-review/", text)
        self.assertIn(".patch", text)

    def test_el_patch_de_la_ronda_incluye_ficheros_nuevos(self):
        text = _read(".agent/workflows/cierre-modulo.md")
        for line in [l for l in text.splitlines() if "framework-review/" in l and "git diff" in l]:
            self.assertIn("git add -N", line, line)
            self.assertIn("--binary", line, line)

    def test_cierre_no_pide_git_diff_al_revisor(self):
        text = _read(".agent/workflows/cierre-modulo.md")
        self.assertRegex(text, r"(?i)(nunca|sin) pedir(le|les)? `?git diff`?")

    def test_reviewer_y_security_leen_el_patch(self):
        for rel in (".agent/subagents/reviewer.md", ".agent/subagents/security.md",
                    ".claude/agents/reviewer.md", ".claude/agents/security.md"):
            self.assertRegex(_read(rel), r"(?i)\bpatch\b", rel)

    def test_wiring_del_doctor_cubre_mode_y_patch(self):
        wiring = json.loads(_read(".agent/scripts/doctor_wiring.json"))["features"]
        dump = json.dumps(wiring, ensure_ascii=False)
        self.assertIn('\\"mode\\": \\"fase\\"', dump)
        self.assertIn("framework-review/", dump)

    # --- PRESERVAR: FW-206 --------------------------------------------------
    def test_preservar_revisores_de_solo_lectura(self):
        for name in ("reviewer", "security"):
            front = _read(f".claude/agents/{name}.md").split("---")[1]
            tools = re.search(r"(?m)^tools:\s*(.*)$", front)
            self.assertIsNotNone(tools, name)
            for forbidden in ("Bash", "Write", "Edit", "NotebookEdit"):
                self.assertNotIn(forbidden, tools.group(1), name)
            self.assertIn("Solo lectura", _read(f".agent/subagents/{name}.md"))


if __name__ == "__main__":
    unittest.main()
