#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Runner del hook pre-commit (pre_commit.sh).

Cubre dos fallos de campo que entrenan `--no-verify` (y con ello desarman los
gates de doctor/tests/transición):

- FW-034: el hook usaba `python3` del sistema, ignorando el `.venv` del
  proyecto → la suite falla por ModuleNotFoundError y bloquea todo commit.
  El hook debe resolver `.venv/bin/python` (o `$FRAMEWORK_PYTHON`) cuando exista.
- FW-038: en una adopción nueva sin tests (o `tests/` vacío) el hook fallaba
  (ImportError / exit 5 de unittest) y bloqueaba el primer commit. El hook debe
  tratar "sin tests que ejecutar" como éxito, no como fallo.

El hook se ejecuta en un sandbox con stubs (doctor que sale 0); no corre el
doctor real ni la suite del framework, así que no hay recursión.
"""
import os
import shutil
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REAL_HOOK = ROOT / ".agent" / "scripts" / "pre_commit.sh"


def _exec(path: Path, body: str) -> None:
    path.write_text(body, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)


class PreCommitHookTests(unittest.TestCase):
    def _sandbox(self, *, tests_dir, venv=False):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        sb = Path(tmp)
        scripts = sb / ".agent" / "scripts"
        scripts.mkdir(parents=True)
        shutil.copy(REAL_HOOK, scripts / "pre_commit.sh")
        # Stubs: doctor and transition validator succeed without the real framework.
        _exec(scripts / "framework_doctor.py", "import sys\nsys.exit(0)\n")
        _exec(scripts / "validate_transition.py", "import sys\nsys.exit(0)\n")
        if tests_dir == "empty":
            (sb / "tests").mkdir()
            (sb / "tests" / "__init__.py").write_text("")
        elif tests_dir == "passing":
            (sb / "tests").mkdir()
            (sb / "tests" / "__init__.py").write_text("")
            (sb / "tests" / "test_ok.py").write_text(
                "import unittest\n"
                "class T(unittest.TestCase):\n"
                "    def test_ok(self): self.assertTrue(True)\n"
            )
        # tests_dir == "absent": no tests/ directory at all
        if venv:
            venvbin = sb / ".venv" / "bin"
            venvbin.mkdir(parents=True)
            witness = sb / "venv_witness"
            _exec(
                venvbin / "python",
                f'#!/usr/bin/env bash\necho used >> "{witness}"\nexec python3 "$@"\n',
            )
        return sb

    def _run(self, sb):
        return subprocess.run(
            ["bash", ".agent/scripts/pre_commit.sh"],
            cwd=sb, capture_output=True, text=True, env={**os.environ, "FRAMEWORK_PYTHON": ""},
        )

    def test_missing_tests_dir_does_not_block_commit(self):
        res = self._run(self._sandbox(tests_dir="absent"))
        self.assertEqual(res.returncode, 0, f"FW-038: sin tests/ no debe bloquear.\n{res.stdout}\n{res.stderr}")

    def test_empty_tests_dir_does_not_block_commit(self):
        res = self._run(self._sandbox(tests_dir="empty"))
        self.assertEqual(res.returncode, 0, f"FW-038: tests/ vacío (exit 5) no debe bloquear.\n{res.stdout}\n{res.stderr}")

    def test_passing_tests_still_succeed(self):
        res = self._run(self._sandbox(tests_dir="passing"))
        self.assertEqual(res.returncode, 0, f"el camino feliz debe seguir pasando.\n{res.stdout}\n{res.stderr}")

    def test_uses_venv_python_when_present(self):
        sb = self._sandbox(tests_dir="passing", venv=True)
        self._run(sb)
        self.assertTrue(
            (sb / "venv_witness").exists(),
            "FW-034: el hook debe usar .venv/bin/python cuando existe",
        )

    # --- Cosecha 2026-07-09 (propagación 4.6.0 a Boton): el hook asumía
    # unittest y solo .venv/ — un proyecto pytest con venv/ quedaba bloqueado
    # (50 errores de colección de unittest sobre fixtures pytest).

    def _venv_stub(self, sb, dirname, *, has_pytest):
        venvbin = sb / dirname / "bin"
        venvbin.mkdir(parents=True)
        witness = sb / f"{dirname}_witness"
        runner_log = sb / "runner_log"
        # Stub que registra el runner usado y simula pytest presente/ausente.
        _exec(
            venvbin / "python",
            "#!/usr/bin/env bash\n"
            f'echo used >> "{witness}"\n'
            'args="$*"\n'
            'if [[ "$args" == *"import pytest"* ]]; then\n'
            f'    exit {0 if has_pytest else 1}\n'
            "fi\n"
            'if [[ "$args" == *"-m pytest"* ]]; then\n'
            f'    echo pytest >> "{runner_log}"\n'
            "    exit 0\n"
            "fi\n"
            'if [[ "$args" == *"-m unittest"* ]]; then\n'
            f'    echo unittest >> "{runner_log}"\n'
            "    exit 0\n"
            "fi\n"
            'exec python3 "$@"\n',
        )
        return witness, runner_log

    def test_uses_plain_venv_dir_when_present(self):
        sb = self._sandbox(tests_dir="passing")
        witness, _ = self._venv_stub(sb, "venv", has_pytest=False)
        self._run(sb)
        self.assertTrue(
            witness.exists(),
            "el hook debe resolver venv/bin/python cuando no hay .venv/ (caso Boton)",
        )

    def test_pytest_project_runs_pytest_not_unittest(self):
        sb = self._sandbox(tests_dir="passing")
        (sb / "tests" / "conftest.py").write_text("")
        _, runner_log = self._venv_stub(sb, ".venv", has_pytest=True)
        res = self._run(sb)
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        self.assertIn("pytest", runner_log.read_text(encoding="utf-8"))
        self.assertNotIn("unittest", runner_log.read_text(encoding="utf-8"))

    def test_conftest_without_pytest_blocks_instead_of_falling_back_to_unittest(self):
        # L9 (decisión A del builder): con señal de pytest y pytest no importable, el
        # fallback silencioso a unittest daba un falso verde; ahora el hook bloquea.
        sb = self._sandbox(tests_dir="passing")
        (sb / "tests" / "conftest.py").write_text("")
        _, runner_log = self._venv_stub(sb, ".venv", has_pytest=False)
        res = self._run(sb)
        self.assertNotEqual(res.returncode, 0, res.stdout + res.stderr)
        self.assertIn("pytest", res.stdout + res.stderr)
        self.assertFalse(runner_log.exists(), "no debe correr ningún runner")


class PreCommitInstallTests(unittest.TestCase):
    """FW-207 (cierra FW-107): `pre_commit.sh install` debe funcionar tanto en un
    repo normal como dentro de un `git worktree`, donde `$REPO_ROOT/.git` es un
    archivo puntero y la ruta literal `.git/hooks/pre-commit` no resuelve
    (`cp: .git/hooks/pre-commit: Not a directory`). El hook vive en el common dir,
    compartido por todos los worktrees."""

    # Config global/sistema neutralizada: un `core.hooksPath` del entorno
    # redirigiría el destino y volvería el test no determinista.
    _ENV = {**os.environ, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull}

    def _git(self, *args, cwd):
        return subprocess.run(
            ["git", *args], cwd=cwd, capture_output=True, text=True,
            env=self._ENV, check=True,
        )

    def _repo_with_hook(self) -> Path:
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        main = Path(tmp) / "main"
        scripts = main / ".agent" / "scripts"
        scripts.mkdir(parents=True)
        shutil.copy(REAL_HOOK, scripts / "pre_commit.sh")
        self._git("init", "-q", cwd=main)
        self._git("config", "user.email", "t@t", cwd=main)
        self._git("config", "user.name", "t", cwd=main)
        self._git("add", "-A", cwd=main)
        self._git("commit", "-qm", "init", cwd=main)
        return main

    def _install(self, cwd):
        return subprocess.run(
            ["bash", ".agent/scripts/pre_commit.sh", "install"],
            cwd=cwd, capture_output=True, text=True, env=self._ENV,
        )

    def _resolved_hook(self, cwd: Path) -> Path:
        rel = self._git("rev-parse", "--git-path", "hooks/pre-commit", cwd=cwd).stdout.strip()
        return Path(rel) if os.path.isabs(rel) else cwd / rel

    def test_install_in_plain_repo(self):
        main = self._repo_with_hook()
        res = self._install(main)
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        hook = main / ".git" / "hooks" / "pre-commit"
        self.assertTrue(hook.exists(), f"{res.stdout}\n{res.stderr}")
        self.assertTrue(os.access(hook, os.X_OK))
        # Cambiado en 5.3.1 L2.3 (TL-FW-320): el hook es el lanzador, no una copia.
        launcher = subprocess.run(
            ["bash", ".agent/scripts/pre_commit.sh", "print-launcher"],
            cwd=main, capture_output=True, text=True, env=self._ENV, check=True,
        ).stdout
        self.assertEqual(hook.read_text(encoding="utf-8"), launcher)

    def test_install_in_worktree(self):
        main = self._repo_with_hook()
        wt = main.parent / "wt"
        self._git("worktree", "add", "-q", str(wt), cwd=main)
        res = self._install(wt)
        self.assertEqual(
            res.returncode, 0,
            f"FW-207: install desde un worktree no debe fallar.\n{res.stdout}\n{res.stderr}",
        )
        hook = self._resolved_hook(wt)
        self.assertTrue(hook.exists(), f"hook no encontrado en {hook}\n{res.stdout}\n{res.stderr}")
        self.assertTrue(os.access(hook, os.X_OK))


if __name__ == "__main__":
    unittest.main()
