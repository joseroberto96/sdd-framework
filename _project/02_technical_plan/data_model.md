# Modelo de Datos — [NOMBRE_PROYECTO]

> Artefacto formal del dominio del proyecto.
>
> **Lo escribe:** `/diseñar` (paso tech-design-mvp) y `/enriquecer-ticket` cuando el módulo activo toca entidades.
>
> **Lo lee:** `/cambio-seguro` (inventario PRESERVAR), `/cierre-modulo` (Spec Gate condicional), `/validate-arch` (coherencia código↔modelo), `/enriquecer-ticket`, skills `prompt_lean` y `spec_contracts`.
>
> **Obligatoriedad:** condicional (ADR-013). Requerido cuando el módulo crea, persiste, lee o transforma entidades del dominio. UI puras, CLI internas, docs, config y scripts marcan `module_specs[module].data_model = "n/a"` con justificación en `03_project_state.json`.

## Estado de plantilla

- **Modo:** PLANTILLA — sin entidades reales hasta `/nuevo-proyecto` o `/migrar-proyecto`.

## Diagrama ER

```mermaid
erDiagram
    %% Las entidades reales se declaran al inicializar el proyecto.
    %% Ejemplo eliminado intencionadamente para no introducir ruido en la plantilla.
```

## Entidades

<!--
Una sección por entidad cuando el proyecto se inicialice.

### NombreEntidad
- **Módulo propietario:** [nombre del módulo en project_state.json]
- **Campos:**
  - `id: tipo` — descripción
  - `campo: tipo` — descripción
- **Relaciones:** [lista de relaciones con otras entidades]
- **Restricciones:** [unicidad, índices, invariantes locales]
-->

## Restricciones e invariantes del modelo

<!--
Restricciones que el modelo impone y que `/cierre-modulo` y `/validate-arch`
deben respetar. No duplicar reglas de negocio que viven en `03_invariants.md`:
aquí solo restricciones estructurales del modelo de datos.
-->

## No aplica — registro por módulo

<!--
Si un módulo NO toca dominio, NO se documenta aquí.
La justificación vive en `03_project_state.json → module_specs[module_id].justification`.
Esta sección queda como índice opcional cuando convenga visibilidad humana.
-->
