---
description: Cierra un módulo completado — reconcilia estado, verifica ACs, gate de seguridad y actualiza estado
---

Eres el agente de cierre de módulo.

Recibirás la vista acotada de `get_work_context`
`{"view":"closure","module_id":"<module>"}`, el código del módulo y la evidencia
que cite. Sigue los cursores. Si aún faltan recibos o evidencia de cierre,
ejecuta los workflows de ejecución y revisión autorizados y vuelve a consultar.
Exige `complete=true` y `required_context_complete=true` justo antes de
`complete_module`; usa `get_full_state` solo para un diagnóstico explícito o
una comprobación de recuperación, nunca como carga normal del cierre.

## Contrato anidado

Con `INVOCADO_POR_CICLO: true`, cierre completo devuelve `INTERNAL_RESULT: CONTINUE`
al driver, sin respuesta final ni Siguiente comando; un gate de cierre devuelve
`INTERNAL_RESULT: BLOCK`; una decisión real del builder devuelve
`INTERNAL_RESULT: DECISION_REQUIRED`. Solo `BLOCK` o `DECISION_REQUIRED` devuelven
el turno. En ejecución anidada, no ejecutar las plantillas standalone.

## Obligaciones de cierre (recuperación independiente)

<!-- CONTEXT_SOURCE: closure_preconditions -->
El cierre solo puede completar un módulo con ACs al 100% y evidencia primaria en
disco, sin bugs o clarificaciones bloqueantes, receipts de ejecución y revisión
vigentes cuando aplican, y fingerprint estable. La vista de cierre es un transporte
paginado; `complete_module` y sus validadores conservan la última palabra.
<!-- END_CONTEXT_SOURCE: closure_preconditions -->

> **Principio de recuperación independiente (D1 Criterio 2):** ante compactación
> del host o expulsión por límite combinado de skills, las obligaciones vigentes
> se recuperan directamente desde este bloque normativo, la vista `closure` y el
> validador mecánico `python3 .agent/scripts/validate_closure.py "<module>"`.
> Las obligaciones normativas van arriba; la guía de investigación abajo.

Invariantes permanentes de cierre:
1. **ACs y Evidencia primaria**: 100% verificados con formato `— evidencia:` válida y rutas reales existentes en disco (`validate_acceptance.py`).
2. **Sin bloqueantes**: 0 bugs críticos abiertos y 0 clarificaciones con `blocks_closure: true` (`blocks_start`, `blocks_implementation`, `blocked_scope`, `blocking_condition`).
3. **Graduación de Rigor por Tier**: clasificado en `work_tiers`. Si `tier_2` o ejecutor `limited`, exige ADR previo, `APPROVE_RECEIPT`, `planned second-pass review`, donde la auditoría independiente sustantiva corre en el paso 3 y el sello final independiente con `record_review_receipt` se sella en el paso 6. Sin `RECEIPT GATE: OK`, bloqueo.
4. **Specs formales**: `module_specs` clasificadas; `data_model.md` y `api_contract.yaml` al día.
5. **Verificación ejecutable**: `.agent/workflows/verificar-ejecutable.md` ejecutado y sellado con `record_execution_receipt`.
6. **STUB GATE**: `python3 .agent/scripts/validate_no_stubs.py` sin stubs ni mocks no declarados.
7. **Cosecha de fricción**: `COSECHA DE FRICCIÓN DE SESIÓN` ejecutada; el gate exige DECLARAR, no ENCONTRAR (`record_harvest`, `classes: "none"`, `friction_buffer.md`; LECCIÓN → proponerla, no escribirla, delegada a `/aprender-de-error`).
8. **Pre-validación determinista**: `python3 .agent/scripts/validate_closure.py "[module]"` adjudica en una llamada todos los gates que el motor re-ejecuta en `complete_module`.

## Pasos

### 0. GATE DE ESTADO Y TIER

Llamar a `get_work_context` con `{"view":"closure","module_id":"[module]"}`.
Preservar `source_fingerprint`. Si el status es `blocked` o `completed`, STOP.
Leer `work_tiers[module]`. Si falta, clasificar según `GUARDRAILS_MASTER.md` → "Graduación de Rigor por Tier".
Si `tier = tier_0` → STOP (re-clasificar como Tier 1 o 2).
Si `tier = tier_2` → verificar ADR, `APPROVE_RECEIPT`, rollback y planned second-pass review; la auditoría independiente sustantiva corre en el paso 3 y el sello final independiente en el paso 6.
Si `execution_profile.capability_tier = "limited"`, exigir `requires_second_review: true` en cualquier tier.

