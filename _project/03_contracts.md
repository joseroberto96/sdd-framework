# Contratos e Interfaces Públicas — [NOMBRE_PROYECTO]

> Se carga bajo demanda: en `/enriquecer-ticket` (paso 5.2), `/validate-arch` o al implementar un módulo que consume interfaces declaradas.
> El skill `prompt_lean` lee solo las interfaces relevantes al módulo activo — no el archivo completo.

## Reglas de coexistencia con specs formales (introducidas en v3.x)

Tres artefactos coexisten para describir contratos del proyecto:

| Artefacto | Fuente formal de | Naturaleza |
|---|---|---|
| `_project/02_technical_plan/data_model.md` | Entidades del dominio persistidas (Mermaid + atributos + relaciones) | Spec formal |
| `_project/02_technical_plan/api_contract.yaml` | Endpoints HTTP/REST (request, response, errores) | Spec formal (OpenAPI 3.1) |
| **Este archivo** (`03_contracts.md`) | CLI internas, bibliotecas, eventos, contratos no-HTTP | Índice narrativo humano |

**Regla operativa:**

- Si la interfaz es **HTTP/REST**, no se duplica aquí: este archivo registra una línea-puntero del estilo `→ ver 02_technical_plan/api_contract.yaml#paths./ruta` y nada más.
- Si la interfaz es una **entidad del dominio persistida**, el modelo formal vive en `data_model.md`. Este archivo solo declara contratos de uso de esa entidad (constructores, validadores, helpers) cuando aplique.
- Si la interfaz es **CLI interna, biblioteca, evento o función pública sin transporte HTTP**, sigue describiéndose aquí con el formato de entrada habitual.

`/validate-arch` verifica coherencia: ninguna línea-puntero apunta a path inexistente en el YAML, y ningún endpoint del YAML carece de mención en este índice cuando aplique.

Detalle completo en ADR-012 en `.agent/docs/framework_arch_decisions.md`.

## Formato de entrada

```
### [NombreInterfaz] — [Módulo que la expone]
**Módulo:** [nombre del módulo en project_state.json]
**Archivo:** [ruta/al/archivo.py]
**Versión:** [número o fecha — incrementar al romper compatibilidad]

#### Funciones / Comandos / Eventos

- `nombre_funcion(param: tipo) -> tipo_retorno`
  - Propósito: [descripción en 1 línea]
  - Efectos secundarios: [si los hay]

#### Tipos auxiliares no persistidos

- `NombreModelo`
  - `campo: tipo` — [descripción breve]
  - `campo: tipo` — [descripción breve]

> Entidades persistidas van en `data_model.md`. Endpoints HTTP/REST van en `api_contract.yaml` y aquí solo dejan una línea-puntero.

#### Restricciones
- [ej: idempotente — llamadas múltiples producen el mismo resultado]
- [ej: no thread-safe — usar un único hilo por instancia]
```

---

<!-- Registrar interfaces públicas aquí -->
