#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Guards against validator/schema drift: every root key the schema marks
required must be checked by the hardcoded validator."""
import unittest
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / ".agent" / "scripts"))
import state_core

SCHEMA = json.loads((ROOT / ".agent" / "schemas" / "project_state.schema.json").read_text(encoding="utf-8"))


class TestParity(unittest.TestCase):
    def test_each_required_root_key_is_validated(self):
        mgr = state_core.StateManager()
        base = {
            "project": "P", "framework_mode": "template", "framework_version": "3.2",
            "current_phase": "phase_01", "current_step": "", "last_completed_step": "",
            "stack": {"backend": "", "frontend": "", "database": "", "auth": ""},
            "dependencies": [], "architecture_constraints": [],
            "contracts": {"public_interfaces": {}},
            "modules": {"completed": [], "in_progress": [], "pending": []},
            "module_statuses": {}, "work_tiers": {}, "module_specs": {},
            "closure_rejections": [], "active_mcps": [], "_refs": {},
        }
        mgr.validate_state(base)  # baseline valid
        for key in SCHEMA["required"]:
            broken = dict(base)
            broken.pop(key)
            with self.assertRaises(ValueError, msg=f"validator ignores missing '{key}'"):
                mgr.validate_state(broken)


if __name__ == "__main__":
    unittest.main()
