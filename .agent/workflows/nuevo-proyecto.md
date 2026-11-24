---
description: Inicializa un proyecto nuevo — crea todos los artefactos _project/ y genera la base de expertos de dominio
---

Eres el agente de inicialización de proyectos.

## Pasos

1. RECOGER LOS DATOS DEL PROYECTO
   Preguntar al usuario en un solo bloque:
   ```
   Para inicializar el proyecto necesito:
   1. Nombre del proyecto:
   2. Descripción breve (1-2 frases — qué problema resuelve):
   3. Dominio principal: [trading | agricultura | salud | logística | industria | educación | finanzas | otro]
   4. Sub-dominio (más específico):
   5. Usuarios objetivo:
   6. Primer quick win deseado (qué debería poder hacer el usuario primero):
   7. Qué NO entra en el MVP inicial:
   8. Nivel del constructor: [vibe-coder | intermedio | developer | equipo]
   9. Criticidad: [baja | media | alta | crítica]
   10. Stack tecnológico: el agente propondrá uno basado en tus respuestas
      anteriores (problema, quick win, criticidad, entorno de operación) —
      confirmas o ajustas después de responder este bloque (paso 1.5). Si ya
      tienes una preferencia fuerte, indícala aquí y el agente la usa como
      punto de partida en vez de proponer desde cero.
   11. ¿El proyecto incluye interfaz web? [sí / no]
      Si sí, plataformas objetivo: Windows / macOS / Android / iOS
   12. ¿Quieres preparar publicación en GitHub desde fase 0? [sí / no]
      Si sí: nombre del repo, visibilidad [privado/público] y quién lo usará.
   13. ¿Quieres instalar skills técnicas locales al proyecto? [sí / no]
      Si sí: fuente exacta (repo/tag/commit, paquete@versión o archivo local).
   14. ¿Ya tienes un product spec o plan técnico? [sí / no]
   15. Entorno principal: [Codex | Claude Code | Antigravity | OpenCode | Otro]
   16. ¿El sistema operará en un entorno sin soporte técnico presente (campo, sede remota, cliente no técnico)? [sí / no]
       Si sí: ¿quién ejecuta actualizaciones y backups, y con qué nivel técnico?
   17. ¿El sistema genera reportes, cálculos o exportaciones con datos operativos críticos? [sí / no]
    18. Ante un fallo abrupto (corte de luz, crash), ¿es aceptable perder los datos de la última sesión? [sí, tolerable / no, requiere recuperación]
    19. ¿Qué capacidad declara para el ejecutor que va a operar este proyecto? `execution_profile.capability_tier` ∈ {strong, standard, limited}. El framework no mapea modelo→tier; lo declaras tú (A8). Si `limited`, `/cierre-modulo` exigirá `requires_second_review: true` en todo tier.
    ```
   Esperar la respuesta.

   **Modo brief.** Si el usuario pega un documento, brief, HU o nota extensa como respuesta a este bloque:
   - No re-preguntar lo que ya esté contenido en el texto pegado.
   - Extraer lo que aplique a cada uno de los 18 puntos.
   - Hacer el eco de verificación de lo extraído (5-8 bullets) antes de seguir.
   - Preguntar **solo** por los huecos detectados, en una sola tanda.
   - Lo que el usuario no responda se registra como `PRD-CLAR-NNN` en `_project/07_pending_clarifications.md`. Nunca rellenar por inferencia, dominios análogos o "buenas prácticas".

1.5. PROPUESTA DE STACK (STACK PROPOSAL) — el agente propone, el builder confirma (4.1.0 endurecido, FW-133)
   Con las respuestas del bloque anterior, proponer backend, frontend, base de
   datos y auth anclados en: descripción del problema (Q2), quick win (Q6),
   criticidad (Q9), y contexto operacional (Q16-Q18 — entorno sin soporte
   técnico, reportes críticos, tolerancia a pérdida de datos). Si el usuario ya
   dio una preferencia en Q10, usarla como punto de partida y proponer solo lo
   que falte.
   Presentar con la plantilla de checkpoint de `decision_translation_core`
   (3.19.0): qué se propone, por qué (citando la respuesta concreta que lo
   justifica, no "es lo estándar"), y 2-3 alternativas con su trade-off.
   El detalle de camino de escalado (qué se rompe a escala, costura de
   reemplazo) se registra después en `/tech-design-mvp` paso 3, cuando ya
   existen NFRs formales — aquí solo se fija la elección inicial confirmada
   por el builder.
   No bloquea el resto de la inicialización si el builder pide seguir con el
   stack tal como lo escribió en Q10 sin más discusión.

