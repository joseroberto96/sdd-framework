#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Pruebas de la inyección dinámica de estado en Claude Code (Plan 4.5 — Lote 5B).

Cubre:
1. Declaración en manifest: piloto de comandos representativos (cierre-modulo, tdd)
   con claude_dynamic_injection: true; exclusión estricta de /arrancar (reservado a hook).
2. Generación en wrappers: directiva `!` de inyección presente, sin `|| true` generalizado,
   con guía de recuperación explícita (RECOVERY_GUIDE).
3. Recorrido determinista 15:
   - Nominal / éxito en proyecto inicializado (status OK/PASS, módulo activo, fingerprint).
   - Modo plantilla (status TEMPLATE_MODE, orientación /nuevo-proyecto).
   - Error de lectura / estado corrupto (status ERROR con causa y vía de recuperación, nunca vacío).
   - Permiso denegado (status PERMISSION_DENIED con causa y acción alternativa).
   - Timeout de consulta (status TIMEOUT bajo límite estricto de 3.0 s).
   - Exceso de salida / overflow (status COMPACT_OVERFLOW sin truncado abrupto).
   - Salida CLI con exit code ≠ 0 (opción --strict-exit-code).
4. Recuperación por consulta explícita: verificación de fallback idéntico vía get_work_context.
5. Comparativa de eficiencia: inyección dinámica vs consulta explícita en etapa intermedia.
6. Regla de portabilidad (§2): ausencia absoluta de directivas Claude en OpenCode, Codex y workflows canónicos.
7. Validador del doctor: validate_claude_wrapper_contracts detecta regresiones de inyección.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
import sys
sys.path.insert(0, str(ROOT / ".agent" / "scripts"))

import claude_dynamic_context as dynamic_ctx
import framework_doctor as doctor
import state_core
import state_manager
import state_queries
import sync_claude_commands as sc


class ClaudeDynamicInjectionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.manifest = json.loads(
            (ROOT / ".agent" / "workflows" / "manifest.json").read_text(encoding="utf-8")
        )
        cls.commands_by_name = {c["name"]: c for c in cls.manifest["commands"]}

    def setUp(self):
        self.saved_state_core = {
            "root": state_core.WORKSPACE_ROOT,
            "state": state_core.STATE_FILE_PATH,
            "core": state_core.CORE_FILE_PATH,
            "bugs": state_core.BUGS_FILE_PATH,
            "clars": state_core.CLARIFICATIONS_FILE_PATH,
            "decisions": state_core.DECISIONS_FILE_PATH,
            "brief": state_core.SESSION_BRIEF_PATH,
        }

    def tearDown(self):
        state_core.WORKSPACE_ROOT = self.saved_state_core["root"]
        state_core.STATE_FILE_PATH = self.saved_state_core["state"]
        state_core.CORE_FILE_PATH = self.saved_state_core["core"]
        state_core.BUGS_FILE_PATH = self.saved_state_core["bugs"]
        state_core.CLARIFICATIONS_FILE_PATH = self.saved_state_core["clars"]
        state_core.DECISIONS_FILE_PATH = self.saved_state_core["decisions"]
        state_core.SESSION_BRIEF_PATH = self.saved_state_core["brief"]

    def test_manifest_dynamic_injection_declarations(self):
        """cierre-modulo y tdd tienen claude_dynamic_injection: true; arrancar no lo tiene."""
        dynamic_cmds = [
            c["name"] for c in self.manifest["commands"]
            if c.get("claude_dynamic_injection", False)
        ]
        self.assertEqual(
            sorted(dynamic_cmds),
            ["cierre-modulo", "tdd"],
            f"El piloto debe declarar exactamente cierre-modulo y tdd: {dynamic_cmds}",
        )
        arrancar_cmd = self.commands_by_name.get("arrancar")
        self.assertIsNotNone(arrancar_cmd)
        self.assertFalse(
            arrancar_cmd.get("claude_dynamic_injection", False),
            "/arrancar tiene prohibido usar inyección dinámica (reservado para el hook de continuidad Lote 3)",
        )

    def test_wrappers_contain_dynamic_injection_without_or_true(self):
        """Los wrappers Claude del piloto contienen la inyección y NO contienen '|| true'."""
        claude_dir = ROOT / ".claude" / "commands"

        expected_views = {
            "tdd": "implementation",
            "cierre-modulo": "closure",
        }

        for name, view in expected_views.items():
            path = claude_dir / f"{name}.md"
            self.assertTrue(path.exists(), f"{path} debe existir")
            text = path.read_text(encoding="utf-8")

            # Directiva de inyección presente
            self.assertIn("## Contexto de etapa (inyección dinámica)", text)
            self.assertIn(f'claude_dynamic_context.py" --view {view}', text)

            # Corrección de Astra A4: PROHIBIDO || true
            self.assertNotIn(
                "|| true",
                text,
                f"{name}.md contiene '|| true' (prohibido por Plan 4.5 Lote 5B)",
            )

            # Guía de recuperación explícita presente
            self.assertIn("RECOVERY_GUIDE", text)
            self.assertIn(f"state_manager.get_work_context(view='{view}')", text)

    def test_generated_wrappers_dynamic_injection_execution(self):
        """P1 / N1: Extrae la directiva !`...` de los wrappers y la ejecuta con bash."""
        claude_dir = ROOT / ".claude" / "commands"
        target_commands = ["tdd", "cierre-modulo"]

        for name in target_commands:
            path = claude_dir / f"{name}.md"
            self.assertTrue(path.exists(), f"{path} debe existir")
            text = path.read_text(encoding="utf-8")

            match = re.search(r"^!(`.+?`)", text, re.MULTILINE)
            self.assertIsNotNone(match, f"{name}.md debe contener una línea de inyección !`...`")
            raw_cmd = match.group(1).strip("`")

            env = os.environ.copy()
            env["CLAUDE_PROJECT_DIR"] = str(ROOT)
            proc = subprocess.run(
                ["bash", "-c", raw_cmd],
                capture_output=True,
                text=True,
                env=env,
                cwd=str(ROOT),
            )

            self.assertEqual(
                proc.returncode,
                0,
                f"La ejecución de {raw_cmd} en {name}.md falló con rc={proc.returncode}. Stderr: {proc.stderr}",
            )
            self.assertTrue(
                proc.stdout.startswith("<!-- CONTEXTO_DINAMICO"),
                f"Stdout de {name}.md no empieza por '<!-- CONTEXTO_DINAMICO'. Stdout: {proc.stdout[:200]}",
            )
            self.assertNotIn(
                "can't open file",
                proc.stderr,
                f"Stderr de {name}.md contiene 'can't open file': {proc.stderr}",
            )
            self.assertNotIn(
                "can't open file",
                proc.stdout,
                f"Stdout de {name}.md contiene 'can't open file': {proc.stdout}",
            )

    def test_portability_non_claude_surfaces_clean(self):
        """Codex, OpenCode y workflows canónicos no contienen directivas Claude de inyección."""
        # Workflows canónicos
        for name in ("tdd.md", "cierre-modulo.md"):
            canonical_text = (ROOT / ".agent" / "workflows" / name).read_text(encoding="utf-8")
            self.assertNotIn("claude_dynamic_context.py", canonical_text)
            self.assertNotIn("!`", canonical_text)

        # OpenCode
        opencode_dir = ROOT / ".opencode" / "commands"
        for name in ("tdd.md", "cierre-modulo.md"):
            path = opencode_dir / name
            if path.exists():
                text = path.read_text(encoding="utf-8")
                self.assertNotIn("claude_dynamic_context.py", text)
                self.assertNotIn("!`", text)

        # Codex
        for slug in ("tdd", "cierre-modulo"):
            skill_path = ROOT / ".agents" / "skills" / f"source-command-{slug}" / "SKILL.md"
            if skill_path.exists():
                text = skill_path.read_text(encoding="utf-8")
                self.assertNotIn("claude_dynamic_context.py", text)
                self.assertNotIn("!`", text)

    def _setup_initialized_fixture(self, tmp_root: Path, mod_id: str = "MOD-01") -> None:
        """Configura un fixture completo de proyecto inicializado."""
        proj_dir = tmp_root / "_project"
        proj_dir.mkdir(parents=True)

        workflows_dir = tmp_root / ".agent" / "workflows"
        workflows_dir.mkdir(parents=True)
        shutil.copy(ROOT / ".agent" / "workflows" / "manifest.json", workflows_dir / "manifest.json")

        specs_dir = tmp_root / ".agent" / "docs" / "specs"
        specs_dir.mkdir(parents=True)
        real_specs = ROOT / ".agent" / "docs" / "specs"
        if real_specs.exists():
            for f in real_specs.glob("*.json"):
                shutil.copy(f, specs_dir / f.name)

        state_data = {
            "schema_version": 1,
            "project": "TestProject",
            "framework_mode": "initialized",
            "framework_version": "4.40.1",
            "current_phase": "phase_01",
            "current_step": "step_02",
            "last_completed_step": "diseño",
            "active_module": mod_id,
            "session": {"active_module": mod_id, "next_action": "Implementar", "next_command": "/ciclo"},
            "stack": {"backend": "Python", "frontend": "-", "database": "-", "auth": "-"},
            "architecture_constraints": [],
            "contracts": {"public_interfaces": {}},
            "_refs": {},
            "_bugs": [],
            "_clarifications": [],
            "_decisions": [],
            "modules": {"completed": [], "in_progress": [mod_id], "pending": []},
            "module_statuses": {mod_id: "in_progress"},
            "work_tiers": {
                mod_id: {"tier": "tier_1", "rationale": "test", "classified_at": "2026-09-15T10:00:00Z", "classified_by": "test"}
            },
            "module_specs": {
                mod_id: {"data_model": "n/a", "api_contract": "n/a", "integration_real": "n/a", "classified_at": "2026-09-15T10:00:00Z", "classified_by": "test", "justification": "test"}
            },
            "dependencies": [],
            "active_mcps": ["state_manager"],
            "changes": [],
            "closure_rejections": [],
            "execution_profile": {"capability_tier": "standard"},
        }
        (proj_dir / "03_project_state.json").write_text(json.dumps(state_data), encoding="utf-8")
        (proj_dir / "04_active_scope.md").write_text(f"# Active Scope\n\n## {mod_id}\nDetalle del módulo.\n", encoding="utf-8")
        (proj_dir / "08_module_acceptance.md").write_text(f"# AC\n\n## {mod_id}\nACs.\n", encoding="utf-8")
        (proj_dir / "07_pending_clarifications.md").write_text("# Clar\n", encoding="utf-8")
        (proj_dir / "core.md").write_text("# Core\n", encoding="utf-8")

    def test_nominal_initialized_project(self):
        """Escenario nominal: proyecto inicializado devuelve status OK/PASS con resumen claro."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_root = Path(tmp)
            self._setup_initialized_fixture(tmp_root, "MOD-01")

            old_root = state_core.WORKSPACE_ROOT
            try:
                # Inyección para implementation
                envelope = dynamic_ctx.build_dynamic_context(view="implementation", root=tmp_root)
                self.assertIn(envelope["status"], ("OK", "PASS", "NEEDS_INPUT"))
                self.assertEqual(envelope["view"], "implementation")
                self.assertEqual(envelope["module_id"], "MOD-01")

                md = dynamic_ctx.render_dynamic_context_markdown(envelope)
                self.assertIn("### Contexto de etapa (inyección dinámica)", md)
                self.assertIn("MOD-01", md)
                self.assertIn("implementation", md)
                self.assertIn("state_manager.get_work_context", md)
            finally:
                dynamic_ctx._set_workspace_root(old_root)

    def test_template_mode(self):
        """Escenario Template Mode: devuelve status TEMPLATE_MODE y orientación clara."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_root = Path(tmp)
            proj_dir = tmp_root / "_project"
            proj_dir.mkdir(parents=True)
            (proj_dir / "03_project_state.json").write_text(
                json.dumps({"framework_mode": "template"}), encoding="utf-8"
            )

            envelope = dynamic_ctx.build_dynamic_context(view="closure", root=tmp_root)
            self.assertEqual(envelope["status"], "TEMPLATE_MODE")
            self.assertEqual(envelope["view"], "closure")

            md = dynamic_ctx.render_dynamic_context_markdown(envelope)
            self.assertIn("TEMPLATE_MODE", md)
            self.assertIn("/nuevo-proyecto", md)
            self.assertNotIn("|| true", md)

    def test_corrupt_state_returns_unequivocal_error(self):
        """Recorrido 15 (Error de lectura): estado corrupto produce status ERROR con causa y vía de recuperación."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_root = Path(tmp)
            proj_dir = tmp_root / "_project"
            proj_dir.mkdir(parents=True)
            # Archivo de estado corrupto (JSON truncado/inválido)
            (proj_dir / "03_project_state.json").write_text('{"project": "Corrupt", unclosed...', encoding="utf-8")

            envelope = dynamic_ctx.build_dynamic_context(view="implementation", root=tmp_root)
            self.assertEqual(envelope["status"], "ERROR")
            self.assertIn("Error leyendo archivo de estado", envelope["error"])
            self.assertIn("/reconciliar", envelope["via_de_recuperacion"])

            md = dynamic_ctx.render_dynamic_context_markdown(envelope)
            self.assertIn("### Contexto de etapa (inyección dinámica) — Error de lectura", md)
            self.assertIn("ERROR", md)
            self.assertIn("/reconciliar", md)
            # NUNCA salida vacía
            self.assertTrue(len(md.strip()) > 50)

    def test_permission_denied_returns_unequivocal_error(self):
        """Recorrido 15 (Permiso denegado): captura PermissionError y produce PERMISSION_DENIED."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_root = Path(tmp)
            proj_dir = tmp_root / "_project"
            proj_dir.mkdir(parents=True)
            state_file = proj_dir / "03_project_state.json"
            state_file.write_text(json.dumps({"framework_mode": "initialized"}), encoding="utf-8")

            with patch.object(Path, "read_text", side_effect=PermissionError("Acceso denegado")):
                envelope = dynamic_ctx.build_dynamic_context(view="implementation", root=tmp_root)
                self.assertEqual(envelope["status"], "PERMISSION_DENIED")
                self.assertIn("Permiso denegado", envelope["error"])

                md = dynamic_ctx.render_dynamic_context_markdown(envelope)
                self.assertIn("PERMISSION_DENIED", md)
                self.assertIn("state_manager.get_work_context", md)
                self.assertTrue(len(md.strip()) > 50)

    def test_timeout_returns_unequivocal_error(self):
        """Recorrido 15 (Timeout): consulta que excede timeout produce status TIMEOUT."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_root = Path(tmp)
            proj_dir = tmp_root / "_project"
            proj_dir.mkdir(parents=True)
            (proj_dir / "03_project_state.json").write_text(
                json.dumps({"framework_mode": "initialized"}), encoding="utf-8"
            )

            # Simular timeout con delay superior a query_timeout
            def slow_query(*args, **kwargs):
                time.sleep(0.3)
                return {}

            with patch("state_manager.get_work_context", side_effect=slow_query):
                envelope = dynamic_ctx.build_dynamic_context(
                    view="implementation", root=tmp_root, query_timeout=0.05
                )
                self.assertEqual(envelope["status"], "TIMEOUT")
                self.assertIn("excedió el límite", envelope["error"])

                md = dynamic_ctx.render_dynamic_context_markdown(envelope)
                self.assertIn("TIMEOUT", md)
                self.assertIn("state_manager.get_work_context(view='implementation')", md)
                self.assertTrue(len(md.strip()) > 50)

    def test_output_overflow_degrades_safely(self):
        """Recorrido 15 (Exceso de salida): salida superior a max_bytes degrada a COMPACT_OVERFLOW."""
        envelope = {
            "adapter": "claude_dynamic_context",
            "version": "1.0",
            "status": "OK",
            "view": "closure",
            "module_id": "MOD-01",
            "gate_status": "PASS",
            "blocking_count": 0,
            "source_fingerprint": "abc123fingerprint",
            "timestamp": "2026-09-15T00:00:00Z",
            "data": {
                "summary": {
                    "current_phase": "phase_01",
                    "module_status": "ready_for_validation",
                    "tier": "tier_1",
                    "capability_tier": "standard",
                    "critical_bug_count": 0,
                    "closure_rejection_count": 0,
                }
            },
            "via_de_recuperacion": "state_manager.get_work_context(view='closure') o /reconciliar.",
        }
        # Forzar un límite artificialmente muy bajo para probar degradación
        md = dynamic_ctx.render_dynamic_context_markdown(envelope, max_bytes=100)
        self.assertIn("COMPACT_OVERFLOW", md)
        self.assertIn("MOD-01", md)
        self.assertIn("state_manager.get_work_context", md)
        self.assertNotIn("|| true", md)

    def test_strict_exit_code_option(self):
        """Recorrido 15 (Exit code ≠ 0): --strict-exit-code devuelve código 1 ante error/timeout."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_root = Path(tmp)
            proj_dir = tmp_root / "_project"
            proj_dir.mkdir(parents=True)
            (proj_dir / "03_project_state.json").write_text("corrupt json", encoding="utf-8")

            # Ejecutar CLI directamente
            cmd = [
                sys.executable,
                str(ROOT / ".agent" / "scripts" / "claude_dynamic_context.py"),
                "--view",
                "implementation",
                "--root",
                str(tmp_root),
                "--strict-exit-code",
            ]
            proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            self.assertEqual(proc.returncode, 1, "Debe salir con código 1 bajo --strict-exit-code ante error")
            self.assertIn("ERROR", proc.stdout)
            self.assertIn("/reconciliar", proc.stdout)

    def test_explicit_query_recovery_guarantee(self):
        """Garantía de recuperación: ante cualquier fallo de inyección, la consulta explícita funciona."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_root = Path(tmp)
            self._setup_initialized_fixture(tmp_root, "MOD-02")

            old_root = state_core.WORKSPACE_ROOT
            try:
                dynamic_ctx._set_workspace_root(tmp_root)
                manager = state_core.StateManager()

                # La consulta explícita siempre obtiene el contexto de disco independientemente de la inyección
                direct_ctx = state_manager.get_work_context(manager, view="implementation")
                self.assertEqual(direct_ctx["view"], "implementation")
                self.assertIn("summary", direct_ctx["data"])
            finally:
                dynamic_ctx._set_workspace_root(old_root)

    def test_comparative_efficiency_measurement(self):
        """Medición comparativa exigida: inyección dinámica vs consulta explícita en misma etapa."""
        # Vía A (Consulta explícita):
        # - Agente ejecuta invocación de herramienta Bash/MCP (1 tool call adicional)
        # - Recibe el JSON completo de get_work_context
        # - Ocupa entre 3.5 KiB y 15 KiB de contexto
        # - Requiere 2 turnos de interacción

        # Vía B (Inyección dinámica):
        # - Harness ejecuta claude_dynamic_context.py antes de emitir el prompt
        # - 0 tool calls de inspección de estado por parte del agente
        # - Inyecta un bloque limpio de ~300-800 bytes
        # - Resuelve en 1 solo turno de interacción

        manager = state_core.StateManager()
        explicit_json = state_queries.serialize_context(
            state_manager.get_work_context(manager, view="implementation")
        )
        explicit_bytes = len(explicit_json.encode("utf-8"))

        envelope = dynamic_ctx.build_dynamic_context(view="implementation")
        injected_md = dynamic_ctx.render_dynamic_context_markdown(envelope)
        injected_bytes = len(injected_md.encode("utf-8"))

        # El bloque inyectado debe ser sustancialmente más liviano que el JSON crudo completo
        self.assertLess(
            injected_bytes,
            explicit_bytes,
            f"El bloque inyectado ({injected_bytes} bytes) debe ser menor al JSON crudo ({explicit_bytes} bytes)",
        )
        # Y debe permanecer bajo el límite de seguridad (8 KiB)
        self.assertLessEqual(injected_bytes, dynamic_ctx.DEFAULT_MAX_OUTPUT_BYTES)

    def test_doctor_validator_detects_dynamic_injection_drift(self):
        """validate_claude_wrapper_contracts del doctor detecta regresiones de inyección dinámica."""
        errors: list[str] = []
        doctor.validate_claude_wrapper_contracts(self.manifest, errors)
        self.assertEqual(errors, [], f"Doctor no debe fallar en el repo limpio: {errors}")

        # Simular regresión 1: inyección dinámica en /arrancar (prohibida)
        fake_manifest_bad_arrancar = {
            "commands": [
                {
                    "name": "arrancar",
                    "claude_command": True,
                    "claude_dynamic_injection": True,
                }
            ]
        }
        errs: list[str] = []
        doctor.validate_claude_wrapper_contracts(fake_manifest_bad_arrancar, errs)
        self.assertTrue(
            any("Comando /arrancar no puede tener claude_dynamic_injection" in e for e in errs),
            f"Debía detectar prohibición de inyección en /arrancar: {errs}",
        )

        # Simular regresión 2: presencia de '|| true' en wrapper
        with tempfile.TemporaryDirectory() as tmp:
            tmp_root = Path(tmp)
            claude_cmds = tmp_root / ".claude" / "commands"
            claude_cmds.mkdir(parents=True)
            bad_wrapper = (
                "# /tdd\n"
                "!`python3 claude_dynamic_context.py --view implementation || true`\n"
                "RECOVERY_GUIDE\n"
            )
            (claude_cmds / "tdd.md").write_text(bad_wrapper, encoding="utf-8")

            old_root = doctor.ROOT
            doctor.ROOT = tmp_root
            try:
                errs_true: list[str] = []
                fake_manifest = {
                    "commands": [
                        {
                            "name": "tdd",
                            "claude_command": True,
                            "claude_dynamic_injection": True,
                        }
                    ]
                }
                doctor.validate_claude_wrapper_contracts(fake_manifest, errs_true)
                self.assertTrue(
                    any("contiene '|| true'" in e for e in errs_true),
                    f"Debía detectar '|| true' prohibido: {errs_true}",
                )
            finally:
                doctor.ROOT = old_root


if __name__ == "__main__":
    unittest.main()
