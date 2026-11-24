#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Pruebas de las reglas condicionadas por ruta en Claude Code (Plan 4.5 — Lote 5C).

Cubre:
1. Declaración en manifest: sección claude_rules con piloto de reglas representativas
   (frontend-design, cross-platform-web) y patrones paths válidos.
2. Generación en .claude/rules/: frontmatter paths YAML, marcador GENERATED y referencia
   a la fuente canónica.
3. Partición estricta dentro de cada skill (Condición 3):
   - Obligaciones de archivo (chunking, CSS dvh, pointer events, 5 estados UI) migran a la regla.
   - Obligaciones de etapa (fuentes, 12_design_profile.md, checklist de cierre) permanecen en .agent/skills/.
4. Recorrido determinista 17:
   - Obligación de etapa que debe activarse antes de abrir un archivo llega a tiempo tras 5C
     a través de workflows de etapa y .agent/skills/.
5. Regla de portabilidad (§2):
   - Cero exclusividad: toda obligación existe en la fuente canónica en .agent/skills/.
   - Codex y OpenCode permanecen limpios y utilizan sus superficies correspondientes.
6. Resolución del solape con /foco (Condición 2 — Salida 1: Coexistencia ortogonal):
   - /foco gobierna la lente conversacional/estratégica de etapa; .claude/rules/ gobierna los
     invariantes mecánicos de archivo.
7. Sincronización e idempotencia:
   - sync_claude_commands.py --check pasa sin drift.
   - framework_doctor.py valida las reglas y detecta drift/huérfanos.
8. Medición real de contexto (Condición 1):
   - Medición exacta de bytes y tokens estimados del incremento de contexto.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
import sys
sys.path.insert(0, str(ROOT / ".agent" / "scripts"))

import framework_doctor as doctor
import sync_claude_commands as sc


class ClaudeRulesTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.manifest = json.loads(
            (ROOT / ".agent" / "workflows" / "manifest.json").read_text(encoding="utf-8")
        )
        cls.claude_rules = {r["name"]: r for r in cls.manifest.get("claude_rules", [])}

    def test_manifest_declares_claude_rules_pilot(self):
        """manifest.json declara claude_rules con el piloto frontend-design y cross-platform-web."""
        self.assertIn("frontend-design", self.claude_rules)
        self.assertIn("cross-platform-web", self.claude_rules)

        fe = self.claude_rules["frontend-design"]
        self.assertEqual(fe["source"], ".agent/skills/frontend_design.md")
        self.assertTrue(any("html" in p or "tsx" in p for p in fe["paths"]))

        cp = self.claude_rules["cross-platform-web"]
        self.assertEqual(cp["source"], ".agent/skills/cross_platform_web.md")
        self.assertTrue(any("js" in p or "ts" in p for p in cp["paths"]))
        self.assertIn("package.json", cp["paths"])

    def test_generated_rules_exist_with_yaml_frontmatter_and_generated_marker(self):
        """.claude/rules/ contiene archivos con frontmatter paths YAML y marcador GENERATED."""
        rules_dir = ROOT / ".claude" / "rules"
        self.assertTrue(rules_dir.is_dir())

        for name, rule in self.claude_rules.items():
            rule_path = rules_dir / f"{name}.md"
            self.assertTrue(rule_path.exists(), f"Falta {rule_path}")
            text = rule_path.read_text(encoding="utf-8")

            # Frontmatter YAML con paths
            self.assertTrue(text.startswith("---\n"), f"{name} no empieza con frontmatter")
            self.assertIn("paths:\n", text)
            for p in rule["paths"]:
                self.assertIn(f'  - "{p}"', text)

            # Marcador GENERATED
            self.assertIn(sc.GENERATED, text)

            # Cita a la fuente canónica
            self.assertIn(f"fuente canónica: {rule['source']}", text)

    def test_strict_partition_frontend_design(self):
        """Partición estricta en frontend_design: archivo vs etapa (Condición 3)."""
        rule_text = (ROOT / ".claude" / "rules" / "frontend-design.md").read_text(encoding="utf-8")
        skill_text = (ROOT / ".agent" / "skills" / "frontend_design.md").read_text(encoding="utf-8")

        # Obligaciones de archivo DEBEN estar en la regla de ruta
        self.assertIn("Descomposición por responsabilidad", rule_text)
        self.assertIn("Monolitos de UI prohibidos", rule_text)
        self.assertIn("Umbral de §Chunking", rule_text)
        self.assertIn("300 líneas", rule_text)
        self.assertIn("500 líneas", rule_text)
        self.assertIn("Estados de interfaz obligatorios", rule_text)
        self.assertIn("loading, empty, error", rule_text)
        self.assertIn("Controles ergonómicos", rule_text)
        self.assertIn("Acciones destructivas", rule_text)

        # Obligaciones de etapa NO deben estar en la regla de ruta (se quedan en la skill)
        self.assertNotIn("_project/12_design_profile.md", rule_text)
        self.assertNotIn("Si falta perfil visual", rule_text)
        self.assertNotIn("Construir la pantalla o flujo real, no una landing explicativa", rule_text)
        self.assertNotIn("Priorizar flujos frecuentes", rule_text)

        # Pero SÍ deben estar en la skill canónica
        self.assertIn("_project/12_design_profile.md", skill_text)
        self.assertIn("Si falta perfil visual", skill_text)
        self.assertIn("landing explicativa", skill_text)
        self.assertIn("Priorizar flujos frecuentes", skill_text)
        self.assertIn("<!-- CONTEXT_SOURCE: file_obligations -->", skill_text)

    def test_strict_partition_cross_platform_web(self):
        """Partición estricta en cross_platform_web: archivo vs etapa (Condición 3)."""
        rule_text = (ROOT / ".claude" / "rules" / "cross-platform-web.md").read_text(encoding="utf-8")
        skill_text = (ROOT / ".agent" / "skills" / "cross_platform_web.md").read_text(encoding="utf-8")

        # Obligaciones de archivo DEBEN estar en la regla de ruta
        self.assertIn("Unidades de viewport en móvil", rule_text)
        self.assertIn("dvh", rule_text)
        self.assertIn("Scroll momentum en iOS", rule_text)
        self.assertIn("pointer events", rule_text)
        self.assertIn("font-size: 16px", rule_text)
        self.assertIn("cross-env", rule_text)
        self.assertIn("rimraf", rule_text)
        self.assertIn("Case sensitivity", rule_text)
        self.assertIn("Anti-patrones frecuentes", rule_text)

        # Obligaciones de etapa NO deben estar en la regla de ruta
        self.assertNotIn("Checklist de revisión (activar en `/cierre-modulo`", rule_text)
        self.assertNotIn("Plataformas objetivo:", rule_text)

        # Pero SÍ deben estar en la skill canónica
        self.assertIn("Checklist de revisión (activar en `/cierre-modulo`", skill_text)
        self.assertIn("Plataformas objetivo:", skill_text)
        self.assertIn("<!-- CONTEXT_SOURCE: file_obligations -->", skill_text)

    def test_recorrido_17_stage_obligations_before_file_access(self):
        """Recorrido 17: obligaciones de etapa que deben activarse antes de abrir archivo llegan a tiempo.

        Verifica que decisiones previas a tocar código (perfil visual, invariantes de persistencia,
        checklist de revisión en cierre) se rigen por la etapa (workflows y skills canónicas),
        mientras que las reglas de ruta se activan de forma complementaria al acceder al archivo.
        """
        # 1. En etapa de diseño / preflight: las fuentes canónicas contienen la guía previa
        fe_skill = (ROOT / ".agent" / "skills" / "frontend_design.md").read_text(encoding="utf-8")
        self.assertIn("Antes de diseñar, leer si existe:", fe_skill)
        self.assertIn("_project/12_design_profile.md", fe_skill)

        # 2. Las reglas de ruta NO contienen la lectura previa de diseño (no se activan en startup)
        fe_rule = (ROOT / ".claude" / "rules" / "frontend-design.md").read_text(encoding="utf-8")
        self.assertNotIn("Antes de diseñar, leer si existe:", fe_rule)

        # 3. La verificación de cierre /cierre-modulo exige checklist de revisión de etapa
        cp_skill = (ROOT / ".agent" / "skills" / "cross_platform_web.md").read_text(encoding="utf-8")
        self.assertIn("Checklist de revisión (activar en `/cierre-modulo`", cp_skill)
        cp_rule = (ROOT / ".claude" / "rules" / "cross-platform-web.md").read_text(encoding="utf-8")
        self.assertNotIn("Checklist de revisión (activar en `/cierre-modulo`", cp_rule)

    def test_portability_rule_section_2(self):
        """Regla de portabilidad (§2): ninguna obligación vive únicamente en .claude/rules/.

        Las fuentes canónicas residen en .agent/skills/ y son accesibles para Codex y OpenCode.
        """
        # Fuentes existen en .agent/skills/
        for name, rule in self.claude_rules.items():
            source = ROOT / rule["source"]
            self.assertTrue(source.exists(), f"Fuente canónica no existe: {source}")
            self.assertTrue(source.is_relative_to(ROOT / ".agent"), f"Fuente no reside en .agent/: {source}")

        # OpenCode tiene sus skills sincronizadas desde .agent/skills/
        opencode_fe = ROOT / ".opencode" / "skills" / "frontend-design" / "SKILL.md"
        opencode_cp = ROOT / ".opencode" / "skills" / "cross-platform-web" / "SKILL.md"
        self.assertTrue(opencode_fe.exists())
        self.assertTrue(opencode_cp.exists())

        # OpenCode y Codex NO tienen .claude/rules
        self.assertFalse((ROOT / ".opencode" / "rules").exists())
        self.assertFalse((ROOT / ".agents" / "rules").exists())

    def test_foco_overlap_resolution_orthogonal_coexistence(self):
        """Resolución del solape con /foco (Condición 2 — Salida 1: Coexistencia ortogonal).

        /foco gobierna la lente conversacional y de etapa; .claude/rules/ gobierna los
        invariantes mecánicos de archivo.
        """
        foco_text = (ROOT / ".agent" / "workflows" / "foco.md").read_text(encoding="utf-8")
        self.assertIn("Coexistencia con reglas por ruta", foco_text)
        self.assertIn("Salida 1", foco_text)
        self.assertIn("ortogonal", foco_text)
        self.assertIn("active_focus", foco_text)

    def test_sync_check_idempotency(self):
        """sync_claude_commands.py --check pasa sin drift con las reglas generadas."""
        cmd = [sys.executable, str(ROOT / ".agent" / "scripts" / "sync_claude_commands.py"), "--check"]
        res = subprocess.run(cmd, capture_output=True, text=True)
        self.assertEqual(res.returncode, 0, f"Sync check falló:\n{res.stdout}\n{res.stderr}")
        self.assertIn("Sync check: OK", res.stdout)

    def test_framework_doctor_validates_claude_rules(self):
        """framework_doctor.py ejecuta validate_claude_rules y aprueba el estado actual."""
        errors: list[str] = []
        warnings: list[str] = []
        doctor.validate_claude_rules(self.manifest, errors, warnings)
        self.assertEqual(errors, [])

    def test_doctor_detects_missing_rule(self):
        """framework_doctor.py detecta si falta una regla declarada."""
        fake_manifest = {
            "claude_rules": [
                {
                    "name": "regla-inexistente",
                    "source": ".agent/skills/frontend_design.md",
                    "paths": ["**/*.ts"],
                    "description": "Prueba",
                }
            ]
        }
        errors: list[str] = []
        warnings: list[str] = []
        doctor.validate_claude_rules(fake_manifest, errors, warnings)
        self.assertTrue(any("Regla Claude faltante" in e for e in errors))

    def test_doctor_detects_broken_source(self):
        """framework_doctor.py detecta si una regla apunta a una fuente inexistente."""
        fake_manifest = {
            "claude_rules": [
                {
                    "name": "frontend-design",
                    "source": ".agent/skills/inexistente.md",
                    "paths": ["**/*.ts"],
                    "description": "Prueba",
                }
            ]
        }
        errors: list[str] = []
        warnings: list[str] = []
        doctor.validate_claude_rules(fake_manifest, errors, warnings)
        self.assertTrue(any("apunta a fuente inexistente" in e for e in errors))

    def test_doctor_detects_empty_paths(self):
        """framework_doctor.py detecta si una regla declara paths vacíos."""
        fake_manifest = {
            "claude_rules": [
                {
                    "name": "frontend-design",
                    "source": ".agent/skills/frontend_design.md",
                    "paths": [],
                    "description": "Prueba",
                }
            ]
        }
        errors: list[str] = []
        warnings: list[str] = []
        doctor.validate_claude_rules(fake_manifest, errors, warnings)
        self.assertTrue(any("debe declarar una lista 'paths' no vacía" in e for e in errors))

    def test_context_increase_measurement(self):
        """Medición real del incremento de contexto por reglas por ruta (Condición 1).

        Mide bytes y tokens estimados (aprox. 4 bytes/token) de cada regla generada.
        """
        rules_dir = ROOT / ".claude" / "rules"

        fe_path = rules_dir / "frontend-design.md"
        fe_bytes = fe_path.stat().st_size
        fe_tokens = fe_bytes / 4.0

        cp_path = rules_dir / "cross-platform-web.md"
        cp_bytes = cp_path.stat().st_size
        cp_tokens = cp_bytes / 4.0

        total_bytes = fe_bytes + cp_bytes
        total_tokens = total_bytes / 4.0

        # Verificaciones cuantitativas de tamaño acotado:
        # frontend-design: ~1.2 - 2.0 KB (~300-500 tokens)
        self.assertGreater(fe_bytes, 800)
        self.assertLess(fe_bytes, 3000)

        # cross-platform-web: ~2.5 - 4.5 KB (~600-1100 tokens)
        self.assertGreater(cp_bytes, 1500)
        self.assertLess(cp_bytes, 6000)

        # Total cuando ambos coinciden: ~4 - 7 KB (~1000 - 1800 tokens)
        self.assertLess(total_tokens, 2000)

        # Registrar métricas exactas para el informe de bitácora
        self.fe_metrics = {"bytes": fe_bytes, "tokens": fe_tokens}
        self.cp_metrics = {"bytes": cp_bytes, "tokens": cp_tokens}
        self.total_metrics = {"bytes": total_bytes, "tokens": total_tokens}


if __name__ == "__main__":
    unittest.main()
