---
description: Crea un diseño técnico ligero para el MVP con alternativas, trade-offs, contratos y plan por módulos
---

# Tech Design MVP

Usar despues de `/prd-mvp` o antes de implementar un hito con impacto tecnico.

## Objetivo

Convertir el MVP en un plan tecnico ejecutable sin sobredisenar: arquitectura, contratos, decisiones, alternativas descartadas, riesgos y plan de pruebas.

## Pasos

1. LEER CONTEXTO
   Leer:
   - `_project/01_product_requirements.md`
   - `_project/03_project_state.json` (incluye `module_specs` para clasificación heredada)
   - `_project/03_contracts.md` (índice narrativo de contratos no-HTTP)
   - `_project/02_technical_plan/data_model.md` (modelo formal del dominio, Mermaid)
   - `_project/02_technical_plan/api_contract.yaml` (contrato HTTP/REST formal, OpenAPI 3.1)
   - `_project/03_arch_decisions.md`
   - `_project/03_invariants.md` si existe — las reglas de negocio, flujos de datos y dominio que el diseño no puede violar
   - `_project/08_module_acceptance.md`
   - `_project/10_arch_constraints.md`
   - `_project/11_file_dependencies.md`
   - `_project/20_builder_profile.md` si existe
   - `_project/21_knowledge_map.md` si existe

2. ACTIVAR SKILLS CORE Y GATE THINK
   Ejecutar el Gate THINK de `GUARDRAILS_MASTER.md` (raíz del proyecto) antes de diseñar:
   - ¿El tier de rigor de cada módulo propuesto está clasificado por blast radius + reversibilidad?
   - ¿El Risk Surface Map identifica qué dimensiones activa esta tarea?
   - ¿El ADR está pre-comprometido si la decisión afecta >1 módulo o es no obvia?
   - ¿Hay riesgo de N+1, query sin bound, operación costosa o async sin contrato?
   - ¿Los 5 estados de UI están identificados si el módulo tiene interfaz?
   - Si `03_invariants.md` existe: ¿el diseño propuesto respeta todas las reglas de negocio, secuencias de flujo de datos y términos de dominio declarados?
   Si algún item bloquea el diseño, resolverlo antes de continuar al paso 3.

   Activar mentalmente:
   - `.agent/skills/agile_product.md`
   - `.agent/skills/architecture_core.md`
   - `.agent/skills/security_core.md` si hay auth/datos sensibles
   - `.agent/skills/quality_testing_core.md`

