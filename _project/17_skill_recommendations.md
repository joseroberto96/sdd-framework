# Recomendaciones de Skills

> Registro de oportunidades donde una skill podría mejorar la calidad, velocidad o criterio del agente.
> Ninguna recomendación instala nada por sí sola; la instalación pasa por `/instalar-skills`.

## Política

- Proponer skills cuando haya una señal clara de beneficio: UI/UX, JS/CSS, seguridad, testing, performance, accesibilidad, dominio, framework específico o errores repetidos.
- No proponer la misma skill en cada respuesta; si ya fue rechazada o pospuesta, respetar la decisión hasta que cambie el contexto.
- Activar skills core de `_project/19_core_skills.md` antes de proponer instalar nuevas.
- Preferir skills locales existentes antes de instalar nuevas.
- Las skills externas deben tener fuente exacta: repo con tag/commit, paquete@versión o archivo local.
- Toda instalación queda en `.agent/skills/imported/` salvo aprobación explícita.

## Catálogo de Señales

| Señal detectada | Skill sugerida | Cuándo sugerir | Instalación |
|---|---|---|---|
| UI, UX, diseño visual, CSS complejo, responsive, accesibilidad | UI/UX o frontend design | Antes de implementar o refactorizar pantallas | Usar local si existe; si no, proponer `/instalar-skills` |
| JavaScript, TypeScript, HTML, CSS, DOM, navegador | Experto JS/HTML/CSS | Si la tarea toca interacción, layout o compatibilidad browser | Proponer skill técnica local |
| Seguridad, auth, permisos, datos sensibles | Security review | Antes de endpoints, auth o acciones de escritura | Usar reglas existentes o instalar skill especializada |
| Tests, bugs repetidos, regresiones | Testing/TDD | Antes de arreglos complejos o bugs recurrentes | Usar `/tdd`; sugerir skill si faltan patrones |
| Sobreingeniería, bloat, boilerplate, dependencias innecesarias, YAGNI | Solución Mínima Correcta | Antes de proponer una skill externa de minimalismo | Core incluida; Ponytail externo solo si se aprueban plugin/hooks con fuente exacta |
| Dominio específico de negocio | Experto de dominio | Si falta lenguaje ubicuo o reglas de negocio | Crear/enriquecer `.agent/skills/domain/` |
| Performance, cargas lentas, consultas pesadas | Performance profiling | Si la tarea menciona lentitud o escala | Proponer skill según stack |
| Scope, MVP, backlog, ACs, agile | Agile Product | Al definir o recortar módulos | Core incluida |
| Arquitectura, interfaces, refactor, dependencias | Arquitectura Core | Antes de cambios estructurales | Core incluida |
| GitHub, fase 0, releases, handoff | Delivery y Release | Antes de publicar o compartir | Core incluida |
| Deploy, CI/CD, secretos por entorno, "subir a producción", Docker, systemd, PaaS, recovery, sync dev↔prod | Deployment / Operación (L5) | Antes del primer deploy a un entorno nuevo o al cerrar un release con release_profile activo; **engarga con `/publicar-github` cuando el release toca deploy/operación** | Core incluida |
| Plan, decision, riesgo, bloqueo o cierre explicado al usuario | Lenguaje de Builder | Antes de pedir decision o presentar resumen al builder | Core incluida |

## Registro

| Fecha | Módulo/tarea | Señal | Recomendación | Estado | Decisión |
|---|---|---|---|---|---|
| — | — | — | — | — | — |

## Estados

- `propuesta`: sugerida, pendiente de respuesta.
- `aprobada`: el usuario aprobó instalar o usar.
- `pospuesta`: no instalar ahora.
- `rechazada`: no volver a sugerir salvo cambio de contexto.
- `instalada`: ya pasó por `/instalar-skills`.
