---
description: Único workflow autorizado para modificar 03_invariants.md — requiere razón documentada y aprobación explícita del usuario
---

# Actualizar Invariante

Este workflow es el **ÚNICO medio autorizado** para modificar `_project/03_invariants.md`.
El AI **nunca** debe editar ese archivo directamente fuera de este workflow.

## Cuándo usar
Solo cuando una regla de negocio, flujo de datos o concepto de dominio genuinamente cambió en el negocio —
no cuando el AI "cree" que debería cambiar por conveniencia técnica.

## Pasos

1. LEER ESTADO ACTUAL
   Leer `_project/03_invariants.md` completo.
   Identificar la sección y el invariante específico que se quiere modificar:
   - Sección 1 (Dominio): entidad o término de lenguaje ubicuo
   - Sección 2 (Reglas de negocio): RN-XXX específica
   - Sección 3 (Flujos de datos): flujo y paso afectado

2. PRESENTAR PROPUESTA DE CAMBIO
   Mostrar al usuario en formato explícito:
   ```
   PROPUESTA DE CAMBIO DE INVARIANTE
   ─────────────────────────────────
   Sección: [Dominio | Reglas de negocio | Flujos de datos]
   Invariante actual:
     [texto completo del invariante actual]
   Cambio propuesto:
     [texto nuevo exacto]
   Razón:
     [por qué este invariante ya no refleja la realidad del negocio]
   Impacto en módulos existentes:
     [lista de módulos que implementan este invariante]
   ```
   Esperar respuesta explícita del usuario.

3. GATE DE APROBACIÓN
   Respuestas válidas: "apruebo", "confirmo", "sí, apruebo el cambio" o equivalente inequívoco.
   Respuestas insuficientes: "ok", "sí", "dale", "adelante" → pedir confirmación más explícita.

   Si el usuario NO confirma → STOP. No modificar el documento bajo ninguna circunstancia.

4. REGISTRAR Y MODIFICAR
   Solo con aprobación explícita:
   - Modificar la sección correspondiente en `_project/03_invariants.md`.
   - Actualizar la fecha en el encabezado del documento.
   - Agregar entrada al "Registro de cambios aprobados":
     ```
     | [YYYY-MM-DD] | [Sección] | [resumen del cambio] | [razón] | [usuario] |
     ```
   - Si el cambio afecta `_project/03_contracts.md` o `_project/10_arch_constraints.md`:
     indicar qué debe actualizarse en esos documentos.

5. BARRIDO DE MÓDULOS ACEPTADOS (obligatorio)
   Antes de emitir el aviso, recorrer TODOS los módulos `completed` en `03_project_state.json → module_statuses` y contrastar sus ACs en `08_module_acceptance.md` contra el invariante nuevo o modificado.
   - Para cada módulo `completed` cuyo comportamiento o ACs dependan del invariante tocado: marcarlo como candidato a reapertura y listarlo explícitamente.
   - Un invariante nuevo puede invalidar retroactivamente un módulo ya aceptado; no asumir que "completed" significa "a salvo".
   El `framework_doctor` además advierte (WARN) mientras `03_invariants.md` difiera de HEAD y existan módulos `completed`, como red de seguridad.

6. AVISO DE IMPACTO
   Emitir:
   > ⚠️ Invariante modificado. Módulos `completed` a re-validar: [lista del paso 5].
   > Ejecutar `/cambio-seguro` en cada uno antes de continuar desarrollo.
   > Si un módulo ya no cumple el invariante, reabrirlo vía `mutate_state` action
   > `reopen_module` con `reason` citando el invariante (TL FW-006: es el único
   > camino legal `completed → in_progress`; invalida los receipts de cierre y
   > exige `/cambio-seguro` antes de tocar el código existente).
   > Siguiente: /ciclo Revisar el impacto del invariante modificado en los módulos completed de la lista y re-validarlos.
   > Modelo: <strong|standard|limited> · Esfuerzo: <🟢 bajo|🟡 medio|🟠 alto|🔴 xhigh> (<tier>)

La interfaz, los logs y los textos para el usuario van en español.
