# Protocolo extendido del framework

> Este archivo lo cargan los workflows cuando lo necesitan.
> No se incluye en el contexto de arranque normal.
> Referencia rápida siempre activa: `CLAUDE.md`

---

## Portabilidad entre entornos

- Aunque `CLAUDE.md` es el nombre de este archivo en Claude Code, funciona como constitución portable. En Codex/OpenAI agents existe `AGENTS.md` que apunta a estas mismas reglas.
- En v3, la fuente canónica de workflows es `.agent/workflows/manifest.json` + `.agent/workflows/*.md`. Los comandos de `.claude/commands/` son wrappers generados por `.agent/scripts/sync_claude_commands.py`.
- Detectar el adaptador activo en `.agent/adapters/`.
- Registrar capacidades reales en `15_environment_profile.md`.
- Si no hay slash commands, ejecutar `.agent/workflows/[comando].md` manualmente.
- Si hay dispatcher sin aislamiento read-only, usar revisor distinto bajo
  contrato no-write y declarar `INDEPENDENT_SUBAGENT_NO_ISOLATION`; usar
  `SECUENCIAL_NO_AISLADO` solo si no hay dispatcher.

## SUBAGENT_WAIT_LIFECYCLE

Este contrato es canónico y portable. Los workflows y subagentes que esperen
revisión o auditoría deben enlazar esta sección literalmente y no duplicar sus
reglas en versiones divergentes.

- `WAIT_TIMEOUT_NONTERMINAL`: el timeout no es un veredicto terminal.
- El adaptador puede observar `RUNNING` o `COMPACTING`; ambos son estados
  observacionales no terminales previos al resultado.
- Los estados terminales de resultado son únicamente `PASS`, `FAIL`,
  `NEEDS_ATTENTION` y `UNAVAILABLE`.
- El estado solo es terminal si el adaptador lo declara explícitamente.
- Mientras haya revisión activa, el árbol queda congelado.
- Silencio, compactación o demora no producen veredicto parcial.
- Si el adaptador no responde con estado terminal, el padre continúa
  consultando hasta obtener `PASS`, `FAIL`, `NEEDS_ATTENTION` o `UNAVAILABLE`,
  según soporte del entorno, o hasta el tope de espera que declare el
  adaptador (en Codex, 20 min sin progreso observable): pasado el tope, el
  padre puede decidir de forma explícita interrumpir y seguir en el hilo
  principal; el subagente queda como `NEEDS_ATTENTION`, nunca `PASS` ni `FAIL`
  (ADR-072, que modifica ADR-057).
- Los workflows consumidores enlazan este contrato común y no reexpresan reglas
  propias que contradigan esta sección.

---

## Capa constitucional

`GUARDRAILS_MASTER.md` (raíz del proyecto) define la jerarquía canónica de precedencia de calidad, la graduación de rigor por tier, las 5 dimensiones de calidad y el Protocolo de 3 Fases (THINK → APPROVE → CODE+LOG) que rigen todo el desarrollo. No se desactiva por conveniencia.

El framework es la capa operacional portable: workflows, artefactos y máquina de estados. Guardrails v4.2 es la capa constitucional de calidad: decide qué gana ante trade-offs. Si hay discrepancia, prevalece `GUARDRAILS_MASTER.md` en calidad/precedencia y `.agent/rules/architecture_constraints.md` en máquina de estados.

---

## Flujo completo de sesión

```
/arrancar → [/diseñar si el producto/fase no está claro] → /scope-siguiente → [/enriquecer-ticket si la HU necesita detalle técnico] → /entrevistar (si hay ambigüedad o cambio estructural) → [/tdd o implementar] → /cierre-modulo → /validate-arch (opcional) → /publicar-github (si aplica) → /gycd → compactación nativa del entorno
```