2. CREAR O ACTUALIZAR LOS ARTEFACTOS DE PRODUCTO Y DEL BUILDER
   Crear o actualizar:
   - `_project/01_product_requirements.md` con el problema, los usuarios, el quick win, el MVP y "No Incluye por Ahora".
   - `_project/20_builder_profile.md` con el nivel del builder, el entorno y sus preferencias de explicación. Rellenar la sección «Privacidad del Proveedor de IA» como una pregunta más de la entrevista (proveedor y plan, si el plan entrena con datos del proyecto, datos que nunca se pegan al agente).
   - `_project/21_knowledge_map.md` con el quick win, los temas y los recursos.
   Hacer el eco de verificación antes de generar una PRD o un plan técnico grandes.

3. CREAR _project/03_project_state.json
   Rellenarlo con los datos reales del usuario. Fijar `last_completed_step` en "Proyecto inicializado".
   Fijar `framework_mode` en `"initialized"`.
   Fijar `framework_version` en `"5.3.3"` (el mismo valor que `.agent/workflows/manifest.json`).
   Si en la inicialización se crean ficheros de dependencias (`requirements.txt`, `package.json`, `pyproject.toml`, etc.), copiar en `dependencies` los nombres de dependencias de ejecución confirmados; si no, poner `"dependencies": []`.
   Incluir `"work_tiers": {}`. Al crear un módulo inicial en curso, clasificarlo por blast radius + reversibilidad y añadir su tier.
   Incluir `"module_specs": {}` y `"active_mcps": []` desde el principio; el esquema exige ambos aunque no haya ningún módulo ni MCP activo.
   Fijar `"execution_profile"` con la respuesta a la pregunta 19 (`"standard"` por defecto si el usuario no respondió). Estructura:
   ```json
   "execution_profile": {
     "capability_tier": "strong|standard|limited",
     "model": "[modelo declarado por el operador, o null]",
     "rationale": "[por qué este tier, opcional]"
   }
   ```
   Inicializar los contenedores vacíos del estado para que el doctor (A9) y las proyecciones no vean campos faltantes hasta la primera mutación:
   ```json
   "_bugs": [],
   "_clarifications": [],
   "_decisions": [],
   "session": {}
   ```
   Si se aprobó la fase 0 de GitHub:
   - Fijar `current_phase` en `phase_00` y `current_step` en `esqueleto publicable`.
   - Crear el módulo inicial con id `"phase_00_esqueleto_publicable"`.
   - Fijar `modules.in_progress` en `["phase_00_esqueleto_publicable"]`.
   - Fijar `module_statuses.phase_00_esqueleto_publicable` en `"in_progress"`.
   - Añadir `work_tiers.phase_00_esqueleto_publicable` como `tier_1`, con `classified_by` en `"manual"` y la razón "Fase 0 toca documentación/configuración publicable y es reversible por diff".
   - Añadir:
     ```json
     "module_specs": {
       "phase_00_esqueleto_publicable": {
         "data_model": "n/a",
         "api_contract": "n/a",
         "justification": "Fase 0 solo prepara documentación, configuración de publicación y esqueleto de repo; no define entidades persistidas ni endpoints HTTP.",
         "classified_at": "[ISO8601]",
         "classified_by": "manual"
       }
     }
     ```
   Si no, fijar `current_phase` en `phase_01`.

