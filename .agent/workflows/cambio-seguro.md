---
description: Gate anti-regresión para implementar, corregir o refactorizar sin perder funcionalidad existente
---

# Cambio Seguro — Workflow Anti-Regresión

Usar antes de modificar código existente, durante refactors, fixes y cambios de UI/API.

## Objetivo

Evitar que una mejora, simplificación o refactor quite funciones, rutas, comandos, estados de UI, validaciones, permisos o comportamientos ya implementados sin permiso explícito.

**Alcance del inventario PRESERVAR: por invocación, no continuo.** Cada
invocación de este workflow produce un inventario válido para esa incursión
en el código. Si la implementación sigue tocando código existente en áreas
nuevas después, este workflow no se relanza automáticamente por cada una — la
cobertura continua real la da la re-ejecución completa de esta misma sección
de verificación sobre el diff final en `.agent/workflows/cierre-modulo.md`
paso 3.5 (ANTI-REGRESSION GATE), al cerrar el módulo.

## Contrato anidado

Con `INVOCADO_POR_CICLO: true`, un inventario y cambio
ADITIVO/COMPATIBLE dentro del scope devuelve `INTERNAL_RESULT: CONTINUE` sin
respuesta final; un gate duro devuelve `INTERNAL_RESULT: BLOCK`; un cambio
DESTRUCTIVO, ampliación real de scope o decisión de builder devuelve
`INTERNAL_RESULT: DECISION_REQUIRED`. Solo los dos últimos devuelven el turno.

## Pasos

0. CONTEXTO MÍNIMO
   Primero llamar a `get_work_context` con `{"view":"implementation","module_id":"<módulo>"}` y seguir sus cursores. La primera consulta puede devolver `NEEDS_INPUT` por preparativos aún no producidos; eso enruta a clasificación, enriquecimiento, inventario o aprobación y no autoriza CODE. Reconsultar después de esos preparativos y exigir `complete=true` y `required_context_complete=true` inmediatamente antes de editar.
   La vista entrega estado, tier, specs, stack, dependencias, restricciones y las referencias acotadas de scope/ACs.
   Leer directamente `_project/03_project_state.json` solo si la consulta reporta
   inconsistencia o si un diagnóstico explícito necesita un campo que la vista no
   entrega; no usarlo como payload normal.
   Leer después, de forma selectiva:
   - `_project/04_active_scope.md` y `_project/08_module_acceptance.md` solo en las secciones señaladas por la vista
   - `_project/03_contracts.md` solo si el módulo tiene interfaz pública no-HTTP
   - `_project/02_technical_plan/data_model.md` solo si `module_specs[módulo].data_model ∈ {required, done}` — usar skill `spec_contracts` para lectura selectiva
   - `_project/02_technical_plan/api_contract.yaml` solo si `module_specs[módulo].api_contract ∈ {required, done}` — lectura selectiva por endpoints del módulo
   - `_project/11_file_dependencies.md` si existe entrada para el módulo

   Si el módulo está `blocked`, detenerse — **con una excepción** (Example CLI
   FW-004): si el módulo está `blocked` por un bug registrado y el cambio que se
   evalúa ES el fix de ese `BUG-XXX` dentro de `/debug`, continuar. Sin esta
   excepción el único workflow que puede desbloquear el módulo quedaría prohibido
   de editar (deadlock por diseño). La excepción exige nombrar el BUG-XXX en el
   inventario del paso 1 y limita el alcance del cambio al fix de ese bug.
   El fix se registra como unidad `bug:BUG-XXX` (tier del paso 0.25 con ese
   id) y se consulta la vista `implementation` de esa unidad, que no cuenta
   su propio bug (FW-359); la del módulo dueño sigue en BLOCK.

   Si `module_specs[módulo]` no existe → emitir advertencia y derivar a `/scope-siguiente` o `/enriquecer-ticket` para clasificar antes de continuar. No bloquear si el cambio es claramente local y `tier_0`.

   <!-- CONTEXT_SOURCE: preserve_inventory -->
   PRESERVAR es un inventario de comportamientos existentes, no una lectura
   del procedimiento: listar API, comandos, rutas, estados, validaciones,
   permisos, persistencia y tests con evidencia concreta antes de editar.
   El inventario no se considera satisfecho por la mera presencia de este
   workflow; la evidencia del cambio se produce durante TDD y cierre.
   La evidencia documental persistente se registra en la entrada del módulo de
   `_project/11_file_dependencies.md` bajo `preserva:`. Cada comportamiento
   debe usar el formato `- [comportamiento concreto] — evidencia:
   [archivo|test|AC|contrato]`; `# pendiente`, ejemplos y referencias genéricas
   no satisfacen el inventario. Si la incursión no modifica comportamiento
   existente, registrar `preserva: n/a — esta incursión no modifica
   comportamiento existente: [justificación concreta]`.
   <!-- END_CONTEXT_SOURCE: preserve_inventory -->

   <!-- CONTEXT_SOURCE: security_surface -->
   Revisar explícitamente auth/autorización, secretos, PII, persistencia,
   contratos públicos, operaciones externas y comandos destructivos. Mantener
   las superficies preservadas; cualquier cambio destructivo o ampliación de
   scope requiere decisión explícita y sus gates de tier correspondientes.
   <!-- END_CONTEXT_SOURCE: security_surface -->

