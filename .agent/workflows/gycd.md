---
description: Guardar y Continuar Después — cierre ordenado de sesión con prompt de continuación listo
---

Eres el agente de cierre de sesión.

Recibirás:
- @03_project_state.json
- @00_session_brief.json si existe
- @04_active_scope.md
- @13_session_journal.md si existe
- @06_bug_fixes.md solo si hubo bugs o el diario los menciona

## Producto en producción (FW-225)

Si `lifecycle.stage == production` y **no hay cambio `open`** en `changes`: solo
paso 6.6 (commit de sesión), `set_session_pointer` con `next_command: /ciclo`
y la línea `Fricción del framework: …` de 6.7. Omitir scope, journal, digest de
lecciones y búfer de fricción. Si hay un cambio `open`, dejarlo registrado en
`next_action` ("Cerrar CHG-NNN: …") y aplicar lo mismo.

## Pasos

0. LECTURAS MÍNIMAS Y DIARIO
   No recargar todos los artefactos del proyecto.
   Llamar a `get_work_context` con `{"view":"recovery"}` y usar sus referencias
   para abrir solo el diario y la evidencia necesarios para persistir la
   siguiente acción. El estado completo es un recurso de diagnóstico, no la
   carga normal del cierre de sesión.
   Incorporar los cambios hechos desde la última mutación del estado o el último `/gycd`, también los anteriores a una compactación.
   Usar @13_session_journal.md y cualquier bloque de compactación del contexto actual como registro.

1. ACTUALIZAR EL ESTADO
   Llamar a `mutate_state` con la acción `update_last_step` y {"last_completed_step": "[máximo 2 frases: qué se hizo y qué viene después]"}.
   Si algún módulo activo o completado no tiene tier, llamar además a `mutate_state` con la acción `add_work_tier` y {"id": "[módulo]", "tier": "tier_1|tier_2", "rationale": "[razón]", "classified_by": "gycd"} para cada módulo que falte.

2. ACTUALIZAR EL SCOPE
   En @04_active_scope.md, marcar como `[x]` las tareas completadas.
   No archivarlas aquí: eso se hace en /cierre-modulo.

3. REGISTRAR BUGS ABIERTOS
   Si en la sesión aparecieron bugs que aún no se registraron con /nuevo-bug,
   registrarlos con `mutate_state`, acción `register_bug`, con el payload que corresponda.

4. LIMPIAR EL DIARIO
   Conservar el journal para pendientes no persistidos; limpiar las entradas de @13_session_journal.md solo tras confirmar que su contenido ya quedó incorporado en el estado canónico o scope.
   Mantener únicamente las entradas no resueltas con su razón explícita.

4.4. PROPONER LECCIÓN SI APLICA
   Antes de regenerar el digest, revisar si esta sesión cumple alguna señal ya
   existente en el framework (no se inventa contador nuevo):
   - **Mecánica**: `closure_rejections` con 2+ entradas en el sprint actual
     (la misma condición que evalúa el paso 6.5 más abajo).
   - **Conversacional**: la misma pregunta que hace `/aprender-de-error` Paso 0
     — ¿hubo una corrección explícita del usuario en esta sesión que revele un
     patrón no capturado aún?
   Si cualquiera aplica y `/aprender-de-error` no se invocó ya en esta sesión,
   proponer invocarlo antes de cerrar (una línea, no bloqueante). El gate de
   aprobación literal sigue viviendo enteramente en `aprender-de-error.md`.

4.5. REGENERAR DIGEST DE LECCIONES
   Si durante la sesión una lección existente resultó útil, un callejón sin
   salida o quedó corregida, añadir la señal a su bloque en
   `_project/22_lessons.md` (sección `**Señales:**`, ver /aprender-de-error).
   Después, si `_project/22_lessons.md` existe, regenerar la proyección:
   ```bash
   python3 .agent/scripts/lessons_reflect.py
   ```
   Determinista, sin LLM. Si el archivo no existe (proyecto sin lecciones o
   modo template), el script no escribe nada — no es un error.