1. Leer `_project/core.md` (via `/arrancar`)
2. Si el proyecto/fase todavía es una idea amplia, ejecutar `/diseñar` — detecta si faltan descubrimiento, PRD o diseño técnico y los ejecuta en secuencia
3. Ejecutar `/scope-siguiente` para abrir el módulo y clasificar `module_specs` inicial.
4. Si la HU o el módulo necesita más detalle técnico, ejecutar `/enriquecer-ticket` antes de codificar.
5. Si el módulo es nuevo, tier_2 o tiene ambigüedad de negocio, ejecutar `/entrevistar` antes de codificar.
6. Verificar que el módulo tiene ACs en `08_module_acceptance.md` — si no, definirlos antes de codificar.
7. La interfaz pública y el mapa de dependencias se declaran en `/enriquecer-ticket` (paso 5.2) al detallar el módulo.
8. Antes de modificar código existente, ejecutar `/cambio-seguro`.
9. Antes de CODE en planes, tickets enriquecidos, TDD, refactors o cambios sobre código existente, ejecutar el workflow interno `.agent/workflows/preflight-review.md`: subagente `preflight-reviewer` aislado si existe; dispatcher sin aislamiento → `INDEPENDENT_SUBAGENT_NO_ISOLATION`; sin dispatcher → `SECUENCIAL_NO_AISLADO`.
10. En tier_1/tier_2 con lógica que probar, preferir `/tdd`: inventario anti-regresión → test rojo → implementación mínima → refactor.
11. Implementar según scope — una tarea por mensaje (tareas cohesivas del mismo módulo pueden agruparse).
12. El estado se actualiza automáticamente dentro de `/cierre-modulo` y `/nuevo-bug`; una decisión sin cambio de código se registra con `mutate_state register_decision`.
13. Al terminar el módulo: `/cierre-modulo` ejecuta Spec Gate 3.7 y Ejecutable Gate 3.8; luego `/validate-arch` si es hito de fase.
14. Si se completa fase 0, esqueleto publicable o MVP compartible, ejecutar `/publicar-github`.
15. Si se completaron 3+ módulos sin higiene de diseño, sugerir `/refactorizar-arquitectura`.

---

## Orientación de siguiente acción

Todo turno que devuelve el control al builder, con workflow o sin él, acaba en
su cierre. El contrato vive en la sección «Cierre de turno» de
`.agent/rules/runtime_core.md` (forma, cinco casos y comando por tipo de
trabajo); el detalle, en `.agent/skills/decision_translation_core.md`; lo
verifica `.agent/scripts/turn_close.py`. Esta sección no lo repite.

Reglas que añade este documento:

- Una sola opción recomendada. No listar rutas alternativas: si hay que elegir, el cierre es la forma de decisión.
- El comando recomendado debe mantener el flujo del framework, no saltarse gates.

Matriz base. Es el ruteo interno del agente, no lo que se escribe en el
cierre: allí solo caben comandos del builder (`/ciclo` conduce la maquinaria
interna).

| Estado | Workflow que corresponde |
|---|---|
| Template sin inicializar | `/nuevo-proyecto` o `/migrar-proyecto` según contexto |
| Producto/fase poco clara | `/diseñar` |
| Sin módulo activo listo | `/scope-siguiente` |
| HU/módulo sin detalle técnico | `/enriquecer-ticket` |
| Ambigüedad bloqueante | `/clarificar` |
| Cambio sobre código existente | `/cambio-seguro` |
| Bug no entendido | `/debug` |
| Implementación tier_1/tier_2 con lógica que probar | `/tdd` |
| Módulo listo para validar | `/cierre-modulo` |
| Hito de fase | `/validate-arch` |
| MVP publicable | `/publicar-github` |
| Cierre de sesión | `/gycd` |

---

## Invariantes del sistema

