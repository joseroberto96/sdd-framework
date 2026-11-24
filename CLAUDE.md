@AGENTS.md

<!-- BEGIN GESTIONADO: framework sha256=8a3fe86985d9d0d9 — propagate_framework.py; no editar a mano -->
## Claude Code — Especificidades

- Adaptador de entorno: `.agent/adapters/claude-code.md`.
- Comandos y subagentes nativos viven en `.claude/commands/` y `.claude/agents/` (generados por `sync_claude_commands.py`).
- Configuración de workspace en `.claude/settings.json`.
- Respetar `Guardrails v4.2` gobernando calidad (THINK → APPROVE → CODE+LOG). Precedencia: `GUARDRAILS_MASTER.md` y `.agent/rules/architecture_constraints.md`.
- Al compactar, conservar siempre: módulo o lote en curso, ficheros modificados, comando de verificación y último sha.
- Conversación no lineal: ante interrupción, compactación o inconsistencia con el scope, ejecutar `/reconciliar`.
- Feedback del framework: incidencias o fricciones del framework se registran como `FW-NNN` según `framework_mode` (`template` → `.agent/docs/field_report.md`; `initialized` → `_feedback/field_report.md` con `PREFIJO-FW-NNN`, el `fw_prefix` del perfil).
- Los 9 comandos del builder (`/nuevo-proyecto`, `/migrar-proyecto`, `/arrancar`, `/diseñar`, `/ciclo`, `/nuevo-bug`, `/reconciliar`, `/gycd`, `/actualizar-framework`) son inyectados como skills por el harness en cada sesión. Maquinaria interna invocada por el agente según los gates del árbol de decisión canónico (`.agent/rules/runtime_core.md`):

<!-- BEGIN GENERADO: maquinaria-interna — sync_claude_commands.py; no editar a mano -->
`/entrevistar` · `/tdd` · `/cierre-modulo` · `/scope-siguiente` · `/enriquecer-ticket` · `verificar-ejecutable` · `preflight-review` · `/aprender-de-error` · `/retrospectiva-framework` · `/validate-arch` · `/cambio-seguro` · `/debug` · `/clarificar` · `/nueva-fase` · `/publicar-github` · `/refactorizar-arquitectura` · `/skills` · `/añadir-experto` · `/actualizar-invariante` · `descubrir-producto` · `prd-mvp` · `tech-design-mvp` · `instalar-skills` · `sugerir-skills` · `lean-context` · `crear-sesion-artefactos` · `/foco` · `mantenimiento`
<!-- FIN GENERADO: maquinaria-interna -->
<!-- FIN GESTIONADO: framework -->
