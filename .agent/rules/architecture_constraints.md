---
description: Invariantes del agente que aplican SIEMPRE — nunca se desactivan
---

# Invariantes del Agente

Estas reglas aplican a TODO el proyecto, sin excepción. Son del agente, no del proyecto.

## Estado externo como fuente de verdad
- SIEMPRE usa la vista MCP `get_work_context` con `view="startup"` (o el
  fallback CLI equivalente) al inicio de cada sesión; reserva
  `get_session_brief` y `get_full_state` para compatibilidad, diagnóstico o
  recuperación explícita.
- Los archivos `core.md`, `06_bug_fixes.md`, `07_pending_clarifications.md`, `03_arch_decisions.md` y `00_session_brief.json` son **proyecciones de solo lectura**. El agente tiene PROHIBIDO editarlos directamente a mano.
- Cualquier modificación de estado, registro/resolución de bugs, clarificaciones o decisiones arquitectónicas debe realizarse exclusivamente llamando a la herramienta MCP `mutate_state` (o en su defecto invocando `python3 .agent/scripts/state_manager.py mutate`).
- SIEMPRE lee `_project/04_active_scope.md` antes de implementar un módulo.
- NO reconstruyas contexto desde memoria conversacional. Si falta información, pregunta.
- NO asumas interfaces, módulos o dependencias no declaradas en el estado del proyecto.

## Comportamiento por defecto
- Una tarea por interacción.
- No anticipar módulos futuros ni proponer mejoras no solicitadas.
- No añadir docstrings, comments o type annotations a código que no se modificó.
- No crear abstracciones prematuras para operaciones de una sola vez.
- Respuestas concisas: no resumir lo que el diff ya muestra.
- NO quitar, desactivar ni reemplazar funcionalidad ya implementada salvo petición explícita del usuario o aprobación previa tras explicar el impacto.
- Antes de borrar código, rutas, comandos, archivos de estado, APIs o comportamientos existentes, confirmar si son obsoletos o si deben migrarse.
- Si una mejora entra en conflicto con una implementación previa, preservar la implementación y adaptar alrededor de ella hasta tener decisión del usuario.
- Antes de modificar código existente, ejecutar el gate de `.agent/workflows/cambio-seguro.md`: inventario `PRESERVAR`, clasificación `ADITIVO | COMPATIBLE | DESTRUCTIVO` y verificación anti-regresión.
- Una aprobación genérica de refactor no autoriza cambios `DESTRUCTIVO`; cualquier eliminación o cambio de comportamiento requiere aprobación explícita separada.

## Journal de sesión
- Si haces cambios relevantes y no ejecutas inmediatamente `mutate_state`, `/cierre-modulo` o `/gycd`, registrar un resumen en `_project/13_session_journal.md`.
- Tras una compactación, los cambios previos no persistidos deben seguir presentes en el bloque de compactación o en `_project/13_session_journal.md`.
- `/gycd` debe incorporar primero el journal y luego limpiarlo si todo quedó persistido.
- En `framework_mode: template`, `_project/13_session_journal.md` debe permanecer como plantilla limpia. La evolución del framework base se documenta en `.agent/docs/framework_history.md` o `.agent/docs/framework_arch_decisions.md`, no en artefactos exportables del proyecto.

## Dependencias

<!-- CONTEXT_SOURCE: dependencies -->
La lista canónica de dependencias vive en `03_project_state.json`. Solo pueden
importarse paquetes declarados allí; una dependencia nueva requiere declararla
explícitamente y aprobación. Las skills externas deben quedar fijadas y
registradas en `.agent/skills/imported/` con procedencia.
<!-- END_CONTEXT_SOURCE: dependencies -->

- Solo importar paquetes listados en `project_state.json → dependencies`.
- Si necesitas un paquete nuevo, decláralo explícitamente y espera aprobación.
- Toda skill o paquete técnico instalado desde una fuente externa debe registrarse en `_project/14_skill_provenance.md`.
- No usar instaladores remotos con versión implícita (`latest`) para poblar skills del proyecto.
- Instalar skills por defecto en `.agent/skills/imported/`; no instalar globalmente sin aprobación explícita.
- Proponer skills cuando haya señales claras de especialización, pero no instalar ni ejecutar instaladores sin aprobación.
- Toda publicación GitHub requiere gate de secretos, revisión de `.gitignore` y aprobación explícita antes de push.
- Usar comandos nativos del entorno cuando existan; mantener workflows duplicados solo como fallback portable.

