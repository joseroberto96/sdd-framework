#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import unittest, tempfile, sys, json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / ".agent" / "scripts"))
import state_core
import projections

BUG_MD = """# Bug Fixes

### BUG-001 — Login rompe
- **Módulo:** auth
- **Severidad:** Crítico
- **affects_ac:** true
- **Estado:** Abierto
"""


class TestImporter(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        (root / "_project").mkdir(parents=True)
        self._orig = {k: getattr(state_core, k) for k in
                      ["WORKSPACE_ROOT", "STATE_FILE_PATH", "CORE_FILE_PATH", "BUGS_FILE_PATH",
                       "CLARIFICATIONS_FILE_PATH", "DECISIONS_FILE_PATH", "SESSION_BRIEF_PATH"]}
        state_core.WORKSPACE_ROOT = root
        state_core.STATE_FILE_PATH = root / "_project" / "03_project_state.json"
        state_core.CORE_FILE_PATH = root / "_project" / "core.md"
        state_core.BUGS_FILE_PATH = root / "_project" / "06_bug_fixes.md"
        state_core.CLARIFICATIONS_FILE_PATH = root / "_project" / "07_pending_clarifications.md"
        state_core.DECISIONS_FILE_PATH = root / "_project" / "03_arch_decisions.md"
        state_core.SESSION_BRIEF_PATH = root / "_project" / "00_session_brief.json"

    def tearDown(self):
        for k, v in self._orig.items():
            setattr(state_core, k, v)
        self.tmp.cleanup()

    def test_import_bugs_from_markdown(self):
        state_core.BUGS_FILE_PATH.write_text(BUG_MD, encoding="utf-8")
        state = {"_bugs": [], "_clarifications": [], "_decisions": []}
        count = projections.import_markdown(state)
        self.assertGreaterEqual(count, 1)
        bug = state["_bugs"][0]
        self.assertEqual(bug["id"], "BUG-001")
        self.assertEqual(bug["module_id"], "auth")
        self.assertEqual(bug["severity"], "Crítico")
        self.assertTrue(bug["affects_ac"])
        self.assertEqual(bug["status"], "Abierto")

    def test_import_is_idempotent(self):
        state_core.BUGS_FILE_PATH.write_text(BUG_MD, encoding="utf-8")
        state = {"_bugs": [], "_clarifications": [], "_decisions": []}
        projections.import_markdown(state)
        projections.import_markdown(state)  # second run must not duplicate
        self.assertEqual(len([b for b in state["_bugs"] if b["id"] == "BUG-001"]), 1)

    def test_import_clar_resolved_status_with_markdown_decoration(self):
        # FW-032: status decorado ("**Resuelta** (fecha) — ...") debe importarse
        # como "Resuelta", no caer al default "Abierta".
        clar_md = (
            "# Clarificaciones\n\n"
            "### CLAR-002 — Backup diario\n"
            "- **blocked_scope:** M0_orquestacion\n"
            "- **blocks_start:** false\n"
            "- **blocks_implementation:** false\n"
            "- **blocks_closure:** false\n"
            "- **Pregunta:** cadencia del backup\n"
            "- **Estado:** **Resuelta** (2026-06-07) — ver **ADR-011**\n"
        )
        state_core.CLARIFICATIONS_FILE_PATH.write_text(clar_md, encoding="utf-8")
        state = {"_bugs": [], "_clarifications": [], "_decisions": []}
        projections.import_markdown(state)
        clar = state["_clarifications"][0]
        self.assertEqual(clar["id"], "CLAR-002")
        self.assertEqual(clar["status"], "Resuelta")

    def test_import_adr_status_strips_trailing_text(self):
        # FW-032: status de ADR "Aceptada (resuelve ...)" → solo "Aceptada".
        adr_md = (
            "# ADRs\n\n"
            "### ADR-011 — Estrategia de backup\n"
            "- **Fecha:** 2026-06-07\n"
            "- **Estado:** Aceptada (resuelve PRD-CLAR-002; decisión del constructor)\n"
        )
        state_core.DECISIONS_FILE_PATH.write_text(adr_md, encoding="utf-8")
        state = {"_bugs": [], "_clarifications": [], "_decisions": []}
        projections.import_markdown(state)
        adr = state["_decisions"][0]
        self.assertEqual(adr["id"], "ADR-011")
        self.assertEqual(adr["status"], "Aceptada")

    def test_clar_boolean_with_trailing_text_stays_true(self):
        # FW-035 (el más grave): "true para M1 (...)" degradaba a False vía
        # `_parse_field(...).lower() == "true"`, desarmando el gate de cierre.
        clar_md = (
            "# Clarificaciones\n\n"
            "### CLAR-004 — Catálogo de secciones\n"
            "- **blocked_scope:** M1_onboarding\n"
            "- **blocks_start:** false\n"
            "- **blocks_implementation:** false\n"
            "- **blocks_closure:** true para M1_onboarding (faltan las 9 secciones)\n"
            "- **Pregunta:** ¿catálogo?\n"
            "- **Estado:** Abierta\n"
        )
        state_core.CLARIFICATIONS_FILE_PATH.write_text(clar_md, encoding="utf-8")
        state = {"_bugs": [], "_clarifications": [], "_decisions": []}
        projections.import_markdown(state)
        clar = state["_clarifications"][0]
        self.assertTrue(clar["blocks_closure"], "boolean con matiz textual debe ser True")
        self.assertFalse(clar["blocks_start"])

    def test_bug_affects_ac_with_trailing_text_stays_true(self):
        bug_md = (
            "# Bugs\n\n"
            "### BUG-009 — Rompe AC\n"
            "- **Módulo:** core\n"
            "- **Severidad:** Mayor\n"
            "- **affects_ac:** true (rompe el AC-3)\n"
            "- **Estado:** Abierto\n"
        )
        state_core.BUGS_FILE_PATH.write_text(bug_md, encoding="utf-8")
        state = {"_bugs": [], "_clarifications": [], "_decisions": []}
        projections.import_markdown(state)
        self.assertTrue(state["_bugs"][0]["affects_ac"])

    def test_adr_roundtrip_preserves_body(self):
        # FW-035: render() escribe ADRs sin guion y con cuerpos multilínea;
        # import_markdown debe recuperarlos en vez de poner "[importado]".
        state = {
            "project": "P", "current_phase": "phase_01", "modules": {"in_progress": []},
            "module_statuses": {}, "work_tiers": {},
            "_bugs": [], "_clarifications": [],
            "_decisions": [{
                "id": "ADR-001", "title": "Usar X",
                "date": "2026-06-07", "status": "Aceptada", "module_id": "M1_core",
                "context": "El sistema necesita Y.\nSegunda línea de contexto.",
                "alternatives": "1. Opción A\n2. Opción B",
                "decision": "Elegimos X por Z.",
                "consequences": "- Mejora P\n- Empeora Q",
            }],
        }
        projections.render(state)
        fresh = {"_bugs": [], "_clarifications": [], "_decisions": []}
        projections.import_markdown(fresh)
        adr = fresh["_decisions"][0]
        self.assertEqual(adr["date"], "2026-06-07")
        self.assertEqual(adr["module_id"], "M1_core")
        self.assertEqual(adr["status"], "Aceptada")
        self.assertNotEqual(adr["context"], "[importado]")
        self.assertIn("necesita Y", adr["context"])
        self.assertIn("Segunda línea", adr["context"])
        self.assertIn("Opción B", adr["alternatives"])
        self.assertIn("Elegimos X", adr["decision"])
        self.assertIn("Empeora Q", adr["consequences"])
        # El separador horizontal del render no debe filtrarse al contenido.
        self.assertNotIn("---", adr["consequences"])

    def test_render_after_import_succeeds(self):
        state_core.BUGS_FILE_PATH.write_text(BUG_MD, encoding="utf-8")
        state = {"project": "P", "current_phase": "phase_01", "modules": {"in_progress": []},
                 "module_statuses": {}, "work_tiers": {}, "_bugs": [], "_clarifications": [], "_decisions": []}
        projections.import_markdown(state)
        projections.render(state)  # bugs file now regenerated with banner -> no guard error
        text = state_core.BUGS_FILE_PATH.read_text(encoding="utf-8")
        self.assertIn(projections.BANNER, text)
        self.assertIn("BUG-001", text)


if __name__ == "__main__":
    unittest.main()


class TestImportBannerSafety(TestImporter):
    """FW-093 (Example Tool): el banner de autogenerado solo se estampa si el archivo
    aportó entradas en formato reconocido. Un archivo manual en formato legado
    (## BUG-NN, sin ### BUG-NNN) no debe quedar marcado como autogenerado —
    un render() posterior lo sobrescribiría desde el estado y destruiría la
    historia manual."""

    LEGACY_MD = (
        "# Historial de bugs\n\n"
        "## BUG-66 — Etiquetas inutilizables tras rotación\n"
        "**Severidad:** Alta\n**Fix:** helper de normalización\n"
    )

    def test_unrecognized_manual_file_is_not_stamped(self):
        state_core.BUGS_FILE_PATH.write_text(self.LEGACY_MD, encoding="utf-8")
        state = {"_bugs": [], "_clarifications": [], "_decisions": []}
        count = projections.import_markdown(state)
        self.assertEqual(count, 0)
        text = state_core.BUGS_FILE_PATH.read_text(encoding="utf-8")
        self.assertNotIn(projections.BANNER, text,
                         "un archivo manual no reconocido quedó estampado como autogenerado")
        self.assertIn("BUG-66", text, "el contenido manual debe quedar intacto")

    def test_recognized_file_is_stamped(self):
        state_core.BUGS_FILE_PATH.write_text(BUG_MD, encoding="utf-8")
        state = {"_bugs": [], "_clarifications": [], "_decisions": []}
        projections.import_markdown(state)
        self.assertIn(projections.BANNER,
                      state_core.BUGS_FILE_PATH.read_text(encoding="utf-8"))

    def test_reimport_of_already_imported_file_still_stamps(self):
        # 0 entradas NUEVAS pero formato reconocido → estampar sigue siendo seguro.
        state_core.BUGS_FILE_PATH.write_text(BUG_MD, encoding="utf-8")
        state = {"_bugs": [{"id": "BUG-001"}], "_clarifications": [], "_decisions": []}
        count = projections.import_markdown(state)
        self.assertEqual(count, 0)
        self.assertIn(projections.BANNER,
                      state_core.BUGS_FILE_PATH.read_text(encoding="utf-8"))

    def test_empty_file_is_stamped(self):
        state_core.BUGS_FILE_PATH.write_text("\n", encoding="utf-8")
        state = {"_bugs": [], "_clarifications": [], "_decisions": []}
        projections.import_markdown(state)
        self.assertIn(projections.BANNER,
                      state_core.BUGS_FILE_PATH.read_text(encoding="utf-8"))

    def test_collect_markdown_entities(self):
        state_core.BUGS_FILE_PATH.write_text(BUG_MD, encoding="utf-8")
        entities = projections.collect_markdown_entities()
        self.assertEqual(len(entities["bugs"]), 1)
        self.assertEqual(entities["bugs"][0]["id"], "BUG-001")
        self.assertEqual(entities["clarifications"], [])
        self.assertEqual(entities["decisions"], [])

    def test_import_markdown_entities_mutation_and_audit_backing(self):
        import replay_state_log
        manifest_dir = state_core.WORKSPACE_ROOT / ".agent" / "workflows"
        manifest_dir.mkdir(parents=True, exist_ok=True)
        (manifest_dir / "manifest.json").write_text(
            json.dumps({"commands": []}), encoding="utf-8"
        )
        base_state = {
            "project": "TestProject",
            "framework_mode": "template",
            "framework_version": "3.2",
            "current_phase": "phase_01",
            "current_step": "init",
            "last_completed_step": "Ninguno",
            "stack": {"backend": "Python", "frontend": "JS", "database": "SQLite", "auth": "None"},
            "dependencies": [],
            "architecture_constraints": [],
            "contracts": {"public_interfaces": {}},
            "modules": {"completed": ["auth"], "in_progress": [], "pending": []},
            "module_statuses": {"auth": "completed"},
            "work_tiers": {},
            "module_specs": {},
            "closure_rejections": [],
            "active_mcps": [],
            "_refs": {},
            "_bugs": [],
            "_clarifications": [],
            "_decisions": [],
        }
        state_core.STATE_FILE_PATH.write_text(json.dumps(base_state), encoding="utf-8")
        log_path = state_core.STATE_FILE_PATH.parent / "state_log.jsonl"
        log_path.write_text("", encoding="utf-8")

        state_core.BUGS_FILE_PATH.write_text(BUG_MD, encoding="utf-8")
        clar_md = """# Clarificaciones

### CLAR-001 — Pregunta de auth
- **blocked_scope:** auth
- **blocks_start:** true
- **Estado:** Abierta
- **Pregunta:** ¿Usar JWT?
"""
        state_core.CLARIFICATIONS_FILE_PATH.write_text(clar_md, encoding="utf-8")
        dec_md = """# Decisiones

### ADR-001 — Usar JWT
**Fecha:** 2026-09-12
**Estado:** Aceptada
**Módulo afectado:** auth

**Contexto:**
Sesiones stateless.

**Decisión:**
Se decide usar JWT.
"""
        state_core.DECISIONS_FILE_PATH.write_text(dec_md, encoding="utf-8")

        manager = state_core.StateManager()
        payload = projections.collect_markdown_entities(manager.state_data)
        self.assertEqual(len(payload["bugs"]), 1)
        self.assertEqual(len(payload["clarifications"]), 1)
        self.assertEqual(len(payload["decisions"]), 1)

        msg = manager.mutate("import_markdown_entities", payload)
        self.assertIn("Importadas 3 entidades", msg)

        # 1. Verificar que se incorporaron en state_data
        self.assertEqual(len(manager.state_data["_bugs"]), 1)
        self.assertEqual(manager.state_data["_bugs"][0]["id"], "BUG-001")
        self.assertEqual(len(manager.state_data["_clarifications"]), 1)
        self.assertEqual(manager.state_data["_clarifications"][0]["id"], "CLAR-001")
        self.assertEqual(len(manager.state_data["_decisions"]), 1)
        self.assertEqual(manager.state_data["_decisions"][0]["id"], "ADR-001")

        # 2. Verificar que NO mutó module_statuses (auth sigue completed)
        self.assertEqual(manager.state_data["module_statuses"]["auth"], "completed")

        # 3. Verificar que se registró en state_log.jsonl
        log_lines = log_path.read_text(encoding="utf-8").strip().splitlines()
        self.assertGreaterEqual(len(log_lines), 1)
        last_event = json.loads(log_lines[-1])
        self.assertEqual(last_event["action"], "import_markdown_entities")

        # 4. Verificar que audit_state_backing reconoce las entidades como respaldadas
        audit = replay_state_log.audit_state_backing(manager.state_data, log_path)
        self.assertEqual(audit["_bugs"], [], "Bugs importados deben tener respaldo en el log")
        self.assertEqual(audit["_clarifications"], [], "Clarificaciones importadas deben tener respaldo")
        self.assertEqual(audit["_decisions"], [], "Decisiones importadas deben tener respaldo")

        # 5. Verificar que replay_state_log reconstruye las entidades importadas
        replayed_path = state_core.WORKSPACE_ROOT / "replayed.json"
        res = replay_state_log.replay(log_path, replayed_path)
        self.assertEqual(res["errors"], [])
        self.assertGreaterEqual(res["applied"], 1)
        replayed_state = res["state"]
        self.assertEqual(len(replayed_state["_bugs"]), 1)
        self.assertEqual(replayed_state["_bugs"][0]["id"], "BUG-001")
        self.assertEqual(len(replayed_state["_clarifications"]), 1)
        self.assertEqual(replayed_state["_clarifications"][0]["id"], "CLAR-001")
        self.assertEqual(len(replayed_state["_decisions"]), 1)
        self.assertEqual(replayed_state["_decisions"][0]["id"], "ADR-001")

    def test_collect_markdown_entities_with_ids_greater_than_999(self):
        # BUG-03: IDs >= 1000 deben ser reconocidos por collect_markdown_entities
        bug_md = """# Bug Fixes

### BUG-1000 — Error en checkout a gran escala
- **Módulo:** checkout
- **Severidad:** Crítico
- **affects_ac:** true
- **Estado:** Abierto
"""
        clar_md = """# Clarificaciones

### CLAR-1042 — Clarificación de escalabilidad
- **blocked_scope:** checkout
- **blocks_start:** true
- **Estado:** Abierta
- **Pregunta:** ¿Soportar 10k RPS?
"""
        dec_md = """# Decisiones

### ADR-2050 — Usar particionado horizontal
**Fecha:** 2026-09-12
**Estado:** Aceptada
**Módulo afectado:** checkout

**Contexto:**
Alto tráfico.

**Decisión:**
Particionar horizontalmente.
"""
        state_core.BUGS_FILE_PATH.write_text(bug_md, encoding="utf-8")
        state_core.CLARIFICATIONS_FILE_PATH.write_text(clar_md, encoding="utf-8")
        state_core.DECISIONS_FILE_PATH.write_text(dec_md, encoding="utf-8")

        entities = projections.collect_markdown_entities()
        self.assertEqual(len(entities["bugs"]), 1)
        self.assertEqual(entities["bugs"][0]["id"], "BUG-1000")
        self.assertEqual(len(entities["clarifications"]), 1)
        self.assertEqual(entities["clarifications"][0]["id"], "CLAR-1042")
        self.assertEqual(len(entities["decisions"]), 1)
        self.assertEqual(entities["decisions"][0]["id"], "ADR-2050")

    def test_correlatives_with_malformed_ids(self):
        # BUG-09: IDs anómalos o malformados en _bugs, _clarifications o _decisions
        # se descartan con (IndexError, ValueError) sin tragar BaseException
        manifest_dir = state_core.WORKSPACE_ROOT / ".agent" / "workflows"
        manifest_dir.mkdir(parents=True, exist_ok=True)
        (manifest_dir / "manifest.json").write_text(
            json.dumps({"commands": []}), encoding="utf-8"
        )
        base_state = {
            "project": "TestProject",
            "framework_mode": "template",
            "framework_version": "3.2",
            "current_phase": "phase_01",
            "current_step": "init",
            "last_completed_step": "Ninguno",
            "stack": {"backend": "Python", "frontend": "JS", "database": "SQLite", "auth": "None"},
            "dependencies": [],
            "architecture_constraints": [],
            "contracts": {"public_interfaces": {}},
            "modules": {"completed": [], "in_progress": ["core"], "pending": []},
            "module_statuses": {"core": "in_progress"},
            "work_tiers": {},
            "module_specs": {},
            "closure_rejections": [],
            "active_mcps": [],
            "_refs": {},
            "_bugs": [
                {"id": "BUG-xyz", "module_id": "core", "severity": "Menor", "description": "d1", "status": "Abierto", "affects_ac": False, "fix": "", "affected_files": []},
                {"id": "BUG-", "module_id": "core", "severity": "Menor", "description": "d2", "status": "Abierto", "affects_ac": False, "fix": "", "affected_files": []},
                {"id": "BUG-005", "module_id": "core", "severity": "Menor", "description": "d3", "status": "Abierto", "affects_ac": False, "fix": "", "affected_files": []},
            ],
            "_clarifications": [
                {"id": "CLAR-bad", "title": "t1", "blocked_scope": "core", "question": "q1", "status": "Abierta", "blocks_start": False, "blocks_implementation": False, "blocks_closure": False, "blocking_condition": "", "answer": "", "options": "", "impact": ""},
                {"id": "CLAR-010", "title": "t2", "blocked_scope": "core", "question": "q2", "status": "Abierta", "blocks_start": False, "blocks_implementation": False, "blocks_closure": False, "blocking_condition": "", "answer": "", "options": "", "impact": ""},
            ],
            "_decisions": [
                {"id": "ADR-corrupt", "title": "t1", "date": "2026-09-12", "status": "Aceptada", "module_id": "core", "context": "c1", "decision": "dec1", "alternatives": "", "consequences": ""},
                {"id": "ADR-020", "title": "t2", "date": "2026-09-12", "status": "Aceptada", "module_id": "core", "context": "c2", "decision": "dec2", "alternatives": "", "consequences": ""},
            ],
        }
        state_core.STATE_FILE_PATH.write_text(json.dumps(base_state), encoding="utf-8")
        manager = state_core.StateManager()

        # Al registrar un bug nuevo, calcula max_num = 5 y genera BUG-006
        manager.mutate("register_bug", {
            "module_id": "core", "severity": "Menor", "description": "Bug menor test"
        })
        self.assertEqual(manager.state_data["_bugs"][-1]["id"], "BUG-006")

        # Al registrar clarificación nueva, calcula max_num = 10 y genera CLAR-011
        manager.mutate("register_clarification", {
            "blocked_scope": "core", "question": "¿Pregunta?"
        })
        self.assertEqual(manager.state_data["_clarifications"][-1]["id"], "CLAR-011")

        # Al registrar ADR nueva, calcula max_num = 20 y genera ADR-021
        manager.mutate("register_decision", {
            "title": "Decisión test", "context": "Ctx", "decision": "Dec"
        })
        self.assertEqual(manager.state_data["_decisions"][-1]["id"], "ADR-021")
