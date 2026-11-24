#!/usr/bin/env python3
import contextlib
import io
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / ".agent" / "scripts"))

import state_core
import state_manager
import state_queries

if __package__:
    from .test_state_queries import fixture_state
else:
    from test_state_queries import fixture_state


class QueryTransportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        project = self.root / "_project"
        project.mkdir()
        self.state_path = project / "03_project_state.json"
        self.state_path.write_text(json.dumps(fixture_state()), encoding="utf-8")
        (project / "04_active_scope.md").write_text("# Scope\n\n## M1\nScope de M1\n", encoding="utf-8")
        (project / "08_module_acceptance.md").write_text("## M1\n- [ ] AC-1\n", encoding="utf-8")
        matrix = self.root / ".agent" / "docs" / "specs"
        matrix.mkdir(parents=True)
        shutil.copy2(
            ROOT / ".agent" / "docs" / "specs" / "progressive_context_obligations.json",
            matrix / "progressive_context_obligations.json",
        )
        manifest = self.root / ".agent" / "workflows"
        manifest.mkdir(parents=True)
        (manifest / "manifest.json").write_text(
            json.dumps({"commands": [{"name": "ciclo", "builder_facing": True}]}),
            encoding="utf-8",
        )
        self.saved = {
            "root": state_core.WORKSPACE_ROOT,
            "state": state_core.STATE_FILE_PATH,
            "core": state_core.CORE_FILE_PATH,
            "bugs": state_core.BUGS_FILE_PATH,
            "clars": state_core.CLARIFICATIONS_FILE_PATH,
            "decisions": state_core.DECISIONS_FILE_PATH,
            "brief": state_core.SESSION_BRIEF_PATH,
        }
        state_core.WORKSPACE_ROOT = self.root
        state_core.STATE_FILE_PATH = self.state_path
        state_core.CORE_FILE_PATH = project / "core.md"
        state_core.BUGS_FILE_PATH = project / "06_bug_fixes.md"
        state_core.CLARIFICATIONS_FILE_PATH = project / "07_pending_clarifications.md"
        state_core.DECISIONS_FILE_PATH = project / "03_arch_decisions.md"
        state_core.SESSION_BRIEF_PATH = project / "00_session_brief.json"
        self.manager = state_manager.StateManager()

    def tearDown(self):
        for key, value in self.saved.items():
            setattr(state_core, {
                "root": "WORKSPACE_ROOT", "state": "STATE_FILE_PATH", "core": "CORE_FILE_PATH",
                "bugs": "BUGS_FILE_PATH", "clars": "CLARIFICATIONS_FILE_PATH",
                "decisions": "DECISIONS_FILE_PATH", "brief": "SESSION_BRIEF_PATH",
            }[key], value)
        self.tmp.cleanup()

    def test_tools_list_exposes_selective_context_without_removing_legacy_tools(self):
        response = state_manager.handle_mcp_request(self.manager, {
            "jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}
        })
        names = [tool["name"] for tool in response["result"]["tools"]]
        self.assertIn("get_work_context", names)
        self.assertIn("get_session_brief", names)
        self.assertIn("get_full_state", names)
        self.assertIn("lookup_state", names)
        self.assertIn("mutate_state", names)

    def test_mcp_and_shared_helper_return_same_context_json(self):
        expected = state_manager.get_work_context(self.manager, view="implementation", module_id="M1")
        response = state_manager.handle_mcp_request(self.manager, {
            "jsonrpc": "2.0", "id": 2, "method": "tools/call",
            "params": {"name": "get_work_context", "arguments": {
                "view": "implementation", "module_id": "M1"
            }}
        })
        raw = response["result"]["content"][0]["text"]
        served = json.loads(raw)
        self.assertEqual(served, expected)
        self.assertEqual(raw, state_queries.serialize_context(expected))
        self.assertLessEqual(
            len(raw.encode("utf-8")), state_queries.VIEW_LIMITS["implementation"]
        )
        self.assertEqual(served["module_id"], "M1")

    def test_mcp_invalid_context_arguments_are_structured_errors(self):
        response = state_manager.handle_mcp_request(self.manager, {
            "jsonrpc": "2.0", "id": 3, "method": "tools/call",
            "params": {"name": "get_work_context", "arguments": {
                "view": "startup", "module_id": 123
            }},
        })
        payload = json.loads(response["result"]["content"][0]["text"])
        self.assertEqual(payload["error"]["code"], "INVALID_TARGET")

        malformed = state_manager.handle_mcp_request(self.manager, {
            "jsonrpc": "2.0", "id": 4, "method": "tools/call",
            "params": {"name": "get_work_context", "arguments": []},
        })
        malformed_payload = json.loads(
            malformed["result"]["content"][0]["text"]
        )
        self.assertEqual(malformed_payload["error"]["code"], "INVALID_ARGUMENT")

    def test_query_does_not_render_or_mutate_state(self):
        before = self.state_path.read_text(encoding="utf-8")
        state_manager.get_work_context(self.manager, view="startup")
        self.assertEqual(self.state_path.read_text(encoding="utf-8"), before)
        self.assertFalse((self.root / "_project" / "core.md").exists())

    def test_duplicate_document_heading_is_an_explicit_reason(self):
        # 5.3.1 L3.5 (PA-FW-011, cambio de veredicto D2): dos secciones del
        # módulo siguen siendo ambiguas, pero salen como un motivo más de la
        # respuesta (`ambiguous_source`), sin cortar el resto con un error.
        (self.root / "_project" / "08_module_acceptance.md").write_text(
            "## M1\nfirst\n## M1\nsecond\n", encoding="utf-8"
        )
        result = state_manager.get_work_context(self.manager, view="closure", module_id="M1")
        self.assertNotEqual(result["gate_status"], "PASS")
        self.assertFalse(result["required_context_complete"])
        self.assertIn(
            ("acceptance", "ambiguous_source"),
            {(ref.get("section"), ref.get("reason")) for ref in result["refs"]},
        )

    def test_missing_document_section_requires_input(self):
        (self.root / "_project" / "04_active_scope.md").write_text(
            "# Scope\n\n## M2\nScope de M2\n", encoding="utf-8"
        )
        result = state_manager.get_work_context(
            self.manager, view="implementation", module_id="M1"
        )
        self.assertEqual(result["gate_status"], "NEEDS_INPUT")
        self.assertFalse(result["required_context_complete"])
        self.assertIn("scope", [ref["section"] for ref in result["refs"]])
        self.assertIn("PRESERVE", {ref.get("obligation") for ref in result["refs"]})

    def test_relative_escape_is_rejected(self):
        with self.assertRaises(state_queries.QueryError) as error:
            state_manager.resolve_source_path(self.root, "../outside.md")
        self.assertEqual(error.exception.code, "SOURCE_MISSING")

    def test_mcp_returns_structured_error_for_stale_source(self):
        notes = self.root / "_project" / "04_active_scope.md"
        notes.write_text("# Scope\n\n## M1\n" + ("x" * 40000), encoding="utf-8")
        first = state_manager.get_work_context(self.manager, view="implementation", module_id="M1")
        self.assertFalse(first["complete"])
        notes.write_text("# Scope\n\n## M1\nchanged", encoding="utf-8")
        response = state_manager.handle_mcp_request(self.manager, {
            "jsonrpc": "2.0", "id": 3, "method": "tools/call",
            "params": {"name": "get_work_context", "arguments": {
                "view": "implementation", "module_id": "M1", "cursor": first["next_cursor"]
            }}
        })
        self.assertTrue(response["result"]["isError"])
        error = json.loads(response["result"]["content"][0]["text"])["error"]
        self.assertEqual(error["code"], "STALE_CURSOR")


if __name__ == "__main__":
    unittest.main()
