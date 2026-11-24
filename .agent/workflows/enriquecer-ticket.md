---
description: Refina una historia de usuario o módulo recién definido a tarea técnica detallada lista para implementación. Refina module_specs con classified_by:enriquecer-ticket
---

# Enriquecer Ticket — HU/Módulo a Tarea Técnica

Usar después de `/scope-siguiente` antes de implementar, o cuando llega una historia de usuario sin detalle técnico suficiente.

## Cuándo usar

- El módulo activo ya está definido con ACs iniciales pero falta detalle técnico (endpoints concretos, validaciones, esquemas, criterios de test).
- La HU describe el qué pero no el cómo, y la complejidad es Medium o High.
- `module_specs[módulo]` ya existe (inicializado por `/scope-siguiente`) pero necesita refinarse con base en el ticket concreto.

## Delimitación frente a otros comandos

- `/entrevistar` — entrevista de **negocio** (qué resuelve, para quién, criterio de éxito). Se ejecuta antes de tener scope.
- `/diseñar` — pipeline de **arquitectura macro** (PRD + diseño técnico de fase). Define módulos, no tickets.
- `/enriquecer-ticket` — traducción de **tarea concreta** lista para `/tdd` o implementación directa. Trabaja sobre un módulo ya existente.

## Contrato anidado

Con `INVOCADO_POR_CICLO: true`, no emitir cierre user-facing ni `Siguiente
comando`: un refinado derivado devuelve `INTERNAL_RESULT: CONTINUE`; un gate
duro devuelve `INTERNAL_RESULT: BLOCK`; una decisión real del builder devuelve
`INTERNAL_RESULT: DECISION_REQUIRED`. Solo `BLOCK` o `DECISION_REQUIRED`
devuelven el turno al builder.

## Pasos

0. CONTEXTO MÍNIMO
   Leer:
   - `_project/03_project_state.json → module_specs[módulo activo]`
   - `_project/04_active_scope.md` — módulo activo con ACs y tareas iniciales
   - `_project/08_module_acceptance.md` — ACs declarados
   - `_project/01_product_requirements.md` — sección que motivó el módulo
   - `_project/03_arch_decisions.md` — ADRs aplicables
   - `_project/03_invariants.md` si existe — invariantes que el ticket no puede violar
   - `_project/02_technical_plan/data_model.md` si `module_specs[módulo].data_model ∈ {required, done}` — vía skill `spec_contracts`
   - `_project/02_technical_plan/api_contract.yaml` si `module_specs[módulo].api_contract ∈ {required, done}` — vía skill `spec_contracts`
   - `_project/09_domain_profile.md` si existe
   - `_project/10_arch_constraints.md` — NFRs, integridad de reportes, atributos de calidad

   Si la vista de implementación está incompleta por una obligación de
   preparación, este workflow puede producirla. Después de escribir o mutar
   el enriquecimiento, reconsultar la vista y no pasar a CODE hasta que el
   contexto requerido esté completo.

   <!-- CONTEXT_SOURCE: ui_states -->
   Dentro de la sección de aceptación declarar siempre la aplicabilidad:
   `UI_STATES: required` cuando existe superficie de UI, o
   `UI_STATES: n/a — justificación concreta` cuando no existe. Declarar también
   `NFR: required` si aplican NFR adicionales, o `NFR: n/a — justificación
   concreta`. La ausencia de cualquiera de las dos clasificaciones es
   `NEEDS_INPUT`; nunca se infiere `n/a` por falta de frontend o de una clave.
   Cuando `UI_STATES: required`, añadir dentro de la sección del módulo una
   subsección `### UI_STATES` con una línea verificable para cada estado
   obligatorio: `Ideal`, `Vacío`, `Cargando`, `Error` y `Parcial`. Cada línea
   debe describir el comportamiento esperado y terminar en `— evidencia:` con
   un archivo, test, AC o contrato concreto; si un estado individual no aplica,
   debe aparecer como `Estado — n/a — razón individual` sin heredar una razón
   global. Una mera clasificación o este procedimiento no constituye evidencia.
   Cuando `NFR: required`, añadir en `10_arch_constraints.md` una subsección
   `## NFR del módulo: <módulo>` con entradas concretas para `Objetivo`,
   `Métrica`, `Umbral` y `Verificación`; esta última debe apuntar a un archivo,
   test, AC, contrato o endpoint existente. `NFR: n/a` solo conserva la razón
   explícita de no aplicar y no requiere esa subsección.
   <!-- END_CONTEXT_SOURCE: ui_states -->

   Si `module_specs[módulo]` no existe → STOP, derivar a `/scope-siguiente` para clasificación inicial.

