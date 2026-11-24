# Contribuir al Framework SDD

## Reportar un problema o sugerir una mejora

Abre un issue y elige la plantilla correspondiente. Para un problema, incluye
la versión del framework, tu sistema operativo, la versión de Python y el
agente que usaste, si aplica. Describe el resultado esperado y el observado.
Si puedes, incluye un caso mínimo reproducible con datos sintéticos.

## Enviar cambios

Para un cambio amplio en el motor o en los workflows, abre primero un issue
para discutir el alcance. En la pull request, explica el problema, enlaza el
issue si existe y registra los comandos de verificación y sus resultados.

Desde la raíz del proyecto, puedes empezar con estas comprobaciones:

```bash
python3 .agent/scripts/framework_doctor.py
python3 examples/session-continuity.py
```

Para la suite completa sigue los pasos del [README](README.md#código-y-pruebas).
Si cambias una fuente de comandos, usa
`python3 .agent/scripts/sync_claude_commands.py` para regenerar sus wrappers y
añade los cambios generados a la pull request.

El mantenedor registra el contexto en el issue, revisa el cambio en la pull
request y documenta lo que se incorpora en el
[historial de cambios](.agent/docs/CHANGELOG.md).
