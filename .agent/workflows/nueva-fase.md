---
description: Transición entre fases — verifica cierre completo, archiva scope, actualiza JSON y prepara la siguiente fase
---

Eres el agente de transición de fase.

Recibirás:
- @03_project_state.json
- @08_module_acceptance.md
- @04_active_scope.md

## Pasos

1. COMPROBAR QUE LA FASE ACTUAL ESTÁ CERRADA
   Leer @08_module_acceptance.md. Si algún módulo de la fase activa NO tiene el
   sello de cierre (`✅ Verificado por el agente (código + integración)`, o el
   antiguo `✅ Verificado con cliente`), parar y listar los módulos pendientes. No continuar.
   Después, listar los módulos de la fase sin `work_tiers[mod].builder_use`
   (uso real no confirmado por el builder, FW-275) y preguntar al builder si
   cierra la fase de todos modos. Es un aviso, no un bloqueo; no registrar
   `confirm_builder_use` salvo que el builder diga que usó el módulo.

1.5. GATE DE VALIDACIÓN ARQUITECTÓNICA
   Ejecutar `/validate-arch` sobre la fase que se cierra antes de archivarla.
   Comprueba que las restricciones de arquitectura, los contratos y las ADR son coherentes en todos los módulos.
   Si validate-arch da resultados FAIL:
   > ⚠️ Validación arquitectónica detectó problemas antes del archivado de fase. Resolver antes de continuar.
   Pasar al paso 2 solo cuando validate-arch dé todo PASS o el usuario acepte explícitamente excepciones documentadas.

2. ARCHIVAR EL SCOPE
   Añadir el contenido completo de @04_active_scope.md al final de @04_scope_archive.md
   (crear el fichero con una cabecera estándar si no existe):
   ```markdown
   ---
   ## Fase [N] — [nombre de la fase] — Archivada [fecha]
   [contenido completo de 04_active_scope.md]
   ```

2b. ARCHIVAR BUGS Y CRITERIOS DE ACEPTACIÓN
   Este paso evita saturar el contexto en proyectos largos.

   BUGS — `06_bug_fixes.md` es una **proyección read-only** del motor
   (FW-226, TL FW-121/171): NO se resetea ni se edita a mano — la siguiente
   mutación la regeneraría desde `03_project_state.json` y el guard de
   proyecciones bloquea contenido sin banner. Los bugs abiertos siguen vivos en
   el estado y se arrastran solos a la nueva fase.
   - Opcional, como snapshot histórico: copiar su contenido a
     `_project/06_bug_fixes_phase_[N].md` (archivo nuevo, no proyección).

   CRITERIOS DE ACEPTACIÓN — leer @08_module_acceptance.md:
   - Copiar el contenido completo a `_project/08_module_acceptance_phase_[N].md` (fichero nuevo)
   - Dejar @08_module_acceptance.md así:
     ```markdown
     # Criterios de Aceptación — [PROJECT_NAME]
     **Fase activa:** phase_0[N+1]
     _Criterios de fases anteriores en `08_module_acceptance_phase_[N].md`_
     ---
     ```

3. ACTUALIZAR EL ESTADO POR MCP
   - Llamar a `mutate_state` con la acción `set_phase` y {"current_phase": "phase_0[N+1]", "current_step": "inicio"}.
   - Llamar a `mutate_state` con la acción `update_last_step` y {"last_completed_step": "Fase [N] completada. Todos los módulos verificados con cliente."}.
   Reconciliar el estado de los módulos por MCP (NO editar a mano modules.*, module_statuses ni closure_rejections):
   - Para cada módulo de la fase que se cierra y está verificado: llamar a `mutate_state` con la acción `update_module_status` y {"id": "[módulo]", "status": "completed"}; lo mueve a modules.completed, fija su estado y purga sus closure_rejections automáticamente.
   - Para cada módulo conocido de la fase NUEVA: llamar a `mutate_state` con la acción `update_module_status` y {"id": "[módulo]", "status": "not_started"}; lo registra y lo coloca en modules.pending.
   (Las listas modules.*, module_statuses y closure_rejections las gestiona solo update_module_status; nunca editar 03_project_state.json a mano para esto.)
   Nota: el cierre formal de un módulo se hace normalmente con /cierre-modulo; nueva-fase solo reconcilia las listas en la frontera de fase.

4. PREPARAR EL ENTORNO DE LA FASE NUEVA
   Dejar @04_active_scope.md así:
   ```markdown
   # Scope Activo — [PROJECT_NAME]
   **Fase:** phase_0[N+1]
   **Sprint:** 1
   **Actualizado:** [fecha]
   ## Módulo actual: Por definir
   ### Notas
   Fase [N+1] iniciada. Ejecutar /scope-siguiente para definir el primer módulo.
   ```
   Nota (FW-132): la auditoría de expertos de dominio corre en `/prd-mvp` paso
   3.5 y `/scope-siguiente` paso 1.5 — no aquí, porque en este paso el scope
   de la fase nueva todavía no tiene contenido que auditar.
   Añadir a @08_module_acceptance.md:
   `--- ## === FASE [N+1] ===`

5. COMPROBAR EL PLAN TÉCNICO
   Comprobar si existe `_project/02_technical_plan/phase_0[N+1].md`.
   - Si existe: decir que está disponible para /scope-siguiente
   - Si no: recordar que hay que definirlo antes de empezar a implementar

5.5. DIGEST DE DECISIONES DE FASE (enseñanza)

Aplicar `decision_translation_core.md` (Modo C): por cada decisión técnica autónoma
relevante de la fase que cierra, **un párrafo máximo** — qué decidí, por qué (concreto)
y un ejemplo sencillo para aprender. Sin decisión que tomar.
Aplicar también `.agent/skills/builder_language_core.md`: traducir cada decisión
a impacto cotidiano antes del detalle tecnico.

6. CONFIRMAR
   ```
   ✅ Transición a phase_0[N+1] completada.
   Fase [N] archivada: [N] módulos verificados.
   Archivos creados: 06_bug_fixes_phase_[N].md · 08_module_acceptance_phase_[N].md
   Bugs arrastrados: [N abiertos] · Bugs cerrados: [N cerrados]
   ```

   Siguiente: /ciclo Definir el primer módulo de la fase nueva y construirlo con todos los gates.
   Modelo: <strong|standard|limited> · Esfuerzo: <🟢 bajo|🟡 medio|🟠 alto|🔴 xhigh> (<tier>)

   Sugerir abrir una ventana nueva para limpiar el contexto de la fase anterior.

La interfaz, los logs y los textos para el usuario van en español.
