# Planes Técnicos y Specs Formales

Esta carpeta contiene dos clases de artefactos técnicos del proyecto:

1. **Planes por fase** — `phase_NN.md` (semántica original v3).
2. **Specs formales** — `data_model.md` (Mermaid) y `api_contract.yaml` (OpenAPI 3.1). Introducidos en la evolución v3.x descrita en `.agent/docs/framework_history.md` y ADR-012.

Las dos clases coexisten sin solaparse: los planes de fase organizan **qué se construye y cuándo**; las specs formales declaran **cómo se modela el dominio y qué endpoints expone el sistema** a nivel de contrato.

## Estructura

```
02_technical_plan/
├── phase_01.md         ← Plan de la fase 1
├── phase_02.md         ← Plan de la fase 2
├── ...
├── data_model.md       ← Modelo de datos formal (Mermaid)
└── api_contract.yaml   ← Contrato HTTP/REST formal (OpenAPI 3.1)
```

## Planes por fase — `phase_NN.md`

### Cuándo se usa
- `/scope-siguiente` lee el plan de la fase activa para proponer el siguiente módulo y sus ACs.
- `/nueva-fase` verifica si existe el plan de la siguiente fase antes de iniciarla.

### Formato
```markdown
# Plan Técnico — Fase [N]: [Nombre de la fase]

## Objetivo de la fase
[Qué se construye y por qué en esta fase]

## Módulos
### Módulo 1 — [Nombre]
- **Objetivo:** [qué resuelve]
- **Dependencias:** [módulos o datos previos que necesita]
- **Estimación:** [Low / Medium / High]

### Módulo 2 — [Nombre]
...

## Criterios de completitud de la fase
- [ ] [condición para dar la fase por terminada]
- [ ] [condición de aceptación con el cliente]
```

### Si no existe el plan de una fase
`/scope-siguiente` y `/nueva-fase` lo detectan y te pedirán que lo definas antes de continuar. Puedes crearlo manualmente o pedirle al agente que lo genere a partir del product spec.

## Specs formales — `data_model.md` y `api_contract.yaml`

### Naturaleza
- `data_model.md` documenta entidades, relaciones y restricciones del dominio con diagramas Mermaid.
- `api_contract.yaml` documenta el contrato HTTP/REST con OpenAPI 3.1 — request, response y errores por endpoint.

### Obligatoriedad condicional (ADR-013)
- **`data_model.md` requerido** cuando el módulo crea, persiste, lee o transforma entidades del dominio.
- **`api_contract.yaml` requerido** cuando el módulo expone o modifica endpoints HTTP/REST.
- **No aplica** para UI pura, CLI interna, documentación, scripts y configuración. La justificación se registra en `_project/03_project_state.json → module_specs[module_id]`.

### Quién las escribe
- `/diseñar` paso tech-design-mvp (cuando se introduzca formalmente la integración).
- `/enriquecer-ticket` al refinar un módulo concreto.
- `/migrar-proyecto` al adoptar el framework sobre código existente.

### Quién las lee y valida
- `/cambio-seguro` — inventario PRESERVAR de entidades y endpoints.
- `/cierre-modulo` — Spec Gate condicional.
- `/validate-arch` — coherencia código↔contrato.
- `/enriquecer-ticket` — referencia formal al detallar la tarea.
- `verificar-ejecutable` — deriva pruebas curl/httpie desde `api_contract.yaml`.
- Skills `prompt_lean` y `spec_contracts` — lectura selectiva por módulo activo.

### Estado en plantilla
En modo `framework_mode=template` ambos archivos existen como esqueletos:
- `data_model.md` con la sección "Estado de plantilla" indicando `Modo: PLANTILLA — sin entidades reales`.
- `api_contract.yaml` con `paths: {}` y `components.schemas: {}`.

`/nuevo-proyecto` y `/migrar-proyecto` los pueblan al inicializar.

### Coexistencia con `03_contracts.md`
`03_contracts.md` queda como **índice narrativo humano**. Para interfaces HTTP/REST mantiene una línea-puntero `→ ver 02_technical_plan/api_contract.yaml#paths./ruta`. Su contenido formal vive en el YAML. Interfaces no-HTTP (CLI internas, bibliotecas, event bus) siguen describiéndose narrativamente en `03_contracts.md`. La regla completa está en ADR-012.

## Referencia cruzada

- Resumen de evolución: `.agent/docs/framework_history.md`.
- Decisiones registradas: `.agent/docs/framework_arch_decisions.md` (ADR-011 a ADR-016).
- Historial del framework: `.agent/docs/framework_history.md`.
