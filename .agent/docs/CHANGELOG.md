# Historial de cambios del Framework SDD

La edición pública comienza con la versión 5.3.3. Los cambios de octubre de
2026 resumen hitos del desarrollo privado que dieron forma a esta versión.

## [5.3.3] — Primera edición pública

### Publicación inicial

- Plantilla independiente con licencia MIT, instrucciones de instalación y un
  ejemplo ejecutable de continuidad entre sesiones.
- Guía de contribución, hoja de ruta y plantillas para reportar problemas y
  proponer mejoras.

### Desarrollo privado · 31 de octubre de 2026

- Permite abandonar un módulo que ya empezó y reasignar los bugs abiertos
  que deben seguir atendiéndose.
- Mantiene válida la revisión de un módulo cuando se editan secciones de
  alcance o criterios de aceptación que pertenecen a otro módulo registrado.
  Los cambios en las secciones propias siguen requiriendo revisión.

## [5.3.2] — Desarrollo privado · octubre de 2026

- Los avisos de longitud y redacción del siguiente paso dejaron de bloquear
  el cierre de un turno.
- Cuando el cierre requiere una corrección, el agente pide corregir solo las
  líneas afectadas. Al retomar una sesión, presenta el paso guardado de forma
  más breve cuando hace falta.

## [5.3.1] — Desarrollo privado · 22 de octubre de 2026

- Unificó las reglas que limitan operaciones destructivas en las integraciones
  de Claude Code y Codex.
- El escaneo de credenciales del pre-commit pasó a revisar los archivos
  preparados para el commit.
- Añadió avisos de calidad configurables para el cierre de tareas.

## [5.3.0] — Desarrollo privado · 13 de octubre de 2026

- Unificó la validación del cierre de turno y el registro del siguiente paso.
- Acotó los resúmenes de estado y evidencia que necesita el agente para retomar
  trabajo o revisar un cierre.
