---
description: Reconciliación de conversación no lineal antes de escribir, publicar o continuar tras cambios de intención
---

# Reconciliar — Workflow de Conversación No Lineal

Usar cuando la conversación deje de ser lineal: interrupción, compactación de contexto, recap dudoso, cambio de intención del usuario, petición que contradice el scope activo, o cuando el agente detecte que la siguiente acción narrada no coincide con el estado en disco.

## Objetivo

<!-- CONTEXT_SOURCE: recovery_audit -->
La recuperación reconstruye la verdad desde estado, bitácora, scope y
proyecciones; una compactación o timeout no autoriza continuar. Auditar el
respaldo real de receipts y resoluciones, conservar fingerprints y volver a
consultar la vista `recovery` si cambia cualquier fuente.
<!-- END_CONTEXT_SOURCE: recovery_audit -->

Reconstruir la verdad operativa desde archivos, no desde memoria ni recap narrativo, y elegir una única continuación segura antes de escribir, publicar, instalar o continuar implementando.

## Reglas de seguridad

- Modo solo auditoría: **no escribir**, **no red**, no pedir permisos de red, no instalar skills/MCPs, no hacer push, no ejecutar migraciones ni comandos destructivos.
- Si hay duda entre conversación y disco, prevalece disco.

### Tabla de autoridad entre artefactos (precedencia disco-vs-disco)

Cuando dos artefactos persistidos se contradicen, **no promediar ni elegir el más reciente por fecha**: aplicar este orden de autoridad (de mayor a menor):

1. `_project/03_invariants.md` — reglas de negocio canónicas (solo cambian vía `/actualizar-invariante`).
2. `_project/03_project_state.json` — estado canónico (fuente única; las proyecciones derivan de él).
3. `_project/01_product_requirements.md` — PRD: fuente de verdad del producto.
4. `_project/02_technical_plan/` (specs: `data_model.md`, `api_contract.yaml`) — derivadas del PRD.
5. `_project/04_active_scope.md` — scope operativo de la sesión.
6. Narrativa de la conversación — la de menor autoridad.

Regla: el artefacto de mayor autoridad gana; el de menor se reporta como brecha a corregir vía el workflow correspondiente (`/scope-siguiente`, `/enriquecer-ticket`, `/actualizar-invariante`…). Si un artefacto de baja autoridad contradice a uno de alta, **no** se ajusta el de alta para que cuadre: se corrige el de baja. Ejemplo: si `04_active_scope.md` describe trabajo fuera del `01_product_requirements.md`, gana el PRD y el scope se reporta como divergencia.
- Si el usuario cambia de intención, separar: intención nueva, estado actual, conflicto y próximo comando seguro.
- Si aparecen brechas que requieren cambios de estado, no corregirlas dentro de este workflow; derivar a `mutate_state` (acción del motor que corresponda), `/scope-siguiente`, `/gycd`, `/nuevo-bug` o `/cierre-modulo`.

## Entrada por get_work_context

Consultar primero `get_work_context` con `{"view":"recovery"}` (MCP o fallback CLI `python3 .agent/scripts/state_manager.py query --view recovery`). Seguir su cursor y abrir únicamente las referencias entregadas por la vista (`_refs`). `get_full_state` queda estrictamente reservado como diagnóstico explícito ante inconsistencia, corrupción o replay, nunca como consulta rutinaria. Por encima de 256 KiB devuelve un resumen; el volcado exige `{"force": true}`. Bugs, clarificaciones y decisiones: `lookup_state` (`kind=bugs|clarifications|decisions`).

Si no hay MCP ni CLI disponibles, leer `_project/core.md` como proyección corta de recuperación.

Si `core.md` o la vista devuelven `framework_mode: template`, no reescribir nada: reportar template y elegir una única continuación (`/migrar-proyecto` para código existente o `/nuevo-proyecto` para carpeta nueva).

## Checks: Continuidad normal vs Auditoría completa

### A. Continuidad normal (tras compactación o interrupción limpia)
Si la sesión se reanuda tras una compactación normal del host o interrupción sin incoherencia reportada ni contradicciones:
1. Validar con los datos entregados por la vista `recovery`: fase, módulo activo, estado, tier y siguiente acción.
2. Si la vista referencia `_project/13_session_journal.md`, verificar pendientes no incorporados.
3. Si los datos son consistentes y no hay discrepancia con el scope, emitir la salida de reconciliación y proceder al siguiente paso sin disparar doctor/tests ni auditoría completa de bitácora.

### B. Auditoría completa de recuperación (ante contradicción, corrupción o duda)
Se activa obligatoriamente ante:
- Duda, contradicción explícita, recap inconsistente o cuando el usuario pide revisar qué se hizo.
- Petición del usuario que implica un cambio de intención o contradice el scope activo.
- Corrupción de estado, fallo transaccional pendiente (`.state.lock`), o cursor repetidamente inválido.
- Migración pendiente declarada (`framework_migrations`).

En estos casos de incoherencia:

