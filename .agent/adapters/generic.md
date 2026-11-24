# Adaptador — Entorno Genérico

> **Soporte:** experimental — fallback portable para entornos sin integración específica; sin validación automática del doctor.
> **Degradación:** ver `.agent/adapters/_capability_matrix.md`. Declarar cada capacidad en `_project/15_environment_profile.md` y degradar explícitamente la que falte (`WORKFLOW_MANUAL`, `HOOKS_MANUAL`, `SECUENCIAL_NO_AISLADO`, `COMPACT_FALLBACK`, `UI_MANUAL`).
> **Subagentes read-only:** según-versión-verificar-una-vez — este adaptador es un fallback genérico; verificar una vez por entorno concreto (ver "Capacidades" abajo) y registrar el resultado en `_project/15_environment_profile.md` en vez de reevaluarlo en cada cierre.

Usar este adaptador cuando la herramienta no tenga integración específica.

## SUBAGENT_WAIT_LIFECYCLE

Este adaptador fija la degradación por defecto del contrato portable `SUBAGENT_WAIT_LIFECYCLE` (`.agent/docs/extended_protocol.md`) para cualquier entorno sin integración específica.

Si el entorno no expone terminales observables del subagente, toda espera agotada es `WAIT_TIMEOUT_NONTERMINAL`: cierra la ventana de observación y nada más. No es `PASS` ni `FAIL`. El padre sigue consultando mientras el estado sea `RUNNING` o `COMPACTING`; si no puede volver a consultar, o el subagente resulta inaccesible, el resultado es `UNAVAILABLE` — y `NEEDS_ATTENTION` cuando haga falta intervención del builder.

El adaptador no debe llamar `interrupt` ni `close_agent` automáticamente por una espera agotada. Declarar en `_project/15_environment_profile.md` qué terminales expone realmente el entorno; sin esa declaración, se asume el caso conservador (ninguno) y no se emite veredicto por silencio.

## Inicio

1. Leer `CLAUDE.md`.
2. Leer `.agent/rules/architecture_constraints.md`.
3. Ejecutar manualmente `.agent/workflows/arrancar.md`.

## Ejecución

- Antes de modificar código existente, ejecutar `.agent/workflows/cambio-seguro.md`.
- Para publicar o compartir avances, usar `.agent/workflows/publicar-github.md`.
- Para instalar skills, usar `.agent/workflows/instalar-skills.md`.
- Para proponer skills, usar `.agent/workflows/sugerir-skills.md`.
- Para cada comando `/x`, buscar:
  1. `.agent/workflows/<comando>.md`
  2. `.claude/commands/<comando>.md`
  3. `.agents/skills/source-command-<comando>/SKILL.md`
- Si existe, seguir sus pasos.
- Si no existe, preguntar antes de improvisar.

## Capacidades

Declarar explícitamente lo que el entorno soporta:
- shell: sí/no
- edición de archivos: sí/no
- subagentes read-only: sí/no
- compactación: sí/no
- git: sí/no
- gh: sí/no
- hooks: sí/no
- navegador/browser: sí/no

Registrar limitaciones en `_project/15_environment_profile.md`.