3. DISEÑO LIGERO
   Proponer y registrar:
   - Arquitectura MVP.
   - Modulos y responsabilidades.
   - Contratos publicos necesarios.
   - Datos/modelos principales.
   - Riesgos tecnicos.
   - Plan de pruebas.
   - Alternativas consideradas y por que se descartan.
   - Integridad de datos: ¿los modelos tienen constraints e integridad referencial donde corresponde? ¿Hay timestamps en registros con trazabilidad critica? ¿Las migraciones o cambios de esquema seran idempotentes? Declarar decisiones en 03_arch_decisions.md.
   - Resiliencia operacional (si aplica segun 10_arch_constraints.md): ¿cual es la estrategia de backup, aunque sea manual? ¿Como se recupera el sistema tras un fallo abrupto? ¿Necesita instrucciones para usuario no tecnico?
   - Riesgos de codigo IA: para cada modulo, marcar explicitamente si hay calculos o agregaciones sin fuente de datos trazable, o manejadores de error que podrian silenciar fallos criticos. Documentar mitigaciones en 03_arch_decisions.md.
   - Atributos de calidad (NFRs): declarar explicitamente para este MVP — no para un futuro hipotetico. Para cada atributo, elegir un nivel o declararlo diferido con justificacion:
       · Rendimiento: ¿tiempo de respuesta aceptable para la operacion principal? (< 500ms / < 2s / no critico — justificar si se difiere)
       · Disponibilidad: ¿downtime aceptable? (horas / minutos / zero tolerance — justificar si se difiere)
       · Escalabilidad: ¿usuarios o volumen concurrente esperado? (< 10 / 10-100 / > 100 / diferido conscientemente)
       · Mantenibilidad: ¿quien mantendra el sistema y con que frecuencia? (mismo dev / equipo / cliente no tecnico)
       · Seguridad: nivel de sensibilidad de los datos (PII / financieros / operativos internos / no sensible)
     Registrar en 10_arch_constraints.md. Un atributo sin declarar es una decision tomada por accidente.
   - Stack y camino de escalado (4.1.0 endurecido, FW-133): para cada pieza del
     stack (backend, frontend, base de datos, auth), declarar tres campos
     obligatorios, citando los NFRs recién declarados arriba y
     `_project/20_builder_profile.md` si existe — nunca "es lo estándar" como
     única razón:
       · Elección + porqué: qué NFR concreto (escalabilidad, mantenibilidad,
         criticidad de datos) y qué perfil de builder la justifican.
       · Qué se rompe a ×10 y ×100 de volumen respecto al NFR de escalabilidad
         declarado.
       · Costura de escape: qué interfaz aísla la pieza que habría que
         reemplazar (p. ej. persistencia detrás de un repositorio → cambiar el
         motor de base de datos sin tocar el dominio). Si una pieza no tiene
         costura razonable, declararla explícitamente **puerta de una sola
         dirección** con su coste de reemplazo — no dejarla implícita.
     Registrar como ADR vía `register_decision` y en la tabla "Camino de
     escalado" de `10_arch_constraints.md`. Esto no es sobreingeniería
     anticipada: el MVP se construye igual de mínimo, solo queda explícito por
     dónde escalar después sin rehacer la pieza entera.
   - Modelo de entidades: antes del primer modulo que persiste datos, declarar las entidades del dominio con atributos, tipos y relaciones. Registrar en `_project/02_technical_plan/data_model.md` (Mermaid + secciones por entidad). 03_contracts.md NO duplica esta información — solo conserva contratos de uso (constructores, validadores, helpers) cuando aplique. Cambios posteriores al modelo requieren aprobacion explicita como cambio DESTRUCTIVO.
   - Contrato HTTP/REST: si el MVP expone endpoints HTTP, declararlos en `_project/02_technical_plan/api_contract.yaml` con request, response y códigos de error. 03_contracts.md mantiene línea-puntero al YAML por endpoint (no duplica el detalle). Si el módulo no expone HTTP, no se toca el YAML.
   - Estrategia de errores: clasificar errores en FATAL (detiene la operacion, visible al usuario), RECUPERABLE (reintento/fallback + log), ESPERADO (validacion de usuario, mensaje claro sin traza interna), SILENCIOSO-OK (edge case aceptado con justificacion). Registrar en 03_arch_decisions.md. Todo modulo debe adherirse a esta clasificacion.
   - Estrategia de observabilidad: ¿donde quedan registrados los errores no esperados? ¿que operaciones criticas producen un log trazable? Si la respuesta es "ningun lado", declararlo explicitamente como decision aceptada con su riesgo documentado. Registrar en 03_arch_decisions.md.

4. ACTUALIZAR ARTEFACTOS
   - Crear o actualizar un archivo en `_project/02_technical_plan/` (plan de fase `phase_NN.md`).
   - Actualizar `_project/03_contracts.md` con contratos no-HTTP (CLI, bibliotecas, eventos) y punteros a las specs formales.
   - Actualizar `_project/03_arch_decisions.md` si hay decisiones no obvias.
   - Actualizar `_project/11_file_dependencies.md` si ya se conocen rutas previstas.
   - Actualizar `_project/04_active_scope.md` con el primer modulo viable si el usuario lo aprueba.
   - Si Q17 = si (sistema genera reportes): actualizar `_project/10_arch_constraints.md` con la seccion de integridad de reportes si esta vacia.
   - Actualizar `_project/10_arch_constraints.md` con la seccion de atributos de calidad (NFRs) declarados.
   - Actualizar `_project/10_arch_constraints.md` → tabla "Camino de escalado" con la decisión de stack del paso 3 (elección, ruptura a escala, costura de escape).
   - Si se declaro estrategia de errores u observabilidad: registrar en `_project/03_arch_decisions.md`.

   ESPECIFICACIONES FORMALES (ADR-013):

   `tech-design-mvp` **propone** la clasificación de specs por módulo en el documento de diseño, pero **no escribe** `_project/03_project_state.json → module_specs[módulo]`. La escritura efectiva la hace `/scope-siguiente` con `classified_by: "scope-siguiente"` cuando el módulo ingresa al scope activo, o `/enriquecer-ticket` al refinar con `classified_by: "enriquecer-ticket"`. Si por excepción operativa hay que escribir desde este workflow, usar `classified_by: "manual"` para no falsificar el origen (ADR-013).

   Para cada módulo declarado en el diseño, proponer:

   - `data_model`:
     · `required` si el módulo crea, persiste, lee o transforma entidades del dominio → declarar entidades y relaciones en `_project/02_technical_plan/data_model.md` (Mermaid + secciones por entidad).
     · `n/a` si el módulo es UI pura, CLI interna, docs, config o scripts → registrar `justification` breve en `module_specs[módulo].justification`.

   - `api_contract`:
     · `required` si el módulo expone o modifica endpoints HTTP/REST → declarar paths, schemas y errores en `_project/02_technical_plan/api_contract.yaml`. Añadir línea-puntero correspondiente en `03_contracts.md`.
     · `n/a` si el módulo no expone HTTP → registrar `justification` en `module_specs[módulo].justification`.

   Mientras `module_specs[módulo]` esté incompleto, `/cierre-modulo` bloqueará el cierre del módulo (Spec Gate 3.7).

