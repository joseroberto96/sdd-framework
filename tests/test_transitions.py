#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import unittest
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / ".agent" / "scripts"))
import _transitions


class TestTransitions(unittest.TestCase):
    def test_legal_prev_shape(self):
        self.assertEqual(
            _transitions.LEGAL_PREV["completed"], {"ready_for_validation", "completed"}
        )
        self.assertIn(None, _transitions.LEGAL_PREV["not_started"])

    def test_is_legal_true(self):
        self.assertTrue(_transitions.is_legal("in_progress", "ready_for_validation"))

    def test_is_legal_false(self):
        self.assertFalse(_transitions.is_legal("ready_for_validation", "blocked"))

    def test_unknown_target_is_illegal(self):
        self.assertFalse(_transitions.is_legal("in_progress", "bogus"))

    def test_none_prev_is_legal_for_not_started(self):
        self.assertTrue(_transitions.is_legal(None, "not_started"))
        self.assertFalse(_transitions.is_legal(None, "completed"))


if __name__ == "__main__":
    unittest.main()