1. **Intención actual**
   - Resumir en una línea lo último que pidió el usuario.
   - Marcar si es continuación, cambio de intención, corrección, pausa, revisión, publicación o cierre.

2. **Estado real en disco**
   - Fase actual desde `03_project_state.json.current_phase`.
   - Módulo activo desde `04_active_scope.md` y `module_statuses`.
   - Tier activo desde `work_tiers`.
   - Bloqueadores críticos: módulos `blocked`, bugs críticos, clarificaciones bloqueantes y `closure_rejections`.

2.b **Estado vs bitácora** (auditoría de respaldo)
   - Ejecutar `python3 .agent/scripts/replay_state_log.py --audit`.
   - Contrasta cada decisión, bug, clarificación y receipt del estado contra el
     evento de `state_log.jsonl` que debería haberlo creado, en ambas
     direcciones: lo que está sin haber sido registrado, y lo que se registró y
     ya no está.
   - **Salida `OK`** → el estado y la bitácora cuentan la misma historia; seguir.
   - **Entidades sin respaldo** → llegaron por una vía que no es el motor.
     Legítimo en proyectos migrados (su historia es anterior a la bitácora);
     sospechoso en cualquier otro caso. No corregir aquí: reportar al builder
     con la lista y dejar que decida, porque un receipt o un ADR fabricado
     invalida la evidencia de todo lo que se apoye en él.
   - **Entidades desaparecidas** → alguien las borró del estado o la bitácora se
     truncó. Tratar como pérdida de datos: git es la vía de recuperación.

2.c **Recuperación de una migración Codex**
   - Si el estado declara una migración aplicada, verificar la ruta y el hash
     del manifest y conservar su evidencia; no reabrir una ejecución retirada.
   - Si aparece un estado con `session.codex_execution`, el proyecto no pasó
     por 5.3.0: detener el flujo normal y seguir el suelo de versión de
     `/actualizar-framework` (paso 2); la plantilla en la etiqueta `v5.3.0` trae
     el inspector y el migrador. No relanzar, cancelar ni completar por
     inferencia.
   - Si el estado tiene una transacción pendiente o el fingerprint del
     manifest no coincide, resolver primero la recuperación y repetir la
     reconciliación. El rollback requiere el puente de compatibilidad, con la
     plantilla en `v5.3.0`, y un fingerprint sin trabajo posterior.

3. **Divergencias**
   - Conversación vs disco: scope, fase, módulo, ACs, tier y siguiente acción.
   - Diff de ACs: comparar `[x]`, evidencias y estado real de `08_module_acceptance.md`.
   - Artefactos esperados vs presentes: docs, scripts, tests, contratos,
     wrappers y el checkpoint Codex que correspondan al módulo activo.

4. **doctor/tests**
   - Ejecutar `python3 .agent/scripts/framework_doctor.py` si existe.
   - Ejecutar tests relevantes disponibles solo en modo lectura/validación.
   - Si no hay runner viable, registrar verificación manual mínima y el motivo.

5. **Decisión**
   - Si todo coincide: recomendar continuar con el comando que corresponda al flujo.
   - Si falta persistir estado: recomendar `/gycd` o la acción `mutate_state` correspondiente.
   - Si cambió el objetivo activo: recomendar `/scope-siguiente`.
   - Si hay bug nuevo: recomendar `/nuevo-bug`.
   - Si un bug ya registrado tiene un dato incorrecto (no un síntoma nuevo): señalar `amend_bug` vía MCP (no editar `06_bug_fixes.md` a mano).
   - Si un módulo en `modules.pending` nunca arrancó (sin `module_spec`, sin estado distinto de `not_started`) y ya no aplica: señalar `drop_module` vía MCP como la vía legal de retiro, en vez de editar el JSON a mano. Si ya arrancó y el builder aprobó cerrarlo sin completarlo: `abandon_module` (motivo + evidencia; estado terminal `dropped`), moviendo antes sus bugs abiertos con `reassign_bug_module`.
   - Si el módulo parece listo pero no cerrado: recomendar `/cierre-modulo`.
   - Si el proyecto está en template: recomendar un solo comando, `/migrar-proyecto` para código existente o `/nuevo-proyecto` para carpeta nueva.

## Salida

Formato obligatorio en español:

```markdown
RECONCILIACIÓN:
- Intención actual: [continuación | cambio de intención | recap | cierre | publicación | revisión]
- Estado en disco: [fase, módulo, estado, tier]
- Consistencia: [OK | brechas encontradas]
- Brechas: [lista corta o "ninguna"]
- Verificado con: [doctor/tests/comandos o fallback manual]
- Riesgo residual: [ninguno o explicación]
```

Se usa el comando del builder que corresponda a lo reconciliado; `/ciclo` es el ejemplo.

Siguiente: /ciclo <instrucción concreta y autosuficiente>
Modelo: <strong|standard|limited> · Esfuerzo: <🟢 bajo|🟡 medio|🟠 alto|🔴 xhigh> (<tier>)

UI, logs y strings visibles al usuario deben estar en ESPAÑOL.
