# Perfil de Entorno

> Capacidades reales del entorno donde se está usando el framework.

## Entorno actual

- Herramienta: [Codex | Claude Code | Antigravity | OpenCode | Otro]
- Fecha de revisión:
- Sistema operativo:
- Shell disponible: sí/no
- Edición de archivos disponible: sí/no
- Slash commands nativos: sí/no
- Hooks nativos: sí/no
- Subagentes disponibles: sí/no
- Subagentes read-only verificables: sí/no
- Compactación nativa: sí/no
- Browser/UI automation: sí/no
- Red disponible: sí/no
- Git disponible: sí/no
- GitHub CLI (`gh`) disponible: sí/no
- Grafo de código disponible (CODE_GRAPH): sí/no
- Comandos nativos que duplican workflows: [lista o "ninguno"]
- Última auditoría de comandos: [fecha o pendiente]
- Soporte estructural Codex (`structural_support`): [pass | fail | unavailable]
- Smoke runtime Codex (`runtime_smoke`): [pass | fail | unavailable]
- Superficies Codex requeridas: [cli | cli,desktop]
- Smoke runtime Codex CLI: [pass | fail | unavailable]
- Smoke runtime Codex Desktop/app-server: [pass | fail | unavailable]
- Versión de Codex: [versión o no disponible]
- Versión de Codex CLI: [versión o no disponible]
- Versión de Codex Desktop/app-server: [versión o no disponible]
- Modelo Codex smoke: [modelo explícito o no declarado]
- Esfuerzo Codex smoke: [none | minimal | low | medium | high | xhigh | max | ultra]
- Hooks Codex: [gestionados | manuales | no disponibles]
- MCP local `state_manager` (Codex `.codex/config.toml` native; `.mcp.json` espejo opcional): [conectado | fallback | no disponible]
- Diferencias Codex conocidas: [lista o "ninguna"]
- `turn_close_hook`: [on | off] — `off` deja el hook de cierre de turno en solo registro (ADR-074)
- `fw_prefix`: [prefijo corto del proyecto para sus FW, p. ej. TL; en la plantilla se deja vacío]
- `documentary_paths`: [rutas del proyecto que no lanzan la suite en el pre-commit, separadas por coma, p. ej. docs/ipn/*; en la plantilla se deja vacío]
- `suite_command`: [comando de la suite completa si no es pytest ni unittest, p. ej. scripts/run_tests.sh --full; en la plantilla se deja vacío]

## Modo de auditoría de cierre

- `READ_ONLY_SUBAGENTS` si hay subagentes aislados con permisos de solo lectura.
- `SECUENCIAL_NO_AISLADO` si la auditoría se ejecuta en el agente principal.

Modo actual: `[COMPLETAR]`

## Adaptador activo

- Archivo: `.agent/adapters/[codex|claude-code|antigravity|opencode|generic].md`

## Limitaciones operativas

- [COMPLETAR]

## Decisiones de degradación

| Fecha | Capacidad no disponible | Degradación usada | Riesgo aceptado |
|---|---|---|---|
| — | — | — | — |

## Política de comandos nativos

- Si el entorno ofrece una capacidad nativa equivalente y confiable, usar la nativa.
- Mantener el workflow portable solo como fallback documentado.
- No crear slash commands duplicados cuando el comando nativo aparece en el mismo menú.
- Si se usa un fallback portable, declarar el motivo en la respuesta o en `13_session_journal.md` si afecta continuidad.
