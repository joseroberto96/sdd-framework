#!/usr/bin/env python3
"""Task 3B: aplicabilidad y transporte de contexto sin caché stale."""

from __future__ import annotations

import json
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / ".agent" / "scripts"))

import state_manager
import state_queries

if __package__:
    from .test_framework_corrections import (
        run_cli_query,
        run_mcp_query,
        runtime_context_root,
        runtime_module_docs,
        temporary_manager,
    )
    from .test_state_queries import fixture_state
else:
    from test_framework_corrections import (
        run_cli_query,
        run_mcp_query,
        runtime_context_root,
        runtime_module_docs,
        temporary_manager,
    )
    from test_state_queries import fixture_state


def _implementation_state() -> dict:
    state = fixture_state()
    state["module_specs"]["M1"] = {
        "data_model": "n/a",
        "api_contract": "n/a",
        "justification": "fixture sin superficie ejecutable",
        "classified_at": "2026-09-09T00:00:00Z",
        "classified_by": "fixture",
    }
    return state


def _mcp_request(process: subprocess.Popen[str], request: dict) -> dict:
    assert process.stdin is not None
    assert process.stdout is not None
    process.stdin.write(json.dumps(request, ensure_ascii=False) + "\n")
    process.stdin.flush()
    line = process.stdout.readline()
    if not line:
        stderr = process.stderr.read() if process.stderr is not None else ""
        raise AssertionError(f"MCP process closed before responding: {stderr}")
    return json.loads(line)


def _mcp_notification(process: subprocess.Popen[str], request: dict) -> None:
    assert process.stdin is not None
    process.stdin.write(json.dumps(request, ensure_ascii=False) + "\n")
    process.stdin.flush()


def _mcp_tools_call(request_id: int) -> dict:
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "method": "tools/call",
        "params": {
            "name": "get_work_context",
            "arguments": {"view": "implementation", "module_id": "M1"},
        },
    }