Ejecutar pre-validación: `python3 .agent/scripts/validate_closure.py "[module]"`. Con `_project/quality.yaml` ejecuta además sus órdenes, hasta 600 s cada una: lanzarla con un tiempo de espera holgado.
Si status es `in_progress`, transicionar a `ready_for_validation` vía `mutate_state` action `update_module_status`.

### 1. RECONCILIACIÓN

Reconciliar fuentes de verdad contra `03_project_state.json`:
a) BUGS CRÍTICOS: verificar que no existan bugs críticos abiertos para este módulo. Si existen, transicionar a `blocked`, registrar `critical_bug_exists` en `add_closure_rejection` y STOP.
b) ACLARACIONES BLOQUEANTES: revisar `07_pending_clarifications.md` por CLARs abiertas con `blocked_scope` coincidente y `blocks_closure: true` (distinguiendo `blocks_start` y `blocks_implementation`, con `blocking_condition` explícita). Si existen, STOP con `clar_pending`.
c) CRITERIOS DE ACEPTACIÓN: cobertura 100% de ACs en `08_module_acceptance.md`. Cada AC `[x]` debe tener `— evidencia:` ≥ 20 caracteres con comando, test o ruta existente en disco.
Ejecutar `python3 .agent/scripts/validate_acceptance.py "[module]"`. Si falla, registrar `ac_incomplete` o `ac_evidence_missing` y STOP.

### 2. VÁLVULA DE ESCAPE — DEUDA MENOR

Antes de trasladar nada, proponer `resolve_bug` para los bugs abiertos que citan las ADR del módulo (`_decisions` con su `module_id` cuyo texto nombra `BUG-NNN`) si el arreglo ya está hecho (TL-FW-313).
Bugs de severidad menor o mejoras no bloqueantes se trasladan a "Deuda Técnica" en `04_active_scope.md` llamando a `mutate_state` action `defer_bug_to_tech_debt`. El cierre continúa.

### 3. AUDITORÍAS EN PARALELO SI ESTÁN DISPONIBLES

Enlazar `.agent/docs/extended_protocol.md` → `SUBAGENT_WAIT_LIFECYCLE`. `WAIT_TIMEOUT_NONTERMINAL` no es pass/fail ni cancelación; el árbol queda congelado hasta estado terminal.

3.0 ACTIVAR LA REVISIÓN DE ÁREA (CONDICIONAL AL FOCO ACTIVO, 3.16.0):
Leer `active_focus`. Si es `frontend`/`ui`/`ux` invocar `.agent/subagents/area-frontend.md`; si `backend` invocar `.agent/subagents/area-backend.md`; si `data` invocar `.agent/subagents/area-data.md` junto a Reviewer + Security.
Si existe despacho sin aislamiento read-only, usar `INDEPENDENT_SUBAGENT_NO_ISOLATION`.
Si no hay despacho independiente y el módulo no es tier_2, usar `SECUENCIAL_NO_AISLADO`.
Si el módulo es `tier_2` y no hay despacho independiente, degradar a `HUMAN_INDEPENDENT_REVIEW`.

3.1 DETECTAR SOPORTE DE SUBAGENTES READ-ONLY:
> **Tamaño del encargo (FW-195).** Antes de despachar, partir la revisión en
> encargos pequeños y autosuficientes; no asignar una auditoría integral a un
> único subagente. La revisión independiente del cierre sí se delega (reviewer
> y security); en Codex el resto no se delega por defecto y lo fija el
> adaptador activo, con su tope (`CODEX_SMALL_TASK_DELEGATION`).
> **Espera (`SUBAGENT_WAIT_LIFECYCLE`).** Ver `.agent/docs/extended_protocol.md`.
Una revisión interrumpida por tope de espera queda como `NEEDS_ATTENTION` (nunca `PASS`/`FAIL`) y no satisface la revisión independiente de un módulo tier_2: se repite o se degrada según este paso.
> **Patch para los revisores** (FW-341, JS:JSO-FW-002). Antes de cada ronda, el driver genera el diff de la ronda como fichero fuera del árbol, sin ensuciarlo: `mkdir -p "$(git rev-parse --git-dir)/framework-review" && git add -N . && git diff --binary <base> > "$(git rev-parse --git-dir)/framework-review/<módulo>-<ronda>.patch"`, y pasa esa ruta a `reviewer` y `security`. Los revisores no tienen shell: nunca pedirles `git diff`; leen el patch y el árbol. `<base>` es el sha base del módulo; `git add -N .` hace que los ficheros nuevos entren en el patch sin cambiar su contenido.
Invocar en paralelo:
- `reviewer`: audita código contra contratos, ACs, dependencias y specs formales (`spec_contracts`).
- `security`: audita contra restricciones y secret scanning. Prohibido ejecutar instaladores (pip/npm/uv).
Si no hay subagentes y el módulo no es tier_2, declarar:
`MODO DE AUDITORÍA: SECUENCIAL_NO_AISLADO`
Si el módulo es `tier_2` y no hay despacho independiente, `SECUENCIAL_NO_AISLADO` no es válido; declarar:
`MODO DE AUDITORÍA: HUMAN_INDEPENDENT_REVIEW` y solicitar revisión humana independiente.