5. GATE ANTI-REGRESION
   Si el proyecto ya tiene codigo existente, ejecutar `.agent/workflows/cambio-seguro.md` antes de proponer cambios sobre archivos existentes.

6. REVISIÓN PREFLIGHT — DOBLE CHECK DEL DISEÑO ANTES DE CODE
   Ejecutar `.agent/workflows/preflight-review.md` sobre el diseño técnico, primer módulo recomendado y plan de pruebas.
   - Si `PLAN_REVIEW: fail`, corregir el diseño o ejecutar el workflow recomendado antes de pedir aprobación para CODE.
   - Con dispatcher sin aislamiento read-only, usar revisor distinto y declarar
     `INDEPENDENT_SUBAGENT_NO_ISOLATION`; usar `SECUENCIAL_NO_AISLADO` solo sin
     dispatcher.
   - El preflight no escribe `_project/` ni reemplaza la aprobación del usuario; solo detecta omisiones y gates faltantes.

7. GATE APPROVE — BLOQUEO ANTES DE CODE
   Aplicar `.agent/skills/builder_language_core.md`: presentar el diseño como
   impacto cotidiano, recomendacion y opciones; dejar el detalle tecnico al final.
   Presentar el diseño y esperar aprobación explícita del usuario antes de que comience cualquier implementación.
   Chequear:
   - ¿El alcance está acotado y el usuario lo revisó?
   - ¿Los riesgos identificados en el Gate THINK son aceptables?
   - ¿No hay dependencias nuevas sin aprobación?
   - ¿El ADR (si aplica) está aprobado?
   Sin respuesta afirmativa del usuario: NO avanzar a CODE. Esperar.

8. SALIDA
   ```markdown
   Diseño tecnico MVP listo:
   - Plan:
   - Contratos:
   - Riesgos:
   - Primer modulo recomendado:
   - Verificacion:
   [APPROVE] ¿Aprobamos este diseño para iniciar implementación?
   ```

   Siguiente: /ciclo Abrir el primer módulo del plan y llevarlo a cierre.
   Modelo: <strong|standard|limited> · Esfuerzo: <🟢 bajo|🟡 medio|🟠 alto|🔴 xhigh> (<tier>)

## Reglas

- No introducir tecnologia nueva sin declararla en `03_project_state.json -> dependencies` y pedir aprobacion.
- Preferir diseno simple y extensible sobre arquitectura prematura.
- Registrar alternativas solo cuando evitan repetir debates importantes.
- Si una decision depende del usuario, bloquear el modulo relacionado en vez de asumir.
- **Anti-fabricación:** entidades de `data_model.md`, endpoints de `api_contract.yaml`, módulos y ACs solo se declaran si están justificables desde `_project/01_product_requirements.md` y/o respuestas explícitas del usuario. Ante un hueco, registrar `PRD-CLAR-NNN` en `_project/07_pending_clarifications.md` y bloquear el módulo afectado. Nunca inferir desde dominios análogos ni desde "buenas prácticas".

La interfaz, los logs y los textos para el usuario van en español.