class ContextApplicabilityTransportTests(unittest.TestCase):
    def test_missing_obligation_matrix_fails_closed_on_cli_and_mcp(self):
        holder, root = runtime_context_root(_implementation_state())
        try:
            runtime_module_docs(root, "M1", ui_states="n/a")
            matrix = root / ".agent" / "docs" / "specs" / "progressive_context_obligations.json"
            matrix.unlink()

            cli = run_cli_query(root, "implementation", "M1")
            self.assertEqual(cli.returncode, 3, cli.stderr)
            self.assertEqual(
                json.loads(cli.stdout)["error"]["code"], "SOURCE_MISSING"
            )

            process, _, responses = run_mcp_query(root, "implementation", "M1")
            self.assertEqual(process.returncode, 0, process.stderr)
            call = next(response for response in responses if response.get("id") == 3)
            self.assertTrue(call["result"]["isError"])
            error = json.loads(call["result"]["content"][0]["text"])
            self.assertEqual(error["error"]["code"], "SOURCE_MISSING")
        finally:
            holder.cleanup()

    def test_cli_recomputes_required_sections_and_rejects_pre_edit_cursor(self):
        holder, root = runtime_context_root(_implementation_state())
        try:
            runtime_module_docs(root, "M1", ui_states="n/a")
            acceptance = root / "_project" / "08_module_acceptance.md"
            acceptance.write_text(
                acceptance.read_text(encoding="utf-8") + "\n" + ("contexto estable\n" * 5000),
                encoding="utf-8",
            )

            before = run_cli_query(root, "implementation", "M1")
            self.assertEqual(before.returncode, 0, before.stderr)
            before_result = json.loads(before.stdout)
            self.assertNotIn("ui_states", before_result["required_sections"])
            self.assertNotIn("ui_evidence", before_result["required_sections"])
            self.assertIsNotNone(before_result["next_cursor"])

            acceptance.write_text(
                acceptance.read_text(encoding="utf-8").replace(
                    "UI_STATES: n/a — módulo sin superficie visual",
                    "UI_STATES: required — ahora aplica la superficie visual",
                    1,
                ),
                encoding="utf-8",
            )
            after = run_cli_query(root, "implementation", "M1")
            self.assertEqual(after.returncode, 0, after.stderr)
            after_result = json.loads(after.stdout)
            self.assertTrue({"ui_states", "ui_evidence"}.issubset(
                set(after_result["required_sections"])
            ))
            self.assertIn("UI_STATES", after_result["required_obligations"])

            stale = run_cli_query(
                root, "implementation", "M1", before_result["next_cursor"]
            )
            self.assertEqual(stale.returncode, 3, stale.stderr)
            self.assertEqual(json.loads(stale.stdout)["error"]["code"], "STALE_CURSOR")
        finally:
            holder.cleanup()

    def test_mcp_transport_session_observes_applicability_edit_without_stale_plan_cache(self):
        holder, root = runtime_context_root(_implementation_state())
        try:
            runtime_module_docs(root, "M1", ui_states="n/a")
            process = subprocess.Popen(
                [sys.executable, "-B", ".agent/scripts/state_manager.py"],
                cwd=root,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,
            )
            try:
                initialized = _mcp_request(process, {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {"protocolVersion": "2025-03-26"},
                })
                self.assertEqual(initialized["id"], 1)
                self.assertIn("result", initialized)

                _mcp_notification(process, {
                    "jsonrpc": "2.0",
                    "method": "notifications/initialized",
                    "params": {},
                })

                tools = _mcp_request(process, {
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": "tools/list",
                    "params": {},
                })
                names = [tool["name"] for tool in tools["result"]["tools"]]
                self.assertIn("get_work_context", names)
                self.assertIn("lookup_state", names)

                first_response = _mcp_request(process, _mcp_tools_call(3))
                self.assertEqual(first_response["id"], 3)
                self.assertIn("result", first_response)
                before = json.loads(
                    first_response["result"]["content"][0]["text"]
                )
                self.assertNotIn("ui_states", before["required_sections"])
                self.assertNotIn("ui_evidence", before["required_sections"])

                acceptance = root / "_project" / "08_module_acceptance.md"
                acceptance.write_text(
                    acceptance.read_text(encoding="utf-8").replace(
                        "UI_STATES: n/a — módulo sin superficie visual",
                        "UI_STATES: required — ahora aplica la superficie visual",
                        1,
                    ),
                    encoding="utf-8",
                )
                second_response = _mcp_request(process, _mcp_tools_call(4))
                self.assertEqual(second_response["id"], 4)
                self.assertIn("result", second_response)
                after = json.loads(
                    second_response["result"]["content"][0]["text"]
                )
            finally:
                if process.stdin is not None and not process.stdin.closed:
                    process.stdin.close()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.terminate()
                    process.wait(timeout=5)
                for stream in (process.stdout, process.stderr):
                    if stream is not None:
                        stream.close()

            self.assertTrue({"ui_states", "ui_evidence"}.issubset(
                set(after["required_sections"])
            ))
            self.assertIn("UI_STATES", after["required_obligations"])
            self.assertNotEqual(
                before["required_sections"], after["required_sections"]
            )
            self.assertNotIn("plan", after)
        finally:
            holder.cleanup()

    def test_real_implementation_query_derives_private_plan_once(self):
        holder, root = runtime_context_root(_implementation_state())
        try:
            runtime_module_docs(root, "M1", ui_states="n/a")
            with temporary_manager(root, _implementation_state()) as manager:
                with mock.patch.object(
                    state_queries, "context_plan", wraps=state_queries.context_plan
                ) as planner:
                    result = state_manager.get_work_context(
                        manager, view="implementation", module_id="M1"
                    )

            self.assertEqual(planner.call_count, 1)
            self.assertNotIn("plan", result)
            persisted = json.loads(
                (root / "_project" / "03_project_state.json").read_text(encoding="utf-8")
            )
            self.assertNotIn("plan", persisted)
        finally:
            holder.cleanup()

    def test_each_public_view_derives_one_private_plan_and_keeps_schema(self):
        state = _implementation_state()
        state["last_completed_step"] = "recuperación de M1"
        holder, root = runtime_context_root(state)
        try:
            runtime_module_docs(root, "M1", ui_states="n/a")
            with temporary_manager(root, state) as manager:
                for view, module_id in (
                    ("startup", None),
                    ("implementation", "M1"),
                    ("closure", "M1"),
                    ("recovery", None),
                ):
                    with self.subTest(view=view):
                        with mock.patch.object(
                            state_queries, "context_plan", wraps=state_queries.context_plan
                        ) as planner:
                            result = state_manager.get_work_context(
                                manager, view=view, module_id=module_id
                            )
                        self.assertEqual(planner.call_count, 1)
                        self.assertEqual(result["schema_version"], 1)
                        self.assertNotIn("plan", result)
        finally:
            holder.cleanup()


if __name__ == "__main__":
    unittest.main()
