# Decisiones de arquitectura

### ADR-012 — Especificaciones formales

El modelo de datos y el contrato HTTP se guardan en `_project/02_technical_plan/`.
El alcance y los criterios de aceptación indican cuándo corresponde consultar cada uno.

### ADR-019 — Entradas por entorno

El manifiesto es la fuente de los comandos. Cada integración expone la forma de
invocación que admite su entorno, con los workflows por archivo como respaldo.

### ADR-079 — Abandono de módulos

Los módulos abandonados conservan su historial y liberan el alcance operativo.
`reassign_bug_module` permite reasignar bugs a un módulo activo. Las operaciones
`add_closure_rejection`, `import_markdown_entities`, `seed_snapshot`,
`resolve_clarification` y `add_work_tier` conservan sus propios contratos.