0.7. EXPERTISE GATE (4.1.0, FW-072)
   Antes de enriquecer, verificar la lente: ¿el ticket toca un sub-dominio del
   producto que no cubren ni las skills core ni los expertos activos de
   `_project/09_domain_profile.md`? Si sí → ejecutar la Parte A de
   `.agent/workflows/sugerir-skills.md`: proponer el experto con borrador listo
   (checkpoint 3.19.0) citando la sección del ticket/PRD que lo dispara.
   Si el sub-dominio ya tiene experto en `.agent/skills/domain/` → cargarlo y
   enriquecer el ticket CON esa lente (validaciones e invariantes del dominio
   entran a la Definition of Done). No bloquea: si el builder difiere, seguir
   y dejar la propuesta registrada en `09_domain_profile.md`.

   Si este paso corre sobre un módulo ya identificado (no una fase completa) y
   `scope-siguiente.md` paso 1.5 todavía no selló su checkpoint para ese
   módulo, sellarlo aquí con `mutate_state` action `record_checkpoint`:
   `{"module": "[module]", "kind": "expert_audit", "scope_id": "[module]",
   "evidence": "[expertos consultados y huecos encontrados, o 'ningún experto
   activo toca el módulo']", "reference": "09_domain_profile.md → Expertos
   activos, o esta sección del ticket"}` (FW-134).

1. DETECTAR GAPS
   Comparar el módulo activo (scope + ACs) contra los artefactos formales y declarar qué falta:
   - ¿Las entidades implicadas están declaradas en `data_model.md`? Si no, marcar `data_model_pendiente_de_declarar`.
   - ¿Los endpoints HTTP están declarados en `api_contract.yaml`? Si no, marcar `api_contract_pendiente_de_declarar`.
   - ¿Hay validaciones de entrada declaradas (backend/frontend)? Si el módulo expone HTTP, declarar al menos: tipo, requeridos, rangos, sanitización.
   - ¿Hay criterios de seguridad? auth, ownership, rate-limit cuando aplique.
   - ¿Hay criterios de test concretos? unit + integración + E2E mínimo.
   - ¿Hay estrategia de errores y observabilidad declarada? (alinear con `03_arch_decisions.md`).