3.2 INVOCACIÓN SECUENCIAL (fallback sin dispatcher):
En módulos no tier_2 sin dispatcher, ejecutar secuencialmente las checklists de Reviewer y Security.

3.3 QUÉ HACER CON UN fail:
Corregir la clase completa del defecto en todo el módulo antes de reintentar.

3.4 GATE:
Ambas revisiones deben resultar en `pass`. Si cualquiera falla, STOP con `INTERNAL_RESULT: BLOCK`.

3.5 ANTI-REGRESSION GATE:
Verificar `.agent/workflows/cambio-seguro.md` y preservar contratos, UI, persistencia y tests.

3.6 DATA SAFETY GATE (FW-083):
Si `work_tiers[module].handles_real_data == true`, verificar `backup_verified: true` vía `record_data_safety_check`. Si falta, STOP con `data_safety_missing`.

3.7 SPEC GATE — Specs vivas (ADR-012, ADR-013):
Verificar `module_specs[module]` en `03_project_state.json`. `validate_closure.py` valida presencia. Validar que entidades en `data_model.md` y endpoints en `api_contract.yaml` estén declarados.

3.8 EJECUTABLE GATE — Verificación real (ADR-015):
Ejecutar `.agent/workflows/verificar-ejecutable.md`. Registrar receipt vía `record_execution_receipt`. `complete_module` exige execution receipt vigente.

3.8.b STUB GATE — el código citado no puede ser decorado (FW-145):
Ejecutar `python3 .agent/scripts/validate_no_stubs.py "[module]"`. Bloquea ante mocks o datos de relleno no declarados.

3.9 EXPERT GATE — Expertos de dominio:
Revisar `09_domain_profile.md`. En tier_2, verificar checkpoints de `domain_sweep` y `expert_audit`. Si faltan, STOP.

### 4. SECURITY GATE + GATE CODE+LOG

Verificar el Gate CODE + LOG de `GUARDRAILS_MASTER.md`:
- Logs estructurados, estrategia de errores, migraciones con guard, sin secretos hardcodeados ni PII en logs.
- Respetar reglas de negocio e invariantes de `03_invariants.md`.

### 5. CRITERIO DE MUERTE

Si `closure_rejections` para este módulo es ≥ 2 por gates duros, `validate_closure.py` bloquea: STOP y recomendar módulo "Estabilización".

### 5.5. COSECHA DE FRICCIÓN DE SESIÓN

Ejecutar la cosecha acotada a este módulo antes de cerrar.
El gate exige DECLARAR, no ENCONTRAR. `classes: "none"` es legítimo si no hubo fricción.
Destinos:
- Hallazgo accionable → `field_report.md` (`FW-NNN` en la plantilla; `PREFIJO-FW-NNN` en un adoptante, con el `fw_prefix` del perfil).
- Clase en observación → `friction_buffer.md`.
- LECCIÓN → proponerla, no escribirla (espera a `/aprender-de-error`).
Llamar a `mutate_state` action `record_harvest` con `classes`. Emitir en el reporte: `Cosecha de fricción: ...`.

### 6. MARCAR COMO CERRADO

