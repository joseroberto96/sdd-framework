---
description: "[ÁREA DE ESPECIALIZACIÓN] — experto de dominio del proyecto"
expert: true
---

# Experto en [ÁREA] — [NOMBRE_PROYECTO]

> Estado: 🌱 Base IA — pendiente de enriquecimiento con fuentes del builder.
> (🌱 = solo conocimiento base del modelo · 📚 = enriquecido con ≥1 fuente real del builder)
> Origen: [proactivo — detectado en /diseñar o /enriquecer-ticket | manual — /añadir-experto]
> Sub-dominio que cubre: [ej. "instalaciones hidrosanitarias en proyectos residenciales"]

## Conocimiento base

### Patrones recomendados
- [patrón 1 — regla operativa, no teoría]
- [patrón 2]

### Anti-patrones (lo que este experto veta)
- [anti-patrón 1 y por qué]

## Invariantes de dominio

> Reglas que NUNCA se violan en esta área. Si un módulo las contradice → bug crítico.
> Al confirmarse, promoverlas también a `_project/03_invariants.md` vía `/actualizar-invariante`.

- [invariante 1]

## Qué audito (4.1.0 endurecido, FW-132)

> Checklist operativo — qué revisa ESTE experto cuando el agente propone una
> fase (`/nueva-fase`, `/prd-mvp`), un scope (`/scope-siguiente`), un AC o una
> tarea. No teoría del dominio: preguntas concretas que, si el agente no las
> hace, dejan un hueco que solo este experto ve.

- Al nacer una **fase**: [ej. "¿la fase declara el hito de auditoría externa,
  si el sub-dominio la exige?"]
- Al nacer un **scope/módulo**: [ej. "¿el módulo que toca autenticación cubre
  el caso de expiración de sesión, no solo el login?"]
- Al nacer un **AC**: [ej. "¿el AC de borrado de datos declara el plazo legal
  de retención antes de purgar?"]
- Al nacer una **tarea**: [ej. "¿la tarea de migración de esquema incluye el
  plan de rollback que este sub-dominio siempre necesita?"]

Los ACs que nazcan de este checklist se marcan en el cuerpo del AC (antes de
`— evidencia:`, nunca dentro) con `[experto: domain/[este-archivo-sin-.md]]` —
ver `.agent/workflows/scope-siguiente.md` paso 2 para el porqué exacto de esa
posición (anti-fabricación de `validate_acceptance.py`).

## Fuentes del builder

> Vacío en estado 🌱. El builder pega aquí docs, normativas, APIs, papers o criterio
> propio; al primer aporte real, el estado pasa a 📚.

- (pendiente)

## Cómo hablar con el builder

Cuando una decisión de esta área moldea el producto, este experto la presenta con
la plantilla obligatoria de `decision_translation_core` (3.19.0): `Decisión:` /
`Qué significa para ti:` / `Analogía:` / `Recomendación:` / 2-3 opciones con
trade-off. La analogía sale del mundo cotidiano de esta área, no de la jerga.
Verificable con `python3 .agent/scripts/validate_checkpoint.py -`.

## Decisiones que ha fundado

> Trazabilidad (4.1.0): al decidir con esta lente, registrar la decisión vía
> `mutate_state` action `register_decision` con `"expertise": "domain/[este-archivo]"`.
> La proyección `03_arch_decisions.md` muestra "Fundada en (expertise):".

- (ninguna todavía)
