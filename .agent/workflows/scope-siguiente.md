---
description: Define el scope del siguiente módulo tras un cierre — propone ACs y tareas
---

Eres el agente de planificación del scope.

Recibirás:
- @03_project_state.json
- @04_active_scope.md
- @08_module_acceptance.md
- @07_pending_clarifications.md
- @_project/02_technical_plan/phase_0X.md (el plan de la fase activa, si existe)

## Contrato anidado

Con `INVOCADO_POR_CICLO: true`, no cerrar una respuesta user-facing ni emitir
`Siguiente:` scope derivado sin ambigüedad devuelve
`INTERNAL_RESULT: CONTINUE`; un gate duro devuelve `INTERNAL_RESULT: BLOCK`; una
elección de producto, ambigüedad o ampliación de scope devuelve
`INTERNAL_RESULT: DECISION_REQUIRED`. Solo los dos últimos devuelven el turno.

## Pasos

0.a PRODUCTO EN PRODUCCIÓN (FW-225)
   Si `lifecycle.stage == production`, no hay roadmap que encadenar: salvo un
   módulo escalado explícitamente, devolver al builder a `/ciclo` (flujo de
   mantenimiento). No proponer módulos nuevos por iniciativa propia.

0. CRITERIO DE PARADA
   Antes de proponer el siguiente módulo, verificar en @03_project_state.json:
   - ¿Hay bugs con `Severidad: Crítico` y `Estado: Abierto`? Llamar a
     `get_work_context` con `{"view":"startup"}` y usar sus items de bloqueo;
     abrir `get_full_state` solo si la vista reporta una inconsistencia explícita.
   - ¿Hay 2 o más entradas en `closure_rejections` en el sprint actual?
   - ¿El constraint de backup/recuperación en @10_arch_constraints.md existe pero no tiene evidencia de implementación?

   Si alguna condición se cumple:
   > ⚠️ Criterio de parada activo: [condición detectada].
   > Abrir un módulo nuevo ahora aumenta la deuda sin resolver.
   > Opciones: 1) Abrir módulo "Estabilización" con las tareas específicas (`add_module` con `stabilization: true`). 2) Continuar de todos modos (requiere decisión explícita del usuario — registrar vía `mutate_state` action `register_decision`; 03_arch_decisions.md es proyección, no editar a mano).
   El motor impone mecánicamente este criterio en `update_module_status` (bloquea el paso a `in_progress` ante criterio de parada activo salvo si el módulo fue declarado con `stabilization: true`); ante el rechazo, abrir un módulo de estabilización o resolver los bloqueantes con `resolve_closure_rejections` / `resolve_bug`.
   Esperar decisión del usuario antes de continuar.

1. IDENTIFICAR EL SIGUIENTE MÓDULO
   Orden de prioridad:
   1. El primer módulo de `modules.pending` sin `⚠️ BLOQUEADO` y sin una CLAR abierta cuyo `blocked_scope` sea el módulo y tenga `blocks_start: true`.
   2. Si el JSON no tiene módulos pendientes, derivarlo del plan técnico
   3. Si no hay plan técnico, preguntar al usuario antes de continuar

   Si algún módulo en `modules.pending` es fantasma (nunca arrancó, sin
   `module_spec` clasificado — típico de un módulo dividido en submódulos
   posteriores) y ya no aplica, no lo dejes ahí ni lo edites a mano: señala
   `drop_module` vía MCP como la vía legal de retiro antes de proponer el
   siguiente módulo real. Un módulo que ya arrancó y quedó obsoleto sale con
   `abandon_module` (motivo + evidencia, estado terminal `dropped`); sus bugs
   abiertos se mueven antes con `reassign_bug_module`.

   Semántica de las aclaraciones:
   - `blocks_start: true` bloquea abrir el `blocked_scope` correspondiente.
   - `blocks_implementation: true` bloquea solo la tarea o subtarea correspondiente, salvo que `blocked_scope` sea el módulo entero.
   - `blocks_closure: true` no bloquea abrir ni seguir con trabajo de implementación seguro; bloquea `/cierre-modulo` hasta que se cumpla `blocking_condition`.
   - Las entradas antiguas que solo dicen `Módulo bloqueado` son ambiguas: pedir al usuario que las reescriba con `blocked_scope`, `blocks_start`, `blocks_implementation`, `blocks_closure` y `blocking_condition` antes de usarlas para bloquear un módulo.

