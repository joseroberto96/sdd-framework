---
description: Genera o actualiza un PRD ligero enfocado en MVP, no-MVP y criterios verificables
---

# PRD MVP

Usar despues de `/descubrir-producto`, antes de dividir modulos o cuando el scope crece sin claridad.

## Objetivo

Crear un PRD suficiente para construir: problema, usuarios, flujos principales, MVP, no-MVP, criterios de exito y preguntas abiertas.

## Pasos

1. LEER CONTEXTO
   Leer:
   - `_project/01_product_requirements.md`
   - `_project/03_project_state.json`
   - `_project/04_active_scope.md`
   - `_project/08_module_acceptance.md`
   - `_project/09_domain_profile.md` si existe
   - `_project/20_builder_profile.md` si existe
   - `_project/21_knowledge_map.md` si existe

2. ECO DE VERIFICACIÓN
   Antes de escribir cambios grandes, mostrar:
   ```markdown
   Entiendo que el MVP es:
   - Usuario:
   - Problema:
   - Flujo principal:
   - Incluye:
   - No incluye:
   - Exito medible:
   - Dudas:
   ```
   Si hay dudas que cambian comportamiento, registrarlas en `_project/07_pending_clarifications.md`.

3. ACTUALIZAR PRD
   Actualizar `_project/01_product_requirements.md` con:
   - Resumen.
   - Usuarios y contexto.
   - MVP Incluye.
   - No Incluye por Ahora.
   - Historias/flujos principales.
   - Criterios de Exito.
   - Preguntas abiertas.

3.5. EXPERT GATE — auditoría proactiva a escala de fase (4.1.0 endurecido, FW-132)
   Leer `_project/09_domain_profile.md → Expertos activos`. Para cada experto
   cuyo sub-dominio toca el MVP recién escrito, cargar
   `.agent/skills/domain/[archivo].md → Qué audito` y ejercer su checklist a
   escala de fase — huecos en los flujos principales, MVP/no-MVP, o criterios
   de éxito que el checklist revela. Mismo formato de marca que
   `.agent/workflows/scope-siguiente.md` paso 1.5 (`[experto: domain/[archivo]]`
   en el cuerpo del AC, antes de `— evidencia:`). No bloquea aquí: si el
   builder difiere, registrar la propuesta en `09_domain_profile.md` como
   descartada con su razón.

4. DERIVAR ACCEPTANCE CRITERIA
   Para cada flujo MVP, agregar o actualizar `_project/08_module_acceptance.md` con ACs observables.
   Preferir formato EARS ("Cuando [disparador], el sistema debe [respuesta]" y
   variantes ubicua/estado/opcional/no-deseada — detalle en
   `.agent/workflows/enriquecer-ticket.md`): obligatorio en tier_2, recomendado
   en tier_1, libre en tier_0. Sin gate mecánico: es guía de formato.
   Los ACs que nazcan del EXPERT GATE (paso 3.5) llevan la marca de experto
   en el cuerpo del AC.

5. PROPONER MODULOS
   Si el usuario lo aprueba, proponer modulos pequenos:
   - `module_id`
   - valor observable
   - dependencia principal
   - primer test/check

   No marcar modulos como `completed`.

6. SALIDA
   Indicar:
   - PRD actualizado.
   - ACs creados/actualizados.
   - Preguntas bloqueantes.

   Cerrar con:

   Siguiente: /diseñar Continuar el diseño técnico.
   Modelo: <strong|standard|limited> · Esfuerzo: <🟢 bajo|🟡 medio|🟠 alto|🔴 xhigh> (<tier>)

## Reglas

- El PRD es vivo, no documento ceremonial.
- Separar siempre MVP de no-MVP.
- No inventar reglas de negocio si hay duda: registrar CLAR.
- Mantener historias en lenguaje de usuario, no de implementacion.
- **Anti-fabricación:** solo derivar de `_project/01_product_requirements.md` y de respuestas explícitas del usuario en esta sesión. Ante un hueco, registrar `PRD-CLAR-NNN` en `_project/07_pending_clarifications.md` y bloquear el paso afectado. Nunca inferir desde dominios análogos ni desde "buenas prácticas".

La interfaz, los logs y los textos para el usuario van en español.
