#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Pruebas de la superficie de wrappers generados en Claude Code (Plan 4.5 — Lote 5A).

Cubre:
1. Declaración en manifest: piloto de 2 comandos representativos (cierre-modulo, tdd)
   con claude_inline_body: true, y clasificación de disable_model_invocation: true
   para comandos que requieren iniciativa explícita del builder.
2. Contenido generado inline: los wrappers del piloto contienen el cuerpo real
   del workflow canónico y no el puntero; los no piloto preservan el puntero delgado.
3. Clasificación de invocación en frontmatter: disable-model-invocation en comandos
   del builder y ausencia estricta en procedimientos internos invocables por driver.
4. Determinista 16: La cadena del driver /ciclo invocando /tdd y /cierre-modulo
   permanece intacta (INVOCADO_POR_CICLO, señales CONTINUE/BLOCK/DECISION_REQUIRED).
5. Presupuesto de tokens: los wrappers generados inline permanecen por debajo de
   5.000 tokens renderizados (gate del Lote 4).
6. Referencias relativas y confinamiento: rutas relativas válidas al espacio de trabajo.
7. Validador del doctor: validate_claude_wrapper_contracts detecta regresiones.
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
import sys
sys.path.insert(0, str(ROOT / ".agent" / "scripts"))

import framework_doctor as doctor
import sync_claude_commands as sc


class ClaudeInlineWrappersTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.manifest = json.loads(
            (ROOT / ".agent" / "workflows" / "manifest.json").read_text(encoding="utf-8")
        )
        cls.commands_by_name = {c["name"]: c for c in cls.manifest["commands"]}

    def test_manifest_pilot_declarations(self):
        """El piloto de Lote 5A declara exactamente 2 comandos representativos con inline body."""
        inline_cmds = [
            c["name"] for c in self.manifest["commands"]
            if c.get("claude_inline_body", False)
        ]
        self.assertEqual(
            sorted(inline_cmds),
            ["cierre-modulo", "tdd"],
            f"El piloto debe tener exactamente 2 comandos representativos: {inline_cmds}",
        )

    def test_manifest_disable_model_invocation_classification(self):
        """Comandos del builder llevan disable_model_invocation; procedimientos internos no."""
        builder_initiative = ["nuevo-proyecto", "publicar-github", "actualizar-invariante"]
        for name in builder_initiative:
            cmd = self.commands_by_name.get(name)
            self.assertIsNotNone(cmd, f"Comando {name} debe existir en manifest")
            self.assertTrue(
                cmd.get("disable_model_invocation", False),
                f"/{name} requiere iniciativa explícita del builder y debe tener disable_model_invocation: true",
            )

        internal_procedures = ["tdd", "cierre-modulo", "cambio-seguro", "scope-siguiente"]
        for name in internal_procedures:
            cmd = self.commands_by_name.get(name)
            self.assertIsNotNone(cmd, f"Comando {name} debe existir en manifest")
            self.assertFalse(
                cmd.get("disable_model_invocation", False),
                f"/{name} es un procedimiento interno autorizado y NO debe tener disable_model_invocation",
            )

    def test_pilot_wrappers_contain_inline_body(self):
        """Los comandos del piloto en .claude/commands/ contienen el cuerpo real y no el puntero."""
        claude_dir = ROOT / ".claude" / "commands"

        for name in ("cierre-modulo", "tdd"):
            path = claude_dir / f"{name}.md"
            self.assertTrue(path.exists(), f"{path} debe existir")
            text = path.read_text(encoding="utf-8")

            # Marcador GENERATED presente
            self.assertIn(sc.GENERATED, text, f"{name}.md debe tener marcador GENERATED")
            # Provenance trazable
            self.assertIn(f"Fuente canónica: `.agent/workflows/{name}.md`", text)
            # NO debe contener el puntero de wrapper delgado
            self.assertNotIn(
                "No dupliques lógica en este wrapper",
                text,
                f"{name}.md no debe ser un puntero delgado",
            )
            # Debe contener secciones sustantivas del workflow
            self.assertIn("## Contrato anidado", text)
            self.assertIn("## Pasos", text)

        # cierre-modulo específico: obligaciones de cierre normativas arriba
        cierre_text = (claude_dir / "cierre-modulo.md").read_text(encoding="utf-8")
        self.assertIn("Obligaciones de cierre (recuperación independiente)", cierre_text)
        self.assertIn("Eres el agente de cierre de módulo", cierre_text)

        # tdd específico: ciclo rojo-verde
        tdd_text = (claude_dir / "tdd.md").read_text(encoding="utf-8")
        self.assertIn("Ciclo rojo-verde-refactorizar", tdd_text)

    def test_non_pilot_wrappers_remain_thin_pointers(self):
        """Los comandos que no están en el piloto conservan el wrapper delgado con puntero."""
        claude_dir = ROOT / ".claude" / "commands"
        for name in ("arrancar", "gycd", "reconciliar", "cambio-seguro"):
            path = claude_dir / f"{name}.md"
            self.assertTrue(path.exists(), f"{path} debe existir")
            text = path.read_text(encoding="utf-8")
            self.assertIn("Ejecuta el artefacto canónico indicado arriba", text)
            self.assertIn("No dupliques lógica en este wrapper", text)

    def test_frontmatter_disable_model_invocation_presence(self):
        """disable-model-invocation se emite en frontmatter YAML para comandos aplicables."""
        claude_dir = ROOT / ".claude" / "commands"

        for name in ("nuevo-proyecto", "publicar-github", "actualizar-invariante"):
            text = (claude_dir / f"{name}.md").read_text(encoding="utf-8")
            self.assertIn(
                "disable-model-invocation: true",
                text,
                f"/{name} debe tener disable-model-invocation: true en frontmatter",
            )

        for name in ("tdd", "cierre-modulo", "cambio-seguro", "scope-siguiente"):
            text = (claude_dir / f"{name}.md").read_text(encoding="utf-8")
            self.assertNotIn(
                "disable-model-invocation: true",
                text,
                f"/{name} NO debe tener disable-model-invocation: true en frontmatter",
            )

    def test_deterministic_walkthrough_16_driver_chain_intact(self):
        """Item 16: Driver /ciclo invocando /tdd y /cierre-modulo con cadena intacta."""
        claude_dir = ROOT / ".claude" / "commands"

        tdd_text = (claude_dir / "tdd.md").read_text(encoding="utf-8")
        cierre_text = (claude_dir / "cierre-modulo.md").read_text(encoding="utf-8")

        # Señales de contrato anidado intactas
        for text, name in ((tdd_text, "tdd"), (cierre_text, "cierre-modulo")):
            self.assertIn("INVOCADO_POR_CICLO: true", text, f"{name} debe soportar INVOCADO_POR_CICLO")
            self.assertIn("INTERNAL_RESULT: CONTINUE", text, f"{name} debe retornar CONTINUE")
            self.assertIn("INTERNAL_RESULT: BLOCK", text, f"{name} debe retornar BLOCK")
            self.assertIn("INTERNAL_RESULT: DECISION_REQUIRED", text, f"{name} debe retornar DECISION_REQUIRED")
            self.assertNotIn("disable-model-invocation: true", text, f"{name} debe ser invocable por el driver")

        # Driver ciclo.md referencia a ambos
        ciclo_text = (ROOT / ".agent" / "workflows" / "ciclo.md").read_text(encoding="utf-8")
        self.assertIn("/tdd", ciclo_text)
        self.assertIn("/cierre-modulo", ciclo_text)

    def test_rendered_size_under_5000_tokens_gate(self):
        """Los wrappers generados inline no superan el gate de 5.000 tokens renderizados."""
        claude_dir = ROOT / ".claude" / "commands"

        for name in ("cierre-modulo", "tdd"):
            text = (claude_dir / f"{name}.md").read_text(encoding="utf-8")
            tokens = doctor.estimate_workflow_tokens(text)
            self.assertLessEqual(
                tokens,
                5000,
                f".claude/commands/{name}.md excede 5.000 tokens ({tokens} estimados)",
            )

    def test_workspace_relative_references(self):
        """Las referencias a ficheros dentro de los wrappers generados son relativas al repo."""
        claude_dir = ROOT / ".claude" / "commands"

        for name in ("cierre-modulo", "tdd"):
            text = (claude_dir / f"{name}.md").read_text(encoding="utf-8")
            # No escapes de directorio ../
            self.assertNotIn("../", text, f"{name}.md no debe contener '../'")
            # Scripts citados existen
            for script in ("validate_closure.py", "validate_acceptance.py", "validate_no_stubs.py"):
                if script in text:
                    self.assertTrue(
                        (ROOT / ".agent" / "scripts" / script).exists(),
                        f"Script {script} citado en {name}.md debe existir",
                    )

    def test_doctor_validator_detects_wrapper_contract_drift(self):
        """validate_claude_wrapper_contracts del doctor detecta regresiones."""
        errors: list[str] = []
        doctor.validate_claude_wrapper_contracts(self.manifest, errors)
        self.assertEqual(errors, [], f"Doctor no debe fallar en el repo limpio: {errors}")

        # Simular regresión 1: wrapper inline que tiene puntero
        fake_manifest = {
            "commands": [
                {
                    "name": "arrancar",
                    "claude_command": True,
                    "claude_inline_body": True,
                }
            ]
        }
        errors_regression: list[str] = []
        doctor.validate_claude_wrapper_contracts(fake_manifest, errors_regression)
        self.assertTrue(
            any("tiene puntero en vez de cuerpo inline" in e for e in errors_regression),
            f"Debía detectar falta de cuerpo inline: {errors_regression}",
        )

        # Simular regresión 2: comando que debía tener disable-model-invocation pero no lo tiene
        fake_manifest_dmi = {
            "commands": [
                {
                    "name": "arrancar",
                    "claude_command": True,
                    "disable_model_invocation": True,
                }
            ]
        }
        errors_dmi: list[str] = []
        doctor.validate_claude_wrapper_contracts(fake_manifest_dmi, errors_dmi)
        self.assertTrue(
            any("debe tener 'disable-model-invocation: true'" in e for e in errors_dmi),
            f"Debía detectar ausencia de disable-model-invocation: {errors_dmi}",
        )

        # Simular regresión 3: procedimiento interno que erróneamente tiene disable-model-invocation
        fake_manifest_bad_internal = {
            "commands": [
                {
                    "name": "nuevo-proyecto",
                    "claude_command": True,
                    "disable_model_invocation": False,
                }
            ]
        }
        errors_internal: list[str] = []
        doctor.validate_claude_wrapper_contracts(fake_manifest_bad_internal, errors_internal)
        self.assertTrue(
            any("no debe tener 'disable-model-invocation: true'" in e for e in errors_internal),
            f"Debía detectar presencia indebida de disable-model-invocation: {errors_internal}",
        )

    def test_sync_check_passes(self):
        """sync_claude_commands.py --check pasa limpiamente sin drift."""
        manifest = sc.load_manifest()
        issues = sc.check_sync(manifest)
        self.assertEqual(issues, [], f"check_sync reporta drift: {issues}")


if __name__ == "__main__":
    unittest.main()
