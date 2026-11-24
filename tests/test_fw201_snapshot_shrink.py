#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""FW-201 / ADR-139 — retroporte de Example Lab (4.39.1).

FW-185 (4.26.0) sacó el `derived_snapshot` de los recibos de aprobación a un
store content-addressed, pero en un proyecto maduro el peso seguía creciendo:
Example Lab llegó a `state_log.jsonl` 62 MB y `approval_snapshots/` 40 MB,
con GH001 de GitHub en cada push. Esta segunda mitad:

- trunca a 16 hex las huellas de LÍNEA del snapshot (no el fingerprint del
  recibo ni el digest del sidecar), con compatibilidad para snapshots v1;
- `shrink_state_log_snapshots.py` encoge lo ya acumulado y recalcula la cadena;
- `pre_push.sh` mide antes de publicar (WARN >= 50 MB, bloquea >= 90 MB);
- el doctor avisa del tamaño del log y del hook pre-push ausente.

Tests portados de `Example Lab/tests/unit/` (pytest) a unittest, que es el
runner de la plantilla. Solo los casos de FW-201: los de FW-185/FW-186 ya
tienen cobertura propia en la plantilla.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / ".agent" / "scripts"))

import replay_state_log  # noqa: E402
import shrink_state_log_snapshots as shrink  # noqa: E402
import state_core  # noqa: E402
import workflow_receipts as wr  # noqa: E402

HOOK = ROOT / ".agent" / "scripts" / "pre_push.sh"
BIG = 95 * 1024 * 1024


def _git_env() -> dict:
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env["GIT_CONFIG_GLOBAL"] = "/dev/null"
    return env


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, env=_git_env())


def _init_repo(path: Path, *, remote: Path | None = None) -> None:
    _git(path, "init", "-q")
    _git(path, "config", "user.email", "t@t.example")
    _git(path, "config", "user.name", "t")
    if remote is not None:
        _git(path, "remote", "add", "origin", str(remote))


def _run_hook(cwd: Path, stdin: str = "", *, allow_large: bool = False) -> subprocess.CompletedProcess:
    env = _git_env()
    env["ALLOW_LARGE_FILES"] = "1" if allow_large else ""
    return subprocess.run(["bash", str(HOOK)], cwd=cwd, input=stdin, capture_output=True, text=True, env=env)


class TmpDirCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()


# --- pre_push.sh ---------------------------------------------------------------


