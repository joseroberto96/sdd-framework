#!/usr/bin/env python3
import os
import subprocess
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class TestCollectionEntrypointTests(unittest.TestCase):
    def test_progressive_context_modules_run_without_pythonpath_override(self):
        environment = os.environ.copy()
        environment.pop("PYTHONPATH", None)
        result = subprocess.run(
            [
                sys.executable,
                "-B",
                "-m",
                "unittest",
                "-q",
                "tests.test_state_queries_transport",
                "tests.test_progressive_context_fixes",
            ],
            cwd=ROOT,
            env=environment,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
