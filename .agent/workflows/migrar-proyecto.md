---
description: Adopta el framework en un proyecto ya iniciado — infiere stack, módulos y dominio desde el código existente
---

Eres el agente de migración de proyectos.

## Pasos

1. ESCANEAR EL REPOSITORIO
   Leer en paralelo (sin fallar si algo no existe):
   - package.json / pyproject.toml / requirements.txt / Cargo.toml / go.mod
   - README.md / README.rst
   - llms.txt
   - CLAUDE.md
   - _project/01_product_requirements.md
   - _project/12_design_profile.md o los documentos de diseño existentes si hay UI
   - _project/20_builder_profile.md
   - _project/21_knowledge_map.md
   - Estructura de carpetas de la raíz (un nivel)
   - .env.example, docker-compose.yml, Dockerfile

   Inferir: nombre del proyecto, stack, dominio, módulos existentes, fase actual, quick win, pistas del MVP y perfil del builder.

2. PRESENTAR LO INFERIDO Y PEDIR CONFIRMACIÓN
   ```
   He analizado el repositorio. Esto es lo que inferí:

   **Proyecto:** [nombre inferido]
   **Stack:** Backend: [X] | Frontend: [X] | DB: [X] | Auth: [X]
   **Dominio:** [inferido]
   **MVP / quick win detectado:** [inferido o "no claro"]
   **Módulos detectados:**
     - [módulo 1] → [completed / in_progress / pending]
     - [módulo 2] → ...
   **Fase estimada:** phase_0[X]
   **Perfil del constructor:** [vibe-coder / intermedio / developer / equipo / no inferido]

   ¿Qué corrijo o añado antes de generar los artefactos?
   ¿Qué NO debe entrar en el MVP inicial?
   ¿Quieres preparar publicación en GitHub desde fase 0/MVP? [sí / no]
   ¿Quieres instalar skills técnicas locales al proyecto? Si sí, indica fuente exacta (repo/tag/commit, paquete@versión o archivo local).
   Entorno principal: [Codex | Claude Code | Antigravity | OpenCode | Otro]
   16. ¿El sistema operará en un entorno sin soporte técnico presente (campo, sede remota, cliente no técnico)? [sí / no]
       Si sí: ¿quién ejecuta actualizaciones y backups, y con qué nivel técnico?
   17. ¿El sistema genera reportes, cálculos o exportaciones con datos operativos críticos? [sí / no]
    18. Ante un fallo abrupto (corte de luz, crash), ¿es aceptable perder los datos de la última sesión? [sí, tolerable / no, requiere recuperación]
    19. ¿Qué capacidad declara para el ejecutor que va a operar este proyecto? `execution_profile.capability_tier` ∈ {strong, standard, limited}. El framework no mapea modelo→tier; lo declaras tú (A8). Si `limited`, `/cierre-modulo` exigirá `requires_second_review: true` en todo tier.
    ```
   Esperar la confirmación ("correcto" o ajustes).

3. CREAR _project/03_project_state.json
   Usar los datos confirmados. Marcar los módulos existentes en completed/in_progress/pending.
   Fijar `framework_mode` en `"initialized"`.
   Fijar `framework_version` en `"5.3.3"` (el mismo valor que `.agent/workflows/manifest.json`).
   Rellenar `dependencies` con los ficheros de dependencias detectados (`requirements.txt`, `package.json`, `pyproject.toml`, `Cargo.toml`, `go.mod`, etc.), con los nombres de dependencias de ejecución confirmados. Si no hay fichero de dependencias o el usuario rechaza lo inferido, poner `"dependencies": []` y anotar el hueco en el informe de migración.
   Incluir `"work_tiers": {}`. Clasificar cada módulo completado o en curso detectado por blast radius + reversibilidad. Si la evidencia no basta, `tier_1` por defecto con la incertidumbre en `rationale`; subir a `tier_2` ante cualquier disparador crítico.
   Incluir `"module_specs": {}` y `"active_mcps": []` si faltan; el esquema exige ambos. Clasificar `module_specs` a posteriori en los módulos completados o en curso si hay evidencia suficiente; si no, anotar el hueco en el informe de migración.
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
   Fijar `last_completed_step` en "Proyecto migrado al framework".

