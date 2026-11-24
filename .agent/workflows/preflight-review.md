---
description: Revisión read-only previa a CODE para planes, cambios y refactors; invoca subagente si existe y degrada explícitamente si no
---

# Revisión preflight

Workflow interno. Usar antes de CODE cuando un plan, ticket enriquecido, TDD,
refactor o cambio sobre codigo existente pueda omitir gates del framework.

## Principio

<!-- CONTEXT_SOURCE: independent_review -->
La revisión independiente debe identificar riesgo, gates faltantes y alcance
antes de CODE. En `tier_2`, capability `limited` o `requires_second_review` se
exige identidad de reviewer distinta del implementador, resultado aprobado,
evidencia y hash vigente del diff. Un timeout, ausencia de dispatcher o review
no ejecutado no equivale a PASS.
<!-- END_CONTEXT_SOURCE: independent_review -->

`AUTO_SUGGEST`, no `AUTO_MUTATE`: esta capa detecta riesgos y recomienda el
workflow correcto. No escribe archivos, no instala, no ejecuta fixers, no muta
`_project/` y no llama `mutate_state`.

Contrato común enlazado: `.agent/docs/extended_protocol.md` →
`SUBAGENT_WAIT_LIFECYCLE`. Ese contrato define `WAIT_TIMEOUT_NONTERMINAL`; un
timeout no es terminal y el padre sigue consultando. No se ejecuta `interrupt`
ni `close_agent` automáticamente; el resultado solo se emite cuando el
adaptador declara estado terminal.

Al reportar al usuario, aplicar `.agent/skills/builder_language_core.md`: traducir
Risk Surface, gates y hallazgos a que significa para ti antes del detalle tecnico.

## Contrato anidado

Con `INVOCADO_POR_CICLO: true`, un review aprobado devuelve
`INTERNAL_RESULT: CONTINUE` sin cierre user-facing; un hallazgo bloqueante
devuelve `INTERNAL_RESULT: BLOCK`; una decisión de builder devuelve
`INTERNAL_RESULT: DECISION_REQUIRED`. Un comentario informativo no devuelve el
turno ni emite `Siguiente:`.

## Activación graduada

- Low / Tier 0: self-check breve; no bloquear salvo gate duro.
- Medium / Tier 1 simple: revisar Risk Surface y siguiente workflow.
- Medium multiarchivo o codigo existente: ejecutar plan review + validar
  inventario `PRESERVAR`.
- High / Tier 2: revision obligatoria y bloqueante antes de CODE.
- ANTES de degradar, VERIFICAR de verdad si el entorno tiene subagentes
  read-only o un revisor independiente bajo contrato de solo lectura (FW-078 —
  en dogfooding real el agente degradó 3 veces teniendo los subagentes
  disponibles): en Claude Code, el Agent tool despacha
  `.agent/subagents/preflight-reviewer.md` como contrato; en OpenCode, los
  agentes generados en `.opencode/agents/` (verificable con
  `opencode agent list`); en Codex, usar el despacho de subagentes cuando esté
  expuesto y exigir el mismo contrato read-only. Si el arnés no lo enforza
  mecánicamente, declarar `INDEPENDENT_SUBAGENT_NO_ISOLATION`, no fingir aislamiento.
  Un plan revisado por su propio autor es medio doble check.

> **Tamaño del encargo (FW-195).** Antes de despachar, partir el trabajo en
> encargos pequeños y autosuficientes: un objetivo verificable, archivos
> concretos, entradas mínimas y salida breve; solo se serializan dependencias y
> escrituras solapadas. No se transfiere el historial completo ni se asigna una
> auditoría integral a un único subagente: un encargo que agota su ventana
> entrega hallazgos parciales, y una revisión parcial no satisface una revisión
> Tier 2. La revisión independiente del preflight sí se delega; en Codex lo
> demás no se delega por defecto y el adaptador activo fija cuándo y el tope —
> `.agent/adapters/codex.md` → `CODEX_SMALL_TASK_DELEGATION`.
- Solo si la verificación confirma que no existe dispatcher: declarar
  `SECUENCIAL_NO_AISLADO` y ejecutar la misma revision secuencialmente.

## Lectura mínima

Leer solo lo necesario:

- `get_work_context` con `{"view":"implementation","module_id":"<módulo>"}`;
  si aún no hay módulo, usar `{"view":"startup"}`. El estado completo queda
  reservado para una inconsistencia concreta o diagnóstico explícito.
- `_project/04_active_scope.md`
- `_project/08_module_acceptance.md`
- Plan, ticket, propuesta o diff objetivo.
- Specs formales solo si el Risk Surface las activa:
  `_project/02_technical_plan/data_model.md`,
  `_project/02_technical_plan/api_contract.yaml`,
  `_project/03_contracts.md`.
- `_project/10_arch_constraints.md` y `_project/11_file_dependencies.md` si el
  cambio toca arquitectura, dependencias, datos, seguridad o reportes.

## Pasos

1. RISK SURFACE
   Revisar si el plan toca auth, permisos, PII, dinero, persistencia,
   migraciones, contratos publicos, async, UI, dependencias nuevas, red o
   operaciones externas.