2. GENERAR BORRADOR ENRIQUECIDO
   Producir bloque que se incrustará en `_project/04_active_scope.md` reemplazando o ampliando la sección del módulo activo:

   ```markdown
   ## Módulo: [module_id] — ticket enriquecido

   ### Resumen funcional
   [1-3 líneas que mantengan la intención de la HU original]

   ### Cambios en modelo de datos
   - [Entidad afectada y cambio concreto, o "ninguno"]
   - Referencia: `02_technical_plan/data_model.md → ### NombreEntidad`

   ### Endpoints afectados
   - `[MÉTODO /ruta]` — propósito, request schema, response schema, errores documentados
   - Referencia: `02_technical_plan/api_contract.yaml → paths./ruta`

   ### Validaciones backend
   - [Campo: tipo + reglas (requerido, longitud, formato, rango)]
   - [Sanitización aplicada]

   ### Validaciones frontend (si aplica)
   - [Campo: regla visible + mensaje al usuario]
   - [Estados manejados: 5 estados de UI según GUARDRAILS_MASTER.md]

   ### Seguridad
   - [auth requerida: sí/no — tipo]
   - [ownership/autorización: regla concreta]
   - [datos sensibles tratados: cómo]

   ### Tests requeridos
   - Unit: [nombres o descripciones de tests mínimos]
   - Integración: [escenarios principales]
   - E2E mínimo: [golden path obligatorio]
   - Cobertura objetivo: según `10_arch_constraints.md → NFRs`

   ### Estrategia de errores
   - [Clasificación según `03_arch_decisions.md`: FATAL / RECUPERABLE / ESPERADO / SILENCIOSO-OK]
   - [Logs estructurados que se emiten]

   ### Definition of Done
   - [ ] [criterio concreto y verificable]
   - [ ] [criterio que cubra anti-regresión: cambio-seguro ejecutado si aplica]
   - [ ] [criterio que cubra observabilidad: log presente / métrica emitida]
   - [ ] [criterio que cubra docs: README/contrato actualizado]
   - [ ] [si el módulo toca UI] La UI queda descompuesta en componentes con responsabilidad única (vista / estado / servicios separados); ningún archivo de UI supera el umbral de §Chunking sin justificación.
   ```

   **Formato EARS para los criterios (4.12.0, graduado por tier).** Escribir
   cada criterio con la plantilla EARS que corresponda — mapean casi 1:1 a
   tests y eliminan ambigüedad de interpretación:
   - Ubicua: "El sistema debe [respuesta]."
   - Por evento: "Cuando [disparador], el sistema debe [respuesta]."
   - Por estado: "Mientras [estado], el sistema debe [respuesta]."
   - Opcional: "Donde [feature presente], el sistema debe [respuesta]."
   - No deseada: "Si [condición no deseada], entonces el sistema debe [respuesta]."

   Graduación: **obligatorio en tier_2** (el criterio ambiguo es
   inaceptable donde el error es caro), **recomendado en tier_1**, **libre en
   tier_0**. Es guía de formato, no gate mecánico: el doctor no valida
   sintaxis de ACs.

3. ECO DE VERIFICACIÓN
   Mostrar al usuario:
   ```
   Entiendo que el ticket enriquecido cubre:
   - Modelo de datos: [cambios o "ninguno"]
   - Endpoints: [lista o "ninguno"]
   - Validaciones backend/frontend: [resumen]
   - Tests requeridos: [resumen]
   - DoD: [N] criterios concretos

   Specs formales propuestas a refinar:
   - data_model: [required|n/a — razón si n/a]
   - api_contract: [required|n/a — razón si n/a]

   ¿Sigo? ¿Hay algo que ajustar antes de escribir?
   ```
   En standalone, esperar aprobación explícita. Dentro de `/ciclo`, mostrar este
   echo como comentario informativo y continuar cuando el borrador sea trazable
   al scope aprobado.

4. ESCRIBIR ARTEFACTOS (tras aprobación standalone o `APPROVE_RECEIPT` vigente)
   - Antes de editar en modo anidado, conservar el `fingerprint` vigente como
     `parent_fingerprint`.
   - Actualizar `_project/04_active_scope.md`: reemplazar/ampliar la sección del módulo activo con el bloque del paso 2.
   - Actualizar `_project/08_module_acceptance.md`: derivar ACs de la sección "Definition of Done" del paso 2. Mantener formato `- [ ] criterio — evidencia: pendiente`.
     - **Evidencia ejecutable robusta (FW-028):** cuando un AC se verifica con un comando, usar comandos que distingan "no aplica todavía" de "fallo real" y no rompan ante rutas ausentes. Preferir un script validador idempotente (p. ej. `python3 .agent/scripts/validate_no_headless_claude.py`) sobre un `grep dir/` literal que sale con exit 2 si el directorio no existe. El grep puede citarse como regla conceptual, pero la verificación ejecutable del AC debe ser el comando robusto.
   - Refinar la spec por MCP: llamar a `mutate_state` con la acción `classify_module_spec` y
     {"id": "[módulo]", "data_model": "required|n/a", "api_contract": "required|n/a", "integration_real": "required|n/a", "justification": "[si n/a]", "classified_by": "enriquecer-ticket"}.
     NO tocar aquí module_statuses ni work_tiers (son de /scope-siguiente y /cambio-seguro).
     **FW-148 — `integration_real`:** clasificar también si el módulo necesita
     al menos un test SIN mocks contra la dependencia viva (base de datos en
     fichero, LLM real, renderer real, HTTP real). `required` si el módulo
     toca una costura con el mundo; `n/a` con justificación si es lógica pura.
     Una suite entera sobre mocks puede estar verde mientras el producto no
     arranca (Example Service: 606 tests verdes, 4 flujos rotos al ejecutar).
   - Actualizar `_project/13_session_journal.md` con una línea breve del refinado (solo si `framework_mode = initialized`).
   - **No sellar todavía el recibo de aprobación**: specs (paso 5), mapa de
     dependencias (5.2) y ADRs (5.5) también entran en su fingerprint. Se sella
     en el paso 5.6, cuando ya no cambian (FW-226).

5. PROPONER ACTUALIZACIÓN DE SPECS FORMALES (si aplica)
   Si `data_model = "required"` y faltan entidades en `data_model.md` → proponer
   las secciones a añadir. Con `INVOCADO_POR_CICLO: true`, escribir specs
   **aditivas y derivadas** únicamente desde scope, ACs, PRD o ADRs ya aprobados
   y cubiertos por el `APPROVE_RECEIPT` vigente;
   si requieren una suposición, cambian producto o son `DESTRUCTIVO`, devolver
   `INTERNAL_RESULT: DECISION_REQUIRED`. La actualización debe ocurrir antes de
   implementar; diferirla al cierre está prohibido.

   Si `api_contract = "required"` y faltan endpoints en `api_contract.yaml` →
   mismo procedimiento: paths + schemas + errores; en modo anidado solo aplica
   la autorización anterior para cambios aditivos y trazables.

   Clasificación correcta del cambio sobre las specs (ADR-012 + `/cambio-seguro`):
   - **Entidad nueva** añadida a `data_model.md` → `ADITIVO` (no rompe nada existente).
   - **Endpoint nuevo** añadido a `api_contract.yaml` → `ADITIVO`.
   - **Campo opcional añadido** a entidad o response existente → `ADITIVO`.
   - **Quitar o renombrar** entidad/endpoint declarado, cambiar tipo de campo, cambiar shape de respuesta, eliminar códigos de error → `DESTRUCTIVO` (requiere aprobación explícita según `/cambio-seguro`).

   Recordatorio al usuario: cualquier endpoint nuevo en `api_contract.yaml` requiere también una línea-puntero en `03_contracts.md` (regla ADR-012).

5.2. INTERFAZ PÚBLICA + MAPA DE DEPENDENCIAS (desde 3.18.0)
   Para módulos que introducen o modifican una interfaz pública (funciones exportadas,
   endpoints, CLI, eventos, modelos persistidos):
   - Declarar la interfaz en el borrador del paso 2 (firma + propósito por función;
     endpoints con request/response/errores; modelos con campos y tipos).
   - CHECK DE INVARIANTES: si la interfaz propuesta contradice una Regla de negocio,
     Flujo de datos o término del Dominio en `_project/03_invariants.md` (o
     `09_domain_profile.md`), flagear con `⛔ La interfaz propuesta viola [RN-XXX |
     Flujo | Entidad]` y no continuar hasta rediseñar o ejecutar `/actualizar-invariante`.
   - Registro tras aprobación: entidades persistidas → `data_model.md`; endpoints
     HTTP → `api_contract.yaml` + línea-puntero en `03_contracts.md`; contratos
     no-HTTP (CLI, librería, eventos) → `03_contracts.md`.
   - MAPA DE DEPENDENCIAS: actualizar `_project/11_file_dependencies.md` con `lee`,
     `escribe`, `interfaces_requeridas` (no-HTTP), `specs_formales_requeridas` y
     `preserva` (funciones, endpoints, rutas, flujos UI, validaciones, permisos o
     comportamiento público que este módulo NO debe romper). Si algún campo queda
     `# pendiente`, no implementar hasta completar el mapa.

