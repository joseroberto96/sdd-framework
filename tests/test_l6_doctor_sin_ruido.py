"""5.3.1 L6 — doctor sin ruido y peso.

L6.1: avisos falsos (FW-306 superpowers, FW-307 índice de palabras clave de Codex,
FW-274 mensaje de `seed_snapshot`). Todos los avisos de L6 son WARN.
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / ".agent" / "scripts"))

import framework_doctor as doctor  # noqa: E402
import replay_state_log as rsl  # noqa: E402
import state_core  # noqa: E402


def git(root: Path, *args: str) -> str:
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    return subprocess.run(["git", "-c", "core.excludesFile=/dev/null", *args], cwd=root, capture_output=True,
                          text=True, env=env, check=True).stdout


class TmpCase(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)

    def write(self, rel, content):
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, int):
            with open(path, "wb") as fh:
                fh.truncate(content)
        elif isinstance(content, bytes):
            path.write_bytes(content)
        else:
            path.write_text(content, encoding="utf-8")
        return path


class AvisosFalsosTests(TmpCase):
    """L6.1: FW-306, FW-307 y FW-274."""

    def host(self, mode="initialized"):
        warnings: list[str] = []
        doctor.validate_host_conflicts(mode, warnings, home=self.root)
        return warnings

    def test_fw306_catalogo_de_claude_sin_plugin_habilitado_no_avisa(self):
        self.write(".claude/settings.json", json.dumps({
            "extraKnownMarketplaces": {"superpowers-marketplace": {"source": {"source": "github"}}},
            "enabledPlugins": {"otro@x": True},
        }))
        self.assertEqual(self.host(), [])

    def test_fw306_catalogo_de_codex_con_plugin_desactivado_no_avisa(self):
        self.write(".codex/config.toml", '[marketplaces.superpowers-dev]\nsource_type = "git"\n\n'
                   '[plugins."superpowers@superpowers-dev"]\nenabled = false\n')
        self.assertEqual(self.host(), [])

    def test_fw306_plugin_habilitado_sigue_avisando(self):
        self.write(".claude/settings.json", json.dumps({
            "extraKnownMarketplaces": {"superpowers-marketplace": {}},
            "enabledPlugins": {"superpowers@superpowers-marketplace": True},
        }))
        self.write(".codex/config.toml", '[plugins."superpowers@superpowers-dev"]\nenabled = true\n')
        warnings = self.host()
        self.assertEqual(len(warnings), 2, warnings)
        self.assertTrue(any("settings.json" in w for w in warnings) and any("config.toml" in w for w in warnings))

    def test_fw306_settings_que_no_es_un_objeto_no_rompe(self):
        self.write(".claude/settings.json", "[]")
        self.assertEqual(self.host(), [])

    def test_fw307_seccion_keywords_de_codex_no_avisa(self):
        self.write(".codex/memories/MEMORY.md", (
            "# Task Group: framework\n\n### keywords\n\n"
            "- Codex, AGENTS.md, codex_context_budget.py, codex_execution.py, BLOCKED_HANDOFF, relevo\n\n"
            "## Task 1\n\n- el builder prefiere prosa llana\n"))
        self.assertEqual(self.host(), [])

    def test_fw307_linea_indice_de_terminos_no_avisa(self):
        self.write(".codex/memories/MEMORY.md", "- Codex, AGENTS.md, codex_context_budget.py, codex_execution.py, "
                   "BLOCKED_HANDOFF, small self-contained tasks\n")
        self.assertEqual(self.host(), [])

    def test_fw307_linea_indice_con_titulo_de_memory_summary_no_avisa(self):
        self.write(".codex/memories/memory_summary.md", "- Delegate small Codex tasks to subagents safely: Codex, "
                   "AGENTS.md, codex_context_budget.py, codex_execution.py, BLOCKED_HANDOFF, relevo\n")
        self.assertEqual(self.host(), [])

    def test_fw307_la_regla_fuera_del_indice_sigue_avisando(self):
        self.write(".codex/memories/MEMORY.md", "### keywords\n\n- a, b, c\n\n## Reglas\n\n"
                   "- Ante bloqueo emite BLOCKED_HANDOFF\n")
        warnings = self.host()
        self.assertTrue(any("MEMORY.md" in w and "BLOCKED_HANDOFF" in w for w in warnings), warnings)


class SeedSnapshotMessageTests(TmpCase):
    """L6.1, FW-274: el mensaje distingue la base del replay de lo añadido al estado vivo."""

    def setUp(self):
        super().setUp()
        self.past = rsl._seed_state()
        self.past.update({
            "_bugs": [{"id": "BUG-001", "status": "Cerrado", "severity": "Menor", "description": "x",
                       "affects_ac": False}],
            "module_statuses": {"m_old": "completed"},
            "stack": {"backend": "Python", "frontend": "HTMX", "database": "SQLite", "auth": "ninguna"},
        })
        self.write("_project/03_project_state.json", json.dumps(self.past))
        git(self.root, "init", "-q")
        git(self.root, "add", "-A")
        git(self.root, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "base")
        self.commit = git(self.root, "rev-parse", "HEAD").strip()
        self.by_reference = {"source_commit": self.commit, "reason": "ancla",
                             "state_hash": state_core.state_hash_at_commit(self.root, self.commit)}

    def seed(self, live_state, payload):
        manager = state_core.StateManager.__new__(state_core.StateManager)
        manager._is_replay = True
        manager._replay_workspace_root = self.root
        manager._replay_warnings = []
        manager._state_sig = None
        manager.state_data = json.loads(json.dumps(live_state))
        return manager._apply_event("seed_snapshot", payload, "2026-10-21T00:00:00Z"), manager.state_data

    def test_por_referencia_sin_nada_que_anadir_declara_la_base(self):
        message, _ = self.seed(self.past, self.by_reference)
        self.assertIn(f"base del replay declarada en {self.commit} (por referencia)", message)
        self.assertIn("0 elemento(s) añadidos al estado vivo; ya estaban todos", message)

    def test_por_referencia_que_anade_cuenta_lo_anadido(self):
        message, state = self.seed(rsl._seed_state(), self.by_reference)
        self.assertIn("base del replay declarada", message)
        self.assertIn("6 elemento(s) añadidos al estado vivo", message)
        self.assertEqual(state["stack"]["backend"], "Python")

    def test_por_referencia_sin_entidades_sembrables_lo_dice(self):
        self.write("_project/03_project_state.json", "{}")
        git(self.root, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qam", "vacío")
        commit = git(self.root, "rev-parse", "HEAD").strip()
        message, _ = self.seed(rsl._seed_state(), {"source_commit": commit, "reason": "x",
                                                    "state_hash": state_core.state_hash_at_commit(self.root, commit)})
        self.assertEqual(message, f"seed_snapshot: base del replay declarada en {commit} (por referencia); "
                         "0 elemento(s) añadidos: el commit no tiene entidades sembrables")

    def test_claves_de_stack_vacias_no_cuentan_y_el_estado_no_cambia(self):
        empty = {"backend": "", "frontend": "", "database": "", "auth": ""}
        live = rsl._seed_state()
        live["stack"] = dict(empty)
        message, state = self.seed(live, {"source_commit": "abc1234", "reason": "x", "entities": {"stack": empty}})
        self.assertEqual(message, "seed_snapshot: semilla en línea desde abc1234; "
                         "0 elemento(s) añadidos al estado vivo; ya estaban todos")
        self.assertEqual(state["stack"], empty)


class DoctorRootCase(TmpCase):
    def run_in_root(self, fn, *args):
        warnings: list[str] = []
        original = doctor.ROOT
        doctor.ROOT = self.root
        try:
            fn(*args, warnings)
        finally:
            doctor.ROOT = original
        return warnings


PYPROJECT = ('[project]\nname = "x"\ndependencies = ["ccxt==4.5.78", "pypdf==6.16.1"]\n'
             '[project.optional-dependencies]\ndev = ["pytest==9.1.0"]\n')
LOCK = "ccxt==4.5.78\npypdf==6.16.1\npytest==9.1.0\n"


class DependenciasTests(DoctorRootCase):
    """L6.2, TL-FW-314: `pyproject.toml` y `requirements.lock`, duplicados y pins."""

    def check(self, deps, mode="initialized", files=None):
        for rel, text in (files if files is not None else {"pyproject.toml": PYPROJECT, "requirements.lock": LOCK}).items():
            self.write(rel, text)
        return self.run_in_root(doctor.validate_dependencies_requirements_alignment, mode, {"dependencies": deps})

    def test_mismo_paquete_con_versiones_distintas_avisa_con_la_del_lock(self):
        warnings = self.check(["ccxt==4.5.70", "ccxt==4.5.78", "pypdf==6.16.1"])
        self.assertEqual(len(warnings), 1, warnings)
        self.assertIn("ccxt (4.5.70, 4.5.78)", warnings[0])
        self.assertIn("requirements.lock fija ccxt==4.5.78", warnings[0])
        self.assertIn("TL-FW-314", warnings[0])

    def test_pin_distinto_del_lock_avisa(self):
        warnings = self.check(["ccxt==4.5.78", "pypdf==6.14.2"])
        self.assertEqual(len(warnings), 1, warnings)
        self.assertIn("pypdf==6.14.2 (lock 6.16.1)", warnings[0])

    def test_pyproject_sin_requirements_txt_avisa_de_lo_que_falta(self):
        warnings = self.check(["uvicorn==0.30"], files={
            "pyproject.toml": '[project]\nname = "x"\ndependencies = ["fastapi>=0.110", "uvicorn"]\n'})
        self.assertEqual(warnings, ["pyproject.toml declara dependencias no reflejadas en "
                                    "03_project_state.json.dependencies: fastapi"])

    def test_dependencias_de_poetry_sin_python(self):
        warnings = self.check([], files={"pyproject.toml": '[tool.poetry.dependencies]\npython = "^3.12"\nHTTPX = "*"\n'})
        self.assertEqual(len(warnings), 1, warnings)
        self.assertTrue(warnings[0].endswith(": httpx"), warnings)

    def test_requirements_txt_manda_sobre_pyproject(self):
        warnings = self.check(["ccxt==4.5.78"], files={"pyproject.toml": PYPROJECT, "requirements.txt": "ccxt==4.5.78\n"})
        self.assertEqual(warnings, [])

    def test_estado_alineado_y_extras_de_desarrollo_fuera_no_avisa(self):
        self.assertEqual(self.check(["ccxt==4.5.78", "pypdf==6.16.1"]), [])

    def test_misma_version_repetida_no_avisa(self):
        self.assertEqual(self.check(["playwright==1.61.0 (dev, aprobada)", "playwright==1.61.0",
                                     "ccxt==4.5.78", "pypdf==6.16.1"], files={"pyproject.toml": PYPROJECT}), [])

    def test_dependencies_que_no_es_una_lista_no_rompe(self):
        for value in (True, 1, "ccxt==1"):
            self.assertEqual(self.check(value, files={}), [], value)

    def test_plantilla_y_pyproject_ilegible_no_avisan(self):
        self.assertEqual(self.check(["ccxt==4.5.70", "ccxt==4.5.78"], mode="template"), [])
        self.assertEqual(self.check(["a==1"], files={"pyproject.toml": "esto no es toml [[["}), [])


PRODUCT_TEST = ('from pathlib import Path\nimport json\n\ndef test_x():\n'
                '    state = json.loads(Path("_project/03_project_state.json").read_text(encoding="utf-8"))\n'
                '    assert state["module_statuses"]["M1"] == "in_progress"\n')


class TestsQueLeenElEstadoTests(DoctorRootCase):
    """L6.2, PA-FW-007: tests de producto versionados que leen el estado canónico real."""

    def check(self, files, mode="initialized", use_git=True):
        for rel, text in files.items():
            self.write(rel, text)
        if use_git:
            git(self.root, "init", "-q")
            git(self.root, "add", "-A")
        return self.run_in_root(doctor.validate_product_tests_read_state, mode)

    def test_test_de_producto_que_lee_el_estado_avisa(self):
        warnings = self.check({"tests/test_ui03_review_contract.py": PRODUCT_TEST})
        self.assertEqual(len(warnings), 1, warnings)
        self.assertIn("tests/test_ui03_review_contract.py", warnings[0])
        self.assertIn("PA-FW-007", warnings[0])

    def test_test_js_con_supertest_que_lee_el_estado_avisa(self):
        warnings = self.check({"test/api.test.js": "const agent = request.agent(app);\n"
                               "const s = JSON.parse(fs.readFileSync('_project/03_project_state.json'));\n"})
        self.assertTrue(any("test/api.test.js" in w for w in warnings), warnings)

    def test_lectura_con_constante_de_raiz_avisa(self):
        warnings = self.check({"tests/test_y.py": 'ROOT = Path(__file__).parents[1]\n'
                               'S = json.loads((ROOT / "_project" / "03_project_state.json").read_text())\n'})
        self.assertTrue(any("tests/test_y.py" in w for w in warnings), warnings)

    def test_lo_que_no_es_una_lectura_de_producto_no_avisa(self):
        self.assertEqual(self.check({
            "tests/test_framework_doctor.py": 'DOCTOR = ROOT / ".agent" / "scripts" / "framework_doctor.py"\n'
                                              'state_path = ROOT / "_project" / "03_project_state.json"\n',
            "tests/test_z.py": 'def test_z(tmp_path):\n    p = tmp_path / "_project" / "03_project_state.json"\n',
            "tests/test_d.py": '"""Lee `_project/03_project_state.json` solo en la doc."""\n'
                               '# Path("_project/03_project_state.json") comentado\n',
            "scripts/report.py": 'Path("_project/03_project_state.json").read_text()\n',
        }), [])

    def test_plantilla_no_avisa(self):
        self.assertEqual(self.check({"tests/test_ui03_review_contract.py": PRODUCT_TEST}, mode="template"), [])

    def test_sin_git_no_avisa_ni_rompe(self):
        self.assertEqual(self.check({"tests/test_ui03_review_contract.py": PRODUCT_TEST}, use_git=False), [])


BRIEF_STATE = {
    "project": "Brief", "framework_mode": "initialized",
    "framework_version": state_core.FRAMEWORK_VERSION, "current_phase": "phase_01",
    "current_step": "s", "last_completed_step": "",
    "stack": {"backend": "Python", "frontend": "-", "database": "-", "auth": "-"},
    "dependencies": [], "architecture_constraints": [],
    "modules": {"completed": ["m1"], "in_progress": [], "pending": []},
    "module_statuses": {"m1": "completed"},
    "work_tiers": {}, "module_specs": {}, "closure_rejections": [],
    "active_mcps": [], "contracts": {"public_interfaces": {}},
    "_refs": {}, "_bugs": [], "_clarifications": [], "_decisions": [],
}
PROJECTION_PATHS = {
    "STATE_FILE_PATH": "03_project_state.json", "CORE_FILE_PATH": "core.md", "BUGS_FILE_PATH": "06_bug_fixes.md",
    "CLARIFICATIONS_FILE_PATH": "07_pending_clarifications.md", "DECISIONS_FILE_PATH": "03_arch_decisions.md",
    "SESSION_BRIEF_PATH": "00_session_brief.json",
}


class BriefTests(TmpCase):
    """L6.3, FW-296: `00_session_brief.json` solo se reescribe si cambia algo más que `generated_at`."""

    def setUp(self):
        super().setUp()
        for attr in ("WORKSPACE_ROOT", *PROJECTION_PATHS):
            self.addCleanup(setattr, state_core, attr, getattr(state_core, attr))
        state_core.WORKSPACE_ROOT = self.root
        for attr, name in PROJECTION_PATHS.items():
            setattr(state_core, attr, self.root / "_project" / name)
        self.write(".agent/workflows/manifest.json", json.dumps({"commands": []}))
        self.write("_project/03_project_state.json", json.dumps(BRIEF_STATE))
        self.brief = state_core.SESSION_BRIEF_PATH

    def render(self, **changes):
        import projections
        projections.render({**json.loads(json.dumps(BRIEF_STATE)), **changes})
        return self.brief.read_bytes(), self.brief.stat().st_mtime_ns

    def test_sin_cambios_no_se_reescribe(self):
        first = self.render()
        os.utime(self.brief, ns=(10**9, 10**9))
        self.assertEqual(self.render(), (first[0], 10**9))

    def test_si_cambia_el_estado_se_reescribe_con_el_valor_nuevo(self):
        self.render()
        os.utime(self.brief, ns=(10**9, 10**9))
        second, mtime = self.render(current_phase="phase_02")
        self.assertEqual(json.loads(second)["current_phase"], "phase_02")
        self.assertNotEqual(mtime, 10**9)

    def test_brief_roto_o_sin_module_counts_se_regenera(self):
        self.render()
        for broken in ("{roto", "[]", json.dumps({k: v for k, v in json.loads(self.brief.read_text()).items()
                                                  if k != "module_counts"})):
            self.brief.write_text(broken, encoding="utf-8")
            self.assertIn("module_counts", json.loads(self.render()[0]), broken)

    def test_una_mutacion_que_no_toca_el_brief_no_lo_reescribe(self):
        manager = state_core.StateManager()
        manager.mutate("append_to_list_field", {"field": "architecture_constraints", "value": "sin red"})
        before = self.brief.read_bytes()
        os.utime(self.brief, ns=(10**9, 10**9))
        manager.mutate("append_to_list_field", {"field": "dependencies", "value": "httpx==0.27"})
        self.assertEqual((self.brief.read_bytes(), self.brief.stat().st_mtime_ns), (before, 10**9))
        self.assertIn("httpx==0.27", json.loads(state_core.STATE_FILE_PATH.read_text())["dependencies"])


KIB, MIB = 1024, 1024 * 1024


class PesoTests(DoctorRootCase):
    """L6.4, N2 (FW-361): avisos de peso de `_project/`; umbrales D1 a D3 del builder."""

    def check(self, files, mode="initialized", use_git=True):
        for rel, content in files.items():
            self.write(rel, content)
        if use_git:
            git(self.root, "init", "-q")
            git(self.root, "add", "-A")
        return self.run_in_root(doctor.validate_project_weight, mode)

    def test_umbrales_decididos(self):
        self.assertEqual((doctor.PROJECT_STATE_WARN_BYTES, doctor.ACTIVE_SCOPE_WARN_BYTES,
                          doctor.SESSION_JOURNAL_WARN_BYTES, doctor.PROJECT_FILE_WARN_BYTES,
                          doctor.PROJECT_DUPLICATE_MIN_BYTES), (6 * MIB, 32 * KIB, 60 * KIB, MIB, 64 * KIB))

    def test_estado_scope_y_diario_por_encima_del_umbral_avisan(self):
        warnings = self.check({"_project/03_project_state.json": 6 * MIB + 1,
                               "_project/04_active_scope.md": 32 * KIB + 1,
                               "_project/13_session_journal.md": 60 * KIB + 1})
        self.assertEqual(len(warnings), 3, warnings)
        self.assertIn("pesa 6.0 MiB (más de 6.0 MiB)", warnings[0])
        self.assertIn("migrate_test_identities.py --write", warnings[0])
        self.assertIn("pesa 32.0 KiB (más de 32.0 KiB)", warnings[1])
        self.assertIn("04_scope_archive.md", warnings[1])
        self.assertIn("13_session_journal.md", warnings[2])

    def test_justo_en_el_umbral_no_avisa(self):
        self.assertEqual(self.check({"_project/03_project_state.json": 6 * MIB, "_project/04_active_scope.md": 32 * KIB,
                                     "_project/13_session_journal.md": 60 * KIB}), [])

    def test_fichero_versionado_pesado_avisa_y_propone_backups(self):
        warnings = self.check({"_project/02_technical_plan/c8a/integration.patch": MIB + 1,
                               "_project/02_technical_plan/c8a/evidence.zip": os.urandom(2 * MIB)})
        self.assertEqual(len(warnings), 1, warnings)
        self.assertIn("2 fichero(s) de más de 1 MiB", warnings[0])
        self.assertLess(warnings[0].index("evidence.zip"), warnings[0].index("integration.patch"))
        self.assertIn("backups/", warnings[0])

    def test_copias_identicas_versionadas_avisan(self):
        blob = os.urandom(64 * KIB)
        warnings = self.check({"_project/a/sources.zip": blob, "_project/a/intento1/sources.zip": blob})
        self.assertEqual(warnings, ["_project/ versiona copias idénticas: _project/a/intento1/sources.zip = "
                                    "_project/a/sources.zip. Deja una y cita su huella"])

    def test_copia_de_una_proyeccion_cuenta_como_copia(self):
        blob = os.urandom(100 * KIB)
        warnings = self.check({"_project/06_bug_fixes.md": blob, "_project/archivo/06_bug_fixes_phase_01.md": blob})
        self.assertEqual(len(warnings), 1, warnings)
        self.assertIn("copias idénticas: _project/06_bug_fixes.md = _project/archivo/06_bug_fixes_phase_01.md", warnings[0])

    def test_git_add_n_y_conflictos_no_dan_copias_ni_repeticiones(self):
        self.write("_project/a.bin", os.urandom(100 * KIB))
        self.write("_project/b.bin", os.urandom(100 * KIB))
        git(self.root, "init", "-q", "-b", "main")
        git(self.root, "add", "-N", "_project")
        self.assertEqual(self.run_in_root(doctor.validate_project_weight, "initialized"), [])
        commit = ("-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qam")
        git(self.root, *commit, "a")
        git(self.root, "checkout", "-q", "-b", "otra")
        self.write("_project/x.zip", os.urandom(MIB + 1))
        git(self.root, "add", "-A")
        git(self.root, *commit, "b")
        git(self.root, "checkout", "-q", "main")
        self.write("_project/x.zip", os.urandom(MIB + 2))
        git(self.root, "add", "-A")
        git(self.root, *commit, "c")
        subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", "merge", "-q", "otra"], cwd=self.root,
                       capture_output=True, env={k: v for k, v in os.environ.items() if not k.startswith("GIT_")})
        self.assertIn(" 2\t_project/x.zip", git(self.root, "ls-files", "-s"))
        self.assertEqual(self.run_in_root(doctor.validate_project_weight, "initialized"), [])

    def test_log_proyecciones_copias_pequenas_y_vacias_no_avisan(self):
        small = os.urandom(64 * KIB - 1)
        self.assertEqual(self.check({
            "_project/state_log.jsonl": 2 * MIB, "_project/03_arch_decisions.md": os.urandom(MIB + 1),
            "_project/core.md": os.urandom(MIB + 1), "_project/a.md": small, "_project/b.md": small,
            "_project/.gitkeep": 0, "_project/x.md": 0,
        }), [])

    def test_plantilla_no_avisa(self):
        self.assertEqual(self.check({"_project/03_project_state.json": 6 * MIB + 1}, mode="template"), [])

    def test_sin_git_avisa_del_estado_y_no_de_versionados(self):
        warnings = self.check({"_project/03_project_state.json": 6 * MIB + 1, "_project/x.patch": MIB + 1},
                              use_git=False)
        self.assertEqual(len(warnings), 1, warnings)
        self.assertIn("03_project_state.json", warnings[0])

    def test_gitignore_base_ignora_backups(self):
        lines = (ROOT / ".agent" / "templates" / "gitignore.base").read_text(encoding="utf-8").splitlines()
        self.assertIn("backups/", lines)


if __name__ == "__main__":
    unittest.main()
