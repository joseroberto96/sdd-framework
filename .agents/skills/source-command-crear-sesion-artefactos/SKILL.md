---
name: "source-command-crear-sesion-artefactos"
description: "(interno — lo invoca el agente) Sub-workflow interno — crea artefactos auxiliares de sesión y _refs. Invocado por /nuevo-proyecto y /migrar-proyecto — Usar cuando: termina /nuevo-proyecto o /migrar-proyecto y hay que crear los artefactos auxiliares de sesión y _refs — no se invoca directamente."
---

# source-command-crear-sesion-artefactos

Usar esta skill cuando el usuario pida ejecutar el comando fuente migrado `crear-sesion-artefactos`.

## Command Template

<!-- GENERATED: framework wrapper; edit manifest/source, then run sync_claude_commands.py -->

# /crear-sesion-artefactos


Fuente canónica: `.agent/workflows/crear-sesion-artefactos.md`

Ejecuta el artefacto canónico indicado arriba y sigue sus pasos. No dupliques lógica en este wrapper.
