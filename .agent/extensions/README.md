# Extensiones del motor de estado (ADR-052)

Acciones `mutate_state` **custom del producto**. Este directorio pertenece al
proyecto, no a la plantilla: `/actualizar-framework` **nunca lo sobrescribe**.

## Contrato

Crear `state_actions.py` en este directorio con:

```python
def marcar_dep_custom(manager, payload, now_iso):
    """manager.state_data es el estado; devolver SIEMPRE un str de resultado."""
    manager.state_data["dependencies"].append(payload["dep"])
    return f"dep {payload['dep']} registrada por extensión"

EXTENSION_ACTIONS = {
    "marcar_dep_custom": marcar_dep_custom,
}
```

Reglas (las aplica el motor, no son opcionales):

- Una extensión **no puede sombrear** una acción built-in — colisión = error de
  carga; las acciones del motor siempre ganan.
- Archivo roto o sin `EXTENSION_ACTIONS: dict` → error claro en la mutación,
  nunca silencio.
- La validación de schema post-mutación sigue aplicando: una extensión no puede
  dejar el estado inválido.
- El evento queda en `state_log.jsonl` como cualquier mutación. Ojo con
  `replay_state_log.py`: reproducir un log con acciones de extensión requiere
  las mismas extensiones presentes.
- En modo template este archivo no debe existir (guard del doctor).

Antes de escribir una extensión, comprobar si un verbo canónico ya cubre el
caso (`append_to_list_field`, `add_module`, `set_active_module`, …). Si la
extensión resulta útil en 2+ proyectos, candidata a subir al framework
(`_feedback/field_report.md`).

## Patrones de audit del dominio

Los patrones que el motor aplica a `reason`/`resolution`/`evidence` antes de
persistirlos en la bitácora encadenada son **genéricos** (credenciales, JWT,
tarjetas). Los calibrados a un dominio — móviles de un país, email, un formato
de identificador local — son ciertos para tu producto y falsos positivos para
otros, así que se declaran aquí en vez de forkear el motor (FW-182):

```python
import re

AUDIT_EXTRA_PATTERNS = (
    re.compile(r"\b09\d{8}\b"),                                  # móvil EC
    re.compile(r"(?i)\b[a-z0-9._%+-]+@[a-z0-9.-]+\.[a-z]{2,}\b"),  # email
)
```

Puede convivir con `EXTENSION_ACTIONS` en el mismo `state_actions.py`.