5. FIJAR LOS PUNTEROS DE SESIÓN (core.md y 00_session_brief.json son proyecciones de solo lectura)
   Llamar a `mutate_state` con la acción `set_session_pointer` y next_action, next_command, active_module_description y next_module.

   `next_command` debe ser **del builder** (uno de los 9 verbos con
   `builder_facing: true` en `manifest.json`) y coincidir con lo que elegiría el
   árbol de decisión de CLAUDE.md para el estado en que se cierra la sesión;
   nunca un workflow que el agente invoca internamente (`/tdd`,
   `/scope-siguiente`, `/cierre-modulo`, `/cambio-seguro`…). Si se llegó a este
   `/gycd` SIN pasar por `/cierre-modulo` (a mitad de tarea, después de
   `/nuevo-bug`, a mitad de `/diseñar`), elegir entre:
   - Módulo todavía `in_progress` con un siguiente paso claro → `/ciclo`
   - El rumbo del scope o del producto sigue sin estar claro → `/diseñar`
   - Bug abierto que bloquea el módulo → `/nuevo-bug` (si no está registrado) o `/ciclo` (si ya lo está: lo corrige en un módulo de Estabilización, FW-349)
   - Nada pendiente, módulo cerrado en esta sesión → `/ciclo` (el agente llamará internamente a `/scope-siguiente`)
   - Nunca `/arrancar` cuando hay trabajo definido: `/arrancar` solo abre una sesión sin prompt o muestra el estado (FW-318)

   `next_command` guarda únicamente el comando (con `fase`/`--paralelo` si
   aplica): nunca el prompt, un separador ni texto natural. La instrucción
   completa pertenece exclusivamente a `next_action`.

   `next_action` debe ser una **instrucción autosuficiente y lista para usar**
   (imperativa, concreta), no una etiqueta vaga: `/arrancar` la une con
   `next_command` en la única línea copiable `Siguiente:` de la sesión
   siguiente (FW-109/FW-141). Cualquier modelo, sin el contexto de esta
   conversación, debe saber exactamente qué hacer solo con ese texto.
   Mal: `"continuar con el módulo"`, `"seguir donde quedamos"`.
   Bien: `"Implementar el motor de Tailoring en src/tailoring_engine.py
   con interfaz desacoplada de la persistencia"`.
   Como toda línea `Siguiente:`, `next_action` es una acción de hasta 40
   palabras y sin prohibiciones (FW-360): el contexto, los límites y lo
   pendiente van al diario (`13_session_journal.md`), no al puntero.

   El render regenera core.md Y 00_session_brief.json. No escribir ninguno de los dos a mano.

6. GENERAR LA CONTINUACIÓN (una sola continuación generada por el puntero)
   Emitir exactamente (fusionando next_command y next_action de set_session_pointer en una sola continuación copiable):
   > Sesión guardada. Si prevés ausentarte más de ~1 h, cierra con `/gycd`; al volver, sesión nueva pegando el `Siguiente:`; si no tienes prompt o quieres ver el estado, `/arrancar` (reanudar en frío cuesta 4–11× más tokens de caché que arrancar). Si sigues ahora y vas a limpiar contexto, usa la compactación nativa del entorno.
   > Siguiente: {next_command} {next_action}

6.5. CRITERIO DE PARADA — ADVERTENCIA PRE-CIERRE
   Antes de cerrar, verificar en @06_bug_fixes.md y @03_project_state.json:
   - ¿Hay bugs con `Severidad: Crítico` y `Estado: Abierto`?
   - ¿Hay 2 o más entradas en `closure_rejections` en el sprint actual?

   Si alguna condición se cumple, emitir advertencia (no bloquea el cierre):
   > ⚠️ Al retomar: hay condiciones de parada activas — [condición]. No abrir módulos nuevos hasta resolver.
   Pasar la advertencia como parte del `next_action` o en la llamada `set_session_pointer` del paso 5 para que quede reflejada en la proyección 00_session_brief.json. No editar 00_session_brief.json a mano.

