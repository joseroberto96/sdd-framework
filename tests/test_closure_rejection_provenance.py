#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Procedencia de closure_rejections (FW-175, retroportado de Boton).

La Regla 7 (≥2 rechazos → módulo "Estabilización") se apoya en
`closure_rejections`. Antes, el cierre los BORRABA: la evidencia del criterio de
muerte desaparecía justo al cerrar, y un `resolved_at` escrito a mano en el
estado bastaba para desactivar el gate. Ahora se archivan con procedencia y una
resolución solo cuenta si está respaldada por un evento real de la bitácora
encadenada.
"""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / ".agent" / "scripts"))
import state_core
import validate_closure


def _state(rejections):
    return {
        "modules": {"completed": [], "in_progress": ["M1"], "pending": []},
        "module_statuses": {"M1": "in_progress"},
        "closure_rejections": rejections,
    }


_REFS = {"acceptance": "_project/08_module_acceptance.md", "api_contract": "_project/02_technical_plan/api_contract.yaml", "arch_constraints": "_project/10_arch_constraints.md", "arch_decisions": "_project/03_arch_decisions.md", "bugs": "_project/06_bug_fixes.md", "builder_profile": "_project/20_builder_profile.md", "contracts": "_project/03_contracts.md", "core": "_project/core.md", "core_skills": "_project/19_core_skills.md", "data_model": "_project/02_technical_plan/data_model.md", "design_profile": "_project/12_design_profile.md", "domain_profile": "_project/09_domain_profile.md", "environment_profile": "_project/15_environment_profile.md", "file_dependencies": "_project/11_file_dependencies.md", "knowledge_map": "_project/21_knowledge_map.md", "llms_map": "llms.txt", "product_requirements": "_project/01_product_requirements.md", "project_state_schema": ".agent/schemas/project_state.schema.json", "release_profile": "_project/16_release_profile.md", "scope": "_project/04_active_scope.md", "session_brief": "_project/00_session_brief.json", "session_journal": "_project/13_session_journal.md", "skill_provenance": "_project/14_skill_provenance.md", "skill_recommendations": "_project/17_skill_recommendations.md", "workflow_manifest": ".agent/workflows/manifest.json"}


def _rejection(**extra):
    base = {"module_id": "M1", "reason": "ac_incomplete", "timestamp": "2026-08-29T00:00:00Z"}
    base.update(extra)
    return base


class DeathCriterionProvenanceTests(unittest.TestCase):
    def test_dos_rechazos_activos_disparan_el_criterio_de_muerte(self):
        problems = validate_closure.death_problems(_state([_rejection(), _rejection()]), "M1")

        self.assertTrue(any("death criterion" in p for p in problems), problems)

    def test_un_resolved_at_fabricado_no_desactiva_el_gate(self):
        """El vector que esto cierra: escribir `resolved_at` directamente en el
        estado, sin evento que lo respalde, apagaba el criterio de muerte."""
        fabricados = [
            _rejection(resolved_at="2026-08-29T01:00:00Z", id="CRR-0001"),
            _rejection(resolved_at="2026-08-29T01:00:00Z", id="CRR-0002"),
        ]

        problems = validate_closure.death_problems(_state(fabricados), "M1")

        self.assertTrue(
            any("death criterion" in p for p in problems),
            "una resolución sin respaldo en la bitácora debe seguir contando",
        )

    def test_un_rechazo_restaurado_vuelve_a_contar(self):
        restaurados = [
            _rejection(resolved_at="2026-08-29T01:00:00Z", restored_at="2026-08-29T02:00:00Z"),
            _rejection(resolved_at="2026-08-29T01:00:00Z", restored_at="2026-08-29T02:00:00Z"),
        ]

        problems = validate_closure.death_problems(_state(restaurados), "M1")

        self.assertTrue(any("death criterion" in p for p in problems), problems)


class ArchiveIntegrityTests(unittest.TestCase):
    def test_las_tres_acciones_estan_registradas(self):
        for action in (
            "resolve_closure_rejections",
            "restore_closure_rejections",
            "backfill_closure_rejection_provenance",
        ):
            self.assertIn(action, state_core.BUILTIN_ACTIONS)

    def test_los_ids_de_archivo_no_colisionan_con_los_activos(self):
        state = {
            "closure_rejections": [{"id": "CRR-0001"}],
            "closure_rejection_archive": [{"id": "CRR-0002"}],
        }

        self.assertEqual("CRR-0003", state_core.StateManager._next_closure_rejection_id(state))

    def test_el_archivo_exige_resolucion_y_evidencia_auditables(self):
        manager = state_core.StateManager.__new__(state_core.StateManager)
        incompleto = {"id": "CRR-0001", "module_id": "M1", "reason": "ac_incomplete",
                      "timestamp": "t", "resolved_at": "t"}  # sin resolution/evidence/archived_at
        data = {
            "project": "x", "framework_mode": "initialized", "framework_version": "4.24.5",
            "current_phase": "phase_01", "current_step": "", "last_completed_step": "",
            "stack": {"backend": "-", "frontend": "-", "database": "-", "auth": "-"},
            "dependencies": [], "architecture_constraints": [],
            "contracts": {"public_interfaces": {}},
            "modules": {"completed": [], "in_progress": [], "pending": []},
            "module_statuses": {}, "work_tiers": {}, "module_specs": {},
            "closure_rejections": [], "active_mcps": [], "_refs": _REFS,
            "closure_rejection_archive": [incompleto],
        }

        with self.assertRaises(ValueError) as ctx:
            manager.validate_state(data)

        self.assertIn("closure_rejection_archive", str(ctx.exception))

    def test_ids_duplicados_entre_activos_y_archivo_se_rechazan(self):
        manager = state_core.StateManager.__new__(state_core.StateManager)
        data = {
            "project": "x", "framework_mode": "initialized", "framework_version": "4.24.5",
            "current_phase": "phase_01", "current_step": "", "last_completed_step": "",
            "stack": {"backend": "-", "frontend": "-", "database": "-", "auth": "-"},
            "dependencies": [], "architecture_constraints": [],
            "contracts": {"public_interfaces": {}},
            "modules": {"completed": [], "in_progress": [], "pending": []},
            "module_statuses": {}, "work_tiers": {}, "module_specs": {},
            "closure_rejections": [_rejection(id="CRR-0001")], "active_mcps": [], "_refs": _REFS,
            "closure_rejection_archive": [{
                "id": "CRR-0001", "module_id": "M1", "reason": "ac_incomplete",
                "timestamp": "t", "resolved_at": "t", "resolution": "r",
                "evidence": "e", "archived_at": "t",
            }],
        }

        with self.assertRaises(ValueError) as ctx:
            manager.validate_state(data)

        self.assertIn("duplicados", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
