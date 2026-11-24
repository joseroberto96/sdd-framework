# Adaptadores de Entorno

El framework tiene un núcleo portable y adaptadores por herramienta.

## Núcleo portable

- `AGENTS.md` y `CLAUDE.md` — constitución del workspace con bloque gestionado por el framework.
- `.agent/rules/` — núcleo permanente (`runtime_core.md`) e invariantes de arquitectura (`architecture_constraints.md`).
- `.agent/skills/` — skills cargadas por contexto.
- `.agent/workflows/` — workflows portables, usables aunque no existan slash commands.
- `_project/` — estado externo y fuente de verdad.

## Adaptadores

| Entorno | Archivo | Estado |
|---|---|---|
| Claude Code | `claude-code.md` | **verified** |
| Codex / OpenAI coding agents | `codex.md` | **verified (estructural)** |
| OpenCode | `opencode.md` | **verified** |
| Antigravity | `antigravity.md` | experimental |
| Genérico | `generic.md` | experimental |

**Estado de soporte** (honestidad de portabilidad):
- **verified** — el framework genera wrappers de comandos para este entorno y `framework_doctor.py` los valida en cada corrida. El smoke runtime se declara por entorno; no se infiere desde esta etiqueta.
- **experimental** — documentado y portable vía `.agent/workflows/`. Abarca desde solo-documentado hasta tener wrappers generados y validados por el doctor; lo que aún no se garantiza es el uso en un ciclo de sesión real end-to-end.

`framework_doctor.validate_adapters` exige que cada adapter declare su estado (`**Soporte:**`) y su conformidad de degradación (`**Degradación:**`), de modo que la portabilidad anunciada coincida con la verificada.

Para Codex, `structural_support` y `runtime_smoke` son estados independientes. El primero demuestra cableado estático; el segundo requiere ejecutar `codex_runtime_smoke.py` en el entorno concreto. `runtime_smoke=unavailable` es informativo y no bloquea el CI general.

**OpenCode** está en `verified`: provee `opencode.json` (MCP `state_manager` + `instructions`), wrappers `.opencode/commands/`, skills `.opencode/skills/` y subagentes read-only `.opencode/agents/`, generados por `sync_claude_commands.py`, validados por el doctor y verificados empíricamente (detalle en `opencode.md`).

**Antigravity** también está en `experimental` avanzado, verificado contra el codelab oficial de Google (2026-08-25): `.agents/skills/source-command-*/SKILL.md` es su superficie **nativa** de proyecto (mismo directorio que genera para Codex — es una superficie compartida, no exclusiva de Codex pese al nombre histórico), auto-cargada por coincidencia de `description`. Le falta el ciclo de sesión real para `verified`, y arrastra una degradación propia (`MENU_DUPLICADO`, `_capability_matrix.md`): el IDE lee esa superficie **y** `.claude/commands/` a la vez, duplicando 29 de 68 entradas en el menú `/` (detalle en `antigravity.md`).

## Degradación por capacidad

`_capability_matrix.md` generaliza el patrón `SECUENCIAL_NO_AISLADO` a todas las capacidades del entorno (slash commands, hooks, subagentes, compactación, browser, shell, git). Cada adaptador apunta ahí con su marcador `**Degradación:**`.

## Regla

Si el entorno soporta una función nativa, usarla. Si no, degradar de forma explícita y registrar el modo usado.

## Adaptadores de capacidad (no de entorno)

A diferencia de los adaptadores de entorno (tabla arriba), estos envuelven una
capacidad opcional que puede faltar en cualquier entorno.

| Capacidad | Archivo | Estado |
|---|---|---|
| Grafo de código (`CODE_GRAPH`) | `code_graph.md` | experimental |
