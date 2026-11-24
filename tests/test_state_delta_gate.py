#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""L7 (plan 5.2) — gate de estado respaldado por la bitácora y replay anclado.

Cubre N2 (T2/T5/H2/H3 de `probe_tamper.sh`), FW-252 (`--anchor auto`,
`seed_snapshot`) y la acción retirada `drift_fix`.

Dos capas: la función pura `verify_state_delta` (unitarias) y el hook real
`pre_commit.sh` sobre un repo git efímero con el motor real copiado (P1: un test
que solo mira texto no prueba cableado — aquí se ejecuta el hook).
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


def _ev(action, payload, ts="2026-09-19T10:00:00Z"):
    return json.dumps({"ts": ts, "action": action, "payload": payload}, ensure_ascii=False)


def _base_state(**over):
    s = json.loads((ROOT / "_project" / "03_project_state.json").read_text(encoding="utf-8"))
    s.update(over)
    return s


class VerifyStateDeltaUnitTests(unittest.TestCase):
    def _run(self, head_state, head_log, staged_state, staged_log):
        return rsl.verify_state_delta(head_state, head_log, staged_state, staged_log)

    def _errors(self, problems):
        return [p for p in problems if p.startswith("ERROR")]

    def _warns(self, problems):
        return [p for p in problems if p.startswith("WARN")]

    def test_identical_state_is_noop(self):
        s = _base_state()
        self.assertEqual(self._run(s, [], s, []), [])

    def test_state_change_without_new_events_is_error(self):
        head = _base_state()
        staged = _base_state(current_phase="phase_99")
        problems = self._run(head, [_ev("add_active_mcp", {"name": "x"})],
                             staged, [_ev("add_active_mcp", {"name": "x"})])
        errs = self._errors(problems)
        self.assertTrue(errs, problems)
        self.assertIn("sin eventos nuevos", "\n".join(errs))
        self.assertIn("current_phase", "\n".join(errs))

    def test_log_not_prefix_is_error(self):
        s = _base_state()
        log = [_ev("add_active_mcp", {"name": "a"}), _ev("add_active_mcp", {"name": "b"})]
        problems = self._run(s, log, s, log[:1])
        self.assertIn("truncada o reescrita", "\n".join(self._errors(problems)))

    def test_legit_event_reproduced_passes(self):
        head = _base_state()
        staged = _base_state(active_mcps=["playwright"])
        ev = _ev("add_active_mcp", {"name": "playwright"})
        self.assertEqual(self._run(head, [], staged, [ev]), [])

    def test_allowlisted_field_change_without_events_passes(self):
        head = _base_state()
        staged = _base_state(framework_version="9.9.9", last_completed_step="algo",
                             session={"described_module": "m1"},
                             workflow_receipts={"reviews": {"m1": [{"result": "pass"}]}})
        self.assertEqual(self._run(head, [], staged, []), [])

    def test_soft_divergence_with_events_is_warn_not_error(self):
        head = _base_state()
        staged = _base_state(active_mcps=["playwright"], stack={"backend": "Go", "frontend": "", "database": "", "auth": ""})
        ev = _ev("add_active_mcp", {"name": "playwright"})
        problems = self._run(head, [], staged, [ev])
        self.assertEqual(self._errors(problems), [], problems)
        self.assertIn("stack.backend", "\n".join(self._warns(problems)))

    def test_in_progress_reorder_is_warn_naming_active_module(self):
        head = _base_state(modules={"completed": [], "pending": [], "in_progress": ["mod_a", "mod_b"]})
        staged = _base_state(modules={"completed": [], "pending": [], "in_progress": ["mod_b", "mod_a"]},
                             active_mcps=["x"])
        ev = _ev("add_active_mcp", {"name": "x"})
        problems = self._run(head, [], staged, [ev])
        self.assertEqual(self._errors(problems), [], problems)
        warn = "\n".join(self._warns(problems))
        self.assertIn("modules.in_progress", warn)
        self.assertIn("mod_a", warn)
        self.assertIn("mod_b", warn)

    def test_in_progress_reorder_by_hand_without_events_is_warn_not_error(self):
        head = _base_state(modules={"completed": [], "pending": [], "in_progress": ["mod_a", "mod_b"]})
        staged = _base_state(modules={"completed": [], "pending": [], "in_progress": ["mod_b", "mod_a"]})
        problems = self._run(head, [], staged, [])
        self.assertEqual(self._errors(problems), [], problems)
        self.assertIn("modules.in_progress", "\n".join(self._warns(problems)))

    def test_other_collection_reorder_is_silent(self):
        head = _base_state(modules={"completed": [], "in_progress": [], "pending": ["p1", "p2"]})
        staged = _base_state(modules={"completed": [], "in_progress": [], "pending": ["p2", "p1"]},
                             active_mcps=["x"])
        self.assertEqual(self._run(head, [], staged, [_ev("add_active_mcp", {"name": "x"})]), [])

    def test_missing_vs_empty_normalized_outside_hard_class(self):
        head = _base_state()
        head.pop("dependencies", None)
        staged = _base_state(dependencies=[], active_mcps=["x"])
        self.assertEqual(self._run(head, [], staged, [_ev("add_active_mcp", {"name": "x"})]), [])

    def test_missing_vs_empty_not_normalized_in_hard_class(self):
        head = _base_state(module_specs={})
        staged = _base_state(active_mcps=["x"])
        staged.pop("module_specs")
        problems = self._run(head, [], staged, [_ev("add_active_mcp", {"name": "x"})])
        self.assertIn("module_specs", "\n".join(self._errors(problems)), problems)

    def test_hard_divergence_with_events_is_error(self):
        head = _base_state()
        staged = _base_state(current_phase="phase_99", active_mcps=["x"])
        problems = self._run(head, [], staged, [_ev("add_active_mcp", {"name": "x"})])
        self.assertIn("current_phase", "\n".join(self._errors(problems)))

    def test_deleted_bug_is_error(self):
        bug = {"id": "BUG-001", "module_id": "m", "severity": "Mayor", "description": "d", "status": "Abierto"}
        head = _base_state(_bugs=[bug])
        staged = _base_state(_bugs=[], active_mcps=["x"])
        problems = self._run(head, [], staged, [_ev("add_active_mcp", {"name": "x"})])
        self.assertIn("_bugs[BUG-001]", "\n".join(self._errors(problems)))

    def _ack(self, paths):
        return _ev("acknowledge_state_delta",
                   {"reason": "migración legítima", "approver": "builder", "paths": paths})

    def test_acknowledge_literal_path_degrades_error_to_warn_and_is_printed(self):
        head = _base_state()
        staged = _base_state(current_phase="phase_99")
        problems = self._run(head, [], staged, [self._ack(["current_phase"])])
        self.assertEqual(self._errors(problems), [], problems)
        text = "\n".join(self._warns(problems))
        self.assertIn("acknowledge_state_delta", text)
        self.assertIn("migración legítima", text)
        self.assertIn("builder", text)

    def test_acknowledge_wildcard_does_not_cover(self):
        head = _base_state()
        staged = _base_state(current_phase="phase_99")
        problems = self._run(head, [], staged, [self._ack(["current_*"])])
        self.assertIn("current_phase", "\n".join(self._errors(problems)))

    def test_acknowledge_is_printed_even_without_divergence(self):
        s = _base_state()
        problems = self._run(s, [], s, [self._ack(["current_phase"])])
        self.assertEqual(self._errors(problems), [])
        self.assertIn("acknowledge_state_delta", "\n".join(problems))

    def test_acknowledge_does_not_excuse_broken_prefix(self):
        s = _base_state()
        log = [_ev("add_active_mcp", {"name": "a"})]
        problems = self._run(s, log, s, [self._ack(["x"])])
        self.assertIn("truncada o reescrita", "\n".join(self._errors(problems)))