0.25. CLASIFICADOR DE TIER
   Leer `GUARDRAILS_MASTER.md` → "Graduación de Rigor por Tier".

   Antes de clasificar ADITIVO/COMPATIBLE/DESTRUCTIVO, declarar el tier por blast radius + reversibilidad:
   - `tier_2` si toca auth/autorización/secretos, esquema/migraciones, contrato público externo, dinero/transacciones, PII más allá de leerla, o si podría ser `DESTRUCTIVO`.
   - `tier_0` solo si vive en `/_scratch/`, es de un solo uso, no toca datos reales ni contratos y se borra/revierte eliminándolo.
   - `tier_1` en cualquier otro caso.

   Si el cambio entra al proyecto: si el módulo ya tiene clasificado su tier en `work_tiers` y el nivel (`tier`) coincide, no re-registrar para no mutar el estado innecesariamente. Si no tiene tier o el nivel cambia, call `mutate_state` action `add_work_tier` with {"id": "[module_or_task_id o bug:BUG-NNN]", "tier": "tier_1|tier_2", "rationale": "[razón por blast radius + reversibilidad]", "classified_by": "cambio-seguro"}.

   Si es `tier_0`, no llamar al MCP; confirmar que el artefacto vive fuera del árbol persistente o en `/_scratch/`.

0.35. REVISIÓN PREFLIGHT
   Antes de CODE o de editar archivos existentes, ejecutar `.agent/workflows/preflight-review.md` sobre el plan de cambio y el inventario preliminar.
   - **Receipt del llamador (triaje 2026-07-09):** si el workflow que invocó
     este (`/tdd`, `/enriquecer-ticket`, `tech-design-mvp`) YA ejecutó
     preflight-review sobre el MISMO plan y scope en este mismo flujo y su
     `PLAN_REVIEW: pass` está en la conversación, consumir ese resultado en
     vez de repetir el preflight. Re-ejecutar SOLO si el plan, el scope o el
     inventario cambiaron desde aquel review — ante la duda, re-ejecutar.
   - Si `PLAN_REVIEW: fail`, resolver los hallazgos antes de continuar.
   - Si el review detecta que falta un workflow obligatorio (`/clarificar`, `/enriquecer-ticket`, specs formales, decision_translation_core), ejecutar ese workflow primero.
   - Con dispatcher sin aislamiento read-only, usar revisor distinto y declarar
     `INDEPENDENT_SUBAGENT_NO_ISOLATION`; usar `SECUENCIAL_NO_AISLADO` solo sin
     dispatcher.

0.5. GATE DE INVARIANTES
   Si `_project/03_invariants.md` existe, leerlo completo antes de continuar.
   Para el cambio propuesto, verificar las tres dimensiones:

   **Dominio:** ¿El cambio renombra, redefine o elimina una entidad o término del lenguaje ubicuo declarado?
   **Reglas de negocio:** ¿El cambio contradice o debilita alguna RN-XXX — aunque sea "simplificando" o "generalizando"?
   **Flujos de datos:** ¿El cambio altera el orden, las entradas, las salidas o las transformaciones de una secuencia canónica?

   Si alguna respuesta es sí:
   ```
   ⛔ VIOLACIÓN DE INVARIANTE DETECTADA
   El cambio propuesto contradice [RN-XXX | Flujo: nombre | Entidad: nombre]
   en _project/03_invariants.md.
   Conflicto: [descripción exacta de qué invariante se viola y cómo]

   Este cambio no puede ejecutarse.
   Opciones:
   1. Reformular el cambio para que no viole el invariante.
   2. Si el negocio genuinamente cambió, ejecutar /actualizar-invariante primero.
   ```
   STOP. No continuar hasta resolver el conflicto.

   Si `03_invariants.md` no existe: continuar sin este check.
   Añadir al inventario PRESERVAR: `⚠️ Sin 03_invariants.md — considerar crearlo para proteger reglas de negocio y flujos de datos.`

