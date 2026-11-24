# Refactorizar arquitectura

Auditoría proactiva para profundizar módulos, reducir acoplamiento y evitar entropía.

## Pasos

1. Leer intención: `03_project_state.json`, `09_domain_profile.md`, `03_arch_decisions.md`, `03_contracts.md`, `10_arch_constraints.md`, `11_file_dependencies.md`.
2. Inspeccionar solo el área objetivo, no el repositorio completo.
3. Ejecutar `.agent/workflows/cambio-seguro.md` en modo auditoría:
   - Construir inventario `PRESERVAR` antes de proponer cambios.
   - Identificar contratos, rutas, comandos, flujos de UI, validaciones, permisos y formatos de datos que no pueden cambiar.
4. Buscar duplicación, interfaces superficiales, detalles internos filtrados, nombres fuera del lenguaje ubicuo y dependencias sin contrato.
5. Proponer máximo 3 refactors ordenados por impacto.
   Cada propuesta debe incluir:
   - Tipo de cambio: `ADITIVO`, `COMPATIBLE` o `DESTRUCTIVO`.
   - Funcionalidad preservada.
   - Riesgo de regresión.
   - Tests/checks que se ejecutarán.
6. Ejecutar `.agent/workflows/preflight-review.md` sobre la propuesta aprobable antes de CODE.
   - Si `PLAN_REVIEW: fail`, corregir la propuesta o ejecutar el workflow recomendado.
   - Con dispatcher sin aislamiento read-only, usar revisor distinto y declarar
     `INDEPENDENT_SUBAGENT_NO_ISOLATION`; usar `SECUENCIAL_NO_AISLADO` solo sin
     dispatcher.
7. Implementar solo con aprobación.
   - Una aprobación genérica de "refactorizar" solo autoriza cambios `COMPATIBLE`.
   - Cualquier cambio `DESTRUCTIVO` requiere aprobación explícita separada con impacto descrito.
8. Actualizar contratos/ADRs/dependencias si cambian.
9. Ejecutar la verificación anti-regresión de `.agent/workflows/cambio-seguro.md` antes de cerrar.
10. Cerrar con:
   Siguiente: /ciclo Validar el refactor contra la arquitectura y el gate anti-regresión.
   Modelo: <strong|standard|limited> · Esfuerzo: <🟢 bajo|🟡 medio|🟠 alto|🔴 xhigh> (<tier>)
