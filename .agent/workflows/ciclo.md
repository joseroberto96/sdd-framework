---
description: Driver autónomo — por defecto ejecuta UN módulo de scope a cierre respetando todos los gates; con "/ciclo fase" encadena la fase completa y con "--paralelo" (opt-in) despacha módulos independientes a ejecutores en worktrees. Se para ante STOP GATE, gates bloqueantes o decisiones de builder.
---

# /ciclo [<módulo> | fase [--paralelo]]

> **Paso −1 — ciclo de vida (FW-225). Léelo antes que nada.** Si
> `_project/core.md` muestra `Ciclo de vida: production` (o
> `03_project_state.json → lifecycle.stage == "production"`) y NO hay un módulo
> escalado en `in_progress`, **deja de leer este archivo** y sigue
> `.agent/workflows/mantenimiento.md`. Con un módulo escalado en curso, continúa
> aquí con ese módulo.

Eres el agente driver de este proyecto. El driver por defecto es **acotado**:
ejecuta **un solo módulo** de scope a cierre, parándose en bifurcaciones reales.
Cada invocación cubre un módulo y termina con un digest en lenguaje de builder.
**Un módulo por invocación**: el driver NO auto-continúa al siguiente módulo
sin acción explícita del builder.

Extensiones opt-in: `/ciclo fase` (fase actual completa) y `/ciclo fase --paralelo`
(ejecutores en worktrees aislados). Los gates NO cambian en ningún modo.

## Principio (Regla 5 — anti-regresión de gates)

El driver confía en los gates calibrados. Ante un BLOCK duro de cualquier gate
(cierre, transición, aceptación), el driver para y reporta; nunca lo bypasea
con ediciones manuales del estado ni evadiendo hooks (no bypasear los gates).
La deuda menor usa las válvulas de `/cierre-modulo` (`defer_bug_to_tech_debt`).

## Contrato de ejecución anidada — propiedad del turno

Desde que el builder invoca `/ciclo`, el driver conserva la **propiedad del turno**
hasta cerrar el módulo o encontrar una parada real. Cada workflow interno se
invoca con `INVOCADO_POR_CICLO: true` y devuelve un recibo al driver:

- `INTERNAL_RESULT: CONTINUE` — el paso terminó; el driver continúa en el mismo
  turno. Este resultado **no devuelve el turno al builder** ni emite continuaciones.
- `INTERNAL_RESULT: BLOCK` — un gate duro o fallo ejecutable impide avanzar; el
  driver registra `ciclo_paused` y devuelve el turno.
- `INTERNAL_RESULT: DECISION_REQUIRED` — se requiere decisión real del builder;
  el driver aplica la plantilla de decisión, registra la pausa y devuelve el turno.

Un checkpoint informativo, resultado de tests o fin de sub-workflow nunca liberan
el turno. Siguen siendo paradas reales: STOP GATE, cambio destructivo, ampliación
de scope, dependencia nueva no declarada, migración irreversible, fork A/B,
credenciales/input externo y cualquier gate de cierre que falle. El driver NO
intenta bypasearlos.

### Recibo de aprobación del plan

Antes de CODE, ejecutar `python3 .agent/scripts/workflow_receipts.py approval <módulo>`.
El recibo vive en `03_project_state.json.workflow_receipts.approvals` y solo se
crea vía `mutate_state record_approval_receipt` tras aprobación explícita;
nunca se reconstruye desde memoria conversacional. El motor valida scope+ACs, tier,
ADRs, fase y dependencias. Si el plan prevé dependencias nuevas, deben declararse
en `covers_dependencies` para que su registro posterior no invalide el recibo.
Lo mismo con `covers_changes` para lo que el plan prevé tocar fuera de su scope y
sus ACs: ADRs aditivas del módulo (`decisions:aditivas`), specs que se clasifican
después (`module_spec:clasificar`), una sección de la PRD, del modelo o de los
contratos (`prd:## MVP`) o una ruta de la API (`api_contract:/ruta`). Lo declarado
no caduca el recibo y el cierre avisa de lo que cambió; en la PRD solo se toleran
casillas y anotaciones `— evidencia: …`. Tu scope y tus ACs no se declaran.
Si falta o está stale, devolver `INTERNAL_RESULT: DECISION_REQUIRED`.

### Estimación de coste (5.2.3, `ESTIMACION_DE_COSTE`)

Si el plan tiene 3 o más tareas, o algún paso de más de 10 min (benchmark, suite
completa repetida, E2E), el recibo de aprobación se pide **después** de presentar
duración estimada, subagentes previstos (0 por defecto) y los pasos largos
marcados como opcionales (`decision_translation_core.md` → «Estimación de coste
antes de aprobar»). Con recibo vigente, es una línea informativa.

