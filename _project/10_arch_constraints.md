---
description: Constraints arquitectónicos específicos de este proyecto — completar al inicializar
---

# Constraints Arquitectónicos — [NOMBRE_PROYECTO]

<!-- CONTEXT_SOURCE: nfr -->
## NFR y aplicabilidad

La aceptación de cada módulo debe declarar `NFR: required` si activa NFR
adicionales, o `NFR: n/a — justificación concreta` si no los activa. No se
infiere aplicabilidad por ausencia de una sección o clave. Cuando sea
`required`, documentar el objetivo medible, el umbral y la prueba que lo
verifica en la sección correspondiente de este archivo.

La sección de evidencia del módulo debe usar el formato `## NFR del módulo:
<id>` y cada entrada debe declarar `Objetivo`, `Métrica`, `Umbral` y
`Verificación`. La verificación debe apuntar a un test, archivo o AC concreto.

El gate automático comprueba lo verificable sin ambigüedad: que la sección tenga
contenido real y que las referencias se resuelvan. La evidencia es lo que está
citado dentro de una etiqueta de evidencia — `— evidencia:`, o el propio campo
`Verificación:` de cada entrada, que cuenta como etiqueta —; la prosa fuera de
una etiqueta no se interpreta como referencia, ni para bloquear ni para pasar
(una sección `NFR` sin ninguna `Verificación:` citada bloquea por no citar
evidencia, no por juzgar la prosa). Resolverse es que el fichero exista, o que
el identificador (`AC-*`, `RN-*`, `ADR-*`, `BUG-*`) esté declarado con su
casilla **marcada** o como encabezado (con espacio tras las almohadillas) en su
documento canónico en la raíz de `_project/` — citarlo en otro fichero o
subdirectorio no lo declara, y el gate no puede distinguir un identificador
recién añadido de uno inventado en ese mismo documento; esa distinción es de la
revisión humana. **No** comprueba que estén los cuatro campos ni cómo se
reparten entre entradas: eso lo verifica la revisión humana en `/cierre-modulo`. Escribir los cuatro campos sigue siendo
obligatorio; lo que cambia es quién lo comprueba. Los ejemplos, comentarios y
valores pendientes no cuentan como evidencia, y el bloque normativo de este
archivo tampoco.
<!-- END_CONTEXT_SOURCE: nfr -->

> Este archivo se completa durante `/nuevo-proyecto` o al inicio de la fase 1.
> Las reglas del agente (no modificar) están en `.agent/rules/architecture_constraints.md`.

---

## Stack tecnológico

| Capa | Tecnología | Versión |
|------|-----------|---------|
| Backend | [ej: FastAPI, Django, Express] | — |
| Frontend | [ej: React, Vanilla JS, Vue] | — |
| Base de datos | [ej: SQLite, PostgreSQL, MongoDB] | — |
| Auth | [ej: JWT, OAuth2, sesiones] | — |
| Otros | — | — |

### Camino de escalado (4.1.0 endurecido, FW-133)

> Registrado en `/tech-design-mvp` paso 3, vía `register_decision` (ADR). El
> MVP sigue siendo mínimo — esto no es sobreingeniería anticipada, es dejar
> explícito por dónde se rompe y por dónde se cambia, para no rehacer la
> pieza entera cuando escale.

| Pieza del stack | Por qué esta elección (NFR + perfil de builder citados) | Qué se rompe a ×10 / ×100 | Costura de escape |
|---|---|---|---|
| [ej: SQLite] | [ej: escalabilidad declarada <10 usuarios en 10_arch_constraints.md, builder solo-dev] | [ej: escrituras concurrentes a ×100] | [ej: acceso tras un repositorio — migrar a Postgres sin tocar dominio] |
| [COMPLETAR en /tech-design-mvp — toda pieza sin costura de escape se declara explícitamente puerta de una sola dirección, con su coste] | — | — | — |

---

## Convenciones de código

- [ej: Type hints obligatorios en todas las funciones]
- [ej: Docstrings en español en funciones públicas]
- [ej: Nombres de variables en inglés]
- [COMPLETAR según convenciones del equipo]

---

## Reglas de base de datos

### Baseline obligatorio