`_project/03_invariants.md` define las **reglas de negocio**, **flujos de datos** y **dominio** del proyecto.
Este documento es **de solo lectura para el AI**. Reglas absolutas:
- El AI **nunca** modifica `03_invariants.md` directamente.
- El único mecanismo de modificación es `/actualizar-invariante` con aprobación explícita del usuario.
- Antes de cualquier cambio de código, `cambio-seguro` verifica que no viola este documento.
- Antes de declarar cualquier interfaz, `/enriquecer-ticket` (paso 5.2) verifica que no contradice este documento.
- `cierre-modulo` bloquea el cierre si el módulo viola algún invariante.

---

## Máquina de estados de módulos

> Fuente canónica única: `.agent/rules/architecture_constraints.md` → sección "Invariantes de Estado".
> Aquí solo se enumera el contexto operativo; las transiciones y comandos autorizados viven en el archivo de reglas.

**Notas operativas** (no duplican la fuente, complementan):

- El campo `module_statuses` en `03_project_state.json` es la fuente de verdad del estado de cada módulo.
- El campo `work_tiers` en `03_project_state.json` registra el rigor aplicado por módulo/tarea (`tier_0`, `tier_1`, `tier_2`) con razón y fecha.
- El campo `module_specs` en `03_project_state.json` registra si el módulo requiere `data_model` y/o `api_contract`, con justificación obligatoria para `n/a`.
- `closure_rejections` — array de rechazos de cierre. Cada entrada: `{"module_id", "reason": "critical_bug_exists | clar_pending | ac_incomplete | ac_evidence_missing | module_specs_missing | data_model_missing | api_contract_missing", "timestamp": "ISO8601"}`.
- Al acumular ≥ 2 entradas para un mismo módulo, `/cierre-modulo` bloquea el cierre y recomienda organizar "Estabilización" sin saltarse gates duros.
- Se limpia al cerrar con éxito.

---

## Artefactos _project/ — referencia completa

🔴 = núcleo obligatorio · 🟢 = extensión opcional

> **Modelo de estado (motor MCP):** `03_project_state.json` es la fuente única de verdad. Las vistas `core.md`, `06_bug_fixes.md`, `07_pending_clarifications.md`, `03_arch_decisions.md` y `00_session_brief.json` son **proyecciones de solo lectura** generadas por `state_manager.py` en cada mutación (`mutate_state`); llevan banner de autogeneración y no se editan a mano (un guard rechaza la sobrescritura manual). La columna «Cuándo actualizar/cargar» indica cuándo se leen o qué evento dispara su regeneración.

### Capa mínima — leídos en arranque (vía core.md)
| Archivo | Propósito | Cuándo actualizar |
|---|---|---|
| 🔴 `core.md` | Proyección read-only de continuidad de sesión — reemplaza la lectura paralela de 6 archivos | Regenerada por `state_manager.py` en cada mutación |

### Núcleo — bajo demanda por workflows
| Archivo | Propósito | Cuándo cargar |
|---|---|---|
| 🔴 `00_session_brief.json` | Proyección read-only — brief compacto regenerado junto a `core.md` | Regenerada por `state_manager.py` en cada mutación |
| 🔴 `03_project_state.json` | Fase, paso, constraints, dependencias, pendientes | Al ocurrir una mutación vía `mutate_state` |
| 🔴 `04_active_scope.md` | Tareas `[ ]` pendientes del sprint activo | Al completar módulo (marcar → archivar en `/cierre-modulo`) |
| 🔴 `06_bug_fixes.md` | Bugs detectados y fixes (con causa raíz) | Al encontrar/cerrar bugs |
| 🔴 `08_module_acceptance.md` | Criterios de aceptación por módulo | Antes de implementar; marcar ✅ al verificar |

### Extensiones — cargadas si el proyecto las usa
| Archivo | Propósito | Cuándo cargar |
|---|---|---|
| 🟢 `09_domain_profile.md` | Perfil de dominio + expertos activos | Al inicializar y al añadir expertos |

