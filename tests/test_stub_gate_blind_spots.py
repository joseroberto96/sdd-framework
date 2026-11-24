#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""STUB GATE: el CLI no puede fallar con código limpio ni el gate quedar ciego
(FW-230, FW-231 — auditoría de bugs 2026-09-14).

- FW-230: `validate_no_stubs.py` reventaba con `NameError: targets` en el camino
  de éxito de ambos modos, así que el paso 3.8.b de `/cierre-modulo` fallaba
  siempre sobre código limpio.
- FW-231: el cierre real (`stub_problems`) no escaneaba nada cuando (b) una
  carpeta ANCESTRO de la raíz se llama como una exclusión —un worktree bajo
  `.claude/worktrees/`—, (c) el código nuevo vive en una carpeta sin seguimiento
  que `git status` colapsa, o (d) el nombre lleva caracteres no ASCII que git
  escapa en octal. En los tres casos el gate decía OK sin mirar.
"""
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / ".agent" / "scripts"
sys.path.insert(0, str(SCRIPTS))
import maintenance  # noqa: E402
import validate_no_stubs as stubs  # noqa: E402

FAIL_OPEN = (
    "def leer(fila):\n"
    "    try:\n"
    "        return json.loads(fila)\n"
    "    except Exception:\n"
    "        return {}\n"
)
LIMPIO = "def sumar(a, b):\n    return a + b\n"


def _git(root: Path, *args: str) -> None:
    env = {"GIT_CONFIG_GLOBAL": "/dev/null", "PATH": os.environ.get("PATH", "/usr/bin:/bin")}
    subprocess.run(
        ["git", "-C", str(root), "-c", "user.email=t@t", "-c", "user.name=t", *args],
        check=True, env=env, capture_output=True,
    )


def _repo(root: Path) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    _git(root, "init", "-q")
    (root / "README.md").write_text("# producto\n", encoding="utf-8")
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "base")
    return root


def _write(root: Path, rel: str, body: str) -> Path:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    return path


class StubGateCliTests(unittest.TestCase):
    """FW-230 — el CLI es lo que ejecuta el paso 3.8.b de /cierre-modulo."""

    def _proyecto_con_script(self, tmp: Path) -> Path:
        project = _repo(tmp / "producto")
        scripts = project / ".agent" / "scripts"
        scripts.mkdir(parents=True)
        for name in ("validate_no_stubs.py", "validate_acceptance.py"):
            shutil.copy2(SCRIPTS / name, scripts / name)
        return project

    def _run(self, project: Path, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, "-B", ".agent/scripts/validate_no_stubs.py", *args],
            cwd=project, capture_output=True, text=True, timeout=60,
        )

    def test_modo_paths_con_codigo_limpio_sale_en_ok(self):
        with tempfile.TemporaryDirectory() as d:
            project = self._proyecto_con_script(Path(d))
            _write(project, "src/calculo.py", LIMPIO)
            result = self._run(project, "--paths", "src/calculo.py")
            self.assertEqual(0, result.returncode, result.stdout + result.stderr)
            self.assertIn("STUB GATE: OK (1 ficheros fuente revisados)", result.stdout)

    def test_modo_modulo_con_codigo_limpio_sale_en_ok(self):
        with tempfile.TemporaryDirectory() as d:
            project = self._proyecto_con_script(Path(d))
            _write(project, "src/calculo.py", LIMPIO)
            result = self._run(project, "M1")
            self.assertEqual(0, result.returncode, result.stdout + result.stderr)
            self.assertIn("STUB GATE: OK (1 ficheros fuente revisados)", result.stdout)

    def test_modo_modulo_sin_ficheros_lo_declara(self):
        """Principio FW-156: un OK que no miró nada tiene que decirlo."""
        with tempfile.TemporaryDirectory() as d:
            project = self._proyecto_con_script(Path(d))
            _git(project, "add", "-A")
            _git(project, "commit", "-qm", "scripts")
            result = self._run(project, "M1")
            self.assertEqual(0, result.returncode, result.stdout + result.stderr)
            self.assertIn("0 ficheros fuente revisados", result.stdout)
            self.assertIn("nada que escanear", result.stdout)

    def test_hallazgo_sigue_saliendo_en_fail(self):
        with tempfile.TemporaryDirectory() as d:
            project = self._proyecto_con_script(Path(d))
            _write(project, "src/lector.py", FAIL_OPEN)
            result = self._run(project, "--paths", "src/lector.py")
            self.assertEqual(1, result.returncode, result.stdout + result.stderr)
            self.assertIn("STUB GATE: FAIL", result.stdout)


class StubGateBlindSpotTests(unittest.TestCase):
    """FW-231 — `module_findings` es lo que usa el cierre real vía `stub_problems`."""

    def _hallazgos(self, root: Path) -> list[str]:
        return stubs.module_findings("M1", root)

    def test_worktree_bajo_claude_worktrees_se_escanea(self):
        with tempfile.TemporaryDirectory() as d:
            root = _repo(Path(d) / "repo" / ".claude" / "worktrees" / "mod-a")
            _write(root, "src/lector.py", FAIL_OPEN)
            self.assertEqual(1, len(self._hallazgos(root)))

    def test_carpeta_ancestro_con_nombre_excluido_no_ciega_el_gate(self):
        with tempfile.TemporaryDirectory() as d:
            root = _repo(Path(d) / "build" / "app")
            _write(root, "src/lector.py", FAIL_OPEN)
            self.assertEqual(1, len(self._hallazgos(root)))

    def test_exclusiones_dentro_del_proyecto_se_mantienen(self):
        with tempfile.TemporaryDirectory() as d:
            root = _repo(Path(d) / "app")
            _write(root, "tests/test_lector.py", FAIL_OPEN)
            _write(root, "docs/ejemplo.py", FAIL_OPEN)
            self.assertEqual([], self._hallazgos(root))

    def test_ficheros_en_carpeta_nueva_sin_seguimiento_se_escanean(self):
        with tempfile.TemporaryDirectory() as d:
            root = _repo(Path(d) / "app")
            for name in ("uno", "dos", "tres"):
                _write(root, f"src/nuevo_modulo/{name}.py", FAIL_OPEN)
            self.assertEqual(3, len(self._hallazgos(root)))

    def test_nombres_con_acentos_y_espacios_se_escanean(self):
        with tempfile.TemporaryDirectory() as d:
            root = _repo(Path(d) / "app")
            _write(root, "src/diseño.py", FAIL_OPEN)
            _write(root, "src/con espacio.py", FAIL_OPEN)
            self.assertEqual(2, len(self._hallazgos(root)))

    def test_renombrado_se_escanea_en_su_ruta_nueva(self):
        with tempfile.TemporaryDirectory() as d:
            root = _repo(Path(d) / "app")
            _write(root, "src/viejo.py", LIMPIO)
            _git(root, "add", "-A")
            _git(root, "commit", "-qm", "viejo")
            _git(root, "mv", "src/viejo.py", "src/nuevo.py")
            _write(root, "src/nuevo.py", LIMPIO + "\n" + FAIL_OPEN)
            _git(root, "add", "-A")
            hallazgos = self._hallazgos(root)
            self.assertEqual(1, len(hallazgos), hallazgos)
            self.assertIn("nuevo.py", hallazgos[0])


class MaintenanceChangedPathsTests(unittest.TestCase):
    """FW-231 (d) — la escalada y el STUB GATE de mantenimiento leen el mismo diff."""

    def test_rutas_no_ascii_llegan_sin_escapar(self):
        with tempfile.TemporaryDirectory() as d:
            root = _repo(Path(d) / "app")
            _write(root, "migraciones/0002_añadir_campo.sql", "alter table x add y int;\n")
            self.assertEqual(["migraciones/0002_añadir_campo.sql"], maintenance.changed_paths(root))

    def test_renombrado_devuelve_la_ruta_nueva(self):
        with tempfile.TemporaryDirectory() as d:
            root = _repo(Path(d) / "app")
            _git(root, "mv", "README.md", "LEEME.md")
            self.assertEqual(["LEEME.md"], maintenance.changed_paths(root))


if __name__ == "__main__":
    unittest.main()