1. Actualizar `08_module_acceptance.md` con `Estado: ✅ Verificado por el agente (código + integración)` en la sección del módulo. Nunca «con cliente»: el uso real lo confirma el builder con `confirm_builder_use`. Si el módulo añade una pantalla, la evidencia de integración incluye llegar a ella navegando desde el inicio, no solo que la URL responda.
2. Si el módulo es `tier_2`, ejecutor `limited` o `requires_second_review: true`, sellar diff estable:
   - Antes de abrirla: specs (`data_model.md`, `api_contract.yaml`, `03_contracts.md`) y docs en su estado final y todo commiteado; editarlos con la ronda abierta la invalida. Un hallazgo del revisor se aplica tras sellar, en una ronda delta nueva.
   - Llamar `mutate_state start_independent_review` con `module`, `reviewer_id`, `implementer_id` y modo en `READ_ONLY_SUBAGENTS | INDEPENDENT_SUBAGENT_NO_ISOLATION | HUMAN_INDEPENDENT_REVIEW`.
   - Árbol congelado durante la revisión.
   - Patch de la ronda (FW-341, JS:JSO-FW-002): antes de abrirla, el driver genera el diff como fichero fuera del árbol con `mkdir -p "$(git rev-parse --git-dir)/framework-review" && git add -N . && git diff --binary <base> > "$(git rev-parse --git-dir)/framework-review/<módulo>-<ronda>.patch"` y pasa esa ruta a `reviewer` y `security`. Los revisores no tienen shell: nunca pedirles `git diff`.
   - Llamar `mutate_state record_review_receipt` con `reviewer_id`, `result: pass` y evidencia.
   - Una ronda con `result: fail` se sella aunque ya hayas corregido el código (queda anotado el hash corregido); después se abre otra ronda. Un `pass` sí exige el árbol intacto.
   - Ejecutar `python3 .agent/scripts/workflow_receipts.py review <módulo>`. Sin `RECEIPT GATE: OK`, STOP.
   Marcar una spec de `required` a `done` (y reescribir el texto de su exención) o anotar evidencia no caduca el `APPROVE_RECEIPT`: no hace falta `refresh_approval_receipt`.
   El avance del módulo se anota solo con la casilla, una línea `Estado:` o la evidencia (`— evidencia:` o `(evidencia: …)`), que la huella ignora. Reescribir la prosa aprobada de `04_active_scope.md` o de un AC al cerrar sí caduca el recibo (FW-311): si el alcance cambió de verdad, es aprobación nueva.
3. Llamar `mutate_state` action `complete_module` con `{"id": "[module]"}`.
   `complete_module` re-ejecuta todos los gates deterministas en el motor.

### 7. EVIDENCIA DE CIERRE

Añadir entrada concisa en `05_test_history.md`, con las líneas resumen `calidad:` de la `validate_closure.py` del paso 0: aviso que nunca bloquea y que, tras `complete_module`, ya no mide. Salen de `_project/quality.yaml`, opcional, con las órdenes de las herramientas del stack (modelo: `.agent/templates/quality.yaml.base`); sin él, `calidad: no configurada`.

### 8. HIGIENE DE ARQUITECTURA

Verificar criterios de parada globales en bugs y reyecciones. Ejecutar `python3 .agent/scripts/validate_file_size.py` informativo.

### 9. SIGUIENTE MÓDULO

Identificar la siguiente tarea no bloqueada en `04_active_scope.md`.

### 10. ACTUALIZAR LOS PUNTEROS

Llamar a `mutate_state` action `set_session_pointer` con `next_action`, `next_command`, `next_module`.

### Digest de decisiones (enseñanza)

Aplicar `decision_translation_core.md` (Modo C) y `builder_language_core`: explicar qué se decidió y su valor para el producto. Si hubo ADRs registradas después de la aprobación (aditivas o que documentan decisiones previas, presentes en `added_decisions` del recibo de aprobación), listarlas explícitamente:
- Para cada una, indicar a cuál documenta si declara `documents_decision` (ej. `ADR-002 documenta detalle de ADR-001`).
- Si no declara a cuál documenta, marcarla explícitamente como `(sin documentar decisión previa)`.

### 11. SALIDA

Con `INVOCADO_POR_CICLO: true`, devolver `INTERNAL_RESULT: CONTINUE`.
En standalone, si la conversación es larga, la frontera natural de sesión es `/gycd` y abrir sesión nueva con ese mismo `/ciclo`. Emitir en texto plano, sin bloque de código:

Siguiente: /ciclo <instrucción concreta del siguiente módulo>
Modelo: <strong|standard|limited> · Esfuerzo: <🟢 bajo|🟡 medio|🟠 alto|🔴 xhigh> (<tier>)

### 12. COMMIT RECOMENDADO (FW-079)

Recomendar commit del módulo: `git add -A && git commit -m "feat([módulo]): ... — cierre con gates OK"`.

### 13. SUGERIR SESIÓN NUEVA (FW-241)

Sugerir al builder iniciar una sesión nueva para el siguiente módulo pegando el `Siguiente:` (sin prompt, `/arrancar`) para preservar contexto limpio.

UI, logs y mensajes al usuario deben estar en ESPAÑOL.
