#!/usr/bin/env python3
"""Regresiones del lector persistente JSONL de app-server."""
from __future__ import annotations

import subprocess
import sys
import time
import unittest

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / ".agent" / "scripts"))

import codex_app_server_reader as reader  # noqa: E402


class AppServerReaderTests(unittest.TestCase):
    def _spawn(self, source: str) -> subprocess.Popen[bytes]:
        return subprocess.Popen(
            [sys.executable, "-c", source],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

    def _cleanup(self, process: subprocess.Popen[bytes]) -> None:
        if process.poll() is None:
            process.terminate()
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=2)
        for stream in (process.stdin, process.stdout, process.stderr):
            if stream is not None:
                stream.close()

    def test_reads_two_lines_from_one_write_without_waiting_for_eof(self):
        process = self._spawn(
            "import os, time; os.write(1, b'{\"id\":1}\\n{\"id\":2}\\n'); time.sleep(1)"
        )
        self.addCleanup(self._cleanup, process)
        jsonl = reader.AppServerReader(process.stdout, process.stderr)

        first = jsonl.read_message(time.monotonic() + 1)
        second = jsonl.read_message(time.monotonic() + 1)

        self.assertEqual(first, {"id": 1})
        self.assertEqual(second, {"id": 2})

    def test_utf8_split_across_reads_is_decoded_after_reassembly(self):
        process = self._spawn(
            "import os, time; os.write(1, b'{\"text\":\"caf\\xc3'); time.sleep(.05); os.write(1, b'\\xa9\"}\\n'); time.sleep(1)"
        )
        self.addCleanup(self._cleanup, process)
        jsonl = reader.AppServerReader(process.stdout, process.stderr)

        self.assertEqual(jsonl.read_message(time.monotonic() + 1), {"text": "café"})

    def test_partial_eof_is_a_protocol_error(self):
        process = self._spawn("import os; os.write(1, b'{\"partial\":')")
        self.addCleanup(self._cleanup, process)
        jsonl = reader.AppServerReader(process.stdout, process.stderr)

        with self.assertRaises(reader.ReaderProtocolError):
            jsonl.read_message(time.monotonic() + 1)

    def test_clean_eof_returns_none_after_complete_lines_are_consumed(self):
        process = self._spawn("import os; os.write(1, b'{\"id\":1}\\n')")
        self.addCleanup(self._cleanup, process)
        jsonl = reader.AppServerReader(process.stdout, process.stderr)

        self.assertEqual(jsonl.read_message(time.monotonic() + 1), {"id": 1})
        self.assertIsNone(jsonl.read_message(time.monotonic() + 1))

    def test_line_and_stdout_budgets_fail_without_discarding_bytes(self):
        process = self._spawn("import os; os.write(1, b'{\"long\":true}\\n')")
        self.addCleanup(self._cleanup, process)
        jsonl = reader.AppServerReader(process.stdout, process.stderr, max_line_bytes=4)
        with self.assertRaises(reader.ReaderLimitError):
            jsonl.read_message(time.monotonic() + 1)

        process2 = self._spawn("import os; os.write(1, b'123456789\\n')")
        self.addCleanup(self._cleanup, process2)
        jsonl2 = reader.AppServerReader(process2.stdout, process2.stderr, max_stdout_bytes=5)
        with self.assertRaises(reader.ReaderLimitError):
            jsonl2.read_message(time.monotonic() + 1)

    def test_invalid_utf8_and_json_are_explicit_protocol_errors(self):
        process = self._spawn("import os; os.write(1, b'\\xff\\n')")
        self.addCleanup(self._cleanup, process)
        jsonl = reader.AppServerReader(process.stdout, process.stderr)
        with self.assertRaises(reader.ReaderProtocolError):
            jsonl.read_message(time.monotonic() + 1)

        process2 = self._spawn("import os; os.write(1, b'not-json\\n')")
        self.addCleanup(self._cleanup, process2)
        jsonl2 = reader.AppServerReader(process2.stdout, process2.stderr)
        with self.assertRaises(reader.ReaderProtocolError):
            jsonl2.read_message(time.monotonic() + 1)

    def test_stderr_keeps_only_the_tail(self):
        process = self._spawn(
            "import os; os.write(2, b'a' * 70000); os.write(1, b'{\"ok\":true}\\n')"
        )
        self.addCleanup(self._cleanup, process)
        jsonl = reader.AppServerReader(process.stdout, process.stderr)

        self.assertEqual(jsonl.read_message(time.monotonic() + 1), {"ok": True})
        jsonl.drain_stderr(time.monotonic() + 1)
        self.assertEqual(len(jsonl.stderr_tail_bytes), 64 * 1024)
        self.assertEqual(jsonl.stderr_tail(), "a" * (64 * 1024))


if __name__ == "__main__":
    unittest.main()