0.6. CONSULTA DE GRAFO DE CÓDIGO (CODE_GRAPH)
   Antes de construir el inventario a mano, intentar obtener evidencia del adaptador de grafo.

   **Modo pre-edición (preferido cuando se conocen los símbolos a tocar):** por
   cada función/clase que el cambio va a modificar, consultar sus callers ANTES
   de editar (traversal inverso, fase 2):

   ```bash
   python3 .agent/scripts/code_graph.py --root . --symbol nombre_funcion --depth 2
   ```

   Con `status: "ok"`, `callers` lista quién depende del símbolo — entra
   directo al inventario PRESERVAR. Con 0 callers y símbolo resuelto, es
   evidencia de ausencia condicionada a la frescura del índice (el warning lo
   dice); no dispensa el paso 1 para superficies no indexables (UI, config,
   datos).

   **Modo diff (cuando ya hay cambios sin commitear en el árbol):**

   ```bash
   python3 .agent/scripts/code_graph.py --root . --diff-ref HEAD
   ```

   - Si `status: "ok"` → usar `afectados` y `evidence` como base del inventario del paso 1. `riesgo`/`clasificacion_sugerida` no aportan nada en esta fase (siempre `unknown`/`null`) — la clasificación del paso 2 sigue siendo íntegramente del agente. Registrar los archivos/símbolos detectados en la entrada del módulo en `_project/11_file_dependencies.md`.
   - Si `status: "degraded"` → declarar `CODE_GRAPH_MANUAL` (ver `.agent/adapters/_capability_matrix.md`) en la respuesta y, si afecta continuidad, en `_project/15_environment_profile.md`; continuar con el inventario manual del paso 1 sin ningún cambio.
   - **Importante:** en modo diff, `detect_changes` solo mide diff ya presente en el árbol de trabajo. Si el árbol está limpio (caso normal antes de implementar), el modo diff degrada — eso es lo esperado, no un fallo, y no debe interpretarse como "confirmado sin impacto"; usar el modo pre-edición (`--symbol`) para obtener evidencia antes de editar, y re-ejecutar el modo diff tras la primera pasada de implementación.
   - Esta consulta nunca bloquea el flujo: cualquier fallo o ausencia del adaptador se trata como degradación, no como error.

