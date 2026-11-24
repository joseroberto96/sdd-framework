#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import unittest
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = ROOT / ".agent" / "schemas" / "project_state.schema.json"


class TestSchemaShape(unittest.TestCase):
    def setUp(self):
        self.schema = json.loads(SCHEMA.read_text(encoding="utf-8"))

    def test_declares_record_arrays(self):
        props = self.schema["properties"]
        for key in ["_bugs", "_clarifications", "_decisions", "session"]:
            self.assertIn(key, props, f"schema must declare {key}")

    def test_bug_status_enum_includes_tech_debt(self):
        status = self.schema["properties"]["_bugs"]["items"]["properties"]["status"]
        self.assertEqual(set(status["enum"]), {"Abierto", "Resuelto", "Deuda Técnica"})

    def test_session_fields(self):
        sess = self.schema["properties"]["session"]["properties"]
        for f in ["next_action", "next_command", "active_module_description", "next_module"]:
            self.assertIn(f, sess)

    def test_next_command_schema_rejects_empty_and_separators(self):
        next_command = self.schema["properties"]["session"]["properties"]["next_command"]
        self.assertEqual(next_command["minLength"], 1)
        self.assertIn("pattern", next_command)
        self.assertIn("—", next_command["pattern"])

    def test_schema_rejects_retired_codex_execution_pointer(self):
        sess = self.schema["properties"]["session"]["properties"]
        self.assertNotIn("codex_execution", sess)
        self.assertEqual(
            self.schema["properties"]["session"]["not"],
            {"required": ["codex_execution"]},
        )

    def test_closure_rejections_reason_enum_includes_data_safety_missing(self):
        # FW-083: Data Safety Gate.
        reason = self.schema["properties"]["closure_rejections"]["items"]["properties"]["reason"]
        self.assertIn("data_safety_missing", reason["enum"])

    def test_work_tiers_declares_data_safety_fields(self):
        # FW-083: declaración de riesgo de datos + sello de verificación.
        props = self.schema["properties"]["work_tiers"]["additionalProperties"]["properties"]
        self.assertEqual(props["handles_real_data"]["type"], "boolean")
        data_safety = props["data_safety"]
        self.assertEqual(data_safety["type"], "object")
        self.assertEqual(
            set(data_safety["required"]),
            {"target_env", "backup_verified", "evidence", "recorded_at"},
        )
        self.assertEqual(
            set(data_safety["properties"]["target_env"]["enum"]), {"dev", "staging", "prod"}
        )

    def test_workflow_receipts_declares_executions(self):
        # Bug preexistente: additionalProperties:false solo listaba
        # approvals/reviews aunque workflow_receipts.py::_receipts() ya
        # escribe 'executions' en runtime desde FW-144.
        wr = self.schema["properties"]["workflow_receipts"]
        self.assertIn("executions", wr["properties"])
        executions = wr["properties"]["executions"]
        self.assertEqual(executions["type"], "object")
        entry = executions["additionalProperties"]
        self.assertEqual(
            set(entry["required"]),
            {"status", "module", "kind", "evidence", "diff_hash", "recorded_at"},
        )

    def test_workflow_receipts_declares_checkpoints(self):
        # FW-134: recibos genéricos domain_sweep/expert_audit.
        wr = self.schema["properties"]["workflow_receipts"]
        self.assertIn("checkpoints", wr["properties"])
        checkpoints = wr["properties"]["checkpoints"]
        self.assertEqual(checkpoints["type"], "object")
        by_kind = checkpoints["additionalProperties"]
        entry = by_kind["additionalProperties"]
        self.assertEqual(
            set(entry["required"]), {"kind", "scope_id", "evidence", "reference", "recorded_at"}
        )
        self.assertEqual(
            set(entry["properties"]["kind"]["enum"]), {"domain_sweep", "expert_audit"}
        )

    def test_closure_rejections_reason_enum_includes_checkpoint_reasons(self):
        # FW-134: el paso 3.9 de cierre-modulo pasa de STOP-en-prosa a
        # closure_rejection mecánico en tier_2.
        reason = self.schema["properties"]["closure_rejections"]["items"]["properties"]["reason"]
        self.assertIn("domain_sweep_missing", reason["enum"])
        self.assertIn("expert_audit_missing", reason["enum"])

    def test_schema_declares_framework_migrations(self):
        migrations = self.schema["properties"]["framework_migrations"]
        self.assertEqual(migrations["type"], "object")
        entry = migrations["additionalProperties"]
        self.assertEqual(
            set(entry["required"]),
            {"type", "status", "archive_path", "archive_sha256"},
        )


if __name__ == "__main__":
    unittest.main()
