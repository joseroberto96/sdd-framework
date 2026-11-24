# Entrevistar

Entrevista proactiva antes de módulos nuevos, cambios estructurales o requisitos ambiguos.

## Cuándo usar
- Módulo nuevo con lógica de negocio no trivial.
- Cambio estructural (migración, refactor de interfaz pública, nueva integración).
- Requisito ambiguo o con más de una interpretación válida.
- Cualquier módulo que toca auth, roles, datos sensibles o flujos de dinero/operación crítica.

## Pasos

1. CARGAR CONTEXTO MÍNIMO
   Leer en paralelo:
   - `_project/03_project_state.json`
   - `_project/04_active_scope.md`
   - `_project/08_module_acceptance.md`
   - `_project/03_invariants.md` si existe — para saber qué reglas, flujos y dominio ya están capturados y no volver a preguntar lo que ya está definido
   - `_project/09_domain_profile.md` si existe
   - `_project/03_contracts.md` si el módulo toca interfaces existentes
   - `_project/03_arch_decisions.md` si hay decisiones previas relevantes

2. ENTREVISTA ESTRUCTURADA
   Hacer las preguntas aplicables en UN solo bloque (no en mensajes separados).
   Omitir las que ya están respondidas en el contexto.

   **Bloque base — todo módulo:**
   ```
   Para alinear requisitos del módulo [nombre] necesito:
   1. ¿Qué debe poder hacer el usuario al final de este módulo? (resultado observable)
   2. ¿Qué casos borde o situaciones inusuales deben manejarse?
   3. ¿Qué pasa si la operación falla a mitad? ¿El usuario puede reintentar?
   4. ¿Hay términos del dominio que deba entender antes de diseñar? (nombres, reglas, invariantes)
   5. ¿Qué NO debe hacer este módulo? (límites explícitos del scope)
   ```

   **Bloque adicional — si el módulo toca auth/roles/permisos:**
   ```
   6. ¿Quién puede ver estos datos? ¿Quién puede modificarlos?
   7. ¿Hay roles distintos con acceso diferente a la misma operación?
   8. ¿Qué pasa si un usuario sin permiso intenta acceder?
   ```

   **Bloque adicional — si el módulo persiste o migra datos:**
   ```
   9. ¿Qué pasa con los datos existentes cuando se aplica este cambio?
   10. ¿Hay datos que no deben modificarse nunca (inmutables)?
   11. ¿Se necesita historial o auditoría de cambios?
   ```

   **Bloque adicional — si el módulo tiene UI:**
   ```
   12. ¿Qué ve el usuario cuando no hay datos todavía? ¿Y cuando hay un error?
   13. ¿Hay feedback inmediato requerido (spinner, mensaje, redireccionamiento)?
   ```

   **Bloque adicional — si el módulo tiene lógica de negocio no trivial o no está en 03_invariants.md:**
   ```
   Para capturar las reglas que nunca deben cambiar:
   14. ¿Hay una condición que debe cumplirse SIEMPRE en este proceso, sin excepción?
       (ej: "un pedido no puede despacharse sin stock confirmado", "un peso solo cierra si tiene pesador asignado")
   15. ¿Hay un paso que NUNCA puede saltarse u omitirse, aunque parezca conveniente técnicamente?
   16. ¿Cómo fluyen los datos en este módulo? Describir en pasos:
       entra [qué dato] desde [dónde] → se transforma en [qué] → sale hacia [dónde]
   17. ¿Qué términos usa el negocio para nombrar las cosas de este módulo que NO deben renombrarse en el código?
       (ej: en el negocio se dice "Cosecha", no "Lote" — ese nombre tiene que quedar igual en el sistema)
   ```

   Esperar respuesta antes de continuar.

3. ECO DE VERIFICACIÓN
   Resumir en bullets lo entendido:
   - Resultado observable del módulo.
   - Casos borde identificados.
   - Comportamiento ante fallo.
   - Restricciones de acceso (si aplica).
   - Límites de scope.
   - Reglas de negocio invariantes identificadas: [lista o ninguna].
   - Flujo de datos capturado: [descripción en pasos o "ninguno nuevo"].
   - Términos del dominio que no deben renombrarse: [lista o ninguno].
   - Preguntas sin respuesta → registrar como CLAR.

   Si el usuario corrige algo, incorporar antes de continuar.

4. REGISTRAR ARTEFACTOS
   - Actualizar `_project/08_module_acceptance.md` con ACs derivados de las respuestas.
   - Si surgieron términos nuevos: registrar en `_project/09_domain_profile.md` (lenguaje ubicuo).
   - Si hay decisión de diseño no obvia: proponer ADR en `_project/03_arch_decisions.md`.
   - Si quedaron preguntas sin respuesta bloqueantes: registrar en `_project/07_pending_clarifications.md` con `/clarificar`.
   - **Si surgieron reglas de negocio invariantes (Q14-Q15):** agregar como RN-XXX en `_project/03_invariants.md` sección "Reglas de negocio". Si el archivo no existe, crearlo desde el template.
   - **Si se describió un flujo de datos (Q16):** agregar en `_project/03_invariants.md` sección "Flujos de datos" con la secuencia canónica y su invariante.
   - **Si surgieron términos del dominio que no deben cambiar (Q17):** agregar en `_project/03_invariants.md` sección "Dominio — lenguaje ubicuo".
   - Mostrar al usuario qué se agregó a `03_invariants.md` y pedir confirmación antes de guardar. Este documento es canónico — no se guarda sin aprobación.

5. SALIDA
   ```
   Entrevista completada:
   - ACs registrados: [N]
   - CLARs abiertas: [N o ninguna]
   - Términos de dominio nuevos: [lista o ninguno]
   - ADR propuesto: [sí/no]
   - Invariantes registrados en 03_invariants.md:
       · Reglas de negocio: [RN-XXX, ... | ninguna nueva]
       · Flujos de datos: [nombre del flujo | ninguno nuevo]
       · Dominio/lenguaje ubicuo: [términos | ninguno nuevo]
   ```

   Siguiente: /ciclo <instrucción concreta: detallar el módulo con lo decidido en la entrevista>
   Modelo: <strong|standard|limited> · Esfuerzo: <🟢 bajo|🟡 medio|🟠 alto|🔴 xhigh> (<tier>)
   No implementar mientras queden CLARs bloqueantes sin respuesta.
