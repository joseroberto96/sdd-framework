---
description: Actualiza el framework (.agent/ + wrappers) en un proyecto inicializado sin tocar el estado del producto
---

# Actualizar Framework — Upgrade de plantilla en proyecto inicializado

Usar cuando el doctor advierte que el proyecto corre una `framework_version` anterior a la de la plantilla, o cuando el usuario quiere adoptar una versión nueva del framework en un proyecto vivo.

**No confundir con `/migrar-proyecto`** (que adopta el framework por primera vez). Este workflow asume `framework_mode: initialized`.

Al explicar el upgrade o pedir aprobación, aplicar `.agent/skills/builder_language_core.md`: empezar por qué cambia para el proyecto y qué decisión necesita el usuario; dejar rutas, scripts y checks internos como detalle técnico.

## Reglas de seguridad

- Tier de rigor: **tier_2** (blast radius: toda la operativa del framework; reversible solo con backup).
- `_project/` es del producto: **no se toca**, salvo `framework_version` vía `mutate_state`.
- `.agent/skills/imported/` y `.mcp.json` son del producto: **preservar**.
- `opencode.json` raíz puede contener el proveedor/credenciales del usuario: **no sobrescribir**; solo asegurar (mergear) las claves del framework `mcp.state_manager` e `instructions`.
- `GUARDRAILS_MASTER.md` y `README.md` raíz pueden tener ediciones del producto: **no sobrescribir en silencio** — presentar diff y dejar la decisión al usuario.
- `AGENTS.md` y `CLAUDE.md` (ADR-073): el framework es dueño solo del bloque entre `GESTIONADO: framework`; `propagate_framework.py` lo sustituye y muestra el diff. Lo de fuera es del proyecto y no se toca. Un raíz sin bloque (adoptante anterior a 5.2.3) se migra con `python3 .agent/scripts/migrate_root_docs.py --project <ruta>` desde la plantilla: propuesta con diff, y solo `--apply` tras la aprobación del builder.
- Sin working tree limpio (o backup equivalente confirmado), no continuar.
- No propagar sobre un proyecto con una sesión abierta; cambiar las herramientas MCP invalida la caché completa de esa sesión (H5).

## Pasos

1. PRECONDICIONES
   - Verificar `framework_mode: initialized` en `_project/03_project_state.json`. Si es `template`, abortar y derivar a `/nuevo-proyecto` o `/migrar-proyecto`.
   - Verificar git limpio (`git status`) o pedir confirmación de backup manual de `.agent/` y `_project/`. "Working tree limpio" no basta si git ignora rutas (FW-T06): correr `python3 .agent/scripts/framework_doctor.py` — falla si hay archivos bajo `.agent/` con el bit `skip-worktree`/`assume-unchanged` (`git ls-files -v -- .agent/ | grep -v '^H '`), porque un upgrade quedaría a medias en disco sin que HEAD avance ni `git status` lo detecte. Reparar con `git update-index --no-skip-worktree --no-assume-unchanged <ruta>` antes de continuar.
   - Verificar que no haya una sesión abierta en el proyecto antes de propagar: cambiar las herramientas MCP invalida la caché completa de esa sesión (H5).
   - Pedir al usuario la ruta de la plantilla nueva (carpeta descargada del framework).

