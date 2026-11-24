#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""FW-206/D1: `.claude/agents/*.md` — subagentes read-only de Claude Code
generados desde los contratos portables de `.agent/subagents/`.

Parity con `.opencode/agents/` (que ya existía con `edit/bash/webfetch: deny`):
mismo set de slugs, `tools:` sin herramientas de escritura (lección FW-014)."""
from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CLAUDE_AGENTS = ROOT / ".claude" / "agents"
OPENCODE_AGENTS = ROOT / ".opencode" / "agents"

sys.path.insert(0, str(ROOT / ".agent" / "scripts"))
import sync_claude_commands as scc  # noqa: E402

GENERATED = scc.GENERATED
EXPECTED = {slug for slug, _t, _d in scc.SUBAGENTS}
WRITE_TOOL_RE = re.compile(r"\btools\s*:.*\b(Edit|Write|Bash|MultiEdit)\b", re.IGNORECASE)


class ClaudeAgentsSurfaceTests(unittest.TestCase):
    def test_all_expected_agents_exist(self):
        present = {p.stem for p in CLAUDE_AGENTS.glob("*.md")}
        self.assertEqual(EXPECTED, present, "el set de .claude/agents/ no coincide con SUBAGENTS")

    def test_no_executor(self):
        self.assertNotIn("executor", EXPECTED, "executor no debe generarse (aislamiento por worktree, no por permisos)")

    def test_frontmatter_is_read_only(self):
        for path in CLAUDE_AGENTS.glob("*.md"):
            text = path.read_text(encoding="utf-8")
            self.assertIn(GENERATED, text, f"{path.name} sin marcador GENERATED")
            self.assertRegex(text, r"tools:\s*Read,\s*Grep,\s*Glob", f"{path.name}: tools no es read-only")
            self.assertIsNone(WRITE_TOOL_RE.search(text), f"{path.name}: tools incluye una herramienta de escritura")

    def test_points_to_canonical_contract(self):
        for path in CLAUDE_AGENTS.glob("*.md"):
            text = path.read_text(encoding="utf-8")
            self.assertIn(f".agent/subagents/{path.stem}.md", text, f"{path.name} no referencia su contrato canónico")

    def test_parity_with_opencode_agents(self):
        claude = {p.stem for p in CLAUDE_AGENTS.glob("*.md")}
        opencode = {p.stem for p in OPENCODE_AGENTS.glob("*.md")}
        self.assertEqual(claude, opencode, "los slugs de .claude/agents y .opencode/agents difieren")

    def test_sync_is_idempotent(self):
        before = {p.name: p.read_text(encoding="utf-8") for p in CLAUDE_AGENTS.glob("*.md")}
        scc.main([])
        after = {p.name: p.read_text(encoding="utf-8") for p in CLAUDE_AGENTS.glob("*.md")}
        self.assertEqual(before, after, "un segundo sync cambió .claude/agents/")

    def test_orphan_with_marker_is_removed(self):
        zombie = CLAUDE_AGENTS / "zombie.md"
        zombie.write_text(f"---\nname: zombie\n---\n{GENERATED}\ncuerpo\n", encoding="utf-8")
        self.addCleanup(lambda: zombie.exists() and zombie.unlink())
        scc.main([])
        self.assertFalse(zombie.exists(), "el sync no podó un .claude/agents/ generado huérfano")

    def test_manual_file_without_marker_is_preserved(self):
        manual = CLAUDE_AGENTS / "mi-agente.md"
        manual.write_text("---\nname: mi-agente\n---\nagente propio del proyecto\n", encoding="utf-8")
        self.addCleanup(lambda: manual.exists() and manual.unlink())
        scc.main([])
        self.assertTrue(manual.exists(), "el sync borró un .claude/agents/ hecho a mano (sin marcador)")


class DoctorClaudeAgentsTests(unittest.TestCase):
    def setUp(self):
        import importlib
        self.doctor = importlib.import_module("framework_doctor")

    def test_write_tool_in_agent_is_rejected(self):
        errors: list[str] = []
        warnings: list[str] = []
        original = (CLAUDE_AGENTS / "reviewer.md").read_text(encoding="utf-8")
        (CLAUDE_AGENTS / "reviewer.md").write_text(
            original.replace("tools: Read, Grep, Glob", "tools: Read, Grep, Glob, Bash"),
            encoding="utf-8",
        )
        self.addCleanup(lambda: (CLAUDE_AGENTS / "reviewer.md").write_text(original, encoding="utf-8"))
        self.doctor.validate_claude_agents(errors, warnings)
        self.assertTrue(any("herramienta de escritura" in e for e in errors), errors)

    def test_missing_agent_is_rejected(self):
        errors: list[str] = []
        warnings: list[str] = []
        p = CLAUDE_AGENTS / "security.md"
        original = p.read_text(encoding="utf-8")
        p.unlink()
        self.addCleanup(lambda: p.write_text(original, encoding="utf-8"))
        self.doctor.validate_claude_agents(errors, warnings)
        self.assertTrue(any("security.md" in e for e in errors), errors)


if __name__ == "__main__":
    unittest.main()
