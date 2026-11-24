"""L9 (5.2.3): FW-282, FW-283 y FW-284, aplazadas de 5.2.2."""
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / ".agent" / "scripts"))

import propagate_framework as propagate


def git(path, *args, check=True):
    return subprocess.run(
        ["git", "-C", str(path), "-c", "user.name=t", "-c", "user.email=t@t", *args],
        capture_output=True, text=True, check=check,
    )


def user_line(text, *, kind="user"):
    stamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - 120))
    return json.dumps({"type": kind, "timestamp": stamp, "message": {"role": kind, "content": text}}) + "\n"


class Gycd282Tests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        base = Path(self.tmp.name)
        self.project = base / "proyecto"
        self.project.mkdir()
        git(self.project, "init", "-q")
        (self.project / "a.txt").write_text("a\n")
        git(self.project, "add", "-A")
        git(self.project, "commit", "-q", "-m", "init")
        self.projects_dir = base / "claude_projects"
        self.slug_dir = self.projects_dir / propagate.claude_project_slug(self.project)
        self.slug_dir.mkdir(parents=True)

    def transcript(self, *lines):
        path = self.slug_dir / "s.jsonl"
        path.write_text("".join(lines), encoding="utf-8")
        mtime = time.time() - 120
        os.utime(path, (mtime, mtime))

    def check(self):
        return propagate.check_open_claude_session(self.project, claude_projects_dir=self.projects_dir)

    def test_gycd_con_arbol_limpio_cuenta_como_cerrada(self):
        self.transcript(user_line("trabajo"), user_line("<command-name>/gycd</command-name>"), user_line("haz push"))
        self.assertEqual(self.check(), (False, ""))

    def test_gycd_escrito_como_texto_tambien(self):
        self.transcript(user_line("/gycd cierra"))
        self.assertEqual(self.check(), (False, ""))

    def test_gycd_con_arbol_sucio_sigue_bloqueando_y_lo_dice(self):
        (self.project / "b.txt").write_text("b\n")
        self.transcript(user_line("/gycd"))
        has_open, reason = self.check()
        self.assertTrue(has_open)
        self.assertIn("sin commitear", reason)

    def test_sin_gycd_bloquea_con_el_mensaje_nuevo(self):
        self.transcript(user_line("sigo trabajando"))
        has_open, reason = self.check()
        self.assertTrue(has_open)
        self.assertIn("sin /gycd en la sesión; ciérrala con /gycd o usa --allow-open-session", reason)

    def test_mencionar_gycd_en_una_respuesta_no_es_invocarlo(self):
        self.transcript(user_line("ejecuta /gycd cuando acabes", kind="assistant"), user_line("¿qué hace gycd?"))
        self.assertTrue(self.check()[0])

    def test_transcripcion_corrupta_es_conservadora(self):
        self.transcript("no es json gycd\n")
        self.assertTrue(self.check()[0])


class Hook283Tests(unittest.TestCase):
    def test_un_worktree_sin_venv_usa_el_del_repo_principal(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        main = Path(tmp) / "principal"
        main.mkdir()
        git(main, "init", "-q")
        (main / "x").write_text("x\n")
        git(main, "add", "-A")
        git(main, "commit", "-q", "-m", "init")
        witness = main / "venv_witness"
        venvbin = main / ".venv" / "bin"
        venvbin.mkdir(parents=True)
        stub = venvbin / "python"
        stub.write_text(f'#!/usr/bin/env bash\necho used >> "{witness}"\nexec python3 "$@"\n', encoding="utf-8")
        stub.chmod(stub.stat().st_mode | stat.S_IEXEC)
        worktree = Path(tmp) / "worktree"
        git(main, "worktree", "add", "-q", str(worktree), "-b", "rama")
        scripts = worktree / ".agent" / "scripts"
        scripts.mkdir(parents=True)
        shutil.copy(ROOT / ".agent" / "scripts" / "pre_commit.sh", scripts / "pre_commit.sh")
        (scripts / "framework_doctor.py").write_text("import sys\nsys.exit(0)\n")
        (scripts / "validate_transition.py").write_text("import sys\nsys.exit(0)\n")
        result = subprocess.run(
            ["bash", ".agent/scripts/pre_commit.sh"], cwd=worktree, capture_output=True, text=True,
            env={**os.environ, "FRAMEWORK_PYTHON": ""},
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue(witness.exists(), "el hook debe usar el .venv del repo principal")

    def test_sin_venv_en_ninguno_cae_a_python3(self):
        text = (ROOT / ".agent" / "scripts" / "pre_commit.sh").read_text(encoding="utf-8")
        self.assertIn('PYTHON_BIN="python3"', text)

    def test_el_propagador_sugiere_framework_python_ante_modulenotfound(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        project = Path(tmp)
        git(project, "init", "-q")
        # `commit_upgrade` llama a git sin -c: en un entorno sin identidad global (CI, clon limpio) hace falta la local.
        git(project, "config", "user.name", "t")
        git(project, "config", "user.email", "t@t")
        hook = project / ".git" / "hooks" / "pre-commit"
        hook.write_text("#!/usr/bin/env bash\necho \"ModuleNotFoundError: No module named 'pydantic'\"\nexit 1\n")
        hook.chmod(hook.stat().st_mode | stat.S_IEXEC)
        (project / "f.txt").write_text("f\n")
        ok, motivo = propagate.commit_upgrade(project, {"declared": "5.2.2", "target": "5.2.3"})
        self.assertFalse(ok)
        self.assertIn("ModuleNotFoundError", motivo)
        self.assertIn("FRAMEWORK_PYTHON", motivo)


class Copy284Tests(unittest.TestCase):
    def run_copy(self, mode):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        copy = Path(tmp) / "proyecto"
        (copy / ".agent").mkdir(parents=True)
        shutil.copytree(ROOT / ".agent" / "scripts", copy / ".agent" / "scripts", ignore=shutil.ignore_patterns("__pycache__"))
        (copy / "_project").mkdir()
        if mode is not None:
            (copy / "_project" / "03_project_state.json").write_text(json.dumps({"framework_mode": mode}))
        return subprocess.run(
            [sys.executable, "-B", ".agent/scripts/propagate_framework.py", "--search-root", str(Path(tmp) / "vacio")],
            cwd=copy, capture_output=True, text=True,
        )

    def test_la_copia_de_un_adoptante_sale_con_el_mensaje_claro(self):
        result = self.run_copy("initialized")
        self.assertEqual(result.returncode, 2)
        self.assertIn("ejecútalo desde la plantilla", result.stderr)

    def test_la_plantilla_sigue_funcionando(self):
        result = self.run_copy("template")
        self.assertNotIn("ejecútalo desde la plantilla", result.stderr)

    def test_estado_ilegible_no_bloquea(self):
        result = self.run_copy(None)
        self.assertNotIn("ejecútalo desde la plantilla", result.stderr)


if __name__ == "__main__":
    unittest.main()
