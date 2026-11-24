---
name: "spec-contracts"
description: "Lectura selectiva de specs formales (data_model.md y api_contract.yaml) por módulo activo, sin cargar el archivo completo. Complementa prompt_lean cuando el módulo toca dominio o expone HTTP."
---

<!-- GENERATED: framework wrapper; edit manifest/source, then run sync_claude_commands.py -->

# Spec Contracts — Lectura Selectiva de Specs Formales

**Activación:** invocada desde `/cambio-seguro` (inventario PRESERVAR), `/cierre-modulo` (Spec Gate 3.7), `/enriquecer-ticket` y `/validate-arch` cuando `module_specs[módulo].data_model = "required"` o `module_specs[módulo].api_contract = "required"`.

## Objetivo

<!-- CONTEXT_SOURCE: module_specs -->
Las specs formales se cargan de forma selectiva por el módulo activo. La
clasificación `data_model` y `api_contract` debe constar en `module_specs`; un
artefacto `required` o `done` activa únicamente sus entidades, endpoints y
schemas relacionados. `n/a` exige justificación explícita.
<!-- END_CONTEXT_SOURCE: module_specs -->

Cargar al contexto del agente solo las entidades del dominio y los endpoints que el módulo activo declara o consume — nunca el archivo completo. Evita inflar el contexto con specs de otros módulos.

---

## Paso 1 — Determinar alcance del módulo

Leer en paralelo:

- `_project/03_project_state.json → module_specs[módulo]`
- `_project/04_active_scope.md` — tarea activa y endpoints/entidades que menciona
- `_project/11_file_dependencies.md` — entrada del módulo si existe

Determinar:

- ¿`data_model = "required"` o `"done"`? → cargar entidades relevantes del módulo.
- ¿`api_contract = "required"` o `"done"`? → cargar endpoints relevantes del módulo.
- ¿`"n/a"`? → no leer ese artefacto. Verificar que `justification` está presente.

---

## Paso 2 — Lectura selectiva

### Si data_model aplica

Leer `_project/02_technical_plan/data_model.md` y extraer **solo**:

- El bloque `erDiagram` completo (el grafo se entiende como unidad global).
- Las secciones de entidad (`### NombreEntidad` o `## Entidad ...`) cuyo módulo propietario coincide con el módulo activo, o que aparecen en el `erDiagram` conectadas directamente con entidades del módulo, o que figuran declaradas en `04_active_scope.md` / `11_file_dependencies.md`.
- La sección `## Restricciones e invariantes del modelo` — siempre completa.

NO leer:

- Entidades de otros módulos sin relación ni consumo con el activo.
- La sección "No aplica — registro por módulo" salvo que el módulo activo aparezca ahí.
- Si no existen entidades para el módulo activo, se produce una selección vacía (`empty_source`) que bloquea con `NEEDS_INPUT`.

### Si api_contract aplica

Leer `_project/02_technical_plan/api_contract.yaml` y extraer **solo**:

- Metadatos globales: `openapi`, `info`, `servers` y `security`.
- Las operaciones HTTP bajo `paths` pertenecientes al módulo activo (según `tags`, `x-module`, o pares `(método, ruta)` declarados en `04_active_scope.md`, `03_contracts.md` o `11_file_dependencies.md`). En rutas compartidas se conservan únicamente las operaciones activas y los `parameters` de nivel de path.
- Clausura transitiva completa de componentes alcanzables (`schemas`, `parameters`, `responses`, `requestBodies`, `headers`, `securitySchemes`) referenciados directa o indirectamente mediante `$ref`, con prevención de ciclos.
- Detección de problemas de especificación (`spec_problems`): referencias rotas o externas sin resolver y construcciones no soportadas (ej. `webhooks`) bloquean con `NEEDS_INPUT` en el gate de `SPECS`. Si `tags` y `x-module` discrepan contradictoriamente para una operación, se eleva `SOURCE_AMBIGUOUS`.

NO leer:

- Operaciones pertenecientes a otros módulos.
- Schemas huérfanos o de otros módulos que no son alcanzables desde las operaciones extraídas.

---

## Paso 3 — Producir bloque de contexto

```markdown
## Specs del módulo [módulo]

### Modelo de datos (entidades activas)
[Mermaid local + entidades del módulo]

### Contrato HTTP (endpoints activos)
[Paths y schemas referenciados]

### Restricciones del modelo
[Sección completa de restricciones e invariantes del modelo, si existe]
```

Si `data_model` o `api_contract` son `"n/a"` para el módulo:

```markdown
## Specs del módulo [módulo]

- data_model: n/a — [justification]
- api_contract: n/a — [justification]
```

---

## Reglas

- **Nunca** cargar `data_model.md` o `api_contract.yaml` completos. Solo el subconjunto del módulo activo.
- Si `module_specs[módulo]` no existe → emitir advertencia y derivar a `/scope-siguiente` o `/enriquecer-ticket` para clasificar antes de continuar.
- Si la extracción detecta entidades/endpoints del módulo que no están declarados → emitir advertencia: el módulo está consumiendo specs sin declararlas. El workflow llamador decide si bloquear o continuar.
- Esta skill NO modifica los artefactos. Solo lee. La escritura es responsabilidad de `/diseñar` (tech-design-mvp) y `/enriquecer-ticket`.

UI, logs y mensajes en SPANISH.
