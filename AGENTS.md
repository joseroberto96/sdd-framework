# [NOMBRE_PROYECTO] — Constitución del Workspace

> Esta zona es del proyecto: el framework nunca la reescribe. Lo que hay entre los
> marcadores `GESTIONADO: framework` lo gestiona `propagate_framework.py`.

## Stack
- [Describe tu stack aquí]

## Convenciones
- [Define tus convenciones aquí]

## Arquitectura
- [Describe la arquitectura aquí]

## Restricciones
- [Define restricciones del entorno aquí]

<!-- BEGIN GESTIONADO: framework sha256=765bca27e1866894 — propagate_framework.py; no editar a mano -->
# Framework — Instrucciones para agentes

Entrada portable para Codex, OpenAI coding agents y entornos compatibles con
`AGENTS.md`. Este archivo orienta la etapa actual; no es un volcado del
framework completo.

## Carga progresiva y fuentes de verdad

- Cargar primero `.agent/rules/runtime_core.md`.
- Abrir las reglas constitucionales y operativas completas solo bajo demanda,
  según la etapa: `GUARDRAILS_MASTER.md` si existe, `CLAUDE.md` y
  `.agent/rules/architecture_constraints.md` se activan antes de la decisión
  o implementación que los requiera; no se cargan completos en startup.
- Usar `state_manager.get_work_context` con la vista `startup`,
  `implementation`, `closure` o `recovery`. Seguir sus cursores y abrir solo
  las referencias de la etapa. `get_full_state` es diagnóstico explícito.
- `_project/03_project_state.json` es el estado canónico y
  `_project/04_active_scope.md` delimita el scope. Mutar exclusivamente vía
  MCP `mutate_state` o `python3 .agent/scripts/state_manager.py mutate`.
- Las proyecciones (`core.md`, `06_bug_fixes.md`, `07_pending_clarifications.md`,
  `03_arch_decisions.md`, `00_session_brief.json`) son de solo lectura. La
  precedencia completa vive en `GUARDRAILS_MASTER.md`,
  `.agent/rules/architecture_constraints.md` y
  `.agent/docs/extended_protocol.md`.
- Responder con ruta, decisión, evidencia y siguiente acción. Si falta una
  sección necesaria, paginar o pedirla; nunca inventar ni bajar el rigor.

## Etapas y workflows

`startup` inicia; `implementation` precede al código; `closure` precede al
cierre; `recovery` se usa tras interrupción, compactación o conflicto.
La conversación no lineal exige `.agent/workflows/reconciliar.md` y
`/reconciliar` antes de escribir, publicar, instalar o continuar.
`/gycd` y `/cierre-modulo` son fronteras limpias para persistir pendientes en
`_project/13_session_journal.md`.
La compactación del host no está bajo el control del framework: la continuidad
normal tras compactación reanuda verificando la vista `recovery` sin disparar
auditoría completa por defecto. Si el contexto resulta dudoso o contradictorio,
activar la auditoría completa de `/reconciliar` antes de continuar.

`.agent/workflows/manifest.json` es el índice canónico. Los wrappers de
`.claude/commands/`, `.opencode/commands/` y `.agents/skills/` son generados:
editar manifest/workflow y ejecutar `python3 .agent/scripts/sync_claude_commands.py`.

Equivalencias:

<!-- BEGIN GENERADO: tabla-comandos — sync_claude_commands.py; no editar a mano -->
Índice compacto; no cargar workflows futuros ni sus descripciones al arrancar.
- Builder: `/arrancar` · `/reconciliar` · `/gycd` · `/diseñar` · `/nuevo-bug` · `/nuevo-proyecto` · `/migrar-proyecto` · `/actualizar-framework` · `/ciclo`
- Internos: `/entrevistar` · `/tdd` · `/cierre-modulo` · `/scope-siguiente` · `/enriquecer-ticket` · `verificar-ejecutable` · `preflight-review` · `/aprender-de-error` · `/retrospectiva-framework` · `/validate-arch` · `/cambio-seguro` · `/debug` · `/clarificar` · `/nueva-fase` · `/publicar-github` · `/refactorizar-arquitectura` · `/skills` · `/añadir-experto` · `/actualizar-invariante` · `descubrir-producto` · `prd-mvp` · `tech-design-mvp` · `instalar-skills` · `sugerir-skills` · `lean-context` · `crear-sesion-artefactos` · `/foco` · `mantenimiento`
- Fuente canónica de etapa, trigger y ruta: `.agent/workflows/manifest.json`.
- Abrir solo el `source` de la etapa actual y los documentos que devuelva `get_work_context`.
<!-- FIN GENERADO: tabla-comandos -->