class PrePushHookTests(TmpDirCase):
    def _sandbox_with_state_log(self, size_bytes: int | None) -> Path:
        sb = self.tmp / "sb"
        sb.mkdir()
        _init_repo(sb)
        project = sb / "_project"
        project.mkdir()
        if size_bytes is None:
            (project / "state_log.jsonl").write_text('{"a":1}\n', encoding="utf-8")
        else:
            (project / "state_log.jsonl").write_bytes(b"0" * size_bytes)
        _git(sb, "add", "-A")
        _git(sb, "commit", "-q", "-m", "x")
        return sb

    def test_small_file_passes_without_warn(self):
        res = _run_hook(self._sandbox_with_state_log(None))
        self.assertEqual(0, res.returncode, res.stdout + res.stderr)
        self.assertNotIn("WARN", res.stdout)

    def test_file_between_50_and_90mb_warns_but_does_not_block(self):
        res = _run_hook(self._sandbox_with_state_log(60 * 1024 * 1024))
        self.assertEqual(0, res.returncode, res.stdout + res.stderr)
        self.assertIn("WARN", res.stdout)
        self.assertIn("state_log.jsonl", res.stdout)

    def test_file_at_or_above_90mb_blocks(self):
        res = _run_hook(self._sandbox_with_state_log(BIG))
        self.assertEqual(1, res.returncode, res.stdout + res.stderr)
        self.assertIn("bloqueado", res.stdout)

    def test_allow_large_files_escape_hatch_overrides_block(self):
        res = _run_hook(self._sandbox_with_state_log(BIG), allow_large=True)
        self.assertEqual(0, res.returncode, res.stdout + res.stderr)
        self.assertIn("ALLOW_LARGE_FILES=1", res.stdout)

    def _repo_with_hook(self, *names: str) -> Path:
        _init_repo(self.tmp)
        scripts = self.tmp / ".agent" / "scripts"
        scripts.mkdir(parents=True)
        for name in names:
            shutil.copy(ROOT / ".agent" / "scripts" / name, scripts / name)
        return scripts

    def test_install_is_worktree_safe(self):
        scripts = self._repo_with_hook("pre_push.sh")
        res = subprocess.run(["bash", str(scripts / "pre_push.sh"), "install"],
                             cwd=self.tmp, capture_output=True, text=True, env=_git_env())
        self.assertEqual(0, res.returncode, res.stdout + res.stderr)
        installed = self.tmp / ".git" / "hooks" / "pre-push"
        self.assertTrue(installed.exists())
        self.assertTrue(os.access(installed, os.X_OK))

    def test_install_does_not_overwrite_manual_hook(self):
        scripts = self._repo_with_hook("pre_push.sh")
        hooks_dir = self.tmp / ".git" / "hooks"
        hooks_dir.mkdir(parents=True, exist_ok=True)
        manual = hooks_dir / "pre-push"
        manual.write_text("#!/usr/bin/env bash\necho manual\n", encoding="utf-8")
        res = subprocess.run(["bash", str(scripts / "pre_push.sh"), "install"],
                             cwd=self.tmp, capture_output=True, text=True, env=_git_env())
        self.assertEqual(1, res.returncode)
        self.assertEqual("#!/usr/bin/env bash\necho manual\n", manual.read_text(encoding="utf-8"))

    def test_pre_commit_install_also_installs_pre_push(self):
        scripts = self._repo_with_hook("pre_commit.sh", "pre_push.sh")
        res = subprocess.run(["bash", str(scripts / "pre_commit.sh"), "install"],
                             cwd=self.tmp, capture_output=True, text=True, env=_git_env())
        self.assertEqual(0, res.returncode, res.stdout + res.stderr)
        self.assertTrue((self.tmp / ".git" / "hooks" / "pre-commit").exists())
        self.assertTrue((self.tmp / ".git" / "hooks" / "pre-push").exists())

    def _local_with_remote(self) -> tuple[Path, Path]:
        remote = self.tmp / "remote.git"
        local = self.tmp / "local"
        subprocess.run(["git", "init", "-q", "--bare", str(remote)], check=True, capture_output=True, env=_git_env())
        local.mkdir()
        _init_repo(local, remote=remote)
        (local / "_project").mkdir()
        return local, remote

    @staticmethod
    def _head(cwd: Path) -> str:
        return subprocess.run(["git", "rev-parse", "HEAD"], cwd=cwd, capture_output=True,
                              text=True, check=True, env=_git_env()).stdout.strip()

    def test_new_branch_push_does_not_re_audit_history_already_on_remote(self):
        local, _ = self._local_with_remote()
        (local / "_project" / "big_old.jsonl").write_bytes(b"0" * BIG)
        _git(local, "add", "-A")
        _git(local, "commit", "-q", "-m", "old big file")
        _git(local, "branch", "-M", "main")
        _git(local, "push", "-q", "origin", "main")
        _git(local, "fetch", "-q", "origin")
        _git(local, "checkout", "-q", "-b", "feature")
        (local / "_project" / "small_new.txt").write_text("hola\n", encoding="utf-8")
        _git(local, "add", "-A")
        _git(local, "commit", "-q", "-m", "small new file")
        stdin = f"refs/heads/feature {self._head(local)} refs/heads/feature {'0' * 40}\n"
        res = _run_hook(local, stdin)
        self.assertEqual(0, res.returncode, res.stdout + res.stderr)
        self.assertNotIn("big_old.jsonl", res.stdout)
        self.assertNotIn("bloqueado", res.stdout)

    def test_first_push_of_new_repo_still_audits_its_own_large_file(self):
        local, _ = self._local_with_remote()
        (local / "_project" / "big_new.jsonl").write_bytes(b"0" * BIG)
        _git(local, "add", "-A")
        _git(local, "commit", "-q", "-m", "primer commit, ya grande")
        stdin = f"refs/heads/main {self._head(local)} refs/heads/main {'0' * 40}\n"
        res = _run_hook(local, stdin)
        self.assertEqual(1, res.returncode, res.stdout + res.stderr)
        self.assertIn("big_new.jsonl", res.stdout)
        self.assertIn("bloqueado", res.stdout)

    def test_blob_with_space_in_path_still_blocks(self):
        local, _ = self._local_with_remote()
        (local / "_project" / "big file with spaces.jsonl").write_bytes(b"0" * BIG)
        _git(local, "add", "-A")
        _git(local, "commit", "-q", "-m", "archivo grande con espacio en el nombre")
        stdin = f"refs/heads/main {self._head(local)} refs/heads/main {'0' * 40}\n"
        res = _run_hook(local, stdin)
        self.assertEqual(1, res.returncode, res.stdout + res.stderr)
        self.assertIn("big file with spaces.jsonl", res.stdout)
        self.assertIn("bloqueado", res.stdout)