3.1. ARRANCAR EL ESTADO Y LAS PROYECCIONES
   Si el proyecto ya tiene 06_bug_fixes.md / 07_pending_clarifications.md / 03_arch_decisions.md escritos a mano, ejecutar PRIMERO `python3 .agent/scripts/state_manager.py import` (los vuelca en 03_project_state.json y los marca como gestionados) y después `python3 .agent/scripts/state_manager.py bootstrap` para validar y regenerar todas las proyecciones. En Claude Code y otros entornos, conservar el comportamiento actual y copiar `.agent/templates/mcp.json.base` a `.mcp.json`; en Codex, dejar `.codex/config.toml` para el paso 13.1, donde `ensure_codex_integration.py --native-only` añade solo el `state_manager` local obligatorio y deja `.mcp.json` como espejo opcional. NO editar después las proyecciones a mano: se regeneran desde el estado.

4. CREAR O ACTUALIZAR LOS ARTEFACTOS DE PRODUCTO, BUILDER Y CONOCIMIENTO
   Crear o actualizar:
   - `_project/01_product_requirements.md` con MVP, fuera del MVP, quick win, criterios y preguntas abiertas.
   - `_project/20_builder_profile.md` con el nivel del builder, el entorno y sus preferencias. Rellenar la sección «Privacidad del Proveedor de IA» como una pregunta más de la entrevista (proveedor y plan, si el plan entrena con datos del proyecto, datos que nunca se pegan al agente).
   - `_project/21_knowledge_map.md` con temas conocidos y desconocidos y el quick win.
   - `llms.txt` con el mapa de navegación del repositorio si falta.
   Si no se puede inferir el MVP, fijar `/descubrir-producto` como siguiente acción recomendada.

5. CREAR O ACTUALIZAR _project/04_active_scope.md
   Si hay un módulo in_progress, usarlo como scope activo.
   Si no, usar el primer módulo pendiente.
   Si ningún módulo está claro, crear un scope de descubrimiento que ejecute `/descubrir-producto`, `/prd-mvp` y `/tech-design-mvp`.

6. CREAR O ACTUALIZAR _project/10_arch_constraints.md
   Inferir las restricciones del código (linters, configuración de la base de datos, patrones de autenticación).
   Si el fichero ya existe, leerlo antes y añadir solo lo que falte.
   Añadir la sección de resiliencia operativa según las preguntas 16 a 18 del paso 2:
   - Si 16 = sí: declarar el requisito de usuario no técnico y la cadencia esperada de actualizaciones y backups.
   - Si 18 = no: añadir la restricción "procedimiento de backup y recuperación ante caídas obligatorio antes de cerrar el primer módulo que persista datos de producción".
   - Si 17 = sí: añadir la restricción "todos los informes y exportaciones deben tener fuentes de datos trazables documentadas en 03_arch_decisions.md antes del cierre del módulo".

7. CREAR O ACTUALIZAR _project/09_domain_profile.md
   Si ya existe, enriquecerlo. Si no, crearlo con los datos del dominio inferidos.

8. GENERAR LOS EXPERTOS DE DOMINIO en .agent/skills/domain/
   Igual que el paso 6 de /nuevo-proyecto. Saltar los expertos que ya existan.

8.5. CREAR O INFERIR _project/03_invariants.md
   Si `_project/03_invariants.md` ya existe: leerlo y añadir solo las secciones vacías o con `[COMPLETAR]`.
   Si no existe: inferirlo del código existente, el README y el perfil de dominio:
   - **Dominio:** entidades detectadas en el código (modelos, schemas, clases principales) + términos recurrentes que deben protegerse.
   - **Reglas de negocio:** lógica de negocio identificada en el código (validaciones, condiciones críticas, cálculos no triviales). Marcar con `⚠️ INFERIDO — verificar con el usuario`.
   - **Flujos de datos:** flujos detectados (rutas principales, pipelines, secuencias de operaciones). Marcar con `⚠️ INFERIDO — verificar con el usuario`.
   Presentar el borrador/inferencia al usuario y pedir confirmación explícita antes de guardar como canónico.
   Una vez aprobado, actualizar marcas con la fecha de aprobación.

