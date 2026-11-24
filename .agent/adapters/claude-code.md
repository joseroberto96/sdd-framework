# Adaptador — Claude Code

> **Soporte:** verified — wrappers generados en `.claude/commands/` y validados por `framework_doctor.validate_claude_wrappers`. Usado en proyectos reales.
> **Degradación:** ver `.agent/adapters/_capability_matrix.md`. Expone slash commands, hooks (`.claude/settings.json`) y compactación nativa; subagentes read-only según versión, si no → `SECUENCIAL_NO_AISLADO`. Modo `/ciclo fase --paralelo` (4.2.0): requiere subagentes ejecutores + `git worktree` (fila `PARALLEL_EXEC` de la matriz); Claude Code los soporta vía Agent tool + worktrees.
> **Subagentes read-only:** según-versión-verificar-una-vez — el Agent tool soporta contratos read-only, pero el enforcement depende de la versión instalada; verificar una vez por entorno (no evaluar en cada cierre) y reusar el resultado.

## SUBAGENT_WAIT_LIFECYCLE

Claude Code aplica el contrato portable `SUBAGENT_WAIT_LIFECYCLE` de `.agent/docs/extended_protocol.md`. Aquí el vector de fallo **no es el dispatcher**: el Agent tool no expone una espera con timeout que mate al subagente — los subagentes corren en background y el host notifica la finalización. El infractor posible es el **padre**, que puede narrar un terminal que nunca observó.

`WAIT_TIMEOUT_NONTERMINAL` cubre por tanto cualquier ventana de observación agotada sin notificación del host: silencio, demora o compactación del subagente. No es `PASS`, no es `FAIL` y no autoriza cerrar el workflow. Mientras no llegue la notificación, el estado asumido es `RUNNING` o `COMPACTING`, nunca un terminal inventado.

Reglas específicas del entorno:

- No resumir, predecir ni dar por bueno el resultado de un subagente pendiente. Si el builder pregunta antes de la notificación, la respuesta es que sigue en curso.
- `TaskStop` es el equivalente local de `close_agent`: el adaptador no debe llamar `interrupt` ni `close_agent` automáticamente como consecuencia de una espera agotada. Detener es un acto deliberado y declarado, nunca una reacción a la demora.
- La compactación nativa del subagente alarga la espera sin cambiar su estado. Un contexto grande vuelve este caso más frecuente, no más sospechoso.
- Si el subagente queda inaccesible o su tarea desaparece del árbol, reportar `UNAVAILABLE` o `NEEDS_ATTENTION`.

Limitación honesta: el framework documenta y valida este cableado, pero no intercepta `TaskStop` ni promete enforcement runtime del host.

## SESSION_HYGIENE

Aplica la regla 7 de `.agent/rules/runtime_core.md` con los comandos de este host. El contexto es el recurso que hay que gestionar: el rendimiento baja a medida que la ventana se llena, y cada reanudación en frío reprocesa toda la conversación (piloto 5.0: arrancar ~33K tokens; reanudar tras más de 1 h, 120K–379K).

- Política de sesión: si prevés ausentarte más de ~1 h, cierra con `/gycd`; al volver, sesión nueva pegando el `Siguiente:`; si no tienes prompt o quieres ver el estado, `/arrancar`.
- `/clear` entre tareas no relacionadas. En una frontera de módulo, mejor `/gycd` y sesión nueva: el estado vive en ficheros.
- `/btw` para una pregunta lateral: la respuesta no entra en el historial.
- `/rename` para nombrar la sesión como una rama (`modulo-x`, `release-5-1`).
- `/compact <qué conservar>` cuando se compacta a mano; `/rewind` para volver a un punto anterior en vez de acumular correcciones.
- Tras dos correcciones fallidas del mismo problema: `/gycd`, sesión nueva y un prompt que incorpore lo aprendido.
- Modelo y esfuerzo se eligen al empezar. Cambiar de modelo a mitad invalida la caché de prompt y el turno siguiente se cobra en frío. Cambiar el esfuerzo la conserva con Opus 5.5, Sonnet 5.5, Haiku 5.5 y Fable 5.1 por suscripción o clave de API; con otros modelos, o por Bedrock, Vertex o una pasarela, la invalida entera (FW-320, verificado el 2026-10-12). Un subagente con otro modelo o esfuerzo no toca la caché de quien lo lanza.
- Exploración amplia (muchos ficheros o logs): un subagente, que devuelve solo el resumen.

## TURN_CLOSE_HOOK

