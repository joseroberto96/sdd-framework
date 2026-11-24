# Criterios de Aceptación por Módulo — [NOMBRE_PROYECTO]

## Formato de sección

El nombre del módulo DEBE coincidir exactamente con la clave en `modules.*` de `03_project_state.json`.

```
## Módulo N — [Nombre exacto en project_state.json]
Estado: ⬜ Pendiente | 🔄 En progreso | ✅ Verificado por el agente | 👤 Usado por el builder

### Criterios
- [ ] [Criterio funcional 1] — evidencia: pendiente
- [ ] [Criterio funcional 2] — evidencia: pendiente
- [ ] [Criterio de seguridad] — evidencia: pendiente
- [ ] [Criterio de rendimiento si aplica] — evidencia: pendiente
```

## Regla de evidencia (obligatoria)

Un AC solo se marca `[x]` cuando lleva una evidencia concreta. Formatos válidos:

- `[x] AC1 — evidencia: test "tests/foo_spec.py::test_X" ejecutado verde`
- `[x] AC2 — evidencia: comando "curl http://localhost/api/x" → 200 OK`
- `[x] AC3 — evidencia: verificación manual — flujo login completo el 2026-MM-DD`
- `[x] AC4 — evidencia: revisión de código — función `bar()` cubre el caso en `src/foo.py:42`

Formatos NO válidos (rechazados por `/cierre-modulo`):
- `[x] AC1` (sin evidencia)
- `[x] AC2 — evidencia: hecho`
- `[x] AC3 — evidencia: funciona`
- `[x] AC4 — evidencia: implementado`

`/cierre-modulo` verifica que cada AC marcado `[x]` contenga el patrón `— evidencia:` seguido de al menos 20 caracteres de descripción concreta. Si falta, el cierre se rechaza con `reason: ac_evidence_missing`.

---

<!-- Definir ACs antes de implementar cada módulo -->
