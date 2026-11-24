#!/usr/bin/env python3
"""Pruebas para lookup_state y query_state_point (L4, H2)."""

import copy
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / ".agent" / "scripts"))

import state_core
import state_manager
import state_queries
import workflow_receipts


def _base_state() -> dict:
    return {
        "project": "test_project",
        "current_phase": "phase_1",
        "current_step": "step_1",
        "modules": {
            "completed": ["M0"],
            "in_progress": ["M1"],
            "pending": ["M2"],
        },
        "module_statuses": {
            "M0": "completed",
            "M1": "in_progress",
            "M2": "not_started",
            "M3": "ready_for_validation",
            "M4": "blocked",
        },
        "work_tiers": {
            "M0": {"tier": "tier_0"},
            "M1": {"tier": "tier_1"},
            "M2": {"tier": "tier_2"},
            "M3": {"tier": "tier_1"},
            "M4": {"tier": "tier_2"},
        },
        "workflow_receipts": {
            "approvals": {
                "M1": {
                    "status": "approved",
                    "kind": "explicit",
                    "module": "M1",
                    "tier": "tier_1",
                    "approved_by": "builder",
                    "approval_evidence": "evidencia de aprobación",
                    "risk_summary": "riesgo bajo",
                    "adr_refs": [],
                    "basis": {"phase_plan": "hash1"},
                    "basis_version": 2,
                    "approved_at": "2026-09-18T20:00:00Z",
                    "fingerprint": "fp_m1_123456",
                },
                "M4": {
                    "status": "pending",
                    "approved_by": "agent",
                    "approval_evidence": "",
                    "fingerprint": "fp_invalid",
                },
            },
            "reviews": {
                "M1": [
                    {
                        "status": "pass",
                        "result": "pass",
                        "module": "M1",
                        "reviewer_id": "reviewer_a",
                        "implementer_id": "implementer_b",
                        "mode": "READ_ONLY_SUBAGENTS",
                        "diff_hash": "v3:code=abc;docs=def",
                        "evidence": "revisión independiente OK",
                        "completed_at": "2026-09-18T20:30:00Z",
                    }
                ],
                "M4": [
                    {
                        "status": "fail",
                        "result": "fail",
                        "module": "M4",
                        "reviewer_id": "same",
                        "implementer_id": "same",
                        "mode": "subagent",
                        "diff_hash": "v3:code=abc;docs=def",
                        "evidence": "revisión fallida",
                    }
                ],
            },
            "executions": {
                "M1": {
                    "status": "recorded",
                    "module": "M1",
                    "kind": "ok_automatizada",
                    "command": "pytest",
                    "exit_code": 0,
                    "evidence": "salida completa de tests OK 1234567890",
                    "diff_hash": "v3:code=abc;docs=def",
                    "recorded_at": "2026-09-18T20:45:00Z",
                },
                "M4": {
                    "status": "recorded",
                    "module": "M4",
                    "kind": "ok_automatizada",
                    "command": "pytest",
                    "exit_code": 1,
                    "evidence": "falló",
                },
            },
        },
    }


