---
description: Documenta un bug al vuelo sin interrumpir el flujo de trabajo
---

Eres el agente que registra bugs.

Recibirás:
- @06_bug_fixes.md

## Pasos

0. ¿BUG NUEVO O CORRECCIÓN DE UNO YA REGISTRADO?
   Si lo que trae el usuario no es un síntoma nuevo sino un dato incorrecto en
   un `BUG-XXX` ya existente (descripción, fix o archivos afectados mal
   redactados), no uses `register_bug`: usa `mutate_state` acción `amend_bug`
   con `{"id": "BUG-XXX", ...campo corregido..., "reason": "[motivo, ≥20
   chars]"}`. `severity`, `status`, `affects_ac` y `module_id` no son
   enmendables (mueven gates); si el corregir cae ahí, es un evento distinto,
   no una enmienda.

1. RECOGER LOS DATOS DEL BUG
   Preguntar al usuario (en un solo bloque, no en varios mensajes):
   ```
   Para documentar el bug necesito:
   1. Título breve del bug
   2. Síntoma: ¿qué observas exactamente?
   3. Archivos afectados (si se conocen): ¿cuáles?
   4. Severidad: ¿Crítico (bloquea siempre), Mayor o Menor?
      → Si es Mayor: ¿afecta algún criterio de aceptación? (affects_ac: sí/no)
   ```
   Esperar la respuesta antes de continuar.

2. REGISTRAR POR MCP
   Llamar a la herramienta `mutate_state` con la acción `register_bug` y el payload:
   ```json
   {
     "module_id": "[nombre exacto del módulo activo obtenido del brief]",
     "severity": "[Crítico / Mayor / Menor]",
     "description": "[Título breve] - Síntoma: [síntoma]",
     "affects_ac": [true / false]
   }
   ```

3. COMPROBAR LA ACTUALIZACIÓN AUTOMÁTICA
   El motor de estado (MCP) hará automáticamente:
   - Generar el siguiente id de bug (BUG-XXX).
   - Poner el módulo en `blocked` si la severidad es `Crítico`, o `Mayor` con `affects_ac: true`.
   - Actualizar `last_completed_step` en `03_project_state.json`.
   - Regenerar las vistas `06_bug_fixes.md` y `core.md`.

4. SALIDA
   Producto en producción (FW-225): el fix se hace como cambio de
   mantenimiento (`.agent/workflows/mantenimiento.md`), no reabriendo el
   módulo con el flujo completo; cerrar con:

   Siguiente: /ciclo Corregir BUG-XXX como cambio de mantenimiento.
   Modelo: <strong|standard|limited> · Esfuerzo: <🟢 bajo|🟡 medio|🟠 alto|🔴 xhigh> (<tier>)

   En desarrollo:
   Si el bug bloquea (`/nuevo-bug` solo registra; lo corrige `/ciclo` en un módulo de Estabilización, FW-349):

   Siguiente: /ciclo Abrir el módulo de Estabilización estabilizacion-BUG-XXX y corregir el bug bloqueante BUG-XXX.
   Modelo: <strong|standard|limited> · Esfuerzo: <🟢 bajo|🟡 medio|🟠 alto|🔴 xhigh> (<tier>)

   El id del módulo nombra el bug que arregla; así su vista `implementation` no lo cuenta como bloqueo (FW-359).

   Si el bug no bloquea, no se emite un cierre nuevo: vuelve al `Siguiente:` vigente de la sesión y repítelo tal cual.

La interfaz, los logs y los textos para el usuario van en español.
