#!/usr/bin/env python3
import copy
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / ".agent" / "scripts"))

import state_queries


def fixture_state():
    return json.loads(
        (ROOT / "tests" / "fixtures" / "progressive_context" / "state_base.json").read_text(
            encoding="utf-8"
        )
    )


def section(path: str, content: str, *, module_id: str | None = None):
    value = {"path": path, "selector": "fixture", "content": content}
    if module_id is not None:
        value["module_id"] = module_id
    return value


class ProgressiveContextTests(unittest.TestCase):
    def test_explicit_target_wins(self):
        result = state_queries.query_context(fixture_state(), view="implementation", module_id="M2")
        self.assertEqual(result["module_id"], "M2")
        self.assertEqual(result["target_kind"], "module")

    def test_set_active_module_order_is_preserved(self):
        state = fixture_state()
        before = copy.deepcopy(state["modules"]["in_progress"])
        result = state_queries.query_context(state, view="startup")
        self.assertEqual(result["module_id"], "M1")
        self.assertEqual(state["modules"]["in_progress"], before)

    def test_scope_conflict_is_needs_input(self):
        state = fixture_state()
        sources = {"hashes": {"_project/04_active_scope.md": "scope-hash"}, "sections": {
            "scope": section("_project/04_active_scope.md", "M1", module_id="M1")
        }}
        result = state_queries.query_context(state, view="implementation", module_id="M2", sources=sources)
        self.assertEqual(result["gate_status"], "NEEDS_INPUT")
        self.assertEqual(result["gate_scope"], "implementation")

    def test_global_critical_is_not_filtered_out(self):
        state = fixture_state()
        state["_bugs"] = [{
            "id": "BUG-OUTSIDE", "module_id": "OTHER", "severity": "Crítico",
            "status": "Abierto", "affects_ac": False, "description": "Bloqueo global"
        }]
        result = state_queries.query_context(state, view="implementation", module_id="M1")
        self.assertEqual(result["gate_status"], "BLOCK")
        self.assertGreaterEqual(result["blocking_count"], 1)

    def test_two_closure_rejections_block_startup(self):
        state = fixture_state()
        state["closure_rejections"] = [
            {"id": "REJ-1", "module_id": "M1", "reason": "ac_incomplete", "timestamp": "2026-09-09T00:00:00Z"},
            {"id": "REJ-2", "module_id": "M1", "reason": "ac_evidence_missing", "timestamp": "2026-09-09T00:01:00Z"},
        ]
        result = state_queries.query_context(state, view="startup")
        self.assertEqual(result["gate_status"], "BLOCK")
        self.assertEqual(result["blocking_count"], 2)

    def test_unrelated_history_does_not_expand_startup(self):
        small = state_queries.query_context(fixture_state(), view="startup")
        large_state = fixture_state()
        large_state["_bugs"] = [
            {"id": f"BUG-{i}", "module_id": "OLD", "severity": "Menor",
             "status": "Resuelto", "affects_ac": False, "description": "historial"}
            for i in range(10000)
        ]
        large = state_queries.query_context(large_state, view="startup")
        self.assertLessEqual(
            len(state_queries.canonical_json(large)),
            len(state_queries.canonical_json(small)) + 64,
        )

    def test_unicode_fragments_roundtrip(self):
        state = fixture_state()
        original = "áéíóú🙂漢字\n" * 5000
        sources = {"hashes": {"_project/04_active_scope.md": "unicode-v1"}, "sections": {
            "scope": section("_project/04_active_scope.md", original, module_id="M1")
        }}
        parts = []
        cursor = None
        while True:
            result = state_queries.query_context(
                state, view="implementation", module_id="M1", cursor=cursor, sources=sources
            )
            for item in result["data"]["items"]:
                if item["kind"] == "section_fragment" and item["id"].startswith("scope"):
                    parts.append((item["id"], item["content"]))
            if result["complete"]:
                self.assertFalse(result["required_context_complete"])
                break
            cursor = result["next_cursor"]
            self.assertIsNotNone(cursor)
        self.assertEqual("".join(content for _, content in sorted(parts)), original)

    def test_cursor_rejected_after_source_change(self):
        state = fixture_state()
        sources = {"hashes": {"_project/04_active_scope.md": "v1"}, "sections": {
            "scope": section("_project/04_active_scope.md", "scope", module_id="M1"),
            "notes": section("notes.md", "x" * 40000),
        }}
        first = state_queries.query_context(state, view="implementation", module_id="M1", sources=sources)
        self.assertFalse(first["complete"])
        changed = copy.deepcopy(sources)
        changed["hashes"]["_project/04_active_scope.md"] = "v2"
        with self.assertRaises(state_queries.QueryError) as error:
            state_queries.query_context(
                state, view="implementation", module_id="M1",
                cursor=first["next_cursor"], sources=changed
            )
        self.assertEqual(error.exception.code, "STALE_CURSOR")

    def test_required_context_differs_from_pagination(self):
        state = fixture_state()
        sources = {"hashes": {"scope.md": "v1", "notes.md": "v1"}, "sections": {
            "scope": section("scope.md", "scope", module_id="M1"),
            "notes": section("notes.md", "optional" * 20000),
        }}
        result = state_queries.query_context(state, view="implementation", module_id="M1", sources=sources)
        self.assertFalse(result["complete"])
        self.assertFalse(result["required_context_complete"])

    def test_query_does_not_mutate_input(self):
        state = fixture_state()
        sources = {"hashes": {}, "sections": {}}
        before = copy.deepcopy(state)
        state_queries.query_context(state, view="closure", module_id="M1", sources=sources)
        self.assertEqual(state, before)

    def test_implementation_summary_carries_decision_fields(self):
        state = fixture_state()
        state["active_focus"] = "backend"
        state["execution_profile"] = {"capability_tier": "standard"}
        state["module_specs"] = {
            "M1": {
                "data_model": "n/a",
                "api_contract": "n/a",
                "justification": "fixture sin persistencia",
                "classified_at": "2026-09-09T00:00:00Z",
                "classified_by": "fixture",
            }
        }
        state["dependencies"] = ["M0:runtime"]
        result = state_queries.query_context(
            state,
            view="implementation",
            module_id="M1",
            sources={"hashes": {}, "sections": {}},
        )
        summary = result["data"]["summary"]
        self.assertEqual(summary["active_focus"], "backend")
        self.assertEqual(summary["capability_tier"], "standard")
        self.assertEqual(summary["module_spec"]["data_model"], "n/a")
        self.assertEqual(summary["dependencies"][0], "M0:runtime")

    def test_invalid_cursor_has_stable_error(self):
        with self.assertRaises(state_queries.QueryError) as error:
            state_queries.query_context(fixture_state(), view="startup", cursor="not-a-cursor")
        self.assertEqual(error.exception.code, "INVALID_CURSOR")


if __name__ == "__main__":
    unittest.main()