9. ACTUALIZAR AGENTS.md (zona del proyecto — ADR-073)
   Sustituir solo los marcadores sin rellenar de la zona del proyecto de `AGENTS.md` (fuera del
   bloque `GESTIONADO: framework`). No sobrescribir contenido personalizado. Si el proyecto
   ya tiene un `CLAUDE.md` con su propio Stack/Convenciones/Arquitectura/Restricciones,
   no moverlo a mano: ejecutar `python3 .agent/scripts/migrate_root_docs.py --project <ruta>`
   desde la plantilla (propuesta con diff; el builder aprueba antes de `--apply`).

10. CREAR O ACTUALIZAR EL PERFIL DE DISEÑO SI HAY UI
   Si el proyecto tiene frontend o UI, crear `_project/12_design_profile.md` de forma conservadora
   o, si ya existe, rellenar solo los campos vacíos.

11. PREPARAR LA PUBLICACIÓN EN GITHUB SI SE APROBÓ
   Si el usuario quiere publicar en GitHub:
   - Crear o actualizar `_project/16_release_profile.md`.
   - Anotar la URL del repositorio si ya existe, o el nombre y la visibilidad previstos si no.
   - No hacer push todavía; `/publicar-github` se encarga del commit, el remoto y el push.

12. INSTALAR SKILLS DEL PROYECTO SI SE APROBÓ
   Ejecutar `.agent/workflows/instalar-skills.md`.
   Exigir antes la fuente y la versión exactas.
   Pedir permiso antes de cualquier orden de red.
   Instalar por defecto en local al proyecto, en `.agent/skills/imported/`.
   Registrar fuente, versión, orden, ficheros instalados, alcance y estado en `_project/14_skill_provenance.md`.

12.5. OFRECER LA PLANTILLA MCP (opcional, ADR-015)
   Si `Entorno principal` es `Codex`, no ejecutar todavía
   `ensure_codex_integration.py`: el perfil puede ser creado o reconciliado por
   el paso 13, y `--from-profile` se ejecutará después de que exista en disco.
   En Codex, `state_manager` nativo es obligatorio y `.mcp.json` solo es un
   espejo opcional; los MCP remotos siguen siendo opt-in. Si el entorno es Claude Code u otro, conservar
   sin cambios la rama MCP existente.

   Si el entorno principal es Codex, no leer ni normalizar `.mcp.json`: es un
   espejo opcional y la auditoría de `state_manager` se hace sobre
   `.codex/config.toml` y el host real. Para Claude Code u otro entorno,
   comprobar si la raíz del proyecto ya contiene `.mcp.json`:

   - Si existe y el entorno no es Codex: leerlo y auditar la sección `mcpServers`:
     - Para cada MCP activo, verificar que los `args` declaran versión explícita (`@<semver>`, commit hash o tag verificado) o `source` con fuente aprobada. Si algún MCP usa versión implícita (`@paquete` sin versión, o flags `@latest`/`@next`), reportarlo como ⚠️ Hallazgo de supply-chain y recomendar al usuario fijar versión antes de continuar con la migración.
     - Comparar contra los ejemplos de `.agent/templates/mcp.json.base`. Si faltan MCPs que podrían ser útiles (ej: proyecto con UI web sin Playwright), proponer añadirlos manualmente — NO sobreescribir el archivo existente.
     - Para cada MCP activo verificado, añadirlo a `03_project_state.json → active_mcps` si no está ya. Si no se puede verificar su versión/source, no añadirlo como activo.

   - Si no existe y el entorno es Claude Code u otro: ofrecer copiar la plantilla:
     ```
     No detecté .mcp.json en el proyecto. ¿Quieres copiar la plantilla del framework? [sí / no]

     La plantilla incluye configuraciones desactivadas para: Playwright, Context7, Sentry,
     Atlassian, Figma, Snyk. Ningún MCP se activa por defecto. Al activar uno tendrás que
     reemplazar el marcador '<VERSION>' por una versión exacta — regla de supply-chain del
     framework.
     ```
     Si responde sí:
     - Copiar `.agent/templates/mcp.json.base` a `.mcp.json` raíz. Confirmar.
     - Recordar la regla obligatoria de supply-chain (versión exacta, nunca `latest`; alternativa: `source` con fuente aprobada).
   Si responde no en Claude Code u otro entorno: no crear. En Codex esta
   pregunta no aplica; el paso 13.1 configura el MCP nativo en
   `.codex/config.toml`. `.mcp.json` es un espejo opcional.

