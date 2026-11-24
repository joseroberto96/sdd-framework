---
name: "source-command-aprender-de-error"
description: "(interno — lo invoca el agente) Captura una lección de la conversación actual en _project/22_lessons.md tras aprobación explícita; no modifica CLAUDE.md — Usar cuando: el usuario corrige explícitamente un error del agente que revela un patrón repetible (\"no hagas X\", \"siempre haz Y\")."
---

# source-command-aprender-de-error

Usar esta skill cuando el usuario pida ejecutar el comando fuente migrado `aprender-de-error`.

## Command Template

<!-- GENERATED: framework wrapper; edit manifest/source, then run sync_claude_commands.py -->

# /aprender-de-error


Fuente canónica: `.agent/workflows/aprender-de-error.md`

Ejecuta el artefacto canónico indicado arriba y sigue sus pasos. No dupliques lógica en este wrapper.