El `Stop` llama a `.agent/scripts/turn_close_hook.py` (5.3.0, ADR-074), que pasa
el último mensaje por el validador único del cierre (`turn_close.py`, modo
`live`). Si el cierre tiene errores bloquea **una vez** con la corrección de
cada defecto y el agente responde solo con las líneas de cierre corregidas, sin
repetir la respuesta anterior; nunca bloquea dos veces seguidas. Los avisos solo
se registran: `INSTRUCCION_LARGA` y `PROHIBICIONES` avisan sin bloquear desde la
5.3.2 (FW-402). Si el hook falla o agota su tiempo, deja pasar y lo registra. No
evalúa en modo `template` ni en subagentes. Telemetría: solo códigos y
contadores, fuera del repo, en `~/.cache/framework-v4/<proyecto>-<huella>/turn_close.jsonl`.
Reversión: `turn_close_hook: off` en `_project/15_environment_profile.md` lo deja
en solo registro.

En Claude Code es el único hook `Stop` del framework: emite también el
recordatorio de `/gycd`, una vez por sesión. Además corta bucles (el mismo test
en rojo con el mismo fallo, o el mismo fichero editado y revertido, 3 veces:
bloquea una vez y pide cerrar con `/gycd` o `/reconciliar`) y avisa al builder de
sesión larga sin bloquear (una vez por compactación y una vez al pasar de 200 k
de contexto). Lee solo el final de la transcripción. Contrato del evento
verificado en Claude Code 2.1.296; `SubagentStop` no se engancha.

## Activación

Claude Code usa:
- `CLAUDE.md` como constitución.
- `.claude/commands/*.md` como wrappers generados desde `.agent/workflows/manifest.json`.
- `.claude/agents/*.md` como subagentes read-only generados (`sync_claude_commands.py`) desde los contratos portables de `.agent/subagents/` — `reviewer`, `security`, `preflight-reviewer`, `area-frontend|backend|data`, con `tools: Read, Grep, Glob` (sin `Bash`/`Edit`/`Write`; FW-206). El Agent tool los despacha por `subagent_type`.
- `.claude/settings.json` para hooks (Production Command Guard `PreToolUse`, cierre de turno y recordatorio `/gycd` en `Stop`, nudge `/debug`, recordatorio `/cierre-modulo`, formateo post-edición). Lo siembran `/nuevo-proyecto` y `/migrar-proyecto` desde `.agent/templates/settings.json.base` vía `python3 .agent/scripts/ensure_claude_settings.py`, que mergea los hooks del framework sin pisar los del proyecto (FW-204). El doctor lo exige también en modo `initialized`.
- `.mcp.json` en la raíz del proyecto (copiado por `/nuevo-proyecto` o `/migrar-proyecto` desde `.agent/templates/mcp.json.base`) para registrar el MCP `state_manager`. Claude Code lo lee nativamente y expone `mutate_state`, `get_full_state`, `get_session_brief` como tools. Si `.mcp.json` no existe, usar el fallback shell: `python3 .agent/scripts/state_manager.py mutate --action <a> --payload '<json>'`.

## Comandos — superficie de dos planos (4.0.0, FW-071)

El builder usa los 9 verbos de producto (`builder_facing: true` en el
manifest): `/nuevo-proyecto`, `/migrar-proyecto`, `/arrancar`, `/diseñar`,
`/ciclo`, `/nuevo-bug`, `/reconciliar`, `/gycd`, `/actualizar-framework`.

El resto de slash commands existe en el menú `/` marcado
"(interno — lo invoca el agente)": el AGENTE los invoca según los gates
(`/tdd`, `/cambio-seguro`, `/cierre-modulo`, `/enriquecer-ticket`…) y el
usuario avanzado puede llamarlos a mano. No enseñar la maquinaria al
builder: el ruteo vive en CLAUDE.md → "Maquinaria interna".

Para compactación, usar la nativa de Claude Code. La continuidad normal tras compactación reanuda verificando la vista `recovery` sin disparar auditoría completa por defecto. Si el contexto resulta dudoso o inconsistente con el scope, ejecutar `/reconciliar`. Antes de compactar voluntariamente, persistir pendientes en `_project/13_session_journal.md`.

## Degradación

Si un slash command no está disponible, abrir el archivo equivalente en `.claude/commands/` o `.agent/workflows/` y ejecutar sus pasos manualmente.

## Mantenimiento

No editar `.claude/commands/*.md` ni `.claude/agents/*.md` a mano (son superficies generadas). Cambiar `.agent/workflows/`, `manifest.json` o los contratos de `.agent/subagents/` y ejecutar:

```bash
python3 .agent/scripts/sync_claude_commands.py
python3 .agent/scripts/framework_doctor.py
```

## Restricción

`.claude/settings.local.json` no se copia ni se distribuye con la plantilla. Si existe localmente, debe estar gitignored y sin secretos; cualquier permiso local se configura por proyecto.