13. CREAR LOS ARTEFACTOS DE SESIÓN SI FALTAN
   Ejecutar `.agent/workflows/crear-sesion-artefactos.md` (sub-workflow compartido).
   Este sub-workflow crea o actualiza 00_session_brief.json, 13_session_journal.md,
   14_skill_provenance.md, 15_environment_profile.md, 17_skill_recommendations.md,
   19_core_skills.md, llms.txt y asegura todos los _refs
   en 03_project_state.json. No sobreescribe archivos existentes con contenido real.
   Si GitHub fue aprobado, también crea 16_release_profile.md si no existe.

Prefijo de FW (FW-342): si `_project/15_environment_profile.md` no declara `fw_prefix`, proponer al builder un prefijo corto (letras y dígitos, empieza por letra, hasta 12, p. ej. `TL`) y declararlo tras su aprobación; las FW nuevas del proyecto se numeran `PREFIJO-FW-NNN` y las antiguas no se renumeran.

13.1. ACTIVAR CODEX (solo si `15_environment_profile.md` declara Codex)
   Ejecutar desde la raíz, después de crear/reconciliar el perfil:
   ```bash
   export CODEX_SMOKE_MODEL="${CODEX_SMOKE_MODEL:?define el modelo Codex probado}"
   export CODEX_SMOKE_REASONING_EFFORT="${CODEX_SMOKE_REASONING_EFFORT:?define none|minimal|low|medium|high|xhigh|max|ultra}"
   python3 .agent/scripts/ensure_codex_integration.py --from-profile --native-only
   python3 .agent/scripts/codex_runtime_smoke.py --surface cli --required-surfaces cli --strict --model "$CODEX_SMOKE_MODEL" --reasoning-effort "$CODEX_SMOKE_REASONING_EFFORT" --record-profile --json
   ```
   La operación crea o fusiona únicamente `state_manager` en `.codex/config.toml`,
   sincroniza los hooks Codex, preserva hooks personalizados y no lee/escribe el
   espejo `.mcp.json`. `runtime_smoke=pass` requiere dispatch real del host; no
   convertir `unavailable` en `pass` por tener el CLI instalado. Si el perfil
   exige `cli,desktop`, repetir el gate con `--surface desktop
   --required-surfaces desktop` y las mismas opciones explícitas de modelo y
   esfuerzo; Desktop usa `app-server` y conserva un checklist manual de UI/trust.

14. SINCRONIZAR LOS WRAPPERS DE SLASH COMMANDS
   Ejecutar desde la raíz del proyecto. Si el harness principal es Codex, usar
   `python3 .agent/scripts/sync_claude_commands.py --codex-only`; en otro caso,
   usar `python3 .agent/scripts/sync_claude_commands.py`.
   Esto genera (o actualiza) los wrappers de slash commands para los tres entornos:
   - `.claude/commands/<name>.md` para Claude Code (filtrados por `claude_command: true`)
   - `.opencode/commands/<name>.md` para OpenCode (misma superficie que Claude), subagentes read-only en `.opencode/agents/` y skills en `.opencode/skills/`
   - `.agents/skills/source-command-<name>/SKILL.md` para Codex (todos salvo `native_preferred: true`)
   Importante en migración: si el proyecto ya tenía wrappers manuales fuera del template, hacer backup antes — el modo Codex-only solo borra wrappers Codex generados huérfanos; el modo completo conserva el contrato histórico y no toca wrappers hechos a mano sin marcador `GENERATED`.
   Confirmar la salida `Claude wrappers sincronizados: N`, `OpenCode wrappers sincronizados: N` y `Codex wrappers sincronizados: M`.
   Si el entorno principal es Codex, ejecutar además `python3 .agent/scripts/sync_codex_hooks.py`.
   La operación es local y no modifica ninguna superficie Claude u OpenCode.