3.1. ARRANCAR EL ESTADO Y LAS PROYECCIONES
   Tras escribir 03_project_state.json, ejecutar `python3 .agent/scripts/state_manager.py bootstrap` para validar el estado y generar las proyecciones iniciales de solo lectura (core.md, 06_bug_fixes.md, 07_pending_clarifications.md, 03_arch_decisions.md, 00_session_brief.json). En Claude Code y otros entornos, conservar el comportamiento actual y copiar `.agent/templates/mcp.json.base` a `.mcp.json` en la raíz del proyecto. En Codex, dejar la creación de `.codex/config.toml` para el paso 12.1; `.mcp.json` es un espejo opcional y `ensure_codex_integration.py --native-only` añade solo el `state_manager` local obligatorio; los MCP remotos siguen siendo opcionales. NO crear core.md ni las demás proyecciones a mano: las genera bootstrap.

3.5. POLÍTICA DE SALIDA COMPACTA
   Para documentos largos ya escritos o preparados en archivo:
   - Usar salida compacta: mostrar `ruta + resumen` de cambios + pedir revisión del archivo.
   - No pegar el documento completo en el chat salvo solicitud expresa del usuario.
   - Si el documento requiere aprobación, pedir: "Revisa [ruta] y dime si apruebas o quieres cambios".
   - Esta regla aplica especialmente a `_project/03_invariants.md`, PRDs, planes técnicos y perfiles largos.

4. CREAR _project/04_active_scope.md
   Usar la cabecera estándar. Si el usuario tiene un product spec, anotar en la sección Notas
   que el primer módulo se define después de revisarlo.
   Si `current_phase` es `phase_00`, el módulo activo es "Fase 0 — Esqueleto publicable" con id `phase_00_esqueleto_publicable`; sus tareas salen de `_project/16_release_profile.md`.
   Si todavía no hay un módulo claro, fijar como siguiente acción `/prd-mvp` o `/tech-design-mvp`.

5. CREAR _project/10_arch_constraints.md
   Rellenar la sección Stack con los datos confirmados.
   Añadir reglas de base de datos según la declarada (p. ej., PostgreSQL → migraciones idempotentes).
   Añadir la sección de resiliencia operativa según las preguntas 16 a 18:
   - Si 16 = sí: declarar el requisito de usuario no técnico y la cadencia esperada de actualizaciones y backups.
   - Si 18 = no: añadir la restricción "procedimiento de backup y recuperación ante caídas obligatorio antes de cerrar el primer módulo que persista datos de producción".
   - Si 17 = sí: añadir la restricción "todos los informes y exportaciones deben tener fuentes de datos trazables documentadas en 03_arch_decisions.md antes del cierre del módulo".

6. CREAR _project/09_domain_profile.md
   Rellenarlo con datos reales del dominio. En Invariantes, Criterios de calidad y Riesgos críticos,
   usar el conocimiento del dominio para proponer valores concretos y realistas (no marcadores).

7. DETECTAR EXPERTOS DE DOMINIO — camino único (4.1.0 endurecido, FW-131)
   No crear expertos aquí directamente. Ejecutar la Parte A de
   `.agent/workflows/sugerir-skills.md` con el ancla de init-time: la
   descripción del problema (Q2), dominio + sub-dominio (Q3/Q4), usuarios
   objetivo (Q5), quick win (Q6) y criticidad (Q9) — el PRD todavía no existe
   en este paso, así que esas respuestas son la fuente de anclaje, no el
   PRD. El número de expertos sale del barrido, no de un rango fijo.
   Cada sub-dominio detectado se registra en la tabla "Barrido de
   sub-dominios" de `_project/09_domain_profile.md` con su veredicto
   (propuesto/descartado + razón) y la cita que lo dispara — silencio ya no
   es una salida válida: un barrido vacío es visible en el artefacto.
   Los expertos propuestos y confirmados por el usuario se escriben sobre
   `.agent/skills/domain/_TEMPLATE.md` (mismo formato que el camino
   proactivo posterior en `/diseñar` y `/enriquecer-ticket`) — un solo
   formato de experto en todo el framework, no dos que divergen).

8. ACTUALIZAR _project/09_domain_profile.md
   Rellenar la tabla de expertos activos con los ficheros confirmados en el paso 7. Las dos tablas
   ("Barrido de sub-dominios" y "Expertos activos") deben cuadrar: cada veredicto
   `propuesto` corresponde a una fila de la tabla de expertos activos.

