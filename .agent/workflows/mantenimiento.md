---
description: (interno — lo invoca /ciclo) Flujo ligero para un proyecto en producción — un cambio acotado con gates mecánicos y escalada automática a módulo cuando el riesgo lo exige
---

# Mantenimiento — un cambio en producción (FW-225)

Lo invoca `/ciclo` (paso −1) cuando `lifecycle.stage == production`. **No leas
`ciclo.md`, `cierre-modulo.md`, `enriquecer-ticket.md` ni `scope-siguiente.md`**:
este archivo los sustituye para trabajo tier_0/tier_1. Si el cambio escala, el
flujo completo de módulo vuelve a aplicar íntegro.

**Qué se conserva:** saber qué se toca, no romper lo que funciona, verificar
con evidencia real y dejar rastro. **Qué se omite:** fases, scope de módulo,
ACs en `08_module_acceptance.md`, recibos de aprobación/ejecución/revisión,
cosecha de fricción obligatoria, digest de enseñanza. Los gates mecánicos
(motor, STUB GATE, pre-commit, guard destructivo) siguen activos.

> El ciclo de vida lo cambia **solo el builder** (`set_lifecycle` con
> `builder_confirmed: true` tras su petición explícita). Nunca lo infieras ni
> lo propongas por tu cuenta.

## Pasos

### 1. Contexto mínimo
- `get_work_context {"view":"startup"}` + `_project/core.md` (muestra ciclo de
  vida, cambio activo y bloqueadores).
- Si hay un cambio `open`, continúa ese; no abras otro.
- `_project/03_invariants.md` si existe (siempre: en producción son las reglas
  que no se pueden romper).
- Lee el código afectado, no documentos de módulo.

### 2. Clasificar — ¿mantenimiento o módulo?
Aplica los criterios de `cambio-seguro.md` paso 0.25. Es **tier_2 → escala** si
cualquiera se cumple:
- migraciones/esquema, auth/roles/secretos, contrato público externo,
  dinero/transacciones, PII más allá de leerla;
- funcionalidad nueva (no un ajuste de algo existente);
- el cambio sería `DESTRUCTIVO` (quita, renombra o altera comportamiento).

Si escala: `mutate_state escalate_change {"id","module_id","reason"}` (si ya
abriste cambio) o `add_module` (si no), y sigue `/ciclo` normal sobre ese
módulo. Informa al builder en una línea: *"Esto ya no es un ajuste: toca
[superficie]. Lo llevo como módulo completo."*

### 3. Abrir el cambio
`mutate_state open_change`:
```json
{"summary": "<qué y por qué, 1 línea>", "affected_modules": ["<módulo>"],
 "tier": "tier_0|tier_1", "criteria": ["<1 a 3 criterios observables>"]}
```

### 4. PRESERVAR acotado
En la respuesta (no en archivos), lista lo que ya funciona en lo que vas a
tocar: funciones/rutas/controles/estados de UI/validaciones, con evidencia
(`archivo:línea` o test). Si descubres que el cambio sería DESTRUCTIVO → vuelve
al paso 2 y escala.

### 5. Implementar y verificar
- Cambio mínimo. Sin refactors oportunistas.
- Test de regresión si hay runner; si no, verificación real reproducible
  (arrancar y probar el flujo). Reporta **comando y `rc`** reales.
- Checklist de seguridad: solo las secciones aplicables de
  `cierre-modulo.md` §4 (léelas selectivamente) + invariantes.

### 6. Cerrar el cambio
`mutate_state close_change`:
```json
{"id": "CHG-NNN",
 "evidence": {"<criterio exacto>": "<ruta/test/comando con rc, ≥20 caracteres>"},
 "tests": "<comando> → rc=0",
 "changelog": "<qué cambió, en lenguaje del builder>"}
```
El motor bloquea si: falta evidencia o cita rutas inexistentes, no hay `rc=0`,
hay bugs críticos en los módulos afectados, el diff toca superficies frágiles
(`lifecycle.escalation_patterns` + defaults: migraciones, auth, esquema,
contratos) o aparece un stub. **Un bloqueo es un gate, no un obstáculo**:
resuélvelo o escala; nunca lo rodees editando el estado.

### 7. Commit del cambio
`git add -A && git commit -m "fix|feat(<área>): <resumen> (CHG-NNN)"`. Los gates
del pre-commit aplican íntegros; nunca `--no-verify`.

### 8. Cierre de turno
Una sola respuesta breve en lenguaje del builder: qué cambió y cómo se
verificó. Luego el bloque de cierre de `decision_translation_core.md`, en texto plano y sin bloque de código:

Siguiente: /ciclo <instrucción: siguiente ajuste concreto>
Modelo: <strong|standard|limited> · Esfuerzo: <🟢 bajo|🟡 medio> (<tier>)

Si no queda ningún ajuste, el cierre es una sola línea, en línea propia:

Siguiente: ninguno — sin cambios pendientes

## Bugs en producción
`/nuevo-bug` registra el bug igual. El fix se hace **como cambio** (pasos 2-7
de este archivo, citando `BUG-XXX` en el summary) y se resuelve con
`resolve_bug` antes de `close_change`. Un bug `Crítico` sigue bloqueando el
cierre de cualquier cambio sobre ese módulo.

## Anti-patrones
- Abrir un cambio tier_2 "porque es chico". El tier se mide por blast radius,
  no por líneas (Example Tool `19ba2de`: migración + routers entró sin gate).
- Reabrir ceremonia de desarrollo (scope, ACs, recibos) para un tier_1.
- Cambiar `lifecycle` sin petición explícita del builder.
