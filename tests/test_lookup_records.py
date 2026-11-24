#!/usr/bin/env python3
"""L11 (N7/N8): lookup_state para bugs/clarifications/decisions y resumen de get_full_state."""

import copy
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / ".agent" / "scripts"))

import state_core
import state_manager
import state_queries


def _state(n_bugs: int = 5, desc: str = "descripción") -> dict:
    return {
        "project": "p",
        "modules": {"completed": [], "in_progress": ["M1"], "pending": ["M2"]},
        "module_statuses": {"M1": "in_progress", "M2": "not_started"},
        "_bugs": [
            {"id": f"BUG-{i:03d}", "severity": "Mayor", "status": "Abierto" if i % 2 else "Resuelto",
             "module_id": "M1" if i % 2 else "M2", "description": desc, "fix": "no debe salir"}
            for i in range(n_bugs)
        ],
        "_clarifications": [
            {"id": "CLAR-001", "status": "Pendiente", "blocked_scope": "M1", "blocks_start": False,
             "blocks_implementation": True, "blocks_closure": True, "question": "¿q?", "impact": "no sale"},
        ],
        "_decisions": [
            {"id": "ADR-001", "title": "uno", "status": "Aceptada", "module_id": "framework"},
            {"id": "ADR-002", "title": "dos", "status": "Aceptada", "module_id": ["M1", "M2"]},
            {"id": "ADR-003", "title": "tres", "status": "Superseded", "module_id": "M1"},
        ],
    }


def _size(payload: dict) -> int:
    return len(json.dumps(payload, indent=2, ensure_ascii=False).encode("utf-8"))


class RecordKindsTests(unittest.TestCase):
    def test_bugs_minimal_fields_and_filters(self):
        st = _state()
        r = state_queries.query_state_point(st, kind="bugs", module_id="M1", status="Abierto")
        self.assertEqual([i["id"] for i in r["items"]], ["BUG-001", "BUG-003"])
        self.assertEqual(set(r["items"][0]), {"id", "severity", "status", "module_id", "description"})
        self.assertNotIn("truncated", r)

    def test_bug_description_clipped_to_200(self):
        st = _state(1, desc="x" * 500)
        r = state_queries.query_state_point(st, kind="bugs")
        self.assertLessEqual(len(r["items"][0]["description"]), 200)

    def test_clarifications_fields_and_filter(self):
        r = state_queries.query_state_point(_state(), kind="clarifications", module_id="M1")
        self.assertEqual(r["items"][0]["id"], "CLAR-001")
        self.assertIn("blocks_closure", r["items"][0])
        self.assertNotIn("impact", r["items"][0])

    def test_decisions_literal_filter_and_list_reference(self):
        st = _state()
        lit = state_queries.query_state_point(st, kind="decisions", module_id="framework")
        self.assertEqual([i["id"] for i in lit["items"]], ["ADR-001"])
        lst = state_queries.query_state_point(st, kind="decisions", module_id="M1")
        self.assertEqual([i["id"] for i in lst["items"]], ["ADR-002", "ADR-003"])

    def test_decisions_unknown_module_is_not_validated(self):
        r = state_queries.query_state_point(_state(), kind="decisions", module_id="no_es_modulo")
        self.assertEqual(r["items"], [])
        self.assertEqual(r["total"], 0)

    def test_bugs_unknown_module_is_invalid_target(self):
        with self.assertRaises(state_queries.QueryError) as ctx:
            state_queries.query_state_point(_state(), kind="bugs", module_id="M9_typo")
        self.assertEqual(ctx.exception.code, "INVALID_TARGET")

    def test_clarifications_unknown_module_is_invalid_target(self):
        with self.assertRaises(state_queries.QueryError) as ctx:
            state_queries.query_state_point(_state(), kind="clarifications", module_id="M9_typo")
        self.assertEqual(ctx.exception.code, "INVALID_TARGET")

    def test_invalid_offset(self):
        for bad in (-1, True, "2", 99):
            with self.assertRaises(state_queries.QueryError, msg=repr(bad)) as ctx:
                state_queries.query_state_point(_state(), kind="bugs", offset=bad)
            self.assertEqual(ctx.exception.code, "INVALID_TARGET")

    def test_pure_does_not_mutate_state(self):
        st = _state(300, desc="y" * 300)
        before = copy.deepcopy(st)
        for kind in ("bugs", "clarifications", "decisions"):
            state_queries.query_state_point(st, kind=kind)
        self.assertEqual(st, before)