# --- shrink_state_log_snapshots.py --------------------------------------------


def _v1_snapshot(prefix: str, suffix: str) -> dict:
    fp = prefix + suffix
    assert len(fp) == 64
    return {"active_scope": [fp], "acceptance": [], "data_model": [], "api_contract": [],
            "contracts": [], "decisions": [], "module_spec": "0" * 64}


def _write_sidecar(store: Path, snapshot: dict) -> str:
    store.mkdir(parents=True, exist_ok=True)
    digest = wr._stable_hash(snapshot)
    (store / f"{digest}.json").write_text(json.dumps(snapshot, ensure_ascii=False, sort_keys=True), encoding="utf-8")
    return digest


def _receipt(module: str, *, ref: str | None = None, inline: dict | None = None) -> dict:
    r: dict = {"status": "approved", "module": module, "fingerprint": f"fp-{module}", "approved_by": "builder"}
    if ref is not None:
        r["derived_snapshot_ref"] = ref
    if inline is not None:
        r["derived_snapshot"] = inline
    return r


def _append_pre_chain(events: list[dict], action: str, payload: dict, ts: str) -> None:
    events.append({"ts": ts, "action": action, "payload": payload, "result": "ok"})


def _append_chained(events: list[dict], action: str, payload: dict, ts: str) -> None:
    entry = {"ts": ts, "action": action, "payload": payload, "result": "ok"}
    if events:
        prev = events[-1]
        entry["prev"] = prev["hash"] if "hash" in prev else state_core._event_hash(prev)
    else:
        entry["prev"] = None
    entry["hash"] = state_core._event_hash(entry)
    events.append(entry)


