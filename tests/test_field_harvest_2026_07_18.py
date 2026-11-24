#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Golden tests de la cosecha de campo 2026-07-18.

Cubre los retroportes confirmados en los field reports de Example Lab (TL),
Research App (PA), Example CLI (Bot), Example Desktop y Example Tool:

- TL FW-007/FW-012, PA FW-097, Example Tool FW-T05, Bot FW-101: el doctor marcaba
  fingerprints/citas de los artefactos de estado como secretos/placeholders.
- TL FW-008, PA FW-047, Bot FW-006: `_project/.state.lock` entraba al hash de
  diff de los review receipts en la rama git → todo cierre tier_2 bloqueado.
- TL FW-009: el replay estricto abortaba si el primer evento era
  `register_decision` (colecciones no inicializadas en el estado base).
- TL FW-006: no existía camino canónico de reapertura de un módulo completado.
- TL FW-010: la falta de lockfile solo se descubría al aprobar la enésima
  dependencia; ahora el doctor avisa temprano.
- PA FW-044: variantes que solo difieren en casing generaban ruido masivo.
- Bot FW-102: el install del hook abortaba también ante hooks de versiones
  anteriores del framework, dejándolos stale para siempre.
"""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / ".agent" / "scripts"))
import framework_doctor as doctor  # noqa: E402
import replay_state_log  # noqa: E402
import state_core  # noqa: E402
import validate_transition  # noqa: E402
import workflow_receipts as wr  # noqa: E402


def make_manager(state: dict) -> state_core.StateManager:
    m = state_core.StateManager.__new__(state_core.StateManager)
    m.state_data = state
    return m


class StateArtifactScanExemptionTests(unittest.TestCase):
    """TL FW-007/FW-012 · PA FW-097 · Example Tool FW-T05 · Bot FW-101."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.sandbox = Path(self._tmp.name)
        (self.sandbox / "_project").mkdir()
        self._orig_root = doctor.ROOT
        doctor.ROOT = self.sandbox

    def tearDown(self):
        doctor.ROOT = self._orig_root
        self._tmp.cleanup()

    def test_state_fingerprints_are_not_flagged_as_secrets(self):
        # Fingerprint SHA-256 real bajo una clave sin marcador de allowlist
        # ("acceptance"), el caso exacto de TL FW-007.
        payload = {"approval_receipts": {"M1": {"basis": {"acceptance": "b" * 64}}}}
        (self.sandbox / "_project" / "03_project_state.json").write_text(
            json.dumps(payload), encoding="utf-8"
        )
        (self.sandbox / "_project" / "state_log.jsonl").write_text(
            json.dumps({"action": "x", "payload": {"acceptance": "c" * 64}}) + "\n",
            encoding="utf-8",
        )
        (self.sandbox / "_project" / "05_test_history.md").write_text(
            "diff_hash del review: " + "d" * 64 + "\n", encoding="utf-8"
        )
        errors: list[str] = []
        doctor.scan_potential_secrets(errors)
        self.assertEqual(errors, [], f"artefactos de estado exentos, got: {errors}")

    def test_real_secret_outside_state_artifacts_still_flagged(self):
        secret_line = "api_key" + " = " + '"' + "A" + "k" * 24 + '9"'
        (self.sandbox / "config.py").write_text(secret_line + "\n", encoding="utf-8")
        errors: list[str] = []
        doctor.scan_potential_secrets(errors)
        self.assertTrue(errors, "un secreto real fuera del estado debe seguir detectándose")

    def test_formatted_credential_inside_state_artifact_still_flagged(self):
        # La exención es SOLO para hex desnudo (fingerprints del motor): una
        # credencial con formato pegada en evidencia (05_test_history.md se
        # escribe a mano en /cierre-modulo) debe seguir bloqueando — el doctor
        # es también el gate de secretos de /publicar-github.
        cred = "ghp_" + "A" * 36
        (self.sandbox / "_project" / "05_test_history.md").write_text(
            "salida observada: Authorization con " + cred + "\n", encoding="utf-8"
        )
        errors: list[str] = []
        doctor.scan_potential_secrets(errors)
        self.assertTrue(errors, "credencial con formato en artefacto de estado debe detectarse")

    def test_formatted_credential_inside_state_json_still_flagged(self):
        cred = "sk-ant-" + "a" * 30
        (self.sandbox / "_project" / "03_project_state.json").write_text(
            json.dumps({"_bugs": [{"description": "el bug filtró " + cred}]}),
            encoding="utf-8",
        )
        errors: list[str] = []
        doctor.scan_potential_secrets(errors)
        self.assertTrue(errors, "credencial con formato dentro del state JSON debe detectarse")

    def test_structured_placeholder_check_covers_project_and_stack(self):
        # La cobertura perdida al retirar el scan de texto completo se
        # conserva vía chequeo campo por campo en validate_state.
        errors: list[str] = []
        doctor.validate_state(
            {"framework_mode": "initialized", "project": "[PROJECT_NAME]"},
            errors, [],
        )
        self.assertTrue(any("project" in e and "Placeholder" in e for e in errors))
        errors2: list[str] = []
        doctor.validate_state(
            {"framework_mode": "initialized", "project": "Mi producto",
             "stack": {"backend": "[Describe tu stack aquí]"}},
            errors2, [],
        )
        self.assertTrue(any("stack.backend" in e for e in errors2))

    def test_spanish_pattern_citation_is_not_flagged(self):
        # Bot FW-101 residual (4.11.1): la evidencia de ACs se escribe en
        # español; "cita el patrón `X` como ejemplo" no es un secreto.
        (self.sandbox / "_project" / "08_module_acceptance.md").write_text(
            "- [x] Sin secretos — evidencia: cita el patrón `BEGIN PRIVATE KEY` "
            "como ejemplo, no es un secreto real\n",
            encoding="utf-8",
        )
        errors: list[str] = []
        doctor.scan_potential_secrets(errors)
        self.assertEqual(errors, [], f"cita en español de un patrón no es secreto: {errors}")

    def test_real_pem_block_still_flagged(self):
        pem = "-----BEGIN " + "PRIVATE KEY-----\nMIIEvAIBADANBg\n"
        (self.sandbox / "deploy_key.pem").write_text(pem, encoding="utf-8")
        errors: list[str] = []
        doctor.scan_potential_secrets(errors)
        self.assertTrue(errors, "un bloque PEM real debe seguir detectándose")

    def test_completar_citation_in_state_json_does_not_fail_placeholders(self):
        # TL FW-012: prosa de auditoría que CITA el token [COMPLETAR].
        (self.sandbox / "_project" / "03_project_state.json").write_text(
            json.dumps({"evidence": "Sin [COMPLETAR] en 10_arch_constraints.md"}),
            encoding="utf-8",
        )
        errors: list[str] = []
        doctor.validate_placeholders("initialized", errors, [])
        self.assertEqual(errors, [])


