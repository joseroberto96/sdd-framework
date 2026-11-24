#!/usr/bin/env python3
import json
import re
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / ".agent" / "scripts"))
import sync_claude_commands as sync  # noqa: E402


class ProgressiveContextWiringTests(unittest.TestCase):
    def setUp(self):
        self.manifest = json.loads(
            (ROOT / ".agent" / "workflows" / "manifest.json").read_text(encoding="utf-8")
        )

    def test_runtime_core_covers_permanent_obligations(self):
        text = (ROOT / ".agent" / "rules" / "runtime_core.md").read_text(encoding="utf-8")
        for phrase in ("estado canónico", "scope", "PRESERVAR", "destruct", "validación"):
            self.assertIn(phrase, text)

    def test_bootstrap_instruction_surfaces_stay_bounded(self):
        """The managed entry points must not recreate the old full dump."""
        for relative in ("AGENTS.md", "CLAUDE.md"):
            size = len((ROOT / relative).read_bytes())
            self.assertLessEqual(size, 8 * 1024, f"{relative} excede 8 KiB: {size}")
        core_size = len((ROOT / ".agent/rules/runtime_core.md").read_bytes())
        # 6 KiB desde 5.3.0 L2: el contrato del cierre de turno vive en el núcleo (D3).
        self.assertLessEqual(core_size, 6 * 1024, f"runtime_core excede 6 KiB: {core_size}")

    def test_progressive_metadata_points_to_real_sources_and_conditions(self):
        allowed = {"always", "interruption_or_inconsistency", "implementation", "closure", "recovery", "publish"}
        covered = {"arrancar", "ciclo", "cierre-modulo", "reconciliar", "gycd"}
        commands = {item["name"]: item for item in self.manifest["commands"]}
        self.assertTrue(covered.issubset(commands))
        for name in covered:
            context = commands[name].get("context")
            self.assertIsInstance(context, dict, name)
            self.assertTrue((ROOT / context["required_now"][0]).exists())
            for trigger in context.get("activate_when", []):
                self.assertIn(trigger["condition"], allowed)

    def test_normal_workflows_route_through_selective_query(self):
        for relative in (
            ".agent/workflows/arrancar.md",
            ".agent/workflows/ciclo.md",
            ".agent/workflows/cierre-modulo.md",
            ".agent/workflows/gycd.md",
        ):
            text = (ROOT / relative).read_text(encoding="utf-8")
            self.assertIn("get_work_context", text, relative)

    def test_sync_check_is_idempotent_and_wrappers_remain_generated(self):
        result = subprocess.run(
            [sys.executable, ".agent/scripts/sync_claude_commands.py", "--check"],
            cwd=ROOT,
            text=True,
            capture_output=True,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        for item in self.manifest["commands"]:
            source = ROOT / item["source"]
            self.assertTrue(source.exists(), item["name"])

    def test_obligation_matrix_has_all_required_ids(self):
        matrix = json.loads(
            (ROOT / ".agent/docs/specs/progressive_context_obligations.json").read_text(encoding="utf-8")
        )
        ids = {item["id"] for item in matrix["obligations"]}
        self.assertEqual(
            ids,
            {"STATE", "SCOPE", "TIER", "APPROVAL", "PRESERVE", "DESTRUCTIVE",
             "DEPENDENCIES", "SPECS", "SECURITY", "UI_STATES", "NFR", "EXECUTION",
             "REVIEW", "CLOSURE", "RECOVERY", "PUBLISH"},
        )

    def test_arrancar_has_no_legacy_references(self):
        text = (ROOT / ".agent/workflows/arrancar.md").read_text(encoding="utf-8")
        self.assertNotIn("5 archivos legacy", text)
        self.assertNotIn("Con los datos de `core.md`, presentar", text)
        self.assertNotIn("Con los datos de core.md, presentar", text)
        self.assertIn("vista de arranque", text)
        manifest_cmd = next(c for c in self.manifest["commands"] if c["name"] == "arrancar")
        self.assertNotIn("lee estado, scope, bugs, ACs", manifest_cmd["description"])
        self.assertIn("vista de arranque", manifest_cmd["description"])


if __name__ == "__main__":
    unittest.main()