8.5. CREAR _project/03_invariants.md — BORRADOR
   Con el dominio declarado, los requisitos de producto y las respuestas 1 a 5, generar un borrador con:
   - **Dominio:** entidades centrales inferidas (nombre, descripción, invariante propuesto) + términos del lenguaje ubicuo que deben protegerse.
   - **Reglas de negocio:** RN-001..N con las reglas más críticas del negocio declarado — en lenguaje del dominio, no de implementación. Marcar con `⚠️ BORRADOR — pendiente de revisión`.
   - **Flujos de datos:** los flujos principales inferidos del quick win y MVP, con secuencia canónica y su invariante. Marcar con `⚠️ BORRADOR — pendiente de revisión`.
   Mostrar el borrador al usuario y pedir confirmación antes de guardarlo como canónico.
   Si el usuario corrige algo, incorporar antes de guardar.
   Una vez aprobado, reemplazar las marcas `⚠️ BORRADOR` por la fecha de aprobación.

9. CREAR EL PERFIL DE DISEÑO SI HAY UI WEB
   Crear o rellenar `_project/12_design_profile.md` con público, tono visual, plataformas objetivo,
   reglas de maquetación y los términos oficiales de la interfaz de `_project/09_domain_profile.md`.

10. PREPARAR LA FASE 0 DE GITHUB SI SE APROBÓ
   Si el usuario quiere GitHub desde la fase 0:
   - Crear `_project/16_release_profile.md` si falta.
   - Rellenar la intención del repositorio, la visibilidad, el público y la lista de la fase 0.
   - No crear el remoto ni hacer push todavía; eso es de `/publicar-github`.

11. INSTALAR SKILLS DEL PROYECTO SI SE APROBÓ
   Si el usuario quiere skills, ejecutar `.agent/workflows/instalar-skills.md`.
   Exigir antes la fuente y la versión exactas.
   Pedir permiso antes de cualquier orden de red.
   Instalar por defecto en local al proyecto, en `.agent/skills/imported/`.
   Registrar fuente, versión, orden, ficheros instalados, alcance y estado en `_project/14_skill_provenance.md`.
   Saltar si no se aprobó o no aplica.

11.5. OFRECER LA PLANTILLA MCP (opcional, ADR-015)
   Si la respuesta del paso 1 indica `Entorno principal: Codex`, no ejecutar
   todavía `ensure_codex_integration.py`: el perfil de entorno se crea en el
   paso 12 y `--from-profile` debe resolverlo desde disco. La integración Codex
   se ejecuta en el paso 12.1. En esta rama no preguntar por la plantilla MCP:
   `state_manager` nativo y su espejo mínimo se crean obligatoriamente, mientras
   que los MCP remotos siguen siendo opt-in.

   Para Claude Code y otros entornos, conservar exactamente la elección MCP
   existente de este paso y no ejecutar la integración Codex. Preguntar al usuario:
   ```
   ¿Quieres copiar la plantilla MCP del framework a .mcp.json? [sí / no]

   La plantilla incluye configuraciones desactivadas para: Playwright (verificación ejecutable UI),
   Context7 (docs), Sentry (errores prod), Atlassian (Jira/Confluence), Figma, Snyk.
   Ningún MCP se activa por defecto; tendrás que mover el bloque deseado de
   "_disabled_examples" a "mcpServers", reemplazar el marcador "<VERSION>" por una
   versión exacta verificada en la documentación oficial y completar tokens.
   ```

   Si responde sí (Claude Code u otro entorno):
   - Copiar `.agent/templates/mcp.json.base` a `.mcp.json` en la raíz del proyecto.
   - Confirmar al usuario que el archivo se copió desactivado y mostrar la ruta.
   - Recordar la regla de supply-chain del framework:
     ```
     ⚠️ Regla obligatoria al activar un MCP:
     - Versión exacta. Reemplaza '<VERSION>' por semver fijado (ej: '@0.5.2'),
       commit hash o tag verificado en la fuente oficial.
     - NUNCA usar 'latest', 'next', 'beta' o dejar el paquete sin versión.
     - Alternativa válida: declarar 'source' con fuente aprobada (repo+commit,
       mirror interno, artefacto firmado).
     - Activar un MCP sin versión explícita viola la política del framework y
       expone el proyecto a supply-chain attacks.
     ```
   - Sugerir que cuando active un MCP, añada el nombre al campo `03_project_state.json → active_mcps`.

   Si responde no (Claude Code u otro entorno): no crear ni eliminar `.mcp.json`;
   `verificar-ejecutable` operará en fallback manual reproducible (admitido por
   ADR-015). En Codex, esta pregunta no aplica: el paso 12.1 configura el MCP
   nativo en `.codex/config.toml`; `.mcp.json` es un espejo opcional y no activa
   MCPs remotos.

