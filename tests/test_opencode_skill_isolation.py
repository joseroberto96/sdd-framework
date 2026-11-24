# tests/test_opencode_skill_isolation.py
import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class TestOpencodeSkillIsolation(unittest.TestCase):
    def test_template_opencode_json_denies_codex_skill_bleed(self):
        config = json.loads((ROOT / "opencode.json").read_text(encoding="utf-8"))
        skill_perms = (config.get("permission") or {}).get("skill") or {}
        denied = [
            pat for pat, action in skill_perms.items()
            if pat.startswith("source-command") and action == "deny"
        ]
        self.assertTrue(
            denied,
            "opencode.json debe denegar las skills source-command-* (FW-043)",
        )


if __name__ == "__main__":
    unittest.main()
