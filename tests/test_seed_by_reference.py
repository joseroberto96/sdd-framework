#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""L14 (plan 5.2, D9c) — `seed_snapshot` por referencia.

El evento lleva `source_commit` y `state_hash` (hash del estado en ese commit); el
replay carga la base desde git y comprueba el hash. Si el clon no alcanza el
commit, lo dice en vez de fingir. La forma con `entities` en línea sigue válida.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / ".agent" / "scripts"))
import replay_state_log as rsl  # noqa: E402
import state_core  # noqa: E402
import action_contracts  # noqa: E402


def _ev(action, payload, ts="2026-09-19T10:00:00Z"):
    return json.dumps({"ts": ts, "action": action, "payload": payload}, ensure_ascii=False)


PAST = {
    "_bugs": [{"id": "BUG-001", "status": "Abierto", "module_id": "m_old",
               "severity": "Menor", "description": "previo al log"}],
    "module_statuses": {"m_old": "completed"},
    "modules": {"completed": ["m_old"], "in_progress": [], "pending": []},
}


class SeedByReferenceTests(unittest.TestCase):
    def setUp(self):
        self.w = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.w, ignore_errors=True)
        (self.w / "_project").mkdir()
        self.g("init", "-q")
        self.g("config", "user.email", "t@t")
        self.g("config", "user.name", "t")
        state = rsl._seed_state()
        state.update(PAST)
        (self.w / "_project" / "03_project_state.json").write_text(json.dumps(state))
        self.g("add", "-A")
        self.g("commit", "-qm", "estado previo")
        self.commit = self.g("rev-parse", "HEAD").stdout.strip()
        self.digest = state_core.state_hash_at_commit(self.w, self.commit)

    def g(self, *a):
        env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
        return subprocess.run(["git", *a], cwd=self.w, capture_output=True, text=True, env=env)

    def _replay(self, payload, root=None):
        base = rsl._seed_state()
        return rsl.apply_events(base, [_ev("seed_snapshot", payload)], workspace_root=root or self.w)

    def _ref(self, **over):
        p = {"source_commit": self.commit, "reason": "ancla FW-252", "state_hash": self.digest}
        p.update(over)
        return p

    def test_reference_seeds_from_git_when_hash_matches(self):
        state, warnings = self._replay(self._ref())
        self.assertEqual(warnings, [])
        self.assertEqual([b["id"] for b in state["_bugs"]], ["BUG-001"])
        self.assertEqual(state["module_statuses"]["m_old"], "completed")
        self.assertIn("m_old", state["modules"]["completed"])

    def test_reference_event_is_small(self):
        self.assertLess(len(_ev("seed_snapshot", self._ref())), 400)

    def test_hash_mismatch_is_reported_and_seeds_nothing(self):
        state, warnings = self._replay(self._ref(state_hash="0" * 64))
        self.assertTrue(any("hash" in w for w in warnings), warnings)
        self.assertEqual(state["_bugs"], [])

    def test_unreachable_commit_says_so_and_does_not_pretend(self):
        state, warnings = self._replay(self._ref(source_commit="f" * 40))
        self.assertTrue(any("no alcanza" in w and "f" * 40 in w for w in warnings), warnings)
        self.assertEqual(state["_bugs"], [])

    def test_repo_without_the_commit_says_so(self):
        otro = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, otro, ignore_errors=True)
        subprocess.run(["git", "init", "-q"], cwd=otro, check=True)
        _, warnings = self._replay(self._ref(), root=otro)
        self.assertTrue(any("no alcanza" in w for w in warnings), warnings)

    def test_inline_form_still_valid_backwards_compatible(self):
        state, warnings = self._replay({"source_commit": "abc1234", "reason": "x", "entities": PAST})
        self.assertEqual(warnings, [])
        self.assertEqual([b["id"] for b in state["_bugs"]], ["BUG-001"])

    def test_neither_entities_nor_hash_is_rejected(self):
        _, warnings = self._replay({"source_commit": self.commit, "reason": "x"})
        self.assertTrue(any("state_hash" in w for w in warnings), warnings)

    def test_reference_never_overwrites_existing(self):
        base = rsl._seed_state()
        base["_bugs"] = [{"id": "BUG-001", "status": "Cerrado", "description": "vivo"}]
        state, _ = rsl.apply_events(base, [_ev("seed_snapshot", self._ref())], workspace_root=self.w)
        self.assertEqual(state["_bugs"][0]["description"], "vivo")

    def test_contract_lists_state_hash_as_optional_and_entities_optional(self):
        self.assertNotIn("entities", state_core._REQUIRED_PAYLOAD_KEYS["seed_snapshot"])
        self.assertIn("state_hash", state_core._OPTIONAL_PAYLOAD_KEYS["seed_snapshot"])
        self.assertIn("state_hash", action_contracts.describe_action("seed_snapshot")["fields"])

    def test_build_reference_payload_round_trips(self):
        p = state_core.seed_reference_payload(self.w, self.commit, "ancla")
        self.assertEqual(p["state_hash"], self.digest)
        self.assertNotIn("entities", p)
        _, warnings = self._replay(p)
        self.assertEqual(warnings, [])


