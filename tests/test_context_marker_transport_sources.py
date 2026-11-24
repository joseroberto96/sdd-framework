import json
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))

from test_framework_corrections import (  # noqa: E402
    fixture_state,
    run_cli_query,
    run_mcp_query,
    runtime_context_root,
    runtime_module_docs,
)


class ContextMarkerTransportSourceTests(unittest.TestCase):
    SOURCE = Path(".agent/workflows/cambio-seguro.md")
    PRESERVE_START = "<!-- CONTEXT_SOURCE: preserve_inventory -->"
    PRESERVE_END = "<!-- END_CONTEXT_SOURCE: preserve_inventory -->"
    SECURITY_START = "<!-- CONTEXT_SOURCE: security_surface -->"
    SECURITY_END = "<!-- END_CONTEXT_SOURCE: security_surface -->"

    def _state(self):
        state = fixture_state()
        state["module_specs"]["M1"] = {
            "data_model": "n/a",
            "api_contract": "n/a",
            "justification": "fixture sin persistencia ni HTTP",
            "classified_at": "2026-09-10T00:00:00Z",
            "classified_by": "fixture",
        }
        return state

    def _root(self, *, ui_states="n/a"):
        holder, root = runtime_context_root(self._state())
        runtime_module_docs(root, "M1", ui_states=ui_states)
        return holder, root

    def _query_both(self, root, view="implementation"):
        cli_process = run_cli_query(root, view, "M1")
        mcp_process, mcp_result, responses = run_mcp_query(root, view, "M1")
        return cli_process, mcp_process, json.loads(cli_process.stdout), mcp_result, responses

    def _replace_preserve_with_fenced_example(self, root):
        path = root / self.SOURCE
        content = path.read_text(encoding="utf-8")
        pattern = re.compile(
            re.escape(self.PRESERVE_START) + r".*?" + re.escape(self.PRESERVE_END),
            re.DOTALL,
        )
        replacement = (
            "~~~markdown\n"
            f"{self.PRESERVE_START}\n"
            "PRESERVAR — ejemplo no normativo\n"
            f"{self.PRESERVE_END}\n"
            "~~~"
        )
        updated, count = pattern.subn(replacement, content, count=1)
        self.assertEqual(count, 1)
        path.write_text(updated, encoding="utf-8")

    def _append_ui_marker_example(self, root):
        path = root / "_project/08_module_acceptance.md"
        with path.open("a", encoding="utf-8") as stream:
            stream.write(
                "\n<!-- CONTEXT_SOURCE: generic_example -->\n"
                "ejemplo normativo junto a evidencia visible válida\n"
                "<!-- END_CONTEXT_SOURCE: generic_example -->\n"
            )

    def _append_active_and_hidden_preserve_copy(self, root, *, active_copies=1):
        path = root / self.SOURCE
        content = path.read_text(encoding="utf-8")
        pattern = re.compile(
            re.escape(self.PRESERVE_START) + r".*?" + re.escape(self.PRESERVE_END),
            re.DOTALL,
        )
        blocks = "".join(
            f"\n{self.PRESERVE_START}\n"
            f"inventario activo {index}\n"
            f"{self.PRESERVE_END}\n"
            for index in range(active_copies)
        )
        blocks += (
            "\n```markdown\n"
            f"{self.PRESERVE_START}\n"
            "copia oculta idéntica\n"
            f"{self.PRESERVE_END}\n"
            "```\n"
        )
        updated, count = pattern.subn(blocks.rstrip("\n"), content, count=1)
        self.assertEqual(count, 1)
        path.write_text(updated, encoding="utf-8")

    def test_valid_baseline_and_restoration_are_pass_on_cli_and_mcp(self):
        holder, root = self._root()
        try:
            cli, mcp_process, cli_result, mcp_result, _ = self._query_both(root)
            self.assertEqual(cli.returncode, 0, cli.stderr)
            self.assertEqual(mcp_process.returncode, 0, mcp_process.stderr)
            self.assertEqual(cli_result["gate_status"], "PASS")
            self.assertTrue(cli_result["required_context_complete"])
            self.assertEqual(cli_result, mcp_result)
        finally:
            holder.cleanup()

    def test_fenced_preserve_source_needs_input_until_restored_on_both_transports(self):
        holder, root = self._root()
        try:
            self._replace_preserve_with_fenced_example(root)
            cli, mcp_process, cli_result, mcp_result, _ = self._query_both(root)
            for process in (cli, mcp_process):
                self.assertEqual(process.returncode, 0, process.stderr)
            for result in (cli_result, mcp_result):
                self.assertEqual(result["gate_status"], "NEEDS_INPUT")
                self.assertFalse(result["required_context_complete"])
                self.assertTrue(any(
                    ref.get("obligation") == "PRESERVE"
                    and ref.get("reason") == "missing_source"
                    for ref in result["refs"]
                ), result["refs"])

            source = root / self.SOURCE
            content = source.read_text(encoding="utf-8")
            fenced = (
                "~~~markdown\n"
                f"{self.PRESERVE_START}\n"
                "PRESERVAR — ejemplo no normativo\n"
                f"{self.PRESERVE_END}\n"
                "~~~"
            )
            original = (
                f"{self.PRESERVE_START}\n"
                "PRESERVAR es un inventario restaurado para la incursión.\n"
                f"{self.PRESERVE_END}"
            )
            self.assertIn(fenced, content)
            source.write_text(content.replace(fenced, original, 1), encoding="utf-8")
            cli, mcp_process, cli_result, mcp_result, _ = self._query_both(root)
            self.assertEqual(cli.returncode, 0, cli.stderr)
            self.assertEqual(mcp_process.returncode, 0, mcp_process.stderr)
            self.assertEqual(cli_result["gate_status"], "PASS")
            self.assertTrue(cli_result["required_context_complete"])
            self.assertEqual(cli_result, mcp_result)
            self.assertFalse(any(
                ref.get("obligation") == "PRESERVE" and ref.get("reason") == "missing_source"
                for ref in cli_result["refs"]
            ))
        finally:
            holder.cleanup()

    def test_active_normative_marker_keeps_visible_ui_evidence_invalid(self):
        holder, root = self._root(ui_states="required")
        try:
            self._append_ui_marker_example(root)
            cli, mcp_process, cli_result, mcp_result, _ = self._query_both(root)
            self.assertEqual(cli.returncode, 0, cli.stderr)
            self.assertEqual(mcp_process.returncode, 0, mcp_process.stderr)
            for result in (cli_result, mcp_result):
                self.assertEqual(result["gate_status"], "NEEDS_INPUT")
                self.assertFalse(result["required_context_complete"])
                self.assertTrue(any(
                    ref.get("obligation") == "UI_STATES"
                    and ref.get("reason") == "invalid_evidence"
                    for ref in result["refs"]
                ), result["refs"])
            self.assertEqual(cli_result, mcp_result)
        finally:
            holder.cleanup()

    def test_active_marker_with_hidden_copy_is_valid_but_two_active_copies_are_ambiguous(self):
        holder, root = self._root()
        try:
            self._append_active_and_hidden_preserve_copy(root)
            cli, mcp_process, cli_result, mcp_result, _ = self._query_both(root)
            self.assertEqual(cli.returncode, 0, cli.stderr)
            self.assertEqual(mcp_process.returncode, 0, mcp_process.stderr)
            self.assertEqual(cli_result["gate_status"], "PASS")
            self.assertTrue(cli_result["required_context_complete"])
            self.assertEqual(cli_result, mcp_result)

            with (root / self.SOURCE).open("a", encoding="utf-8") as stream:
                stream.write(
                    f"\n{self.PRESERVE_START}\n"
                    "segundo inventario activo\n"
                    f"{self.PRESERVE_END}\n"
                )
            cli = run_cli_query(root, "implementation", "M1")
            self.assertEqual(cli.returncode, 3)
            self.assertEqual(json.loads(cli.stdout)["error"]["code"], "SOURCE_AMBIGUOUS")

            mcp_process, _, responses = run_mcp_query(root, "implementation", "M1")
            self.assertEqual(mcp_process.returncode, 0, mcp_process.stderr)
            call = next(response for response in responses if response.get("id") == 3)
            self.assertTrue(call["result"]["isError"])
            structured = json.loads(call["result"]["content"][0]["text"])
            self.assertEqual(structured["error"]["code"], "SOURCE_AMBIGUOUS")
        finally:
            holder.cleanup()

    def test_cursor_is_stale_after_hidden_marker_copy_content_changes(self):
        holder, root = self._root()
        try:
            source = root / self.SOURCE
            content = source.read_text(encoding="utf-8")
            hidden_content = "hidden marker copy v1\n" + ("x" * 30000)
            hidden_copy = (
                "```markdown\n"
                f"{self.PRESERVE_START}\n"
                f"{hidden_content}"
                f"{self.PRESERVE_END}\n"
                "```"
            )
            updated, count = re.subn(
                re.escape(self.PRESERVE_END),
                hidden_copy + "\n" + self.PRESERVE_END,
                content,
                count=1,
            )
            self.assertEqual(count, 1)
            source.write_text(updated, encoding="utf-8")

            first_process = run_cli_query(root, "implementation", "M1")
            self.assertEqual(first_process.returncode, 0, first_process.stderr)
            first_page = json.loads(first_process.stdout)
            self.assertEqual(first_page["gate_status"], "PASS")
            self.assertFalse(first_page["complete"])
            cursor = first_page["next_cursor"]
            self.assertIsInstance(cursor, str)
            self.assertIn(self.SECURITY_START, updated)
            self.assertIn(self.SECURITY_END, updated)

            changed = updated.replace("hidden marker copy v1", "hidden marker copy v2", 1)
            self.assertNotEqual(changed, updated)
            source.write_text(changed, encoding="utf-8")

            stale_process = run_cli_query(root, "implementation", "M1", cursor)
            self.assertEqual(stale_process.returncode, 3, stale_process.stderr)
            stale_error = json.loads(stale_process.stdout)
            self.assertEqual(stale_error["error"]["code"], "STALE_CURSOR")
        finally:
            holder.cleanup()


if __name__ == "__main__":
    unittest.main()
