# Adaptador — Grafo de Código (CODE_GRAPH)

> **Soporte:** experimental — fase 2. `detect_changes` (modo diff) y `trace_path` inbound (modo pre-edición, `--symbol`) implementados y verificados contra el esquema fuente de la herramienta (0.8.1, `src/mcp/mcp.c`: `handle_detect_changes` y `handle_trace_call_path`). La inferencia de `riesgo` y la clasificación siguen fuera de alcance.
> **Degradación:** ver `.agent/adapters/_capability_matrix.md` (fila `CODE_GRAPH_MANUAL`). Sin el binario, sin coincidencia de pin, o si la invocación falla, `/cambio-seguro` usa el inventario manual existente sin ningún cambio.

## Qué es

Capacidad opcional que da a `/cambio-seguro` evidencia real de blast radius
(archivos cambiados, símbolos impactados) desde `codebase-memory-mcp`, en vez
de deducirlo leyendo archivos a mano. Es **evidencia, no autoridad**: el
agente sigue clasificando ADITIVO/COMPATIBLE/DESTRUCTIVO en el paso 2 de
`/cambio-seguro` — la herramienta no provee esa clasificación.

## Herramienta subyacente

- Proyecto: `codebase-memory-mcp` (https://github.com/DeusData/codebase-memory-mcp).
- **Pin de versión:** `TOOL_VERSION_PIN = "0.8.1"` en `.agent/scripts/code_graph.py`, verificado contra `server.json` de la herramienta. Cambiar el pin es un cambio `tier_1` — implica reverificar los guardarraíles y el esquema de `detect_changes` contra la nueva versión.
- Invocación: subproceso CLI — `codebase-memory-mcp cli detect_changes '{"project": ..., "base_branch": ..., "scope": "symbols"}'`. No se habla el protocolo MCP directamente.

## Esquema real de `detect_changes`

Verificado en el código fuente de la herramienta (`src/mcp/mcp.c:handle_detect_changes`), no en su README:

```json
{"changed_files": [...], "changed_count": N, "impacted_symbols": [{"name","label","file"}, ...], "depth": N}
```

No incluye `riesgo` ni clasificación de cambio. Este shim **no inventa** esos
valores: `riesgo` queda `"unknown"` y `clasificacion_sugerida` queda `null`
incluso con `status: "ok"`.

## Guardarraíles (no negociables)

- El shim **nunca invoca `manage_adr`** — `_project/03_arch_decisions.md` (proyección MCP) es la única fuente de decisiones arquitectónicas.
- `auto_watch` se fuerza a `false`.
- El shim **nunca escribe** en `_project/` ni en el árbol del proyecto — solo lee/invoca subprocesos de solo lectura.
- Sin instalación por `curl | bash`; sin grafo compartido de equipo; sin UI 3D; sin `manage_adr`.

## Contrato de salida

Ver `.agent/scripts/code_graph.py::consultar_blast_radius()`. Devuelve siempre:

```json
{
  "status": "ok | degraded",
  "afectados": ["..."],
  "callers": [],
  "riesgo": "low | medium | high | unknown",
  "clasificacion_sugerida": null,
  "evidence": ["..."],
  "tool_version": "...",
  "warnings": ["..."],
  "source": "code_graph_adapter/codebase-memory-mcp"
}
```

`callers` es siempre `[]` en esta fase — **siempre** acompañado de un warning
explícito ("callers no se calcula...") para que nunca se lea como "verificado
sin callers". `clasificacion_sugerida` es siempre `null`: la clasificación
final es del agente en `/cambio-seguro` paso 2.

## Comportamiento real de la CLI (verificado en dogfood, 2026-07-10)

Tres detalles que el código fuente no revela y solo el uso real expone:

1. **stdout mezcla logs y JSON**: la CLI emite `level=info msg=...` antes del
   objeto JSON. Se parsea la última línea JSON, no el stdout completo.
2. **`project` NO es una ruta**: es el nombre normalizado que la herramienta
   asigna al indexar (`Users-x-Proyecto`). El shim lo resuelve con
   `cli list_projects` (`root_path` → `name`); si el proyecto no está indexado,
   degrada con el comando `index_repository` como hint.
3. **El JSON de error va por stderr con rc=1**: símbolo no encontrado o ambiguo
   devuelve `{"error", "hint"}` en stderr. El shim lo lee y propaga el hint al
   agente en vez de reportar "no devolvió JSON válido".

Requisito operativo: el proyecto debe estar indexado
(`codebase-memory-mcp cli index_repository '{"repo_path": "<ruta>"}'`).

## Modo pre-edición (fase 2, FW-B)

`consultar_afectados()` invoca `codebase-memory-mcp cli trace_path` con
`direction: "inbound"` para un símbolo concreto ANTES de editarlo — cierra la
limitación de orden del modo diff (ver abajo). Contrato idéntico; `callers` y
`afectados` se rellenan con `nombre (qualified_name) [hop N]`. Cero callers con
símbolo resuelto es evidencia de ausencia **condicionada a la frescura del
índice** (warning siempre presente). Símbolo no encontrado o ambiguo → la
herramienta no adivina y el shim degrada con el hint de la herramienta.

```bash
python3 .agent/scripts/code_graph.py --root . --symbol nombre_funcion --depth 2
```

## Fuera de alcance de esta fase

- Inferencia de `riesgo` o de clasificación ADITIVO/COMPATIBLE/DESTRUCTIVO.
- `manage_adr`, grafo compartido de equipo, UI 3D, instalación por script.

## Limitación de orden: diff vacío no es "sin impacto"

`detect_changes` solo mide diff **ya presente** en el árbol de trabajo — no
puede anticipar el impacto de un cambio que todavía no se ha hecho. Como
`/cambio-seguro` invoca este shim **antes de editar** (paso 0.6, antes del
inventario), un árbol de trabajo limpio es el caso normal, no una excepción.
Por eso un diff vacío (`changed_files`/`impacted_symbols` ambos vacíos)
**degrada explícitamente** en vez de devolver `status: "ok"` con listas
vacías — que se leería falsamente como "confirmado sin impacto". El shim
aporta evidencia real solo cuando ya hay cambios sin commitear en el árbol
(p. ej. al reejecutar el chequeo tras una primera pasada de implementación).

## Uso desde workflows

`/cambio-seguro` paso 0.6 invoca:

```bash
python3 .agent/scripts/code_graph.py --root . --diff-ref HEAD
```

- `status: "ok"` → usar `afectados`/`evidence` como base del inventario. `riesgo`/`clasificacion_sugerida` no aportan nada nuevo en esta fase (siempre `unknown`/`null`) — el agente sigue clasificando desde cero en el paso 2.
- `status: "degraded"` → declarar `CODE_GRAPH_MANUAL` y continuar con el inventario manual, sin cambios. Incluye el caso de diff vacío (ver arriba).
