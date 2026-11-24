# Workflows Portables — Índice y Superficies

> **IMPORTANTE:** Esta carpeta contiene los workflows canónicos y portables para cualquier entorno.
> El índice canónico es `manifest.json`. Los wrappers de `.claude/commands/`, `.opencode/commands/` y `.agents/skills/` son generados y nunca se editan a mano.

## Diferencia entre fuentes y superficies generadas

| Aspecto | `.agent/workflows/` | `.claude/commands/` · `.opencode/commands/` · `.agents/skills/` | `.agent/adapters/` |
|---------|--------------------|----------------------------------------------------------------|-------------------|
| Propósito | Protocolo portable canónico | Wrappers nativos generados desde `manifest.json` | Mapeo y degradación por entorno |
| Activación | Por el agente o manual | `/comando` o skill nativa del host | Según el arnés activo |
| Uso | Todos los entornos | Claude Code, OpenCode, Codex y Antigravity | Todos |
| Estado | Siempre `_project/` | Siempre `_project/` | Declara capacidades reales |

## Mantenimiento

La lógica vive aquí. No editar los directorios de wrappers generados a mano.

Después de cambiar un workflow o `manifest.json`:

```bash
python3 .agent/scripts/sync_claude_commands.py
python3 .agent/scripts/framework_doctor.py
```

## Superficie de dos planos (`manifest.json`)

- **Comandos del builder (`builder_facing: true`):**
  `/arrancar` · `/reconciliar` · `/gycd` · `/diseñar` · `/nuevo-bug` · `/nuevo-proyecto` · `/migrar-proyecto` · `/actualizar-framework` · `/ciclo`
- **Maquinaria interna (invocada por el agente según gates):**
  `/entrevistar` · `/tdd` · `/cierre-modulo` · `/scope-siguiente` · `/enriquecer-ticket` · `verificar-ejecutable` · `preflight-review` · `/aprender-de-error` · `/retrospectiva-framework` · `/validate-arch` · `/cambio-seguro` · `/debug` · `/clarificar` · `/nueva-fase` · `/publicar-github` · `/refactorizar-arquitectura` · `/skills` · `/añadir-experto` · `/actualizar-invariante` · `descubrir-producto` · `prd-mvp` · `tech-design-mvp` · `instalar-skills` · `sugerir-skills` · `lean-context` · `crear-sesion-artefactos` · `/foco` · `mantenimiento`