1. INVENTARIO DE PRESERVACIÓN
   Antes de editar, listar lo que ya debe seguir funcionando:
   (Si el paso 0.6 obtuvo evidencia con `status: "ok"`, partir de `afectados`/`evidence` en vez de leer archivos desde cero.)
   - Funciones públicas, clases exportadas, endpoints, comandos, rutas y eventos.
   - Estados visibles de UI, flujos de usuario, mensajes, validaciones y permisos.
   - Persistencia, migraciones, formato de datos, nombres de campos y compatibilidad.
   - Tests existentes o checks manuales que cubren esos comportamientos.

   Cuando aplique (según `module_specs`), añadir explícitamente al inventario:
   - **Entidades declaradas** en `data_model.md` que toca el módulo: nombre, atributos, relaciones declaradas.
   - **Endpoints declarados** en `api_contract.yaml` que toca el módulo: path, método, request schema, response schema y errores documentados.

   Formato:
   ```markdown
   PRESERVAR:
   - [contrato/comportamiento] — evidencia: [archivo, test, AC o uso]
   - Entidad [NombreEntidad] — evidencia: data_model.md sección `### NombreEntidad`
   - Endpoint [MÉTODO /ruta] — evidencia: api_contract.yaml paths./ruta
   ```

2. CLASIFICAR EL CAMBIO
   Declarar una de estas categorías:
   - `ADITIVO`: agrega comportamiento sin cambiar lo existente.
   - `COMPATIBLE`: cambia implementación interna y conserva comportamiento público equivalente.
   - `DESTRUCTIVO`: elimina, desactiva, renombra, reemplaza, simplifica o altera comportamiento existente.

   Si hay duda, clasificar como `DESTRUCTIVO`.

   Reglas explícitas para specs formales (ADR-012):
   - Quitar o renombrar una **entidad declarada** en `data_model.md` → `DESTRUCTIVO`.
   - Quitar campos declarados de una entidad, o cambiar su tipo → `DESTRUCTIVO`.
   - Añadir campos opcionales a una entidad declarada → `ADITIVO` (actualizar `data_model.md` en el mismo cambio).
   - Quitar o renombrar un **endpoint declarado** en `api_contract.yaml` → `DESTRUCTIVO`.
   - Cambiar el shape de un response schema existente o eliminar códigos de error documentados → `DESTRUCTIVO`.
   - Añadir un endpoint nuevo o un response schema opcional → `ADITIVO` (actualizar `api_contract.yaml` en el mismo cambio).
   - Cambiar implementación interna manteniendo el contrato declarado → `COMPATIBLE`.

   Si el resultado es `DESTRUCTIVO`, call `mutate_state` action `add_work_tier` with {"id": "[module_or_task_id]", "tier": "tier_2", "rationale": "Cambio clasificado DESTRUCTIVO; requiere aprobación explícita, evaluación de impacto, rollback probado y revisión de segundo paso.", "classified_by": "cambio-seguro"} antes de continuar.

3. GATE DE APROBACIÓN
   Si el cambio es `DESTRUCTIVO`, detenerse y preguntar antes de editar:
   ```markdown
   CAMBIO DESTRUCTIVO DETECTADO
   - Se afectaría: [función/ruta/comportamiento]
   - Impacto: [qué dejaría de funcionar o qué cambiaría]
   - Alternativa compatible: [si existe]
   ¿Apruebas eliminar/cambiar esto?
   ```

   Sin aprobación explícita, preservar el comportamiento existente y adaptar la solución alrededor de él.

4. IMPLEMENTACIÓN
   Durante la edición:
   - No borrar código funcional para "limpiar" si no es necesario para la tarea.
   - No reemplazar una API pública por otra sin mantener adaptador, alias o migración compatible.
   - Si aparece un archivo no declarado en `11_file_dependencies.md`, actualizar
     el mapa autónomamente cuando sea una dependencia derivable del scope. Solo
     devolver `DECISION_REQUIRED` si revela una ampliación real de scope o una
     nueva superficie de producto no aprobada.
   - Si se descubre funcionalidad no documentada pero en uso, añadirla al inventario `PRESERVAR`.

4.5. CONTROL DE ALCANCE DEL DIFF — señal, no gate por líneas
   La constitución clasifica rigor por blast radius + reversibilidad, **nunca por
   número de líneas**. Los límites siguientes son una señal interna para volver a
   comprobar alcance y capacidad de revisión; superarlos por sí solo NO pide
   aprobación ni devuelve el turno:

   | Tipo de cambio | Umbral líneas añadidas/borradas | Umbral archivos tocados |
   |---|---|---|
   | `ADITIVO` | 300 | 8 |
   | `COMPATIBLE` | 200 | 6 |
   | `DESTRUCTIVO` | 100 | 4 |
   | Bugfix puntual | 80 | 3 |

   Si el cambio se proyecta o ya supera el umbral:
   1. Revalidar que cada archivo y cada bloque pertenece al scope/ACs aprobados.
   2. Si es posible, trocear autónomamente en rebanadas verificables dentro del
      mismo módulo; si supera 2x, preferir esa ruta y sellar cada rebanada verde.
   3. Registrar en el digest la razón concreta del tamaño y la estrategia de
      revisión. Dentro de `/ciclo`, devolver `CONTINUE` si el scope sigue intacto.
   4. Devolver `DECISION_REQUIRED` únicamente si para continuar hace falta
      ampliar scope, alterar una superficie observable o aceptar un cambio
      destructivo; devolver `BLOCK` si el tamaño impide una revisión fiable y no
      existe una rebanada segura.

   Excepciones automáticas (no requieren aprobación):
   - Creación de un módulo nuevo declarado en `04_active_scope.md` (sin tocar otros módulos).
   - Generación de archivos por scaffolders/migrations declaradas como tales.
   - Cambios documentales puros (sólo `.md` fuera de `_project/` críticos).

   El conteo de líneas nunca sustituye los gates de tier, contratos, invariantes,
   anti-regresión o aprobación destructiva.

5. VERIFICACIÓN ANTI-REGRESIÓN
   Antes de cerrar el cambio, ejecutar el set más pequeño viable:
   - Tests nuevos o existentes relacionados con el cambio.
   - Checks manuales de los elementos del inventario `PRESERVAR`.
   - Para UI: verificar estados principales y que no desaparecieron controles o flujos existentes.

   Si no hay runner viable, documentar verificación manual mínima.

6. EVIDENCIA DE CIERRE
   Con `INVOCADO_POR_CICLO: true`, reportar la evidencia al driver y devolver
   `INTERNAL_RESULT: CONTINUE`; no emitir `Siguiente:`. Si se activó un
   gate de los pasos anteriores, devolver `BLOCK` o `DECISION_REQUIRED` según el
   contrato anidado. En standalone, reportar:
   ```markdown
   ANTI-REGRESIÓN:
   - Tipo de cambio: [ADITIVO | COMPATIBLE | DESTRUCTIVO aprobado por usuario]
   - Preservado: [lista corta]
   - Verificado con: [tests/comandos/manual]
   - Riesgo residual: [ninguno o explicación]
   ```

   Emitir en texto plano, sin bloque de código:

   Siguiente: /ciclo Aplicar el cambio con prueba primero y llevar el módulo a cierre.
   Modelo: <strong|standard|limited> · Esfuerzo: <🟢 bajo|🟡 medio|🟠 alto|🔴 xhigh> (<tier>)

La interfaz, los logs y los textos para el usuario van en español.