class EngineActionsTests(unittest.TestCase):
    def _state(self):
        return _base_state()

    def _replay_lines(self, lines, base=None):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        (Path(tmp) / "_project").mkdir()
        log = Path(tmp) / "_project" / "state_log.jsonl"
        log.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return rsl.apply_events(base if base is not None else self._state(), log.read_text().splitlines(),
                                workspace_root=Path(tmp))

    def test_drift_fix_is_tolerated_in_replay_with_warning(self):
        state, warnings = self._replay_lines([_ev("drift_fix", {"id": "FW-ADR-002-texto"})])
        self.assertTrue(any("drift_fix" in w for w in warnings), warnings)

    def test_drift_fix_strict_replay_does_not_abort(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        (Path(tmp) / "_project").mkdir()
        log = Path(tmp) / "_project" / "state_log.jsonl"
        log.write_text(_ev("drift_fix", {"id": "x"}) + "\n", encoding="utf-8")
        stats = rsl.replay(log, Path(tmp) / "_project" / "03_project_state.json", dry_run=True, strict=True)
        self.assertEqual(stats["errors"], [])

    def test_drift_fix_is_still_rejected_live(self):
        import state_core
        self.assertNotIn("drift_fix", state_core.BUILTIN_ACTIONS)

    # L12b (N12): acciones retiradas de extensiones de producto, misma clase que drift_fix.
    RETIRED_L12B = (
        ("set_dependencies", {"dependencies": ["x"]}),
        ("retire_dependency", {"name": "x"}),
        ("set_stack_frontend", {"frontend": "React"}),
    )

    def test_l12b_retired_actions_tolerated_in_replay_naming_action_and_project(self):
        for action, payload in self.RETIRED_L12B:
            with self.subTest(action=action):
                tmp = tempfile.mkdtemp()
                self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
                proj = Path(tmp) / "MiProyecto"
                (proj / "_project").mkdir(parents=True)
                log = proj / "_project" / "state_log.jsonl"
                log.write_text(_ev(action, payload) + "\n", encoding="utf-8")
                state, warnings = rsl.apply_events(self._state(), log.read_text().splitlines(),
                                                   workspace_root=proj)
                self.assertTrue(any(action in w and "MiProyecto" in w and "retirada" in w
                                    for w in warnings), warnings)
                self.assertFalse(any("desconocida" in w for w in warnings), warnings)

    def test_l12b_retired_actions_strict_replay_does_not_abort(self):
        for action, payload in self.RETIRED_L12B:
            with self.subTest(action=action):
                tmp = tempfile.mkdtemp()
                self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
                (Path(tmp) / "_project").mkdir()
                log = Path(tmp) / "_project" / "state_log.jsonl"
                log.write_text(_ev(action, payload) + "\n", encoding="utf-8")
                stats = rsl.replay(log, Path(tmp) / "_project" / "03_project_state.json",
                                   dry_run=True, strict=True)
                self.assertEqual(stats["errors"], [])

    def test_l12b_retired_actions_still_rejected_live(self):
        import state_core
        for action, _ in self.RETIRED_L12B:
            with self.subTest(action=action):
                self.assertNotIn(action, state_core.BUILTIN_ACTIONS)
                sm = state_core.StateManager.__new__(state_core.StateManager)
                sm.state_data = self._state()
                with self.assertRaisesRegex(ValueError, "Acción desconocida"):
                    sm._apply_event(action, {}, "2026-09-19T00:00:00Z")

    def test_seed_snapshot_seeds_absent_and_never_overwrites(self):
        base = self._state()
        base["_bugs"] = [{"id": "BUG-001", "status": "Abierto", "description": "vivo"}]
        payload = {
            "source_commit": "abc1234", "reason": "ancla FW-252",
            "entities": {
                "_bugs": [{"id": "BUG-001", "status": "Cerrado", "description": "OTRO"},
                          {"id": "BUG-002", "status": "Abierto", "description": "previo al log"}],
                "module_statuses": {"m_old": "completed"},
                "modules": {"completed": ["m_old"], "in_progress": [], "pending": []},
            },
        }
        state, _ = self._replay_lines([_ev("seed_snapshot", payload)], base=base)
        ids = [b["id"] for b in state["_bugs"]]
        self.assertEqual(ids, ["BUG-001", "BUG-002"])
        self.assertEqual(state["_bugs"][0]["description"], "vivo")
        self.assertEqual(state["module_statuses"]["m_old"], "completed")
        self.assertIn("m_old", state["modules"]["completed"])

    def test_seed_snapshot_requires_source_commit(self):
        _, warnings = self._replay_lines([_ev("seed_snapshot", {"reason": "x", "entities": {"stack": {"backend": "Go"}}})])
        self.assertTrue(any("source_commit" in w for w in warnings), warnings)

    def test_new_actions_are_builtin_with_contract(self):
        import state_core
        import action_contracts
        for a in ("seed_snapshot", "acknowledge_state_delta"):
            self.assertIn(a, state_core.BUILTIN_ACTIONS)
            self.assertTrue(action_contracts.describe_action(a)["description"])


class HookGateTests(unittest.TestCase):
    """El hook real sobre un repo git efímero con el motor real copiado."""

    base = None

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        base = Path(cls.tmp) / "base"
        (base / "_project").mkdir(parents=True)
        shutil.copytree(ROOT / ".agent", base / ".agent",
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        shutil.copy(ROOT / "_project" / "03_project_state.json", base / "_project" / "03_project_state.json")
        # El doctor real no es el objeto de estas pruebas; el gate de transición sí es el real.
        (base / ".agent" / "scripts" / "framework_doctor.py").write_text("import sys\nsys.exit(0)\n")
        cls._git(base, "init", "-q")
        cls._git(base, "config", "user.email", "t@t")
        cls._git(base, "config", "user.name", "t")
        for act, pl in [
            ("add_module", {"id": "mod_a"}),
            ("add_module", {"id": "mod_b"}),
            ("update_module_status", {"id": "mod_a", "status": "in_progress"}),
            ("update_module_status", {"id": "mod_b", "status": "in_progress"}),
            ("update_module_status", {"id": "mod_b", "status": "blocked"}),
            ("register_bug", {"module_id": "mod_a", "severity": "Menor", "description": "bug de prueba"}),
        ]:
            cls._mutate(base, act, pl)
        cls._git(base, "add", "-A")
        cls._git(base, "commit", "-qm", "base")
        cls.base = base

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    @staticmethod
    def _git(cwd, *args):
        env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
        return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, env=env)

    @staticmethod
    def _mutate(cwd, action, payload):
        env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
        r = subprocess.run(
            [sys.executable, ".agent/scripts/state_manager.py", "mutate", "--action", action,
             "--payload", json.dumps(payload)],
            cwd=cwd, capture_output=True, text=True, env=env)
        if r.returncode != 0:
            raise AssertionError(f"mutate {action} falló: {r.stdout}{r.stderr}")
        return r

    def setUp(self):
        self.w = Path(tempfile.mkdtemp()) / "c"
        self.addCleanup(shutil.rmtree, self.w.parent, ignore_errors=True)
        self._git(self.w.parent, "clone", "-q", "--local", str(self.base), str(self.w))
        self._git(self.w, "config", "user.email", "t@t")
        self._git(self.w, "config", "user.name", "t")

    def _state_path(self):
        return self.w / "_project" / "03_project_state.json"

    def _edit_state(self, fn):
        s = json.loads(self._state_path().read_text(encoding="utf-8"))
        fn(s)
        self._state_path().write_text(json.dumps(s, ensure_ascii=False, indent=2), encoding="utf-8")

    def _hook(self):
        env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
        env["FRAMEWORK_PYTHON"] = sys.executable
        r = subprocess.run(["bash", ".agent/scripts/pre_commit.sh"], cwd=self.w,
                           capture_output=True, text=True, env=env)
        return r.returncode, r.stdout + r.stderr

    def test_t0_control_neutral_change_passes(self):
        (self.w / "README.md").write_text("# x\n")
        self._git(self.w, "add", "-A")
        rc, out = self._hook()
        self.assertEqual(rc, 0, out)

    def test_t2_phase_edited_by_hand_blocks(self):
        self._edit_state(lambda s: s.__setitem__("current_phase", "phase_99"))
        self._git(self.w, "add", "-A")
        rc, out = self._hook()
        self.assertEqual(rc, 1, out)
        self.assertIn("current_phase", out)

    def test_t5_last_event_deleted_blocks(self):
        log = self.w / "_project" / "state_log.jsonl"
        lines = log.read_text().splitlines()
        log.write_text("\n".join(lines[:-1]) + "\n")
        self._git(self.w, "add", "-A")
        rc, out = self._hook()
        self.assertEqual(rc, 1, out)
        self.assertIn("truncada o reescrita", out)

    def test_h2_unblock_by_hand_blocks(self):
        self._edit_state(lambda s: s["module_statuses"].__setitem__("mod_b", "in_progress"))
        self._git(self.w, "add", "-A")
        rc, out = self._hook()
        self.assertEqual(rc, 1, out)
        self.assertIn("module_statuses.mod_b", out)

    def test_h3_bug_deleted_blocks(self):
        self._edit_state(lambda s: s.__setitem__("_bugs", []))
        self._git(self.w, "add", "-A")
        rc, out = self._hook()
        self.assertEqual(rc, 1, out)
        self.assertIn("_bugs[BUG-001]", out)

    def test_legit_engine_mutation_passes(self):
        self._mutate(self.w, "register_bug",
                     {"module_id": "mod_a", "severity": "Menor", "description": "otro bug"})
        self._mutate(self.w, "set_phase", {"current_phase": "phase_02", "current_step": "s"})
        self._git(self.w, "add", "-A")
        rc, out = self._hook()
        self.assertEqual(rc, 0, out)

    def test_allowlisted_field_change_passes(self):
        def f(s):
            s["session"] = {"described_module": "mod_a"}
            s["last_completed_step"] = "hecho a mano"
        self._edit_state(f)
        self._git(self.w, "add", "-A")
        rc, out = self._hook()
        self.assertEqual(rc, 0, out)

    def test_state_staged_without_log_is_error(self):
        """Condición 2: el motor escribió estado y log, pero solo el estado va a stage."""
        self._mutate(self.w, "set_phase", {"current_phase": "phase_02", "current_step": "s"})
        self._git(self.w, "add", "_project/03_project_state.json")
        rc, out = self._hook()
        self.assertEqual(rc, 1, out)
        self.assertIn("sin eventos nuevos", out)

    def test_acknowledge_covers_literal_path_and_is_printed(self):
        self._edit_state(lambda s: s["module_statuses"].__setitem__("mod_b", "in_progress"))
        self._mutate(self.w, "acknowledge_state_delta",
                     {"reason": "migración de prueba", "approver": "builder",
                      "paths": ["module_statuses.mod_b"]})
        self._git(self.w, "add", "-A")
        rc, out = self._hook()
        self.assertEqual(rc, 0, out)
        self.assertIn("acknowledge_state_delta", out)
        self.assertIn("migración de prueba", out)

    def test_acknowledge_for_other_path_still_blocks(self):
        self._edit_state(lambda s: s["module_statuses"].__setitem__("mod_b", "in_progress"))
        self._mutate(self.w, "acknowledge_state_delta",
                     {"reason": "otra cosa", "approver": "builder", "paths": ["current_phase"]})
        self._git(self.w, "add", "-A")
        rc, out = self._hook()
        self.assertEqual(rc, 1, out)
        self.assertIn("module_statuses.mod_b", out)


class AnchorTests(unittest.TestCase):
    def setUp(self):
        self.w = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.w, ignore_errors=True)
        (self.w / "_project").mkdir()
        self.g("init", "-q")
        self.g("config", "user.email", "t@t")
        self.g("config", "user.name", "t")
        self.sp = self.w / "_project" / "03_project_state.json"
        self.lp = self.w / "_project" / "state_log.jsonl"

    def g(self, *a):
        env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
        return subprocess.run(["git", *a], cwd=self.w, capture_output=True, text=True, env=env)

    def _commit(self, msg):
        self.g("add", "-A")
        self.g("commit", "-qm", msg)

    def test_anchor_auto_uses_parent_state_of_first_event(self):
        s0 = _base_state(current_phase="phase_03", active_mcps=["previo"])
        self.sp.write_text(json.dumps(s0))
        self._commit("estado previo al log")
        s1 = dict(s0, active_mcps=["previo", "playwright"])
        self.sp.write_text(json.dumps(s1))
        self.lp.write_text(_ev("add_active_mcp", {"name": "playwright"}) + "\n")
        self._commit("primer evento")
        anchor = rsl.anchor_state(self.lp)
        self.assertEqual(anchor["current_phase"], "phase_03")
        self.assertEqual(anchor["active_mcps"], ["previo"])
        report = rsl.anchored_report(self.lp, self.sp)
        self.assertTrue(report["reproduces"], report)

    def test_anchor_auto_first_event_in_root_commit_uses_seed(self):
        seed = rsl._seed_state()
        self.sp.write_text(json.dumps(dict(seed, active_mcps=["playwright"])))
        self.lp.write_text(_ev("add_active_mcp", {"name": "playwright"}) + "\n")
        self._commit("commit raíz con estado y log")
        self.assertEqual(rsl.anchor_state(self.lp), seed)
        self.assertTrue(rsl.anchored_report(self.lp, self.sp)["reproduces"])

    def test_seed_state_mirrors_replay_seed(self):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        (tmp / "_project").mkdir()
        log = tmp / "_project" / "state_log.jsonl"
        log.write_text(_ev("add_active_mcp", {"name": "x"}) + "\n")
        out = tmp / "_project" / "03_project_state.json"
        stats = rsl.replay(log, out, dry_run=False, strict=False, force=True)
        expected = rsl._seed_state()
        expected["active_mcps"] = ["x"]
        state = stats["state"]
        self.assertTrue(set(expected) <= set(state), set(expected) - set(state))
        self.assertEqual({k: state[k] for k in expected}, expected)

    def test_anchor_auto_reports_divergence_when_state_predates_log(self):
        s0 = _base_state()
        self.sp.write_text(json.dumps(s0))
        self._commit("previo")
        s1 = _base_state(active_mcps=["x"], _bugs=[{"id": "BUG-001", "status": "Abierto", "module_id": "m",
                                                     "severity": "Menor", "description": "d"}])
        self.sp.write_text(json.dumps(s1))
        self.lp.write_text(_ev("add_active_mcp", {"name": "x"}) + "\n")
        self._commit("primer evento")
        report = rsl.anchored_report(self.lp, self.sp)
        self.assertFalse(report["reproduces"])
        self.assertTrue(any("_bugs" in d for d in report["divergences"]), report)


if __name__ == "__main__":
    unittest.main()
