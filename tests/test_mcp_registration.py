#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import unittest, json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / ".agent" / "templates" / "mcp.json.base"


class TestRegistration(unittest.TestCase):
    def test_state_manager_registered_local(self):
        data = json.loads(BASE.read_text(encoding="utf-8"))
        servers = data["mcpServers"]
        self.assertIn("state_manager", servers)
        sm = servers["state_manager"]
        self.assertEqual(sm["command"], "python3")
        self.assertIn(".agent/scripts/state_manager.py", " ".join(sm["args"]))


if __name__ == "__main__":
    unittest.main()
