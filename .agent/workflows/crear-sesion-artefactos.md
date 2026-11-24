---
description: Sub-workflow compartido — crea o actualiza los artefactos auxiliares de sesión y referencias _refs. Invocado por /nuevo-proyecto y /migrar-proyecto.
---

# Crear Artefactos de Sesión

Sub-workflow invocado al final de `/nuevo-proyecto` y `/migrar-proyecto`.
No se llama directamente; es un paso interno de inicialización.

## Precondiciones

Se asume que antes de llamar este sub-workflow ya existen:
- `_project/03_project_state.json` con datos reales (project, framework_mode, current_phase)
- `_project/01_product_requirements.md`
- `_project/20_builder_profile.md`
- `_project/21_knowledge_map.md`

## Pasos

1. CREAR ARTEFACTOS DE SESIÓN SI NO EXISTEN
   Crear (o mantener si ya existe) cada archivo:

   > **Proyecciones de solo lectura — NO crear a mano.**
   > `core.md`, `06_bug_fixes.md`, `07_pending_clarifications.md`, `03_arch_decisions.md` y
   > `00_session_brief.json` son proyecciones generadas por
   > `python3 .agent/scripts/state_manager.py bootstrap` (inicialización) o
   > `python3 .agent/scripts/state_manager.py render` (regeneración) a partir de
   > `03_project_state.json`. No editarlas a mano.

   - `_project/13_session_journal.md` — ledger temporal:
     ```markdown
     # Session Journal — [PROJECT_NAME]

     > Registro temporal de cambios del proyecto inicializado. No usar para historial del framework base.

     _Sin cambios pendientes._
     ```

   - `_project/14_skill_provenance.md` — tabla vacía de procedencia:
     ```markdown
     # Skill Provenance — [PROJECT_NAME]
     | Skill | Fuente | Versión/commit | Instalado | Scope | Estado |
     |-------|--------|----------------|-----------|-------|--------|
     ```

   - `_project/15_environment_profile.md` — capacidades del entorno declarado:
     ```markdown
     # Environment Profile — [PROJECT_NAME]
     **Entorno:** [entorno declarado]
     **Slash commands:** [sí/no]
     **Subagentes:** [sí/no]
     **Compactación nativa:** [sí/no]
     **Notas:** Completar al confirmar capacidades reales.
     **Soporte estructural Codex (`structural_support`):** [pass | fail | unavailable]
     **Smoke runtime Codex (`runtime_smoke`):** [pass | fail | unavailable]
     **Superficies Codex requeridas:** [cli | cli,desktop]
     **Smoke runtime Codex CLI:** [pass | fail | unavailable]
     **Smoke runtime Codex Desktop/app-server:** [pass | fail | unavailable]
     **Versión de Codex:** [versión o no disponible]
     **Versión de Codex CLI:** [versión o no disponible]
     **Versión de Codex Desktop/app-server:** [versión o no disponible]
     **Modelo Codex smoke:** [modelo explícito o no declarado]
     **Esfuerzo Codex smoke:** [none | minimal | low | medium | high | xhigh | max | ultra]
     **Hooks Codex:** [gestionados | manuales | no disponibles]
     **MCP local `state_manager` (Codex `.codex/config.toml` nativo; `.mcp.json` espejo opcional):** [conectado | fallback | no disponible]
     **Diferencias Codex conocidas:** [lista o "ninguna"]
     ```
     Si el harness principal es Codex, reemplazar `[entorno declarado]` por
     `Codex` y completar estos campos con `codex_runtime_smoke.py`, pasando
     `--surface`, `--model` y `--reasoning-effort` explícitos. Ejecutar CLI y
     Desktop/app-server como gates independientes; no escribir
     `runtime_smoke=pass` sin una ejecución real del host. La revisión de
     confianza y `/hooks` Desktop sigue siendo checklist manual. Para Claude
     Code u otros entornos, conservar sus datos actuales y dejar estos campos
     como no aplicables si no se evalúa Codex.

   - `_project/17_skill_recommendations.md` — tabla vacía de recomendaciones:
     ```markdown
     # Skill Recommendations — [PROJECT_NAME]
     | Señal | Skill sugerida | Prioridad | Estado |
     |-------|----------------|-----------|--------|
     ```

   - `_project/19_core_skills.md` — catálogo de skills core:
     ```markdown
     # Core Skills — [PROJECT_NAME]
     | Skill | Archivo | Activación |
     |-------|---------|------------|
     | Agile/Product | .agent/skills/agile_product.md | Siempre |
     | Architecture | .agent/skills/architecture_core.md | Siempre |
     | Security | .agent/skills/security_core.md | Siempre |
     | Quality/Testing | .agent/skills/quality_testing_core.md | Implementación tier_1/tier_2 |
     | Delivery | .agent/skills/delivery_core.md | Cierre de módulo/fase |
     | Builder Language | .agent/skills/builder_language_core.md | Explicar planes, riesgos y decisiones al builder |
     | Decision Translation | .agent/skills/decision_translation_core.md | Decisiones que se notan en uso/coste/tiempo |
     ```

