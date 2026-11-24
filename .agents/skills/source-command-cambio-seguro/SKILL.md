---
name: "source-command-cambio-seguro"
description: "(interno — lo invoca el agente) Gate anti-regresión para cambios sobre código existente — Usar cuando: se va a modificar código existente, durante refactors, fixes y cambios de UI/API."
---

# source-command-cambio-seguro

Usar esta skill cuando el usuario pida ejecutar el comando fuente migrado `cambio-seguro`.

## Command Template

<!-- GENERATED: framework wrapper; edit manifest/source, then run sync_claude_commands.py -->

# /cambio-seguro


Fuente canónica: `.agent/workflows/cambio-seguro.md`

Ejecuta el artefacto canónico indicado arriba y sigue sus pasos. No dupliques lógica en este wrapper.
