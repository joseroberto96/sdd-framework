# Skills Core del Framework

> Skills incluidas por defecto. No requieren `/instalar-skills` porque forman parte del framework.

## Política

- Las skills core se activan por señales de la tarea.
- Si una skill core cubre la necesidad, usarla antes de proponer instalar una skill externa.
- Las skills core no sustituyen los workflows obligatorios; los complementan.
- Si una skill externa contradice una skill core, prevalecen `CLAUDE.md`, `.agent/rules/architecture_constraints.md` y la skill core aplicable.

## Catálogo Core

| Skill | Archivo | Activación |
|---|---|---|
| Agile Product | `.agent/skills/agile_product.md` | Descubrimiento, PRD, scope, MVP, backlog, ACs, entrevistas, prioridades |
| Arquitectura Core | `.agent/skills/architecture_core.md` | Tech design MVP, interfaces, dependencias, refactors, DB, decisiones técnicas |
| Seguridad Core | `.agent/skills/security_core.md` | Auth, permisos, inputs, datos sensibles, publicación, dependencias |
| Calidad y Testing | `.agent/skills/quality_testing_core.md` | Features Medium/High, bugs, regresiones, contratos, refactors |
| Delivery y Release | `.agent/skills/delivery_core.md` | GitHub, fase 0, MVP, releases, handoff, sesiones |
| Solución Mínima Correcta | `.agent/skills/minimal_solution_core.md` | Sobreingeniería, YAGNI, bloat, boilerplate, dependencias innecesarias, abstracciones especulativas |
| Diseño Frontend | `.agent/skills/frontend_design.md` | UI/UX, layout, copy visible, componentes |
| Compatibilidad Web | `.agent/skills/cross_platform_web.md` | HTML, CSS, JS, browser, responsive |
| Prompt Lean | `.agent/skills/prompt_lean.md` | Contexto mínimo para módulos con mapa de dependencias |
| Contratos de Specs | `.agent/skills/spec_contracts.md` | Lectura selectiva de `data_model.md` y `api_contract.yaml` por módulo activo sin cargar el archivo completo |
| Lenguaje de Builder | `.agent/skills/builder_language_core.md` | Traducir planes, decisiones, riesgos y cierres tecnicos a lenguaje cotidiano con analogias utiles |
| Traducción de Decisiones | `.agent/skills/decision_translation_core.md` | Decisión técnica con impacto en producto, fork de visión, tradeoff que el usuario siente, digest en hitos |
| Deployment / Operación (L5) | `.agent/skills/deployment_core.md` | Deploy, CI/CD, secretos por entorno, "subir a producción", escenarios Docker/systemd/PaaS, recovery, sync dev↔prod; heurística no prescriptiva, no ejecuta deploys |

## Matriz de Activación

| Señal | Activar primero | Proponer externa solo si |
|---|---|---|
| Agile, scope, MVP, backlog | Agile Product | Hace falta metodología específica del equipo |
| Idea amplia, usuario/MVP poco claro | Agile Product | Hace falta investigación de producto específica del dominio |
| Arquitectura, refactor, interfaces | Arquitectura Core | Stack/patrón muy específico no cubierto |
| Diseño técnico de MVP | Arquitectura Core + Calidad y Testing | Requiere patrón/framework especializado |
| Seguridad, auth, permisos | Seguridad Core | Requiere análisis especializado o estándar concreto |
| Tests, bugs, regresiones | Calidad y Testing | Framework de testing específico no cubierto |
| Sobreingeniería, bloat, dependencias innecesarias, YAGNI | Solución Mínima Correcta | El usuario pide explícitamente instalar Ponytail u otra skill externa con comandos/hooks |
| GitHub, publicación, fase 0 | Delivery y Release | Requiere plataforma/tooling no cubierto |
| UI/UX | Diseño Frontend + Agile Product | Se necesita sistema visual avanzado |
| HTML/CSS/JS browser | Compatibilidad Web + Calidad y Testing | Se requiere experto framework específico |
| Módulo que toca dominio (`data_model.md`) o expone HTTP (`api_contract.yaml`) | Contratos de Specs + Prompt Lean | — |
| Plan, decision, riesgo, bloqueo o cierre explicado al usuario | Lenguaje de Builder | — |
| Decisión que toca el producto (cómo se usa/se siente) o tradeoff que el usuario siente | Traducción de Decisiones | — |
| Deploy, CI/CD, secretos por entorno, "subir a producción", Docker, systemd, PaaS, recovery, sync dev↔prod | Deployment / Operación (L5) | Antes del primer deploy a un entorno nuevo o al cerrar un release con release_profile activo | Core incluida |

## Regla de Propuesta

Formato máximo:

```markdown
Activaré la skill core [nombre] para esta tarea.
También podría convenir una skill externa de [tipo] si quieres profundizar en [motivo].
```

No proponer externa si la core basta.
