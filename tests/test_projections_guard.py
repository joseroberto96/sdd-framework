#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import unittest
import tempfile
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / ".agent" / "scripts"))
import state_core
import projections


class TestGuard(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        (root / "_project").mkdir(parents=True)
        self._orig = {k: getattr(state_core, k) for k in
                      ["WORKSPACE_ROOT", "CORE_FILE_PATH", "BUGS_FILE_PATH",
                       "CLARIFICATIONS_FILE_PATH", "DECISIONS_FILE_PATH", "SESSION_BRIEF_PATH"]}
        state_core.WORKSPACE_ROOT = root
        state_core.CORE_FILE_PATH = root / "_project" / "core.md"
        state_core.BUGS_FILE_PATH = root / "_project" / "06_bug_fixes.md"
        state_core.CLARIFICATIONS_FILE_PATH = root / "_project" / "07_pending_clarifications.md"
        state_core.DECISIONS_FILE_PATH = root / "_project" / "03_arch_decisions.md"
        state_core.SESSION_BRIEF_PATH = root / "_project" / "00_session_brief.json"
        self.state = {"project": "P", "current_phase": "phase_01", "modules": {"in_progress": []},
                      "module_statuses": {}, "work_tiers": {}, "_bugs": [], "_clarifications": [], "_decisions": []}

    def tearDown(self):
        for k, v in self._orig.items():
            setattr(state_core, k, v)
        self.tmp.cleanup()

    def test_guard_blocks_manual_file(self):
        state_core.BUGS_FILE_PATH.write_text("### BUG-001 — escrito a mano\n", encoding="utf-8")
        with self.assertRaises(projections.ProjectionGuardError):
            projections.render(self.state)

    def test_guard_allows_banner_file(self):
        state_core.BUGS_FILE_PATH.write_text(projections.BANNER + "\nviejo\n", encoding="utf-8")
        projections.render(self.state)  # no raise
        self.assertIn(projections.BANNER, state_core.BUGS_FILE_PATH.read_text(encoding="utf-8"))

    def test_guard_allows_empty_dir(self):
        projections.render(self.state)  # nothing exists yet → fine
        self.assertTrue(state_core.CORE_FILE_PATH.exists())


if __name__ == "__main__":
    unittest.main()
