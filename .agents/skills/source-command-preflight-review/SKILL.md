---
name: "source-command-preflight-review"
description: "(interno — lo invoca el agente) Revisión read-only previa a CODE para planes, cambios y refactors — workflow interno — Usar cuando: un plan, ticket enriquecido, TDD, refactor o cambio sobre código existente puede estar a punto de omitir un gate del framework."
---

# source-command-preflight-review

Usar esta skill cuando el usuario pida ejecutar el comando fuente migrado `preflight-review`.

## Command Template

<!-- GENERATED: framework wrapper; edit manifest/source, then run sync_claude_commands.py -->

# /preflight-review


Fuente canónica: `.agent/workflows/preflight-review.md`

Ejecuta el artefacto canónico indicado arriba y sigue sus pasos. No dupliques lógica en este wrapper.