### Bajo demanda — no cargar en arranque
| Archivo | Propósito | Cuándo cargar |
|---|---|---|
| 🔴 `01_product_requirements.md` | PRD/MVP vivo | En descubrimiento, PRD, scope o cambios de producto |
| 🔴 `03_invariants.md` | Reglas de negocio — solo via `/actualizar-invariante` | En `/cambio-seguro`, `/enriquecer-ticket` y `/cierre-modulo` |
| 🔴 `03_arch_decisions.md` | Decisiones arquitectónicas completas | Solo en `/validate-arch` o debugging sistémico |
| 🔴 `03_contracts.md` | Índice narrativo de interfaces no-HTTP y punteros HTTP; entidades viven en `data_model.md` | Solo en `/enriquecer-ticket` o `/validate-arch` |
| 🔴 `02_technical_plan/data_model.md` | Modelo formal de entidades persistidas en Mermaid | Cuando `module_specs[módulo].data_model` es `required` o `done` |
| 🔴 `02_technical_plan/api_contract.yaml` | Contrato HTTP/REST formal en OpenAPI 3.1 | Cuando `module_specs[módulo].api_contract` es `required` o `done` |
| 🔴 `04_scope_archive.md` | Historial de tareas completadas | Solo lectura histórica |
| 🟢 `05_test_history.md` | Pruebas funcionales con el cliente | Tras sesiones de validación |
| 🟢 `07_pending_clarifications.md` | Preguntas abiertas al cliente | Al detectar ambigüedad de negocio |
| 🟢 `10_arch_constraints.md` | Constraints técnicos del proyecto | Al inicializar y al tomar decisiones arquitectónicas |
| 🟢 `11_file_dependencies.md` | Mapa de archivos lee/escribe por módulo | Al ejecutar `/enriquecer-ticket` o `/lean-context --register` |
| 🟢 `12_design_profile.md` | Perfil visual/UX | Al inicializar UI y al cambiar sistema visual |
| 🟢 `13_session_journal.md` | Ledger temporal de cambios no persistidos | Durante sesión; se limpia en `/gycd` |
| 🟢 `14_skill_provenance.md` | Procedencia, versión y estado de skills externas | Al instalar o importar skills |
| 🟢 `15_environment_profile.md` | Capacidades reales del entorno activo | Al iniciar en una herramienta nueva |
| 🟢 `16_release_profile.md` | GitHub, fase 0, distribución y publicaciones | Al ejecutar `/publicar-github` |
| 🟢 `17_skill_recommendations.md` | Recomendaciones de skills por señales de tarea | Al ejecutar `/sugerir-skills` |
| 🟢 `18_native_command_audit.md` | Duplicados entre comandos nativos y workflows | Al configurar entorno nuevo |
| 🟢 `19_core_skills.md` | Skills core incluidas por defecto y matriz de activación | Al revisar o ampliar habilidades base |
| 🟢 `20_builder_profile.md` | Nivel, preferencias y forma de colaboración del constructor | Al inicializar, migrar o adaptar |
| 🟢 `21_knowledge_map.md` | Mapa de conocimiento, temas sensibles y quick win inicial | En onboarding o cambios de fase |
| 🟢 `22_lessons.md` | Lecciones aprendidas del proyecto | Solo se crea on-demand con `/aprender-de-error` y aprobación explícita |

---

## Specs vivas como contrato

- Entidades persistidas: `_project/02_technical_plan/data_model.md`.
- Endpoints HTTP/REST: `_project/02_technical_plan/api_contract.yaml`.
- Interfaces no-HTTP: `_project/03_contracts.md`.
- Clasificación por módulo: `_project/03_project_state.json → module_specs[módulo]`.

Transiciones operativas de specs:

