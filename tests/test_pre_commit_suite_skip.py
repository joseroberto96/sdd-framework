#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Suite selectiva en pre-commit (BOTÓN FW-085, spec S2).

Un commit del comando `/gycd` (cierre de sesión) que solo toca artefactos de
sesión (`_project/`, `_feedback/`, `.agent/`) no ejercita código de
producto — pagar la suite completa en cada uno de esos commits es coste sin
señal. Este test cubre la regla determinista sobre los paths staged:

- (a) commit con SOLO archivos bajo `_project/` → el runner de tests NO se
  invoca; aparece el mensaje de omisión.
- (b) commit con un `.py` de producto fuera de esas carpetas → el runner de
  tests SÍ se invoca (comportamiento actual sin cambios).
- (c) el doctor y el transition gate se invocan en AMBOS casos.

Sigue el patrón EXACTO de `tests/test_pre_commit_hook.py`: sandbox efímero
con `tempfile.mkdtemp()`, stubs para doctor/validate_transition que salen 0,
y el script real `.agent/scripts/pre_commit.sh` ejecutado vía subprocess con
`cwd` apuntando al sandbox y un repo git real inicializado ahí.
"""
import json
import os
import shutil
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REAL_HOOK = ROOT / ".agent" / "scripts" / "pre_commit.sh"

# Este test suite corre `git` de verdad dentro de un repo sandbox. Si este
# proceso es a su vez hijo de un hook de git real (p.ej. cuando el propio
# pre_commit.sh del repo ejecuta la suite completa, que incluye este
# archivo), el entorno trae GIT_DIR/GIT_INDEX_FILE/etc. apuntando al repo
# REAL — y sin limpiarlos, "git init/add/commit" dentro del sandbox
# terminaría mutando el repo real en vez del sandbox. Se limpia SIEMPRE,
# nested o no, para que el sandbox quede genuinamente aislado.
_CLEAN_ENV = {
    k: v for k, v in os.environ.items() if not k.startswith("GIT_")
}


def _exec(path: Path, body: str) -> None:
    path.write_text(body, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)


def _git(sb, *args):
    return subprocess.run(
        ["git", *args],
        cwd=sb,
        capture_output=True,
        text=True,
        check=True,
        env=_CLEAN_ENV,
    )


class PreCommitSuiteSkipTests(unittest.TestCase):
    def _sandbox(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        sb = Path(tmp)
        scripts = sb / ".agent" / "scripts"
        scripts.mkdir(parents=True)
        shutil.copy(REAL_HOOK, scripts / "pre_commit.sh")

        doctor_witness = sb / "doctor_witness"
        transition_witness = sb / "transition_witness"
        runner_witness = sb / "runner_witness"

        # Stubs: doctor y transition validator escriben un testigo y salen 0
        # (patrón EXACTO de test_pre_commit_hook.py) — así podemos assertar
        # que se invocaron sin depender del framework real.
        _exec(
            scripts / "framework_doctor.py",
            "import sys, pathlib\n"
            f"pathlib.Path({str(doctor_witness)!r}).write_text('called')\n"
            "sys.exit(0)\n",
        )
        _exec(
            scripts / "validate_transition.py",
            "import sys, pathlib\n"
            f"pathlib.Path({str(transition_witness)!r}).write_text('called')\n"
            "sys.exit(0)\n",
        )

        # L7 (5.2): el gate de estado respaldado por la bitácora es otro gate
        # del hook; se stubea igual que doctor y transition (este test cubre la
        # omisión de la suite, no el gate; el gate tiene tests/test_state_delta_gate.py).
        _exec(scripts / "validate_state_delta.py", "import sys\nsys.exit(0)\n")

        # Runner "marcador": el propio test, al ejecutarse de verdad bajo
        # unittest discover, escribe un testigo — así detectamos si el
        # runner de tests fue invocado o no, sin fingir el intérprete.
        tests_dir = sb / "tests"
        tests_dir.mkdir()
        (tests_dir / "__init__.py").write_text("")
        (tests_dir / "test_marker.py").write_text(
            "import unittest, pathlib\n"
            "class T(unittest.TestCase):\n"
            "    def test_marker(self):\n"
            f"        pathlib.Path({str(runner_witness)!r}).write_text('called')\n"
            "        self.assertTrue(True)\n"
        )

        # _project/03_project_state.json committed en HEAD para que el
        # transition gate (que solo corre si el state file existe en HEAD Y
        # en el índice) tenga camino para ejecutarse en ambos escenarios.
        project_dir = sb / "_project"
        project_dir.mkdir()
        (project_dir / "03_project_state.json").write_text(
            json.dumps({"modules": [], "version": 1}, indent=2)
        )

        _git(sb, "init", "-q")
        _git(sb, "config", "user.email", "test@example.com")
        _git(sb, "config", "user.name", "Test")
        _git(sb, "add", "-A")
        _git(sb, "commit", "-q", "-m", "initial")

        return sb, doctor_witness, transition_witness, runner_witness

    def _stage_state_change(self, sb):
        # Modificación trivial y legal del state file — solo sirve para que
        # el bloque del transition gate tenga un diff staged que evaluar.
        state_path = sb / "_project" / "03_project_state.json"
        state_path.write_text(json.dumps({"modules": [], "version": 2}, indent=2))
        _git(sb, "add", "_project/03_project_state.json")

    def _run(self, sb):
        return subprocess.run(
            ["bash", ".agent/scripts/pre_commit.sh"],
            cwd=sb, capture_output=True, text=True,
            env={**_CLEAN_ENV, "FRAMEWORK_PYTHON": ""},
        )

    def test_codigo_del_framework_no_omite_la_suite(self):
        """FW-176: la regla anterior omitía la suite si todo lo staged caía bajo
        `_project/`, `_feedback/` o `.agent/` — y `.agent/` es donde vive TODO
        el código del framework. Un commit que solo tocara `state_core.py` se
        saltaba los tests enteros."""
        sb, doctor_witness, transition_witness, runner_witness = self._sandbox()
        self._stage_state_change(sb)
        script = sb / ".agent" / "scripts" / "algo.py"
        script.parent.mkdir(parents=True, exist_ok=True)
        script.write_text("# código del framework\n")
        _git(sb, "add", ".agent/scripts/algo.py")

        res = self._run(sb)

        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        self.assertTrue(
            runner_witness.exists(),
            "un cambio de código bajo .agent/ DEBE ejercitar la suite",
        )

    def test_artifact_only_commit_skips_suite(self):
        sb, doctor_witness, transition_witness, runner_witness = self._sandbox()
        self._stage_state_change(sb)
        # FW-176: la allowlist documental es explícita por ruta Y extensión.
        # Un `.txt` bajo `_project/` ya no la satisface — estar en una carpeta
        # de artefactos no vuelve documental a un archivo.
        (sb / "_project" / "note.md").write_text("nota de sesion")
        _git(sb, "add", "_project/note.md")

        res = self._run(sb)

        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        self.assertFalse(
            runner_witness.exists(),
            "commit solo-artefactos no debe invocar el runner de tests",
        )
        self.assertIn(
            "pre_commit: suite omitida (solo artefactos de sesión) — gates de estado y doctor sí corren",
            res.stdout,
        )
        # (c) doctor y transition gate SÍ corren en el caso omitido.
        self.assertTrue(doctor_witness.exists(), "el doctor debe correr siempre")
        self.assertTrue(
            transition_witness.exists(), "el transition gate debe correr siempre"
        )

    def test_product_code_commit_runs_suite(self):
        sb, doctor_witness, transition_witness, runner_witness = self._sandbox()
        self._stage_state_change(sb)
        src_dir = sb / "src"
        src_dir.mkdir()
        (src_dir / "app.py").write_text("x = 1\n")
        _git(sb, "add", "src/app.py")

        res = self._run(sb)

        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        self.assertTrue(
            runner_witness.exists(),
            "commit con codigo de producto debe invocar el runner de tests (sin cambios)",
        )
        self.assertNotIn("pre_commit: suite omitida", res.stdout)
        # (c) doctor y transition gate SÍ corren también en el caso normal.
        self.assertTrue(doctor_witness.exists(), "el doctor debe correr siempre")
        self.assertTrue(
            transition_witness.exists(), "el transition gate debe correr siempre"
        )

    def test_empty_staged_diff_is_treated_as_artifact_only(self):
        # AC: staged vacío (p.ej. commit vacío o de solo metadata git) se
        # trata como "solo artefactos" — no como error.
        sb, doctor_witness, transition_witness, runner_witness = self._sandbox()

        res = self._run(sb)

        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        self.assertFalse(runner_witness.exists())
        self.assertIn("pre_commit: suite omitida", res.stdout)
        self.assertTrue(doctor_witness.exists())


if __name__ == "__main__":
    unittest.main()
