# Procedencia de Skills

> Registro obligatorio de skills técnicas instaladas o importadas desde fuentes externas.

## Política

- No instalar skills globales para un proyecto sin registrarlas aquí.
- No ejecutar instaladores remotos sin aprobación explícita del usuario.
- Instalar por defecto en scope local del proyecto: `.agent/skills/imported/[nombre]/`.
- No instalar en `$HOME`, `$CODEX_HOME`, configuración global de Claude/Codex ni rutas del sistema salvo aprobación explícita.
- Preferir versiones fijadas: `paquete@version`.
- Registrar fuente, versión, fecha, comando usado y resumen de archivos creados.
- Si no se puede verificar procedencia o versión, marcar como `no_verificado` y no tratar la skill como autoridad.

## Fuentes de referencia sugeridas

Estas fuentes no quedan aprobadas automáticamente para el proyecto. Si se usan, registrar la decisión en la tabla de registro.

### Plugin oficial Anthropic

| Propiedad | Valor |
|---|---|
| URL | `claude.com/plugins/claude-code-setup` |
| Tipo | Plugin de plataforma (sin instalación de archivos) |
| Categorías cubiertas | MCP servers, skills, hooks, subagents, slash commands |
| Estado inicial | `referencia` |
| Modo de uso | Invocar desde Claude Code: *"recommend automations for this project"* |

Las recomendaciones de esta fuente se registran con `fuente: claude.com/plugins/claude-code-setup` y el estado que apruebe el usuario.

### Referencia externa revisada: Ponytail

| Propiedad | Valor |
|---|---|
| URL | `https://github.com/DietrichGebert/ponytail.git` |
| Tipo | Skill/plugin externo de minimalismo operativo |
| Pin aprobado para referencia | tag `v4.7.0`, commit `adad50d9b393926b2dd5ed7225dcb1848b9df408` |
| Categorías cubiertas | YAGNI, stdlib/plataforma primero, review de sobreingeniería |
| Estado inicial | `referencia` |
| Modo de uso | No instalar por defecto. El framework incorpora una skill core subordinada en `.agent/skills/minimal_solution_core.md`; instalar Ponytail externo solo con aprobación explícita y fuente exacta. |

## Registro

| Fecha | Skill / paquete | Versión | Fuente | Scope | Comando | Archivos instalados | Estado |
|---|---|---|---|---|---|---|---|
| — | — | — | — | — | — | — | — |

## Estados

- `aprobado`: instalado con versión fijada y fuente conocida.
- `no_verificado`: instalado o copiado sin suficiente evidencia.
- `rechazado`: no usar; fuente dudosa, versión desconocida o conflicto con el framework.
- `referencia`: no instalado; usado solo como fuente de lectura secundaria.

## Scopes

- `project-local`: instalado dentro de `.agent/skills/` del proyecto.
- `global-aprobado`: instalación global aprobada explícitamente por el usuario.