class ShrinkStateLogSnapshotsTests(TmpDirCase):
    def _build_sandbox(self) -> tuple[Path, Path, str, str]:
        project = self.tmp / "_project"
        project.mkdir(parents=True)
        store = project / wr.APPROVAL_SNAPSHOT_DIR
        digest_a = _write_sidecar(store, _v1_snapshot("a1b2c3d4e5f6a7b8", "1" * 48))
        digest_b = _write_sidecar(store, _v1_snapshot("a1b2c3d4e5f6a7b8", "2" * 48))
        self.assertNotEqual(digest_a, digest_b)
        state = {"workflow_receipts": {"approvals": {
            "M1": _receipt("M1", ref=digest_a), "M2": _receipt("M2", ref=digest_b),
        }}}
        state_path = project / "03_project_state.json"
        state_path.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")
        events: list[dict] = []
        _append_pre_chain(events, "set_phase", {"current_phase": "phase_01"}, "2026-07-01T00:00:00Z")
        _append_pre_chain(events, "record_approval_receipt",
                          {"module": "M3", "_receipt": _receipt("M3", inline=_v1_snapshot("b" * 16, "3" * 48))},
                          "2026-07-01T00:01:00Z")
        _append_chained(events, "update_last_step", {"last_completed_step": "x"}, "2026-07-24T00:00:00Z")
        _append_chained(events, "refresh_approval_receipt",
                        {"module": "M4", "_receipt": _receipt("M4", inline=_v1_snapshot("c" * 16, "4" * 48))},
                        "2026-07-24T00:01:00Z")
        _append_chained(events, "record_approval_receipt",
                        {"module": "M1", "_receipt": _receipt("M1", ref=digest_a)}, "2026-07-24T00:02:00Z")
        log_path = project / "state_log.jsonl"
        with open(log_path, "w", encoding="utf-8") as f:
            for event in events:
                f.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")
        self.assertEqual([], state_core.verify_log_chain(log_path))
        return state_path, log_path, digest_a, digest_b

    @staticmethod
    def _run(state_path: Path, log_path: Path, *, write: bool) -> int:
        argv = ["shrink_state_log_snapshots.py", "--state", str(state_path), "--log", str(log_path)]
        if write:
            argv.append("--write")
        with mock.patch.object(sys, "argv", argv):
            return shrink.main()

    def test_truncate_snapshot_slices_existing_hashes(self):
        truncated = shrink._truncate_snapshot(_v1_snapshot("a1b2c3d4e5f6a7b8", "1" * 48))
        self.assertEqual(2, truncated["fmt"])
        self.assertEqual(["a1b2c3d4e5f6a7b8"], truncated["active_scope"])
        self.assertEqual("0" * 16, truncated["module_spec"])

    def test_is_v2_detects_fmt_marker(self):
        self.assertTrue(shrink._is_v2({"fmt": 2}))
        self.assertFalse(shrink._is_v2({"fmt": 1}))
        self.assertFalse(shrink._is_v2({}))
        self.assertFalse(shrink._is_v2("not a dict"))

    def test_recompute_chain_carries_prev_hash_through_pre_chain_boundary(self):
        events: list[dict] = []
        _append_pre_chain(events, "a", {"x": 1}, "t0")
        _append_chained(events, "b", {"x": 2}, "t1")
        _append_chained(events, "c", {"x": 3}, "t2")
        original = events[1]["hash"]
        events[0]["payload"]["x"] = 999
        shrink._recompute_chain(events, first_touched=0)
        self.assertNotIn("hash", events[0])
        self.assertNotIn("prev", events[0])
        self.assertNotEqual(original, events[1]["hash"])
        self.assertEqual(state_core._event_hash(events[0]), events[1]["prev"])
        self.assertEqual(events[1]["hash"], events[2]["prev"])

    def test_dry_run_does_not_write(self):
        state_path, log_path, _a, _b = self._build_sandbox()
        state_before, log_before = state_path.read_bytes(), log_path.read_bytes()
        self.assertEqual(0, self._run(state_path, log_path, write=False))
        self.assertEqual(state_before, state_path.read_bytes())
        self.assertEqual(log_before, log_path.read_bytes())
        self.assertFalse((self.tmp / "_project" / "state_log_rebase.json").exists())

    def test_write_truncates_and_updates_refs_and_chain_verifies(self):
        state_path, log_path, digest_a, digest_b = self._build_sandbox()
        self.assertEqual(0, self._run(state_path, log_path, write=True))
        self.assertEqual([], state_core.verify_log_chain(log_path))
        approvals = json.loads(state_path.read_text(encoding="utf-8"))["workflow_receipts"]["approvals"]
        ref_m1 = approvals["M1"]["derived_snapshot_ref"]
        self.assertNotEqual(digest_a, ref_m1)
        self.assertNotEqual(digest_b, approvals["M2"]["derived_snapshot_ref"])
        loaded = wr.load_derived_snapshot(self.tmp, {"derived_snapshot_ref": ref_m1})
        self.assertEqual(2, loaded["fmt"])
        self.assertEqual(16, len(loaded["active_scope"][0]))
        events = [json.loads(l) for l in log_path.read_text(encoding="utf-8").splitlines() if l.strip()]
        fat = [e for e in events if e.get("action") in shrink._APPROVAL_ACTIONS
               and isinstance(e["payload"].get("_receipt", {}).get("derived_snapshot"), dict)]
        self.assertTrue(all(e["payload"]["_receipt"]["derived_snapshot"]["fmt"] == 2 for e in fat))
        self.assertTrue((self.tmp / "_project" / "state_log_rebase.json").exists())

    def test_dedup_collapses_sidecars_with_identical_truncated_content(self):
        state_path, log_path, digest_a, digest_b = self._build_sandbox()
        self._run(state_path, log_path, write=True)
        approvals = json.loads(state_path.read_text(encoding="utf-8"))["workflow_receipts"]["approvals"]
        ref_m1 = approvals["M1"]["derived_snapshot_ref"]
        self.assertEqual(ref_m1, approvals["M2"]["derived_snapshot_ref"])
        store = self.tmp / "_project" / wr.APPROVAL_SNAPSHOT_DIR
        self.assertFalse((store / f"{digest_a}.json").exists())
        self.assertFalse((store / f"{digest_b}.json").exists())
        self.assertTrue((store / f"{ref_m1}.json").exists())

    def test_replay_dry_run_reconstructs_without_error(self):
        state_path, log_path, _a, _b = self._build_sandbox()
        self._run(state_path, log_path, write=True)
        stats = replay_state_log.replay(log_path, self.tmp / "_unused.json", dry_run=True, strict=True)
        self.assertIsNone(stats["state"])
        self.assertFalse(stats["errors"])

    def test_second_run_is_idempotent(self):
        state_path, log_path, _a, _b = self._build_sandbox()
        self._run(state_path, log_path, write=True)
        state_first, log_first = state_path.read_bytes(), log_path.read_bytes()
        self.assertEqual(0, self._run(state_path, log_path, write=True))
        self.assertEqual(state_first, state_path.read_bytes())
        self.assertEqual(log_first, log_path.read_bytes())

    def test_aborts_without_writing_if_chain_verification_fails(self):
        state_path, log_path, _a, _b = self._build_sandbox()
        state_before, log_before = state_path.read_bytes(), log_path.read_bytes()
        with mock.patch.object(state_core, "verify_log_chain", lambda path: ["problema sintético"]):
            rc = self._run(state_path, log_path, write=True)
        self.assertEqual(2, rc)
        self.assertEqual(state_before, state_path.read_bytes())
        self.assertEqual(log_before, log_path.read_bytes())
        self.assertFalse(list(state_path.parent.glob("*.bak-*")))
        self.assertFalse((self.tmp / "_project" / "state_log_rebase.json").exists())
        self.assertFalse(log_path.with_suffix(log_path.suffix + ".new").exists())