5.5. GATE DE DECISIÓN ARQUITECTÓNICA (ADR) — FW-027
   Antes de recomendar implementación, revisar si el enriquecimiento introdujo o asumió una **decisión que requiere ADR**: estrategia de arquitectura, resiliencia/recuperación, modelo o flujo de datos, seguridad, o contrato/interfaz pública. Señales típicas: política de reintentos, manejo de huérfanos/fallos, elección de almacenamiento, formato de auditoría, cambio de shape de datos, autenticación.
   - Si la decisión NO está canonizada en `_project/03_arch_decisions.md` (no existe el ADR correspondiente) → marcar `adr_pending = true` y NO recomendar `/tdd`.
   - Registrar la decisión donde el ticket lo necesite (`04_active_scope.md`, `08_module_acceptance.md`) está permitido, pero la canonización del ADR debe ocurrir **antes** de implementar, vía `mutate_state` action `register_decision` — incluir siempre `"expertise": "domain/[experto].md | [skill core]"` (la lente que fundó la decisión, FW-080; sin ese campo la trazabilidad 4.1.0 queda vacía). No escribir `03_arch_decisions.md` a mano: es proyección read-only.
   - Diferir el ADR al cierre está prohibido: implementar comportamiento sobre una decisión no canonizada genera deuda documental y puede contradecir invariantes.

