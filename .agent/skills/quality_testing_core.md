---
description: Skill core. Se activa al implementar lógica, corregir bugs, tocar contratos públicos o prevenir regresiones.
core: true
---

# Calidad y Testing — Skill Core

**Activación:** implementación tier_1/tier_2, bugs, regresiones, lógica de negocio, contratos públicos, refactors o cambios compartidos.

## Principios

- Probar comportamiento, no detalles internos.
- Tests focalizados antes que suites enormes como primer paso.
- Bugs de regresión cierran con test nuevo o justificación explícita.
- Refactor solo con red de seguridad: tests existentes, checks manuales o inventario `PRESERVAR`.
- No marcar AC como cumplido sin evidencia.

## Estrategia

1. Identificar comportamiento observable.
2. Elegir el menor test/check útil.
3. En tier_1/tier_2 con lógica que probar, preferir `/tdd`.
4. Ejecutar test focalizado.
5. Registrar evidencia en `05_test_history.md` al cerrar módulo.

## Checklist

- [ ] ¿Qué comportamiento debe funcionar?
- [ ] ¿Qué comportamiento existente debe preservarse?
- [ ] ¿Hay test o check focalizado?
- [ ] ¿El fallo reproduce el problema correcto?
- [ ] ¿La corrección es mínima?
- [ ] ¿Se verificaron loading/empty/error/success si hay UI?

## Cuando No Hay Runner

Documentar check manual mínimo:

```markdown
Verificación manual:
- Acción:
- Resultado esperado:
- Resultado observado:
- Riesgo residual:
```

No inventar que se ejecutaron tests.
