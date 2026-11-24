#!/usr/bin/env python3
import json
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / ".agent" / "scripts"))

import context_sources
import state_core
import state_manager
import state_queries

if __package__:
    from .test_state_queries import fixture_state, section
else:
    from test_state_queries import fixture_state, section


class ProgressiveContextFixTests(unittest.TestCase):
    def test_blocked_module_cannot_pass_implementation_gate(self):
        state = fixture_state()
        state["module_statuses"]["M1"] = "blocked"
        result = state_queries.query_context(
            state,
            view="implementation",
            module_id="M1",
            sources={
                "hashes": {"scope.md": "v1"},
                "sections": {"scope": section("scope.md", "## M1\nScope\n", module_id="M1")},
            },
        )
        self.assertEqual(result["gate_status"], "BLOCK")
        self.assertTrue(any(item["id"] == "module:M1:blocked" for item in result["data"]["items"]))

    def test_decision_lists_are_paginated_without_truncation(self):
        state = fixture_state()
        state["module_specs"] = {
            "M1": {
                "data_model": "n/a",
                "api_contract": "n/a",
                "justification": "fixture",
                "classified_at": "2026-09-09T00:00:00Z",
                "classified_by": "fixture",
            }
        }
        state["architecture_constraints"] = [f"RULE-{index}" for index in range(71)]
        sources = {
            "hashes": {"scope.md": "v1"},
            "sections": {"scope": section("scope.md", "## M1\nScope\n", module_id="M1")},
        }

        pages = []
        cursor = None
        while True:
            result = state_queries.query_context(
                state,
                view="implementation",
                module_id="M1",
                cursor=cursor,
                sources=sources,
            )
            pages.append(result)
            if result["complete"]:
                break
            cursor = result["next_cursor"]

        encoded = json.dumps(pages, ensure_ascii=False, sort_keys=True)
        for index in range(71):
            self.assertIn(f"RULE-{index}", encoded)

    def test_implicit_target_is_resolved_before_loading_document_sections(self):
        manager = mock.Mock()
        manager.state_data = fixture_state()
        sources = {
            "hashes": {},
            "sections": {
                "scope": section("scope.md", "## M1\nONLY-M1\n", module_id="M1"),
                "acceptance": section("acceptance.md", "## M1\nAC-M1\n", module_id="M1"),
            },
        }
        with mock.patch.object(
            context_sources, "load_sources", return_value=sources
        ) as load_sources:
            state_manager.get_work_context(manager, view="implementation")

        self.assertEqual(load_sources.call_args.args[2], "M1")

    def test_emitted_context_json_is_measured_with_the_transport_serializer(self):
        state = fixture_state()
        result = state_queries.query_context(
            state,
            view="implementation",
            module_id="M1",
            sources={
                "hashes": {},
                "sections": {
                    "scope": section("scope.md", "a" * 21300, module_id="M1"),
                },
            },
        )
        payload = state_queries.serialize_context(result)
        self.assertEqual(
            payload,
            json.dumps(result, ensure_ascii=False, sort_keys=True),
        )
        self.assertLessEqual(len(payload.encode("utf-8")), state_queries.VIEW_LIMITS["implementation"])

    def test_manual_upgrade_states_the_version_floor_before_copying(self):
        # 5.3.1 L5.5 (S8): la migración Codex se retiró; el upgrade solo sube desde 5.3.0.
        workflow = (ROOT / ".agent" / "workflows" / "actualizar-framework.md").read_text(
            encoding="utf-8"
        )
        floor = "v5.3.0"
        copy_marker = "Sobrescribir desde la plantilla nueva"
        self.assertIn(floor, workflow)
        self.assertNotIn("migrate_codex_budget_retirement", workflow)
        self.assertLess(workflow.index(floor), workflow.index(copy_marker))

    def test_bootstrap_defers_full_constitution_documents(self):
        bootstrap = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
        self.assertNotRegex(bootstrap, r"(?m)^- Leer .*GUARDRAILS_MASTER\.md")
        self.assertNotRegex(bootstrap, r"(?m)^- Leer .*CLAUDE\.md")
        self.assertRegex(bootstrap, r"(?i)bajo demanda|según la etapa|etapa")


if __name__ == "__main__":
    unittest.main()