5.6. SELLAR EL RECIBO DE APROBACIÓN (FW-226)
   Último paso que escribe superficies del fingerprint. Sellar antes obligaba a
   un `refresh_approval_receipt` garantizado en cada módulo tier_2 (búfer TL
   `approve-receipt-stale-por-reconciliacion-de-specs`, 21 instancias).
   - Si hubo aprobación explícita nueva, llamar `record_approval_receipt` (con
     `covers_changes` si el plan lo prevé, `ciclo.md`); si
     fue derivación aditiva anidada, llamar `refresh_approval_receipt` con
     `parent_fingerprint`, `derived_from` y evidencia.
   - Si el motor rechaza el refresh, devolver `DECISION_REQUIRED`; no
     auto-declarar el receipt.

5.7. REVISIÓN PREFLIGHT ANTES DE RECOMENDAR CODE
   Ejecutar `.agent/workflows/preflight-review.md` sobre el ticket enriquecido y las specs propuestas.
   - Si `PLAN_REVIEW: fail`, no recomendar `/tdd` hasta corregir los hallazgos.
   - Con dispatcher sin aislamiento read-only, usar revisor distinto y declarar
     `INDEPENDENT_SUBAGENT_NO_ISOLATION`; usar `SECUENCIAL_NO_AISLADO` solo sin
     dispatcher.
   - Si el preflight detecta specs, ADRs, scope o ACs pendientes, reflejarlo en la salida del caso B o C.