2. CREAR llms.txt SI NO EXISTE
   Contenido mínimo:
   ```
   # [PROJECT_NAME] — Navegación para agentes AI

   Framework: el de `.agent/workflows/manifest.json` (`framework_version`) + Guardrails v4.2
   Guardrails: GUARDRAILS_MASTER.md
   Constitución: CLAUDE.md
   Sesión (arranque): _project/core.md
   Estado completo: _project/03_project_state.json
   Workflows: .agent/workflows/manifest.json
   Scope activo: _project/04_active_scope.md
   Protocolo extendido: .agent/docs/extended_protocol.md
   ```

3. ASEGURAR _refs EN 03_project_state.json
   Leer `_project/03_project_state.json` y asegurar que `_refs` contiene:
   ```json
   {
     "core": "_project/core.md",
     "session_brief": "_project/00_session_brief.json",
     "workflow_manifest": ".agent/workflows/manifest.json",
     "project_state_schema": ".agent/schemas/project_state.schema.json",
     "product_requirements": "_project/01_product_requirements.md",
     "scope": "_project/04_active_scope.md",
     "arch_decisions": "_project/03_arch_decisions.md",
     "contracts": "_project/03_contracts.md",
     "data_model": "_project/02_technical_plan/data_model.md",
     "api_contract": "_project/02_technical_plan/api_contract.yaml",
     "bugs": "_project/06_bug_fixes.md",
     "acceptance": "_project/08_module_acceptance.md",
     "domain_profile": "_project/09_domain_profile.md",
     "arch_constraints": "_project/10_arch_constraints.md",
     "file_dependencies": "_project/11_file_dependencies.md",
     "design_profile": "_project/12_design_profile.md",
     "session_journal": "_project/13_session_journal.md",
     "skill_provenance": "_project/14_skill_provenance.md",
     "environment_profile": "_project/15_environment_profile.md",
     "release_profile": "_project/16_release_profile.md",
     "skill_recommendations": "_project/17_skill_recommendations.md",
     "core_skills": "_project/19_core_skills.md",
     "builder_profile": "_project/20_builder_profile.md",
     "knowledge_map": "_project/21_knowledge_map.md",
     "llms_map": "llms.txt"
   }
   ```
   Solo añadir las entradas faltantes. No sobreescribir las que ya existen.

4. CREAR 16_release_profile.md SI FUE APROBADO GITHUB
   Si el contexto indica que se aprobó publicación en GitHub, crear el archivo con:
   ```markdown
   # Release Profile — [PROJECT_NAME]
   **Repo:** [nombre o pendiente]
   **Visibilidad:** [privado/público]
   **Rama principal:** main
   **Fase 0:** pendiente — ejecutar /publicar-github
   ## Historial de publicaciones
   _Sin publicaciones aún._
   ## Procedimiento de rollback
   _Pendiente de documentar antes del primer push._
   ```

La interfaz, los logs y los textos para el usuario van en español.

Siguiente: ninguno — sub-workflow interno; el flujo continúa en el workflow invocador (`/nuevo-proyecto` o `/migrar-proyecto`).
