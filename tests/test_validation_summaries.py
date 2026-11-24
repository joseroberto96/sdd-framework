#!/usr/bin/env python3
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / ".agent" / "scripts"))

from validation_summary import summarize_result


class ValidationSummaryTests(unittest.TestCase):
    def test_pass_requires_zero_exit_and_complete_contract(self):
        result = summarize_result(
            tool="doctor", exit_code=0,
            parsed={"status": "pass", "failures": [], "warnings": ["baseline"]},
            evidence_ref="evidence/doctor.json",
        )
        self.assertEqual(result["status"], "PASS")
        self.assertEqual(result["failure_count"], 0)
        self.assertEqual(result["warnings"], ["baseline"])

    def test_text_ok_cannot_hide_nonzero_exit(self):
        result = summarize_result(
            tool="doctor", exit_code=1,
            parsed={"status": "pass", "failures": [], "warnings": []},
            evidence_ref="doctor.log",
        )
        self.assertEqual(result["status"], "FAIL")
        self.assertEqual(result["exit_code"], 1)

    def test_zero_exit_with_incomplete_result_is_error(self):
        result = summarize_result(tool="doctor", exit_code=0, parsed={"status": "pass"}, evidence_ref="x")
        self.assertEqual(result["status"], "ERROR")

    def test_failure_list_is_preserved_and_counted(self):
        result = summarize_result(
            tool="closure", exit_code=0,
            parsed={"status": "fail", "failures": ["AC-1", "review"], "warnings": []},
            evidence_ref="closure.log",
        )
        self.assertEqual(result["status"], "FAIL")
        self.assertEqual(result["failure_count"], 2)
        self.assertEqual(result["failures"], ["AC-1", "review"])

    def test_unavailable_or_timeout_is_not_pass(self):
        result = summarize_result(tool="runtime", exit_code=None, parsed=None, evidence_ref="missing")
        self.assertEqual(result["status"], "ERROR")
        self.assertEqual(result["failure_count"], 1)


if __name__ == "__main__":
    unittest.main()