6. SALIDA
   Antes de emitir la salida, calcular el estado de las specs formales:
   - `data_model_pending` = `true` si `data_model = "required"` Y faltan entidades en `data_model.md` que el ticket declara afectar.
   - `api_contract_pending` = `true` si `api_contract = "required"` Y faltan endpoints en `api_contract.yaml` que el ticket declara introducir o modificar.

   Con `INVOCADO_POR_CICLO: true`, no usar las plantillas standalone de abajo:
   - Caso A → `INTERNAL_RESULT: CONTINUE`.
   - Caso B → si el pendiente no pudo resolverse de forma aditiva bajo el
     `APPROVE_RECEIPT`, `INTERNAL_RESULT: DECISION_REQUIRED`; si es una
     imposibilidad externa, `INTERNAL_RESULT: BLOCK`.
   - Caso C → canonizar vía `register_decision` y re-evaluar si la decisión
     interna ya está determinada por scope/ADRs aprobados; si contiene una
     bifurcación real, `INTERNAL_RESULT: DECISION_REQUIRED`.

   Caso A — Sin specs pendientes (`data_model_pending = false` Y `api_contract_pending = false`):
   ```
   Ticket enriquecido para [módulo]:
   - module_specs refinado: data_model=[estado], api_contract=[estado], classified_by=enriquecer-ticket
   - ACs derivados de DoD: [N]
   - Specs formales: completas
   ```

   Siguiente: /ciclo Implementar con prueba primero y llevar el módulo a cierre.
   Modelo: <strong|standard|limited> · Esfuerzo: <🟢 bajo|🟡 medio|🟠 alto|🔴 xhigh> (<tier>)

   Caso B — Specs pendientes:
   ```
   ⚠️ Ticket enriquecido para [módulo] CON ACCIÓN OBLIGATORIA PENDIENTE

   - module_specs refinado: data_model=[estado], api_contract=[estado], classified_by=enriquecer-ticket
   - ACs derivados de DoD: [N]
   - Specs formales propuestas pero NO escritas todavía:
     · data_model.md: [lista de entidades a añadir, si aplica]
     · api_contract.yaml: [lista de endpoints a añadir, si aplica]

   ⛔ Acción obligatoria antes de /tdd o implementación directa:
     1. Revisar y aprobar la propuesta de specs.
     2. Actualizar data_model.md / api_contract.yaml (clasificación ADITIVO según paso 5).
     3. Añadir línea-puntero en 03_contracts.md para cada endpoint HTTP nuevo.

   Si avanzas a implementar sin actualizar las specs:
   - /cierre-modulo bloqueará con reason: data_model_missing / api_contract_missing.
   - El módulo no podrá cerrar hasta resolverlo.
   ```
   En el caso B, no recomendar `/tdd` ni implementación directa hasta que el usuario confirme cómo procede.
   Cerrar Caso B con:
   `Siguiente: ninguno — aprobar y escribir las specs pendientes.`

   Caso C — Decisión ADR pendiente (`adr_pending = true`, del paso 5.5), tenga o no specs pendientes:
   ```
   ⚠️ Ticket enriquecido para [módulo] — DECISIÓN ARQUITECTÓNICA SIN CANONIZAR

   - Decisión detectada: [resumen de la decisión ADR-worthy]
   - Aún no existe ADR en 03_arch_decisions.md.

   ⛔ Antes de /tdd o implementación directa:
     1. Canonizar la decisión como ADR vía mutate_state action register_decision.
     2. Verificar que no contradice 03_invariants.md.
   ```
   Prioridad: el Caso C manda sobre A. No recomendar `/tdd` mientras `adr_pending = true`.
   Cerrar Caso C con:
   `Siguiente: ninguno — canonizar el ADR antes de implementar.`

## Reglas

- No bloquear si `module_specs[módulo]` ya fue clasificado por `/scope-siguiente`: refinar y sobrescribir con `classified_by: "enriquecer-ticket"`.
- No modificar `classified_by` de otro firmante a "enriquecer-ticket" sin haber añadido información técnica real. La regla es: si refino, escribo; si solo leo, no toco.
- No escribir specs formales (`data_model.md`, `api_contract.yaml`) sin
  aprobación explícita standalone o un `APPROVE_RECEIPT` vigente para
  specs aditivas y derivadas. Cualquier destructivo, scope nuevo o ambigüedad
  devuelve `DECISION_REQUIRED`.
- Mantener trazabilidad: la HU original no se borra del scope, se enriquece.
- **Anti-fabricación:** validaciones, endpoints, entidades y criterios de test del ticket enriquecido solo se declaran si están justificables desde `_project/01_product_requirements.md`, ACs declarados, ADRs o respuestas explícitas del usuario. Ante un hueco, registrar la clarificación vía `mutate_state` action `register_clarification` (o ejecutar `/clarificar`) y bloquear el refinado. Nunca inferir desde dominios análogos ni desde "buenas prácticas". (07_pending_clarifications.md es proyección — no editar a mano.)

La interfaz, los logs y los textos para el usuario van en español.