## Regla transversal — convocar al builder SIEMPRE con la plantilla

Antes de CUALQUIER pausa que pida una decisión al builder (STOP GATE, diff, blast
radius, smoke manual), aplicar la plantilla de `decision_translation_core.md`
(qué significa para ti → qué recomiendo → alternativas con trade-off).

## Pasos

### 0. STOP GATE — criterio de parada (Regla 7) **ANTES** de avanzar

**Prompt caducado (FW-318, aviso que no bloquea).** Solo en el primer `/ciclo` de la sesión: pasar la instrucción recibida por la entrada estándar a `python3 .agent/scripts/stale_prompt.py` (heredoc con delimitador entre comillas; nunca interpolarla en la línea de comando). Si imprime una línea, mostrársela tal cual al builder (propone `/reconciliar`) y **seguir** con este paso: es un aviso, no una parada. Si no imprime nada o falla, continuar sin comentarlo.

Evaluar el estado canónico:
- ¿Hay bugs con `Severidad: Crítico` y `Estado: Abierto`?
- ¿Hay 2 o más entradas en `closure_rejections` para el sprint actual?

**Excepción — módulo de Estabilización (FW-349).** Solo si la instrucción pide abrir o continuar el módulo de Estabilización, o el módulo activo ya lo es, el STOP GATE no para: el trabajo va en un módulo declarado con `stabilization: true` en `module_specs`, que el motor permite arrancar. Si aún no existe, crearlo con `add_module` (`stabilization: true`) con un id que nombre los `BUG-NNN` que arregla (`estabilizacion-BUG-NNN`: su vista `implementation` no los cuenta, FW-359), y continuar al paso 1 con ese módulo. Para cualquier otro módulo o instrucción el STOP GATE se aplica entero.

Consumo del hook de continuidad: si el hook de sesión ya entregó la vista de
arranque vigente (`completitud`, `vigencia` con fingerprint coincidente sin
mutaciones intermedias) con los conteos requeridos, reutilizarla sin emitir
consulta redundante. Si no hay entrega previa o expiró, llamar a `get_work_context`
con `{"view":"startup"}`. Ante estado corrupto o vista ausente, parar con
`INTERNAL_RESULT: BLOCK`. `get_full_state` queda para diagnóstico explícito.

Si alguna condición se cumple:
1. Llamar a `mutate_state` action `ciclo_paused` con `{"reason": "STOP GATE: [condición detectada]"}`.
2. Resumir al builder en `builder_language_core` qué se detectó y por qué se para.
3. **NO avanzar**. Terminar con el cierre, en texto plano:
   Siguiente: /ciclo <instrucción: abrir un módulo de Estabilización y corregir BUG-id o los rechazos de cierre; en un BLOCK, corregir lo que el gate señaló>
   Modelo: <strong|standard|limited> · Esfuerzo: <🟢 bajo|🟡 medio|🟠 alto|🔴 xhigh> (<tier>)

El motor impone mecánicamente este criterio en `update_module_status` (rechaza arrancar módulos no-estabilización si hay bugs críticos o $\ge 2$ rechazos de cierre); ante el rechazo, abrir un módulo de estabilización (`add_module` con `stabilization: true`) o resolver los bloqueantes (`resolve_bug` / `resolve_closure_rejections`).

Si el STOP GATE está limpio, continuar al paso 1.

### 1. Identificar el módulo a ejecutar

Llamar a `mutate_state` action `ciclo_started` con `{"module": "<id>"}`.
El módulo es: el argumento `<módulo>` si se dio; o el primer `in_progress` no
bloqueado; o el primer `pending` sin CLAR bloqueante de apertura.

Si no hay módulo identificable, ejecutar `/scope-siguiente` como maquinaria
interna. Si requiere decisión del builder, pausar con el checkpoint; si no hay
pendientes, parar con `ciclo_paused {"reason": "no hay módulo en in_progress/pending"}`.

### 1.5 Chequeo de discrepancia de tier (4.20.1, FW-142)

Comparar el tier real del módulo (`work_tiers[módulo]`) contra el anticipado al
cerrar el turno anterior (`discrepancia de tier`):
- Coinciden → continuar sin ruido.
- Difieren hacia arriba (`tier_1` a `tier_2` o `requires_second_review`) →
  pausar antes del trabajo pesado con `ciclo_paused {"reason": "tier real superior al anticipado"}`,
  informar (`El módulo resultó <tier> — recomiendo Modelo: <nivel> · Esfuerzo: <nivel>`)
  y devolver el turno.