# --- formato v2 del derived snapshot ------------------------------------------


def _project_tree(root: Path) -> None:
    project = root / "_project"
    (project / "02_technical_plan").mkdir(parents=True)
    (project / "01_product_requirements.md").write_text("# PRD\n\nRequisito 1\n", encoding="utf-8")
    (project / "02_technical_plan" / "phase_01.md").write_text("# Fase 1\n\nPlan\n", encoding="utf-8")
    (project / "04_active_scope.md").write_text("# Scope\n\n- Scope linea A\n- Scope linea B\n", encoding="utf-8")
    (project / "08_module_acceptance.md").write_text(
        "# ACs\n\n- [ ] AC-1 criterio uno\n- [ ] AC-2 criterio dos\n", encoding="utf-8")
    (project / "02_technical_plan" / "data_model.md").write_text("# Data model\n\nEntidad Foo { id }\n", encoding="utf-8")
    (project / "02_technical_plan" / "api_contract.yaml").write_text(
        "openapi: 3.0.0\npaths:\n  /foo:\n    get: {}\n", encoding="utf-8")
    (project / "03_contracts.md").write_text("# Contratos\n\nContrato Foo\n", encoding="utf-8")


def _state(module: str = "M99_demo", tier: str = "tier_1") -> dict:
    return {
        "current_phase": "phase_01", "dependencies": [], "_decisions": [],
        "work_tiers": {module: {"tier": tier, "rationale": "demo"}},
        "module_specs": {module: {"data_model": "done", "api_contract": "done"}},
        "workflow_receipts": {"approvals": {}, "reviews": {}, "executions": {}},
    }


def _sample_snapshot() -> dict:
    return {
        "active_scope": [wr._stable_hash("linea a"), wr._stable_hash("linea b")],
        "acceptance": [wr._stable_hash("ac 1")], "data_model": [wr._stable_hash("foo")],
        "api_contract": [], "contracts": [wr._stable_hash("contrato foo")], "decisions": [],
        "module_spec": wr._stable_hash({"data_model": "applies"}),
    }


def _sample_snapshot_v2() -> dict:
    return {
        "fmt": 2,
        "active_scope": [wr._truncated_hash("linea a"), wr._truncated_hash("linea b")],
        "acceptance": [wr._truncated_hash("ac 1")], "data_model": [wr._truncated_hash("foo")],
        "api_contract": [], "contracts": [wr._truncated_hash("contrato foo")], "decisions": [],
        "module_spec": wr._truncated_hash({"data_model": "applies"}),
    }