14.5. INSTALAR LOS GATES DEL PRE-COMMIT (si el proyecto usa git)
   Si existe `.git/`, ejecutar `bash .agent/scripts/pre_commit.sh install` para que los gates de cierre y transición de estado se apliquen en cada commit. Si el proyecto ya tiene un pre-commit propio, el instalador aborta sin pisarlo: avisar al usuario y dejar la integración a su criterio.

14.6. ASEGURAR opencode.json (soporte OpenCode)
   El sync del paso 14 ya generó `.opencode/commands/`, `.opencode/agents/` y `.opencode/skills/`. Falta la config raíz que registra el motor de estado y las instrucciones para OpenCode:
   - Si NO existe `opencode.json` en la raíz: crearlo con `$schema`, `instructions: ["CLAUDE.md", ".agent/adapters/opencode.md"]` y `mcp.state_manager` (local, `command: ["python3", ".agent/scripts/state_manager.py"]`, `enabled: true`). Usar el `opencode.json` de la plantilla del framework como referencia.
   - Si YA existe (el usuario tiene su proveedor/credenciales ahí): **no sobrescribir**. Mergear solo `mcp.state_manager`, `instructions` y `permission.skill["source-command-*"]="deny"` (sin pisar `provider`, `model`, ni otras claves de `permission`), mostrar el resultado y confirmar.
   - En ambos casos, asegurarse de que `opencode.json` incluya las siguientes claves del framework:
     - `mcp.state_manager` — motor de estado local.
     - `instructions` — apunta a `CLAUDE.md` y `.agent/adapters/opencode.md`.
     - `permission.skill` con `{"source-command-*": "deny"}` — aísla la superficie
       compartida Codex + Antigravity (`.agents/skills/`) que OpenCode escanea por
       defecto; sin esto se filtran ~40 skills duplicadas/internas al namespace de
       OpenCode (FW-043). El doctor lo exige.
   - Si OpenCode está instalado, verificar con `opencode mcp list` que `state_manager` figure `connected`. Si no lo está, dejarlo documentado; no es bloqueante.

14.7. ASEGURAR .claude/settings.json (hooks de Claude Code — FW-204)
   `.claude/settings.json` lleva el hook `PreToolUse` del Production Command Guard (FW-082), que `framework_doctor` exige también en proyectos ya inicializados. El sync del paso 14 NO lo genera. Ejecutar desde la raíz:
   ```bash
   python3 .agent/scripts/ensure_claude_settings.py
   ```
   - Si el proyecto no tiene `.claude/settings.json`: lo crea desde `.agent/templates/settings.json.base`.
   - Si ya lo tiene: mergea los hooks del framework (guard, recordatorio `/gycd`, nudge `/debug`, recordatorio `/cierre-modulo`, formateo post-edición) **preservando** cualquier hook propio del proyecto en el mismo evento y sin tocar `permissions`/`env`. Si actualiza un hook gestionado, la salida lo enumera — revisar el diff antes de commitear.
   El helper es idempotente; `--check` hace dry-run sin escribir.

15. VALIDAR CON EL DOCTOR
   Ejecutar `python3 .agent/scripts/framework_doctor.py`. Debe terminar en `Framework doctor: OK`.
   Cualquier ERROR debe resolverse antes de declarar la migración completa.

16. CONFIRMAR CON EL INFORME DE HUECOS
   ```
   ✅ Proyecto [NOMBRE] migrado al framework.
   Artefactos generados/actualizados: [lista]
   Módulos marcados como completados: [lista]
   Módulo activo: [nombre o "ninguno detectado"]
   Slash commands: N para Claude, M para Codex

   ⚠️ Gaps que requieren atención manual:
   - [campos que no se pudieron inferir]
   - [módulos futuros no detectados]

   ```

   Siguiente: /arrancar Retomar con estado mínimo.
   Modelo: <strong|standard|limited> · Esfuerzo: <🟢 bajo|🟡 medio|🟠 alto|🔴 xhigh> (<tier>)

La interfaz, los logs y los textos para el usuario van en español.