1. `/diseñar` propone specs y trade-offs; no debe asumir que el estado ya quedó clasificado.
2. `/scope-siguiente` abre el módulo y escribe la clasificación inicial (`required` o `n/a` con justificación).
3. `/enriquecer-ticket` refina la clasificación y puede completar specs faltantes antes de implementar.
4. `/cambio-seguro` usa `spec_contracts` para inventariar entidades/endpoints existentes en `PRESERVAR`.
5. `/cierre-modulo` paso 3.7 bloquea si `module_specs` falta o si los artefactos formales requeridos están incompletos.
6. `/cierre-modulo` paso 3.8 verifica comportamiento ejecutable; la evidencia es suplementaria a los ACs, no los sustituye.
7. `/validate-arch` comprueba coherencia código ↔ specs y punteros `03_contracts.md` ↔ `api_contract.yaml`.

---

## Reglas cargadas automáticamente

- `.agent/rules/architecture_constraints.md` — invariantes del agente (siempre activos)
- `.agent/skills/` — skills contextuales (se activan según la tarea)
- `.agent/skills/domain/` — expertos de dominio (se activan según `09_domain_profile.md`)
- `llms.txt` — mapa rápido para agentes y navegadores AI del repositorio

---

## Descubrimiento y onboarding

- Si el objetivo del usuario todavía es amplio, ejecutar `/descubrir-producto` antes de diseñar o codificar.
- Antes de generar documentos grandes o planes técnicos, hacer `Verification Echo`: repetir entendimiento, MVP, no-MVP, criterios de éxito y dudas.
- Mantener `_project/01_product_requirements.md` como PRD vivo y ligero.
- Adaptar explicación y autonomía con `_project/20_builder_profile.md`.
- Usar `_project/21_knowledge_map.md` para saber qué temas requieren explicación.

---

## Pruebas y debugging

- Antes de CODE en planes no triviales, usar `.agent/workflows/preflight-review.md`
  para revisar Risk Surface, scope, ACs, contratos, tests y gates faltantes.
- En tier_1/tier_2 con lógica que probar, preferir `/tdd` sobre implementación directa.
- No ejecutar suites enormes como primer paso conversacional.
- `/debug` sigue el ciclo: reproducir → minimizar → hipotetizar → instrumentar → arreglar → test de regresión.

---

## Optimización de contexto

- Durante implementación pura, evitar exploración global. Usar `rg`, `11_file_dependencies.md` o rutas concretas.
- `/gycd` usa lectura mínima: solo los archivos necesarios para el evento + `core.md` + `13_session_journal.md`.

---

## Regla anti-regresión — detalle completo

- No quitar, desactivar ni reemplazar funcionalidad existente salvo petición explícita del usuario.
- Si una mejora requiere eliminar o cambiar comportamiento previo, preguntar antes y explicar el impacto.
- Al refactorizar, preservar contratos, rutas, comandos y comportamientos públicos salvo aprobación.
- Antes de tocar código existente, generar un inventario `PRESERVAR` con funciones, endpoints, comandos, rutas, estados UI, validaciones, permisos, formatos de datos y tests/checks relevantes.
- Clasificar todo cambio como `ADITIVO`, `COMPATIBLE` o `DESTRUCTIVO`; ante duda, tratarlo como `DESTRUCTIVO`.
- Una aprobación general para refactorizar no autoriza eliminar funcionalidad. Los cambios `DESTRUCTIVO` requieren aprobación explícita separada.
- `/cierre-modulo` debe bloquear el cierre si no hay evidencia de preservación o verificación anti-regresión para código existente modificado.

---

## Supply-chain de skills y MCPs

**Orden de prioridad de fuentes:**

1. **Plugin oficial `claude-code-setup`** (`claude.com/plugins/claude-code-setup`) — fuente primaria. Analiza el proyecto en modo solo lectura y recomienda skills, MCP servers, hooks, subagents y slash commands. Invocar: *"recommend automations for this project"*.
   Registrar en `14_skill_provenance.md` con fuente `claude.com/plugins/claude-code-setup` y estado `aprobado`.

2. **Mecanismo existente del framework** — fallback. Repos Git con tag/commit SHA, paquetes npm/pip con versión exacta, o archivos locales.