2. COMPARAR VERSIONES
   - **Desde la plantilla**, correr el detector de deriva (FW-169):
     `python3 .agent/scripts/propagate_framework.py` — informa qué proyectos van
     atrasados, cuáles están bloqueados (árbol sucio, sin git, `state ≠ manifest`)
     y, sobre todo, **qué parches locales pisaría el upgrade**. Un proyecto con
     parches detectados NO se actualiza hasta resolverlos según el paso 3.
     Este detector existe porque el aviso de atraso del doctor no puede verlo:
     compara `state.framework_version` con `manifest.framework_version` y ambos
     viajan en la misma copia, así que en un proyecto sin actualizar coinciden.
   - **Publicar una versión nueva de la plantilla incluye crear su marca de
     release.** El detector de deriva identifica cada versión por el commit
     `release: X.Y.Z -- ...` en el historial de la plantilla, no por etiquetas
     (la etiqueta `v5.3.0` solo sirve para la ruta del suelo de versión).
     Sin ese commit, `local_patches` no tiene base contra la que comparar y
     todo proyecto que declare esa versión queda `INDETERMINATE` — bloqueado
     hasta que se cree la marca. Al cerrar una release: `git commit
     --allow-empty -m "release: X.Y.Z -- <descripción>"`. `propagate_framework.py`
     avisa (sin bloquear) cuando la plantilla actual no tiene esta marca.
     En la plantilla, la versión se sube a mano (manifest, motor, estado, README
     y los dos workflows de alta), no con `mutate` (FW-272), y va en ese mismo
     commit, que entonces no lleva `--allow-empty`.
   - Versión actual: `_project/03_project_state.json → framework_version`.
   - Versión nueva: `manifest.json → framework_version` de la plantilla descargada.
   - **Suelo de versión (5.3.1, S8).** Este upgrade solo sube desde 5.3.0. Si la
     versión actual es anterior, no se aplica: sube primero a 5.3.0 con este
     mismo workflow, usando como plantilla nueva la etiqueta `v5.3.0`
     (`git -C <plantilla> fetch --tags` y `git -C <plantilla> worktree add
     <carpeta> v5.3.0`), y después a esta versión. No ejecutar
     `propagate_framework.py --apply` desde esa carpeta: trataría como atrasados,
     y bajaría a 5.3.0, los proyectos que ya están en una versión posterior.
     `propagate_framework.py` marca el suelo como bloqueo con la misma ruta.
   - Leer en la plantilla nueva `.agent/docs/CHANGELOG.md` y resumir al usuario los cambios entre ambas versiones, marcando los **Changed**/**Removed** como posibles breaking changes.
   - Si las versiones son iguales, reportar "ya actualizado" y terminar.

3. INVENTARIO PRESERVAR (regla anti-regresión)
   Listar antes de copiar:
   - `.agent/skills/imported/` — skills instaladas del producto.
   - `.mcp.json` raíz y `.claude/settings.local.json` — configuración local.
   - Cualquier archivo bajo `.agent/` que no exista en la plantilla nueva (extensiones propias del producto): listarlos y preservarlos.
   - **Modificaciones locales sobre archivos que SÍ existen en la plantilla** (PA
     FW-040): diffear cada archivo de `.agent/scripts/` del proyecto contra la
     plantilla **vieja** (la versión que el proyecto declaró al adoptar). Todo
     archivo con diff es una extensión/parche local (p. ej. una action custom en
     `state_core.py`). Reportarlos al usuario ANTES de sobrescribir y acordar:
     migrarlos a `.agent/extensions/state_actions.py` (mecanismo canónico,
     ADR-052 — el upgrade nunca lo toca), o subirlos al framework. Un upgrade
     que pisa una extensión local sin aviso es una regresión silenciosa — la
     única señal sería un test del producto, si existe.
   - **`.agent/extensions/` se preserva SIEMPRE** (ADR-052): no está en la lista
     de sobrescritura del paso 4. Las acciones custom del motor viven ahí
     (`EXTENSION_ACTIONS` en `state_actions.py`) y sobreviven upgrades sin
     re-aplicar nada.

4. APLICAR UPGRADE
   - Sobrescribir desde la plantilla nueva: `.agent/workflows/`, `.agent/scripts/`, `.agent/schemas/`, `.agent/adapters/`, `.agent/rules/`, `.agent/templates/`, `.agent/docs/`, `.agent/subagents/`, y las skills core `*.md` de `.agent/skills/` **preservando `.agent/skills/imported/` y `.agent/skills/domain/`** (skills del producto: copiar sin `--delete` sobre `skills/`).
   - **Sincronizar los tests del framework que el proyecto ya tenga** (FW-154).
     El README ofrece dos vías de adopción y no coinciden: copiar el repo entero
     (la primera y más usada) arrastra los ~40 tests del framework a `tests/`,
     mientras que el bloque archivo-por-archivo solo lista
     `tests/gate_calibration.py`. El upgrade sobrescribe `.agent/scripts/` pero
     nunca esas copias, así que quedan probando gates que ya no existen y la
     suite del producto se pone roja por el framework, no por el producto —
     entrenando a ignorarla o a usar `--no-verify`.
     Para cada fichero de `tests/` de la plantilla cuyo **mismo nombre** exista
     en `tests/` del proyecto, copiarlo. Los tests del producto no se tocan
     (no existen en la plantilla). Si la plantilla trae un test nuevo cuyo
     nombre no está en el proyecto, NO se añade salvo que el proyecto ya
     hospedara la suite del framework.
     ```bash
     for f in <plantilla>/tests/*.py; do
       b=$(basename "$f"); [ -f "tests/$b" ] && cp "$f" "tests/$b"
     done
     ```
   - Prefijo de FW (FW-342): si `_project/15_environment_profile.md` no declara `fw_prefix`, proponer al builder un prefijo corto (letras y dígitos, empieza por letra, hasta 12, p. ej. `TL`) y declararlo tras su aprobación; las FW nuevas del proyecto se numeran `PREFIJO-FW-NNN` y las antiguas no se renumeran.
   - Regenerar wrappers: si `_project/15_environment_profile.md` declara Codex
     mediante `Herramienta:`, `Entorno principal:` o `Entorno:`, ejecutar
     `python3 .agent/scripts/sync_claude_commands.py --codex-only`; en Claude Code
     u otros entornos conservar `python3 .agent/scripts/sync_claude_commands.py`.
     La rama Codex solo actualiza `.agents/skills/source-command-*` y no toca
     `.claude/`, `.opencode/commands/`, `.opencode/agents/`, `.opencode/skills/`, `AGENTS.md` ni `CLAUDE.md`.
   - Si `_project/15_environment_profile.md` declara Codex en cualquiera de esas
     etiquetas, ejecutar
     `python3 .agent/scripts/ensure_codex_integration.py --from-profile --native-only`.
     Esta rama genera o actualiza únicamente `.codex/hooks.json` y la configuración
     nativa `.codex/config.toml` para el servidor local `state_manager`, preservando
     hooks personalizados y sin leer/escribir el espejo opcional `.mcp.json`.
     Después, con un modelo y esfuerzo explícitos, ejecutar los gates requeridos:
     `python3 .agent/scripts/codex_runtime_smoke.py --surface cli --required-surfaces cli --strict --model "$CODEX_SMOKE_MODEL" --reasoning-effort "$CODEX_SMOKE_REASONING_EFFORT" --json`;
     si el perfil incluye Desktop, repetir con `--surface desktop
     --required-surfaces desktop`. No usar `--dangerously-bypass-hook-trust`.
     Si declara Claude Code, el comando hace no-op: no modifica `.claude/settings.json`,
     `.claude/settings.local.json` ni su ruta de wrappers.
   - Para `AGENTS.md` y `CLAUDE.md`: el bloque `GESTIONADO: framework` lo sincroniza el propagador (con diff); si no hay bloque, proponer `migrate_root_docs.py` (ver Reglas de seguridad). Para `GUARDRAILS_MASTER.md` y `llms.txt`: mostrar diff contra la versión nueva y aplicar solo lo que el usuario apruebe.
   - `opencode.json` (soporte OpenCode): si no existe, copiarlo desde la plantilla. Si ya existe, **no sobrescribir** (puede traer el proveedor/credenciales del usuario): mergear solo `mcp.state_manager`, `instructions` y `permission.skill["source-command-*"] = "deny"` (FW-043) desde la plantilla, mostrar el diff resultante y aplicar tras aprobación. No sobrescribir el resto (puede traer proveedor/credenciales del usuario).
   - `.claude/settings.json` (hooks de Claude Code — FW-204): ejecutar `python3 .agent/scripts/ensure_claude_settings.py`. Mergea los hooks del framework (Production Command Guard `PreToolUse`, recordatorio `/gycd`, nudge `/debug`, recordatorio `/cierre-modulo`, formateo post-edición) **preservando** cualquier hook propio del proyecto en el mismo evento y sin tocar `permissions`/`env`. Si actualiza un hook gestionado, la salida lo enumera — mostrar ese diff y aplicar tras aprobación (regla de `:19`). Aplica en todos los entornos: el guard es obligatorio también donde el harness principal no es Claude Code.
   - **Identidades de test por referencia (FW-357, 5.3.1).** `python3 .agent/scripts/migrate_test_identities.py` informa de los mapas de identidades que los recibos de aprobación llevan inline; con `--write` los pasa al almacén `_project/approval_snapshots/` y deja la copia `03_project_state.json.fw357-<fecha>.bak`. Es opcional (los recibos antiguos se leen igual) y no toca `state_log.jsonl`. Commitear el almacén junto al estado.
   - **Reconciliar `_project/` framework-adjacent (FW-052).** El upgrade no copia `_project/`, pero los guards de skills core (`validate_minimal_solution_core_wiring`, `validate_decision_translation_core_wiring`, `validate_builder_language_core_wiring`, …) exigen catálogo y procedencia al día — y en saltos de varias versiones esos archivos quedan atrás. Reconciliar, **preservando las filas/entradas propias del producto**:
     - `_project/19_core_skills.md`: catalogar toda skill core nueva de la plantilla (p. ej. `minimal_solution_core`, `decision_translation_core`, `builder_language_core` / Lenguaje de Builder).
     - `_project/17_skill_recommendations.md`: añadir señales de activación para skills core nuevas (p. ej. Lenguaje de Builder ante planes, decisiones, riesgos, bloqueos o cierres explicados al usuario).
     - `_project/14_skill_provenance.md`: asegurar las referencias externas que exige el doctor (p. ej. Ponytail, URL `DietrichGebert/ponytail.git` con pin exacto `v4.7.0` + commit).
     - `_project/10_arch_constraints.md`: desde 4.20.0 el doctor exige la tabla
       **"Camino de escalado"** (por qué cada pieza del stack, qué se rompe a
       ×10/×100, costura de escape). Un proyecto adoptado antes no la tiene y
       el upgrade no toca `_project/`, así que el paso 5 falla sin que el
       proyecto haya hecho nada mal. Copiar la sección de la plantilla nueva y
       **rellenarla con el stack real del proyecto** — no dejarla en
       `[COMPLETAR]`, que es cambiar un FAIL por un WARN sin decir nada cierto.
     - Si el doctor del paso 5 falla con `no cataloga …` o `no registra Ponytail`, es esto.
     - **Regla de clase (FW-162):** cuando el doctor gana un requisito nuevo
       sobre la raíz o sobre `_project/`, el upgrade tiene que satisfacerlo o
       cada proyecto que salte esa versión llegará al paso 5 en FAIL. El
       síntoma se ve en el primer proyecto propagado; **arreglarlo ahí y en el
       framework antes de seguir con los demás**, no cuatro veces.

4.bis RECONCILIAR EL CONTRATO DE CIERRE CON LOS MÓDULOS ABIERTOS
   Una versión nueva puede endurecer los gates de `/cierre-modulo`. Los módulos
   ya abiertos se clasificaron bajo el contrato viejo, así que sin este paso el
   choque aparece semanas después, a mitad de un cierre — el peor momento y la
   fricción que FW-111 registró. Aquí el coste es cero: el upgrade ya está en
   marcha.

   Para cada módulo en `modules.in_progress` (y en `ready_for_validation`):

   ```
   python3 .agent/scripts/validate_closure.py "<módulo>"
   ```

   Ese comando es la previsualización FIEL del veredicto del motor: corre los
   mismos gates que `complete_module` (tier, specs, recibos de aprobación y
   revisión, cobertura de ACs, sello del builder, execution receipt, STUB GATE).

   Reportar al usuario, en lenguaje llano, SOLO lo que cambió de veredicto por
   el upgrade — no la lista entera. Para cada caso, decir qué falta y cuándo se
   resuelve (ahora o al cerrar). Ejemplos de 4.21.0:
   - `execution receipt ausente` → el módulo necesitará el paso 3.8 sellado con
     `record_execution_receipt` al cerrar (FW-144). No se resuelve ahora.
   - `module_specs.integration_real` sin clasificar → clasificarla YA con
     `classify_module_spec`, mientras el contexto del módulo está fresco
     (FW-148).
   - `no tiene cosecha de fricción declarada` (FW-201, desde 4.34.0) → todo
     módulo `in_progress`/`ready_for_validation` abierto ANTES del upgrade
     necesitará `record_harvest` (con `classes: "none"` si no hubo hallazgos)
     antes de poder cerrar. No se resuelve ahora — el paso 5.5 de
     `/cierre-modulo` lo declara en su momento.
   - `STUB GATE: FAIL` → hay dato fabricado o fail-open en código ya escrito;
     registrarlo como bug con `/nuevo-bug` en vez de descubrirlo al cerrar.

   No bloquea el upgrade: informa. Un módulo abierto que ya no cerraría bajo el
   contrato nuevo es información que el builder necesita ANTES de seguir
   construyendo encima.

5. VERIFICAR
   - `python3 .agent/scripts/framework_doctor.py` debe terminar en OK.
   - Ejecutar los tests del framework si la copia los incluye (`python3 tests/test_*.py`).
   - Si el doctor falla: detenerse, reportar los errores y ofrecer rollback desde git/backup. No dejar el proyecto a medias.

6. REGISTRAR
   - Llamar a `mutate_state` action `set_framework_version` con la versión nueva (el valor exacto de `manifest.json → framework_version`).
   - Llamar a `mutate_state` action `update_last_step` con un resumen del upgrade
     que **nombre los cambios de contrato detectados en el paso 4.bis**. Ese campo
     se proyecta a `core.md`, que es lo primero que lee `/arrancar`: es la vía por
     la que el agente de la sesión siguiente se entera sin ceremonia añadida.
   - Anotar en `_project/13_session_journal.md` los archivos preservados del paso 3 que requieran revisión posterior.

UI, logs y strings visibles al usuario deben estar en ESPAÑOL.

Siguiente: /arrancar Retomar con el framework actualizado.
Modelo: <strong|standard|limited> · Esfuerzo: <🟢 bajo|🟡 medio|🟠 alto|🔴 xhigh> (<tier>)
