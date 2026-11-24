# Adaptador — Antigravity

> **Soporte:** experimental — `.agents/skills/source-command-*/SKILL.md` es la superficie **nativa** de proyecto de Antigravity (verificado contra el codelab oficial de Google, 2026-08-25): el IDE auto-carga una skill cuando la petición del usuario coincide con su `description`, igual que en el alcance global `~/.gemini/config/skills/`. El sync (`sync_claude_commands.py`) genera y valida esa carpeta en cada corrida — **no es "sin wrappers"**, es una superficie que Antigravity comparte con Codex. Sigue en `experimental` porque falta ejercer un ciclo de sesión real en el entorno (requisito de `adapters/README.md` para pasar a `verified`).
> **Descubrimiento doble (FW-135, medido en campo 2026-08-25):** Antigravity también lee `.claude/commands/*.md` (extensión `anthropic.claude-code` instalada) — el menú `/` del builder muestra las 29 entradas de `.claude/commands/` y luego, al seguir el scroll, las 39 de `.agents/skills/` otra vez, 29 duplicadas. Sin mecanismo de ignore conocido a nivel de repo (el codelab solo documenta ajustes de proyecto en la UI, no un fichero portable) → degradación `MENU_DUPLICADO` (`_capability_matrix.md`): el builder usa las entradas con nombre (`.claude/commands/`) y descarta las `source-command-*` del menú manual, confiando en que la auto-carga por `description` (FW-137) haga el ruteo real sin que el builder tenga que elegir del menú.
> **Degradación:** ver `.agent/adapters/_capability_matrix.md`. Comandos: superficie nativa generada, ver arriba — degradación de menú `MENU_DUPLICADO`, no `WORKFLOW_MANUAL`. Motor de estado (MCP) sin registrar → `MCP_FALLBACK` (ver abajo). Sin compactación nativa → `COMPACT_FALLBACK`. Subagentes sin aislamiento read-only → `INDEPENDENT_SUBAGENT_NO_ISOLATION` si hay dispatcher independiente, `SECUENCIAL_NO_AISLADO` si no.
> **Subagentes read-only:** no — verificado contra el codelab y confirmado por el agente en sesión real (2026-08-25): no hay flag nativo para revocar mecánicamente las herramientas de escritura de un subagente a nivel de proceso. Mismo régimen que Codex: usar un subagente distinto bajo contrato no-write y declarar `INDEPENDENT_SUBAGENT_NO_ISOLATION` cuando haya despacho independiente disponible; `SECUENCIAL_NO_AISLADO` si no.

## SUBAGENT_WAIT_LIFECYCLE

Antigravity aplica el contrato portable `SUBAGENT_WAIT_LIFECYCLE` de `.agent/docs/extended_protocol.md`. `WAIT_TIMEOUT_NONTERMINAL` termina solo la ventana de polling del padre: no es `PASS`, no es `FAIL` y no autoriza cerrar el workflow. Los estados observacionales admitidos antes de un terminal son `RUNNING` y `COMPACTING`; los terminales, `PASS`, `FAIL`, `NEEDS_ATTENTION` y `UNAVAILABLE`.

El adaptador no debe llamar `interrupt` ni `close_agent` automáticamente como consecuencia de una espera agotada. Mismo régimen que Codex: sin aislamiento read-only verificable (ver cabecera) tampoco hay interceptación mecánica del cierre de subagentes, así que el framework documenta y valida el cableado sin prometer enforcement runtime del IDE.

Alcance verificado: ninguno en campo — este adaptador sigue en `experimental` y la semántica de espera no se ha ejercido en una sesión real. Ante silencio prolongado, `UNAVAILABLE` o `NEEDS_ATTENTION`, nunca un terminal supuesto.

## Activación

`.agent/workflows/` sigue siendo la fuente canónica; `.agents/skills/source-command-*/SKILL.md` es su proyección nativa en Antigravity, generada por `sync_claude_commands.py` desde `.agent/workflows/manifest.json`. Antigravity no tiene slash commands de proyecto (solo built-ins como `/browser`, `/schedule`): el ruteo real ocurre por auto-carga de skill cuando la `description` coincide con la petición del usuario (FW-137) — por eso toda entrada del manifest lleva una cláusula "Usar cuando: ..." explícita, no solo una etiqueta.

El fichero de instrucciones de raíz que Antigravity lee es `AGENTS.md` o `GEMINI.md`; este repo mantiene `GEMINI.md` como puntero fino a `AGENTS.md` (sin duplicar contenido, para no abrir un tercer sitio de drift junto a `CLAUDE.md`/`AGENTS.md`).

## Motor de estado (MCP)

El MCP `state_manager` se registra en Claude/Codex vía `.mcp.json` en la raíz del repo. **Antigravity no lee ese fichero**: sus MCP se configuran en `~/.gemini/config/mcp_config.json` (global, activable por proyecto desde la configuración del IDE — Configuración → Personalización → Agregar MCP). Si ese registro no está hecho, toda mutación cae al fallback por shell:

```bash
python3 .agent/scripts/state_manager.py mutate --action <a> --payload '<json>'
```

Verificar al inicio de cada proyecto en Antigravity si `state_manager` está registrado en `~/.gemini/config/mcp_config.json`; si no, declarar `MCP_FALLBACK` y usar el comando de arriba para toda mutación de estado.

## Estado

Usar `get_work_context` según la etapa (`startup`, `implementation`, `closure`, `recovery`), vía MCP si está configurado en el entorno o vía el fallback CLI:

```bash
python3 .agent/scripts/state_manager.py query --view <startup|implementation|closure|recovery>
```

Seguir el cursor y abrir solo las referencias necesarias devueltas por la vista. `_project/core.md` es la proyección corta de recuperación y el volcado de estado completo queda reservado para diagnóstico explícito.

Antes de modificar código existente, ejecutar `.agent/workflows/cambio-seguro.md`.
Para publicar fase 0/MVP o compartir actualizaciones, ejecutar `.agent/workflows/publicar-github.md`.
Para instalar skills locales al proyecto, ejecutar `.agent/workflows/instalar-skills.md`.
Para proponer skills ante señales claras, ejecutar `.agent/workflows/sugerir-skills.md`.

## Subagentes

Antigravity **sí tiene dispatcher de subagentes en paralelo** (confirmado en campo, 2026-08-25, cierre real de `phase_01_module_06_review` en `Example Service` con `execution_profile.capability_tier: limited`) — pero sin aislamiento read-only mecánico (ver arriba). Usar siempre:
- `Reviewer` debe seguir `.agent/subagents/reviewer.md`, bajo contrato no-write, declarando `INDEPENDENT_SUBAGENT_NO_ISOLATION`.
- `Security` debe seguir `.agent/subagents/security.md`, mismo contrato.

`SECUENCIAL_NO_AISLADO` queda como fallback solo si en un proyecto concreto el dispatcher no está disponible (versión/config distinta) — verificar una vez por entorno y reusar el resultado, no asumir sin comprobar.

## Compactación

Antes de cambiar ventana o limpiar contexto, usar la compactación nativa de Antigravity y guardar pendientes en `_project/13_session_journal.md`. Sin compactación nativa, aplicar `COMPACT_FALLBACK` según `.agent/adapters/_capability_matrix.md`.