1.5. EXPERT GATE — auditoría proactiva (4.1.0 endurecido, FW-132)
   Leer `_project/09_domain_profile.md → Expertos activos`. Para cada experto
   cuyo sub-dominio toca el módulo identificado en el paso 1, cargar
   `.agent/skills/domain/[archivo].md → Qué audito` y ejercer su checklist
   contra el módulo: huecos de AC, riesgos y preguntas que el checklist
   revela y que el agente no habría visto sin esa lente.
   Los ACs que nazcan de un hueco detectado por el experto se marcan en el
   **cuerpo del AC**, antes de `— evidencia:`, con
   `[experto: domain/[archivo-sin-extensión]]` — nunca dentro del texto de
   evidencia ni con extensión `.md`: `validate_acceptance.py` (anti-fabricación)
   trata toda ruta citada en la evidencia como un artefacto que debe existir
   en disco, y `domain/[archivo].md` no es una ruta real desde la raíz del
   repo (el fichero vive en `.agent/skills/domain/`). Verificado contra el
   parser: la marca en el cuerpo nunca entra al texto que `evidence_problem`
   escanea.
   Esta consulta corre siempre que algún experto activo toque el módulo,
   independiente del tier — es la corrección de fondo de FW-132 (el experto
   deja de esperar a que el builder lo mencione). Lo que sí varía por tier es
   la consecuencia en el cierre: `/cierre-modulo` paso 3.9 solo bloquea el
   cierre en `tier_2` si el hueco quedó sin resolver; en tier_0/1 sigue siendo
   señal, no gate duro. Si ningún experto activo toca el módulo, seguir sin
   más ceremonia — no fabricar una auditoría donde no hay experto que la haga.

   Sellar el resultado (aplique o no algún experto) con `mutate_state` action
   `record_checkpoint`: `{"module": "[module]", "kind": "expert_audit",
   "scope_id": "[module]", "evidence": "[expertos consultados y huecos
   encontrados, o 'ningún experto activo toca el módulo']", "reference":
   "09_domain_profile.md → Expertos activos, o AC [id] con [experto:
   domain/x]"}` (FW-134: mismo contrato "declarar, no encontrar" — el gate
   mecánico de `/cierre-modulo` paso 3.9 exige este recibo en tier_2).

2. PROPONER ACs Y TAREAS
   Antes de proponer el esfuerzo del proceso, clasificar el tier de rigor con `GUARDRAILS_MASTER.md` → "Graduación de Rigor por Tier":
   - Tier 2 si toca autenticación, autorización o secretos, esquema o migraciones, contratos públicos que se consumen fuera del módulo, dinero o transacciones, un cambio destructivo o datos personales más allá de leerlos.
   - Tier 0 solo si es trabajo de usar y tirar que no se queda en el árbol del proyecto.
   - En otro caso, Tier 1.

   Proponer también la clasificación inicial de `module_specs` (ADR-013) según la naturaleza del módulo:
   - `data_model`:
     · `required` si las tareas crean, persisten, leen o transforman entidades del dominio.
     · `n/a` si el módulo es solo UI, CLI interna, documentación, configuración o scripts. `justification` obligatoria (motivo corto).
   - `api_contract`:
     · `required` si las tareas introducen o modifican endpoints HTTP/REST.
     · `n/a` si el módulo no expone HTTP. `justification` obligatoria.

   Si el módulo tiene 3 o más tareas o un paso de más de 10 min, añadir la línea
   `Estimación:` con duración estimada, subagentes previstos (0 por defecto) y
   pasos largos opcionales, según `decision_translation_core.md` →
   «Estimación de coste antes de aprobar» (`ESTIMACION_DE_COSTE`).

   Presentar al usuario:
   ```
   ## Siguiente módulo: [Nombre]

   ### Criterios de aceptación propuestos
   - [ ] [criterio funcional 1 — verificable y concreto]
   - [ ] [criterio funcional 2]
   - [ ] [criterio de seguridad si aplica]

   ### Tareas propuestas
   - [ ] [tarea 1 — granularidad: 1 mensaje = 1 tarea]
   - [ ] [tarea 2]

   ### Estimación: [duración estimada · subagentes previstos: 0 · pasos largos opcionales — o «no aplica»]
   ### Tier de rigor: [Tier 1 — Estándar / Tier 2 — Crítico] — [razón por blast radius + reversibilidad]
   ### Specs formales propuestas:
   - data_model: [required | n/a — razón si n/a]
   - api_contract: [required | n/a — razón si n/a]

   Justificación de modelo y esfuerzo: [razón por blast radius + reversibilidad]

   Modelo: [strong | standard | limited] · Esfuerzo: [🟢 bajo | 🟡 medio | 🟠 alto | 🔴 xhigh] ([tier])
   > Bloque de cierre de `decision_translation_core` (4.20.1, FW-142): dos ejes
   > separados, emitidos al devolver el turno — cambiar de modelo invalida la
   > caché de contexto; cambiar el esfuerzo la conserva o no según el host (regla 7 de `runtime_core.md`). Agnóstico del proveedor (A8): el
   > builder traduce `strong/standard/limited` a su modelo concreto. Si el
   > ejecutor es `limited`, registrar `execution_profile.capability_tier: limited`
   > en `03_project_state.json` — el cierre exigirá segundo-review en todo tier
   > (GUARDRAILS → "Graduación por capacidad del ejecutor").

   ¿Aprobado? ¿Ajustas algún criterio o clasificación de specs antes de continuar?
   ```
   En modo standalone, esperar aprobación antes de escribir. Con
   `INVOCADO_POR_CICLO: true`, continuar solo si existe un `APPROVE_RECEIPT`
   vigente y módulo, ACs, tareas y specs ya pertenecen al scope aprobado. Abrir
   un módulo todavía no aprobado, elegir entre candidatos, suplir una regla de
   negocio o ampliar scope devuelve `DECISION_REQUIRED`.

