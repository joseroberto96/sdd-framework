"""5.2.2 L5 — correcciones de una línea (TL FW-252, FW-239, FW-260)."""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / ".agent" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import claude_dynamic_context  # noqa: E402
import state_queries  # noqa: E402
import validate_acceptance  # noqa: E402

HOOK = SCRIPTS / "post_edit_format.sh"

# Formateador falso: `--check` sobre stdin falla si el texto contiene SUCIO;
# en modo escritura deja el archivo en «formatted\n».
FAKE_RUFF = """#!/usr/bin/env bash
if [ "$2" = "--check" ]; then
  if grep -q SUCIO; then exit 1; fi
  exit 0
fi
printf 'formatted\\n' > "$2"
"""


class PostEditFormatHeadTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.repo = base / "repo"
        self.repo.mkdir()
        bindir = base / "bin"
        bindir.mkdir()
        fake = bindir / "ruff"
        fake.write_text(FAKE_RUFF)
        fake.chmod(0o755)
        self.env = dict(os.environ, PATH=f"{bindir}:/usr/bin:/bin", CLAUDE_PROJECT_DIR=str(self.repo))
        git = ["git", "-C", str(self.repo)]
        subprocess.run(git + ["init", "-q"], check=True)
        subprocess.run(git + ["config", "user.email", "t@t"], check=True)
        subprocess.run(git + ["config", "user.name", "t"], check=True)
        (self.repo / "sucio.py").write_text("x=1  # SUCIO\n")
        (self.repo / "limpio.py").write_text("x = 1\n")
        subprocess.run(git + ["add", "."], check=True)
        subprocess.run(git + ["commit", "-qm", "base"], check=True)

    def tearDown(self):
        self.tmp.cleanup()

    def _run(self, name: str, content: str) -> str:
        target = self.repo / name
        target.write_text(content)
        result = subprocess.run(
            ["bash", str(HOOK)], env=dict(self.env, CLAUDE_TOOL_FILE_PATH=str(target)),
            capture_output=True, text=True,
        )
        self.assertEqual(result.returncode, 0)
        return target.read_text()

    def test_unformatted_in_head_is_left_alone(self):
        self.assertEqual(self._run("sucio.py", "x=2  # SUCIO\n"), "x=2  # SUCIO\n")

    def test_formatted_in_head_is_formatted(self):
        self.assertEqual(self._run("limpio.py", "x=2\n"), "formatted\n")

    def test_new_file_is_formatted(self):
        self.assertEqual(self._run("nuevo.py", "y=3  # SUCIO\n"), "formatted\n")


class SummaryTaskTargetTests(unittest.TestCase):
    def _state(self, key: str) -> dict:
        return {"module_statuses": {key: "in_progress"}, "work_tiers": {key: {"tier": "tier_1"}}}

    def test_task_target_reads_status_and_tier(self):
        for key in ("task:T1", "T1"):
            with self.subTest(key=key):
                summary = state_queries._summary(self._state(key), "task:T1", "task", "implementation", [])
                self.assertEqual(summary["module_status"], "in_progress")
                self.assertEqual(summary["tier"], "tier_1")

    def test_module_target_unchanged(self):
        summary = state_queries._summary(self._state("M1"), "M1", "module", "implementation", [])
        self.assertEqual((summary["module_status"], summary["tier"]), ("in_progress", "tier_1"))


class DynamicContextUnknownTests(unittest.TestCase):
    def _render(self, envelope: dict) -> str:
        envelope = dict({"status": "OK", "gate_status": "PASS", "data": {"summary": {}}}, **envelope)
        return claude_dynamic_context.render_dynamic_context_markdown(envelope)

    def test_missing_fields_with_active_target_say_unknown(self):
        rendered = self._render({"module_id": "task:T1"})
        self.assertIn("`desconocido (consulta lookup_state)`, tier `desconocido (consulta lookup_state)`", rendered)

    def test_no_target_keeps_no_activo(self):
        rendered = self._render({})
        self.assertIn("`no_activo`, tier `no_clasificado`", rendered)


class AcceptanceBacktickMarkerTests(unittest.TestCase):
    def test_backticked_path_with_marker_is_exempt(self):
        with tempfile.TemporaryDirectory() as tmp:
            body = "Evidencia: `pkg/modulo.py` (no es ruta del repo)"
            self.assertEqual(validate_acceptance.missing_artifacts(body, Path(tmp)), [])

    def test_backticked_path_without_marker_still_flagged(self):
        with tempfile.TemporaryDirectory() as tmp:
            body = "Evidencia: `pkg/modulo.py` verificado"
            self.assertEqual(validate_acceptance.missing_artifacts(body, Path(tmp)), ["pkg/modulo.py"])


if __name__ == "__main__":
    unittest.main()