- Al inicio de una tarea con señales claras de UI/UX, JS/CSS, seguridad, testing, performance o dominio, ejecutar mentalmente `/skills` (modo sugerir).
- Activar primero las skills core de `_project/19_core_skills.md`.
- Proponer skills en máximo 4 líneas y continuar si no son bloqueantes.
- No ejecutar instaladores remotos sin versión exacta, tag o commit aprobado.
- Registrar toda skill/MCP externo en `14_skill_provenance.md`.
- Las skills se instalan por defecto localmente en `.agent/skills/imported/`.
- Usar `/skills instalar [fuente@versión]` para repos, paquetes o archivos provistos por el usuario.

---

## GitHub y distribución

- Fase 0 = esqueleto mínimo publicable: framework inicializado, README, `.gitignore`, `.gitattributes`, sin secretos.
- No hacer push sin aprobación explícita.
- No crear repos públicos por defecto; si falta visibilidad, usar privado.
- Registrar URL, rama, tags/hitos e instrucciones de actualización en `_project/16_release_profile.md`.

---

## Mantenimiento del framework

- `.agent/workflows/` contiene la lógica real de los workflows.
- `.agent/workflows/manifest.json` declara comandos, wrappers y duplicados nativos.
- `.claude/commands/` contiene wrappers generados; no guardar lógica ahí.
- Tras cambiar workflows o manifest: `python3 .agent/scripts/sync_claude_commands.py`.
- Antes de publicar o migrar: `python3 .agent/scripts/framework_doctor.py`.

---

## Comandos nativos vs workflows portables

- Si el entorno ofrece un comando nativo equivalente, usar el nativo.
- No duplicar slash commands para capacidades nativas como compactación.
- Mantener workflows portables en `.agent/workflows/` como fallback.
- Si se usa fallback, declarar brevemente el motivo.

---

## Guardas de integridad y cachés (know-how portado de graphify, FW-D/E/F)

Reglas de integridad para scripts y orquestación del framework.

### Guardas de escritura (FW-F)

- **Shrink-guard**: un regenerador nunca sobrescribe un artefacto bueno con uno
  sospechosamente menor sin `--force` explícito; el encogimiento inesperado es
  síntoma de fuente corrupta, no de limpieza. Implementación de referencia:
  `lessons_reflect.py` (compara `> Fuente: N lecciones.` del digest existente).
- **Guard de vacío ANTES de escribir**: si la fuente parseada quedó vacía, no
  escribir nada — jamás publicar un artefacto vacío sobre uno con contenido.
- **Diagnóstico que avisa sin abortar**: los chequeos de salud post-generación
  hacen visible la corrupción silenciosa pero no bloquean el flujo (el doctor
  distingue FAIL de WARN por este criterio).

### Política de invalidación de cachés (FW-D)

- **Resultados deterministas** (parsers, escáneres, renders): cachear
  namespaced **por versión de la herramienta** — un fix del generador no debe
  seguir sirviendo resultados pre-fix; barrer versiones viejas al primer uso.
- **Resultados derivados por LLM** (costosos de regenerar): cachear **por
  contenido de la fuente**, NUNCA por versión — invalidar por release
  re-facturaría trabajo sobre fuentes sin cambios.

### Orquestación de fan-out con subagentes (FW-E, aplica a `/ciclo --paralelo`)

- Despachar TODOS los subagentes independientes en un solo mensaje (paralelismo
  real); estimar y anunciar tiempo/coste ANTES de despachar.
- **Señal de éxito = artefacto en disco**, no la respuesta del subagente; un
  artefacto ausente se reporta con causa probable, nunca se omite en silencio.
- Tolerancia parcial: un chunk fallido se reporta y se sigue; más de la mitad
  fallida → abortar con instrucción de re-ejecución.
- Agrupar unidades de trabajo relacionadas (mismo directorio/módulo) en el
  mismo subagente para no perder relaciones transversales.