def _v1_snapshot_matching(root: Path, state: dict, module: str) -> dict:
    project = root / "_project"

    def full_fps(text: str) -> list[str]:
        return [wr._stable_hash(line) for line in wr._substantive_lines(text)]

    return {
        "active_scope": full_fps(wr._normalize_progress_markdown(wr._file_text(project / "04_active_scope.md"))),
        "acceptance": full_fps(wr._normalize_progress_markdown(wr._file_text(project / "08_module_acceptance.md"))),
        "data_model": full_fps(wr._file_text(project / "02_technical_plan" / "data_model.md")),
        "api_contract": full_fps(wr._file_text(project / "02_technical_plan" / "api_contract.yaml")),
        "contracts": full_fps(wr._file_text(project / "03_contracts.md")),
        "decisions": [wr._stable_hash(d) for d in state.get("_decisions", [])
                      if isinstance(d, dict) and wr._adr_applies_to_module(d.get("module_id"), module)],
        "module_spec": wr._stable_hash(wr._module_spec_basis(state, module)),
    }


class DerivedSnapshotV2Tests(TmpDirCase):
    def test_normalize_snapshot_format_is_noop_for_v2_and_none(self):
        self.assertIsNone(wr._normalize_snapshot_format(None))
        v2 = _sample_snapshot_v2()
        self.assertEqual(v2, wr._normalize_snapshot_format(v2))

    def test_normalize_snapshot_format_truncates_legacy_v1(self):
        v1 = _sample_snapshot()
        normalized = wr._normalize_snapshot_format(v1)
        self.assertEqual(2, normalized["fmt"])
        self.assertEqual([h[:16] for h in v1["active_scope"]], normalized["active_scope"])
        self.assertEqual(v1["module_spec"][:16], normalized["module_spec"])

    def test_refresh_tolerates_legacy_v1_previous_snapshot(self):
        """Un recibo aprobado antes de ADR-139 (snapshot v1, 64 hex) sigue
        permitiendo un refresh genuinamente aditivo: sin la normalización, la
        comparación v1 contra v2 por igualdad de string nunca coincidía."""
        _project_tree(self.tmp)
        state = _state()
        module = "M99_demo"
        basis = wr.approval_basis(self.tmp, state, module)
        ref = wr.store_derived_snapshot(self.tmp, _v1_snapshot_matching(self.tmp, state, module))
        fingerprint = wr.approval_fingerprint(basis, tier="tier_1", approved_by="builder",
                                              approval_evidence="revisado", risk_summary="bajo", adr_refs=[])
        state["workflow_receipts"]["approvals"][module] = {
            "status": "approved", "kind": "explicit", "module": module, "tier": "tier_1",
            "approved_by": "builder", "approval_evidence": "revisado", "risk_summary": "bajo",
            "adr_refs": [], "basis": basis, "derived_snapshot_ref": ref,
            "fingerprint": fingerprint, "approved_at": "2026-08-01T00:00:00Z",
            # 5.1 L3.2: la base se calcula en v2 (approval_basis sin recibo previo),
            # así que el recibo lo declara. Lo "legacy" de este test es el snapshot
            # v1, no la base. Sin el campo, un recibo se trata como v1 (H9).
            "basis_version": 2,
        }
        scope = self.tmp / "_project" / "04_active_scope.md"
        scope.write_text(scope.read_text(encoding="utf-8") + "- Scope linea C\n", encoding="utf-8")
        refreshed = wr.refresh_derived_approval(
            state, self.tmp, module, parent_fingerprint=fingerprint, derived_from=["04_active_scope.md"],
            evidence="ampliacion aditiva sobre receipt legacy v1", now_iso="2026-09-04T00:00:00Z",
        )
        self.assertIn("active_scope", refreshed["derived_surfaces"])
        self.assertEqual([], refreshed["non_additive_surfaces"])

    def test_approval_derived_snapshot_uses_truncated_fingerprints(self):
        _project_tree(self.tmp)
        snapshot = wr.approval_derived_snapshot(self.tmp, _state(), "M99_demo")
        self.assertEqual(2, snapshot["fmt"])
        self.assertEqual(wr._SNAPSHOT_FINGERPRINT_LEN, len(snapshot["active_scope"][0]))
        self.assertEqual(wr._SNAPSHOT_FINGERPRINT_LEN, len(snapshot["module_spec"]))

    def test_approval_basis_fingerprint_stay_full_length(self):
        """El truncado NO se filtra al fingerprint del recibo ni al digest del
        sidecar: truncarlos invalidaría recibos ya aprobados."""
        _project_tree(self.tmp)
        state = _state()
        basis = wr.approval_basis(self.tmp, state, "M99_demo")
        self.assertEqual(64, len(basis["module_spec"]))
        self.assertEqual(64, len(basis["decisions"]))
        self.assertEqual(64, len(wr.approval_fingerprint(basis, tier="tier_1")))
        digest = wr.store_derived_snapshot(self.tmp, wr.approval_derived_snapshot(self.tmp, state, "M99_demo"))
        self.assertEqual(64, len(digest))

    def test_refresh_detects_added_decisions_with_truncated_snapshot(self):
        _project_tree(self.tmp)
        state = _state()
        state["_decisions"].append({"id": "ADR-001", "title": "ya aprobada", "context": "c",
                                    "decision": "d", "module_id": "M99_demo"})
        parent = wr.record_approval(state, self.tmp, "M99_demo", approved_by="builder", evidence="revisado",
                                    risk_summary="bajo", adr_refs=["ADR-001"], now_iso="2026-09-04T00:00:00Z")
        state["_decisions"].append({"id": "ADR-002", "title": "nueva", "context": "c",
                                    "decision": "d", "module_id": "M99_demo"})
        refreshed = wr.refresh_derived_approval(
            state, self.tmp, "M99_demo", parent_fingerprint=parent["fingerprint"],
            derived_from=["03_arch_decisions.md"], evidence="ADR-002 añadida, ADR-001 intacta",
            now_iso="2026-09-04T01:00:00Z",
        )
        self.assertEqual(["ADR-002"], refreshed["added_decisions"])

    def test_secret_scanner_tolerates_truncated_hex_in_v2_snapshots(self):
        import framework_doctor as doctor

        sidecar_v2 = {
            "fmt": 2,
            "active_scope": [wr._truncated_hash("scope linea a"), wr._truncated_hash("scope linea b")],
            "api_contract": [wr._truncated_hash("GET /foo")],
        }
        self.assertIsNone(doctor._scan_json_secret(sidecar_v2, allow_bare_hex=False))

    def test_replay_reproduces_v2_derived_snapshot_verbatim(self):
        """El replay copia `_receipt` tal cual: es agnóstico al formato del
        snapshot, así que el shrink no necesita coordinación con _apply_event."""
        mgr = state_core.StateManager.__new__(state_core.StateManager)
        mgr._is_replay = True
        mgr.state_data = {
            "module_statuses": {}, "modules": {"completed": [], "in_progress": [], "pending": []},
            "work_tiers": {}, "_decisions": [],
            "workflow_receipts": {"approvals": {}, "reviews": {}, "executions": {}},
        }
        snapshot_v2 = {"fmt": 2, "active_scope": ["a1b2c3d4e5f6a7b8"], "module_spec": "0011223344556677"}
        receipt = {"status": "approved", "module": "M17", "fingerprint": "abc", "approved_by": "builder",
                   "derived_snapshot_ref": "deadbeef" * 8, "derived_snapshot": snapshot_v2}
        mgr._apply_event("record_approval_receipt",
                         {"module": "M17", "_receipt": receipt, "adr_refs": ["ADR-049"]},
                         "2026-09-04T00:00:00Z")
        self.assertEqual(snapshot_v2, mgr.state_data["workflow_receipts"]["approvals"]["M17"]["derived_snapshot"])


class DoctorSizeWarningsTests(TmpDirCase):
    def test_state_log_de_50mb_o_mas_avisa(self):
        import framework_doctor as doctor

        (self.tmp / "_project").mkdir()
        with open(self.tmp / "_project" / "state_log.jsonl", "wb") as f:
            f.truncate(doctor.STATE_LOG_WARN_BYTES)
        original = doctor.ROOT
        doctor.ROOT = self.tmp
        try:
            warnings: list[str] = []
            doctor.validate_state_log_size("initialized", warnings)
            silenciosos: list[str] = []
            doctor.validate_state_log_size("template", silenciosos)
        finally:
            doctor.ROOT = original
        self.assertTrue(any("shrink_state_log_snapshots.py" in w for w in warnings), warnings)
        self.assertEqual([], silenciosos)


if __name__ == "__main__":
    unittest.main()
