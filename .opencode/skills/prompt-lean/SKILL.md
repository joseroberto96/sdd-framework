---
name: "prompt-lean"
description: "Genera el contexto mínimo necesario para ejecutar una tarea sin leer el repositorio completo. Se activa al invocar /lean-context o manualmente antes de una sesión enfocada."
---

<!-- GENERATED: framework wrapper; edit manifest/source, then run sync_claude_commands.py -->

# Prompt Lean — Generador de Contexto Mínimo

**Activación:** Al ejecutar `/lean-context [módulo_id]` o cuando el agente detecta que va a iniciar una tarea en un módulo con entrada en `11_file_dependencies.md`.

## Objetivo

Producir un bloque de contexto compacto que contenga exactamente lo que un agente ejecutor necesita para implementar un módulo sin riesgos de romper dependencias — y nada más.

---

## Paso 0 — Regla artefacto-primero

Antes de leer cualquier archivo del repositorio: si la evidencia ya existe
proyectada, consultarla en vez de reconstruirla. Prohibido re-derivar leyendo
código o historial lo que una proyección ya responde:

| Pregunta | Artefacto que ya la responde |
|---|---|
| ¿Dónde estamos? ¿Qué sigue? | `_project/core.md` / `00_session_brief.json` (proyecciones) |
| ¿Qué lecciones aplican? | `_project/22_lessons_digest.md` (digest con decaimiento; no el archivo completo) |
| ¿Qué archivos toca este módulo? | `_project/11_file_dependencies.md` |
| ¿Qué se rompe si toco X? | adaptador CODE_GRAPH (`.agent/scripts/code_graph.py`), y solo en degradación el inventario manual |
| ¿Qué decidimos sobre Y? | `_project/03_arch_decisions.md` (proyección) |

Solo cuando ningún artefacto responde la pregunta se pasa a leer archivos — y
entonces con el presupuesto de evidencia del bloque (abajo), no con exploración
abierta.

## Paso 1 — Leer fuentes (en paralelo)

1. `get_work_context` con `{"view":"implementation","module_id":"<módulo>"}` → estado, tier, specs, stack, dependencias, restricciones, scope y ACs acotados; seguir sus cursores
2. `_project/11_file_dependencies.md` → sección del módulo solicitado, incluyendo `preserva`
3. `_project/04_active_scope.md` → solo la sección señalada por la vista
4. `_project/08_module_acceptance.md` → solo la sección señalada por la vista
5. `_project/03_contracts.md` → solo las interfaces no-HTTP listadas en `interfaces_requeridas` del módulo
6. `_project/02_technical_plan/data_model.md` y/o `_project/02_technical_plan/api_contract.yaml` → solo vía `spec_contracts` si `module_specs[módulo]` marca specs `required` o `done`; usar `specs_formales_requeridas` como filtro adicional si existe

NO leer: todo el repositorio de código, módulos no relacionados, historial de bugs cerrados, fases anteriores.

---

## Paso 2 — Validar dependencias

Antes de generar el bloque, verificar:

- [ ] Cada archivo en `lee:` existe en el proyecto
- [ ] Cada interfaz no-HTTP en `interfaces_requeridas:` existe en `03_contracts.md`
- [ ] Cada entrada de `specs_formales_requeridas:` existe en `data_model.md` / `api_contract.yaml` cuando `module_specs` lo exige
- [ ] Cada entidad/endpoints HTTP que el módulo toca existe en `data_model.md` / `api_contract.yaml` aunque no esté listada todavía en `specs_formales_requeridas`
- [ ] El módulo no está en estado `blocked` en `module_statuses`
- [ ] La sección `preserva:` no está vacía ni contiene `# pendiente` cuando el módulo modifica código existente

Si alguna validación falla → reportar el conflicto y detenerse. No generar el bloque con información incompleta.

Si la entrada del módulo contiene `# pendiente` en cualquier campo → detenerse. El Prompt Lean incompleto es peor que no tener Prompt Lean.

---

## Paso 3 — Producir el Prompt Lean

Generar el siguiente bloque (para uso humano o para pasar a un sub-agente):

```
═══════════════════════════════════════
CONTEXTO MÍNIMO — [NOMBRE_MÓDULO]
═══════════════════════════════════════

TAREA:
[descripción de la tarea activa del scope]

ARCHIVOS A LEER (antes de implementar):
  - [ruta] — [propósito]
  - [ruta] — [propósito]

ARCHIVOS A ESCRIBIR/MODIFICAR:
  - [ruta]

INTERFACES REQUERIDAS:
  [bloque copiado de 03_contracts.md para cada interfaz no-HTTP listada]

SPECS FORMALES:
  [extracto selectivo de data_model.md / api_contract.yaml producido por spec_contracts, si aplica]

PRESERVAR:
  [funciones, endpoints, comandos, rutas, flujos UI, validaciones, permisos y formatos existentes listados en 11_file_dependencies.md]

CRITERIOS DE ACEPTACIÓN:
  [ACs del módulo de 08_module_acceptance.md]

CONSTRAINTS ACTIVOS:
  [stack.backend/frontend/database de project_state.json]
  [architecture_constraints relevantes al módulo]

CAPACIDAD DE EJECUTOR: [strong/standard/limited] — [modelo concreto, agnóstico del proveedor] — [justificación 1 línea]
Esfuerzo: [bajo|medio|alto|xhigh]
PRESUPUESTO DE EVIDENCIA: [N archivos máx. a leer fuera de esta lista | criterio de parada]
═══════════════════════════════════════
```

El `PRESUPUESTO DE EVIDENCIA` acota la exploración del ejecutor (know-how del
patrón query-first: respuesta acotada, no lectura abierta). Por defecto: solo
los archivos listados en `ARCHIVOS A LEER` + máximo 3 archivos adicionales si
una dependencia no declarada aparece durante la implementación; al agotarlo,
parar y reportar el hueco en `11_file_dependencies.md` en vez de seguir leyendo.

---

## Paso 4 — Registrar uso (opcional)

Si el usuario lo solicita con `--register`, añadir al mapa `11_file_dependencies.md` cualquier archivo nuevo detectado durante la generación que no estaba declarado.

---

## Reglas

- El bloque debe ser autocontenido: quien lo recibe no necesita leer ningún otro archivo para comenzar.
- No incluir código existente completo — solo firmas de interfaces y rutas de archivos.
- No incluir historial de la sesión, bugs resueltos ni módulos ya completados.
- Si el módulo no tiene entrada en `11_file_dependencies.md`, notificar al usuario y ofrecer registrarla antes de generar el bloque.
