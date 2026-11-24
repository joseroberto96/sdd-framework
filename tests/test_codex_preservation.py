#!/usr/bin/env python3
"""Regresiones para el inventario de preservación del smoke Codex."""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

import sys

sys.path.insert(0, str(ROOT / ".agent" / "scripts"))
import codex_preservation as preservation  # noqa: E402


class PreservationSnapshotTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="codex-preservation-test-"))
        self.external = Path(tempfile.mkdtemp(prefix="codex-preservation-target-"))
        self.addCleanup(lambda: shutil.rmtree(self.tmp, ignore_errors=True))
        self.addCleanup(lambda: shutil.rmtree(self.external, ignore_errors=True))

    def test_detects_content_change_even_when_file_was_already_dirty(self):
        target = self.tmp / "product.txt"
        target.write_text("dirty before\n", encoding="utf-8")

        before = preservation.capture_snapshot(self.tmp)
        target.write_text("dirty after\n", encoding="utf-8")
        after = preservation.capture_snapshot(self.tmp)

        result = preservation.compare_snapshots(before, after)

        self.assertEqual(result["status"], "fail", result)
        self.assertIn("product.txt", result["changed_paths"])

    def test_detects_untracked_and_ignored_content_changes(self):
        ignored = self.tmp / "ignored.txt"
        (self.tmp / ".gitignore").write_text("ignored.txt\n", encoding="utf-8")
        ignored.write_text("before\n", encoding="utf-8")

        before = preservation.capture_snapshot(self.tmp)
        ignored.write_text("after\n", encoding="utf-8")
        after = preservation.capture_snapshot(self.tmp)

        self.assertEqual(preservation.compare_snapshots(before, after)["status"], "fail")

    def test_tracked_file_inside_excluded_directory_remains_in_inventory(self):
        target = self.tmp / "node_modules" / "kept.txt"
        target.parent.mkdir(parents=True)
        target.write_text("before\n", encoding="utf-8")
        subprocess.run(["git", "init", "-q"], cwd=self.tmp, check=True)
        env = {**os.environ, "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_SYSTEM": "/dev/null"}
        subprocess.run(["git", "add", "node_modules/kept.txt"], cwd=self.tmp, check=True, env=env)
        subprocess.run(
            ["git", "-c", "user.name=preservation", "-c", "user.email=preservation@example.invalid", "commit", "-qm", "fixture"],
            cwd=self.tmp,
            check=True,
            env=env,
        )

        before = preservation.capture_snapshot(self.tmp)
        target.write_text("after\n", encoding="utf-8")
        after = preservation.capture_snapshot(self.tmp)

        result = preservation.compare_snapshots(before, after)
        self.assertEqual(result["status"], "fail", result)
        self.assertIn("node_modules/kept.txt", result["changed_paths"])

    def test_symlink_is_recorded_without_following_target(self):
        target = self.external / "outside.txt"
        target.write_text("before\n", encoding="utf-8")
        link = self.tmp / "link.txt"
        link.symlink_to(target)

        before = preservation.capture_snapshot(self.tmp)
        target.write_text("after\n", encoding="utf-8")
        after = preservation.capture_snapshot(self.tmp)

        self.assertEqual(preservation.compare_snapshots(before, after)["status"], "pass")
        self.assertEqual(before["entries"]["link.txt"]["kind"], "symlink")

    def test_entry_budget_is_unavailable_instead_of_silently_excluding_files(self):
        (self.tmp / "one.txt").write_text("one\n", encoding="utf-8")
        (self.tmp / "two.txt").write_text("two\n", encoding="utf-8")

        result = preservation.capture_snapshot(self.tmp, max_entries=1)

        self.assertEqual(result["status"], "unavailable", result)
        self.assertIn("límite", result["detail"])

    def test_byte_budget_is_unavailable_instead_of_truncating_a_hash(self):
        (self.tmp / "large.txt").write_text("0123456789\n", encoding="utf-8")

        result = preservation.capture_snapshot(self.tmp, max_bytes=1)

        self.assertEqual(result["status"], "unavailable", result)
        self.assertIn("límite", result["detail"])

    def test_transient_allowlist_does_not_exclude_the_rest_of_codex(self):
        codex = self.tmp / ".codex"
        codex.mkdir()
        kept = codex / "preexisting.txt"
        kept.write_text("before\n", encoding="utf-8")
        fixture = codex / "smoke-fixture" / "edit.md"
        fixture.parent.mkdir()
        fixture.write_text("fixture\n", encoding="utf-8")

        before = preservation.capture_snapshot(self.tmp, (fixture.parent,))
        kept.write_text("after\n", encoding="utf-8")
        fixture.unlink()
        fixture.parent.rmdir()
        after = preservation.capture_snapshot(self.tmp, (fixture.parent,))

        result = preservation.compare_snapshots(before, after)
        self.assertEqual(result["status"], "fail", result)
        self.assertIn(".codex/preexisting.txt", result["changed_paths"])

    def test_special_file_makes_capture_unavailable(self):
        fifo = self.tmp / "unsafe.pipe"
        os.mkfifo(fifo)
        result = preservation.capture_snapshot(self.tmp)

        self.assertEqual(result["status"], "unavailable", result)
        self.assertIn("unsafe.pipe", result["detail"])

    def test_mode_addition_and_removal_are_content_inventory_changes(self):
        target = self.tmp / "target.txt"
        target.write_text("same\n", encoding="utf-8")
        before = preservation.capture_snapshot(self.tmp)
        target.chmod(0o600)
        after_mode = preservation.capture_snapshot(self.tmp)
        self.assertEqual(preservation.compare_snapshots(before, after_mode)["status"], "fail")

        target.unlink()
        after_removed = preservation.capture_snapshot(self.tmp)
        self.assertEqual(preservation.compare_snapshots(after_mode, after_removed)["status"], "fail")

    def test_git_index_is_compared_logically_without_requiring_clean_worktree(self):
        target = self.tmp / "staged.txt"
        target.write_text("stage me\n", encoding="utf-8")
        env = {**os.environ, "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_SYSTEM": "/dev/null"}
        subprocess.run(["git", "init", "-q"], cwd=self.tmp, check=True, env=env)
        before = preservation.capture_snapshot(self.tmp)
        subprocess.run(["git", "add", "staged.txt"], cwd=self.tmp, check=True, env=env)
        after = preservation.capture_snapshot(self.tmp)

        result = preservation.compare_snapshots(before, after)

        self.assertEqual(result["status"], "fail", result)
        self.assertTrue(any(path.endswith("/staged.txt") for path in result["changed_paths"]), result)

    def test_conflicted_git_index_keeps_all_stages(self):
        env = {**os.environ, "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_SYSTEM": "/dev/null"}
        subprocess.run(["git", "init", "-q", "-b", "main"], cwd=self.tmp, check=True, env=env)
        subprocess.run(["git", "config", "user.name", "preservation"], cwd=self.tmp, check=True, env=env)
        subprocess.run(["git", "config", "user.email", "preservation@example.invalid"], cwd=self.tmp, check=True, env=env)
        target = self.tmp / "conflict.txt"
        target.write_text("base\n", encoding="utf-8")
        subprocess.run(["git", "add", "conflict.txt"], cwd=self.tmp, check=True, env=env)
        subprocess.run(["git", "commit", "-qm", "base"], cwd=self.tmp, check=True, env=env)
        subprocess.run(["git", "checkout", "-qb", "side"], cwd=self.tmp, check=True, env=env)
        target.write_text("side\n", encoding="utf-8")
        subprocess.run(["git", "commit", "-qam", "side"], cwd=self.tmp, check=True, env=env)
        subprocess.run(["git", "checkout", "-q", "main"], cwd=self.tmp, check=True, env=env)
        target.write_text("main\n", encoding="utf-8")
        subprocess.run(["git", "commit", "-qam", "main"], cwd=self.tmp, check=True, env=env)
        subprocess.run(["git", "merge", "side"], cwd=self.tmp, check=False, env=env, capture_output=True)

        snapshot = preservation.capture_snapshot(self.tmp)

        self.assertEqual(snapshot["status"], "pass", snapshot)
        stages = snapshot["git_index"]["conflict.txt"]
        self.assertIsInstance(stages, list, snapshot)
        self.assertEqual([1, 2, 3], [item["stage"] for item in stages])


if __name__ == "__main__":
    unittest.main()