2. ROUTER DE WORKFLOWS
   Recomendar el gate que falta:
   - codigo existente o refactor -> `.agent/workflows/cambio-seguro.md`
   - ambigüedad de negocio -> `/clarificar` o `/entrevistar`
   - HU sin detalle tecnico -> `/enriquecer-ticket`
   - datos o HTTP -> specs formales antes de CODE
   - decision visible para el builder -> `decision_translation_core`
   - bug no reproducido -> `/debug`
   - tier_1/tier_2 con lógica que probar -> `/tdd`

3. REVISIÓN DEL PLAN
   Si el plan tiene 3 o más tareas o un paso de más de 10 min, exigir que
   declare duración estimada, subagentes previstos (0 por defecto) y pasos
   largos marcados como opcionales (`ESTIMACION_DE_COSTE`, ver
   `decision_translation_core.md` → «Estimación de coste antes de aprobar»);
   su ausencia es un hallazgo del plan, no un bloqueo duro.

   Si hay subagentes read-only verificables, ejecutar `preflight-reviewer`
   siguiendo `.agent/subagents/preflight-reviewer.md` y declarar:
   `Modo: READ_ONLY_SUBAGENTS` o `Modo: INDEPENDENT_SUBAGENT_NO_ISOLATION`.

   Si hay dispatcher pero no aislamiento read-only mecánico, usar un subagente
   distinto bajo contrato no-write y declarar
   `Modo: INDEPENDENT_SUBAGENT_NO_ISOLATION`. Solo si no hay dispatcher,
   declarar `Modo: SECUENCIAL_NO_AISLADO` y ejecutar la checklist en el agente
   principal. Mientras la revisión siga activa, el árbol queda congelado; un
   timeout no se interpreta como pass/fail ni como cancelación.

4. BLOQUEO
   Bloquear antes de CODE si:
   - falta un gate duro detectado por Risk Surface;
   - el plan sale del scope activo;
   - toca codigo existente sin `PRESERVAR`;
   - requiere specs formales y no estan propuestas o aprobadas;
   - introduce dependencia nueva sin aprobacion;
   - es Tier 2 y no hay revision independiente/degradacion declarada.

4.b TRATAR EL FAIL POR CLASE (FW-164)
   Un `PLAN_REVIEW: fail` señala una clase de problema, no una línea. Antes de
   volver a pedir revisión: nombrar la clase, corregir todas sus ocurrencias en
   el plan/diff de una vez, y re-revisar sobre el resultado. Parchear el
   ejemplo citado devuelve `fail` en la vuelta siguiente.

4.c ETIQUETADO DE VERIFICACIÓN (TL FW-232 / FW-245)
   Toda afirmación sobre código, firmas o viabilidad de un arnés de prueba va
   etiquetada `VERIFICADO (archivo:línea | comando)` o `NO_VERIFICADO`, y una
   afirmación `NO_VERIFICADO` **no puede sostener un `pass`**.

   El flujo de espera consume `.agent/docs/extended_protocol.md` →
   `SUBAGENT_WAIT_LIFECYCLE`: `WAIT_TIMEOUT_NONTERMINAL` mantiene el árbol
   congelado y no produce veredicto parcial por silencio o compactación. Una
   revisión interrumpida por tope de espera queda como `NEEDS_ATTENTION`
   (nunca `PASS`/`FAIL`) y no satisface una revisión Tier 2: se repite.

   Mientras una revisión independiente corre (aquí o en el paso 6 de
   `/cierre-modulo`), el árbol está congelado: no se edita código, specs ni
   evidencia hasta que el recibo esté registrado (FW-163).

5. SALIDA
   Con `INVOCADO_POR_CICLO: true`, devolver solo el recibo interno correspondiente
   y no ejecutar la plantilla standalone: pass → `INTERNAL_RESULT: CONTINUE`;
   gate duro → `INTERNAL_RESULT: BLOCK`; decisión real →
   `INTERNAL_RESULT: DECISION_REQUIRED`. En standalone, emitir:
   ```markdown
   Qué significa para ti:
   [en palabras cotidianas: si el plan puede seguir o qué riesgo hay que resolver antes]
   Qué recomiendo:
   [siguiente acción práctica y por qué]
   PLAN_REVIEW: pass | fail
   Modo: READ_ONLY_SUBAGENTS | INDEPENDENT_SUBAGENT_NO_ISOLATION | SECUENCIAL_NO_AISLADO
   Detalle técnico:
   Superficie revisada:
   - [senal] — [evidencia]
   Revisiones requeridas:
   - [workflow/gate] — [razon]
   Hallazgos:
   - [severity] [archivo/seccion] [descripcion] [VERIFICADO (archivo:línea | comando) | NO_VERIFICADO] [accion requerida]
   ```

   Siguiente: /ciclo <instrucción concreta y autosuficiente>
   Modelo: <strong|standard|limited> · Esfuerzo: <🟢 bajo|🟡 medio|🟠 alto|🔴 xhigh> (<tier>)

La interfaz, los logs y los textos para el usuario van en español.

Standalone, si PLAN_REVIEW es pass, emitir en texto plano, sin bloque de código:

Siguiente: /ciclo Implementar el plan revisado y llevar el módulo a cierre.
Modelo: <strong|standard|limited> · Esfuerzo: <🟢 bajo|🟡 medio|🟠 alto|🔴 xhigh> (<tier>)