class LookupStateTests(unittest.TestCase):
    def test_lookup_kind_module(self):
        state = _base_state()
        m1 = state_queries.query_state_point(state, kind="module", module_id="M1")
        self.assertEqual(m1, {
            "module_id": "M1",
            "status": "in_progress",
            "tier": "tier_1",
            "bucket": "in_progress",
        })

        m0 = state_queries.query_state_point(state, kind="module", module_id="M0")
        self.assertEqual(m0, {
            "module_id": "M0",
            "status": "completed",
            "tier": "tier_0",
            "bucket": "completed",
        })

    def test_lookup_kind_modules_by_status_all_and_filtered(self):
        state = _base_state()
        all_st = state_queries.query_state_point(state, kind="modules_by_status")
        self.assertEqual(set(all_st.keys()), {
            "not_started", "in_progress", "ready_for_validation", "blocked", "completed", "dropped"
        })
        self.assertEqual(all_st["in_progress"], ["M1"])
        self.assertEqual(all_st["completed"], ["M0"])
        self.assertEqual(all_st["not_started"], ["M2"])
        self.assertEqual(all_st["ready_for_validation"], ["M3"])
        self.assertEqual(all_st["blocked"], ["M4"])

        filtered = state_queries.query_state_point(state, kind="modules_by_status", status="in_progress")
        self.assertEqual(filtered, {"in_progress": ["M1"]})

        with self.assertRaises(state_queries.QueryError) as ctx:
            state_queries.query_state_point(state, kind="modules_by_status", status="unknown_status")
        self.assertEqual(ctx.exception.code, "INVALID_TARGET")

    def test_lookup_kind_receipts_sealed_missing_and_invalid(self):
        state = _base_state()

        # M1 tiene recibos conformes y completos en el estado
        m1_rec = state_queries.query_state_point(state, kind="receipts", module_id="M1")
        self.assertEqual(m1_rec["module_id"], "M1")
        self.assertEqual(m1_rec["approval"]["state"], "sealed")
        self.assertEqual(m1_rec["approval"]["problems"], [])
        self.assertEqual(m1_rec["approval"]["fingerprint"], "fp_m1_123456")
        self.assertEqual(m1_rec["approval"]["approved_at"], "2026-09-18T20:00:00Z")
        self.assertEqual(m1_rec["approval"]["tier"], "tier_1")
        self.assertEqual(m1_rec["approval"]["kind"], "explicit")
        self.assertEqual(m1_rec["review"]["state"], "sealed")
        self.assertEqual(m1_rec["review"]["problems"], [])
        self.assertEqual(m1_rec["execution"]["state"], "sealed")
        self.assertEqual(m1_rec["execution"]["problems"], [])
        self.assertEqual(m1_rec["freshness"], "not_checked")
        self.assertEqual(
            m1_rec["note"],
            "sellado = coherente en el estado; la vigencia contra el disco la comprueba complete_module",
        )

        # M2 no tiene recibos registrados
        m2_rec = state_queries.query_state_point(state, kind="receipts", module_id="M2")
        self.assertEqual(m2_rec["approval"]["state"], "missing")
        self.assertTrue(m2_rec["approval"]["problems"])
        self.assertIsNone(m2_rec["approval"]["fingerprint"])
        self.assertEqual(m2_rec["review"]["state"], "missing")
        self.assertTrue(m2_rec["review"]["problems"])
        self.assertEqual(m2_rec["execution"]["state"], "missing")
        self.assertTrue(m2_rec["execution"]["problems"])

        # M4 tiene recibos inválidos (aprobación sin builder, review fallido, ejecución con exit 1)
        m4_rec = state_queries.query_state_point(state, kind="receipts", module_id="M4")
        self.assertEqual(m4_rec["approval"]["state"], "invalid")
        self.assertTrue(m4_rec["approval"]["problems"])
        self.assertEqual(m4_rec["review"]["state"], "invalid")
        self.assertTrue(m4_rec["review"]["problems"])
        self.assertEqual(m4_rec["execution"]["state"], "invalid")
        self.assertTrue(m4_rec["execution"]["problems"])

    def test_lookup_receipts_never_contains_key_valid(self):
        state = _base_state()
        for mod in ("M0", "M1", "M2", "M3", "M4"):
            res = state_queries.query_state_point(state, kind="receipts", module_id=mod)

            def _check_no_valid(obj, path=""):
                if isinstance(obj, dict):
                    self.assertNotIn(
                        "valid", obj,
                        f"La clave 'valid' no debe aparecer en kind=receipts (en {path})"
                    )
                    for k, v in obj.items():
                        if k == "state":
                            self.assertIn(
                                v, {"sealed", "missing", "invalid"},
                                f"El valor de 'state' debe ser sealed|missing|invalid, no '{v}' (en {path})"
                            )
                            self.assertNotEqual(v, "valid")
                        _check_no_valid(v, f"{path}.{k}" if path else k)
                elif isinstance(obj, list):
                    for idx, item in enumerate(obj):
                        _check_no_valid(item, f"{path}[{idx}]")

            _check_no_valid(res)

    def test_lookup_errors_unknown_kind_and_target(self):
        state = _base_state()

        with self.assertRaises(state_queries.QueryError) as ctx:
            state_queries.query_state_point(state, kind="unknown_kind")
        self.assertEqual(ctx.exception.code, "INVALID_TARGET")

        with self.assertRaises(state_queries.QueryError) as ctx:
            state_queries.query_state_point(state, kind="module")
        self.assertEqual(ctx.exception.code, "INVALID_TARGET")

        with self.assertRaises(state_queries.QueryError) as ctx:
            state_queries.query_state_point(state, kind="module", module_id="NON_EXISTENT")
        self.assertEqual(ctx.exception.code, "INVALID_TARGET")

        with self.assertRaises(state_queries.QueryError) as ctx:
            state_queries.query_state_point(state, kind="receipts")
        self.assertEqual(ctx.exception.code, "INVALID_TARGET")

        with self.assertRaises(state_queries.QueryError) as ctx:
            state_queries.query_state_point(state, kind="receipts", module_id="NON_EXISTENT")
        self.assertEqual(ctx.exception.code, "INVALID_TARGET")

    def test_query_state_point_is_pure_and_does_not_mutate_state(self):
        state = _base_state()
        before = copy.deepcopy(state)

        state_queries.query_state_point(state, kind="module", module_id="M1")
        state_queries.query_state_point(state, kind="modules_by_status")
        state_queries.query_state_point(state, kind="modules_by_status", status="in_progress")
        state_queries.query_state_point(state, kind="receipts", module_id="M1")
        state_queries.query_state_point(state, kind="receipts", module_id="M2")

        self.assertEqual(state, before)

    def test_query_state_point_does_not_create_workflow_receipts(self):
        # _receipts() haría setdefault: el estado sin la clave es la trampa real.
        state = _base_state()
        del state["workflow_receipts"]
        before = copy.deepcopy(state)
        result = state_queries.query_state_point(state, kind="receipts", module_id="M1")
        self.assertEqual("missing", result["approval"]["state"])
        self.assertEqual(state, before)

    def test_lookup_responses_stay_under_12_kib_on_large_state(self):
        state = _base_state()
        for i in range(2000):
            module = f"M{i:04d}_modulo_con_nombre_largo"
            state["module_statuses"][module] = "completed"
            state["modules"]["completed"].append(module)
            state.setdefault("work_tiers", {})[module] = {"tier": "tier_1", "rationale": "x" * 400}
        self.assertGreater(len(json.dumps(state).encode("utf-8")), 1_000_000)
        for kwargs in ({"kind": "receipts", "module_id": "M1"},
                       {"kind": "modules_by_status", "status": "in_progress"}):
            size = len(json.dumps(state_queries.query_state_point(state, **kwargs),
                                  indent=2, ensure_ascii=False).encode("utf-8"))
            self.assertLess(size, 12 * 1024, kwargs)

    def test_refresh_derived_approval_error_mentions_lookup_state_and_no_fingerprint(self):
        state = _base_state()
        root = Path(tempfile.mkdtemp())
        secret_fp = "mismatched_parent_fp_abc999xyz"
        with self.assertRaises(ValueError) as ctx:
            workflow_receipts.refresh_derived_approval(
                state,
                root,
                "M1",
                parent_fingerprint=secret_fp,
                derived_from=["data_model"],
                evidence="actualización derivada de data_model",
                now_iso="2026-09-18T21:00:00Z",
            )
        err_msg = str(ctx.exception)
        self.assertIn("consulta el vigente con lookup_state kind=receipts module_id=M1", err_msg)
        self.assertNotIn(secret_fp, err_msg)

    def test_lookup_state_mcp_and_cli_parity(self):
        tmp_dir = tempfile.TemporaryDirectory()
        root = Path(tmp_dir.name)
        shutil.copytree(
            ROOT / ".agent", root / ".agent",
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
        )
        project = root / "_project"
        project.mkdir(parents=True, exist_ok=True)
        state_file = project / "03_project_state.json"
        state = _base_state()
        state_file.write_text(json.dumps(state, indent=2), encoding="utf-8")

        orig_root = state_core.WORKSPACE_ROOT
        orig_path = state_core.STATE_FILE_PATH
        state_core.WORKSPACE_ROOT = root
        state_core.STATE_FILE_PATH = state_file

        try:
            manager = state_manager.StateManager()

            # MCP call: kind=module
            mcp_resp = state_manager.handle_mcp_message(manager, {
                "jsonrpc": "2.0",
                "id": 10,
                "method": "tools/call",
                "params": {
                    "name": "lookup_state",
                    "arguments": {"kind": "module", "module_id": "M1"},
                },
            })
            self.assertIsNotNone(mcp_resp)
            self.assertEqual(mcp_resp["id"], 10)
            mcp_data = json.loads(mcp_resp["result"]["content"][0]["text"])
            self.assertEqual(mcp_data["module_id"], "M1")
            self.assertEqual(mcp_data["status"], "in_progress")

            # CLI call: kind=module
            proc = subprocess.run(
                [sys.executable, "-B", str(root / ".agent" / "scripts" / "state_manager.py"), "lookup", "--kind", "module", "--module-id", "M1"],
                cwd=str(root),
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(proc.returncode, 0, proc.stderr)
            cli_data = json.loads(proc.stdout)
            self.assertEqual(cli_data, mcp_data)

            # CLI call: kind=modules_by_status
            proc_status = subprocess.run(
                [sys.executable, "-B", str(root / ".agent" / "scripts" / "state_manager.py"), "lookup", "--kind", "modules_by_status", "--status", "in_progress"],
                cwd=str(root),
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(proc_status.returncode, 0, proc_status.stderr)
            cli_status_data = json.loads(proc_status.stdout)
            self.assertEqual(cli_status_data, {"in_progress": ["M1"]})
        finally:
            state_core.WORKSPACE_ROOT = orig_root
            state_core.STATE_FILE_PATH = orig_path
            tmp_dir.cleanup()


if __name__ == "__main__":
    unittest.main()