- Toda migración debe ser idempotente (`IF NOT EXISTS` o equivalente).
- Los cambios destructivos (`DROP`, renames con pérdida, truncados) requieren aprobación explícita del usuario y plan de rollback.
- No construir queries con concatenación de strings o interpolación de input de usuario.
- Toda escritura debe pasar por validación de esquema o tipos explícitos.

### Reglas específicas del proyecto

- [ej: Soft delete en entidades maestras — nunca DELETE físico]
- [ej: Migraciones siempre idempotentes (IF NOT EXISTS)]
- [ej: No modificar registros históricos — solo añadir observaciones]
- [COMPLETAR según modelo de datos del proyecto]

---

## Reglas de seguridad del proyecto

### Baseline obligatorio

- No hardcodear secrets, API keys, passwords, tokens ni IDs de producción.
- No registrar en logs tokens, passwords, PII ni payloads sensibles completos.
- Todo input externo debe validarse antes de usarse en DB, filesystem, red, templates o comandos.
- Los errores visibles al usuario no deben exponer stack traces, estructura de DB, rutas internas ni IDs internos innecesarios.
- Toda acción de escritura debe verificar autenticación y autorización antes de ejecutar efectos.
- El acceso a recursos por ID debe validar pertenencia/rol; no confiar en IDs enviados por el cliente.
- No ejecutar comandos, evals, templates dinámicos o parsers peligrosos con input no confiable.
- Toda dependencia nueva requiere aprobación y registro en `03_project_state.json → dependencies`.

### Reglas específicas del proyecto

- [ej: Toda escritura requiere autenticación verificada]
- [ej: Inputs del usuario validados con schema antes de usar]
- [ej: No exponer IDs internos en respuestas de error]
- [COMPLETAR según requisitos de seguridad del dominio]

---

## Reglas de arquitectura

### Baseline obligatorio

- Preservar funcionalidad existente salvo petición explícita del usuario o aprobación tras explicar impacto.
- No crear dependencias directas entre módulos sin contrato declarado en el artefacto correcto: `03_contracts.md` para interfaces no-HTTP, `data_model.md` para entidades persistidas y `api_contract.yaml` para HTTP/REST.
- No implementar un módulo nuevo sin ACs definidos en `08_module_acceptance.md`.
- No implementar un módulo con estado `blocked` en `03_project_state.json`.
- No saltarse `11_file_dependencies.md` para módulos con interfaz declarada.
- Configuración por variables de entorno o archivo de config; no hardcodear valores de entorno.

### Reglas específicas del proyecto

- [ej: Toda lógica de negocio en el backend — frontend solo consume API]
- [ej: Cada módulo es independiente — no importar lógica entre módulos]
- [ej: No hardcodear valores de configuración — usar variables de entorno]
- [COMPLETAR según decisiones arquitectónicas del proyecto]

---

## Compatibilidad cross-platform (web)

> Completar si el proyecto incluye interfaz web. Aplica a todos los módulos frontend.

**Plataformas objetivo:**
- [ ] Windows — Chrome, Edge, Firefox
- [ ] macOS — Safari, Chrome
- [ ] Android — Chrome
- [ ] iOS — Safari

**Nivel de soporte:**
- Pleno (feature parity): [listar plataformas]
- Degradado graceful (funcional pero sin todas las features): [listar si aplica]
- No soportado: [listar si aplica]

**Reglas activas para este proyecto:**
- Pointer Events obligatorios en lugar de solo Mouse Events
- `font-size: 16px` mínimo en inputs (iOS anti-zoom)
- Alturas full-screen con `dvh` o `safe-area-inset` en lugar de `100vh`
- Scripts npm compatibles con Windows: usar `cross-env`, `rimraf`, `mkdirp`
- Imports con casing exacto del filesystem (sensible en producción Linux)
- `.gitattributes` con `* text=auto` en el repositorio
- Parseo de fechas con ISO 8601 completo + zona horaria

**Excepciones declaradas:**
- [Si alguna plataforma no aplica al proyecto, documentarlo aquí con justificación]

---

## Invariantes de dominio

> Copiar desde `09_domain_profile.md → Invariantes de dominio` al declarar módulos críticos.

[COMPLETAR tras definir el perfil de dominio]