class StateLockDiffHashTests(unittest.TestCase):
    """TL FW-008 · PA FW-047 · Bot FW-006: hash estable bajo .state.lock."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        subprocess.run(["git", "init", "-q", str(self.root)], check=True)
        subprocess.run(
            ["git", "-C", str(self.root), "-c", "user.email=t@t", "-c", "user.name=t",
             "commit", "--allow-empty", "-qm", "init"],
            check=True,
        )
        (self.root / "_project").mkdir()

    def tearDown(self):
        self._tmp.cleanup()

    def test_state_lock_does_not_change_hash(self):
        base = wr.workspace_diff_hash(self.root)
        lock = self.root / "_project" / ".state.lock"
        lock.write_text("123 2026-07-18T10:00:00\n", encoding="utf-8")
        self.assertEqual(wr.workspace_diff_hash(self.root), base)
        lock.write_text("456 2026-07-18T10:00:01\n", encoding="utf-8")
        self.assertEqual(wr.workspace_diff_hash(self.root), base)

    def test_real_untracked_change_still_detected(self):
        base = wr.workspace_diff_hash(self.root)
        (self.root / "producto.py").write_text("x = 1\n", encoding="utf-8")
        self.assertNotEqual(wr.workspace_diff_hash(self.root), base)

    def test_product_file_named_state_lock_stays_in_hash(self):
        # 4.11.2 (regresión de campo de Research App): solo el lock del
        # MOTOR (_project/.state.lock) queda fuera; un archivo de producto
        # homónimo en otro directorio sigue protegido por el hash del review.
        (self.root / "app").mkdir()
        base = wr.workspace_diff_hash(self.root)
        (self.root / "app" / ".state.lock").write_text("v1", encoding="utf-8")
        self.assertNotEqual(wr.workspace_diff_hash(self.root), base)
        self.assertIn("app/.state.lock", wr._tracked_product_paths(self.root))


class ReplayFirstDecisionTests(unittest.TestCase):
    """TL FW-009: replay estricto con register_decision como primer evento."""

    def test_first_event_register_decision_replays(self):
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / "state_log.jsonl"
            events = [
                {"ts": "2026-07-01T00:00:00Z", "action": "register_decision",
                 "payload": {"title": "ADR inicial", "context": "ctx", "decision": "dec"}},
                {"ts": "2026-07-01T00:01:00Z", "action": "register_bug",
                 "payload": {"description": "bug", "severity": "Menor",
                             "module_id": "M1", "affects_ac": False}},
                {"ts": "2026-07-01T00:02:00Z", "action": "register_clarification",
                 "payload": {"question": "¿qué pasa con X?", "blocked_scope": "M1"}},
            ]
            log.write_text(
                "\n".join(json.dumps(e) for e in events) + "\n", encoding="utf-8"
            )
            result = replay_state_log.replay(
                log, Path(tmp) / "out.json", dry_run=True, strict=True
            )
            self.assertEqual(result["applied"], 3)
            self.assertEqual(result["errors"], [])


class ReopenModuleTests(unittest.TestCase):
    """TL FW-006: reapertura canónica completed → in_progress."""

    def _state(self):
        # Forma real del motor: approvals y reviews viven bajo
        # workflow_receipts (record_approval / start_review).
        return {
            "modules": {"completed": ["M1"], "in_progress": [], "pending": []},
            "module_statuses": {"M1": "completed"},
            "workflow_receipts": {
                "approvals": {"M1": {"basis": {}}},
                "reviews": {"M1": [{"status": "pass"}]},
            },
        }

    def test_reopen_moves_module_and_invalidates_receipts(self):
        state = self._state()
        m = make_manager(state)
        msg = m._apply_event(
            "reopen_module",
            {"id": "M1", "reason": "RN-005 actualizado: ValidationResult.pbo ya no cumple"},
            "2026-07-18T00:00:00Z",
        )
        self.assertIn("reabierto", msg)
        self.assertEqual(state["module_statuses"]["M1"], "in_progress")
        self.assertIn("M1", state["modules"]["in_progress"])
        self.assertNotIn("M1", state["modules"]["completed"])
        self.assertNotIn("M1", state["workflow_receipts"]["approvals"])
        self.assertNotIn("M1", state["workflow_receipts"]["reviews"])

    def test_reopen_requires_substantive_reason(self):
        m = make_manager(self._state())
        with self.assertRaises(ValueError):
            m._apply_event("reopen_module", {"id": "M1", "reason": "corto"}, "t")

    def test_reopen_rejects_non_completed_module(self):
        state = self._state()
        state["module_statuses"]["M1"] = "in_progress"
        m = make_manager(state)
        with self.assertRaises(ValueError):
            m._apply_event(
                "reopen_module",
                {"id": "M1", "reason": "una razón suficientemente larga para pasar"},
                "t",
            )

    def test_transition_gate_backs_reopen_via_state_log(self):
        effect = validate_transition._status_effect(
            "reopen_module", {"id": "M1", "reason": "invariante RN-005 actualizado"}
        )
        self.assertEqual(effect, ("M1", "in_progress"))


class RegisterBugReopensCompletedModuleTests(unittest.TestCase):
    """TL FW-020: register_bug Crítico/Mayor+affects_ac sobre un módulo
    `completed` reabre por el mismo camino que reopen_module (invalida
    receipts de cierre obsoletos), en vez de una mutación directa a
    'blocked' que dejaba approval/review receipts apuntando a un módulo
    que ya no está cerrado."""

    def _state(self):
        return {
            "_bugs": [],
            "modules": {"completed": ["M1"], "in_progress": [], "pending": []},
            "module_statuses": {"M1": "completed"},
            "workflow_receipts": {
                "approvals": {"M1": {"basis": {}}},
                "reviews": {"M1": [{"status": "pass"}]},
            },
        }

    # 5.3.1 L3.2 (FW-300, D3-A del builder; cambio de veredicto D2): en vivo,
    # register_bug ya no reabre un módulo `completed` (sigue completed con sus
    # recibos; reabrir es reopen_module con motivo). El efecto de FW-020 se
    # conserva al reproducir un register_bug histórico, sin la marca
    # `_keeps_completed` (tests/test_l3_higiene_bugs.py).
    def test_critical_bug_on_completed_module_keeps_it_completed(self):
        state = self._state()
        m = make_manager(state)
        msg = m._apply_event(
            "register_bug",
            {"module_id": "M1", "severity": "Crítico", "description": "regresión real"},
            "2026-07-18T00:00:00Z",
        )
        self.assertIn("BUG-001 registrado", msg)
        self.assertEqual(state["module_statuses"]["M1"], "completed")
        self.assertIn("M1", state["modules"]["completed"])
        self.assertIn("M1", state["workflow_receipts"]["approvals"])
        self.assertIn("M1", state["workflow_receipts"]["reviews"])

    def test_historic_critical_bug_on_completed_module_invalidates_receipts_on_replay(self):
        state = self._state()
        m = make_manager(state)
        m._is_replay = True
        m._apply_event(
            "register_bug",
            {"module_id": "M1", "severity": "Crítico", "description": "regresión real"},
            "2026-07-18T00:00:00Z",
        )
        self.assertEqual(state["module_statuses"]["M1"], "blocked")
        self.assertIn("M1", state["modules"]["in_progress"])
        self.assertNotIn("M1", state["modules"]["completed"])
        self.assertNotIn("M1", state["workflow_receipts"]["approvals"])
        self.assertNotIn("M1", state["workflow_receipts"]["reviews"])

    def test_major_bug_affecting_ac_on_completed_module_keeps_it_completed(self):
        state = self._state()
        m = make_manager(state)
        m._apply_event(
            "register_bug",
            {
                "module_id": "M1", "severity": "Mayor",
                "description": "un AC ya no se cumple", "affects_ac": True,
            },
            "2026-07-18T00:00:00Z",
        )
        self.assertEqual(state["module_statuses"]["M1"], "completed")
        self.assertIn("M1", state["workflow_receipts"]["approvals"])

    def test_minor_bug_on_completed_module_does_not_reopen(self):
        # known-bad guard: severidad que no cumple el umbral no debe tocar
        # el módulo ni sus receipts.
        state = self._state()
        m = make_manager(state)
        m._apply_event(
            "register_bug",
            {"module_id": "M1", "severity": "Menor", "description": "cosmético"},
            "2026-07-18T00:00:00Z",
        )
        self.assertEqual(state["module_statuses"]["M1"], "completed")
        self.assertIn("M1", state["modules"]["completed"])
        self.assertIn("M1", state["workflow_receipts"]["approvals"])

    def test_critical_bug_on_in_progress_module_keeps_existing_behavior(self):
        state = self._state()
        state["module_statuses"]["M1"] = "in_progress"
        state["modules"] = {"completed": [], "in_progress": ["M1"], "pending": []}
        m = make_manager(state)
        m._apply_event(
            "register_bug",
            {"module_id": "M1", "severity": "Crítico", "description": "regresión"},
            "2026-07-18T00:00:00Z",
        )
        self.assertEqual(state["module_statuses"]["M1"], "blocked")
        self.assertIn("M1", state["workflow_receipts"]["approvals"])

    def test_transition_gate_backs_register_bug_completed_to_blocked(self):
        # FW-020 (known-good): completed -> blocked vía register_bug, respaldado
        # por state_log.jsonl, pasa el TRANSITION GATE.
        prev_state = {"module_statuses": {"M1": "completed"}}
        new_state = {"module_statuses": {"M1": "blocked"}}
        payload = {
            "module_id": "M1", "severity": "Mayor",
            "description": "un AC ya no se cumple", "affects_ac": True,
        }
        logged = dict([validate_transition._status_effect("register_bug", payload)])
        self.assertEqual(logged, {"M1": "blocked"})
        problems = validate_transition.transition_problems(prev_state, new_state, logged)
        self.assertEqual(problems, [])

    def test_transition_gate_rejects_unbacked_completed_to_blocked(self):
        # known-bad: sin el respaldo del log (p. ej. edición a mano del
        # estado), completed -> blocked sigue siendo ilegal.
        prev_state = {"module_statuses": {"M1": "completed"}}
        new_state = {"module_statuses": {"M1": "blocked"}}
        problems = validate_transition.transition_problems(prev_state, new_state, {})
        self.assertTrue(any("M1" in p for p in problems), problems)


class LockfileEarlyWarnTests(unittest.TestCase):
    """TL FW-010: aviso temprano de lockfile ausente."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.sandbox = Path(self._tmp.name)
        self._orig_root = doctor.ROOT
        doctor.ROOT = self.sandbox
        (self.sandbox / "pyproject.toml").write_text("[project]\nname='x'\n")

    def tearDown(self):
        doctor.ROOT = self._orig_root
        self._tmp.cleanup()

    def test_warns_when_deps_declared_without_lockfile(self):
        warnings: list[str] = []
        doctor.validate_dependency_lockfile_presence(
            "initialized", {"dependencies": ["pyarrow==24.0.0"]}, warnings
        )
        self.assertTrue(any("lockfile" in w for w in warnings))

    def test_silent_with_lockfile_or_without_deps(self):
        (self.sandbox / "requirements.lock").write_text("pyarrow==24.0.0\n")
        warnings: list[str] = []
        doctor.validate_dependency_lockfile_presence(
            "initialized", {"dependencies": ["pyarrow==24.0.0"]}, warnings
        )
        self.assertEqual(warnings, [])
        warnings2: list[str] = []
        doctor.validate_dependency_lockfile_presence(
            "initialized", {"dependencies": []}, warnings2
        )
        self.assertEqual(warnings2, [])