## Acoplamiento
- Preferir inyección de dependencias sobre instanciación directa.
- No hardcodear paths ni valores de configuración.
- Cada módulo es independiente — no importar lógica de otros módulos directamente.

---

## Invariantes de Estado (03_project_state.json)

> Esta sección es la FUENTE CANÓNICA ÚNICA de la máquina de estados de módulos.
> Cualquier otra referencia en `extended_protocol.md`, `cierre-modulo.md` o `CLAUDE.md` apunta aquí.

### 0. Tier de rigor por unidad de trabajo

`work_tiers` registra cuánto rigor pagó cada unidad de trabajo que entra al proyecto.

Formato mínimo por clave de módulo o tarea:

```json
"work_tiers": {
  "[module_or_task_id]": {
    "tier": "tier_0 | tier_1 | tier_2",
    "rationale": "[razón por blast radius + reversibilidad]",
    "classified_at": "[ISO8601]",
    "classified_by": "[workflow/agente]"
  }
}
```

- Todo módulo `in_progress`, `ready_for_validation`, `blocked` o `completed` debe tener entrada en `work_tiers`.
- `tier_0` no puede llegar a `completed` como módulo del proyecto; antes debe reclasificarse y pagar Tier 1 o Tier 2.
- `tier_2` requiere ADR, evaluación de impacto, rollback probado, aprobación explícita y revisión de segundo paso antes de cierre.
- La aprobación Tier 2 puede reutilizarse mediante `APPROVE_RECEIPT` solo si el
  fingerprint de scope+ACs, riesgos y ADRs sigue vigente; cambios destructivos,
  dependencias nuevas y ampliaciones de scope exigen aprobación separada.
- La jerarquía y clasificador canónicos viven en `GUARDRAILS_MASTER.md` → "Graduación de Rigor por Tier".

### 1. Estados válidos de `module_statuses`

```
not_started → in_progress → ready_for_validation → completed
                    ↕
                 blocked
```

### 2. Transiciones autorizadas

| Transición | Comando autorizado | Condición |
|---|---|---|
| `not_started → in_progress` | `/scope-siguiente`, `/nuevo-proyecto`, `/migrar-proyecto` | Módulo sin bloqueos |
| `in_progress → blocked` | `/clarificar`, `/nuevo-bug` (crítico, o mayor con `affects_ac: true`), `/cierre-modulo` (reconciliación) | Ambigüedad o bug crítico |
| `blocked → in_progress` | resolución documentada (CLAR resuelta o bug crítico cerrado) | — |
| `in_progress → ready_for_validation` | `/cierre-modulo` Step 0 (interno, agente) | Implementación completa |
| `ready_for_validation → completed` | `/cierre-modulo` Step 5 (EXCLUSIVO) | ACs 100% con evidencia, 0 bugs críticos, 0 CLARs bloqueantes |

### 3. Transiciones PROHIBIDAS

- `blocked → completed`
- `in_progress → completed` (debe pasar por `ready_for_validation`)
- Cualquier transición no listada arriba.

### 4. Razones válidas de `closure_rejections`

`critical_bug_exists | clar_pending | ac_incomplete | ac_evidence_missing | module_specs_missing | data_model_missing | api_contract_missing`

Política: ≥ 2 rechazos del mismo módulo en el sprint actual → `/cierre-modulo` bloquea el cierre y recomienda organizar un módulo "Estabilización" sin cerrar el módulo rechazado. Los rechazos por gates duros no se convierten en deuda técnica. Se limpian solo al cerrar con éxito.

### 5. Eventos de estado fuera de un workflow

Se mutan directamente con `mutate_state` (FW-226 retiró `/update-state`): bug resuelto → `resolve_bug`; clarificación resuelta → `resolve_clarification`; decisión sin cambio de código → `register_decision` (con `expertise`) o `supersede_decision` si reemplaza una ADR; hito → `update_last_step`.

### 6. Puertos de desarrollo

- Usa los puertos que declare el proyecto en `_project/10_arch_constraints.md`.
- Si el equipo mantiene un registro de puertos, consulta el registro confirmado
  para asignar uno libre. Si falta esa información y el proyecto la necesita,
  acuerda el puerto antes de iniciar el servicio.
- Lee el puerto de una variable de entorno con el valor local documentado como
  predeterminado. Cambia un puerto de producción solo con aprobación.

---

> Las reglas de código, base de datos y dominio específicas del proyecto
> se declaran en `_project/10_arch_constraints.md` al inicializar el proyecto.
