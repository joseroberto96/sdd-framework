#!/usr/bin/env python3
"""L3.6 de la 5.3.1: cada mensaje de bloqueo nombra su salida (§2 del plan).

`doctor_wiring.json` declara en `features.gate_exits` un fichero por gate con la
salida literal (`needles`). Este test produce el mensaje de bloqueo real de cada
gate con una entrada mínima y exige que contenga esa salida. Hay una entrada por
cada fila de §2; el doctor comprueba además que la salida sigue en el fichero.
"""
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / ".agent" / "scripts"))

import claude_continuity_hook  # noqa: E402
import claude_dynamic_context  # noqa: E402
import framework_doctor  # noqa: E402
import guard_destructive_command  # noqa: E402
import state_queries  # noqa: E402
import validate_acceptance  # noqa: E402
import validate_no_stubs  # noqa: E402
import workflow_receipts  # noqa: E402

SECTION_2 = {"guard", "stubs", "secrets", "suite", "bugs", "evidence", "formats", "receipts", "turn_close"}
GIT_ENV = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
GIT_ENV.update({"GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull})


def guard_message():
    return guard_destructive_command._deny_payload("borrado recursivo de " + "/srv/datos")


def stubs_message():
    finding = "src/x.py:1: dato fabricado (marcador de trabajo sin terminar): # TODO: x"
    with mock.patch.object(validate_no_stubs, "module_findings", return_value=[finding]):
        return "\n".join(validate_no_stubs.stub_problems("M1", ROOT))


def secrets_message():
    with tempfile.TemporaryDirectory() as tmp:
        sandbox = Path(tmp)
        for args in (["init", "-q"], ["config", "user.email", "t@t"], ["config", "user.name", "t"]):
            subprocess.run(["git", *args], cwd=sandbox, check=True, capture_output=True, env=GIT_ENV)
        (sandbox / "config.py").write_text("TOKEN = '" + "gh" + "p_" + "Q7rT" * 9 + "'\n", encoding="utf-8")
        subprocess.run(["git", "add", "config.py"], cwd=sandbox, check=True, capture_output=True, env=GIT_ENV)
        original, framework_doctor.ROOT = framework_doctor.ROOT, sandbox
        try:
            errors: list[str] = []
            with mock.patch.dict(os.environ, {k: v for k, v in os.environ.items() if not k.startswith("GIT_")},
                                 clear=True):
                framework_doctor.scan_potential_secrets(errors)
            return "\n".join(errors)
        finally:
            framework_doctor.ROOT = original


def suite_message():
    script = (ROOT / ".agent/scripts/pre_commit.sh").read_text("utf-8")
    match = re.search(r"^fail_suite\(\) \{\n(.*?)^\}", script, re.DOTALL | re.MULTILINE)
    return match.group(1)


def bugs_continuity_message():
    return claude_continuity_hook._resolve_recovery_path({"gate_status": "BLOCK", "blocking_count": 1}, "startup")


def bugs_dynamic_message():
    return claude_dynamic_context._resolve_recovery_path(
        {"gate_status": "BLOCK", "blocking_count": 1}, "implementation")


def evidence_message():
    with tempfile.TemporaryDirectory() as tmp:
        text = "## M1\n- [x] AC-1 Exportar — evidencia: tests/test_nada.py::test_x rc=0\n"
        return "\n".join(validate_acceptance.evidence_gate(text, "M1", root=Path(tmp)))


def formats_message():
    return "\n".join(state_queries._documentary_evidence_problems("PRESERVAR", "- algo sin referencia\n"))


def receipts_message():
    basis = {"active_scope": "antes", "acceptance": "criterios"}
    receipt = {"status": "approved", "approved_by": "builder", "tier": "tier_1", "basis": basis,
               "approval_evidence": "aprobado por el builder", "risk_summary": "aditivo", "adr_refs": []}
    receipt["fingerprint"] = workflow_receipts.approval_fingerprint(
        basis, tier="tier_1", approved_by="builder", approval_evidence=receipt["approval_evidence"],
        risk_summary=receipt["risk_summary"], adr_refs=[])
    state = {"workflow_receipts": {"approvals": {"M1": receipt}},
             "work_tiers": {"M1": {"tier": "tier_1"}}, "dependencies": []}
    problems = workflow_receipts.approval_problems(
        ROOT, state, "M1", current_basis={"active_scope": "después", "acceptance": "criterios"})
    return "\n".join(p for p in problems if "stale" in p)


def turn_close_message():
    """5.3.2 (FW-402): el bloqueo del hook `Stop` por un cierre sin «Siguiente:» pide solo las líneas de cierre.

    En la 5.3.1 este gate era el rechazo de PROHIBICIONES, que desde la 5.3.2 es un aviso y no bloquea."""
    import turn_close_hook

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "proyecto"
        (root / "_project").mkdir(parents=True)
        (root / ".agent/workflows").mkdir(parents=True)
        (root / ".agent/workflows/manifest.json").write_text(
            (ROOT / ".agent/workflows/manifest.json").read_text("utf-8"), "utf-8")
        (root / "_project/03_project_state.json").write_text(json.dumps({"framework_mode": "initialized"}), "utf-8")
        with mock.patch.dict(os.environ, {"HOME": tmp}):
            out = turn_close_hook.evaluate({"session_id": "s1", "last_assistant_message": "Hecho, mañana seguimos."},
                                           host="codex", root=root, gycd=False)
    return out.get("reason", "") if out.get("decision") == "block" else ""


PRODUCERS = {
    ".agent/scripts/guard_destructive_command.py": guard_message,
    ".agent/scripts/validate_no_stubs.py": stubs_message,
    ".agent/scripts/framework_doctor.py": secrets_message,
    ".agent/scripts/pre_commit.sh": suite_message,
    ".agent/scripts/claude_continuity_hook.py": bugs_continuity_message,
    ".agent/scripts/claude_dynamic_context.py": bugs_dynamic_message,
    ".agent/scripts/validate_acceptance.py": evidence_message,
    ".agent/scripts/state_queries.py": formats_message,
    ".agent/scripts/workflow_receipts.py": receipts_message,
    ".agent/scripts/turn_close_hook.py": turn_close_message,
}


def entries():
    spec = json.loads((ROOT / ".agent/scripts/doctor_wiring.json").read_text("utf-8"))
    return spec["features"]["gate_exits"]["files"]


class GateExitsTests(unittest.TestCase):
    def test_una_entrada_por_cada_fila_de_la_seccion_2(self):
        self.assertEqual({entry["gate"] for entry in entries()}, SECTION_2)

    def test_cada_mensaje_de_bloqueo_nombra_su_salida(self):
        for entry in entries():
            with self.subTest(gate=entry["gate"], path=entry["path"]):
                message = PRODUCERS[entry["path"]]()
                self.assertTrue(message.strip(), "el gate no produjo mensaje de bloqueo")
                for needle in entry["needles"]:
                    self.assertIn(needle["needle"], message)

    def test_el_doctor_adjudica_la_feature(self):
        errors: list[str] = []
        framework_doctor.validate_wiring_feature("gate_exits", None, errors)
        self.assertEqual(errors, [])


if __name__ == "__main__":
    unittest.main()