- Difieren hacia abajo → no pausar; registrar en el digest de cierre.

### 1.6 Contexto de implementación por etapa — antes del trabajo pesado

Llamar a `get_work_context` con `{"view":"implementation","module_id":"<id>"}`.
Abrir procedimiento, scope, ACs, specs y `PRESERVAR` solo cuando la vista los
entregue o señale como requeridos. `complete=true` y `required_context_complete=true`
deben ser verdaderos antes de CODE. Si el host compacta, activar `/reconciliar` y
reconstruir desde disco.

### 2. Enriquecer (solo si falta detalle técnico)

Si la HU del módulo no tiene `module_specs[data_model]` y `module_specs[api_contract]`
clasificados, invocar `/enriquecer-ticket`. Si ya están clasificados, continuar.

### 2.5 Bootstrap autónomo del entorno declarado

Si faltan herramientas o librerías que ya constan en los archivos de dependencias
del proyecto, el driver puede crear o reusar un entorno local dentro del workspace
(`.venv`) e instalar esas **dependencias ya declaradas** solo tras pasar
`python3 .agent/scripts/workflow_receipts.py dependency '<spec fijado>'`:
la dependencia debe estar en `03_project_state.json.dependencies` Y en el
manifest/lock con la misma versión exacta. No modificar el manifest ni lockfile,
ni instalar globalmente. Este bootstrap devuelve `INTERNAL_RESULT: CONTINUE`.
Una dependencia nueva no declarada o una instalación global devuelven
`DECISION_REQUIRED` o `BLOCK`.

### 3. Implementar (TDD o equivalente)

Invocar `/tdd` si el módulo tiene lógica que probar, o el paso equivalente delegando
en un workflow existente; nunca reimplementar el ciclo inline.

Aplicar el Checkpoint por blast radius de `.agent/skills/decision_translation_core.md`:
- `tier_0`: decidir y ejecutar, no interrumpir.
- `tier_1`: decidir y registrar en digest al hito.
- `tier_2`: con `APPROVE_RECEIPT` vigente, emitir un **checkpoint informativo**
  antes de CODE con impacto, rollback y evidencia de revisión independiente requerida,
  y **continúa en el mismo turno**. Sin recibo vigente, devolver `DECISION_REQUIRED`.
  El checkpoint no autoriza cambio destructivo, ampliación de scope, dependencia
  nueva no declarada ni migración irreversible (devuelven `DECISION_REQUIRED`).

Si el módulo presenta un fork A/B que cambia uso o estética del producto, pausar
y convocar al builder (`decision_translation_core.md`).

### 4. Cerrar

Invocar `/cierre-modulo`. El driver delega el gate completo (`validate_closure.py`,
`validate_acceptance.py`, `pre_commit.sh`).
Si el cierre devuelve BLOCK:
1. Llamar a `mutate_state` action `ciclo_paused` con `{"reason": "cierre-modulo BLOCK: <resumen>"}`.
2. Auto-clasificar la pausa:
   - Gate correcto → pausa legítima, reportar.
   - Falso positivo → registrar fricción en `_feedback/field_report.md` (`PREFIJO-FW-NNN` con el `fw_prefix` del perfil; `FW-NNN` solo en la plantilla) con evidencia.
     Registrar la fricción NO autoriza a bypasear el gate: el driver para igual.
3. Reportar al builder en `builder_language_core`. NO bypasear el gate.

### 5. Digest de cierre (acotado) — un módulo por invocación

Tras cierre-modulo OK, **el driver PARA**. Acotado: un módulo por invocación.
NO auto-continúa. Cada invocación cubre exactamente un módulo.
Llamar a `mutate_state` action `ciclo_finished` con `{"module": "<id>", "result": "ok"}`.
Emitir digest final aplicando `builder_language_core` y `decision_translation_core`:
- Qué se hizo y qué significa para el producto.
- Las líneas resumen `calidad:` del cierre, si las hay (aviso; nunca bloquea).
- Lecciones al digest C (enseñanza).
- Próximo paso opcional si el builder desea continuar con `/ciclo <siguiente>`.

## Mensaje al builder (al cerrar)

```
Ciclo cerrado: <módulo>. Result: ok.
[ digest en builder_language_core: qué se hizo + qué significa para ti ]
```

Siguiente: /ciclo <instrucción concreta del siguiente módulo>
Modelo: <strong|standard|limited> · Esfuerzo: <🟢 bajo|🟡 medio|🟠 alto|🔴 xhigh> (<tier>)

## Modo fase — `/ciclo fase` (4.2.0, FW-073)