Mantenimiento: `python3 .agent/scripts/framework_doctor.py` valida el framework;
`python3 .agent/scripts/sync_claude_commands.py` sincroniza wrappers y docs.

En Codex, trabajar en la conversación principal; delegar solo según
`.agent/adapters/codex.md` §CODEX_SMALL_TASK_DELEGATION.

## Reglas permanentes

- **Arrancar**: usar `state_manager.get_work_context(view="startup")`; `_project/core.md` es la proyección corta de recuperación y `get_full_state` es diagnóstico explícito.
- **Estado en archivos**: no reconstruir contexto desde memoria. Mutar exclusivamente vía MCP `mutate_state` o CLI; las proyecciones son de solo lectura.
- **Conversación no lineal**: ante interrupción, compactación dudosa, recap inconsistente o choque con scope → `/reconciliar` antes de escribir o publicar.
- **Tier de rigor**: antes de implementar, clasificar por blast radius + reversibilidad (`tier_0|tier_1|tier_2`) y registrar en `work_tiers`; declarar capacidad (`strong|standard|limited`) en el estado. Si es `limited`, exigir segunda revisión. Si toca persistencia/API, clasificar specs canónicas.
- **Anti-regresión**: antes de tocar código existente, ejecutar `/cambio-seguro`, inventariar `PRESERVAR` y clasificar el cambio. No quitar funcionalidad sin aprobación.
- **Gate de módulo**: un módulo solo llega a `completed` vía `/cierre-modulo` con `ready_for_validation`, ACs 100% verificados y 0 bugs críticos. No implementar módulos `blocked`. Si la fase es una idea amplia, usar `/diseñar` antes de construir.
- **Criterio de parada**: bugs críticos abiertos o ≥2 `closure_rejections` → no abrir módulos nuevos. Abrir módulo "Estabilización".
- En Codex, leer `.agent/adapters/codex.md`, consultar la vista actual y paginar por cursor. Sus límites acotan transporte, no miden la ventana del host ni autorizan omitir rigor.
- Instalar skills solo en `.agent/skills/imported/`, con procedencia fijada; no copiar `.claude/settings.local.json` ni usar paquetes sin pin.
- Antes de un proyecto real ejecutar `framework_doctor.py`; si declara `framework_mode: template`, usar `/nuevo-proyecto` o `/migrar-proyecto`.
- Si hay dispatcher sin aislamiento read-only, declarar `INDEPENDENT_SUBAGENT_NO_ISOLATION`; sin dispatcher, usar `SECUENCIAL_NO_AISLADO`.

## Comandos destructivos y Codex

<!-- CONTEXT_SOURCE: destructive_guard -->
Operaciones irreversibles como `db push/reset`, `DROP`, `TRUNCATE`, `DELETE`
sin `WHERE`, migraciones forzadas, `git push --force`, `rm -rf` amplio o
sincronización a producción requieren aprobación explícita y verificación del
guard destructivo. La presencia de este procedimiento no demuestra que un
comando concreto sea seguro.
<!-- END_CONTEXT_SOURCE: destructive_guard -->

Verificar con `.agent/scripts/guard_destructive_command.py`; la escotilla aprobada es
`APPROVED-DESTRUCTIVE`. Para activar Codex-native completar el perfil y usar
`python3 .agent/scripts/ensure_codex_integration.py --from-profile --native-only`.
La configuración vive en `.codex/config.toml`; `.codex/hooks.json` es local y
debe conservar hooks existentes. Antes de usarlos, revisar y confiar los hooks
desde `/hooks`.

## Fricción y cierre

Registrar huecos del framework como `FW-NNN`: en `template`,
`.agent/docs/field_report.md`; en `initialized`, `_feedback/field_report.md` con `PREFIJO-FW-NNN` (el `fw_prefix` del perfil).
Los errores del agente usan `/aprender-de-error` y los bugs del producto,
`/nuevo-bug`. Todo turno que devuelve el control al builder cierra como
manda la sección «Cierre de turno» de `.agent/rules/runtime_core.md`
(FW-141/142, FW-314).
<!-- FIN GESTIONADO: framework -->
