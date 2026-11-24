# Adaptador — OpenCode

> **Soporte:** verified — el framework provee configuración nativa (`opencode.json` + `.opencode/`); los wrappers de comandos y subagentes se generan con `sync_claude_commands.py` y los valida `framework_doctor.py`. MCP `state_manager`, `instructions`, slash commands y subagentes read-only verificados empíricamente (opencode 1.17.4).
> **Degradación:** ver `.agent/adapters/_capability_matrix.md`. Compactación nativa si existe, si no → `COMPACT_FALLBACK`. Si `.opencode/` no se ha generado (no se corrió el sync), los slash commands degradan a `WORKFLOW_MANUAL` (ejecutar el workflow homónimo de `.agent/workflows/`) y los subagentes a `SECUENCIAL_NO_AISLADO`.
> **Subagentes read-only:** no — los wrappers declaran `edit/bash/webfetch: deny` en frontmatter, pero OpenCode no garantiza hoy que el host lo enforce (GW FW-044, ver "Aislamiento read-only" abajo); `SECUENCIAL_NO_AISLADO` es el modo válido y documentado, no una degradación accidental.

## SUBAGENT_WAIT_LIFECYCLE

OpenCode aplica el contrato portable `SUBAGENT_WAIT_LIFECYCLE` de `.agent/docs/extended_protocol.md` al esperar los subagentes generados en `.opencode/agents/`. `WAIT_TIMEOUT_NONTERMINAL` significa que se agotó una ventana de observación del padre: no es `PASS`, no es `FAIL`, no cancela el trabajo y no autoriza finalizar el workflow. Antes de un terminal, el adaptador puede observar `RUNNING` o `COMPACTING`.

Solo un terminal declarado cierra la espera: `PASS`, `FAIL`, `NEEDS_ATTENTION` o `UNAVAILABLE`. El adaptador no debe llamar `interrupt` ni `close_agent` automáticamente por una espera agotada; una entrada del usuario devuelve el turno sin cancelar al subagente.

Alcance verificado: los wrappers read-only y el MCP `state_manager` están verificados empíricamente (opencode 1.17.4), pero **la semántica de espera no**. Sin evidencia de terminales observables del dispatcher, la postura es la conservadora de la matriz: seguir consultando y, si el subagente resulta inaccesible, reportar `UNAVAILABLE` en vez de inventar veredicto.

## Activación

OpenCode lee `AGENTS.md` por defecto y, vía `opencode.json → instructions`, también `CLAUDE.md` y este adaptador. El framework provee en la raíz:

- `opencode.json` — registra el MCP `state_manager` (motor de estado; herramienta `mutate_state`) e `instructions`. No incluye credenciales ni proveedor: eso vive en el `opencode.json` global del usuario (`~/.config/opencode/`), que el framework no toca.
- `.opencode/commands/*.md` — slash commands nativos (misma superficie que Claude). **Generados; no editar a mano.**
- `.opencode/agents/` — 6 subagentes read-only generados (`preflight-reviewer`, `reviewer`, `security`, `area-frontend`, `area-backend`, `area-data`; `edit/bash/webfetch: deny`). **Generados; no editar a mano.** El contrato `executor` (4.2.0) NO se genera aquí: no es read-only y OpenCode no garantiza su aislamiento → el modo `/ciclo fase --paralelo` degrada a `SECUENCIAL_NO_AISLADO` (fila `PARALLEL_EXEC`).

Regenerar tras tocar workflows/manifest: `python3 .agent/scripts/sync_claude_commands.py`.

- OpenCode escanea `.agents/skills/` (superficie compartida Codex + Antigravity,
  verificado 2026-08-25 — ver `.agent/adapters/antigravity.md`) además de
  `.opencode/`. La plantilla añade `permission.skill {"source-command-*": "deny"}`
  en `opencode.json` para que esas skills no se filtren al namespace de OpenCode
  (FW-043). No quitar esa regla.
- En modo template, `instructions` carga `CLAUDE.md` con placeholders (FW-049); en un
  proyecto inicializado está relleno. Esperado.

## Motor de estado

- Preferir la herramienta MCP `mutate_state` del servidor `state_manager` (declarado en `opencode.json`). Verificable con `opencode mcp list` (debe figurar `connected`).
- Fallback equivalente por shell: `python3 .agent/scripts/state_manager.py mutate --action <accion> --payload '<json>'`.
- `core.md`, `06_bug_fixes.md`, `07_pending_clarifications.md`, `03_arch_decisions.md` y `00_session_brief.json` son proyecciones read-only autogeneradas. No editarlas a mano.

## Superficie de dos planos (4.0.0, FW-071)

Los 9 verbos del builder: `/nuevo-proyecto`, `/migrar-proyecto`, `/arrancar`,
`/diseñar`, `/ciclo`, `/nuevo-bug`, `/reconciliar`, `/gycd`,
`/actualizar-framework`. El resto de comandos de `.opencode/commands/` lleva
"(interno — lo invoca el agente)" en la descripción: son maquinaria que el
agente rutea según los gates, no menú para el builder.

## Comandos equivalentes

Cuando el usuario diga `/arrancar`, `/gycd`, `/debug`, etc., OpenCode resuelve el wrapper nativo de `.opencode/commands/`, que apunta al artefacto canónico en `.agent/workflows/`. Si el wrapper no existiera, ejecutar directamente el workflow homónimo (`WORKFLOW_MANUAL`).

## Reglas prácticas

- Usar shell para búsquedas (`rg`) y pasar archivos concretos al agente.
- Antes de modificar código existente, ejecutar `/cambio-seguro`.
- En `/cierre-modulo`, delegar la auditoría a los subagentes read-only `reviewer` y `security`; si no estuvieran disponibles, declarar `SECUENCIAL_NO_AISLADO`.
- **Aislamiento read-only (GW FW-044):** OpenCode no ofrece hoy una forma verificable de garantizar que un subagente corre aislado con `edit/bash/webfetch: deny` *enforced* por el host (la declaración vive en el frontmatter del agente, pero el runtime comparte contexto). Mientras eso no cambie, `SECUENCIAL_NO_AISLADO` es el **modo válido y documentado** en OpenCode — no una degradación accidental: declararlo en la salida del cierre y ejecutar la checklist reviewer+security en secuencia. No fingir aislamiento que el host no garantiza.
- Usar compactación nativa si existe; antes, registrar pendientes en `_project/13_session_journal.md`.
- Declarar la capacidad del ejecutor en `03_project_state.json → execution_profile.capability_tier`. Un modelo local (p. ej. vía Ollama) suele ser `standard` o `limited`; si es `limited`, `/cierre-modulo` exige `requires_second_review: true`.
- Antes de usar una copia del framework en un proyecto real, correr `python3 .agent/scripts/framework_doctor.py`.

## Tests

Preferir que las suites grandes se ejecuten desde shell; el agente diagnostica con la salida.