3. ESCRIBIR LOS ARTEFACTOS (solo tras la aprobación)
   Actualizar @08_module_acceptance.md con la sección del módulo nuevo:
   ```markdown
   ## Módulo N — [Nombre] (`module_id`)
   Estado: 🔄 En progreso
   ### Criterios
   - [ ] [criterio 1] — evidencia: pendiente
   UI_STATES: [required | n/a — razón]
   NFR: [required | n/a — razón]
   ```
   > Formato obligatorio desde el nacimiento (Example Tool FW-T04): el H2 debe contener
   > el `module_id` exacto como token (así `validate_acceptance.py` encuentra la
   > sección) y cada AC lleva ` — evidencia: ` desde ya — al marcar `[x]`,
   > `/cierre-modulo` exige evidencia real de ≥20 chars tras ese literal.
   > Escribirlo bien aquí evita reescribir todas las ACs durante el cierre.
   > `UI_STATES` y `NFR` se clasifican aquí: la vista `implementation` los exige.

   Sustituir @04_active_scope.md por el scope del módulo nuevo, empezando por:
   ```markdown
   # Módulo actual: module_id
   [Nombre]
   ```
   Solo el id tras «Módulo actual:»; el nombre va en la línea siguiente.

   Añadir la entrada del módulo a @11_file_dependencies.md (`/cambio-seguro` la amplía si toca código existente):
   ```markdown
   ### module_id — [Nombre]
   preserva: n/a — módulo nuevo: no modifica comportamiento existente: [razón]
   ```
   Si modifica código existente, `preserva:` lista `- [comportamiento] — evidencia: [ruta, test o AC-n]`.

   Actualizar el estado por MCP (en este orden):
   - `mutate_state` action `update_module_status` → {"id": "[module]", "status": "in_progress"}
   - Si este módulo abre una fase posterior: `mutate_state` action `set_phase` → {"current_phase": "phase_0X", "current_step": "inicio"}
   - `mutate_state` action `add_work_tier` → {"id": "[module]", "tier": "tier_1|tier_2", "rationale": "[razón]", "classified_by": "scope-siguiente"}
   - `mutate_state` action `classify_module_spec` → {"id": "[module]", "data_model": "required|n/a", "api_contract": "required|n/a", "integration_real": "required|n/a", "justification": "[obligatoria si alguno es n/a]", "classified_by": "scope-siguiente"}
     **FW-148 — `integration_real`:** clasificar también si el módulo necesita
     al menos un test SIN mocks contra la dependencia viva (base de datos en
     fichero, LLM real, renderer real, HTTP real). `required` si el módulo
     toca una costura con el mundo; `n/a` con justificación si es lógica pura.
     Una suite entera sobre mocks puede estar verde mientras el producto no
     arranca (Example Service: 606 tests verdes, 4 flujos rotos al ejecutar).

   (Transición autorizada: not_started → in_progress)
   - Tras escribir todos los artefactos aprobados, `mutate_state` action
     `record_approval_receipt` → {"module":"[module]", "approved_by":"builder",
     "evidence":"[aprobación explícita literal]", "risk_summary":"[impacto y
     rollback]", "adr_refs":["ADR-..."]}. El motor calcula el fingerprint; no
     aceptar un receipt narrativo. En tier_2, `adr_refs` exige una ADR cuyo
     `module_id` incluya este módulo: una ADR de otro módulo se rechaza aunque
     cubra la decisión (registrar la ADR del módulo antes de aprobar). Si el
     plan prevé marcar la PRD, añadir ADRs o tocar otra sección del modelo, de
     los contratos o de la API, decláralo en `covers_changes` (`ciclo.md`).

4. FIJAR LOS PUNTEROS DE SESIÓN (core.md es una proyección de solo lectura: NO editarla a mano)
   Llamar a `mutate_state` con la acción `set_session_pointer` y:
   {
     "active_module_description": "[breve descripción del módulo]",
     "next_action": "[primera tarea [ ] del scope]",
     "next_command": "/ciclo",
     "next_module": "[siguiente en modules.pending o 'pendiente']"
   }
   El render los reproduce en core.md.

5. SALIDA
   Con `INVOCADO_POR_CICLO: true`, devolver `INTERNAL_RESULT: CONTINUE` al
   driver y no ejecutar las plantillas standalone siguientes. En standalone,
   cerrar con:
   Siguiente: /ciclo Construir el módulo con todos los gates (el agente enriquecerá la HU primero si falta detalle).
   Modelo: <strong|standard|limited> · Esfuerzo: <🟢 bajo|🟡 medio|🟠 alto|🔴 xhigh> (<tier>)

   Si el scope ya quedó suficientemente detallado, usar:

   Siguiente: /ciclo Construir el módulo con todos los gates.
   Modelo: <strong|standard|limited> · Esfuerzo: <🟢 bajo|🟡 medio|🟠 alto|🔴 xhigh> (<tier>)

La interfaz, los logs y los textos para el usuario van en español.
