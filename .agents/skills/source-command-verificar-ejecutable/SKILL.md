---
name: "source-command-verificar-ejecutable"
description: "(interno — lo invoca el agente) Verificación ejecutable invocada por /cierre-modulo paso 3.8; admite fallback manual reproducible (workflow interno, sin wrapper) — Usar cuando: hay que demostrar que un módulo funciona de verdad (no solo tests verdes) — invocado por /cierre-modulo paso 3.8."
---

# source-command-verificar-ejecutable

Usar esta skill cuando el usuario pida ejecutar el comando fuente migrado `verificar-ejecutable`.

## Command Template

<!-- GENERATED: framework wrapper; edit manifest/source, then run sync_claude_commands.py -->

# /verificar-ejecutable


Fuente canónica: `.agent/workflows/verificar-ejecutable.md`

Ejecuta el artefacto canónico indicado arriba y sigue sus pasos. No dupliques lógica en este wrapper.
