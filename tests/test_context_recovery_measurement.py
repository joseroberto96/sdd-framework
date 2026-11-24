from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / ".agent" / "scripts"))
sys.path.insert(0, str(ROOT / "tests"))

import context_measurement
import state_queries

from test_framework_corrections import (
    run_cli_query,
    run_mcp_query,
    runtime_context_root,
    fixture_state,
    runtime_module_docs,
    write_valid_resolution_history,
)


class ContextRecoveryMeasurementTests(unittest.TestCase):
    _PRIVATE_TELEMETRY_KEYS = {
        "codex_window",
        "context_window",
        "private_window",
        "window_tokens",
    }

    def _large_recovery_root(self, count: int = 900):
        state = fixture_state()
        state["last_completed_step"] = "recuperación de M1"
        holder, root = runtime_context_root(state)
        records = [
            f"- [2026-09-10T00:{index // 60:02d}:{index % 60:02d}Z] "
            f"[tipo: test] [M1] recovery-record-{index:04d}\n"
            for index in range(count)
        ]
        journal = (
            "# Recovery journal\n\n"
            "## Mandatory delivery records\n"
            + "".join(records)
        )
        (root / "_project" / "13_session_journal.md").write_text(
            journal, encoding="utf-8"
        )
        return holder, root, journal, records

    def _collect_cli(self, root: Path):
        pages = []
        cursor = None
        while True:
            process = run_cli_query(root, "recovery", cursor=cursor)
            self.assertEqual(process.returncode, 0, process.stderr)
            page = json.loads(process.stdout)
            pages.append((process, page))
            if page["complete"]:
                return pages
            cursor = page["next_cursor"]

    def _collect_mcp(self, root: Path):
        pages = []
        cursor = None
        while True:
            process, page, responses = run_mcp_query(
                root, "recovery", cursor=cursor
            )
            self.assertEqual(process.returncode, 0, process.stderr)
            self.assertTrue(any(response.get("id") == 1 for response in responses))
            self.assertTrue(any(response.get("id") == 2 for response in responses))
            pages.append((process, page, responses))
            if page["complete"]:
                return pages
            cursor = page["next_cursor"]

    @staticmethod
    def _journal_from_pages(pages):
        fragments = [
            item for _, page, *rest in pages
            for item in page["data"]["items"]
            if item.get("path") == "_project/13_session_journal.md"
        ]
        return "".join(
            item["content"]
            for item in sorted(fragments, key=lambda item: item["fragment_index"])
        )

    def _assert_private_telemetry_keys_absent(self, value, path="report"):
        if isinstance(value, dict):
            for key, nested in value.items():
                self.assertNotIn(
                    key,
                    self._PRIVATE_TELEMETRY_KEYS,
                    f"private telemetry key leaked at {path}.{key}",
                )
                self._assert_private_telemetry_keys_absent(nested, f"{path}.{key}")
        elif isinstance(value, list):
            for index, nested in enumerate(value):
                self._assert_private_telemetry_keys_absent(nested, f"{path}[{index}]")

    def test_real_recovery_pagination_reconstructs_journal_and_separates_mcp_envelope(self):
        holder, root, journal, records = self._large_recovery_root()
        try:
            cli_pages = self._collect_cli(root)
            mcp_pages = self._collect_mcp(root)
            self.assertGreater(len(cli_pages), 1)
            self.assertGreater(len(mcp_pages), 1)
            for _, page in cli_pages:
                self.assertLessEqual(
                    state_queries.serialized_context_size(page),
                    state_queries.VIEW_LIMITS["recovery"],
                )
            for _, page, _ in mcp_pages:
                self.assertLessEqual(
                    state_queries.serialized_context_size(page),
                    state_queries.VIEW_LIMITS["recovery"],
                )
            self.assertEqual(self._journal_from_pages(cli_pages), journal)
            self.assertEqual(self._journal_from_pages(mcp_pages), journal)
            self.assertEqual(
                [line for line in self._journal_from_pages(cli_pages).splitlines(True)
                 if "[tipo: test]" in line],
                records,
            )

            report = context_measurement.measure_transport(
                root, view="recovery", transport="mcp"
            )
            self.assertGreater(report["initialization_wire_bytes"], 0)
            self.assertGreater(report["discovery_wire_bytes"], 0)
            self.assertGreater(report["call_wire_bytes"], 0)
            self.assertGreater(report["wire_bytes"], report["total_data_bytes"])
            self.assertEqual(
                report["wire_bytes"], report["call_wire_bytes"],
            )
        finally:
            holder.cleanup()

    def test_recovery_sufficiency_is_false_until_final_required_page(self):
        holder, root, _, _ = self._large_recovery_root()
        try:
            pages = self._collect_cli(root)
            mcp_pages = self._collect_mcp(root)
            for sequence_name, sequence in (("CLI", pages), ("MCP", mcp_pages)):
                with self.subTest(sequence=sequence_name):
                    self.assertTrue(all(
                        not page["required_context_complete"] and not page["complete"]
                        for _, page, *_ in sequence[:-1]
                    ))
                    # gate_status describes the full query gate, while page
                    # completeness is a separate transport contract.  A
                    # global PASS cannot be consumed as final evidence until
                    # both page markers are true.
                    self.assertTrue(all(
                        page["gate_status"] == "PASS"
                        and not page["required_context_complete"]
                        for _, page, *_ in sequence[:-1]
                    ))
            self.assertTrue(pages[-1][1]["complete"])
            self.assertTrue(pages[-1][1]["required_context_complete"])
            self.assertEqual(pages[-1][1]["gate_status"], "PASS")
            self.assertTrue(mcp_pages[-1][1]["complete"])
            self.assertTrue(mcp_pages[-1][1]["required_context_complete"])
            self.assertEqual(mcp_pages[-1][1]["gate_status"], "PASS")
            self.assertTrue(any(
                item.get("path") == "_project/13_session_journal.md"
                for _, page in pages[-1:]
                for item in page["data"]["items"]
            ))
        finally:
            holder.cleanup()

    def test_external_json_measurement_report_has_views_and_separate_categories(self):
        state = fixture_state()
        state["last_completed_step"] = "recuperación de M1"
        holder, root = runtime_context_root(state)
        output_holder = tempfile.TemporaryDirectory()
        try:
            runtime_module_docs(root, "M1")
            write_valid_resolution_history(root, state, count=1)
            views = {
                "startup": None,
                "implementation": "M1",
                "closure": "M1",
                "recovery": None,
            }
            measurements = {}
            commands = []
            return_codes = {}
            for view, module_id in views.items():
                output = Path(output_holder.name) / f"{view}.json"
                command = [
                    sys.executable, "-B", ".agent/scripts/context_measurement.py",
                    "--root", str(root), "--view", view, "--transport", "mcp",
                    "--output", str(output),
                ]
                if module_id is not None:
                    command.extend(("--module-id", module_id))
                commands.append(command)
                process = subprocess.run(
                    command, cwd=ROOT, capture_output=True, text=True,
                    timeout=30, check=False,
                )
                return_codes[view] = process.returncode
                self.assertEqual(process.returncode, 0, process.stderr)
                measurements[view] = json.loads(output.read_text(encoding="utf-8"))

            report_path = Path(output_holder.name) / "context-measurement-report.json"
            report = {
                "transport": "mcp",
                "views": measurements,
                "categories": {
                    "output": {view: data["total_data_bytes"] for view, data in measurements.items()},
                    "read": {view: data["read_bytes"] for view, data in measurements.items()},
                    "cache": {view: data["cache_hit_count"] for view, data in measurements.items()},
                    "startup": measurements["startup"]["initialization_wire_bytes"],
                    "history": {
                        view: data["read_bytes_by_category"].get("history_log", 0)
                        for view, data in measurements.items()
                    },
                },
                "commands": [" ".join(command) for command in commands],
                "return_codes": return_codes,
                "telemetry_scope": "real CLI/MCP transport and external read traces only",
            }
            report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            saved = json.loads(report_path.read_text(encoding="utf-8"))
            self.assertEqual(set(saved["views"]), set(views))
            self.assertEqual(saved["return_codes"], {view: 0 for view in views})
            self.assertEqual(
                set(saved["categories"]), {"output", "read", "cache", "startup", "history"}
            )
            self.assertTrue(all(data["read_source"] == "observed_trace" for data in measurements.values()))
            self.assertTrue(any(data["read_bytes_by_category"].get("history_log", 0) > 0 for data in measurements.values()))
            self._assert_private_telemetry_keys_absent(saved)
        finally:
            holder.cleanup()
            output_holder.cleanup()


if __name__ == "__main__":
    unittest.main()
