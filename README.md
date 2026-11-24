# Framework SDD para agentes de programación

**Framework v5.3.3 · Guardrails v4.2 · MIT**

Conserva el estado, las especificaciones y la evidencia de tu proyecto en el
repositorio. Puedes retomar una tarea en otra sesión o con otro agente a partir
de los mismos archivos y revisar qué verificaciones exige antes de cerrarla.

El motor local expone el mismo estado por CLI y MCP. Las integraciones incluyen
Claude Code, Codex y OpenCode, con workflows por archivo para otros entornos.
La documentación y los comandos están principalmente en español.

**English overview.** A spec-driven development template that stores project state,
acceptance criteria and verification evidence in the repository. The local demo
shows how a fresh session can retrieve the next action from persistent state.

## Origen y mantenimiento

Desarrollé el framework en privado mientras lo usaba en mis propios proyectos.
Preparar mi candidatura a Claude for Open Source me dio el impulso para abrir
esta edición bajo licencia MIT, compartir un ejemplo reproducible y recibir
comentarios de otros desarrolladores.

El proyecto está mantenido por [@joseroberto96](https://github.com/joseroberto96).

- [Historial de cambios](.agent/docs/CHANGELOG.md) para ver qué cambió entre versiones.
- [Hoja de ruta](docs/ROADMAP.md) para seguir lo que falta por verificar o mejorar.
- [Guía para contribuir](CONTRIBUTING.md) para reportar problemas o enviar mejoras.

## Prueba rápida

Requiere Git, Bash y Python 3.12 o superior. Descarga el ZIP del repositorio o
clona la edición pública y abre una terminal en la carpeta extraída.

```bash
python3 .agent/scripts/framework_doctor.py
python3 examples/session-continuity.py
```

El primero verifica la plantilla. El ejemplo crea un proyecto sintético en
una carpeta temporal, registra una próxima acción por CLI y la recupera en
otro proceso. Usa solo la biblioteca estándar y no necesita cuenta, API ni
un agente instalado. Consulta [el ejemplo](examples/README.md) para ver
también cómo se comprueba la evidencia de una tarea.

## Adoptar en un proyecto

1. Para empezar un proyecto, extrae el ZIP en una carpeta propia y abre esa
   carpeta en tu agente. Para un proyecto existente, conserva sus instrucciones
   y usa el flujo de migración.
2. Ejecuta `/nuevo-proyecto` o `/migrar-proyecto`. Si tu agente usa skills con
   nombres generados o carece de comandos slash, pídele que siga el workflow
   correspondiente en `.agent/workflows/`.
3. Usa `/diseñar` para definir el alcance, `/ciclo` para trabajar, `/gycd`
   para guardar la continuidad y `/arrancar` para retomar desde el estado local.

Revisa [AGENTS.md](AGENTS.md), [CLAUDE.md](CLAUDE.md), los
[adaptadores](.agent/adapters/README.md) y la configuración del entorno antes
de confiar hooks o habilitar servidores MCP. El ejemplo local verifica el
motor CLI. Los adaptadores y sus fixtures se validan por separado de la
ejecución en sesiones reales de los agentes. En Codex, consulta
`.agent/scripts/codex_runtime_smoke.py` para probar la superficie que uses
(CLI o Desktop). Solo un resultado `runtime_smoke=pass` de esa superficie
acredita que se ejecutó con el host real.

### Archivos de adopción

Copia estos elementos si vas a incorporar la plantilla archivo por archivo.
Revisa los archivos equivalentes que ya existan en tu proyecto antes de sustituirlos.

```text
AGENTS.md
CLAUDE.md
GEMINI.md
GUARDRAILS_MASTER.md
README.md
llms.txt
.agent/
.agents/
.claude/settings.json
.claude/commands/
.claude/agents/
.claude/rules/
.opencode/commands/
.opencode/agents/
_project/
tests/gate_calibration.py
opencode.json
.gitignore
.gitattributes
```

## Código y pruebas

- `.agent/workflows/manifest.json` indexa los flujos y los comandos generados.
- `.agent/scripts/state_manager.py` gestiona el estado y sus proyecciones.
- `_project/` contiene documentos de partida y un estado vacío de proyecto.
- `tests/` contiene pruebas unitarias y de integración con fixtures sintéticos.

```bash
python3 -m pip install PyYAML
bash .agent/scripts/ci_smoke.sh
```

Instala PyYAML en un entorno virtual para la suite. El motor de estado y el
diagnóstico básico usan la biblioteca estándar. El smoke comprueba el
diagnóstico, la suite, los comandos generados y la adopción desde una copia
limpia. En un clon Git también comprueba la suite con historial superficial.

[Licencia MIT](LICENSE)