class AnchoredSeedTests(unittest.TestCase):
    """El ancla carga la semilla por referencia ANTES de reproducir el log."""

    def setUp(self):
        self.w = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.w, ignore_errors=True)
        (self.w / "_project").mkdir()
        self.sp = self.w / "_project" / "03_project_state.json"
        self.lp = self.w / "_project" / "state_log.jsonl"
        self.g("init", "-q")
        self.g("config", "user.email", "t@t")
        self.g("config", "user.name", "t")

    def g(self, *a):
        env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
        return subprocess.run(["git", *a], cwd=self.w, capture_output=True, text=True, env=env)

    def _commit(self, msg):
        self.g("add", "-A")
        self.g("commit", "-qm", msg)
        return self.g("rev-parse", "HEAD").stdout.strip()

    def _history(self, with_parent_state=True):
        bug = {"id": "BUG-001", "status": "Abierto", "module_id": "m_old", "severity": "Menor",
               "description": "previo al log", "fix": "", "affected_files": []}
        s_bug = dict(rsl._seed_state(), _bugs=[bug], module_statuses={"m_old": "in_progress"},
                     modules={"completed": [], "in_progress": ["m_old"], "pending": []})
        self.sp.write_text(json.dumps(s_bug))
        c0 = self._commit("estado con el bug")
        if with_parent_state:
            self.sp.write_text(json.dumps(rsl._seed_state()))
            self._commit("padre sin el bug")
        else:
            self.sp.unlink()
            self._commit("padre sin estado")
        ev = _ev("resolve_bug", {"id": "BUG-001", "fix": "arreglado"})
        final = json.loads(json.dumps(s_bug))
        final["_bugs"][0].update(status="Resuelto", fix="arreglado", resolved_at="2026-09-19T10:00:00Z")
        final["module_statuses"]["m_old"] = "in_progress"
        self.sp.write_text(json.dumps(final))
        self.lp.write_text(ev + "\n")
        self._commit("nace el log")
        return c0

    def _append_seed(self, c0, **over):
        p = state_core.seed_reference_payload(self.w, c0, "ancla FW-252")
        p.update(over)
        with self.lp.open("a") as f:
            f.write(_ev("seed_snapshot", p) + "\n")

    def test_without_seed_the_log_does_not_reproduce(self):
        self._history()
        self.assertFalse(rsl.anchored_report(self.lp, self.sp)["reproduces"])

    def test_seed_at_end_of_log_is_hoisted_into_the_anchor(self):
        c0 = self._history()
        self._append_seed(c0)
        report = rsl.anchored_report(self.lp, self.sp)
        self.assertTrue(report["reproduces"], report)

    def test_no_parent_state_but_seed_gives_an_anchor(self):
        c0 = self._history(with_parent_state=False)
        self.assertIsNone(rsl.anchor_state(self.lp))
        self._append_seed(c0)
        self.assertIsNotNone(rsl.anchor_state(self.lp))
        self.assertTrue(rsl.anchored_report(self.lp, self.sp)["reproduces"])

    def test_unreachable_seed_is_reported_not_faked(self):
        c0 = self._history()
        self._append_seed(c0, source_commit="f" * 40)
        report = rsl.anchored_report(self.lp, self.sp)
        self.assertFalse(report["reproduces"])
        self.assertTrue(any("no alcanza" in w for w in report["warnings"]), report)

    def test_wrong_hash_seed_is_reported(self):
        c0 = self._history()
        self._append_seed(c0, state_hash="0" * 64)
        report = rsl.anchored_report(self.lp, self.sp)
        self.assertFalse(report["reproduces"])
        self.assertTrue(any("hash" in w for w in report["warnings"]), report)

    def _history_seed_at_log_commit(self):
        """Example Desktop: el padre no tiene estado y la semilla apunta al commit C que
        CREÓ la bitácora; el estado de C ya incluye los efectos de los eventos de C."""
        bug = {"id": "BUG-001", "status": "Abierto", "module_id": "m_old", "severity": "Menor",
               "description": "previo al log", "fix": "", "affected_files": []}
        s_bug = dict(rsl._seed_state(), _bugs=[bug], module_statuses={"m_old": "in_progress"},
                     modules={"completed": [], "in_progress": ["m_old"], "pending": []})
        self.sp.write_text(json.dumps(s_bug))
        self._commit("padre con estado distinto")
        self.sp.unlink()
        self._commit("padre sin estado")
        # El evento no es idempotente: reaplicarlo crearía BUG-002.
        self.sp.write_text(json.dumps(s_bug))
        self.lp.write_text(_ev("register_bug", {"module_id": "m_old", "severity": "Menor",
                                                "description": "previo al log"}) + "\n")
        return self._commit("nace el log con su estado")

    def test_seed_at_the_commit_that_created_the_log_does_not_reapply_its_events(self):
        c = self._history_seed_at_log_commit()
        self._append_seed(c)
        report = rsl.anchored_report(self.lp, self.sp)
        self.assertTrue(report["reproduces"], report)

    def test_events_after_the_seed_commit_are_still_applied(self):
        c = self._history_seed_at_log_commit()
        self._append_seed(c)
        with self.lp.open("a") as f:
            f.write(_ev("add_active_mcp", {"name": "mcp_nuevo"}, ts="2026-09-19T11:00:00Z") + "\n")
        state = json.loads(self.sp.read_text())
        state["active_mcps"] = ["mcp_nuevo"]
        self.sp.write_text(json.dumps(state))
        report = rsl.anchored_report(self.lp, self.sp)
        self.assertTrue(report["reproduces"], report)


if __name__ == "__main__":
    unittest.main()