12. CREAR LOS ARTEFACTOS DE SESIÓN
   Ejecutar `.agent/workflows/crear-sesion-artefactos.md` (sub-workflow compartido).
   Este sub-workflow crea 00_session_brief.json, 13_session_journal.md, 14_skill_provenance.md,
   15_environment_profile.md, 17_skill_recommendations.md,
   19_core_skills.md, llms.txt y asegura todos los _refs en 03_project_state.json.
   Si GitHub fue aprobado, también crea 16_release_profile.md.

Prefijo de FW (FW-342): si `_project/15_environment_profile.md` no declara `fw_prefix`, proponer al builder un prefijo corto (letras y dígitos, empieza por letra, hasta 12, p. ej. `TL`) y declararlo tras su aprobación; las FW nuevas del proyecto se numeran `PREFIJO-FW-NNN` y las antiguas no se renumeran.

12.1. ACTIVAR CODEX (solo si `15_environment_profile.md` declara Codex)
   Ejecutar desde la raíz:
   ```bash
   export CODEX_SMOKE_MODEL="${CODEX_SMOKE_MODEL:?define el modelo Codex probado}"
   export CODEX_SMOKE_REASONING_EFFORT="${CODEX_SMOKE_REASONING_EFFORT:?define none|minimal|low|medium|high|xhigh|max|ultra}"
   python3 .agent/scripts/ensure_codex_integration.py --from-profile --native-only
   python3 .agent/scripts/codex_runtime_smoke.py --surface cli --required-surfaces cli --strict --model "$CODEX_SMOKE_MODEL" --reasoning-effort "$CODEX_SMOKE_REASONING_EFFORT" --record-profile --json
   ```
   `ensure_codex_integration.py` crea o fusiona únicamente `state_manager` en
   `.codex/config.toml` y sincroniza `.codex/hooks.json` sin tocar Claude/OpenCode
   ni leer el espejo `.mcp.json`. `--record-profile` completa el perfil con
   estados independientes de CLI/Desktop, versión, hooks, estado MCP,
   `structural_support`, `runtime_smoke`, modelo y esfuerzo observados. No
   cambiar manualmente `runtime_smoke` ni un estado de superficie a `pass`: el
   smoke host debe observar un dispatch Codex real.
   Si el perfil declara `Superficies Codex requeridas: cli,desktop`, ejecutar
   además el gate independiente:
   ```bash
   python3 .agent/scripts/codex_runtime_smoke.py --surface desktop --required-surfaces desktop --strict --model "$CODEX_SMOKE_MODEL" --reasoning-effort "$CODEX_SMOKE_REASONING_EFFORT" --record-profile --json
   ```
   Desktop usa `codex app-server --stdio`; la apertura del proyecto, confianza y
   revisión de `/hooks` son checklist manual y no se infieren de una terminal.
   Si se quieren MCP remotos, pedir aprobación explícita y activarlos después
   con versión exacta/source aprobado; si no, conservar únicamente el servidor
   local `state_manager`.