El builder solicita explícitamente ejecutar la fase actual completa. El driver
repite los pasos 0-4 módulo a módulo (STOP GATE antes de cada módulo) en el orden
del plan técnico, deteniéndose ante STOP GATE, BLOCK o decisiones reales de builder.
Telemetría: `ciclo_started` con `{"module": "<id>", "mode": "fase"}` y `ciclo_finished` con `{"module": "<id>", "result": "ok", "mode": "fase"}`. En el modo paralelo, `mode` es `paralelo`.
Aplica `decision_translation_core` para agrupaciones de decisiones.

## Modo paralelo — `/ciclo fase --paralelo` (4.2.0, opt-in)

Requiere flag explícito `--paralelo` Y checkpoint de coste aprobado por el builder
(`decision_translation_core`).

P1. Checkpoint de coste: presentar opciones (paralelo / secuencial).
P2. Detectar módulos independientes: según `module_specs` y dependencias (sin intersección en `escribe`/`lee`).
P3. Despachar ejecutores en worktrees:
> **Tamaño del encargo (FW-195).** Antes de despachar, partir el trabajo en
> encargos pequeños y autosuficientes; no asignar una auditoría integral a un
> único subagente. En Codex no se delega por defecto: cuándo se hace y el tope
> de subagentes los fija el adaptador activo (`CODEX_SMALL_TASK_DELEGATION`).
> **Espera (`SUBAGENT_WAIT_LIFECYCLE`).** Ver `.agent/docs/extended_protocol.md`.
- Crear un worktree por módulo desde el sha actual con `git worktree add --detach <ruta> <sha>`.
- Enriquecer cada módulo en el árbol principal antes de despacharlo: el ejecutor no escribe `_project/`.
- Despachar ejecutor con `.agent/subagents/executor.md` (máximo 3 simultáneos).
- Los ejecutores heredan `execution_profile.capability_tier` (en `limited`, second review obligatoria).
- Si el entorno no soporta ejecutores aislados, declarar `SECUENCIAL_NO_AISLADO`.
P4. Integrar y cerrar EN SERIE:
- Integrar uno a uno, sin merge de rama: aplicar el `.patch` del ejecutor solo en rutas de producto y sus tests, con un inventario explícito de ficheros (nunca `_project/` ni `.agent/`). El informe del ejecutor no es evidencia: cada cambio o corrección que declare se comprueba en el diff o en el árbol antes de integrar (FW-356). La suite completa la corre solo el driver, en serie (FW-358): `bash .agent/scripts/pre_commit.sh suite` sobre el árbol integrado; un commit cuyo árbol preparado es ese mismo árbol reutiliza el informe verde (TL-FW-298). Un commit por módulo.
- Mutaciones registradas exclusivamente por el driver vía `state_write_lock`.
- `/cierre-modulo` por módulo: los gates de cierre no se paralelizan.
- Al terminar la tanda: ejecutar `/reconciliar` y digest agrupado.

## Ruteo económico por tier (4.3.0, FW-077)

Sub-tareas mecánicas y tier_0 pueden despacharse a ejecutores con modelos baratos,
heredando `capability_tier` y el contrato del ejecutor. No delegar decisiones de
diseño ni cambios tier_2.

## Anti-patrones (NO)

- NO bypasear un gate con edición manual del estado ni eludiendo pre-commit hooks.
- NO reimplementar lógica de gates (`validate_closure.py`, `validate_acceptance.py`).
- NO auto-continuar al siguiente módulo sin opt-in explícito (`/ciclo fase`).
- NO activar paralelismo sin `--paralelo` ni paralelizar cierres o mutaciones de estado.
- NO saltarse el STOP GATE ante bugs críticos o reyecciones.
- NO decidir forks de visión sin convocar al builder (`decision_translation_core`).
- NO mutar estado directamente sin pasar por MCP.

## Mensaje al builder (al pausar)

```
Ciclo pausado: <módulo>. Razón: <razón del STOP GATE o BLOCK>.
[ digest en builder_language_core: qué se necesita del builder para que el driver siga ]
```

Si la pausa es por STOP GATE o BLOCK:

Siguiente: /ciclo <instrucción: abrir un módulo de Estabilización y corregir BUG-id o los rechazos de cierre; en un BLOCK, corregir lo que el gate señaló>
Modelo: <strong|standard|limited> · Esfuerzo: <🟢 bajo|🟡 medio|🟠 alto|🔴 xhigh> (<tier>)

Si la pausa es por una decisión del builder (`DECISION_REQUIRED`, fork A/B o checkpoint), el cierre es una sola línea, sin comando ni línea de modelo, y la pregunta va antes:

Siguiente: decisión — responde A (recomendada) o B en este chat