class TruncationTests(unittest.TestCase):
    def test_truncation_fits_cap_and_pages_with_next_call(self):
        st = _state(400, desc="z" * 300)
        first = state_queries.query_state_point(st, kind="bugs")
        self.assertLessEqual(_size(first), 12 * 1024)
        self.assertTrue(first["truncated"])
        self.assertEqual(first["total"], 400)
        call = first["next_call"]
        self.assertEqual(call["tool"], "lookup_state")
        self.assertEqual(call["arguments"], {"kind": "bugs", "offset": first["returned"]})
        seen = [i["id"] for i in first["items"]]
        page = first
        while "next_call" in page:
            page = state_queries.query_state_point(st, **page["next_call"]["arguments"])
            self.assertLessEqual(_size(page), 12 * 1024)
            seen += [i["id"] for i in page["items"]]
        self.assertEqual(seen, [f"BUG-{i:03d}" for i in range(400)])
        self.assertNotIn("truncated", page)

    def test_next_call_keeps_filters(self):
        st = _state(400, desc="z" * 300)
        first = state_queries.query_state_point(st, kind="bugs", module_id="M1", status="Abierto")
        self.assertEqual(first["next_call"]["arguments"]["module_id"], "M1")
        self.assertEqual(first["next_call"]["arguments"]["status"], "Abierto")

    def test_single_row_that_does_not_fit_is_returned_clipped(self):
        st = _state(3, desc="L" * 5000)
        r = state_queries.query_state_point(st, kind="bugs", max_bytes=500)
        self.assertGreaterEqual(r["returned"], 1)
        self.assertTrue(r["truncated"])
        self.assertTrue(r["row_clipped"])
        self.assertLessEqual(_size(r), 500)
        self.assertEqual(r["items"][0]["id"], "BUG-000")
        self.assertIn("next_call", r)


class FullStateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        shutil.copytree(ROOT / ".agent", root / ".agent",
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        (root / "_project").mkdir()
        self.state_file = root / "_project" / "03_project_state.json"
        self.orig = (state_core.WORKSPACE_ROOT, state_core.STATE_FILE_PATH)
        state_core.WORKSPACE_ROOT = root
        state_core.STATE_FILE_PATH = self.state_file

    def tearDown(self):
        state_core.WORKSPACE_ROOT, state_core.STATE_FILE_PATH = self.orig
        self.tmp.cleanup()

    def _call(self, name, arguments, state):
        self.state_file.write_text(json.dumps(state, indent=2), encoding="utf-8")
        manager = state_manager.StateManager()
        resp = state_manager.handle_mcp_request(manager, {
            "jsonrpc": "2.0", "id": 1, "method": "tools/call",
            "params": {"name": name, "arguments": arguments}})
        return resp["result"]["content"][0]["text"]

    def test_big_state_without_force_returns_summary_with_exact_calls(self):
        st = _state(1500, desc="w" * 300)
        text = self._call("get_full_state", {}, st)
        self.assertLess(len(text.encode("utf-8")), 16 * 1024)
        summary = json.loads(text)
        self.assertTrue(summary["summary_only"])
        self.assertEqual(summary["counts"]["bugs"], 1500)
        self.assertEqual(summary["active_modules"], ["M1"])
        self.assertEqual(summary["next_calls"]["full_dump"],
                         {"tool": "get_full_state", "arguments": {"force": True}})

    def test_big_state_with_force_returns_full_dump(self):
        st = _state(1500, desc="w" * 300)
        full = json.loads(self._call("get_full_state", {"force": True}, st))
        self.assertEqual(len(full["_bugs"]), 1500)
        self.assertNotIn("summary_only", full)

    def test_small_state_returns_full_dump_without_force(self):
        full = json.loads(self._call("get_full_state", {}, _state(3)))
        self.assertEqual(len(full["_bugs"]), 3)

    def test_mcp_offset_and_cli_parity_for_new_kind(self):
        st = _state(400, desc="q" * 300)
        mcp = json.loads(self._call("lookup_state", {"kind": "bugs", "offset": 10}, st))
        self.assertEqual(mcp["items"][0]["id"], "BUG-010")
        out = subprocess.run(
            [sys.executable, "-B", str(state_core.WORKSPACE_ROOT / ".agent" / "scripts" / "state_manager.py"),
             "lookup", "--kind", "bugs", "--offset", "10"],
            capture_output=True, text=True, stdin=subprocess.DEVNULL,
            cwd=str(state_core.WORKSPACE_ROOT), timeout=60)
        self.assertEqual(json.loads(out.stdout), mcp)


if __name__ == "__main__":
    unittest.main()