13. ACTUALIZAR AGENTS.md (zona del proyecto — ADR-073)
   Sustituir [NOMBRE_PROYECTO] y los marcadores de Stack / Convenciones / Arquitectura / Restricciones
   por datos reales **en la zona del proyecto de `AGENTS.md`** (fuera del bloque
   `GESTIONADO: framework`, que es del framework). `CLAUDE.md` no tiene zona del
   proyecto: solo importa `@AGENTS.md` y guarda lo propio de Claude Code.

14. SINCRONIZAR LOS WRAPPERS DE SLASH COMMANDS
   Ejecutar `python3 .agent/scripts/sync_claude_commands.py` desde la raíz del proyecto.
   Esto genera:
   - `.claude/commands/<name>.md` para Claude Code (filtrados por `claude_command: true`)
   - `.agents/skills/source-command-<name>/SKILL.md` para Codex (todos salvo `native_preferred: true`)
   Confirmar en la salida algo como `Claude wrappers sincronizados: N` y `Codex wrappers sincronizados: M`.
   Si el script falla por una fuente inexistente, revisar el manifest antes de continuar.
   Si el entorno principal es Codex, ejecutar además:
   ```bash
   python3 .agent/scripts/sync_codex_hooks.py
   ```
   Esto solo genera `.codex/hooks.json` (ignorado por git); no modifica
   `.claude/settings.json`, `.claude/commands/` ni los wrappers de OpenCode.

14.5. INSTALAR LOS GATES DEL PRE-COMMIT (si el proyecto usa git)
   Si existe `.git/`, ejecutar `bash .agent/scripts/pre_commit.sh install` para que los gates de cierre y transición de estado se apliquen en cada commit (no como invocación voluntaria). Si ya hay un hook propio distinto, el instalador aborta sin pisarlo: registrar la decisión y avisar al usuario.

14.6. ASEGURAR .claude/settings.json (hooks de Claude Code — FW-204)
   `.claude/settings.json` lleva el hook `PreToolUse` del Production Command Guard (FW-082), que `framework_doctor` exige en todo proyecto inicializado. El sync del paso 14 NO lo genera. Ejecutar desde la raíz:
   ```bash
   python3 .agent/scripts/ensure_claude_settings.py
   ```
   Si el proyecto no tiene el fichero, lo crea desde `.agent/templates/settings.json.base`; si ya lo tiene, mergea los hooks del framework preservando cualquier hook propio y sin tocar `permissions`/`env`. Idempotente; `--check` para dry-run.

15. VALIDAR CON EL DOCTOR
   Ejecutar `python3 .agent/scripts/framework_doctor.py`. Debe terminar en `Framework doctor: OK`.
   Cualquier ERROR debe resolverse antes de declarar el proyecto inicializado.

16. CONFIRMAR
   ```
   ✅ Proyecto [NOMBRE] inicializado.
   Artefactos creados: [lista]
   Expertos de dominio: [lista con estado 🌱]
   GitHub fase 0: [preparado / no solicitado]
   Skills locales: [instaladas / no solicitadas]
   Slash commands: N para Claude, M para Codex
   Hooks Codex: [gestionados / no aplican]
   ```

   Siguiente: /diseñar Aterrizar el MVP y el diseño.
   Modelo: <strong|standard|limited> · Esfuerzo: <🟢 bajo|🟡 medio|🟠 alto|🔴 xhigh> (<tier>)

## Reglas

- **Anti-fabricación:** todo lo que se escriba en `_project/01_product_requirements.md` debe derivar del brief pegado o de respuestas explícitas del usuario en esta sesión. Ante un hueco, registrar `PRD-CLAR-NNN` en `_project/07_pending_clarifications.md` y bloquear el paso afectado. Nunca inferir desde dominios análogos ni desde "buenas prácticas".
- El PRD generado aquí es la **fuente de verdad canónica** del proyecto. El resto de specs (`02_technical_plan/data_model.md`, `02_technical_plan/api_contract.yaml`, `02_technical_plan/phase_NN.md`, `08_module_acceptance.md`) se derivan de él en pasos posteriores. No se sustituye, se enriquece.
- **Puertos:** documenta los puertos del proyecto y su configuración en `_project/10_arch_constraints.md`. Consulta el registro del equipo si existe.

La interfaz, los logs y los textos para el usuario van en español.
