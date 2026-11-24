#!/usr/bin/env python3
"""5.1 L6.1: higiene de sesión y criterio anti-sobreingeniería de los revisores."""

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _read(rel: str) -> str:
    return " ".join((ROOT / rel).read_text(encoding="utf-8").split())


class SessionHygieneTests(unittest.TestCase):
    def test_runtime_core_has_the_four_session_rules(self):
        text = _read(".agent/rules/runtime_core.md")
        for fragment in (
            "una unidad de trabajo por sesión",
            "tras dos correcciones fallidas del mismo problema",
            "modelo y esfuerzo fijos toda la sesión",
            "exploración amplia fuera del contexto principal",
            "SESSION_HYGIENE",
        ):
            self.assertIn(fragment, text)

    def test_claude_code_adapter_has_session_hygiene_section(self):
        text = (ROOT / ".agent/adapters/claude-code.md").read_text(encoding="utf-8")
        self.assertIn("## SESSION_HYGIENE", text)
        for command in ("/clear", "/btw", "/rename", "/compact", "/rewind"):
            self.assertIn(command, text)

    def test_reviewers_only_fail_on_correctness(self):
        for rel in (".agent/subagents/reviewer.md", ".agent/subagents/preflight-reviewer.md"):
            self.assertIn("nunca produce `fail`", _read(rel), rel)

    def test_claude_md_tells_compaction_what_to_keep(self):
        self.assertIn("Al compactar, conservar siempre", _read("CLAUDE.md"))


if __name__ == "__main__":
    unittest.main()