class ProtectedTermCasingTests(unittest.TestCase):
    """PA FW-044: el casing no es drift semántico."""

    def test_pure_case_variant_is_not_reported(self):
        self.assertNotIn("tesis", doctor.protected_term_variants("Tesis"))

    def test_structural_variants_survive(self):
        variants = doctor.protected_term_variants("ThesisKPI")
        self.assertIn("thesis_kpi", variants)


class PreCommitHookInstallTests(unittest.TestCase):
    """Bot FW-102: hooks framework-managed de versiones previas se actualizan."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self._tmp.name)
        subprocess.run(["git", "init", "-q", str(self.repo)], check=True)
        scripts = self.repo / ".agent" / "scripts"
        scripts.mkdir(parents=True)
        self.source = scripts / "pre_commit.sh"
        self.source.write_text(
            (ROOT / ".agent" / "scripts" / "pre_commit.sh").read_text(encoding="utf-8"),
            encoding="utf-8",
        )
        self.hook = self.repo / ".git" / "hooks" / "pre-commit"

    def tearDown(self):
        self._tmp.cleanup()

    def _install(self):
        return subprocess.run(
            ["bash", ".agent/scripts/pre_commit.sh", "install"],
            cwd=self.repo, capture_output=True, text=True,
        )

    def test_older_framework_hook_is_auto_updated(self):
        self.hook.write_text(
            "#!/usr/bin/env bash\n# Framework v3 pre-commit hook\n# (versión vieja)\nexit 0\n",
            encoding="utf-8",
        )
        result = self._install()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        # Cambiado en 5.3.1 L2.3 (TL-FW-320): se instala el lanzador, con respaldo de la copia vieja.
        launcher = subprocess.run(
            ["bash", ".agent/scripts/pre_commit.sh", "print-launcher"],
            cwd=self.repo, capture_output=True, text=True, check=True,
        ).stdout
        self.assertEqual(self.hook.read_text(encoding="utf-8"), launcher)
        self.assertEqual(len(list(self.hook.parent.glob("pre-commit.bak-*"))), 1)

    def test_manual_hook_is_never_overwritten(self):
        manual = "#!/bin/sh\n# hook artesanal del usuario\nexit 0\n"
        self.hook.write_text(manual, encoding="utf-8")
        result = self._install()
        self.assertEqual(result.returncode, 1)
        self.assertEqual(self.hook.read_text(encoding="utf-8"), manual)


if __name__ == "__main__":
    unittest.main()
