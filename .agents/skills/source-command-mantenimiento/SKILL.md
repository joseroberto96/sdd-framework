---
name: "source-command-mantenimiento"
description: "(interno — lo invoca el agente) (interno — lo invoca /ciclo) Flujo ligero para un proyecto en producción: un cambio acotado con gates mecánicos y escalada automática a módulo — Usar cuando: lifecycle.stage es production y el builder pide un ajuste o fix."
---

# source-command-mantenimiento

Usar esta skill cuando el usuario pida ejecutar el comando fuente migrado `mantenimiento`.

## Command Template

<!-- GENERATED: framework wrapper; edit manifest/source, then run sync_claude_commands.py -->

# /mantenimiento


Fuente canónica: `.agent/workflows/mantenimiento.md`

Ejecuta el artefacto canónico indicado arriba y sigue sus pasos. No dupliques lógica en este wrapper.