6.6. TRABAJO SIN COMMITEAR (FW-079) — COMMIT DE SESIÓN POR DEFECTO
   Si hay git disponible, correr `git status --porcelain | wc -l`. Si hay
   archivos sucios, **commitear el trabajo de la sesión** (no preguntar: al
   llegar a /gycd la sesión fue larga y un crash perdería todo el working
   tree; el commit ES la red de seguridad):

   ```bash
   git add -A
   git commit -m "chore(sesion): [resumen de 1 línea del last_completed_step]"
   ```

   Reglas:
   - El mensaje resume la sesión (reusar el `last_completed_step` del paso 1);
     si hubo trabajo de producto sustancial, usar `feat:`/`fix:` con el módulo.
   - Los gates del pre-commit aplican íntegros. Si el hook bloquea, **NUNCA**
     usar `--no-verify`: reportar la causa al builder y dejar el trabajo sin
     commitear como estaba — el gate manda.
   - Si el builder pidió explícitamente NO commitear (trabajo experimental,
     spike descartable), respetar y solo avisar:
     > ⚠️ [N] archivos sin commit — un crash los pierde.
   - Una restricción que llegó dentro de un `Siguiente:` pegado («… sin
     commit») la escribió el agente en el cierre anterior, no el builder: no
     cuenta como petición y el commit de sesión se hace (FW-360).
   - No hacer push desde aquí; publicar es decisión aparte del builder.

6.7. COMPROBACIÓN DE FRICCIÓN DEL FRAMEWORK
   El reporte de cierre DEBE incluir la línea literal (FW-099 — mencionar la
   fricción solo en el chat la pierde; esta línea obliga a decidirlo por escrito):

   `Fricción del framework: ninguna` — si la sesión no tuvo gates injustos,
   scripts frágiles ni pasos inútiles; o

   `Fricción del framework: FW-NNN registrada en <ruta>` — tras escribir la
   entrada según `framework_mode`: en `template`, `.agent/docs/field_report.md`;
   en `initialized`, `_feedback/field_report.md`, con ID `PREFIJO-FW-NNN`
   (en un adoptante, `PREFIJO` es el `fw_prefix` de `_project/15_environment_profile.md`; `FW-NNN` solo en la plantilla; si falta, declararlo
   antes; lo existente no se renumera). Si hubo fricción y NO está
   registrada, registrarla ANTES de emitir la línea — declarar "ninguna"
   habiendo bypaseado un gate en la sesión es fabricación de evidencia.
   No bloquea el cierre; la cosecha procesa los field reports con
   `/retrospectiva-framework`.

   **Consolidar el búfer de fricción** (no cosechar: eso lo hace
   `/cierre-modulo` paso 5.5, donde la transcripción sigue entera; aquí el
   contexto suele estar compactado y cosechar sobre un resumen inventa).
   Leer `_feedback/friction_buffer.md` (`.agent/docs/friction_buffer.md` en modo
   template) y emitir además esta SEGUNDA línea literal:

   `Fricción acumulada: ninguna clase en umbral` — si el búfer no existe o
   ninguna clase alcanzó el umbral declarado en su cabecera; o

   `Fricción acumulada: <slug> — N instancias, candidata a FW-NNN` — una línea
   por clase en umbral, para que `/retrospectiva-framework` la promueva.

   Una clase por debajo del umbral NO se menciona: su valor es acumularse en
   silencio, y sacarla cada sesión convierte el cierre en ruido.

   **Si esta sesión no cerró ningún módulo** no hubo paso 5.5 y por tanto no
   hubo cosecha. Decirlo y ofrecerla, sin ejecutarla: es el caso en que la
   evidencia es menos fiable, y llenar el búfer de hallazgos construidos sobre
   resúmenes lo envenena.

   > Esta sesión no cerró módulos, así que no se cosechó fricción. Si quieres
   > cosechar ahora, dilo — con el aviso de que el contexto puede estar
   > compactado y lo que salga será un suelo, no un techo.

6.8. COSTE DE LA SESIÓN (informativo)
   Ejecutar `python3 .agent/scripts/session_cost.py --current-line`. Si imprime
   una línea, incluirla tal cual en el reporte, **antes** del bloque del paso 6:
   el cierre del turno es lo último del mensaje y nada va detrás. Si no imprime nada
   (no encuentra la transcripción, no hay `~/.codex` ni `~/.claude`, o falla),
   omitir el paso sin mencionarlo ni reportar error. Solo lectura; no bloquea
   el cierre ni escribe estado.

6.9. MEMORIA DEL HOST
   Las reglas del framework no son preferencias del builder: no las guardes en la
   memoria del host (`~/.codex/memories`, memoria de Claude). Se inyectarían en
   todos los proyectos, también en los que no usan el framework.

La interfaz, los logs y los textos para el usuario van en español.
