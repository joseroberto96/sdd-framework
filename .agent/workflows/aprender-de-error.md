---
description: Toma la última corrección del usuario en la conversación, propone una lección concreta y la añade a _project/22_lessons.md tras aprobación explícita. CLAUDE.md no se toca
---

# Aprender de Error — Captura de Lecciones

Inspirado en el setup de Boris Cherny: cuando el agente comete un error y el usuario lo corrige, capturar la lección para que no se repita. Versión adaptada del framework v3 (ADR-016): la lección NO va a `CLAUDE.md` (constitución estable) sino a un artefacto numerado `_project/22_lessons.md` que se crea on-demand.

## Cuándo usar

- Tras una corrección explícita del usuario que revela un patrón ("no hagas X", "siempre haz Y", "esto se rompe cuando Z").
- Cuando el agente cometió un error que vale la pena no repetir en futuras sesiones.
- Como cierre de un debug largo: "lo que aprendimos es...".

NO usar para:
- Correcciones puntuales sin patrón aplicable.
- Información sobre el dominio del producto (eso va a `09_domain_profile.md` vía `/añadir-experto`).
- Reglas inmutables del negocio (eso son invariantes, van a `03_invariants.md` vía `/actualizar-invariante`).
- Decisiones arquitectónicas (eso son ADRs, van a `03_arch_decisions.md`).

## Pasos

0. IDENTIFICAR LA CORRECCIÓN
   Localizar en la conversación actual la corrección del usuario más reciente. Extraer:
   - **Síntoma**: qué hizo el agente que estuvo mal.
   - **Corrección**: cómo el usuario lo redirigió.
   - **Patrón**: la generalización aplicable a futuras situaciones similares.

   Si no hay una corrección clara en la conversación → STOP con mensaje: "No detecté una corrección reciente. Indica el síntoma y la regla aprendida explícitamente."

1. PROPONER LECCIÓN
   Mostrar al usuario:

   ```
   Propongo registrar esta lección en _project/22_lessons.md:

   ## Lección [N] — [Título corto, imperativo si es regla]
   **Fecha:** [ISO8601 actual]
   **Origen:** sesión actual ([referencia opcional al turno])

   **Síntoma:**
   [Qué hice mal, en 1-2 líneas]

   **Regla:**
   [Cómo debo comportarme en situaciones similares, en imperativo]

   **Por qué:**
   [Razón concreta — incidente, principio, convención del proyecto]

   **Aplicabilidad:**
   - Dominio: [técnico|producto|proceso|framework]
   - Alcance: [este proyecto|cualquier proyecto del framework]

   ¿Apruebas registrar? Responder "sí, registrar" para confirmar.
   ```

   Esperar respuesta literal. Cualquier otra cosa que no sea "sí, registrar" → no escribir.

2. CREAR O ABRIR `_project/22_lessons.md`
   Si el archivo NO existe:
   - Si `framework_mode = template` → STOP con mensaje: "22_lessons.md no debe existir en plantilla. Inicializa el proyecto con /nuevo-proyecto antes de registrar lecciones."
   - Si `framework_mode = initialized` → crear el archivo con cabecera inicial:
     ```markdown
     # Lecciones Aprendidas — [NOMBRE_PROYECTO]

     > Lecciones del trabajo concreto en este proyecto. Se añaden vía `/aprender-de-error` con aprobación explícita del usuario.
     >
     > Una lección recurrente puede promoverse a regla del framework editando `CLAUDE.md` manualmente — esa promoción NO es automática (ADR-016).
     ```
   - Actualizar `03_project_state.json → _refs.lessons` apuntando a este archivo si la entrada no existe.

3. ESCRIBIR LA LECCIÓN
   Append al final del archivo el bloque propuesto en el paso 1. Numerar correlativamente: leer el archivo, encontrar el último `## Lección N` y usar N+1.

4. REGISTRAR EN JOURNAL
   Si `framework_mode = initialized`, añadir línea a `_project/13_session_journal.md`:
   ```
   - [fecha] [framework] Lección [N] registrada en 22_lessons.md: [título corto]
   ```

5. SALIDA
   ```
   Lección [N] registrada en _project/22_lessons.md.

   Esta lección NO modifica CLAUDE.md ni 03_invariants.md. Si crees que merece convertirse en regla del framework o invariante de negocio, ese paso debe hacerse manualmente con criterio explícito.
   ```

   Al cerrar no se emite un cierre nuevo: vuelve al `Siguiente:` vigente de la sesión y repítelo tal cual.

## Señales de outcome — el ciclo de vida de una lección

Una lección no es verdad eterna: se corrobora, se cuestiona o se corrige con el
uso real. Cuando durante el trabajo una lección existente demuestre algo, añadir
una línea a la sección `**Señales:**` de su bloque en `_project/22_lessons.md`
(crear la sección al final del bloque si no existe):

```
**Señales:**
- 2026-07-09 useful — aplicada con éxito en el módulo M2
- 2026-08-01 dead_end — la regla no aplicaba a este caso
- 2026-08-10 corrected — la regla correcta es [W]
```

- `useful` — la lección se aplicó y evitó el error.
- `dead_end` — se intentó aplicar y no correspondía; ruido.
- `corrected` — la regla era errónea; la nota lleva la regla correcta.

Estas señales alimentan `_project/22_lessons_digest.md`, la proyección de solo
lectura que `/gycd` regenera con `.agent/scripts/lessons_reflect.py`
(decaimiento con half-life de 30 días + corroboración mínima de 2 señales) y
que `/arrancar` carga en vez del archivo completo. Añadir una señal NO requiere
el gate de aprobación literal — el gate protege la creación de reglas, no el
registro de su uso.

## Reglas

- **CLAUDE.md no se modifica desde aquí.** ADR-016 lo deja claro: la constitución estable no se erosiona con cada corrección. Solo el usuario, con criterio, promueve una lección a `CLAUDE.md`.
- **`03_invariants.md` no se modifica desde aquí.** Las reglas inmutables del negocio usan `/actualizar-invariante` con su propio gate.
- **No registrar sin aprobación literal.** "Quizá", "tal vez", "puede ser" → no escribir.
- **No inventar lecciones.** Si no hay corrección detectable, pedir al usuario que la articule, no rellenar con generalidades.
- **En modo template (`framework_mode = template`) no se permite escritura.** El artefacto `22_lessons.md` no existe hasta que el proyecto está inicializado.

La interfaz, los logs y los textos para el usuario van en español.
